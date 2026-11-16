"""Schematics for the body's `build` tool (defence-plan W3, ADR-057).

A schematic is the sister's own drawing of a structure in local coordinates, in the cuboid
format of jevcraft's generative designer (github.com/rkhazh/jevcraft, src/designer.js,
MIT): `size` [w, h, d], a `palette` of at most 16 blocks, `regions` of inclusive filled
cuboids applied in order — `air` carves openings — and, for a drawing made by a script,
`blocks`: single cells [x, y, z, block] applied after the regions. Unspecified cells are
air and touch nothing in the world.

The check is jevcraft's validateSchematic narrowed to what uc's `buildBlocks` can place:
full blocks with no orientation (no stairs, slabs, doors, beds, torches), nothing that
falls or flows. Every failure is one sentence the sister can act on, so she fixes the
drawing and calls again — the designer's retry-with-feedback loop.

Names are the plain names the sister sees (ADR-037): "oak planks", "oak_planks",
"blk:oak_planks", "itm:oak_planks" are one block. Pure: no body, no I/O.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_SIZE = (32, 24, 32)       # w, h, d; a tower may be tall, a castle is a later problem
MAX_BLOCKS = 2000             # ~7 min of placing at uc's human rate (1 block / 4 ticks)
MAX_REGIONS = 256
MAX_PALETTE = 16
MAX_NAME = 80
AIR = "air"

# Full cubes and self-connecting blocks uc places with no orientation. A whitelist by
# family: an unknown name is a rejection with the reason, never a guess.
_FAMILIES = re.compile(
    r"^(?:"
    r"[a-z_]+_(?:planks|log|wood|wool|terracotta|concrete|bricks|stained_glass|"
    r"stained_glass_pane|leaves|fence|wall)"
    r"|stripped_[a-z_]+_(?:log|wood)"
    r"|(?:polished|smooth|cut|chiseled|cracked|mossy)_[a-z_]+"
    r"|[a-z_]+_block"
    r")$"
)
_SINGLES = frozenset({
    "stone", "cobblestone", "mossy_cobblestone", "bricks", "glass", "glass_pane",
    "terracotta", "sandstone", "red_sandstone", "andesite", "diorite", "granite",
    "deepslate", "cobbled_deepslate", "tuff", "calcite", "dirt", "coarse_dirt", "mud_bricks",
    "packed_mud", "glowstone", "sea_lantern", "bookshelf", "crafting_table", "furnace",
    "pumpkin", "melon", "hay_block", "obsidian", "netherrack", "end_stone", "purpur_block",
    "quartz_block", "prismarine", "dark_prismarine", "blackstone", "basalt", "clay",
    "snow_block", "ice", "packed_ice", "moss_block", "shroomlight", "amethyst_block",
    "iron_bars",
})
# Matches a family pattern but cannot be placed as a plain cube: falls, flows, needs an
# orientation or a second cell, or is an air-like / technical block.
_EXCLUDED = re.compile(
    r"(?:stairs|slab|door|trapdoor|bed|torch|sign|button|pressure_plate|gate|banner|"
    r"carpet|powder|sand$|gravel|lava|water|command_block|structure_block|jigsaw|barrier|"
    r"spawner|tnt|anvil|piston|observer|hopper|dropper|dispenser|rail|chest|shulker)"
)


class SchematicError(ValueError):
    """A schematic the body cannot build; the message says what to change."""


def block_id(value: Any, where: str = "block") -> str:
    """A plain block name to its registry id: "oak planks" / "blk:oak_planks" -> oak_planks."""
    if not isinstance(value, str) or not value.strip():
        raise SchematicError(f"{where}: a block name is a non-empty string, got {value!r}")
    name = value.strip().lower()
    for prefix in ("blk:", "itm:", "minecraft:"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    name = re.sub(r"[\s-]+", "_", name)
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise SchematicError(f"{where}: {value!r} is not a plain block name")
    return name


def buildable(name: str) -> bool:
    if name in _SINGLES:
        return True
    return bool(_FAMILIES.match(name)) and not _EXCLUDED.search(name)


@dataclass(frozen=True)
class Plan:
    """A checked schematic: the solid cells in local coordinates and what they need."""

    name: str
    size: tuple[int, int, int]
    cells: dict[tuple[int, int, int], str]
    entrance: tuple[int, int, int] | None

    @property
    def materials(self) -> dict[str, int]:
        return dict(Counter(self.cells.values()).most_common())

    def placed_at(self, origin: Iterable[int]) -> list[list[Any]]:
        """Absolute [x, y, z, block] cells, bottom layer first (uc sorts again anyway)."""
        ox, oy, oz = (int(c) for c in origin)
        return [
            [ox + x, oy + y, oz + z, block]
            for (x, y, z), block in sorted(self.cells.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))
        ]


def _vector(value: Any, where: str) -> tuple[int, int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(isinstance(v, bool) or not isinstance(v, int) for v in value)
    ):
        raise SchematicError(f"{where} must be three whole numbers [x, y, z], got {value!r}")
    return int(value[0]), int(value[1]), int(value[2])


def _load(schematic: Any) -> Mapping[str, Any]:
    if isinstance(schematic, str):
        try:
            schematic = json.loads(schematic)
        except json.JSONDecodeError as exc:
            raise SchematicError(f"schematic is not valid JSON: {exc}") from None
    if not isinstance(schematic, Mapping):
        raise SchematicError("schematic must be an object with size, palette and regions")
    return schematic


KNOWN_FIELDS = {"name", "description", "size", "palette", "regions", "blocks", "entrance"}


def parse_schematic(schematic: Any) -> Plan:
    """Check a schematic and expand it into cells; SchematicError says what is wrong."""
    data = _load(schematic)
    unknown = sorted(set(data) - KNOWN_FIELDS)
    if unknown:
        raise SchematicError(f"unknown field(s) {unknown}; known: {sorted(KNOWN_FIELDS)}")
    name = data.get("name") or "unnamed"
    if not isinstance(name, str) or len(name) > MAX_NAME:
        raise SchematicError(f"name must be a string of at most {MAX_NAME} characters")

    if "size" not in data:
        raise SchematicError("size [width, height, depth] is required")
    size = _vector(data["size"], "size")
    for axis, n, cap in zip(("width", "height", "depth"), size, MAX_SIZE):
        if n < 1:
            raise SchematicError(f"{axis} is {n}; every dimension must be at least 1")
        if n > cap:
            raise SchematicError(f"{axis} is {n}; the most this body builds is {cap}")

    raw_palette = data.get("palette")
    if not isinstance(raw_palette, list) or not raw_palette:
        raise SchematicError("palette must list the blocks the schematic uses")
    palette = {block_id(b, "palette") for b in raw_palette} - {AIR}
    if len(palette) > MAX_PALETTE:
        raise SchematicError(f"palette has {len(palette)} blocks; at most {MAX_PALETTE}")
    bad = sorted(b for b in palette if not buildable(b))
    if bad:
        raise SchematicError(
            f"cannot place {', '.join(bad)}: only full blocks with no orientation "
            "(no stairs, slabs, doors, torches, beds, nothing that falls or flows) — "
            "leave a doorway as air, use planks for a roof"
        )

    regions = data.get("regions") or []
    singles = data.get("blocks") or []
    if not isinstance(regions, list) or not isinstance(singles, list):
        raise SchematicError("regions and blocks must be lists")
    if not regions and not singles:
        raise SchematicError("nothing to build: give regions or blocks")
    if len(regions) > MAX_REGIONS:
        raise SchematicError(f"{len(regions)} regions; at most {MAX_REGIONS}")

    def inside(p: tuple[int, int, int], where: str) -> None:
        if any(c < 0 or c >= n for c, n in zip(p, size)):
            raise SchematicError(f"{where} {list(p)} is outside size {list(size)} (0-based)")

    def block_of(value: Any, where: str) -> str:
        block = block_id(value, where)
        if block != AIR and block not in palette:
            raise SchematicError(f"{where}: {block} is not in the palette")
        return block

    cells: dict[tuple[int, int, int], str] = {}
    for i, region in enumerate(regions):
        where = f"regions[{i}]"
        if not isinstance(region, Mapping) or set(region) - {"from", "to", "block", "properties"}:
            raise SchematicError(f"{where} must be {{from, to, block}}")
        if region.get("properties"):
            raise SchematicError(f"{where}: properties (orientation) cannot be placed by this body")
        lo, hi = _vector(region.get("from"), f"{where}.from"), _vector(region.get("to"), f"{where}.to")
        inside(lo, f"{where}.from")
        inside(hi, f"{where}.to")
        if any(a > b for a, b in zip(lo, hi)):
            raise SchematicError(f"{where}: from {list(lo)} must not exceed to {list(hi)}")
        block = block_of(region.get("block"), f"{where}.block")
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                for z in range(lo[2], hi[2] + 1):
                    if block == AIR:
                        cells.pop((x, y, z), None)
                    else:
                        cells[(x, y, z)] = block
    for i, cell in enumerate(singles):
        where = f"blocks[{i}]"
        if not isinstance(cell, (list, tuple)) or len(cell) != 4:
            raise SchematicError(f"{where} must be [x, y, z, block]")
        p = _vector(list(cell[:3]), where)
        inside(p, where)
        block = block_of(cell[3], where)
        if block == AIR:
            cells.pop(p, None)
        else:
            cells[p] = block

    if not cells:
        raise SchematicError("the schematic carves everything away: no solid block is left")
    if len(cells) > MAX_BLOCKS:
        raise SchematicError(
            f"that is {len(cells)} blocks; the most this body builds is {MAX_BLOCKS} — "
            "split it into parts or make it smaller"
        )
    _check_connected(cells)
    entrance = _check_entrance(data.get("entrance"), cells, size)
    return Plan(name=name, size=size, cells=cells, entrance=entrance)


_NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _check_connected(cells: Mapping[tuple[int, int, int], str]) -> None:
    """Every solid cell reaches the foundation (y = 0) through solid faces: a floating
    piece has nothing to be placed against."""
    queue = [p for p in cells if p[1] == 0]
    if not queue:
        raise SchematicError("no foundation: nothing at y = 0, the layer that stands on the ground")
    seen = set(queue)
    for p in queue:
        for dx, dy, dz in _NEIGHBOURS:
            n = (p[0] + dx, p[1] + dy, p[2] + dz)
            if n in cells and n not in seen:
                seen.add(n)
                queue.append(n)
    floating = len(cells) - len(seen)
    if floating:
        example = next(list(p) for p in cells if p not in seen)
        raise SchematicError(
            f"{floating} block(s) float, not joined to the foundation through other blocks "
            f"(e.g. {example})"
        )


def _check_entrance(
    value: Any, cells: Mapping[tuple[int, int, int], str], size: tuple[int, int, int]
) -> tuple[int, int, int] | None:
    """An entrance, when given, is a doorway: floor below, two free cells to stand in."""
    if value is None:
        return None
    e = _vector(value, "entrance")
    if any(c < 0 or c >= n for c, n in zip(e, size)) or e[1] < 1 or e[1] + 1 >= size[1]:
        raise SchematicError("entrance must be inside size, above the foundation, with headroom")
    below, foot, head = (e[0], e[1] - 1, e[2]), e, (e[0], e[1] + 1, e[2])
    if below not in cells or foot in cells or head in cells:
        raise SchematicError(
            f"entrance {list(e)} needs a floor below and two free cells to walk through"
        )
    return e


MAX_SCAN = 6000  # cells of the schematic's box read before a build (one uc call each)


def site_conflicts(
    plan: Plan, origin: Iterable[int], read: Any
) -> list[list[Any]]:
    """Cells of the schematic's box the world holds otherwise: [x, y, z, block id].

    `read(x, y, z)` -> {"id", "air", "replaceable"} (the body's block_at). A conflict is
    anything in a cell the plan leaves air (a tree inside the house, grass on its floor),
    and a solid block other than the planned one where the plan puts a block — uc's
    builder skips a solid cell as done, so a log in the wall would stay a log. A
    replaceable plant under a planned block is not a conflict: the block goes over it.
    Top-down order, so digging never leaves a block hanging over the body."""
    ox, oy, oz = (int(c) for c in origin)
    w, h, d = plan.size
    if w * h * d > MAX_SCAN:
        raise SchematicError(
            f"the box is {w}x{h}x{d} = {w * h * d} cells; the site check reads at most {MAX_SCAN}"
        )
    found = []
    for y in range(h - 1, -1, -1):
        for x in range(w):
            for z in range(d):
                cell = read(ox + x, oy + y, oz + z)
                if cell.get("air"):
                    continue
                want = plan.cells.get((x, y, z))
                if want is None or (not cell.get("replaceable") and cell.get("id") != want):
                    found.append([ox + x, oy + y, oz + z, str(cell.get("id"))])
    return found


def material_check(
    plan: Plan, inventory: Iterable[Any], slots: int = 36,
    cells: Iterable[Sequence[Any]] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """(missing, short): blocks absent from the inventory (hotbar + main, slots 0-35),
    and blocks it holds fewer of than the plan needs — or, given `cells` ([x, y, z,
    block] still to place), than those cells need. The body moves stacks into the
    hotbar itself before each placing round (uc's builder takes from slots 0-8, and with
    the named block absent it places whatever is in hand — so an absent block is a
    refusal, never a warning)."""
    have: Counter[str] = Counter()
    for stack in inventory:
        if 0 <= int(getattr(stack, "slot", -1)) < slots:
            have[block_id(str(getattr(stack, "name", "")) or "air", "inventory")] += int(
                getattr(stack, "count", 0)
            )
    bill = plan.materials if cells is None else Counter(str(c[3]) for c in cells)
    missing, short = [], []
    for block, need in bill.items():
        got = have.get(block, 0)
        if got == 0:
            missing.append(block)
        elif got < need:
            short.append({"block": block, "need": need, "have": got})
    return missing, short


def ring_cells(origin: Iterable[int], size: Iterable[int], margin: int = 2) -> list[list[int]]:
    """Cells around the schematic's box, `margin` wide, from the foundation to one above
    the top: where the builder's own scaffolding (pillars, bridges) lands."""
    ox, oy, oz = (int(c) for c in origin)
    w, h, d = (int(c) for c in size)
    cells = []
    for y in range(oy, oy + h + 1):
        for x in range(ox - margin, ox + w + margin):
            for z in range(oz - margin, oz + d + margin):
                if not (ox <= x < ox + w and oz <= z < oz + d and y < oy + h):
                    cells.append([x, y, z])
    return cells
