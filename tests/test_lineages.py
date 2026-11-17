from pathlib import Path

import pytest
import yaml

from runtime.lastorder.lineages import PROFILES_PATH, lineage, load_lineages
from runtime.lastorder.persona import Node, PersonaProvider
from runtime.lastorder.naming import preset
from runtime.lastorder.traits import roll


def test_profiles_survive_reload_and_are_not_shared_between_slots():
    first, second = load_lineages(), load_lineages()
    assert first == second
    assert len({p.traits for p in first.values()}) == 3
    assert len({p.memory_key for p in first.values()}) == 3


def test_unknown_slot_is_not_a_random_or_other_sisters_profile():
    with pytest.raises(ValueError, match="unknown lineage slot"):
        lineage("LastOrder99")


@pytest.mark.parametrize("bad", [-1, 11, True, "5"])
def test_bad_operator_trait_is_rejected(tmp_path, bad):
    data = yaml.safe_load(PROFILES_PATH.read_text(encoding="utf-8"))
    data["profiles"]["LastOrder01"]["traits"]["bravery"] = bad
    path = tmp_path / "lineages.yml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="integers in 0..10"):
        load_lineages(path)


def test_profile_is_readonly_and_historical_nodes_still_render():
    from dataclasses import FrozenInstanceError
    profile = lineage("LastOrder01")
    with pytest.raises(FrozenInstanceError):
        profile.character = "replace identity"
    provider = PersonaProvider(preset("lab"))
    old = provider.render_core(Node(7, roll(123, ["historical quirk"])))
    assert "historical quirk" in old
    assert "ent:lineage-01" not in old
    current = provider.render_core(Node(8, profile.traits, slot="LastOrder01"))
    assert profile.memory_key in current
    assert profile.character in current
