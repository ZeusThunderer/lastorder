"""Schematics for the body's build tool (runtime/mind/build.py, defence-plan W3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mind.build import (
    MAX_BLOCKS,
    SchematicError,
    block_id,
    buildable,
    material_check,
    parse_schematic,
)
from runtime.ports import ItemStack

LIBRARY = Path(__file__).resolve().parents[1] / "agent" / "last_order" / "skills" / "build" / "library"


def hut(**changes):
    """A 3x3x3 hut: floor, walls with a doorway, roof."""
    data = {
        "name": "hut",
        "size": [3, 4, 3],
        "palette": ["oak planks", "glass"],
        "regions": [
            {"from": [0, 0, 0], "to": [2, 3, 2], "block": "oak planks"},
            {"from": [1, 1, 1], "to": [1, 2, 1], "block": "air"},
            {"from": [1, 1, 0], "to": [1, 2, 0], "block": "air"},
            {"from": [2, 2, 1], "to": [2, 2, 1], "block": "glass"},
        ],
        "entrance": [1, 1, 0],
    }
    data.update(changes)
    return data


def test_regions_expand_in_order_with_air_carving():
    plan = parse_schematic(hut())
    assert plan.size == (3, 4, 3)
    assert (1, 1, 1) not in plan.cells and (1, 2, 0) not in plan.cells
    assert plan.cells[(2, 2, 1)] == "glass"
    assert plan.materials == {"oak_planks": 3 * 4 * 3 - 4 - 1, "glass": 1}
    assert plan.entrance == (1, 1, 0)


def test_placed_at_is_absolute_and_bottom_up():
    cells = parse_schematic(hut()).placed_at((100, 64, -20))
    assert cells[0][1] == 64 and cells[-1][1] == 67
    assert [100, 64, -20, "oak_planks"] in cells
    assert all(c[3] in {"oak_planks", "glass"} for c in cells)


def test_json_string_and_single_blocks():
    data = {
        "size": [1, 2, 1],
        "palette": ["stone"],
        "blocks": [[0, 0, 0, "stone"], [0, 1, 0, "blk:stone"]],
    }
    plan = parse_schematic(json.dumps(data))
    assert plan.cells == {(0, 0, 0): "stone", (0, 1, 0): "stone"}


@pytest.mark.parametrize(
    "value, expected",
    [("oak planks", "oak_planks"), ("blk:oak_planks", "oak_planks"),
     ("itm:Glass", "glass"), ("minecraft:stone_bricks", "stone_bricks")],
)
def test_block_names_are_the_plain_names_the_sister_sees(value, expected):
    assert block_id(value) == expected


@pytest.mark.parametrize("name", ["oak_planks", "cobblestone", "white_concrete", "glass_pane",
                                  "stone_bricks", "spruce_log", "smooth_sandstone", "gold_block"])
def test_buildable(name):
    assert buildable(name)


@pytest.mark.parametrize("name", ["oak_stairs", "oak_slab", "oak_door", "torch", "sand", "gravel",
                                  "white_concrete_powder", "red_bed", "chest", "water",
                                  "command_block", "tnt", "ladder"])
def test_not_buildable(name):
    assert not buildable(name)


@pytest.mark.parametrize(
    "changes, words",
    [
        ({"size": [3, 4]}, "three whole numbers"),
        ({"size": [64, 4, 3]}, "width is 64"),
        ({"palette": ["oak stairs"]}, "cannot place oak_stairs"),
        ({"palette": []}, "palette must list"),
        ({"regions": [{"from": [0, 0, 0], "to": [5, 0, 0], "block": "oak planks"}]}, "outside size"),
        ({"regions": [{"from": [2, 0, 0], "to": [0, 0, 0], "block": "oak planks"}]}, "must not exceed"),
        ({"regions": [{"from": [0, 0, 0], "to": [0, 0, 0], "block": "stone"}]}, "not in the palette"),
        ({"regions": [{"from": [0, 0, 0], "to": [0, 0, 0], "block": "oak planks",
                       "properties": {"facing": "north"}}]}, "orientation"),
        ({"regions": [{"from": [0, 1, 0], "to": [2, 1, 2], "block": "oak planks"}]}, "no foundation"),
        ({"regions": [{"from": [0, 0, 0], "to": [0, 0, 0], "block": "oak planks"},
                      {"from": [2, 2, 2], "to": [2, 2, 2], "block": "oak planks"}]}, "float"),
        ({"entrance": [1, 1, 1]}, None),  # carved interior: floor below, free above — fine
        ({"entrance": [0, 1, 0]}, "two free cells"),
        ({"rotation": 90}, "unknown field"),
    ],
)
def test_refusals_say_what_to_change(changes, words):
    if words is None:
        parse_schematic(hut(**changes))
        return
    with pytest.raises(SchematicError) as exc:
        parse_schematic(hut(**changes))
    assert words in str(exc.value)


def test_block_cap():
    big = {"size": [32, 2, 32], "palette": ["stone"],
           "regions": [{"from": [0, 0, 0], "to": [31, 1, 31], "block": "stone"}]}
    assert 32 * 2 * 32 > MAX_BLOCKS
    with pytest.raises(SchematicError, match="the most this body builds"):
        parse_schematic(big)


def test_material_check_reads_the_hotbar_only():
    plan = parse_schematic(hut())
    inventory = [
        ItemStack(name="minecraft:oak_planks", count=10, slot=0),
        ItemStack(name="minecraft:glass", count=5, slot=20),  # main inventory: not usable
    ]
    missing, short = material_check(plan, inventory)
    assert missing == ["glass"]
    assert short == [{"block": "oak_planks", "need": 31, "hotbar": 10}]
    missing, short = material_check(plan, inventory + [ItemStack(name="glass", count=1, slot=3)])
    assert missing == []


def test_build_verdict_names_the_cells_handed_back():
    from runtime.mind.executor import build_verdict, public_args

    assert build_verdict(10, {"placed": 7, "already": 3, "deferred": 0}) == ("done", "")
    verdict, reason = build_verdict(
        10, {"placed": 6, "already": 1, "deferred": 3, "reasons": {"no_material": 2, "no_face": 1}}
    )
    assert verdict == "failed"
    assert reason.startswith("built 7 of 10 cells; 3 handed back")
    assert "2 — the block is not in the hotbar" in reason
    assert public_args({"blocks": [[0, 0, 0, "stone"]] * 5, "name": "x"}) == {"blocks": 5, "name": "x"}


def test_the_library_schematics_all_parse():
    files = sorted(LIBRARY.glob("*.json"))
    assert len(files) >= 9
    for path in files:
        plan = parse_schematic(path.read_text(encoding="utf-8"))
        assert plan.cells, path.name
