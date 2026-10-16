"""Bench of tier candidates on the network's typical calls (T-004, ADR-022/023).

    uv run python dev/llm_bench.py --tier node --repeat 2 --max-usd 0.5
    uv run python dev/llm_bench.py --tier node --models openrouter/qwen/qwen3.7-flash,... --repeat 1 --max-usd 0.05

Case inputs are files in dev/bench_cases/, so every run is reproducible. The persona system prompt
comes from PersonaProvider (the real template, untouched); case files add only the task instruction
and the user content. The step schema is preliminary (NETWORK.md §5; T-011 finalizes it).

Costs: per call the client reports provider cost when litellm gives one, else the run estimates it
from PRICE_TABLE (OpenRouter list prices, verified 2026-10-16). The run stops once the accumulated
cost exceeds --max-usd. Everything measured lands in a raw JSONL under docs/agents/llm-bench/.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from runtime.config import load_dotenv  # noqa: E402
from runtime.lastorder.naming import preset  # noqa: E402
from runtime.lastorder.persona import Inheritance, Node, PersonaProvider  # noqa: E402
from runtime.lastorder.traits import Traits  # noqa: E402
from runtime.llm.client import LLMClient, LLMConfig, LLMError, parse_json_object  # noqa: E402
from tools.snapshot_replies import EMOJI, REQUESTS, check, observation  # noqa: E402
from runtime.perception.translate import Perception  # noqa: E402

CASES_DIR = REPO_ROOT / "dev" / "bench_cases"
RAW_DIR = REPO_ROOT / "docs" / "agents" / "llm-bench" / "raw"

# Provider is pinned: OpenRouter (the litellm `openrouter/` prefix). Prices are $ per 1M tokens
# in/out, read from the authenticated /api/v1/models listing on 2026-10-16; the shortlist figures
# in task.md came from pricepertoken.com and partly matched only the cheaper :batch endpoints.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "openrouter/qwen/qwen3.7-flash": (0.03, 0.13),
    "openrouter/openai/gpt-5-nano": (0.05, 0.40),
    "openrouter/google/gemini-2.5-flash-lite": (0.10, 0.40),  # listed expiration: 2026-10-20
    "openrouter/mistralai/mistral-small-3.2-24b-instruct": (0.0938, 0.25),
    "openrouter/xiaomi/mimo-v2.6-flash": (0.14, 0.28),
    "openrouter/deepseek/deepseek-v4.1-flash": (0.15, 0.60),
    "openrouter/deepseek/deepseek-v3.2": (0.259, 0.80),
    "openrouter/z-ai/glm-5.3-flash": (0.0113, 0.90),
    "openrouter/qwen/qwen3.8-flash": (0.15, 0.47),
}
DEFAULT_MODELS = {
    "node": [
        "openrouter/qwen/qwen3.7-flash",
        "openrouter/openai/gpt-5-nano",
        "openrouter/google/gemini-2.5-flash-lite",
        "openrouter/mistralai/mistral-small-3.2-24b-instruct",
        "openrouter/xiaomi/mimo-v2.6-flash",
    ],
    "smart": [
        "openrouter/deepseek/deepseek-v4.1-flash",
        "openrouter/deepseek/deepseek-v3.2",
        "openrouter/z-ai/glm-5.3-flash",
        "openrouter/qwen/qwen3.8-flash",
    ],
}

# Call registry of the whole 3-node network (NETWORK.md §6, after ADR-022): calls per hour and
# nominal tokens in/out for call kinds the bench does not measure.
REGISTRY = [
    ("step", "node", 1350, 2500, 200),
    ("chatter", "node", 30, 1200, 150),
    ("reflection", "smart", 33, 4000, 600),
    ("will", "smart", 9, 3000, 300),
    ("goals", "smart", 4, 5000, 800),
]

TRAIT_NUMBER = r"(?i)(смелост\w*|жадност\w*|болтливост\w*|довери\w*|суевери\w*)\D{0,15}\d|\d\D{0,15}(смелост\w*|жадност\w*|болтливост\w*|довери\w*|суевери\w*)"
SAMPLE = {"birch_log": "светлый вертикальный столб с тёмными штрихами", "grass_block": "плотный блок, сверху зелёный"}


def load_bench_env() -> None:
    """Keys live in .env of the main checkout: worktrees carry no ignored files."""
    candidates = [REPO_ROOT / ".env"]
    try:
        git_common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        candidates.append(Path(git_common).resolve().parent / ".env")
    except Exception:
        pass
    for path in candidates:
        if path.exists():
            load_dotenv(path)
            return


def persona_system() -> str:
    traits = Traits(5, 5, 5, 5, 5, quirk="likes pale trees more than dark ones", seed=0)
    provider = PersonaProvider(preset("lab"))
    return provider.system_prompt(Node(7, traits), Inheritance(predecessors_summary="you are the first one",
                                                               active_nodes_summary="you are the first one"))


def sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]+", text) if s.strip()])


def cyrillic_share(text: str) -> float | None:
    letters = [c for c in text if c.isalpha() and (c.isascii() or "А" <= c.upper() <= "Я" or c in "Ёё")]
    cyr = [c for c in letters if not c.isascii()]
    return len(cyr) / len(letters) if letters else None


def style_flags(text: str, *, is_will: bool = False) -> list[str]:
    flags = []
    if EMOJI.search(text):
        flags.append("emoji")
    if re.search(TRAIT_NUMBER, text):
        flags.append("trait-number")
    if is_will:
        lines = [l for l in text.splitlines() if l.strip()]
        if not 2 <= len(lines) <= 4:
            flags.append(f"will-lines={len(lines)}")
    elif sentences(text) > 3:
        flags.append(f"sentences={sentences(text)}")
    share = cyrillic_share(text)
    if share is not None and share < 0.5:
        flags.append(f"not-russian={share:.2f}")
    return flags


def load_cases(tier: str, persona: str, perception: Perception) -> list[dict]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if case["tier"] != tier:
            continue
        if case["kind"] == "task1":
            key = case["name"].rsplit("_", 1)[1]  # task1_where -> where
            request = dict(REQUESTS)[key]
            case["system"] = persona
            case["user"] = f"{observation(perception)}\n\n{request}"
            case["snapshot_key"] = key
        else:
            case["system"] = persona + "\n\n" + case["instruction"]
        cases.append(case)
    return cases


def validate(case: dict, reply: str, perception: Perception) -> dict:
    """JSON validity, schema conformance and style flags of one reply. Never raises."""
    out = {"json_ok": None, "schema_ok": None, "style": [], "text": ""}
    if case["kind"] == "task1":
        out["style"] = check(case["snapshot_key"], reply, perception)
        out["text"] = reply
        return out
    if not case.get("json"):
        out["text"] = reply
        out["style"] = style_flags(reply, is_will=case["kind"] == "will")
        return out
    try:
        obj = parse_json_object(reply)
    except LLMError as exc:
        out["json_ok"] = False
        out["style"] = [f"no-json: {exc}"]
        return out
    out["json_ok"] = True
    out["text"] = reply
    if case["kind"] == "step":
        out["schema_ok"] = (
            obj.get("option_id") in case["option_ids"]
            and isinstance(obj.get("say"), str)
            and obj["say"].strip() != ""
            and (not obj.get("note") or isinstance(obj.get("note"), str))
        )
        say = obj.get("say", "")
        out["style"] = style_flags(say) if say else ["no-say"]
    elif case["kind"] == "reflection":
        memory = obj.get("core_memory")
        candidates = obj.get("candidates", [])
        kinds = {"danger", "fact", "recipe", "skill", "term"}
        out["schema_ok"] = (
            isinstance(memory, str)
            and len(memory.encode("utf-8")) <= 1536
            and isinstance(candidates, list)
            and len(candidates) <= 3
            and all(
                c.get("kind") in kinds and isinstance(c.get("claim"), str) and c["claim"].strip()
                and isinstance(c.get("about"), list)
                for c in candidates
                if isinstance(c, dict)
            )
        )
        out["style"] = style_flags(memory) if isinstance(memory, str) else ["no-core-memory"]
    elif case["kind"] == "goals":
        goals = obj.get("goals")
        out["schema_ok"] = (
            isinstance(goals, list)
            and 1 <= len(goals) <= 3
            and all(isinstance(g, dict) and isinstance(g.get("text"), str) and g["text"].strip()
                    and isinstance(g.get("why"), str) for g in goals)
        )
        if isinstance(goals, list):
            out["style"] = [f for g in goals if isinstance(g, dict) for f in style_flags(g.get("text", ""))]
    return out


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[k]


def estimate_cost(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    prices = PRICE_TABLE.get(model)
    if not prices or prompt_tokens is None or completion_tokens is None:
        return None
    pin, pout = prices
    return (prompt_tokens * pin + completion_tokens * pout) / 1e6


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tier", required=True, choices=["node", "smart"])
    ap.add_argument("--models", help="comma-separated litellm ids; default: the verified shortlist")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--max-usd", type=float, default=0.5)
    ap.add_argument("--out", help="raw JSONL path; default docs/agents/llm-bench/raw/<date>-<tier>.jsonl")
    ap.add_argument("--report-md", action="store_true", help="print a markdown metrics table")
    args = ap.parse_args()

    load_bench_env()
    # Bench-level defaults; the operator overrides through the environment. 1500/2000 out tokens
    # leave room for models whose thinking pass is mandatory (they still pay for those tokens).
    import os

    os.environ.setdefault("LLM_NODE_TIMEOUT_S", "60")
    os.environ.setdefault("LLM_SMART_TIMEOUT_S", "180")
    os.environ.setdefault("LLM_NODE_MAX_TOKENS", "1500")
    os.environ.setdefault("LLM_SMART_MAX_TOKENS", "2000")

    models = [m.strip() for m in args.models.split(",")] if args.models else DEFAULT_MODELS[args.tier]
    persona = persona_system()
    perception = Perception(SAMPLE, salt="snapshot")
    cases = load_cases(args.tier, persona, perception)
    if not cases:
        print(f"no cases for tier {args.tier} in {CASES_DIR}")
        return 1

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else RAW_DIR / f"{date.today().isoformat()}-{args.tier}.jsonl"
    spent, records, stopped = 0.0, [], False
    print(f"bench tier={args.tier} models={len(models)} cases={len(cases)} repeat={args.repeat} max=${args.max_usd}")
    print(f"raw: {out_path}")

    for model in models:
        if stopped:
            break
        config = dataclasses.replace(LLMConfig.for_tier(args.tier), model=model)
        client = LLMClient(config)
        for case in cases:
            for i in range(args.repeat):
                if stopped:
                    break
                started = time.perf_counter()
                rec = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "model": model,
                    "case": case["name"],
                    "i": i,
                    "config": {"reasoning_off": config.reasoning_off, "max_tokens": config.max_tokens,
                               "temperature": config.temperature},
                    "prompt_sha256": hashlib.sha256((case["system"] + case["user"]).encode()).hexdigest()[:16],
                }
                try:
                    if case.get("json"):
                        reply = client.complete_json(case["system"], case["user"])
                        reply_text = json.dumps(reply, ensure_ascii=False)
                    else:
                        reply_text = client.complete(case["system"], case["user"])
                    stats = client.last_call
                    cost = stats.cost_usd if stats.cost_usd is not None else estimate_cost(
                        model, stats.prompt_tokens, stats.completion_tokens)
                    rec.update(ok=True, reply=reply_text,
                               stats={"prompt_tokens": stats.prompt_tokens,
                                      "completion_tokens": stats.completion_tokens,
                                      "latency_s": round(stats.latency_s, 3),
                                      "cost_usd": stats.cost_usd},
                               cost_usd=cost)
                    spent += cost or 0.0
                    rec["checks"] = validate(case, reply_text, perception)
                except LLMError as exc:
                    rec.update(ok=False, error=str(exc)[:300], cost_usd=0.0)
                records.append(rec)
                mark = "ok" if rec.get("ok") else f"FAIL {rec.get('error', '')[:60]}"
                print(f"  {model.split('/', 1)[-1]:42s} {case['name']:14s} #{i} {mark} "
                      f"{rec.get('stats', {}).get('latency_s', '?')}s ${rec.get('cost_usd') or 0:.5f}")
                if spent > args.max_usd:
                    stopped = True
                    print(f"BUDGET STOP: spent ${spent:.4f} > ${args.max_usd}")

    with out_path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"calls: {len(records)} (failed {sum(1 for r in records if not r.get('ok'))}), spent ${spent:.4f}"
          + (", STOPPED on budget" if stopped else ""))

    summary = summarize(models, records)
    print_summary(summary)
    if args.report_md:
        print_markdown(summary)
    return 0


def summarize(models: list[str], records: list[dict]) -> list[dict]:
    rows = []
    for model in models:
        mine = [r for r in records if r["model"] == model]
        ok = [r for r in mine if r.get("ok")]
        lat = [r["stats"]["latency_s"] for r in ok]
        json_cases = [r for r in ok if r["checks"]["json_ok"] is not None]
        json_ok = [r for r in json_cases if r["checks"]["json_ok"]]
        schema_cases = [r for r in ok if r["checks"]["schema_ok"] is not None]
        schema_ok = [r for r in schema_cases if r["checks"]["schema_ok"]]
        shares = [cyrillic_share(r.get("reply", "")) for r in ok]
        style = [f for r in ok for f in r["checks"]["style"]]
        in_tok = [r["stats"]["prompt_tokens"] for r in ok if r["stats"]["prompt_tokens"]]
        out_tok = [r["stats"]["completion_tokens"] for r in ok if r["stats"]["completion_tokens"]]
        rows.append({
            "model": model,
            "calls": len(mine),
            "failed": len(mine) - len(ok),
            "json_ok": f"{len(json_ok)}/{len(json_cases)}",
            "schema_ok": f"{len(schema_ok)}/{len(schema_cases)}",
            "p50": percentile(lat, 0.50),
            "p95": percentile(lat, 0.95),
            "avg_in": statistics.mean(in_tok) if in_tok else float("nan"),
            "avg_out": statistics.mean(out_tok) if out_tok else float("nan"),
            "cost": sum(r.get("cost_usd") or 0 for r in ok),
            "cyr": statistics.mean([s for s in shares if s is not None]) if any(s is not None for s in shares) else float("nan"),
            "style": len(style),
            "style_kinds": sorted({f.split("=")[0].split(":")[0] for f in style}),
            "usd_h": dollars_per_hour(model, ok),
        })
    return rows


def dollars_per_hour(model: str, ok_records: list[dict]) -> float | None:
    prices = PRICE_TABLE.get(model)
    if not prices:
        return None
    pin, pout = prices
    total = 0.0
    for kind, tier, per_hour, in_nom, out_nom in REGISTRY:
        mine = [r for r in ok_records if r["case"] == kind]
        if mine and all(r["stats"]["prompt_tokens"] for r in mine):
            in_t = statistics.mean(r["stats"]["prompt_tokens"] for r in mine)
            out_t = statistics.mean(r["stats"]["completion_tokens"] or 0 for r in mine)
        else:
            in_t, out_t = in_nom, out_nom
        total += per_hour * (in_t * pin + out_t * pout) / 1e6
    return total


def print_summary(rows: list[dict]) -> None:
    for r in rows:
        print(f"{r['model']}: calls={r['calls']} failed={r['failed']} json={r['json_ok']} "
              f"schema={r['schema_ok']} p50={r['p50']:.2f}s p95={r['p95']:.2f}s "
              f"in={r['avg_in']:.0f} out={r['avg_out']:.0f} ${r['cost']:.4f} "
              f"$/h={r['usd_h']:.3f} cyr={r['cyr']:.2f} style={r['style']} {r['style_kinds']}")


def print_markdown(rows: list[dict]) -> None:
    print("\n| модель | вызовов | JSON | схема | p50, с | p95, с | вх.ток | вых.ток | $ прогона | $/3 узла/ч | кириллица | стиль |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| `{r['model']}` | {r['calls']} | {r['json_ok']} | {r['schema_ok']} | {r['p50']:.2f} | "
              f"{r['p95']:.2f} | {r['avg_in']:.0f} | {r['avg_out']:.0f} | {r['cost']:.4f} | "
              f"{r['usd_h']:.3f} | {r['cyr']:.2f} | {r['style']} ({', '.join(r['style_kinds']) or '—'}) |")


if __name__ == "__main__":
    sys.exit(main())
