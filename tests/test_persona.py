import re

import pytest

from runtime.config import CHARACTER_DIR
from runtime.lastorder.naming import preset
from runtime.lastorder.persona import Inheritance, Node, PersonaError, PersonaProvider, render
from runtime.lastorder.traits import load_quirks, roll


def make(preset_key="lab", **inheritance):
    node = Node(serial=7, traits=roll(1, load_quirks(CHARACTER_DIR / "quirks.txt")))
    return PersonaProvider(preset(preset_key)).system_prompt(node, Inheritance(**inheritance))


def test_template_renders_without_leftover_placeholders():
    prompt = make()
    assert not re.search(r"\{[a-z_]+\}", prompt)
    assert "<!--" not in prompt
    assert "узел 00007" in prompt
    assert "none yet" in prompt  # empty inheritance is spelled out, not left blank


@pytest.mark.parametrize("key,name", [("lab", "узел 00007"), ("misaka", "Мисака 00007")])
def test_presets_change_name_and_style(key, name):
    prompt = make(key)
    assert name in prompt
    assert preset(key).speech_style in prompt


def test_inheritance_lands_in_prompt():
    prompt = make(inherited_notes="Узлу 00008: не трогать светлые столбы.", collective_terms="«пятнистая колонна»")
    assert "не трогать светлые столбы" in prompt
    assert "«пятнистая колонна»" in prompt


def test_trait_numbers_are_in_prompt():
    node = Node(serial=1, traits=roll(3, ["a quirk"]))
    prompt = PersonaProvider(preset("lab")).system_prompt(node, Inheritance())
    assert f"bravery {node.traits.bravery}," in prompt
    assert "quirk: a quirk" in prompt


def test_render_rejects_missing_and_unused():
    with pytest.raises(PersonaError):
        render("{a} {b}", {"a": "1"})
    with pytest.raises(PersonaError):
        render("{a}", {"a": "1", "typo": "2"})
