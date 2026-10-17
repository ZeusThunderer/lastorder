"""Schemas of the network-goal council (docs/architecture/GOALS_COMMS.md, section 1).

Dataclasses, limit constants and strict ``from_dict`` parsers: a JSON payload either builds a
valid object or raises :class:`GoalSchemaError`, so a broken smart reply or report can never
reach goals.json. No schedule, no rules, no LLM here - the council itself is T-016
(runtime/hive/council.py). The marked examples of the document are canonical;
tests/test_goals_comms_schema.py keeps them and this module from drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from runtime.ports import NetworkGoal


class GoalSchemaError(ValueError):
    """A payload does not match GOALS_COMMS.md; the caller falls back to rules or old goals."""


# --- limits and schedule (GOALS_COMMS.md 1.2-1.3) -------------------------------------

COUNCIL_EVERY_MIN = 15  # one rules pass this often; smart at most COUNCIL_SMART_PER_HOUR
COUNCIL_SMART_PER_HOUR = 4  # hard cap per network (NETWORK.md section 6)
MAX_NETWORK_GOALS = 3  # 1-3 goals serve every node (ADR-021)
GOAL_TTL_MIN = 60  # a goal dies after this unless its signal renews it
GOAL_TEXT_MAX_CHARS = 120
GOAL_WHY_MAX_CHARS = 160
DEATH_WINDOW_MIN = 120  # death causes are counted over this window
DEATH_TOP_MIN_COUNT = 2  # fewer deaths in the window is not a signal

LADDER = ("survive", "food", "safety", "iron", "shared")
SIGNAL_KINDS = ("ladder", "kb_gap", "deaths")
GOAL_SOURCES = ("rule", "smart")
KB_GAP_KINDS = ("danger", "recipe")
KB_GAP_STATUSES = ("", "hypothesis", "disputed")  # "" - no record at all (GOALS_COMMS.md 1.1)
GAME_CAUSE_PREFIX = "game:"  # only world deaths make a danger signal; ttl does not (1.1)
TOOL_TIERS = ("none", "wood", "stone", "iron")  # the MVP ladder stops at iron (GOALS_COMMS.md 1.1)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GoalSchemaError(message)


def _text(data: Mapping[str, Any], key: str, max_chars: int, *, min_chars: int = 1) -> str:
    value = data.get(key)
    _require(
        isinstance(value, str) and min_chars <= len(value.strip()) <= max_chars,
        f"{key} must be a string of {min_chars}-{max_chars} characters",
    )
    return value.strip()


def _int(data: Mapping[str, Any], key: str, minimum: int) -> int:
    value = data.get(key)
    _require(
        isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
        f"{key} must be an integer >= {minimum}",
    )
    return value


def _enum(data: Mapping[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    value = data.get(key)
    _require(value in allowed, f"{key} must be one of {allowed}")
    return value  # type: ignore[return-value]


def _goals_from_list(
    raw: Any, *, why_min_chars: int, max_items: int, what: str
) -> tuple[NetworkGoal, ...]:
    _require(isinstance(raw, list) and 1 <= len(raw) <= max_items, f"{what} must list 1-{max_items} goals")
    goals = []
    for item in raw:
        _require(isinstance(item, dict), f"each goal in {what} must be an object")
        goals.append(
            NetworkGoal(
                text=_text(item, "text", GOAL_TEXT_MAX_CHARS),
                why=_text(item, "why", GOAL_WHY_MAX_CHARS, min_chars=why_min_chars),
            )
        )
    return tuple(goals)


# --- rule-computed signals (GOALS_COMMS.md 1.1) ----------------------------------------


@dataclass(frozen=True)
class GoalSignal:
    """One signal the rules read from the base, the death registry and the live summary.

    No LLM is involved in producing or consuming it. ``detail`` keys the deterministic
    signal-to-goal table, e.g. ``deaths:game:<term>``, ``ladder:gap:food``, ``kb_gap:recipe``.
    """

    kind: str  # SIGNAL_KINDS
    detail: str
    weight: float
    refs: tuple[str, ...] = ()  # entry ids, death serials - provenance for the goal's why

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GoalSignal":
        kind = _enum(data, "kind", SIGNAL_KINDS)
        detail = _text(data, "detail", 96)
        weight = data.get("weight")
        _require(
            isinstance(weight, (int, float)) and not isinstance(weight, bool) and weight >= 0,
            "weight must be a non-negative number",
        )
        refs = data.get("refs", ())
        _require(
            isinstance(refs, (list, tuple)) and all(isinstance(r, str) for r in refs),
            "refs must be a list of strings",
        )
        return cls(kind=kind, detail=detail, weight=float(weight), refs=tuple(refs))


# --- stored goals and the smart call (GOALS_COMMS.md 1.2-1.4) ---------------------------


@dataclass(frozen=True)
class NetworkGoalRecord:
    """A goal as goals.json stores it: the prompt-facing text plus its envelope."""

    id: str
    text: str
    why: str
    source: str  # GOAL_SOURCES
    created_at: str  # ISO-8601
    expires_at: str  # ISO-8601: created_at + GOAL_TTL_MIN, renewed by a repeated signal
    signal_refs: tuple[str, ...] = ()  # signal details that made or renewed the goal

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NetworkGoalRecord":
        source = _enum(data, "source", GOAL_SOURCES)
        refs = data.get("signal_refs", ())
        _require(
            isinstance(refs, (list, tuple)) and all(isinstance(r, str) for r in refs),
            "signal_refs must be a list of strings",
        )
        return cls(
            id=_text(data, "id", 16),
            text=_text(data, "text", GOAL_TEXT_MAX_CHARS),
            why=_text(data, "why", GOAL_WHY_MAX_CHARS),
            source=source,
            created_at=_text(data, "created_at", 32),
            expires_at=_text(data, "expires_at", 32),
            signal_refs=tuple(refs),
        )


def goal_set_from_list(raw: Any) -> tuple[NetworkGoalRecord, ...]:
    """Parse the goals.json list itself: at most MAX_NETWORK_GOALS active records.

    The merge of a pass with this set is GOALS_COMMS.md 1.2 (rules 1-4); the parser only keeps
    the stored file honest, so a corrupted goals.json fails loudly instead of drifting.
    """

    _require(
        isinstance(raw, list) and len(raw) <= MAX_NETWORK_GOALS,
        f"the active goal set must be a list of at most {MAX_NETWORK_GOALS} records",
    )
    records = []
    for item in raw:
        _require(isinstance(item, Mapping), "each goal record must be an object")
        records.append(NetworkGoalRecord.from_dict(item))
    return tuple(records)


@dataclass(frozen=True)
class DeathCount:
    """One cause counted over the death window; ``ttl`` never reaches this schema (1.1)."""

    cause: str  # game:<term> | stuck
    count: int

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeathCount":
        return cls(cause=_text(data, "cause", 64), count=_int(data, "count", 1))


@dataclass(frozen=True)
class KbGap:
    """One knowledge-base gap the council may turn into a goal (GOALS_COMMS.md 1.1)."""

    kind: str  # KB_GAP_KINDS
    about: str  # handle or term
    status: str  # KB_GAP_STATUSES: "" - no record, otherwise the best existing one

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KbGap":
        status = data.get("status", "")
        _require(status in KB_GAP_STATUSES, f"status must be one of {KB_GAP_STATUSES}")
        return cls(
            kind=_enum(data, "kind", KB_GAP_KINDS),
            about=_text(data, "about", 96),
            status=status,
        )


@dataclass(frozen=True)
class CouncilInput:
    """What the rules hand the smart call (GOALS_COMMS.md 1.3)."""

    current_step: str  # LADDER
    gap_step: str  # LADDER
    deaths: tuple[DeathCount, ...] = ()
    gaps: tuple[KbGap, ...] = ()
    current_goals: tuple[NetworkGoal, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CouncilInput":
        current = _enum(data, "current_step", LADDER)
        gap = _enum(data, "gap_step", LADDER)
        deaths_raw = data.get("deaths", [])
        gaps_raw = data.get("gaps", [])
        _require(isinstance(deaths_raw, list), "deaths must be a list")
        _require(isinstance(gaps_raw, list), "gaps must be a list")
        current_raw = data.get("current_goals", [])
        _require(isinstance(current_raw, list), "current_goals must be a list")
        current_goals = (
            _goals_from_list(
                current_raw, why_min_chars=1, max_items=MAX_NETWORK_GOALS, what="current_goals"
            )
            if current_raw
            else ()
        )
        return cls(
            current_step=current,
            gap_step=gap,
            deaths=tuple(DeathCount.from_dict(item) for item in deaths_raw),
            gaps=tuple(KbGap.from_dict(item) for item in gaps_raw),
            current_goals=current_goals,
        )


@dataclass(frozen=True)
class CouncilOutput:
    """The smart reply of the council: one to three goals, nothing else (GOALS_COMMS.md 1.3)."""

    goals: tuple[NetworkGoal, ...]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CouncilOutput":
        return cls(
            goals=_goals_from_list(
                data.get("goals"), why_min_chars=1, max_items=MAX_NETWORK_GOALS, what="goals"
            )
        )


# --- the live summary a mind reports with its next hive call (GOALS_COMMS.md 1.1) --------


@dataclass(frozen=True)
class LivingStatus:
    """What a mind reports about its body: food, best tool, weapon, armor, position.

    The ladder reads the inventory fields, the chatter director reads ``pos`` for the meeting
    event (GOALS_COMMS.md 5). It rides an existing hive call instead of a new endpoint
    (proposal P3 of the T-008 report).
    """

    serial: int
    slot: str
    food_count: int
    tool_tier: str  # TOOL_TIERS
    weapon: bool
    armor: bool
    pos: tuple[int, int, int]
    updated_at: str  # ISO-8601

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LivingStatus":
        for key in ("weapon", "armor"):
            _require(isinstance(data.get(key), bool), f"{key} must be a boolean")
        pos = data.get("pos")
        _require(
            isinstance(pos, (list, tuple))
            and len(pos) == 3
            and all(isinstance(v, int) and not isinstance(v, bool) for v in pos),
            "pos must be a list of 3 integers",
        )
        return cls(
            serial=_int(data, "serial", 1),
            slot=_text(data, "slot", 32),
            food_count=_int(data, "food_count", 0),
            tool_tier=_enum(data, "tool_tier", TOOL_TIERS),
            weapon=data["weapon"],
            armor=data["armor"],
            pos=(pos[0], pos[1], pos[2]),
            updated_at=_text(data, "updated_at", 32),
        )
