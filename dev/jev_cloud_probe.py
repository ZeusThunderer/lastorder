"""Cloud Jev probe through the llm-gateway (T-031, ADR-027; plan review P1-4).

    uv run python dev/jev_cloud_probe.py --n 100 --max-usd 0.05

Measures the cloud Jev model (the TypeSafe System One model behind the OpenRouter id
`typesafe/jev-router`) on synthetic C1 threat states, with and without a sample node threat
policy — 2*`--n` chat-completion calls of EXACTLY the shape `runtime/mind.jev` builds (the
prompt comes from that module, not a copy). Reports p50/p95 wall latency per arm, the
in-format share (a JSON object naming an offered option), input/output tokens, the computed
cost and the share of per-call failures (which is exactly the share of C1 rules fallbacks a
deployment timeout produces). Exits 1 only when the budget guard stopped the loop or an arm
produced no successful call at all; the numbers themselves — including an in-format share
below 0.9 — are data for docs/agents/jev.md, not a probe failure (the suitability verdict
is written there, not encoded in an exit code).

The call path is the production one: through the llm-gateway proxy, never straight to the
provider (ADR-033). The running `lastorder` stack (T-028's stand) is not touched: the probe
brings up its own throwaway compose project (`-p lastorder-jevprobe`) with THIS checkout's
gateway config — the only place tier `jev` exists yet — and removes it at the end. Only the
provider key is lifted from the repo root .env (same quote-peeling as docker/gateway/up.sh);
the probe gateway gets its own random master key and its own throwaway Postgres, so the live
stack's master key never reaches it. The request loop runs inside the llm-gateway container
(`docker compose exec ... python -`, the dev/gateway_keys.py pattern): no secret ever lands
in an argv, and the measured latency covers the real gateway->OpenRouter network path (the
mind's extra docker-network hop is ~1 ms, noted in jev.md). The probe's spend lands on the
probe gateway's own master key and is not tracked in the live stack's DB.

OpenRouter's catalog lists the model with `-1` pricing (price varies), so cost is COMPUTED
from the reported token usage at the quoted prices (--price-in/--price-out, defaults from
ADR-027: $0.042 per 1M input tokens, output free), not read from a billing field. The
budget guard stops the loop before the computed total passes --max-usd.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.mind.jev import (  # noqa: E402
    MAX_TOKENS_DEFAULT,
    MODEL_DEFAULT,
    TEMPERATURE_DEFAULT,
    Option,
    build_messages,
    parse_decision,
)

COMPOSE_FILE = ROOT / "docker" / "compose.yml"
PROBE_PROJECT = "lastorder-jevprobe"

# The C1-shaped world of the probe: what _build_jev_request offers in the real contour.
FLEE = Option("flee", "break off and run away from the threat", "goto", {})
FIGHT = Option("fight", "strike the hostile", "strike", {})
KEEP = Option("keep", "hold the current intent; no maneuver", "keep", {})
FIGHT_RADIUS_M = 3.0  # the executor's MIND_FIGHT_RADIUS_M default

HOSTILES = ["zombie", "creeper", "skeleton", "spider"]
HELD = ["wooden axe", "stone pickaxe", "bread", "torch", "air"]
INTENTS = [
    {"lever": "goto", "args": {"x": 120, "y": 64, "z": -30}},
    {"lever": "get", "args": {"item": "oak_log", "count": 8}},
    {"lever": "mine", "args": {"positions": [[118, 63, -30]]}},
    None,
]

SAMPLE_POLICY = """# threat policy of node 00042 (sample for the T-031 probe)

