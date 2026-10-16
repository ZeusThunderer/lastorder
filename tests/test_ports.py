"""NETWORK.md section 4: ports are typing.Protocols, payloads are plain dataclasses."""

import dataclasses
import inspect
import typing

from runtime import ports

DOCUMENTED_PORTS = frozenset(
    {
        "GameBody",
        "Decider",
        "PersonaProvider",
        "Memory",
        "NodeRegistry",
        "NodeComms",
    }
)
STEP_REPLY_FIELDS = frozenset({"option_id", "why", "say", "new_term", "note", "msg", "votes"})


def module_classes() -> dict[str, type]:
    """Classes defined in ports.py itself; imports like Node or Traits stay out."""
    return {
        name: obj
        for name, obj in vars(ports).items()
        if inspect.isclass(obj) and obj.__module__ == ports.__name__
    }


def is_protocol(cls: type) -> bool:
    return issubclass(cls, typing.Protocol)


def test_every_documented_port_is_a_protocol():
    for name in sorted(DOCUMENTED_PORTS):
        assert is_protocol(getattr(ports, name)), name


def test_protocol_set_matches_the_document():
    found = {name for name, cls in module_classes().items() if is_protocol(cls)}
    assert found == set(DOCUMENTED_PORTS)


def test_every_non_port_class_is_a_plain_dataclass():
    for name, cls in module_classes().items():
        if name in DOCUMENTED_PORTS:
            continue
        assert dataclasses.is_dataclass(cls), name
        assert not is_protocol(cls), name


def test_each_port_declares_at_least_one_method():
    for name in sorted(DOCUMENTED_PORTS):
        methods = [m for m in vars(getattr(ports, name)) if not m.startswith("_")]
        assert methods, name


def test_step_reply_matches_the_documented_schema():
    fields = {f.name for f in dataclasses.fields(ports.StepReply)}
    assert fields == set(STEP_REPLY_FIELDS)


def test_game_body_declares_the_observation_getters():
    methods = {m for m in vars(ports.GameBody) if not m.startswith("_")}
    assert {"state", "entities", "blocks_around", "inventory", "recent_chat"} <= methods


def test_entity_and_block_info_carry_an_optional_cell_pos():
    # The pos default keeps every existing constructor call working; T-020 fills
    # it from uc's "x, y, z" strings, broken ones landing on None.
    assert ports.EntityInfo(kind="cow", name="Cow", dist_m=3.0).pos is None
    assert ports.BlockInfo(name="dirt").pos is None
    assert ports.EntityInfo(kind="cow", name="Cow", dist_m=3.0, pos=(1, 2, 3)).pos == (1, 2, 3)
    assert ports.BlockInfo(name="dirt", pos=(4, 5, 6)).pos == (4, 5, 6)


def test_memory_methods_carry_the_node_serial():
    for name in (
        "inheritance",
        "knowledge",
        "core_memory",
        "store_core_memory",
        "submit_candidate",
        "submit_vote",
    ):
        params = inspect.signature(getattr(ports.Memory, name)).parameters
        assert "serial" in params, name


def test_decider_receives_the_step_intent():
    params = inspect.signature(ports.Decider.choose).parameters
    assert "intent" in params


def test_node_registry_declares_the_death_request():
    params = inspect.signature(ports.NodeRegistry.request_death).parameters
    assert {"serial", "cause"} <= set(params)
