"""Knowledge and will schemas: KNOWLEDGE.md v2 as code (T-007).

Dataclasses with from_dict/to_dict validators — no I/O, no LLM calls. The store (T-012)
persists these, hive serves them, minds parse LLM replies into ReflectionOutput and build
TriggerEvent from game events. Every from_dict rejects unknown fields, wrong types and
out-of-range values, so a malformed record dies at the door instead of poisoning the base.

Examples in KNOWLEDGE.md sit in fenced blocks tagged `json <Type>` / `markdown <Type>`
(optionally `invalid`), and tests/test_knowledge_schema.py parses them straight from the
document: document, schema and tests cannot drift apart silently.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import yaml

from runtime.knowledge.budget import (
    EMERGENCY_ACTION_CHARS,
    EMERGENCY_EVENT_CHARS,
    EMERGENCY_MAX_EVENTS,
    EMERGENCY_NAME_CHARS,
    WILL_BODY_MAX_TOKENS,
    estimate_tokens,
)
from runtime.lastorder.traits import TRAIT_NAMES, Traits
from runtime.ports import KnowledgeCandidate, KnowledgeEntry

RECORD_ID = re.compile(r"\Ak-\d{4,}\Z")
ABOUT_KEY = re.compile(r"\A(blk|ent|itm):[a-z0-9_]+\Z")
FRONTMATTER = re.compile(r"\A---\n(.*)\n---\n(.*)\Z", re.S)

KINDS = ("danger", "fact", "recipe", "skill", "term")
PRIMER_KINDS = ("danger", "fact", "recipe", "skill")
SOURCES = ("primer", "node")
STATUSES = ("hypothesis", "confirmed", "disputed", "retired")
CAUSES = ("game", "ttl", "stuck")
REFLECTION_KINDS = ("birth", "regular", "death")

CLAIM_MAX_CHARS = 280
MAX_ABOUT_KEYS = 4
MAX_CANDIDATES = 3
WILL_MIN_LINES = 2
WILL_MAX_LINES = 4

TRIGGER_IMPORTANCE: dict[str, int] = {
    "first.seen": 3,
    "damage.taken": 4,
    "hp.drop": 6,
    "first.item": 3,
    "action.fail2": 5,
    "action.recovered": 4,
    "note.suggested": 2,
    "death.own": 10,
}
REFLECT_IMPORTANCE_THRESHOLD = 12

CAUSE_TEXT = {"game": "меня убил мир", "ttl": "вышел мой срок", "stuck": "я застрял"}
CAUSE_LESSON = {
    "game": "не повторяй мой последний шаг вслепую",
    "ttl": "главное делай раньше, срок короток",
    "stuck": "если путь не идёт, сворачивай сразу",
}

CLAIM_DUP_JACCARD = 0.6  # duplicate threshold of section 4; T-012 calibrates on live data
CLAIM_TOKEN_PREFIX = 5  # token truncation that merges Russian case endings


class SchemaError(ValueError):
    """Data does not match KNOWLEDGE.md v2."""


def _plain(value: Any) -> Any:
    """asdict keeps tuples; JSON files carry lists, so roundtrips must go through this."""
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


# --- small validators ---------------------------------------------------------------


def _want(data: Any, allowed: tuple[str, ...]) -> None:
    if not isinstance(data, Mapping):
        raise SchemaError(f"expected an object, got {type(data).__name__}")
    unknown = sorted(set(data) - set(allowed), key=str)  # YAML keys may be ints
    if unknown:
        raise SchemaError(f"unknown fields: {unknown}")


def _req(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise SchemaError(f"missing field: {key}")
    return data[key]


def _text(value: Any, key: str, *, max_chars: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{key}: expected a non-empty string")
    text = value.strip()
    if max_chars is not None and len(text) > max_chars:
        raise SchemaError(f"{key}: longer than {max_chars} chars")
    return text


def _int_field(value: Any, key: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{key}: expected an int, got {type(value).__name__}")
    if value < minimum or (maximum is not None and value > maximum):
        raise SchemaError(f"{key}: out of range: {value}")
    return value


def _iso(value: Any, key: str) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str):
        raise SchemaError(f"{key}: expected an ISO-8601 string")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise SchemaError(f"{key}: not ISO-8601: {value!r}") from None
    return value


def _list_field(value: Any, key: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaError(f"{key}: expected a list")
    return value


def _about_keys(value: Any) -> tuple[str, ...]:
    keys = tuple(_list_field(value, "about"))
    if not all(isinstance(k, str) for k in keys):
        raise SchemaError("about: expected a list of strings")
    if len(set(keys)) != len(keys):
        raise SchemaError("about: duplicate keys")
    if len(keys) > MAX_ABOUT_KEYS:
        raise SchemaError(f"about: more than {MAX_ABOUT_KEYS} keys")
    for key in keys:
        if not ABOUT_KEY.match(key):
            raise SchemaError(f"about: bad key {key!r}")
    return keys


def _traits(value: Any) -> Traits:
    if not isinstance(value, Mapping):
        raise SchemaError("traits: expected a mapping")
    expected = {*TRAIT_NAMES, "quirk", "seed"}
    if set(value) != expected:
        raise SchemaError(f"traits: fields must be exactly {sorted(expected)}")
    numbers = {
        name: _int_field(value[name], f"traits.{name}", minimum=0, maximum=10)
        for name in TRAIT_NAMES
    }
    return Traits(
        **numbers,
        quirk=_text(value["quirk"], "traits.quirk"),
        seed=_int_field(value["seed"], "traits.seed", minimum=0),
    )


def _will_body(value: Any) -> str:
    body = _text(value, "will body")
    lines = body.split("\n")
    if any(not line.strip() for line in lines):
        raise SchemaError("will body: blank lines are not allowed")
    if not WILL_MIN_LINES <= len(lines) <= WILL_MAX_LINES:
        raise SchemaError(
            f"will body: expected {WILL_MIN_LINES}-{WILL_MAX_LINES} lines, got {len(lines)}"
        )
    body = "\n".join(line.rstrip() for line in lines)
    if estimate_tokens(body) > WILL_BODY_MAX_TOKENS:
        raise SchemaError(
            f"will body: over {WILL_BODY_MAX_TOKENS} tokens "
            f"(got {estimate_tokens(body)}); the inheritance budget caps every will"
        )
    return body


def claim_tokens(claim: str) -> frozenset[str]:
    """Normalized claim tokens for dedup (section 4): lowercase, `ё`→`е`, non-alphanumerics
    as spaces, each token truncated to CLAIM_TOKEN_PREFIX chars so case endings merge.
    Stop words stay: negation («не») is meaningful."""

    if not isinstance(claim, str):
        raise SchemaError("claim: expected a string")
    text = claim.lower().replace("ё", "е")
    raw = re.split(r"[^0-9a-zа-я]+", text)
    return frozenset(t[:CLAIM_TOKEN_PREFIX] for t in raw if t)


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity of two claims' token sets: |A∩B| / |A∪B| (section 4)."""

    first, second = claim_tokens(a), claim_tokens(b)
    union = first | second
    if not union:
        return 0.0
    return len(first & second) / len(union)


