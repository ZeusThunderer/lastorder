"""Grade a finished build against its schematic, cell by cell, from the live world.

Runs inside mind-N (it has runtime/ and the py4j link to the body):

    docker exec -i lastorder-mind-1-1 python - "<schematic json>" X Y Z < dev/build_grade.py

Reads every cell of the box and of a 2-block ring around it (plus one layer above),
through a second py4j client without a callback server (closed with gateway.close()).
Prints: right / missing / wrong material / stray inside / scaffold around, per block.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from functools import lru_cache

from py4j.java_gateway import GatewayParameters, JavaGateway

from runtime.game.py4j_body import bridge_addresses
from runtime.mind.build import parse_schematic, ring_cells


def main(schematic: str, origin: tuple[int, int, int]) -> None:
    plan = parse_schematic(schematic)
    host, port, _, _ = bridge_addresses()
    gw = JavaGateway(gateway_parameters=GatewayParameters(address=host, port=port, auto_convert=True))
    mc = gw.entry_point
    try:
        @lru_cache(maxsize=None)
        def read(x: int, y: int, z: int) -> str:
            m = dict(mc.getBlockAt(int(x), int(y), int(z)))
            if str(m.get("is_air", "")).lower() == "true":
                return "air"
            raw = str(m.get("id") or m.get("block") or "")
            return raw.split(":", 1)[1] if raw.startswith("minecraft:") else raw

        ox, oy, oz = origin
        sx, sy, sz = plan.size
        right: Counter = Counter()
        missing: Counter = Counter()
        wrong: Counter = Counter()
        stray: Counter = Counter()
        cells: dict[str, list] = {"missing": [], "wrong": [], "stray": [], "scaffold": []}
        for lx in range(sx):
            for ly in range(sy):
                for lz in range(sz):
                    want = plan.cells.get((lx, ly, lz), "air")
                    got = read(ox + lx, oy + ly, oz + lz)
                    at = [lx, ly, lz]
                    if want == "air":
                        if got != "air":
                            stray[got] += 1
                            cells["stray"].append(at + [got])
                    elif got == want:
                        right[want] += 1
                    elif got == "air" or got in ("short_grass", "tall_grass", "water"):
                        missing[want] += 1
                        cells["missing"].append(at + [want])
                    else:
                        wrong[f"{want}->{got}"] += 1
                        cells["wrong"].append(at + [f"{want}->{got}"])
        scaffold: Counter = Counter()
        for x, y, z in ring_cells(origin, plan.size):
            got = read(x, y, z)
            if got != "air":
                scaffold[got] += 1
                cells["scaffold"].append([x - ox, y - oy, z - oz, got])
        total = len(plan.cells)
        directions = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
        open_blocks = {"air", "short_grass", "tall_grass", "water"}
        sealed = [cell for cell in cells["missing"] if all(
            read(ox + cell[0] + dx, oy + cell[1] + dy, oz + cell[2] + dz) not in open_blocks
            for dx, dy, dz in directions)]
        doorway = None
        if plan.entrance is not None:
            ex, ey, ez = plan.entrance
            doorway = [read(ox + ex, oy + ey + dy, oz + ez) for dy in (0, 1)]
        print(json.dumps({
            "name": plan.name, "origin": origin, "planned": total,
            "right": sum(right.values()), "score": round(sum(right.values()) / total, 3),
            "by_block_right": dict(right), "by_block_planned": dict(plan.materials),
            "missing": dict(missing), "wrong": dict(wrong),
            "sealed_missing": sealed, "exposed_missing": [c for c in cells["missing"] if c not in sealed],
            "doorway_feet_head": doorway,
            "stray_inside": dict(stray), "scaffold_ring": dict(scaffold),
        }, indent=1))
        for kind, rows in cells.items():
            if rows:
                print(kind, "(local x,y,z):", rows[:40])
    finally:
        gw.close()


if __name__ == "__main__":
    main(sys.argv[1], (int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])))
