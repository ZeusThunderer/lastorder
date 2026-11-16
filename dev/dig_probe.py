"""Measure one uc dig (altoclef DestroyBlockTask via destroyBlockAt) from where the body stands.

Runs inside mind-N, through a second py4j client without a callback server:

    docker exec -i lastorder-mind-1-1 python - X Y Z [limit_s] < dev/dig_probe.py

Resets uc's run counters and break-reset trace, starts the dig, samples the body every 0.2 s
(feet, on-ground, food, the target block) and prints where the time went: uc's near-target
refusal counters (noReach / air(borne) / hungry / unsafe), the break resets by site, and
the raw break events.
"""

from __future__ import annotations

import re
import sys
import time

from py4j.java_gateway import GatewayParameters, JavaGateway

from runtime.game.py4j_body import bridge_addresses

WANT = ("dbTick", "dbNearTick", "noReach", "air", "hungry", "unsafe", "blockedBy", "dbBlocked",
        "dbNoRetreat", "dbStepOver", "dbApproachStall", "dbUnreachMove", "dbNear", "dbFar",
        "dbReachGoal", "dbBuilderYield", "dbAimWait", "dbToolEquipped", "leafCleared")


def main(x: int, y: int, z: int, limit_s: float) -> None:
    host, port, _, _ = bridge_addresses()
    gw = JavaGateway(gateway_parameters=GatewayParameters(address=host, port=port, auto_convert=True))
    mc = gw.entry_point
    try:
        def block() -> str:
            m = dict(mc.getBlockAt(x, y, z))
            return "air" if str(m.get("is_air")).lower() == "true" else str(m.get("id"))

        def me() -> dict:
            return dict(dict(mc.getGameState()).get("self") or {})

        s = me()
        print("start:", {k: s.get(k) for k in ("pos", "onGround", "food", "saturation", "held")},
              "target:", block())
        mc.resetRunCounters()
        mc.breakResetStats(True)
        print("destroyBlockAt:", dict(mc.destroyBlockAt(x, y, z)))
        t0 = time.monotonic()
        last = None
        while time.monotonic() - t0 < limit_s:
            s = me()
            pos = [round(float(c), 2) for c in str(s.get("pos") or "").split(",") if c]
            row = (pos, s.get("onGround"), block())
            if row != last:
                print(f"t={time.monotonic() - t0:5.1f} pos={pos} ground={s.get('onGround')} "
                      f"food={s.get('food')} vel={s.get('vel')} target={row[2]}")
                last = row
            if row[2] == "air":
                break
            time.sleep(0.2)
        print(f"end t={time.monotonic() - t0:.1f}s target={block()}")
        stats = str(mc.placeStats())
        print("db:", " ".join(m.group(0) for m in re.finditer(r"(\w+)=(\S+)", stats)
                              if m.group(1) in WANT))
        print("breakResets:", str(mc.breakResetStats(False))[:800])
        print("breakEvents:", str(mc.breakEvents())[:1500])
    finally:
        gw.close()


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]), int(a[1]), int(a[2]), float(a[3]) if len(a) > 3 else 40.0)
