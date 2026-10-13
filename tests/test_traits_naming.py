from runtime.config import CHARACTER_DIR
from runtime.lastorder.naming import PRESETS, node_name, preset
from runtime.lastorder.traits import TRAIT_NAMES, load_quirks, roll

import pytest


def test_roll_is_reproducible_and_in_range():
    quirks = load_quirks(CHARACTER_DIR / "quirks.txt")
    a, b = roll(42, quirks), roll(42, quirks)
    assert a == b
    assert all(0 <= v <= 10 for v in a.numbers().values())
    assert set(a.numbers()) == set(TRAIT_NAMES)
    assert a.quirk in quirks


def test_different_seeds_give_different_nodes():
    quirks = load_quirks(CHARACTER_DIR / "quirks.txt")
    rolls = {tuple(roll(seed, quirks).numbers().values()) for seed in range(20)}
    assert len(rolls) > 15


def test_naming_presets():
    assert node_name(preset("lab"), 7) == "узел 00007"
    assert node_name(preset("misaka"), 7) == "Мисака 00007"
    with pytest.raises(ValueError):
        preset("nope")
    assert set(PRESETS) == {"lab", "misaka"}