def claims_duplicate(a: str, b: str) -> bool:
    """True when two claims are the same observation reworded (J ≥ CLAIM_DUP_JACCARD).

    The shared-`about` half of the duplicate rule stays in hive (T-012): it needs the
    records' keys, not just the text.
    """

    return jaccard(a, b) >= CLAIM_DUP_JACCARD


def _candidate(value: Any) -> KnowledgeCandidate:
    if not isinstance(value, Mapping):
        raise SchemaError("candidates: expected objects")
    _want(value, ("kind", "claim", "about", "evidence_event_ids"))
    kind = _req(value, "kind")
    if kind not in KINDS:
        raise SchemaError(f"candidates.kind: {kind!r} not in {KINDS}")
    claim = _text(_req(value, "claim"), "candidates.claim", max_chars=CLAIM_MAX_CHARS)
    about = _about_keys(value.get("about", []))
    if kind == "term" and len(about) != 1:
        raise SchemaError("candidates.about: a term needs exactly one key")
    ids = _list_field(value.get("evidence_event_ids", []), "evidence_event_ids")
    if not all(isinstance(i, str) and i for i in ids):
        raise SchemaError("evidence_event_ids: expected a list of event ids")
    return KnowledgeCandidate(
        kind=kind, claim=claim, about=about, evidence_event_ids=tuple(ids)
    )


