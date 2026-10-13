import pytest

from runtime.perception.translate import Perception, normalize

DESCRIPTIONS = {"birch_log": "светлый столб с тёмными штрихами", "zombie": "стонущее прямоходящее"}


def perception(salt="epoch-1"):
    return Perception(DESCRIPTIONS, salt=salt)


def test_handles_are_stable_opaque_and_salted():
    p = perception()
    h = p.handle("minecraft:birch_log", "block")
    assert h == p.handle("birch_log", "block")
    assert h.startswith("blk:") and "birch" not in h
    assert h != perception("epoch-2").handle("birch_log", "block")


def test_see_uses_term_then_description_then_generic():
    p = perception()
    log = p.see("birch_log", "block")
    assert log.label.endswith(DESCRIPTIONS["birch_log"])
    p.name_term(log.handle, "пятнистая колонна")
    assert p.see("birch_log", "block").label == "«пятнистая колонна»"
    assert "незнакомый блок" in p.see("deepslate", "block").label


def test_first_term_wins():
    p = perception()
    h = p.see("zombie", "entity").handle
    p.name_term(h, "стонущий")
    p.name_term(h, "зелёный")
    assert p.terms[h] == "стонущий"


def test_reverse_map_for_commands():
    p = perception()
    h = p.see("birch_log", "block").handle
    assert p.raw(h) == "birch_log"
    with pytest.raises(KeyError):
        p.raw("blk:0000")


def test_scrub_removes_every_seen_form():
    p = perception()
    p.see("birch_log", "block")
    p.see("zombie", "entity")
    text = "Zombie was slain while chopping Birch Log; got minecraft:birch_log and minecraft:diamond"
    clean = p.scrub(text)
    assert p.leaks(clean) == []
    assert "[неопознанное]" in clean


def test_leaks_detects_raw_ids_and_namespaced():
    p = perception()
    p.see("zombie", "entity")
    assert p.leaks("рядом zombie") == ["zombie"]
    assert p.leaks("minecraft:stone") == ["minecraft:stone"]
    assert p.leaks("зомбирование и zombies") == []  # only whole words


def test_rendered_observation_has_no_leaks():
    p = perception()
    lines = [p.see(raw, kind).render() for raw, kind in [("birch_log", "block"), ("zombie", "entity"), ("dirt", "block")]]
    assert p.leaks("\n".join(lines)) == []


def test_normalize():
    assert normalize(" Minecraft:Oak_Log ") == "oak_log"
