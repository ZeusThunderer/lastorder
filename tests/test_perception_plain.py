"""Plain mode (ADR-020): normalized names, stable `<kind>:<id>` keys, terms on top."""

import pytest

from runtime.config import perception_mode
from runtime.perception.translate import Perception


def plain(salt="epoch-1"):
    return Perception({}, salt=salt, mode="plain")


def test_plain_key_is_stable_and_bridges_display_and_registry_forms():
    p = plain()
    assert p.handle("minecraft:birch_leaves", "block") == "blk:birch_leaves"
    assert p.handle("Birch Leaves", "block") == "blk:birch_leaves"
    assert p.handle("birch leaves", "block") == "blk:birch_leaves"
    assert p.handle("minecraft:birch_log", "item") == "itm:birch_log"
    # epoch-independent, unlike masked handles: fit for the knowledge base across lives
    assert plain("epoch-2").handle("minecraft:birch_leaves", "block") == "blk:birch_leaves"


def test_plain_see_gives_key_readable_name_and_term():
    p = plain()
    seen = p.see("minecraft:birch_log", "block")
    assert seen.render() == "blk:birch_log birch log"
    p.name_term(seen.handle, "пятнистая колонна")
    assert p.see("minecraft:birch_log", "block").render() == "blk:birch_log birch log «пятнистая колонна»"


def test_plain_raw_works_for_keys_of_both_kinds():
    p = plain()
    assert p.raw(p.handle("Birch Leaves", "block")) == "birch_leaves"
    assert p.raw(p.see("zombie", "entity").handle) == "zombie"
    with pytest.raises(KeyError):
        p.raw("blk:never_seen")


def test_plain_prompt_holds_no_namespaced_ids():
    p = plain()
    lines = [p.see(raw, kind).render() for raw, kind in [("birch_log", "block"), ("zombie", "entity")]]
    assert p.leaks("\n".join(lines)) == []


def test_plain_scrub_drops_only_the_namespace():
    p = plain()
    p.see("birch_log", "block")
    clean = p.scrub("got minecraft:birch_log and minecraft:diamond")
    assert clean == "got birch_log and diamond"
    assert p.leaks(clean) == []


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        Perception({}, salt="s", mode="stealth")


def test_perception_mode_env_default_plain_and_validated(monkeypatch):
    monkeypatch.delenv("PERCEPTION_MODE", raising=False)
    assert perception_mode() == "plain"
    monkeypatch.setenv("PERCEPTION_MODE", "masked")
    assert perception_mode() == "masked"
    monkeypatch.setenv("PERCEPTION_MODE", " PLAIN ")
    assert perception_mode() == "plain"  # hand-edited .env: case and spaces tolerated
    monkeypatch.setenv("PERCEPTION_MODE", "stealth")
    with pytest.raises(ValueError):
        perception_mode()
