"""The closed action set of the infrastructure bridge (T-057, ADR-044, TASKBOARD.md 5.4).

Last Order rules the network's composition herself, with no per-action approval
(ADR-044): she posts a board request whose `action` is one of the seven here, the
host-side executor (`dev/infra_bridge.py`) runs it at once and reports the result
back onto the board. The set is closed on purpose — no arbitrary shell, no docker
beyond these verbs; widening it is a task change (ADR-044), never an agent request.

This module is the one authority both sides consult: the board validates an
incoming request at creation, and the executor re-validates what it polled before
running it — the network in between is not trusted. Pure stdlib, no hive imports:
the bridge runs on the host beside the stand, not inside it.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

SLOTS = (1, 2, 3)  # the stand's slots: LastOrder01..03 (docker/compose.yml)
RESULT_MAX = 800  # the command output the board keeps, truncated, never refused
LINES_MAX = 500  # `logs <service> [lines]`: the tail the executor reads
LINES_DEFAULT = 50

# `slot_server N <host:port>`: a host label (letters, digits, dots, dashes) with an
# optional plain port. No spaces, no scheme, no userinfo — anything else is a refusal.
ADDRESS_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,251}[A-Za-z0-9])?(?::([0-9]{1,5}))?$")
# a compose service name: mc-server, body-1, mind-2, node-3, hive, llm-gateway…
SERVICE_RE = re.compile(r"^[a-z][a-z0-9-]{0,29}$")

# action -> (required arg keys, optional arg keys); values are strings on the board
INFRA_ACTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # the slot is the sister: node-N + mind-N (ADR-050); the body is the mind's own
    "slot_up": (("slot",), ()),
    "slot_down": (("slot",), ()),
    "slot_restart": (("slot",), ()),   # the mind restarts, the sister lives on
    "slot_server": (("slot", "server"), ()),
    "mc_restart": ((), ()),
    "status": ((), ()),
    "logs": (("service",), ("lines",)),
    # the body verbs (ADR-050): asked by the slot's mind for its own body, or by the
    # operator / Last Order; `kind` picks the body of a game from BODY_KINDS
    "body_up": (("slot",), ("kind",)),
    "body_down": (("slot",), ()),
    "body_restart": (("slot",), ()),
}

# The bodies a mind may wear (ADR-050): kind -> the compose service of slot N. One game
# today; another game is one more line here, its service in compose and its adapter
# in the mind (runtime/mind/bodyctl.py BODY_ADAPTERS) — slots do not change.
BODY_KINDS: dict[str, str] = {"minecraft": "body-{slot}"}
DEFAULT_BODY_KIND = "minecraft"

# Who may ask for what. Last Order and the operator rule the whole set; a slot's
# environment (`runner:<slot>` — the mind and the runner hold the slot's runner key)
# may only look after its own slot: its body, and its mind's restart.
RUNNER_ACTIONS = frozenset({"body_up", "body_down", "body_restart", "slot_restart"})
# body_up + body_restart + slot_restart asked by one slot within the window: a body
# that dies at boot must not loop the executor (beyond — a `stuck` alarm instead)
RUNNER_RESTARTS_MAX = 3
RUNNER_RESTARTS_WINDOW_S = 600


def body_service(slot: int | str, kind: str = DEFAULT_BODY_KIND) -> str:
    """The compose service of slot N's body of `kind`."""
    if kind not in BODY_KINDS:
        raise InfraError(f"kind must be one of {sorted(BODY_KINDS)}, got {kind!r}")
    return BODY_KINDS[kind].format(slot=int(slot))


class InfraError(ValueError):
    """A refused infra action or arguments; the message is safe for the board."""


def is_infra(action: Any) -> bool:
    """True when `action` names one of the closed infra verbs."""
    return isinstance(action, str) and action in INFRA_ACTIONS


def _slot(value: Any) -> int:
    try:
        slot = int(value)
    except (TypeError, ValueError):
        raise InfraError(f"slot must be a slot number, got {value!r}") from None
    if slot not in SLOTS:
        raise InfraError(f"slot must be one of {list(SLOTS)}, got {slot}")
    return slot


def check(action: Any, args: Any) -> dict[str, str]:
    """Validate one infra request; return the cleaned args or raise InfraError.

    Strict by design: exactly the keys the action takes, string values, shapes
    per key. The caller (board at creation, executor before running) decides the
    error mapping; the message always names what is wrong.
    """
    if not is_infra(action):
        raise InfraError(f"action must be one of {sorted(INFRA_ACTIONS)}, got {action!r}")
    if not isinstance(args, Mapping):
        raise InfraError("args must be a flat dict of strings")
    required, optional = INFRA_ACTIONS[action]
    cleaned: dict[str, str] = {}
    for key, value in args.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InfraError("args maps strings to strings")
        if key not in required and key not in optional:
            raise InfraError(f"{action} takes {list(required)} alone, got extra {key!r}")
        cleaned[key] = value
    for key in required:
        if key not in cleaned:
            raise InfraError(f"{action} needs the {key!r} argument")
    if "slot" in cleaned:
        cleaned["slot"] = str(_slot(cleaned["slot"]))
    if "server" in cleaned:
        address = cleaned["server"].strip()
        match = ADDRESS_RE.match(address)
        if match is None:
            raise InfraError(
                "server must be host[:port] — letters, digits, dots, dashes, no spaces"
            )
        if match.group(1) is not None and int(match.group(1)) > 65535:
            raise InfraError("the port is over 65535")
        cleaned["server"] = address
    if "service" in cleaned:
        if SERVICE_RE.match(cleaned["service"]) is None:
            raise InfraError("service must be a compose service name (mc-server, body-1, …)")
    if "kind" in cleaned:
        if cleaned["kind"] not in BODY_KINDS:
            raise InfraError(f"kind must be one of {sorted(BODY_KINDS)}, got {cleaned['kind']!r}")
    if "lines" in cleaned:
        try:
            lines = int(cleaned["lines"])
        except (TypeError, ValueError):
            raise InfraError("lines must be a number of log lines") from None
        if not 1 <= lines <= LINES_MAX:
            raise InfraError(f"lines must be 1..{LINES_MAX}, got {lines}")
        cleaned["lines"] = str(lines)
    return cleaned


__all__ = [
    "ADDRESS_RE",
    "BODY_KINDS",
    "DEFAULT_BODY_KIND",
    "INFRA_ACTIONS",
    "RUNNER_ACTIONS",
    "RUNNER_RESTARTS_MAX",
    "RUNNER_RESTARTS_WINDOW_S",
    "LINES_DEFAULT",
    "LINES_MAX",
    "RESULT_MAX",
    "SERVICE_RE",
    "SLOTS",
    "InfraError",
    "body_service",
    "check",
    "is_infra",
]
