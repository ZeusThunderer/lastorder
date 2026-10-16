"""FastJev spike on the RTX 2070 (T-005, ADR-004/023): synthetic C1 states over HTTP.

    uv run python dev/jev_spike.py --n 200 [--url http://127.0.0.1:8730] [--gpu 0] [--save file.json]

For each option-list size (4, 8, 16 — texts are uc altoclef commands and levers, ADR-003) fires
`--n` choice requests with compact synthetic body states, after 10 warmup requests per size.
Prints p50/p95 latency per size, the share of in-format answers and the peak VRAM of the GPU
(nvidia-smi polled in a background thread; per-process memory reads as N/A under WSL2).
Exit code 1 if any size's in-format share is below 0.9.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import threading
import time

import httpx

SIZES = (4, 8, 16)
WARMUP = 10
QUESTION = "Which action should the body take right now?"

# Option pool: altoclef commands and uc levers (ADR-003, docs/agents/unionclef-bridge.md).
OPTIONS = [
    ("goto_water", "gotoXYZ to the nearby water edge and hold position there"),
    ("get_birch", "@get birch_log 8: collect 8 birch logs around"),
    ("stop", "@stop: cancel the current task and stand still"),
    ("get_food", "@food: find and eat food, hunger is low"),
    ("mine_stone", "mineBlock stone 12: mine 12 stone blocks at the marked spot"),
    ("place_torch", "placeBlockAt torch on the floor next to the body"),
    ("bridge_gap", "bridgeTo across the 4-block gap to the plateau"),
    ("pillar_up", "pillarTo 3 blocks up to reach the cave mouth"),
    ("punk_mob", "punk the hostile melee mob until it drops"),
    ("punk_stop", "punkStop: disengage and back away from the fight"),
    ("run_away", "runAwayPlayer from the closest threat, 16 blocks"),
    ("look_around", "lookAt the nearest entity and observe for one tick"),
    ("select_axe", "selectHotbar slot with the wooden axe"),
    ("hold_forward", "holdKey W for 2 seconds to close distance"),
    ("hold_sneak", "holdKey shift and move along the ledge"),
    ("tap_jump", "tapKey space to hop over the one-block step"),
]

ENTITIES = ["zombie", "creeper", "spider", "cow", "skeleton", "sheep"]
HELD = ["wooden_axe", "stone_pickaxe", "bread", "torch", "air"]


def synthetic_state(rng: random.Random, i: int) -> dict:
    """Compact C1 body state: the same fields GameBody.state() will carry (NETWORK.md §4)."""
    return {
        "tick": i,
        "hp": rng.randint(4, 20),
        "food": rng.randint(6, 20),
        "pos": [rng.randint(-200, 200), rng.randint(5, 70), rng.randint(-200, 200)],
        "held": rng.choice(HELD),
        "looking_at": rng.choice(["birch_log", "stone", "cave_wall", "water", "grass_block"]),
        "nearest_hostile": {"kind": rng.choice(ENTITIES[:4]), "dist_m": rng.randint(2, 18)},
        "nearest_passive": {"kind": rng.choice(ENTITIES[4:]), "dist_m": rng.randint(3, 24)},
        "event": rng.choice(
            ["damage 3", "item picked up", "path blocked", "task idle 12s", "hp regen", None]
        ),
        "intent": rng.choice(["get wood", "make bed", "find shelter", "explore cave", None]),
    }


def choice_request(state: dict, size: int) -> dict:
    criteria = {oid: desc for oid, desc in OPTIONS[:size]}
    return {
        "model": "fastjev",
        "state": state,
        "questions": {"action": {"type": "choice", "instructions": QUESTION, "criteria": criteria}},
    }


def in_format(reply: dict, size: int) -> bool:
    expected = {oid for oid, _ in OPTIONS[:size]}
    try:
        answer = reply["answers"]["action"]
        # the System One wire format names the pick `choice` (fastjev also emits `type`)
        picked = answer.get("choice", answer.get("value"))
        return (
            picked in expected
            and expected <= set(answer["probabilities"])
            and isinstance(answer["confidence"], (int, float))
        )
    except (KeyError, TypeError):
        return False


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


class VramPoller(threading.Thread):
    """nvidia-smi -i GPU memory.used polled every 0.2 s; per-process VRAM reads N/A under WSL2."""

    def __init__(self, gpu: int):
        super().__init__(daemon=True)
        self.gpu = gpu
        self.peak_mib = 0
        self.stop = threading.Event()

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "-i", str(self.gpu), "--query-gpu=memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                self.peak_mib = max(self.peak_mib, int(out.splitlines()[0]))
            except Exception:
                pass
            self.stop.wait(0.2)


def served_model_id(models_reply) -> str:
    """The request `model` must equal the served model id; take it from GET /v1/models."""
    if isinstance(models_reply, dict):
        for key in ("data", "models"):  # OpenAI-like and fastjev shapes
            entries = models_reply.get(key)
            if isinstance(entries, list) and entries:
                first = entries[0]
                return first.get("id") or first.get("name") if isinstance(first, dict) else str(first)
        if "id" in models_reply:
            return models_reply["id"]
    return "fastjev-2070"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=200, help="measured requests per option-list size")
    ap.add_argument("--url", default="http://127.0.0.1:8730")
    ap.add_argument("--gpu", type=int, default=0, help="nvidia-smi index to watch for peak VRAM")
    ap.add_argument("--save", help="write raw latencies and replies summary to this JSON file")
    args = ap.parse_args()

    rng = random.Random(42)
    poller = VramPoller(args.gpu)
    poller.start()
    rows = []
    with httpx.Client(timeout=30) as client:
        models = client.get(f"{args.url}/v1/models").json()
        model_id = served_model_id(models)
        print(f"service: {args.url} models={json.dumps(models)[:200]} -> using model id {model_id!r}")
        for size in SIZES:
            latencies, good = [], 0
            for i in range(WARMUP + args.n):
                request = choice_request(synthetic_state(rng, i), size)
                request["model"] = model_id
                started = time.perf_counter()
                try:
                    reply = client.post(f"{args.url}/v1/systemone", json=request).json()
                    ok = in_format(reply, size)
                except Exception as exc:
                    print(f"  request failed at size {size} #{i}: {exc}")
                    reply, ok = None, False
                latency = time.perf_counter() - started
                if i >= WARMUP:
                    latencies.append(latency * 1000)
                    good += ok
                if reply is None and i > WARMUP + 5:
                    print(f"  giving up on size {size}: service not answering")
                    break
            rows.append({
                "size": size,
                "n": len(latencies),
                "in_format": good / len(latencies) if latencies else 0.0,
                "p50_ms": percentile(latencies, 0.50),
                "p95_ms": percentile(latencies, 0.95),
                "mean_ms": statistics.mean(latencies) if latencies else float("nan"),
            })
            print(f"  size {size:2d}: n={rows[-1]['n']} in-format={good}/{len(latencies)} "
                  f"p50={rows[-1]['p50_ms']:.0f} ms p95={rows[-1]['p95_ms']:.0f} ms")
    poller.stop.set()
    poller.join(timeout=2)

    print(f"peak VRAM on GPU {args.gpu}: {poller.peak_mib} MiB (nvidia-smi memory.used)")
    print("\n| вариантов | n | в формате | p50, мс | p95, мс | среднее, мс |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['size']} | {r['n']} | {r['in_format']:.2f} | {r['p50_ms']:.0f} | "
              f"{r['p95_ms']:.0f} | {r['mean_ms']:.0f} |")
    if args.save:
        import pathlib

        pathlib.Path(args.save).write_text(
            json.dumps({"url": args.url, "gpu": args.gpu, "peak_vram_mib": poller.peak_mib,
                        "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"saved: {args.save}")
    return 1 if any(r["in_format"] < 0.9 for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
