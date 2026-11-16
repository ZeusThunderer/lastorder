"""Record everything uc exposes about the body, read-only, into one JSONL file.

Runs inside mind-N through its own py4j client (no callback server; closed with close()):

    docker exec -d lastorder-mind-1-1 python /tmp/body_recorder.py /tmp/rec.jsonl [fast_s [slow_s [limit_s]]]

Stops when /tmp/rec.stop exists or after limit_s. Each line is one sample:
  {"t": wall, "m": since start, "kind": "fast"|"slow"|"once", "ms": {call: latency}, ...values}
fast (every fast_s, default 0.2): game state (pos, vel, onGround, yaw/pitch, hp, food, held,
  lookingAt, msSinceBreakProgress...), crosshair, task chain, current goal, build queue with its
  walk trace, pillar, threat, speed, ground block, runner state, tungsten/baritone activity.
slow (every slow_s, default 2): full inventory, entities, recent chat, open screen, and every
  counter dump uc has (place/dig/food/gate/chase/dodge/close/punk/interruptions/perf/lava),
  break events and break resets.
Only getters are called; nothing here moves the body.
"""

from __future__ import annotations

import json
import os
import sys
import time

from py4j.java_gateway import GatewayParameters, JavaGateway

from runtime.game.py4j_body import bridge_addresses

FAST = {
    "state": lambda mc: mc.getGameState(),
    "crosshair": lambda mc: mc.getCrosshairTarget(),
    "task_chain": lambda mc: mc.getTaskChainString(),
    "goal": lambda mc: mc.getCurrentGoalString(),
    "build_queue": lambda mc: mc.buildQueue(),
    "pillar_active": lambda mc: mc.pillarActive(),
    "pillar_placed": lambda mc: mc.pillarPlaced(),
    "scaffold_count": lambda mc: mc.scaffoldCount(),
    "threat": lambda mc: mc.getThreatStatus(),
    "speed": lambda mc: [mc.getSpeedX(), mc.getSpeedY(), mc.getSpeedZ()],
    "ground_block": lambda mc: mc.getGroundBlock(),
    "runner": lambda mc: mc.taskRunnerState(),
    "has_task": lambda mc: mc.hasActiveTask(),
    "tungsten_active": lambda mc: mc.isTungstenActive(),
    "baritone_goal": lambda mc: mc.hasBaritoneGoal(),
}
SLOW = {
    "inventory": lambda mc: mc.getInventoryFull(),
    "entities": lambda mc: mc.getEntitiesInfo(24),
    "chat": lambda mc: mc.getRecentChat(10),
    "screen": lambda mc: mc.getOpenScreen(),
    "place_stats": lambda mc: mc.placeStats(),
    "dig_stats": lambda mc: mc.digStats(),
    "food_stats": lambda mc: mc.foodStats(),
    "gate_stats": lambda mc: mc.gateStats(),
    "chase_stats": lambda mc: mc.chaseStats(),
    "dodge_stats": lambda mc: mc.dodgeStats(),
    "close_stats": lambda mc: mc.closeStats(),
    "punk_stats": lambda mc: mc.punkStats(),
    "interruptions": lambda mc: mc.getTaskInterruptionStats(),
    "perf": lambda mc: mc.getPerfStats(),
    "lava": lambda mc: mc.lavaEntryStats(),
    "break_events": lambda mc: mc.breakEvents(),
    "break_resets": lambda mc: mc.breakResetStats(False),
    "walker_bfs_ticks": lambda mc: mc.walkerBfsTicks(),
    "runner_status": lambda mc: mc.getRunnerStatus(),
}
ONCE = {
    "info": lambda mc: mc.getInfo(),
    "pipeline": lambda mc: mc.getPipelineDescription(),
    "username": lambda mc: mc.getUsername(),
    "voice": lambda mc: mc.isVoiceChatConnected(),
}


def plain(value):
    """py4j proxies (JavaMap/JavaList) to JSON-able Python, recursively."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        if hasattr(value, "keySet") or isinstance(value, dict):
            return {str(k): plain(v) for k, v in dict(value).items()}
    except Exception:
        pass
    try:
        return [plain(v) for v in list(value)]
    except Exception:
        return str(value)


def sample(mc, calls: dict, kind: str, t0: float) -> dict:
    row: dict = {"t": round(time.time(), 3), "m": round(time.monotonic() - t0, 3), "kind": kind, "ms": {}}
    for name, fn in calls.items():
        s = time.monotonic()
        try:
            row[name] = plain(fn(mc))
        except Exception as exc:  # one failing getter never stops the record
            row[name] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        row["ms"][name] = round((time.monotonic() - s) * 1000, 1)
    return row


def main(path: str, fast_s: float, slow_s: float, limit_s: float) -> None:
    host, port, _, _ = bridge_addresses()
    gw = JavaGateway(gateway_parameters=GatewayParameters(address=host, port=port, auto_convert=True))
    mc = gw.entry_point
    stop = path.rsplit(".", 1)[0] + ".stop"
    t0 = time.monotonic()
    try:
        with open(path, "a", encoding="utf-8") as out:
            out.write(json.dumps(sample(mc, ONCE, "once", t0), ensure_ascii=False) + "\n")
            next_slow = 0.0
            while time.monotonic() - t0 < limit_s and not os.path.exists(stop):
                began = time.monotonic()
                out.write(json.dumps(sample(mc, FAST, "fast", t0), ensure_ascii=False) + "\n")
                if began - t0 >= next_slow:
                    out.write(json.dumps(sample(mc, SLOW, "slow", t0), ensure_ascii=False) + "\n")
                    next_slow = began - t0 + slow_s
                out.flush()
                time.sleep(max(0.0, fast_s - (time.monotonic() - began)))
    finally:
        gw.close()


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], float(a[1]) if len(a) > 1 else 0.2, float(a[2]) if len(a) > 2 else 2.0,
         float(a[3]) if len(a) > 3 else 3600.0)
