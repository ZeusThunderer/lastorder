"""One-off: jevcraft's ready-made schematics -> the sisters' build library (ADR-057).

Source: github.com/rkhazh/jevcraft (branch tolik-hermes), data/schematics/*.json, MIT,
Copyright (c) 2026 Matt Rubens. uc's buildBlocks places no oriented blocks, so stairs
and slabs become the full block of their material and doors become an open doorway.

    python -I dev/convert_jevcraft_schematics.py <jevcraft>/data/schematics agent/last_order/skills/build/library
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE = "github.com/rkhazh/jevcraft data/schematics (MIT, (c) 2026 Matt Rubens)"


def full_block(block: str) -> str | None:
    """None: the cell becomes air (a door)."""
    if block.endswith("_door"):
        return None
    for suffix in ("_stairs", "_slab"):
        if block.endswith(suffix):
            base = block[: -len(suffix)]
            if base in {"oak", "spruce", "birch", "jungle", "acacia", "dark_oak", "cherry",
                        "mangrove", "bamboo", "crimson", "warped", "pale_oak"}:
                return f"{base}_planks"
            if base.endswith("_brick"):
                return f"{base}_bricks"
            return base
    return block


def convert(src: dict) -> dict:
    s = src.get("source", src)
    regions, used = [], []
    for r in s["regions"]:
        block = full_block(r["block"]) if r["block"] != "air" else "air"
        block = block or "air"
        regions.append({"from": r["from"], "to": r["to"], "block": block})
        if block != "air" and block not in used:
            used.append(block)
    out = {
        "name": s["name"],
        "description": f"{s.get('description', '')} Adapted from {SOURCE}: stairs and slabs "
                       "as full blocks, doors as an open doorway.".strip(),
        "size": s["size"],
        "palette": used,
        "regions": regions,
    }
    if s.get("entrance"):
        out["entrance"] = s["entrance"]
    return out


def main(src_dir: str, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(src_dir).glob("*.json")):
        data = convert(json.loads(path.read_text(encoding="utf-8")))
        (out / path.name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(path.name, data["size"], len(data["regions"]), data["palette"])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
