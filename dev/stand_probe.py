"""Where can the body stand to place one block? uc's stands against every stand within reach.

Runs inside mind-N, through a second py4j client without a callback server:

    docker exec -i lastorder-mind-1-1 python - X Y Z find < dev/stand_probe.py
    docker exec -i lastorder-mind-1-1 python - X Y Z place NAME [limit_s] < dev/stand_probe.py

find: reads the world around the cell and prints
  - uc's candidates: BlockPlaceHelper.adjacentStand (a neighbour of the cell, dy -1..1),
    whether each is standable and whether a placing ray exists from its eye;
  - every standable cell within 4.5 of a face the block can go against, with a clear ray.
  Blocks are treated as full cubes (the build library has only full blocks).
place: hands uc's build queue this one cell from where the body stands now and reports
  whether it landed, where the body went, and the builder's walk trace.
"""

from __future__ import annotations

import math
import sys
import time

from py4j.java_gateway import GatewayParameters, JavaGateway

from runtime.game.py4j_body import bridge_addresses

REACH = 4.5
EYE = 1.62
DIRS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]
HORIZ = [(1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1)]


def main(x: int, y: int, z: int, mode: str, name: str, limit_s: float) -> None:
    host, port, _, _ = bridge_addresses()
    gw = JavaGateway(gateway_parameters=GatewayParameters(address=host, port=port, auto_convert=True))
    mc = gw.entry_point
    try:
        if mode == "place":
            place(mc, x, y, z, name, limit_s)
        else:
            find(mc, x, y, z)
    finally:
        gw.close()


def find(mc, x: int, y: int, z: int) -> None:
    solid: dict[tuple[int, int, int], bool] = {}

    def is_solid(c: tuple[int, int, int]) -> bool:
        if c not in solid:
            m = dict(mc.getBlockAt(*c))
            solid[c] = not (str(m.get("is_air")).lower() == "true"
                            or str(m.get("replaceable")).lower() == "true")
        return solid[c]

    t = (x, y, z)
    against = [(x + dx, y + dy, z + dz) for dx, dy, dz in DIRS if is_solid((x + dx, y + dy, z + dz))]
    print("target", t, "solid:", is_solid(t), "faces to place against:", against)

    def standable(f: tuple[int, int, int]) -> bool:
        return (not is_solid(f) and not is_solid((f[0], f[1] + 1, f[2]))
                and is_solid((f[0], f[1] - 1, f[2])))

    def ray(f: tuple[int, int, int]) -> tuple[float, tuple] | None:
        """Shortest clear ray from the eye at stand f to a face the block goes against."""
        if f == t or (f[0], f[1] + 1, f[2]) == t:
            return None
        eye = (f[0] + 0.5, f[1] + EYE, f[2] + 0.5)
        best = None
        for n in against:
            d = (n[0] - x, n[1] - y, n[2] - z)
            centre = (x + 0.5 + d[0] * 0.5, y + 0.5 + d[1] * 0.5, z + 0.5 + d[2] * 0.5)
            for u in (-0.3, 0.0, 0.3):
                for v in (-0.3, 0.0, 0.3):
                    p = list(centre)
                    axes = [i for i in range(3) if d[i] == 0]
                    p[axes[0]] += u
                    p[axes[1]] += v
                    dist = math.dist(eye, p)
                    if dist > REACH:
                        continue
                    steps = max(2, int(dist / 0.02))
                    ok = True
                    for k in range(1, steps):
                        q = [eye[i] + (p[i] - eye[i]) * k / steps for i in range(3)]
                        c = (math.floor(q[0]), math.floor(q[1]), math.floor(q[2]))
                        if c == t:
                            continue
                        if is_solid(c):
                            ok = False
                            break
                    if ok and (best is None or dist < best[0]):
                        best = (round(dist, 2), n)
        return best

    print("-- uc adjacentStand candidates (target neighbours, dy -1..1):")
    for dy in (-1, 0, 1):
        for dx, _, dz in HORIZ:
            f = (x + dx, y + dy, z + dz)
            if f == t or (f[0], f[1] + 1, f[2]) == t:
                continue
            print(f"   {f} standable={standable(f)} ray={ray(f)}")
    print("-- every standable cell with a clear placing ray within reach:")
    found = []
    for fx in range(x - 5, x + 6):
        for fz in range(z - 5, z + 6):
            for fy in range(y - 5, y + 3):
                f = (fx, fy, fz)
                if not standable(f):
                    continue
                r = ray(f)
                if r:
                    found.append((f, r))
    found.sort(key=lambda e: (abs(e[0][1] - (y - 1)), e[1][0]))
    for f, r in found[:15]:
        adj = abs(f[0] - x) + abs(f[2] - z) == 1 and -1 <= f[1] - y <= 1
        print(f"   {f} ray={r} {'(uc neighbour)' if adj else ''}")
    print(f"{len(found)} stands in reach; read {len(solid)} cells")


def place(mc, x: int, y: int, z: int, name: str, limit_s: float) -> None:
    def me() -> dict:
        return dict(dict(mc.getGameState()).get("self") or {})

    def cell() -> str:
        m = dict(mc.getBlockAt(x, y, z))
        return "air" if str(m.get("is_air")).lower() == "true" else str(m.get("id"))

    s = me()
    print("start:", {k: s.get(k) for k in ("pos", "onGround", "held")}, "cell:", cell())
    print("buildBlocks:", dict(mc.buildBlocks([[x, y, z, name]])))
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < limit_s:
        s = me()
        q = dict(mc.buildQueue())
        row = (s.get("pos"), s.get("onGround"), cell(), q.get("queued"))
        if row != last:
            print(f"t={time.monotonic() - t0:5.1f} pos={row[0]} ground={row[1]} cell={row[2]} queued={row[3]}")
            last = row
        if row[2] != "air" or not int(q.get("queued") or 0):
            break
        time.sleep(0.2)
    q = dict(mc.buildQueue())
    print(f"end t={time.monotonic() - t0:.1f}s cell={cell()} placed={q.get('placed')} "
          f"deferred={q.get('deferredCount')}")
    print("walk:", str(q.get("walkDebug") or "")[-400:])
    if int(q.get("queued") or 0):
        mc.buildQueueClear()


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]), int(a[1]), int(a[2]), a[3] if len(a) > 3 else "find",
         a[4] if len(a) > 4 else "", float(a[5]) if len(a) > 5 else 30.0)
