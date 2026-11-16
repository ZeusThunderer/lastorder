"""Read a dev/body_recorder.py JSONL and say where the body's time went.

    python dev/analyze_recording.py .stand/run-birch2/rec.jsonl [mind.jsonl]

Prints: jumps (take-offs with upward speed) and who held the body at each one, time per mode
(pillar / build-walk / dig task / placing / idle), placement stalls with the position and the
builder's walk trace, food and hp, break resets, threat changes, and, with the mind journal,
the intent phases on the same clock.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter


def pos(row: dict) -> tuple[float, float, float] | None:
    try:
        return tuple(float(c) for c in str(row["state"]["self"]["pos"]).split(","))  # type: ignore[return-value]
    except Exception:
        return None


def vel_y(row: dict) -> float:
    try:
        return float(str(row["state"]["self"]["vel"]).split(",")[1])
    except Exception:
        return 0.0


def task(row: dict) -> str:
    chain = str(row.get("task_chain") or "")
    chain = chain.replace("Description of current game pipeline that is selected:", "").strip()
    return re.sub(r"\s+", " ", chain)[:160]


def mode(row: dict) -> str:
    q = row.get("build_queue") or {}
    if row.get("pillar_active") is True:
        return "pillar"
    t = task(row)
    if "Destroy" in t or "destroy" in t:
        return "dig-task"
    if int(q.get("queued") or 0) > 0:
        return "build-queue"
    if row.get("has_task") is True:
        return "other-task"
    return "idle"


def main(path: str, mind: str | None) -> None:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    fast = [r for r in rows if r.get("kind") == "fast"]
    slow = [r for r in rows if r.get("kind") == "slow"]
    if not fast:
        print("no fast samples")
        return
    t0 = fast[0]["t"]
    span = fast[-1]["t"] - t0
    gaps = [b["t"] - a["t"] for a, b in zip(fast, fast[1:])]
    print(f"samples: {len(fast)} fast, {len(slow)} slow over {span:.0f} s "
          f"(median gap {sorted(gaps)[len(gaps) // 2]:.2f} s, max {max(gaps):.2f} s)")

    # --- time per mode
    spent: Counter = Counter()
    for a, b in zip(fast, fast[1:]):
        spent[mode(a)] += b["t"] - a["t"]
    print("time per mode:", {k: round(v) for k, v in spent.most_common()})

    # --- jumps: a take-off is on-ground -> airborne with upward speed
    jumps = []
    for a, b in zip(fast, fast[1:]):
        ga = (a.get("state") or {}).get("self", {}).get("onGround")
        gb = (b.get("state") or {}).get("self", {}).get("onGround")
        if ga is True and gb is False and vel_y(b) > 0.2:
            jumps.append(b)
    print(f"jumps: {len(jumps)} ({len(jumps) / max(span / 60, 1e-9):.1f}/min)")
    print("  by mode:", dict(Counter(mode(j) for j in jumps).most_common()))
    print("  by task:", [(k[:110], n) for k, n in Counter(task(j) for j in jumps).most_common(6)])

    # --- placement progress and stalls (queued > 0, placed not moving for > 20 s)
    stalls = []
    start = None
    last_placed = None
    for r in fast:
        q = r.get("build_queue") or {}
        placed, queued = int(q.get("placed") or 0), int(q.get("queued") or 0)
        if queued and placed == last_placed:
            start = start or r
        else:
            if start and r["t"] - start["t"] > 20:
                stalls.append((start, r))
            start = None
        last_placed = placed
    print(f"placement stalls > 20 s: {len(stalls)}, {sum(b['t'] - a['t'] for a, b in stalls):.0f} s in total")
    for a, b in stalls[:12]:
        seg = [r for r in fast if a["t"] <= r["t"] <= b["t"]]
        js = sum(1 for x, y in zip(seg, seg[1:])
                 if (x.get("state") or {}).get("self", {}).get("onGround") is True
                 and (y.get("state") or {}).get("self", {}).get("onGround") is False and vel_y(y) > 0.2)
        ps = [p for p in (pos(r) for r in seg) if p]
        moved = max((abs(p[i] - ps[0][i]) for p in ps for i in range(3)), default=0.0) if ps else 0.0
        q = b.get("build_queue") or {}
        print(f"  t={a['t'] - t0:6.0f}..{b['t'] - t0:6.0f} ({b['t'] - a['t']:4.0f} s) at {pos(a)} jumps={js} "
              f"moved={moved:.1f} modes={dict(Counter(mode(r) for r in seg))} queued={q.get('queued')} "
              f"deferred={q.get('deferredCount')}")
        print(f"     walk: {str(q.get('walkDebug') or '')[-260:]}")

    # --- builder walk trace events
    events: Counter = Counter()
    seen = ""
    for r in fast:
        w = str((r.get("build_queue") or {}).get("walkDebug") or "")
        if w != seen:
            new = w[len(seen):] if w.startswith(seen) else w
            for e in re.findall(r"(PILLARUNREACHED|PILLAR|AIMTIMEOUT|TIMEOUT|EXHAUSTED|NOSTAND|SCAFFOLD|pillarbase=|for=)", new):
                events[e] += 1
            seen = w
    print("builder walk events:", dict(events))

    # --- food, hp, threat
    food = [((r["t"] - t0), (r.get("state") or {}).get("self", {}).get("food"),
             (r.get("state") or {}).get("self", {}).get("hp")) for r in fast[:: max(1, len(fast) // 12)]]
    print("food/hp over time:", [(round(t), f, h) for t, f, h in food])
    threats = []
    last = None
    for r in fast:
        th = re.sub(r"\s+", " ", str(r.get("threat") or ""))
        th = th.replace("=== Player Threat Table Status ===", "").strip()
        if th != last:
            threats.append((round(r["t"] - t0), th[:140]))
            last = th
    print("threat changes:", threats[:10])
    tasks = []
    last = None
    for r in fast:
        t = task(r)
        if t != last:
            tasks.append((round(r["t"] - t0), t[:120]))
            last = t
    print(f"task chain changes: {len(tasks)}; first ones:")
    for t, name in tasks[:25]:
        print(f"  t={t:6d} {name}")
    if slow:
        print("break resets (end):", str(slow[-1].get("break_resets"))[:400])
        print("food stats (end):", str(slow[-1].get("food_stats"))[:300])
        print("interruptions (end):", str(slow[-1].get("interruptions"))[:600])

    if mind:
        print("--- mind intents on the same clock")
        for line in open(mind, encoding="utf-8"):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("kind") in ("intent", "event", "death", "threat", "evade") and e.get("kind") != "call":
                keep = {k: e[k] for k in e if k not in ("ts", "args", "kind")}
                print(f"  t={e['ts'] - t0:6.0f} {e.get('kind')} {json.dumps(keep, ensure_ascii=False)[:180]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
