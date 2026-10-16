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
