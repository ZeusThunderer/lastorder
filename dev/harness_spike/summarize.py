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

Hermes runs (T-034, the stand's node-1 instead of the spike profile) add:
- logs/runner.log      — the node runner's own JSON log (docker logs of node-1): the
                          sessions it started, exit codes, the death/will path timeline
- logs/hermes-state.json — a dump of HERMES_HOME/state.db (sessions + session_model_usage):
                          per-session api_call_count and token columns — the runner pipes
                          the CLI's stream-json away, so the state DB is the token source
- logs/harness.log     — OPTIONAL stream-json of `hermes chat` (when captured directly);
                          parsed per hermes_cli/stream_json.py v0.22.4 shapes
- logs/mind-log.jsonl  — the stand's mind log.jsonl (volume mind-1-run) instead of the
                          spike's mcp-calls.jsonl: `call` records plus the `intent`
                          settle join that recovers act->effect latency

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
    out: list[dict] = []
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


def load_json_stream(path: Path) -> list[dict]:
    """Parse concatenated pretty-printed JSON objects (the zcode CLI prints one per run)."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    decoder = json.JSONDecoder()
    out: list[dict] = []
    i = 0
    while i < len(text):
        while i < len(text) and text[i] not in "{":
            i += 1
        if i >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            out.append(obj)
        i = end
    return out


# --- the MCP call log ------------------------------------------------------------------


def mind_log_entries(events: list[dict]) -> list[dict]:
    """The stand's mind log.jsonl (runtime/mind/mcp_body.py `JsonlLog`): `call` records
    carry the fields mcp_summary expects (tool/args/ts/t_end/ok/woke_by/intent_id);
    `intent` records settle act effects — the spike proxy logged effect_ms directly,
    the stand log joins them by intent_id (first settle after the call)."""
    settled: dict[str, float] = {}
    for ev in events:
        if (
            isinstance(ev, dict)
            and ev.get("kind") == "intent"
            and ev.get("phase") in ("done", "failed")
        ):
            iid = str(ev.get("intent_id") or "")
            ts = float(ev.get("ts") or 0)
            if iid and ts and iid not in settled:
                settled[iid] = ts
    out: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("kind") != "call":
            continue
        entry = {k: v for k, v in ev.items() if k != "kind"}
        iid = str(entry.get("intent_id") or "")
        done = settled.get(iid)
        t_end = float(entry.get("t_end") or entry.get("ts") or 0)
        if iid and done and done >= t_end:
            entry["effect_ms"] = round((done - t_end) * 1000)
        out.append(entry)
    return out


def mcp_summary(entries: list[dict]) -> dict:
    by_tool: dict[str, int] = {}
    wait_timeouts: list[float] = []
    wait_woke: dict[str, int] = {}
    effect_ms: list[int] = []
    errors: dict[str, int] = {}
    says: list[str] = []
    durations: dict[str, list[float]] = {}
    gaps: list[float] = []
    prev_end: float | None = None
    for e in entries:
        tool = str(e.get("tool", "?"))
        if tool == "event":
            continue  # poller-side lines (death, respawn retries), not agent calls
        by_tool[tool] = by_tool.get(tool, 0) + 1
        ts = float(e.get("ts", 0) or 0)
        t_end = float(e.get("t_end", ts) or ts)
        durations.setdefault(tool, []).append(t_end - ts)
        # review R1-1: the gap from the previous call's end to this call's start is the
        # agent/harness latency — a wait_events DURATION is waiting, not latency, and stays
        # outside these gaps by construction.
        if prev_end is not None and ts > prev_end:
            gaps.append(ts - prev_end)
        prev_end = t_end
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
        "turn_gap_s": {"p50": pct(gaps, 0.5), "p95": pct(gaps, 0.95), "n": len(gaps),
                       "note": "idle between consecutive MCP calls (t_end -> next ts): "
                               "model+harness latency, waiting excluded"},
        "tool_duration_ms": tool_durations,
        "rejections": errors,
        "say_quotes": says,
    }


# --- the harness event log --------------------------------------------------------------


def opencode_summary(events: list[dict]) -> dict:
    # opencode 1.19.3 `run --format json` emits step_start / tool_use / step_finish parts;
    # tokens and cost live on the step-finish part: tokens{input,output,reasoning,cache{read,
    # write}}, cost (a plain number, provider-reported).
    turns = []
    starts: dict[str, float] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        part = ev.get("part") or {}
        if part.get("type") == "step-start":
            mid = str(part.get("messageID"))
            if mid not in starts:
                starts[mid] = float(ev.get("timestamp") or 0)
        elif part.get("type") == "step-finish":
            t = part.get("tokens") or {}
            cache = t.get("cache") or {}
            turns.append(
                {
                    "duration_ms": (
                        float(ev.get("timestamp") or 0) - starts.get(str(part.get("messageID")), 0)
                        or None
                    ),
                    "input": t.get("input"),
                    "output": t.get("output"),
                    "reasoning": t.get("reasoning"),
                    "cache_read": cache.get("read"),
                    "cache_write": cache.get("write"),
                    "cost": part.get("cost") if isinstance(part.get("cost"), (int, float)) else None,
                }
            )
    durations = [t["duration_ms"] for t in turns if isinstance(t.get("duration_ms"), (int, float)) and t["duration_ms"] > 0]
    inputs = [t["input"] for t in turns if isinstance(t.get("input"), (int, float))]
    outputs = [t["output"] for t in turns if isinstance(t.get("output"), (int, float))]
    cache_read = [t["cache_read"] for t in turns if isinstance(t.get("cache_read"), (int, float))]
    cache_write = [t["cache_write"] for t in turns if isinstance(t.get("cache_write"), (int, float))]
    cost = [t["cost"] for t in turns if isinstance(t.get("cost"), (int, float))]
    # OpenRouter-style accounting: input EXCLUDES cached tokens, so the prefix is in+cache.
    prefix_total = sum(inputs) + sum(cache_read) + sum(cache_write)
    return {
        "turns": len(turns),
        "tokens": {
            "in_per_turn": round(statistics.mean(inputs), 1) if inputs else None,
            "cache_read_per_turn": round(statistics.mean(cache_read), 1) if cache_read else None,
            "out_per_turn": round(statistics.mean(outputs), 1) if outputs else None,
            "reasoning_total": sum(
                t["reasoning"] for t in turns if isinstance(t.get("reasoning"), (int, float))
            ),
            "cache_read_total": sum(cache_read) if cache_read else 0,
            "cache_write_total": sum(cache_write) if cache_write else 0,
            "cache_share": (
                round(sum(cache_read) / prefix_total, 3) if prefix_total and cache_read else None
            ),
            "note": "opencode/OpenRouter: input excludes cache; prefix = in + cache_read",
        },
        "turn_latency_ms": {"p50": pct(durations, 0.5), "p95": pct(durations, 0.95)},
        "cost_usd": round(sum(cost), 4) if cost else None,
    }


def zcode_summary(events: list[dict]) -> dict:
    # The zcode harness (zcode-app-cli -p --json) prints one JSON object per run/resume:
    # {sessionId, response, usage{inputTokens,outputTokens,cacheReadTokens,...}, projection}.
    turns = 0
    tokens_in = tokens_out = cache_read = 0
    per_turn: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict) or "usage" not in ev:
            continue
        u = ev["usage"] or {}
        if not isinstance(u, dict) or not u.get("modelRequestCount"):
            continue
        turns += 1
        tin = int(u.get("inputTokens") or u.get("input_tokens") or 0)
        tout = int(u.get("outputTokens") or u.get("output_tokens") or 0)
        tcr = int(u.get("cacheReadTokens") or u.get("cache_read_tokens") or 0)
        tcw = int(u.get("cacheWriteTokens") or u.get("cache_write_tokens") or 0)
        tokens_in += tin
        tokens_out += tout
        cache_read += tcr
        per_turn.append(
            {
                "in": tin,
                "out": tout,
                "cache_read": tcr,
                "cache_write": tcw,
                "model_requests": int(u.get("modelRequestCount") or 0),
            }
        )
    prefix_total = tokens_in + sum(t["cache_write"] for t in per_turn)
    return {
        "turns": turns or None,
        "model_requests": sum(t["model_requests"] for t in per_turn) or None,
        "tokens": {
            "in_per_turn": round(tokens_in / turns, 1) if turns else None,
            "out_per_turn": round(tokens_out / turns, 1) if turns else None,
            "cache_read_total": cache_read or 0,
            "cache_write_total": sum(t["cache_write"] for t in per_turn) or 0,
            "cache_share": round(cache_read / prefix_total, 3) if prefix_total and cache_read else None,
            "note": "zcode: one json object per run/resume turn; inputTokens includes cache read",
        },
        # The Coding Plan has no per-call $ price: cost is credits (docs.z.ai formulas),
        # computed in the summary table of harness-spike.md, not here.
        "cost_usd": None,
    }


# --- the hermes harness (T-034) ---------------------------------------------------------

# docs.z.ai/devpack/overview, credit rates per 10k tokens: (input, cache_read, output).
GLM_CREDIT_RATES = (("glm-5.3-flash", (2.3, 0.56, 8.0)), ("glm-5.3", (6.9, 1.7, 24.0)))


def glm_credits(model: str, input_t: int, cache_read: int, output_t: int) -> float | None:
    """Coding-plan credits of a token total; None when the model has no known rates."""
    low = str(model).lower()
    rates = next((r for name, r in GLM_CREDIT_RATES if name in low), None)
    if rates is None:
        return None
    return (input_t * rates[0] + cache_read * rates[1] + output_t * rates[2]) / 10_000


def _usage_block(turns: int, tin: int, tout: int, tcache: int, tcache_w: int, note: str) -> dict:
    prefix_total = tin + tcache_w
    return {
        "turns": turns or None,
        "tokens": {
            "in_per_turn": round(tin / turns, 1) if turns else None,
            "out_per_turn": round(tout / turns, 1) if turns else None,
            "cache_read_total": tcache,
            "cache_write_total": tcache_w,
            # Anthropic-style accounting (hermes/zcode report it the same way): input
            # INCLUDES the cached prefix; the share is cache/(input+cache_write).
            "cache_share": round(tcache / prefix_total, 3) if prefix_total and tcache else None,
            "totals": {"input": tin, "output": tout, "cache_read": tcache, "cache_write": tcache_w},
            "note": note,
        },
    }


def hermes_stream_summary(events: list[dict]) -> dict:
    """`hermes chat --format stream-json`: text/tool events and ONE terminal result
    envelope per oneshot run (hermes_cli/stream_json.py v0.22.4): tokens{input, output,
    total, cache_read, cache_write}, duration_ms, exit_code, session_id."""
    runs: list[dict] = []
    tool_uses: dict[str, int] = {}
    tool_errors: dict[str, int] = {}
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "tool_use":
            name = str(ev.get("name") or "?")
            tool_uses[name] = tool_uses.get(name, 0) + 1
        elif kind == "tool_result" and ev.get("is_error"):
            name = str(ev.get("name") or "?")
            tool_errors[name] = tool_errors.get(name, 0) + 1
        elif kind == "result":
            tk = ev.get("tokens") or {}
            runs.append(
                {
                    "session_id": ev.get("session_id"),
                    "exit_code": ev.get("exit_code"),
                    "input": tk.get("input"),
                    "output": tk.get("output"),
                    "cache_read": tk.get("cache_read"),
                    "cache_write": tk.get("cache_write"),
                    "duration_ms": ev.get("duration_ms"),
                    "error": ev.get("error"),
                }
            )
    def _sum(field: str) -> int:
        return sum(int(r[field]) for r in runs if isinstance(r.get(field), (int, float)))
    block = _usage_block(
        len(runs), _sum("input"), _sum("output"), _sum("cache_read"), _sum("cache_write"),
        "stream-json: one result envelope per oneshot run; input includes cache read",
    )
    block["runs"] = len(runs)
    block["tool_uses"] = tool_uses
    block["tool_errors"] = tool_errors
    block["failed_runs"] = sum(1 for r in runs if r.get("exit_code") not in (0, None))
    block["run_duration_ms"] = {"p50": pct([r["duration_ms"] for r in runs if isinstance(r.get("duration_ms"), (int, float))], 0.5),
                                "p95": pct([r["duration_ms"] for r in runs if isinstance(r.get("duration_ms"), (int, float))], 0.95)}
    block["source"] = "stream-json"
    return block


def hermes_state_summary(state: dict, runner_events: list[dict]) -> dict:
    """HERMES_HOME/state.db dump (the node runner pipes the CLI stdout away): the
    `sessions`/`session_model_usage` rows are the token source; usage columns
    (api_call_count, tokens) live in `session_model_usage` and are joined onto each
    session row by id (summed when a session used several models). `turns` here are
    api_call_count (model requests, the zcode-comparable metric). A session started
    within +-120 s of a runner `will.session` event is the will session, not life."""
    usage: dict[str, dict[str, int]] = {}
    for u in state.get("session_model_usage") or []:
        if not isinstance(u, dict) or not u.get("session_id"):
            continue
        acc = usage.setdefault(str(u["session_id"]), {})
        for col in ("api_call_count", "input_tokens", "output_tokens", "reasoning_tokens",
                    "cache_read_tokens", "cache_write_tokens"):
            v = u.get(col)
            if isinstance(v, (int, float)) and v >= 0:
                acc[col] = acc.get(col, 0) + int(v)
    rows = []
    for r in state.get("sessions") or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        u = usage.get(str(row.get("id")))
        if u:
            for col, val in u.items():
                row[col] = val  # the usage row is authoritative over session-level copies
        rows.append(row)
    will_started = [
        _iso_to_unix(str(ev.get("ts") or "")) for ev in runner_events
        if str(ev.get("type") or "") == "will.session"
    ]
    will_started = [t for t in will_started if t]

    def _int(row: dict, key: str) -> int:
        v = row.get(key)
        return int(v) if isinstance(v, (int, float)) and v >= 0 else 0

    life, will = [], []
    for row in rows:
        started = _int(row, "started_at")
        rec = {
            "session_id": row.get("id"),
            "started_at": row.get("started_at"),
            "api_calls": _int(row, "api_call_count"),
            "input": _int(row, "input_tokens"),
            "output": _int(row, "output_tokens"),
            "reasoning": _int(row, "reasoning_tokens"),
            "cache_read": _int(row, "cache_read_tokens"),
            "cache_write": _int(row, "cache_write_tokens"),
            "messages": _int(row, "message_count"),
            "tool_calls": _int(row, "tool_call_count"),
            "model": row.get("model"),
            "title": row.get("display_name") or row.get("title"),
            "compression_fallback_streak": _int(row, "compression_fallback_streak"),
        }
        if any(abs(started - w) <= 120 for w in will_started):
            rec["kind"] = "will"
            will.append(rec)
        else:
            rec["kind"] = "life"
            life.append(rec)
    model = next((r["model"] for r in rows if r.get("model")), "")
    sessions = life + will

    def _tot(rows_: list[dict], key: str) -> int:
        return sum(r[key] for r in rows_)

    # reasoning is completion tokens too: the plan bills it as output [предположение]
    block = _usage_block(
        sum(r["api_calls"] for r in sessions),
        _tot(sessions, "input"),
        _tot(sessions, "output") + _tot(sessions, "reasoning"),
        _tot(sessions, "cache_read"),
        _tot(sessions, "cache_write"),
        "state.db: turns = api_call_count; output includes reasoning tokens",
    )
    # state.db counts the cached prefix OUTSIDE input_tokens (hermes's own result
    # envelope folds it in, the DB does not) — the share uses the DB convention here
    tin, tcache = _tot(sessions, "input"), _tot(sessions, "cache_read")
    if tin + tcache:
        block["tokens"]["cache_share"] = round(tcache / (tin + tcache), 3)
    block["sessions"] = sessions
    block["life_sessions"] = len(life)
    block["will_sessions"] = len(will)
    block["reasoning_total"] = _tot(sessions, "reasoning")
    # no compaction/summary marks exist in the state.db dump (usage rows carry task=""),
    # so nothing is reported for auxiliary calls — absence is not zero
    block["quota_credits"] = {
        "model": model,
        "credits": glm_credits(
            model,
            _tot(sessions, "input"),
            _tot(sessions, "cache_read"),
            _tot(sessions, "output") + _tot(sessions, "reasoning"),
        ),
        "formula": "(in*rate_in + cache_read*rate_cache + out*rate_out)/10^4, docs.z.ai",
    }
    block["source"] = "state.db dump"
    return block


def _iso_to_unix(ts: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


# --- the node runner log (stand runs) ----------------------------------------------------

def runner_summary(events: list[dict]) -> dict:
    """The node runner's JSON log (docker logs of node-N): event counts, every ended
    session with its exit code and runtime, the death/will path timeline."""
    counts: dict[str, int] = {}
    ended: list[dict] = []
    timeline: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("type") or "?")
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "session.ended":
            ended.append(
                {"ts": ev.get("ts"), "harness": ev.get("harness"), "code": ev.get("code"),
                 "ran_s": ev.get("ran_s"), "tail": ev.get("tail")}
            )
        elif any(kind.startswith(p) for p in ("life.", "death", "will.", "current.", "quota.", "harness.", "runner.")):
            timeline.append({k: v for k, v in ev.items() if k != "type"} | {"type": kind})
    codes: dict[str, int] = {}
    for e in ended:
        codes[str(e["code"])] = codes.get(str(e["code"]), 0) + 1
    ran = [e["ran_s"] for e in ended if isinstance(e.get("ran_s"), (int, float))]
    return {
        "event_counts": counts,
        "sessions_ended": len(ended),
        "session_exit_codes": codes,
        "session_ran_s": {"p50": pct(ran, 0.5), "p95": pct(ran, 0.95), "max": max(ran) if ran else None},
        "timeline": timeline,
        "errors": [e for e in ended if e.get("code") not in (0, None)],
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
    parser.add_argument("--harness", choices=["opencode", "zcode", "hermes"], required=True)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--username", default="LastOrder01")
    args = parser.parse_args()

    run = args.run
    workplace = run / "workplace"
    mind_log = run / "logs" / "mind-log.jsonl"
    mcp = mcp_summary(
        mind_log_entries(load_jsonl(mind_log))
        if mind_log.exists()
        else load_jsonl(run / "logs" / "mcp-calls.jsonl")
    )
    if args.harness == "hermes":
        # the stand's node runner: its own log plus the hermes state DB dump; the
        # stream-json harness.log is optional (only when captured directly)
        runner_events = load_jsonl(run / "logs" / "runner.log")
        stream = load_jsonl(run / "logs" / "harness.log")
        state: dict = {}
        state_path = run / "logs" / "hermes-state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
            except ValueError:
                state = {}
        harness = (
            hermes_stream_summary(stream) if stream
            else hermes_state_summary(state if isinstance(state, dict) else {}, runner_events)
        )
        summary = {
            "harness": args.harness,
            "minutes": args.minutes,
            "mcp": mcp,
            "harness_events": harness,
            "runner": runner_summary(runner_events),
            "memory": stats_summary(run / "stats.log", r"node-1"),
            "wills": wills_summary(run),
            "mc_chat_quotes": quotes_from_mc(run / "logs" / "mc-server.log", args.username)[-10:],
            "staged_wills": _text(run / "logs" / "staged-wills.txt"),
            "state_md": state_fragment(workplace),
        }
        turns = harness.get("turns") or 0
        if turns:
            summary["turns_per_minute"] = round(turns / args.minutes, 2)
        credits = (harness.get("quota_credits") or {}).get("credits")
        if isinstance(credits, (int, float)):
            summary["credits_per_hour"] = round(credits / args.minutes * 60, 1)
    else:
        events = (
            load_json_stream(run / "logs" / "harness.log")
            if args.harness == "zcode"
            else load_jsonl(run / "logs" / "harness.log")
        )
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


def _text(path: Path, limit: int = 4000) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


if __name__ == "__main__":
    main()