# --- the record (KNOWLEDGE.md section 2) ---------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """One node saw the same thing: the proof that outweighs any vote."""

    node: int
    ts: str
    event: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        _want(data, ("node", "event", "ts"))
        event = _req(data, "event")
        if event is not None:
            event = _text(event, "evidence.event")
        return cls(
            node=_int_field(_req(data, "node"), "evidence.node", minimum=1),
            ts=_iso(_req(data, "ts"), "evidence.ts"),
            event=event,
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class RecordVote:
    """One node's vote on a record; a reject with a reason is a refutation (section 4)."""

    node: int
    accept: bool
    ts: str
    why: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecordVote":
        _want(data, ("node", "accept", "why", "ts"))
        accept = _req(data, "accept")
        if not isinstance(accept, bool):
            raise SchemaError("accept: expected a bool")
        why = data.get("why", "")
        if not isinstance(why, str):
            raise SchemaError("why: expected a string")
        return cls(
            node=_int_field(_req(data, "node"), "votes.node", minimum=1),
            accept=accept,
            ts=_iso(_req(data, "ts"), "votes.ts"),
            why=why.strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class Decision:
    """Last Order's ruling on a record (T-028, NETWORK.md section 7, ADR-036).

    Settles the status outright (`status_of`): accept -> confirmed, reject -> retired.
    `by` names the deciding authority (the store writes "last_order"); `why` is mandatory —
    a decision without a recorded reason does not enter the base.
    """

    by: str
    accept: bool
    ts: str
    why: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Decision":
        _want(data, ("by", "accept", "why", "ts"))
        accept = _req(data, "accept")
        if not isinstance(accept, bool):
            raise SchemaError("decision.accept: expected a bool")
        return cls(
            by=_text(_req(data, "by"), "decision.by"),
            accept=accept,
            ts=_iso(_req(data, "ts"), "decision.ts"),
            why=_text(_req(data, "why"), "decision.why"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


RECORD_FIELDS = (
    "id",
    "kind",
    "claim",
    "about",
    "source",
    "author",
    "ts",
    "status",
    "evidence",
    "votes",
    "confidence",
    "decision",
)


@dataclass(frozen=True)
class Record:
    """One knowledge record of the shared base, one file base/records/k-NNNN.json."""

    id: str
    kind: str
    claim: str
    about: tuple[str, ...]
    source: str
    author: int | str
    ts: str
    status: str
    evidence: tuple[Evidence, ...] = ()
    votes: tuple[RecordVote, ...] = ()
    confidence: float | None = None
    decision: Decision | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Record":
        _want(data, RECORD_FIELDS)
        record_id = _req(data, "id")
        if not isinstance(record_id, str) or not RECORD_ID.match(record_id):
            raise SchemaError(f"id: bad record id {record_id!r}")
        kind = _req(data, "kind")
        if kind not in KINDS:
            raise SchemaError(f"kind: {kind!r} not in {KINDS}")
        claim = _text(_req(data, "claim"), "claim", max_chars=CLAIM_MAX_CHARS)
        about = _about_keys(_req(data, "about"))
        if kind == "term" and len(about) != 1:
            raise SchemaError("about: a term needs exactly one key")
        source = _req(data, "source")
        if source not in SOURCES:
            raise SchemaError(f"source: {source!r} not in {SOURCES}")
        author = _req(data, "author")
        if source == "primer":
            if author != "primer":
                raise SchemaError('author: source=primer requires author "primer"')
        else:
            author = _int_field(author, "author", minimum=1)
        status = _req(data, "status")
        if status not in STATUSES:
            raise SchemaError(f"status: {status!r} not in {STATUSES}")
        evidence = tuple(
            Evidence.from_dict(e) for e in _list_field(_req(data, "evidence"), "evidence")
        )
        votes = tuple(
            RecordVote.from_dict(v) for v in _list_field(_req(data, "votes"), "votes")
        )
        if source == "primer" and (status != "confirmed" or evidence or votes):
            raise SchemaError("primer records are confirmed and carry no evidence or votes")
        decision = None
        if "decision" in data and data["decision"] is not None:
            decision = Decision.from_dict(data["decision"])
        if source == "primer" and decision is not None:
            raise SchemaError("primer records carry no decision")
        confidence = None
        if "confidence" in data and data["confidence"] is not None:
            value = data["confidence"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SchemaError(f"confidence: expected 0..1, got {value!r}")
            if not 0.0 <= float(value) <= 1.0:
                raise SchemaError(f"confidence: expected 0..1, got {value!r}")
            confidence = float(value)
        return cls(
            id=record_id,
            kind=kind,
            claim=claim,
            about=about,
            source=source,
            author=author,
            ts=_iso(_req(data, "ts"), "ts"),
            status=status,
            evidence=evidence,
            votes=votes,
            confidence=confidence,
            decision=decision,
        )

    def to_dict(self) -> dict[str, Any]:
        data = _plain(asdict(self))
        if self.confidence is None:
            data.pop("confidence")
        if self.decision is None:
            data.pop("decision")
        return data

    def to_entry(self) -> KnowledgeEntry:
        """The same record as a node sees it in a slice (ports.KnowledgeEntry)."""
        return KnowledgeEntry(
            id=self.id,
            kind=self.kind,
            claim=self.claim,
            status=self.status,
            about=self.about,
        )


@dataclass(frozen=True)
class PrimerEntry:
    """One primer line in agent/last_order/primer/<topic>.json (section 8); not a record yet."""

    kind: str
    claim: str
    about: tuple[str, ...]
    extended: bool = False

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PrimerEntry":
        _want(data, ("kind", "claim", "about", "extended"))
        kind = _req(data, "kind")
        if kind not in PRIMER_KINDS:
            raise SchemaError(f"kind: {kind!r} not in {PRIMER_KINDS}")
        extended = data.get("extended", False)
        if not isinstance(extended, bool):
            raise SchemaError("extended: expected a bool")
        return cls(
            kind=kind,
            claim=_text(_req(data, "claim"), "claim", max_chars=CLAIM_MAX_CHARS),
            about=_about_keys(_req(data, "about")),
            extended=extended,
        )

    def to_record(self, record_id: str, ts: str) -> Record:
        """The record hive loads from this entry: confirmed, author primer, no votes."""
        return Record(
            id=record_id,
            kind=self.kind,
            claim=self.claim,
            about=self.about,
            source="primer",
            author="primer",
            ts=ts,
            status="confirmed",
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


# --- trigger events (section 3) ------------------------------------------------------


@dataclass(frozen=True)
class TriggerEvent:
    """A moment worth remembering; importance comes from the table and is checked."""

    type: str
    importance: int
    event_id: str
    ts: str
    payload: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TriggerEvent":
        _want(data, ("type", "importance", "event_id", "ts", "payload"))
        type_ = _req(data, "type")
        if type_ not in TRIGGER_IMPORTANCE:
            raise SchemaError(f"type: {type_!r} not a trigger")
        importance = _int_field(
            _req(data, "importance"), "importance", minimum=1, maximum=10
        )
        if importance != TRIGGER_IMPORTANCE[type_]:
            raise SchemaError(
                f"importance: {importance} for {type_!r}, table says "
                f"{TRIGGER_IMPORTANCE[type_]}"
            )
        payload = data.get("payload", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise SchemaError("payload: expected an object")
        return cls(
            type=type_,
            importance=importance,
            event_id=_text(_req(data, "event_id"), "event_id"),
            ts=_iso(_req(data, "ts"), "ts"),
            payload=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


# --- the will (section 5) ------------------------------------------------------------


@dataclass(frozen=True)
class WillMeta:
    """Frontmatter of will.md: cause of death, dates, traits — service fields only."""

    serial: int
    slot: str
    cause: str
    born_at: str
    died_at: str
    lifetime_s: int
    emergency: bool
    traits: Traits
    model: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WillMeta":
        _want(
            data,
            (
                "serial",
                "slot",
                "cause",
                "born_at",
                "died_at",
                "lifetime_s",
                "emergency",
                "traits",
                "model",
            ),
        )
        cause = _req(data, "cause")
        if cause not in CAUSES:
            raise SchemaError(f"cause: {cause!r} not in {CAUSES}")
        emergency = _req(data, "emergency")
        if not isinstance(emergency, bool):
            raise SchemaError("emergency: expected a bool")
        return cls(
            serial=_int_field(_req(data, "serial"), "serial", minimum=1),
            slot=_text(_req(data, "slot"), "slot"),
            cause=cause,
            born_at=_iso(_req(data, "born_at"), "born_at"),
            died_at=_iso(_req(data, "died_at"), "died_at"),
            lifetime_s=_int_field(_req(data, "lifetime_s"), "lifetime_s", minimum=0),
            emergency=emergency,
            traits=_traits(_req(data, "traits")),
            model=_text(_req(data, "model"), "model"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


WILL_FIELDS = (
    "serial",
    "slot",
    "cause",
    "born_at",
    "died_at",
    "lifetime_s",
    "emergency",
    "traits",
    "model",
    "body",
)


@dataclass(frozen=True)
class Will:
    """will.md: WillMeta frontmatter plus a 2-4 line body (one lesson, one joke)."""

    meta: WillMeta
    body: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Will":
        _want(data, WILL_FIELDS)
        # data.get, not data[key]: a missing field must reach WillMeta's _req and raise
        # SchemaError, not KeyError — the death path (T-013) catches SchemaError only.
        meta = WillMeta.from_dict(
            {key: data.get(key) for key in WILL_FIELDS if key != "body"}
        )
        return cls(meta=meta, body=_will_body(_req(data, "body")))

    @classmethod
    def from_markdown(cls, text: str) -> "Will":
        if not isinstance(text, str):
            raise SchemaError("will: expected a string")
        match = FRONTMATTER.match(text.strip())
        if not match:
            raise SchemaError("will: expected --- frontmatter --- then the body")
        try:
            loaded = yaml.safe_load(match.group(1))
        except yaml.YAMLError as exc:  # a broken will.md must not crash the loader (T-012)
            raise SchemaError(f"will: broken YAML frontmatter: {exc}") from None
        meta = WillMeta.from_dict(loaded if isinstance(loaded, Mapping) else {})
        return cls(meta=meta, body=_will_body(match.group(2)))

    def to_dict(self) -> dict[str, Any]:
        return {**self.meta.to_dict(), "body": self.body}

    def to_markdown(self) -> str:
        head = yaml.safe_dump(
            self.meta.to_dict(), allow_unicode=True, sort_keys=True, default_flow_style=False
        )
        return f"---\n{head}---\n\n{self.body}\n"


def _one_line(value: Any, limit: int, key: str) -> str:
    """Collapse any field to a single capped line: newlines in caller data must not
    break the 2-4 line body contract of a will."""
    if not isinstance(value, str):
        raise SchemaError(f"{key}: expected a string")
    return " ".join(value.split())[:limit].strip()


def _as_utc(value: str, key: str) -> datetime:
    """Parse an already validated ISO-8601 timestamp; a naive one is hive/mind clock
    skew territory, so assume UTC instead of crashing on aware − naive subtraction."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def emergency_will(
    *,
    serial: int,
    slot: str,
    cause: str,
    born_at: str,
    died_at: str,
    traits: Traits,
    last_action: str,
    last_events: Sequence[str],
    node_name: str,
    heir_address: str,
) -> Will:
    """The fallback will, assembled from fields the mind always has — no LLM involved.

    CLAUDE.md: there is no death without a will. When the smart call fails, this template
    speaks instead: cause, last action, last events, a fixed lesson per cause. Trait numbers
    stay in frontmatter; the body never names them. lifetime_s is computed from the dates.

    Hardened on purpose (review R1-3) — this is the last line before "a death without a
    will", so it does not throw on realistic corruption:
    - naive timestamps are read as UTC, aware ones as-is;
    - a negative lifetime (clock skew between containers) clamps to 0;
    - an unknown cause degrades to `game`;
    - `last_action` and `last_events` are post-perception text (what the node saw in its
      prompt), NOT raw log.jsonl or uc service lines — the caller (T-013) passes them
      through perception first; each is collapsed to one capped line, at most three
      events survive, and the assembled body is checked by `_will_body`.

    `heir_address` carries no serial: the heir's number is assigned at birth, after
    retire, so the dying mind cannot know it — the caller composes a numberless address
    from the naming preset («следующий узел» / «следующая Мисака», ADR-018). Residual
    SchemaError (timestamps not ISO at all, non-int serial) means broken caller fields;
    the death path (T-013) catches it and still writes a minimal file.
    """
    if not isinstance(last_events, Sequence) or isinstance(last_events, (str, bytes)):
        raise SchemaError("last_events: expected a list of strings")
    cause = cause if cause in CAUSES else "game"
    start = _as_utc(_iso(born_at, "born_at"), "born_at")
    end = _as_utc(_iso(died_at, "died_at"), "died_at")
    lifetime_s = max(0, int((end - start).total_seconds()))
    events = [
        _one_line(e, EMERGENCY_EVENT_CHARS, "last_events")
        for e in last_events
        if isinstance(e, str) and e.strip()
    ][:EMERGENCY_MAX_EVENTS]
    action = _one_line(last_action, EMERGENCY_ACTION_CHARS, "last_action")
    name = _one_line(node_name, EMERGENCY_NAME_CHARS, "node_name")
    heir = _one_line(heir_address, EMERGENCY_NAME_CHARS, "heir_address")
    body = _will_body(
        "\n".join(
            [
                f"{heir}, это {name}; "
                "пишу без своей головы, голова уже молчит.",
                f"Смерть: {CAUSE_TEXT[cause]}; последнее действие: {action or 'неизвестно'}.",
                f"Перед концом видел: {', '.join(events) or 'ничего приметного'}.",
                f"Урок: {CAUSE_LESSON[cause]}. Проверь сам.",
            ]
        )
    )
    meta = WillMeta(
        serial=serial,
        slot=slot,
        cause=cause,
        born_at=start.isoformat(),
        died_at=end.isoformat(),
        lifetime_s=lifetime_s,
        emergency=True,
        traits=traits,
        model="template",
    )
    return Will.from_dict({**meta.to_dict(), "body": body})


# --- birth file and reflection IO (sections 6, 10) -----------------------------------


@dataclass(frozen=True)
class BirthFile:
    """nodes/<serial>/birth.json: identity, slot, deadline and the reproducible traits."""

    serial: int
    slot: str
    born_at: str
    ttl_min: int
    traits: Traits

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BirthFile":
        _want(data, ("serial", "slot", "born_at", "ttl_min", "traits"))
        return cls(
            serial=_int_field(_req(data, "serial"), "serial", minimum=1),
            slot=_text(_req(data, "slot"), "slot"),
            born_at=_iso(_req(data, "born_at"), "born_at"),
            ttl_min=_int_field(_req(data, "ttl_min"), "ttl_min", minimum=1, maximum=1440),
            traits=_traits(_req(data, "traits")),
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class ReflectionInput:
    """What goes into one smart call: birth goal pick, a reflection window, or the will."""

    serial: int
    kind: str  # birth | regular | death
    events: tuple[TriggerEvent, ...] = ()
    notes: tuple[str, ...] = ()
    core_memory: str = ""
    personal_goal: str = ""
    network_goals: tuple[str, ...] = ()
    cause: str = ""
    last_action: str = ""
    inheritance_summary: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReflectionInput":
        _want(
            data,
            (
                "serial",
                "kind",
                "events",
                "notes",
                "core_memory",
                "personal_goal",
                "network_goals",
                "cause",
                "last_action",
                "inheritance_summary",
            ),
        )
        kind = _req(data, "kind")
        if kind not in REFLECTION_KINDS:
            raise SchemaError(f"kind: {kind!r} not in {REFLECTION_KINDS}")
        cause = data.get("cause", "")
        if not isinstance(cause, str):
            raise SchemaError("cause: expected a string")
        if kind == "death" and cause not in CAUSES:
            raise SchemaError(f"cause: death reflection needs a cause in {CAUSES}")
        notes = _list_field(data.get("notes", []), "notes")
        if not all(isinstance(n, str) and n.strip() for n in notes):
            raise SchemaError("notes: expected a list of strings")
        goals = _list_field(data.get("network_goals", []), "network_goals")
        if not all(isinstance(g, str) and g.strip() for g in goals):
            raise SchemaError("network_goals: expected a list of strings")
        for key in ("core_memory", "personal_goal", "last_action", "inheritance_summary"):
            if not isinstance(data.get(key, ""), str):
                raise SchemaError(f"{key}: expected a string")
        return cls(
            serial=_int_field(_req(data, "serial"), "serial", minimum=1),
            kind=kind,
            events=tuple(
                TriggerEvent.from_dict(e)
                for e in _list_field(data.get("events", []), "events")
            ),
            notes=tuple(notes),
            core_memory=data.get("core_memory", ""),
            personal_goal=data.get("personal_goal", ""),
            network_goals=tuple(goals),
            cause=cause,
            last_action=data.get("last_action", ""),
            inheritance_summary=data.get("inheritance_summary", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class ReflectionOutput:
    """The smart call's reply: goal, core memory, up to three candidates, or the will."""

    personal_goal: str = ""
    core_memory: str = ""
    candidates: tuple[KnowledgeCandidate, ...] = ()
    will: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReflectionOutput":
        _want(data, ("personal_goal", "core_memory", "candidates", "will"))
        for key in ("personal_goal", "core_memory"):
            if not isinstance(data.get(key, ""), str):
                raise SchemaError(f"{key}: expected a string")
        candidates = tuple(
            _candidate(c) for c in _list_field(data.get("candidates", []), "candidates")
        )
        if len(candidates) > MAX_CANDIDATES:
            raise SchemaError(f"candidates: at most {MAX_CANDIDATES}")
        will = data.get("will")
        will_body = None if will is None else _will_body(will)
        out = cls(
            personal_goal=data.get("personal_goal", "").strip(),
            core_memory=data.get("core_memory", "").strip(),
            candidates=candidates,
            will=will_body,
        )
        if not (out.personal_goal or out.core_memory or out.candidates or out.will):
            raise SchemaError("empty reflection output")
        return out

    def to_dict(self) -> dict[str, Any]:
        data = _plain(asdict(self))
        if self.will is None:
            data.pop("will")
        return data
