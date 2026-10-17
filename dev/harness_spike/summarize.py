"""Spike summary (T-024): the numbers the harness choice is made from.

    uv run python dev/harness_spike/summarize.py --harness opencode --minutes 15 --run <dir>

Reads the run directory produced by spike.sh:
- logs/mcp-calls.jsonl  — every body-MCP call (wait_events stats, act->effect latency,
                          rejections with reasons; the say quotes)
- logs/harness.log     — the harness's own stdout (OpenCode JSON events / zcode jsonl):
                          turns, tokens in/out/cache, cost
- stats.log            — docker stats samples (harness container memory)
- workplace/           — memory/state.md, will.md (+ archive-* of dead nodes)
- logs/mc-server.log   — the vanilla chat log (quotes as the server saw them)
- logs/sandbox-check.txt, cost-check.txt — the review P1-1/P1-7 evidence

Prints a human-readable summary and writes summary.json next to the logs. Field names of the
harness events are best-effort: the parsers below accept both the documented shapes and leave
a marker when a metric could not be found (nothing is guessed silently).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = min(int(len(values) * q), len(values) - 1)
    return values[idx]


def load_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue  # opencode mixes formatted banners into stdout
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --- the MCP call log ------------------------------------------------------------------


def mcp_summary(entries: list[dict]) -> dict:
    by_tool: dict[str, int] = {}
    wait_timeouts: list[float] = []
    wait_woke: dict[str, int] = {}
    effect_ms: list[int] = []
    errors: dict[str, int] = {}
    says: list[str] = []
    durations: dict[str, list[float]] = {}
    for e in entries:
        tool = str(e.get("tool", "?"))
        by_tool[tool] = by_tool.get(tool, 0) + 1
        durations.setdefault(tool, []).append(
            float(e.get("t_end", e.get("ts", 0))) - float(e.get("ts", 0))
        )
        if tool == "wait_events":
            timeout = (e.get("args") or {}).get("timeout_s")
            if isinstance(timeout, (int, float)):
                wait_timeouts.append(float(timeout))
            woke = e.get("woke_by") or ("timeout" if e.get("ok") else "error")
            wait_woke[str(woke)] = wait_woke.get(str(woke), 0) + 1
        if tool == "act":
            ms = e.get("effect_ms")
            if isinstance(ms, (int, float)):
                effect_ms.append(int(ms))
        if e.get("ok") is False:
            reason = re.sub(r"\d+", "N", str(e.get("error", "?")))[:80]
            errors[f"{tool}: {reason}"] = errors.get(f"{tool}: {reason}", 0) + 1
        if tool == "say" and e.get("ok"):
            text = (e.get("args") or {}).get("text")
            if text:
                says.append(str(text))
    tool_durations = {
        t: {"p50_ms": pct(v, 0.5), "p95_ms": pct(v, 0.95)} for t, v in durations.items() if v
    }
    return {
        "calls_by_tool": by_tool,
        "wait_events": {
            "timeouts_used_s": sorted(set(wait_timeouts)),
            "woke_by": wait_woke,
            "self_chosen_waits": len(wait_timeouts),
        },
        "act_to_effect_ms": {"p50": pct(effect_ms, 0.5), "p95": pct(effect_ms, 0.95), "n": len(effect_ms),
                             "note": "resolution ~200 ms (the 5 Hz burst after act)"},
        "tool_duration_ms": tool_durations,
        "rejections": errors,
        "say_quotes": says,
    }


# --- the harness event log --------------------------------------------------------------


def _assistant_messages(events: list[dict]) -> list[dict]:
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            out.append(msg)
    return out


def opencode_summary(events: list[dict]) -> dict:
    messages = _assistant_messages(events)
    turns = []
    for m in messages:
        info = m.get("info") or {}
        tokens = info.get("tokens") or {}
        cache = tokens.get("cache") or {}
        cost = info.get("cost") or {}
        turns.append(
            {
                "duration_ms": info.get("duration"),
                "input": tokens.get("input"),
                "output": tokens.get("output"),
                "reasoning": tokens.get("reasoning"),
                "cache_read": cache.get("read"),
                "cache_write": cache.get("write"),
                "cost": cost.get("total"),
            }
        )
    durations = [t["duration_ms"] for t in turns if isinstance(t.get("duration_ms"), (int, float))]
    inputs = [t["input"] for t in turns if isinstance(t.get("input"), (int, float))]
    outputs = [t["output"] for t in turns if isinstance(t.get("output"), (int, float))]
    cache_read = [t["cache_read"] for t in turns if isinstance(t.get("cache_read"), (int, float))]
    cache_write = [t["cache_write"] for t in turns if isinstance(t.get("cache_write"), (int, float))]
    cost = [t["cost"] for t in turns if isinstance(t.get("cost"), (int, float))]
    prefix_total = sum(inputs) + sum(cache_write)
    return {
        "turns": len(turns),
        "tokens": {
            "in_per_turn": statistics.mean(inputs) if inputs else None,
            "out_per_turn": statistics.mean(outputs) if outputs else None,
            "cache_read_total": sum(cache_read) if cache_read else 0,
            "cache_write_total": sum(cache_write) if cache_write else 0,
            "cache_share": (
                round(sum(cache_read) / prefix_total, 3) if prefix_total and cache_read else None
            ),
        },
        "turn_latency_ms": {"p50": pct(durations, 0.5), "p95": pct(durations, 0.95)},
        "cost_usd": sum(cost) if cost else None,
    }


def zcode_summary(events: list[dict]) -> dict:
    # The zcode CLI jsonl differs; collect whatever token/cost fields look familiar and mark
    # the rest as not found rather than guessing.
    tokens_in = tokens_out = 0
    turns = 0
    cost = 0.0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        usage = ev.get("usage") or (ev.get("message") or {}).get("usage") or {}
        if usage:
            turns += 1
            tokens_in += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        c = ev.get("cost") or ev.get("total_cost_usd")
        if isinstance(c, (int, float)):
            cost += float(c)
    return {
        "turns": turns or None,
        "tokens": {"in_total": tokens_in or None, "out_total": tokens_out or None,
                   "note": "zcode jsonl: per-turn fields depend on CLI version"},
        "cost_usd": cost or None,
    }


# --- misc artifacts ---------------------------------------------------------------------


def stats_summary(path: Path, name_re: str) -> dict:
    peak_kb = 0
    if not path.exists():
        return {"note": "no stats"}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(name_re, line)
        mem = re.search(r"([0-9.]+)([KMGT]?i?B)", line)
        if not mem:
            continue
        value, unit = float(mem.group(1)), mem.group(2)
        kb = {"B": value / 1024, "KiB": value, "MiB": value * 1024, "GiB": value * 1024 * 1024}.get(unit)
        if kb and m:
            peak_kb = max(peak_kb, kb)
    return {"peak_mem_kib": round(peak_kb) or None}


def quotes_from_mc(log: Path, username: str) -> list[str]:
    if not log.exists():
        return []
    pat = re.compile(rf"<{re.escape(username)}>\s*(.+)")
    out = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = pat.search(line)
        if m:
            out.append(m.group(1).strip())
    return out


def wills_summary(run: Path) -> dict:
    found = []
    for will in sorted(run.glob("**/will.md")):
        text = will.read_text(encoding="utf-8", errors="replace").strip()
        found.append(
            {
                "file": str(will.relative_to(run)),
                "emergency": "emergency: true" in text,
                "lines": len([l for l in text.splitlines() if l.strip()]),
            }
        )
    return {"wills": found, "agent_wills": sum(1 for w in found if not w["emergency"]),
            "emergency_wills": sum(1 for w in found if w["emergency"])}


def state_fragment(workplace: Path, limit: int = 15) -> str:
    state = workplace / "memory" / "state.md"
    if not state.exists():
        return "(no memory/state.md)"
    lines = state.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[:limit]) if lines else "(empty)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=["opencode", "zcode"], required=True)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--username", default="LastOrder01")
    args = parser.parse_args()

    run = args.run
    workplace = run / "workplace"
    mcp = mcp_summary(load_jsonl(run / "logs" / "mcp-calls.jsonl"))
    events = load_jsonl(run / "logs" / "harness.log")
    harness = (opencode_summary if args.harness == "opencode" else zcode_summary)(events)
    summary = {
        "harness": args.harness,
        "minutes": args.minutes,
        "mcp": mcp,
        "harness_events": harness,
        "memory": stats_summary(run / "stats.log", r"harness"),
        "sandbox_check": (run / "logs" / "sandbox-check.txt").read_text(encoding="utf-8", errors="replace").strip()
        if (run / "logs" / "sandbox-check.txt").exists() else None,
        "wills": wills_summary(run),
        "mc_chat_quotes": quotes_from_mc(run / "logs" / "mc-server.log", args.username)[-10:],
        "cost_check": (run / "cost-check.txt").read_text(encoding="utf-8", errors="replace").strip()
        if (run / "cost-check.txt").exists() else None,
        "state_md": state_fragment(workplace),
    }
    turns = harness.get("turns") or 0
    if turns:
        summary["turns_per_minute"] = round(turns / args.minutes, 2)
    cost = harness.get("cost_usd")
    if cost is not None:
        summary["cost_usd_per_hour"] = round(cost / args.minutes * 60, 3)

    (run / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