I am worth more alive than brave.
- Below 8 hp: run. Do not look back.
- A creeper within 6 blocks: run immediately, even at full hp.
- A zombie or a spider in reach and my hp is 12 or more: strike it once, then reassess.
- A skeleton: never chase; run and break the line of sight.
- Food below 6: never fight, even in reach — eat first, survive second, pride last.
Keep my tools: never fight bare-handed while the axe is in the hotbar."""

# Runs INSIDE the llm-gateway container via `python -` (the whole script rides stdin, the
# dev/gateway_keys.py pattern): the host appends `JOB = ...` (the embedded JSON job) after
# this source. It fires the calls one by one against the local proxy with the master key
# from the container env, emits one JSON line per call and one final summary line.
# Stdlib only.
RUNNER = r'''
import json, os, sys, time, urllib.error, urllib.request

job = JOB  # appended by the host: the JSON job as a Python literal
master = os.environ.get("LITELLM_MASTER_KEY", "").strip()
if not master:
    sys.exit("no LITELLM_MASTER_KEY in the llm-gateway container: lift GATEWAY_MASTER_KEY "
             "from the repo root .env first (docker/gateway/up.sh does the same)")

def discover_model():
    req = urllib.request.Request("https://openrouter.ai/api/v1/models",
                                 headers={"User-Agent": "last-order-t031-probe"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        for m in json.load(resp)["data"]:
            if "jev" in m["id"].lower():
                return m["id"]
    return None

model = job["model"]
if not model:
    model = discover_model()
    if not model:
        sys.exit("no jev model in the OpenRouter catalog and --model not given")
print(json.dumps({"model": model, "discovered": not job["model"]}))

spent = 0.0
stopped_early = None
for call in job["calls"]:
    body = {
        "model": model,
        "messages": call["messages"],
        "max_tokens": job["max_tokens"],
        "temperature": job["temperature"],
        # the production client (runtime/llm/client.py, reasoning_off=True) sends the same
        # extra: the probe must measure the identical request shape
        "reasoning": {"enabled": False},
    }
    req = urllib.request.Request(
        "http://127.0.0.1:4000/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + master,
                 "Content-Type": "application/json"},
        method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=job["http_timeout_s"]) as resp:
            reply = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        # a per-call failure (e.g. the deployment timeout aborting a slow provider) is
        # DATA — the share of calls that would fall back to the C1 rules — not a stop
        print(json.dumps({"arm": call["arm"], "i": call["i"], "ok": False,
                          "status": exc.code, "error": detail}))
        continue
    except Exception as exc:
        print(json.dumps({"arm": call["arm"], "i": call["i"], "ok": False,
                          "status": 0, "error": "%s: %s" % (type(exc).__name__, exc)[:300]}))
        continue
    latency_ms = (time.perf_counter() - started) * 1000.0
    usage = reply.get("usage") or {}
    p_tok = int(usage.get("prompt_tokens") or 0)
    c_tok = int(usage.get("completion_tokens") or 0)
    cost = (p_tok * job["price_in_per_m"] + c_tok * job["price_out_per_m"]) / 1e6
    spent += cost
    text = ""
    try:
        text = reply["choices"][0]["message"]["content"] or ""
    except (KeyError, TypeError, IndexError):
        pass
    print(json.dumps({"arm": call["arm"], "i": call["i"], "ok": True,
                      "latency_ms": round(latency_ms, 1), "prompt_tokens": p_tok,
                      "completion_tokens": c_tok, "cost_usd": round(cost, 8),
                      "spent_usd": round(spent, 8),
                      "reply_head": text[:400]}))
    if spent >= job["max_usd"]:
        stopped_early = "budget guard"
        break
print(json.dumps({"done": True, "stopped_early": stopped_early}), flush=True)
'''


def lift_env() -> None:
    """The provider key from the MAIN worktree root .env (worktrees have no copy), quotes
    peeled like docker/gateway/up.sh; values are never printed. The probe gateway is a
    throwaway project with no published ports and its own throwaway Postgres, so it gets a
    RANDOM master key when none is exported — the live stack's master key never has to
    reach it (GATEWAY_DB_PASSWORD likewise falls back to the compose default)."""
    main_root = subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    root = Path(main_root[0].split(" ", 1)[1]) if main_root else ROOT
    env_file = root / ".env"
    values: dict[str, str] = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            val = val.strip().strip('"').strip("'")
            if val:
                values[key.strip()] = val
    import os
    import secrets

    for key in ("GATEWAY_MASTER_KEY", "OPENROUTER_API_KEY", "GATEWAY_DB_PASSWORD"):
        if not os.environ.get(key) and values.get(key):
            os.environ[key] = values[key]
    if not os.environ.get("GATEWAY_MASTER_KEY"):
        os.environ["GATEWAY_MASTER_KEY"] = "sk-" + secrets.token_urlsafe(32)  # probe-stack-local


def compose(project: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE), *args],
        capture_output=True, text=True,
    )


def wait_healthy(project: str, timeout_s: float = 150.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = subprocess.run(
            ["docker", "compose", "-p", project, "exec", "-T", "llm-gateway", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness')"],
            capture_output=True,
        )
        if probe.returncode == 0:
            return True
        time.sleep(3.0)
    return False


def synthetic_calls(n: int, with_policy: bool, rng: random.Random) -> tuple[list[dict], dict[int, list[Option]]]:
    """2*n... one arm of n calls in the exact runtime.mind.jev prompt shape."""
    calls, options_by_i = [], {}
    for i in range(n):
        dist = round(rng.uniform(1.0, 8.0), 1)
        snapshot = {
            "hp": rng.randint(4, 20),
            "food": rng.randint(6, 20),
            "pos": [rng.randint(-200, 200), rng.randint(5, 70), rng.randint(-200, 200)],
            "held": rng.choice(HELD),
            "threat": {"kind": rng.choice(HOSTILES), "dist_m": dist},
            "intent": rng.choice(INTENTS),
        }
        options = [FLEE, KEEP]
        if dist <= FIGHT_RADIUS_M:
            options.insert(1, FIGHT)
        policy = SAMPLE_POLICY if with_policy else None
        system, user = build_messages(snapshot, policy, options)
        calls.append({
            "arm": "policy" if with_policy else "bare",
            "i": i,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        })
        options_by_i[i] = options
    return calls, options_by_i


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))] if ordered else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=100, help="measured calls per arm (bare, policy)")
    ap.add_argument("--max-usd", type=float, default=0.05, help="computed-cost stop guard")
    ap.add_argument("--model", default="", help="gateway model id; default: discover from the OpenRouter catalog")
    ap.add_argument("--price-in", type=float, default=0.042, help="$ per 1M input tokens (ADR-027)")
    ap.add_argument("--price-out", type=float, default=0.0, help="$ per 1M output tokens (ADR-027: free)")
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS_DEFAULT,
                    help="per-call completion budget (the router model reasons first; output is free)")
    ap.add_argument("--http-timeout-s", type=float, default=30.0, help="per-call HTTP timeout inside the container")
    ap.add_argument("--project", default=PROBE_PROJECT, help="throwaway compose project (never `lastorder`)")
    ap.add_argument("--keep-stack", action="store_true", help="do not remove the probe project afterwards")
    ap.add_argument("--save", default=str(ROOT / "docs" / "agents" / "jev-cloud-probe-results.json"))
    args = ap.parse_args()

    if args.project == "lastorder":
        print("jev_cloud_probe: --project must not be the live stack's project", file=sys.stderr)
        return 2

    rng = random.Random(20261018)
    bare_calls, bare_options = synthetic_calls(args.n, with_policy=False, rng=rng)
    policy_calls, policy_options = synthetic_calls(args.n, with_policy=True, rng=rng)
    options_by_arm = {"bare": bare_options, "policy": policy_options}

    print(f"probe project: {args.project} (own gateway + db, this checkout's config with tier jev)")
    lift_env()
    up = compose(args.project, "up", "-d", "gateway-db", "llm-gateway")
    if up.returncode != 0:
        print(up.stdout + up.stderr, file=sys.stderr)
        return 2
    try:
        if not wait_healthy(args.project):
            print("jev_cloud_probe: the probe gateway never became healthy", file=sys.stderr)
            return 2
        job = {
            "model": args.model.strip(),
            "calls": bare_calls + policy_calls,
            "max_tokens": args.max_tokens,
            "temperature": TEMPERATURE_DEFAULT,
            "http_timeout_s": args.http_timeout_s,
            "price_in_per_m": args.price_in,
            "price_out_per_m": args.price_out,
            "max_usd": args.max_usd,
        }
        run = subprocess.run(
            ["docker", "compose", "-p", args.project, "exec", "-T", "llm-gateway", "python", "-"],
            # `python -` reads its PROGRAM from stdin, so the job rides inside the source:
            # the assignment must precede RUNNER (which uses JOB at import time).
            input="import json\nJOB = json.loads(%r)\n" % json.dumps(job) + RUNNER,
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            print(run.stdout + run.stderr, file=sys.stderr)
            return 2

        model_id = args.model.strip() or MODEL_DEFAULT
        rows: dict[str, dict] = {}
        results: list[dict] = []
        stopped_early = None
        for line in run.stdout.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "model" in record:
                model_id = record["model"]
                print(f"model: {model_id}" + (" (discovered from the OpenRouter catalog)" if record.get("discovered") else ""))
                continue
            if record.get("done"):
                stopped_early = record.get("stopped_early")
                continue
            results.append(record)
            arm = record["arm"]
            row = rows.setdefault(arm, {"n": 0, "good": 0, "errors": 0, "lat": [], "p_tok": 0, "c_tok": 0, "cost": 0.0})
            row["n"] += 1
            options = options_by_arm[arm][record["i"]]
            if record.get("ok"):
                row["lat"].append(record["latency_ms"])
                row["p_tok"] += record.get("prompt_tokens") or 0
                row["c_tok"] += record.get("completion_tokens") or 0
                row["cost"] += record.get("cost_usd") or 0.0
                try:
                    parse_decision(record.get("reply_head") or "", options)
                    row["good"] += 1
                except Exception:
                    pass
            else:
                row["errors"] += 1
                print(f"  failed call {arm} #{record['i']}: {record.get('status')}: {record.get('error', '')[:120]}")
        total_cost = sum(r["cost"] for r in rows.values())
        print(f"\nmodel {model_id}, arms: bare (no policy) / policy (~{len(SAMPLE_POLICY)} chars sample)")
        print("\n| рука | n | ошибок | в формате | p50, мс | p95, мс | среднее, мс | вход ток. | выход ток. | стоимость |")
        print("|---|---|---|---|---|---|---|---|---|---|")
        for arm in ("bare", "policy"):
            r = rows.get(arm)
            if not r or not r["lat"]:
                print(f"| {arm} | {r['n'] if r else 0} | {r['errors'] if r else 0} | — | — | — | — | — | — | — |")
                continue
            print(
                f"| {arm} | {r['n']} | {r['errors']} | {r['good']}/{r['n']} ({r['good']/r['n']:.2f}) "
                f"| {percentile(r['lat'], 0.5):.0f} | {percentile(r['lat'], 0.95):.0f} "
                f"| {statistics.mean(r['lat']):.0f} | {r['p_tok']/r['n']:.0f} ср. | {r['c_tok']/r['n']:.0f} ср. "
                f"| ${r['cost']:.5f} |"
            )
        print(f"\ncomputed cost (prices {args.price_in}/{args.price_out} $/1M in/out): ${total_cost:.5f} "
              f"of the ${args.max_usd:.2f} guard")
        if stopped_early:
            print(f"STOPPED EARLY: {stopped_early}")
        if args.save:
            Path(args.save).write_text(
                json.dumps({
                    "model": model_id, "prices": {"in": args.price_in, "out": args.price_out},
                    "stopped_early": stopped_early, "total_cost_usd": round(total_cost, 6),
                    "arms": {arm: {"n": r["n"], "good": r["good"], "errors": r["errors"],
                                   "p50_ms": percentile(r["lat"], 0.5), "p95_ms": percentile(r["lat"], 0.95),
                                   "mean_ms": statistics.mean(r["lat"]) if r["lat"] else None,
                                   "avg_prompt_tokens": r["p_tok"]/r["n"] if r["n"] else 0,
                                   "avg_completion_tokens": r["c_tok"]/r["n"] if r["n"] else 0,
                                   "cost_usd": round(r["cost"], 6)}
                              for arm, r in rows.items()},
                    "results": results,
                }, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            print(f"saved: {args.save}")
        for arm, r in rows.items():
            if r["n"] and r["good"] / r["n"] < 0.9:
                print(f"WARNING: {arm} in-format {r['good']}/{r['n']} < 0.9 — data for jev.md, not a probe failure")
        empty_arm = any(not r["lat"] for r in rows.values())
        return 1 if (stopped_early or empty_arm) else 0
    finally:
        if not args.keep_stack:
            compose(args.project, "down", "--remove-orphans")


if __name__ == "__main__":
    sys.exit(main())
