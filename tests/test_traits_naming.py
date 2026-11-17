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
    assert node_name(preset("lab"), 7) == "сестра 00007"
    assert node_name(preset("misaka"), 7) == "Мисака 00007"
    with pytest.raises(ValueError):
        preset("nope")
    assert set(PRESETS) == {"lab", "misaka"}


def test_presets_are_content_of_the_template(tmp_path):
    # ADR-054: naming.yml holds the presets; the code only reads it
    from runtime.lastorder.naming import HEIR_NEUTRAL, load_presets
    from runtime.lastorder.will import HEIR_ADDRESSES

    presets, neutral = load_presets(CHARACTER_DIR / "naming.yml")
    assert presets == PRESETS and neutral == HEIR_NEUTRAL == "наследник"
    assert HEIR_ADDRESSES == {"lab": "следующая сестра", "misaka": "следующая Мисака"}
    assert "сказала Мисака 00007»." in PRESETS["misaka"].speech_style
    with pytest.raises(FileNotFoundError):
        load_presets(tmp_path / "missing.yml")
    broken = tmp_path / "naming.yml"
    broken.write_text("presets:\n  lab: {name_format: 'x'}\nheir_neutral: h\n", encoding="utf-8")
    with pytest.raises(ValueError, match="speech_style"):
        load_presets(broken)


def test_session_openers_are_content_of_the_template(tmp_path):
    from runtime.node.runner import LIFE_PREFIX, WILL_PREFIX, load_openers

    openers = load_openers(CHARACTER_DIR / "session_openers.yml")
    assert openers["life"] == LIFE_PREFIX == "Life session."
    assert WILL_PREFIX == "Will session (you have just died)."
    (tmp_path / "o.yml").write_text("life: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="will_death"):
        load_openers(tmp_path / "o.yml")
