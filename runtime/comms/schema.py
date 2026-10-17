"""Message and chatter schemas (docs/architecture/GOALS_COMMS.md, sections 3-6).

Dataclasses, limit constants and strict ``from_dict`` parsers; a payload either builds a
valid object or raises :class:`CommsSchemaError`, and the caller drops that one message or
line, never the whole reply. No queues, no rate accounting, no director here - that is T-017
(runtime/hive/director.py, runtime/mind/comms.py). Every message stays an observation, never
a command (ADR-012, ADR-022, ADR-025).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class CommsSchemaError(ValueError):
    """A payload does not match GOALS_COMMS.md; the caller drops that one field or line."""


# --- channels, message types, stances (GOALS_COMMS.md 3-5) ------------------------------

CHANNELS = ("net", "chat")
MSG_TYPES = ("request", "warning", "claim", "refuse")
ALL_ADDRESSEE = "all"  # net only: every living node except the sender (GOALS_COMMS.md 3)
STANCES = ("agree", "disagree", "aside")
THREAD_EVENTS = ("death", "knowledge", "meeting", "silence")

# --- limits (GOALS_COMMS.md 3, 5, 6) -----------------------------------------------------

SAY_MIN_INTERVAL_S = 20  # per node, shared by step say, chatter and chat-msg (T-014 env)
MSG_MAX_CHARS = 160
NET_MSG_PER_NODE_PER_MIN = 4
MSG_TTL_S = 600  # undelivered inbox messages are dropped after this
CHATTER_CALLS_PER_HOUR = 30  # per network (NETWORK.md section 6, user answer 2026-10-16)
CHATTER_CALLS_PER_NODE_PER_HOUR = 15
CHATTER_THREAD_MAX_LINES = 6  # K: a thread closes after this many lines
CHATTER_THREAD_IDLE_CLOSE_S = 180
CHATTER_SILENCE_OPEN_MIN = 10  # this many quiet minutes let the director open a thread
CHATTER_LINE_MAX_CHARS = 200
CHATTER_QUOTE_MAX_CHARS = 280  # a knowledge-thread quote is the exact record claim; claims are
# capped at 280 characters by KNOWLEDGE.md section 2, so a legal record must always fit
MEETING_RADIUS_M = 32
MEETING_COOLDOWN_MIN = 30  # the same pair of nodes opens a meeting thread at most this often


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CommsSchemaError(message)


def _text(data: Mapping[str, Any], key: str, max_chars: int) -> str:
    value = data.get(key)
    _require(
        isinstance(value, str) and 1 <= len(value.strip()) <= max_chars,
        f"{key} must be a string of 1-{max_chars} characters",
    )
    return value.strip()


def _optional_text(data: Mapping[str, Any], key: str, max_chars: int) -> str:
    value = data.get(key, "")
    _require(
        isinstance(value, str) and len(value.strip()) <= max_chars,
        f"{key} must be a string of at most {max_chars} characters",
    )
    return value.strip()


# --- piggyback messages (GOALS_COMMS.md 3-4) ---------------------------------------------


@dataclass(frozen=True)
class OutboundMessage:
    """The ``msg`` field of a step reply, after parsing: coordination, never a command.

    The model writes ``to``, ``channel``, ``type`` and ``text``; the mind stamps ``from_serial``
    (0 until stamped) and ``ts`` before sending (GOALS_COMMS.md 3).
    """

    to: int | str  # node serial or ALL_ADDRESSEE; ALL only on the net channel
    channel: str  # CHANNELS
    msg_type: str  # MSG_TYPES; the JSON key is "type"
    text: str
    from_serial: int = 0
    ts: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutboundMessage":
        channel = data.get("channel")
        _require(channel in CHANNELS, f"channel must be one of {CHANNELS}")
        msg_type = data.get("type")
        _require(msg_type in MSG_TYPES, f"type must be one of {MSG_TYPES}")
        to = data.get("to")
        _require(
            to == ALL_ADDRESSEE or (isinstance(to, int) and not isinstance(to, bool) and to >= 1),
            f'to must be a node serial or "{ALL_ADDRESSEE}"',
        )
        _require(
            channel != "chat" or to == ALL_ADDRESSEE,
            'the chat channel is public: to must be "all"',
        )
        from_serial = data.get("from_serial", 0)
        _require(
            isinstance(from_serial, int)
            and not isinstance(from_serial, bool)
            and from_serial >= 0,
            "from_serial must be an integer >= 0",
        )
        ts = data.get("ts", "")
        _require(isinstance(ts, str) and len(ts) <= 32, "ts must be a string of at most 32 characters")
        return cls(
            to=to,
            channel=channel,
            msg_type=msg_type,
            text=_text(data, "text", MSG_MAX_CHARS),
            from_serial=from_serial,
            ts=ts,
        )


# --- chatter (GOALS_COMMS.md 5) ------------------------------------------------------------


@dataclass(frozen=True)
class ChatterReply:
    """What one chatter call returns: a short line and a checkable stance."""

    text: str
    stance: str  # STANCES

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChatterReply":
        stance = data.get("stance")
        _require(stance in STANCES, f"stance must be one of {STANCES}")
        return cls(text=_text(data, "text", CHATTER_LINE_MAX_CHARS), stance=stance)


@dataclass(frozen=True)
class ChatterLine:
    """One accepted reply inside a thread; a dropped reply leaves no line behind."""

    thread_id: str
    serial: int
    text: str
    stance: str  # STANCES
    ts: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChatterLine":
        stance = data.get("stance")
        _require(stance in STANCES, f"stance must be one of {STANCES}")
        serial = data.get("serial")
        _require(
            isinstance(serial, int) and not isinstance(serial, bool) and serial >= 1,
            "serial must be an integer >= 1",
        )
        ts = data.get("ts", "")
        _require(isinstance(ts, str) and len(ts) <= 32, "ts must be a string of at most 32 characters")
        return cls(
            thread_id=_text(data, "thread_id", 16),
            serial=serial,
            text=_text(data, "text", CHATTER_LINE_MAX_CHARS),
            stance=stance,
            ts=ts,
        )


@dataclass(frozen=True)
class ChatterThread:
    """Director-side thread state; the mind sees its echo in the turn history (T-017).

    A knowledge event must carry the exact record claim in ``quote`` (and its id), which is
    anti-mirror rule 4 of GOALS_COMMS.md 5 - the schema itself enforces it.
    """

    thread_id: str
    channel: str  # CHANNELS
    topic: str
    quote: str
    quote_entry_id: str = ""
    event: str = ""  # THREAD_EVENTS
    opened_at: str = ""
    lines: tuple[ChatterLine, ...] = ()
    closed_at: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ChatterThread":
        channel = data.get("channel")
        _require(channel in CHANNELS, f"channel must be one of {CHANNELS}")
        event = data.get("event", "")
        _require(event in THREAD_EVENTS, f"event must be one of {THREAD_EVENTS}")
        quote = _optional_text(data, "quote", CHATTER_QUOTE_MAX_CHARS)
        quote_entry_id = _optional_text(data, "quote_entry_id", 16)
        _require(
            event != "knowledge" or (quote and quote_entry_id),
            "a knowledge thread must quote the record claim and its id",
        )
        lines_raw = data.get("lines", [])
        _require(isinstance(lines_raw, list), "lines must be a list")
        _require(
            len(lines_raw) <= CHATTER_THREAD_MAX_LINES,
            f"a thread holds at most {CHATTER_THREAD_MAX_LINES} lines",
        )
        lines = tuple(ChatterLine.from_dict(item) for item in lines_raw)
        thread_id = _text(data, "thread_id", 16)
        _require(
            all(line.thread_id == thread_id for line in lines),
            "every line must belong to this thread",
        )
        return cls(
            thread_id=thread_id,
            channel=channel,
            topic=_text(data, "topic", 120),
            quote=quote,
            quote_entry_id=quote_entry_id,
            event=event,
            opened_at=_optional_text(data, "opened_at", 32),
            lines=lines,
            closed_at=_optional_text(data, "closed_at", 32),
        )
