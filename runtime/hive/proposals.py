"""The proposal queue and hive's common layers (T-028; NETWORK.md sections 4 and 7, ADR-034/036).

Sisters change the common — the base, common skills, common instructions — only by proposal
(ADR-034); Last Order decides (ADR-036); hive writes (ADR-017). This module is the queue in
between:

- proposals live in `base/proposals/p-NNNN.json`, under the same git as the records;
- a knowledge proposal enters the base at once as a hypothesis record (`submit_candidate`,
  a duplicate adds evidence instead of a record — dedup.py, NETWORK.md section 7); the
  decision then moves the record's status (`KnowledgeStore.apply_decision`);
- a skill proposal waits in the queue; on accept the store writes a confirmed `kind=skill`
  record plus the script under `base/skills/<record id>` (hive stores scripts, never runs
  them — a node's own copy runs through the body MCP, ADR-031);
- an instructions proposal carries a unified diff against `base/instructions.md`; on accept
  hive applies it. Any other path in the diff — the personality core included — is a refusal
  at submit time already (ADR-034: the core is operator-only). Delivering the instructions
  file into sister workplaces is T-027/T-029's business, not hive's.

Every decision lands in exactly one commit whose message names the decider, the reason and
the proposer (review P1-4), and the proposal file rides along in it. `CommonLayers` holds
the two remaining writes behind the same discipline: network goals (`base/goals.md`, the
format `_read_goals` parses) and the chronicle (`base/chronicle.md`, the future Telegram
channel's source, ADR-030).

Concurrency: one hive process (one uvicorn worker, compose comment of T-022), so a plain
in-process lock serializes read-modify-write cycles of the queue; the store's own file lock
still guards every commit against other processes. Lock order is always ProposalBook ->
store; the store never calls back here.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from runtime.knowledge.schema import CLAIM_MAX_CHARS
from runtime.knowledge.slice import format_serial
from runtime.knowledge.store import KnowledgeStore, StoreError
from runtime.perception.translate import NAMESPACED

PROPOSAL_KINDS = ("knowledge", "skill", "instructions")
PROPOSAL_STATUSES = ("pending", "accepted", "rejected")
KNOWLEDGE_KINDS = ("danger", "fact", "recipe", "term")

PROPOSAL_ID = re.compile(r"\Ap-\d{4,}\Z")
HUNK_HEAD = re.compile(r"\A@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIFF_FILE = re.compile(r"\A(-{3}|\+{3}) (.*)\Z")

SKILL_SCRIPT_MAX_CHARS = 16_384
CHRONICLE_MAX_CHARS = 2_000
CHRONICLE_LINE_SNIPPET = 48

# The common instructions of the network: one file hive owns inside the base. Everything
# else — the personality core (`agent/last_order/prompt_template.md`), workplaces, code —
# is out of the proposal path's reach by construction (store._layer_files keeps every
# layer write inside base/).
INSTRUCTIONS_REL = "base/instructions.md"
INSTRUCTIONS_NAMES = ("base/instructions.md", "instructions.md")
CORE_HINT = "agent/last_order/prompt_template.md"


class ProposalError(ValueError):
    """The proposal (or the diff, or the decision) is refused; nothing was written."""


# --- the unified diff of an instructions proposal ----------------------------------------


@dataclass(frozen=True)
class Hunk:
    """One @@ hunk: the old block it replaces and the lines it puts in place of it."""

    old_start: int  # 1-based line in the old file (0 for a creation hunk)
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]


def _diff_path(value: str) -> str:
    """Normalize a ---/+++ path: strip quotes and the a/ b/ prefixes."""
    path = value.strip().strip('"')
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break
    return path


def parse_unified_diff(diff: str) -> tuple[Hunk, ...]:
    """Structure-check one unified diff against the common instructions file.

    Hunks carry ' ', '-' and '+' lines; `--- `/`+++ ` headers may only name
    `base/instructions.md`. Anything else — another path, the personality core, a bare
    `\\`-marker line, no hunks at all — is a ProposalError, so a poisoned diff dies at
    submit time and never reaches a decision."""

    if not isinstance(diff, str) or not diff.strip():
        raise ProposalError("instructions diff: empty")
    hunks: list[Hunk] = []
    old_lines: list[str] = []
    new_lines: list[str] = []
    start = -1  # -1: no @@ header seen yet; 0 is a valid creation hunk start

    def finish_hunk() -> None:
        nonlocal old_lines, new_lines
        if start >= 0 and (old_lines or new_lines):
            hunks.append(
                Hunk(old_start=start, old_lines=tuple(old_lines), new_lines=tuple(new_lines))
            )
        old_lines, new_lines = [], []

    for raw in diff.splitlines():
        head = HUNK_HEAD.match(raw)
        if head:
            finish_hunk()
            start = int(head.group(1))
            continue
        file_line = DIFF_FILE.match(raw)
        if file_line and not old_lines and not new_lines:
            path = _diff_path(file_line.group(2))
            if path == "/dev/null":
                continue
            if path not in INSTRUCTIONS_NAMES:
                if path == CORE_HINT or "agent/" in path or "prompt_template" in path:
                    raise ProposalError(
                        f"instructions diff: {path} is outside the common instructions; "
                        "the personality core is operator-only (ADR-034)"
                    )
                raise ProposalError(
                    f"instructions diff: only {INSTRUCTIONS_REL} is editable, not {path}"
                )
            continue
        if start < 0:
            raise ProposalError("instructions diff: content before the first @@ hunk")
        if raw.startswith(" "):
            old_lines.append(raw[1:])
            new_lines.append(raw[1:])
        elif raw.startswith("-"):
            old_lines.append(raw[1:])
        elif raw.startswith("+"):
            new_lines.append(raw[1:])
        else:
            raise ProposalError(
                f"instructions diff: cannot parse line {raw[:60]!r} "
                "(only context, - and + lines, no '\\ No newline' markers)"
            )
    finish_hunk()
    if not hunks:
        raise ProposalError("instructions diff: no @@ hunks")
    return tuple(hunks)


def apply_unified_diff(current: str, hunks: Sequence[Hunk]) -> str:
    """Apply parsed hunks to the current instructions text; a context that does not match
    is a ProposalError (the decision fails, the proposal stays pending).

    The match is checked at the hunk's stated line first, then searched forward from the
    already-applied position — plain difflib output applies at position, hand-written
    offsets get the same tolerance GNU patch gives."""
    lines = current.splitlines()
    out: list[str] = []
    pos = 0
    for hunk in hunks:
        want = list(hunk.old_lines)
        start = (hunk.old_start - 1) if hunk.old_start > 0 else 0
        start = max(start, pos)
        if lines[start : start + len(want)] != want:
            found = next(
                (
                    i
                    for i in range(start, len(lines) - len(want) + 1)
                    if lines[i : i + len(want)] == want
                ),
                None,
            )
            if found is None:
                raise ProposalError(
                    f"instructions diff: context at line {hunk.old_start} does not match "
                    "the current base/instructions.md"
                )
            start = found
        out.extend(lines[pos:start])
        out.extend(hunk.new_lines)
        pos = start + len(want)
    out.extend(lines[pos:])
    return "\n".join(out) + "\n" if out else ""


# --- the proposal file ---------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalFile:
    """One queued proposal: `base/proposals/p-NNNN.json`."""

    id: str
    kind: str  # PROPOSAL_KINDS
    author: int
    payload: dict[str, Any]
    ts: str
    status: str = "pending"
    record_id: str | None = None  # knowledge: the record at submit; skill: written on accept
    decision: dict[str, Any] | None = None  # {by, accept, why, ts} once decided

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposalFile":
        unknown = sorted(set(data) - {"id", "kind", "author", "payload", "ts", "status", "record_id", "decision"})
        if unknown:
            raise ProposalError(f"proposal: unknown fields {unknown}")
        proposal_id = data.get("id")
        if not isinstance(proposal_id, str) or not PROPOSAL_ID.match(proposal_id):
            raise ProposalError(f"proposal: bad id {proposal_id!r}")
        kind = data.get("kind")
        if kind not in PROPOSAL_KINDS:
            raise ProposalError(f"proposal.kind: {kind!r} not in {PROPOSAL_KINDS}")
        author = data.get("author")
        if isinstance(author, bool) or not isinstance(author, int) or author < 1:
            raise ProposalError("proposal.author: expected a node serial")
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ProposalError("proposal.payload: expected an object")
        status = data.get("status", "pending")
        if status not in PROPOSAL_STATUSES:
            raise ProposalError(f"proposal.status: {status!r} not in {PROPOSAL_STATUSES}")
        ts = data.get("ts")
        if not isinstance(ts, str) or not ts:
            raise ProposalError("proposal.ts: expected an ISO-8601 string")
        record_id = data.get("record_id")
        if record_id is not None and not isinstance(record_id, str):
            raise ProposalError("proposal.record_id: expected a record id or null")
        decision = data.get("decision")
        if decision is not None:
            if not isinstance(decision, dict) or set(decision) != {"by", "accept", "why", "ts"}:
                raise ProposalError("proposal.decision: expected {by, accept, why, ts}")
            if not isinstance(decision["accept"], bool):
                raise ProposalError("proposal.decision.accept: expected a bool")
            for key in ("by", "why", "ts"):
                if not isinstance(decision[key], str) or not decision[key].strip():
                    raise ProposalError(f"proposal.decision.{key}: expected a non-empty string")
            if status == "pending":
                raise ProposalError("proposal: a decision closes the proposal")
        elif status != "pending":
            raise ProposalError("proposal: a closed proposal carries its decision")
        return cls(
            id=proposal_id,
            kind=kind,
            author=author,
            payload=dict(payload),
            ts=ts,
            status=status,
            record_id=record_id,
            decision=dict(decision) if decision is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "author": self.author,
            "payload": self.payload,
            "ts": self.ts,
            "status": self.status,
            "record_id": self.record_id,
        }
        if self.decision is not None:
            data["decision"] = self.decision
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _no_raw_ids(text: str, what: str) -> None:
    if not isinstance(text, str):
        raise ProposalError(f"{what}: expected a string")
    namespaced = NAMESPACED.findall(text)
    if namespaced:
        raise ProposalError(f"{what}: raw game ids {sorted(set(namespaced))} never enter the base")


# --- the queue -----------------------------------------------------------------------------


class ProposalBook:
    """The queue between a sister's `propose` and Last Order's `decide`."""

    def __init__(self, store: KnowledgeStore, *, now: Callable[[], str] | None = None):
        self._store = store
        self._dir = store.root / "base" / "proposals"
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())
        self._lock = threading.RLock()

    # -- reading ---------------------------------------------------------------------------

    def _path(self, proposal_id: str) -> Path:
        return self._dir / f"{proposal_id}.json"

    def _read(self, proposal_id: str) -> ProposalFile:
        path = self._path(proposal_id)
        if not path.exists():
            raise ProposalError(f"unknown proposal {proposal_id!r}")
        try:
            return ProposalFile.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ProposalError) as exc:
            raise StoreError(f"broken proposal file {proposal_id}: {exc}") from exc

    def get(self, proposal_id: str) -> ProposalFile:
        with self._lock:
            return self._read(proposal_id)

    def pending(self) -> list[ProposalFile]:
        """The open queue, oldest first — what Last Order reads."""
        with self._lock:
            out = []
            for path in sorted(self._dir.glob("p-*.json")):
                try:
                    proposal = ProposalFile.from_dict(json.loads(path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, ProposalError) as exc:
                    raise StoreError(f"broken proposal file {path.name}: {exc}") from exc
                if proposal.status == "pending":
                    out.append(proposal)
            return out

    def _next_id_locked(self) -> str:
        numbers = [int(path.stem[2:]) for path in self._dir.glob("p-*.json")]
        return f"p-{(max(numbers) + 1) if numbers else 1:04d}"

    # -- writing ---------------------------------------------------------------------------

    def submit(self, serial: int, kind: str, payload: Mapping[str, Any]) -> ProposalFile:
        """Queue one proposal. A knowledge proposal also enters the base right away
        (hypothesis record, dedup on the way); the queue entry references the record."""
        if kind not in PROPOSAL_KINDS:
            raise ProposalError(f"propose.kind: {kind!r} not in {PROPOSAL_KINDS}")
        with self._lock:
            record_id: str | None = None
            if kind == "knowledge":
                from runtime.ports import KnowledgeCandidate

                cleaned = self._knowledge_payload(payload)
                record = self._store.submit_candidate(
                    serial,
                    KnowledgeCandidate(
                        kind=cleaned["kind"],
                        claim=cleaned["claim"],
                        about=tuple(cleaned.get("about", ())),
                        evidence_event_ids=tuple(cleaned.get("evidence_event_ids", ())),
                    ),
                )
                record_id = record.id
            elif kind == "skill":
                cleaned = self._skill_payload(payload)
            else:
                cleaned = self._instructions_payload(payload)
            proposal = ProposalFile(
                id=self._next_id_locked(),
                kind=kind,
                author=serial,
                payload=cleaned,
                ts=self._now(),
                record_id=record_id,
            )
            self._store.commit_layer(
                f"proposal: {proposal.id} {kind} by {format_serial(serial)}",
                {self._path(proposal.id): proposal.to_json()},
            )
            return proposal

    def decide(
        self, proposal_id: str, *, accept: bool, why: str, by: str = "last_order"
    ) -> tuple[ProposalFile, str]:
        """Last Order's ruling. One commit carries the effect, the proposal file and a
        message naming the decider, the reason and the proposer (review P1-4). Returns
        (updated proposal, note) — the note explains moot outcomes (a record the votes
        retired before the decision)."""
        if not isinstance(why, str) or not why.strip():
            raise ProposalError("decide: why is required")
        with self._lock:
            proposal = self._read(proposal_id)
            if proposal.status != "pending":
                raise ProposalError(f"{proposal_id}: already {proposal.status}")
            ts = self._now()
            decision = {"by": by, "accept": bool(accept), "why": why.strip(), "ts": ts}
            updated = replace(proposal, status="accepted" if accept else "rejected", decision=decision)
            extra = {self._path(proposal.id): updated.to_json()}
            message = (
                f"decide {proposal.id} {'accept' if accept else 'reject'} by {by}: "
                f"{why.strip()}; proposer {format_serial(proposal.author)}"
            )
            note = ""
            if proposal.kind == "knowledge":
                record = self._record_of(proposal)
                if record is None:
                    raise ProposalError(f"{proposal_id}: record {proposal.record_id!r} is gone")
                if record.status == "retired" or record.decision is not None:
                    # The record left the queue's reach before the ruling — retired by
                    # votes (section 4 stays terminal) or decided separately: the queue
                    # item closes without touching the record.
                    self._store.commit_layer(message, extra)
                    note = (
                        f"record {record.id} already retired by votes; proposal closed"
                        if record.status == "retired"
                        else f"record {record.id} was decided separately; proposal closed"
                    )
                else:
                    record = self._store.apply_decision(
                        record.id, by=by, accept=accept, why=why.strip(),
                        extra_files=extra, message=message,
                    )
                    updated = replace(updated, record_id=record.id)
            elif proposal.kind == "skill":
                if accept:
                    record = self._store.write_skill(
                        author=proposal.author,
                        description=proposal.payload["description"],
                        script=proposal.payload["script"],
                        by=by,
                        why=why.strip(),
                        extra_files=extra,
                        message=message,
                    )
                    updated = replace(updated, record_id=record.id)
                else:
                    self._store.commit_layer(message, extra)
            else:  # instructions
                if accept:
                    current = self._instructions_text()
                    new_text = apply_unified_diff(current, parse_unified_diff(proposal.payload["diff"]))
                    self._store.commit_layer(message, {**extra, INSTRUCTIONS_REL: new_text})
                else:
                    self._store.commit_layer(message, extra)
            return updated, note

    # -- payload validation ------------------------------------------------------------------

    def _knowledge_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Coerce a knowledge payload to a JSON-safe draft; the store's `submit_candidate`
        carries the full record validation (claim length, about keys, raw ids)."""
        if not isinstance(payload, Mapping):
            raise ProposalError("propose payload: expected an object")
        allowed = {"kind", "claim", "about", "evidence_event_ids"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ProposalError(f"knowledge payload: unknown fields {unknown}")
        kind = payload.get("kind")
        if kind not in KNOWLEDGE_KINDS:
            raise ProposalError(
                f"knowledge payload.kind: {kind!r} not in {KNOWLEDGE_KINDS} "
                "(a skill is propose kind='skill')"
            )
        claim = payload.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            raise ProposalError("knowledge payload.claim: expected a non-empty string")

        def _str_list(value: Any, what: str) -> list[str]:
            if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
                raise ProposalError(f"knowledge payload.{what}: expected a list of strings")
            return list(value)

        return {
            "kind": kind,
            "claim": claim,
            "about": _str_list(payload.get("about", []), "about"),
            "evidence_event_ids": _str_list(payload.get("evidence_event_ids", []), "evidence_event_ids"),
        }

    def _skill_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ProposalError("propose payload: expected an object")
        unknown = sorted(set(payload) - {"description", "script"})
        if unknown:
            raise ProposalError(f"skill payload: unknown fields {unknown}")
        description = payload.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ProposalError("skill payload.description: expected a non-empty string")
        if len(description.strip()) > CLAIM_MAX_CHARS:
            raise ProposalError(
                f"skill payload.description: longer than {CLAIM_MAX_CHARS} chars (the record's claim)"
            )
        script = payload.get("script")
        if not isinstance(script, str) or not script.strip():
            raise ProposalError("skill payload.script: expected a non-empty string")
        if len(script) > SKILL_SCRIPT_MAX_CHARS:
            raise ProposalError(f"skill payload.script: longer than {SKILL_SCRIPT_MAX_CHARS} chars")
        _no_raw_ids(description, "skill.description")
        _no_raw_ids(script, "skill.script")
        return {"description": description.strip(), "script": script}

    def _instructions_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise ProposalError("propose payload: expected an object")
        unknown = sorted(set(payload) - {"diff"})
        if unknown:
            raise ProposalError(f"instructions payload: unknown fields {unknown}")
        diff = payload.get("diff")
        parse_unified_diff(diff)  # structure, paths and the core refusal — before queueing
        return {"diff": diff}

    # -- helpers -----------------------------------------------------------------------------

    def _record_of(self, proposal: ProposalFile):
        if not proposal.record_id:
            return None
        try:
            return self._store.record(proposal.record_id)
        except StoreError:
            return None

    def _instructions_text(self) -> str:
        path = self._store.root / INSTRUCTIONS_REL
        return path.read_text(encoding="utf-8") if path.exists() else ""


# --- goals and the chronicle ----------------------------------------------------------------


class CommonLayers:
    """The remaining common writes behind the proposal discipline: network goals and the
    chronicle. Records go through the store's own paths; these two are plain layer files."""

    def __init__(self, store: KnowledgeStore, *, now: Callable[[], str] | None = None):
        self._store = store
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())

    def set_goals(self, goals: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
        """Replace `base/goals.md` (ADR-036: Last Order sets the network's goals; the
        operator adds his own through Last Order's GOALS.md, not here). The format is the
        one the store's `_read_goals` parses; the limits come from goals_schema."""
        from runtime.hive.goals_schema import (
            GOAL_TEXT_MAX_CHARS,
            GOAL_WHY_MAX_CHARS,
            MAX_NETWORK_GOALS,
        )

        if not isinstance(goals, (list, tuple)) or not 1 <= len(goals) <= MAX_NETWORK_GOALS:
            raise ProposalError(f"goals: expected a list of 1-{MAX_NETWORK_GOALS} goals")
        cleaned: list[dict[str, str]] = []
        for item in goals:
            if not isinstance(item, Mapping) or set(item) - {"text", "why"}:
                raise ProposalError("goals: each goal is {text, why}")
            text, why = item.get("text"), item.get("why", "")
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= GOAL_TEXT_MAX_CHARS:
                raise ProposalError(f"goals.text: 1-{GOAL_TEXT_MAX_CHARS} characters")
            if not isinstance(why, str) or len(why.strip()) > GOAL_WHY_MAX_CHARS:
                raise ProposalError(f"goals.why: at most {GOAL_WHY_MAX_CHARS} characters")
            cleaned.append({"text": text.strip(), "why": why.strip()})
        self._store.commit_layer(
            f"goals: set {len(cleaned)} by last_order",
            {Path("base/goals.md"): yaml.safe_dump(cleaned, allow_unicode=True, sort_keys=False)},
        )
        return cleaned

    def append_chronicle(self, text: str) -> str:
        """Append one entry to `base/chronicle.md` — the log the Telegram channel of the
        chronicle will publish (ADR-030). One entry, one commit."""
        if not isinstance(text, str) or not text.strip():
            raise ProposalError("chronicle: expected a non-empty text")
        entry = " ".join(text.split())
        if len(entry) > CHRONICLE_MAX_CHARS:
            raise ProposalError(f"chronicle: longer than {CHRONICLE_MAX_CHARS} chars")
        path = Path("base/chronicle.md")
        current = self._store.root / path
        old = current.read_text(encoding="utf-8") if current.exists() else ""
        line = f"- {self._now()} — {entry}\n"
        self._store.commit_layer(
            f"chronicle: {entry[:CHRONICLE_LINE_SNIPPET]}",
            {path: old + line},
        )
        return line


__all__ = [
    "CommonLayers",
    "Hunk",
    "INSTRUCTIONS_REL",
    "ProposalBook",
    "ProposalError",
    "ProposalFile",
    "apply_unified_diff",
    "parse_unified_diff",
]
