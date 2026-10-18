"""KnowledgeStore: the only writer of the network's knowledge base (ADR-017, T-012).

The base is a git repository (the `knowledge` volume, mounted into hive); nodes never
touch files, hive (T-022) is the only caller of this module, and every write is exactly
one git commit authored as `hive`, so the base carries its own history and `git blame`.
Formats, statuses, wills and budgets are KNOWLEDGE.md v2 as code in schema.py/budget.py
(T-007); this module persists them:

- layout `base/records/k-NNNN.json | base/catalog.md | base/goals.md | nodes/<serial>/…`
  (`<serial>` is the 5-digit form, ADR-018);
- node files: birth.json (birth), core.md (store_core_memory), will.md (retire) — a node
  is alive while it has birth.json and no will.md, which keeps the documented layout
  free of extra marker files;
- the serial counter `.serial` and the write lock `.lock` are gitignored internals; the
  counter survives restarts and is recovered from existing nodes when missing.

Statuses follow section 4 as a pure function of evidence and votes, recomputed after
every change — with one lock: a retired record never changes again, a vote or evidence
for it is an error (review P1-3; the fix for a retired record is a new record).

Raw ids (ADR-020 critical zone): a namespaced `minecraft:…` id in a claim or a vote's
`why` is refused before anything is written; an optional `leak_check` callable (e.g.
`Perception.leaks` of T-009) extends the check to ids the caller has seen (review P1-4).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from runtime.config import CHARACTER_DIR, env
from runtime.knowledge.budget import (
    B_CATALOG,
    CORE_MEMORY_LIMIT_BYTES,
    estimate_tokens,
)
from runtime.knowledge.dedup import find_duplicate
from runtime.knowledge.schema import (
    ABOUT_KEY,
    CAUSES,
    BirthFile,
    Decision,
    Evidence,
    PrimerEntry,
    Record,
    RecordVote,
    SchemaError,
    Will,
    WillMeta,
)
from runtime.knowledge.slice import (
    build_inheritance,
    build_slice,
    format_serial,
    parse_utc,
)
from runtime.lastorder.persona import Inheritance
from runtime.lastorder.traits import Traits
from runtime.perception.translate import NAMESPACED
from runtime.ports import (
    BirthRecord,
    KnowledgeCandidate,
    KnowledgeSlice,
    NetworkGoal,
    NodeBrief,
    Vote,
)

DEFAULT_TTL_MIN = 45  # forced death after this many minutes (CLAUDE.md); T-022 reads it
GIT_IDENTITY = ("hive", "hive@last-order")
LOCK_TIMEOUT_S = 120.0

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fh) -> bool:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class StoreError(ValueError):
    """The base rejects the input; nothing was written."""


class UnknownNodeError(StoreError):
    """No birth.json for this serial."""


class AlreadyRetiredError(StoreError):
    """The node already has a will."""


class InvalidWillError(StoreError):
    """The will text does not parse as KNOWLEDGE.md section 5."""


class InvalidCandidateError(StoreError):
    """The candidate (or its claim's raw ids) does not pass the record rules."""


class InvalidVoteError(StoreError):
    """The vote does not pass the rules (unknown/primer/retired record, raw ids)."""


def status_of(
    source: str,
    author: int | str,
    evidence: Sequence[Evidence],
    votes: Sequence[RecordVote],
    decision: "Decision | None" = None,
) -> str:
    """Section 4 status as a pure function of evidence, votes and a Last Order decision.

    Only nodes other than the author count: the author's own vote is his candidate, his
    evidence his testimony. A rejection with a reason is a refutation and outranks
    evidence; a rejection without one changes nothing by itself but still counts as
    "against" for the two-voices confirmation. Primer records are always confirmed.
    Retired is enforced as terminal by the store, not by this function (review P1-3).

    A decision (T-028, NETWORK.md section 7: "решения Last Order двигают статусы") settles
    the record: accept -> confirmed, reject -> retired, above refuters and evidence —
    Last Order administers the shared base (ADR-036). The section 4 thresholds stay the
    lower bound for records nobody has decided on (KNOWLEDGE.md v2 header).
    """

    if source == "primer":
        return "confirmed"
    if decision is not None:
        return "confirmed" if decision.accept else "retired"
    others = [v for v in votes if v.node != author]
    refuters = {v.node for v in others if not v.accept and v.why}
    if len(refuters) >= 2:
        return "retired"
    if refuters:
        return "disputed"
    if any(e.node != author for e in evidence):
        return "confirmed"
    if sum(1 for v in others if v.accept) >= 2 and not any(not v.accept for v in others):
        return "confirmed"
    return "hypothesis"


def fit_core_memory(text: str) -> str:
    """Cut core memory to CORE_MEMORY_LIMIT_BYTES on a line boundary (section 6).

    The final newline is included in the limit; a single overlong line is cut on a
    character boundary — the byte ceiling is hard, the line boundary is best effort.
    """

    out = text.rstrip()
    while out and len(out.encode("utf-8")) + 1 > CORE_MEMORY_LIMIT_BYTES:
        idx = out.rfind("\n")
        if idx > 0:
            out = out[:idx].rstrip()
        else:
            out = out.encode("utf-8")[: CORE_MEMORY_LIMIT_BYTES - 1].decode(
                "utf-8", "ignore"
            ).rstrip()
    return out + "\n" if out else ""


def _json_text(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_atomic(path: Path, data: bytes) -> None:
    """Write through a temp file in the same directory, then `os.replace` into place.

    Readers take no lock (review R1-2), so they must never see a half-written file:
    `os.replace` is atomic within one filesystem, and the temp file lives beside the
    target to stay on it. A crash between write and replace leaves a `*.tmp` only.
    """

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class KnowledgeStore:
    """The shared base under `root`: the only code that writes it (ADR-017)."""

    def __init__(
        self,
        root: Path,
        git: bool = True,
        *,
        primer_dir: Path | None = None,
        leak_check: Callable[[str], Sequence[str]] | None = None,
        now: Callable[[], str] | None = None,
    ):
        self.root = Path(root)
        self.git = git
        self.leak_check = leak_check
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())
        self._records_dir = self.root / "base" / "records"
        self._catalog_path = self.root / "base" / "catalog.md"
        self._goals_path = self.root / "base" / "goals.md"
        self._nodes_dir = self.root / "nodes"
        self._serial_path = self.root / ".serial"
        self._lock_path = self.root / ".lock"

        fresh = not self.root.exists() or not any(self.root.iterdir())
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (self._records_dir, self._nodes_dir):
            directory.mkdir(parents=True, exist_ok=True)
        if self.git and not (self.root / ".git").exists():
            self._git("init", check=True)
            self._git("config", "core.autocrlf", "false")
        if not fresh:
            return

        with self._locked():
            files = {
                self.root / ".gitignore": ".lock\n.serial\n*.tmp\n",
                self._catalog_path: "",
                self._goals_path: "",
            }
            self._commit_op("init: layout", files)
            mode = (env("PERCEPTION_MODE") or "plain").strip().lower()
            if mode == "plain":  # ADR-020: in masked the node does not know the game
                self._load_primer_locked(primer_dir or Path(env("PRIMER_DIR") or CHARACTER_DIR / "primer"))

    # --- git and transactions -----------------------------------------------------------

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """A git call inside the base repo, isolated from the operator's global config."""
        command = ["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", *args]
        environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": GIT_IDENTITY[0],
            "GIT_AUTHOR_EMAIL": GIT_IDENTITY[1],
            "GIT_COMMITTER_NAME": GIT_IDENTITY[0],
            "GIT_COMMITTER_EMAIL": GIT_IDENTITY[1],
        }
        return subprocess.run(
            command,
            cwd=self.root,
            env=environment,
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _commit_op(self, message: str, files: Mapping[Path, str]) -> None:
        """Write files and make them exactly one commit; a failure rolls the files back."""
        snapshot = {
            path: path.read_bytes() if path.exists() else None for path in files
        }
        try:
            for path, text in files.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                _write_atomic(path, text.encode("utf-8"))
            if self.git:
                self._git(
                    "add", "--", *(str(p.relative_to(self.root)) for p in files)
                )
                self._git("commit", "-m", message)
        except Exception as exc:
            for path, was in snapshot.items():
                if was is None:
                    path.unlink(missing_ok=True)
                    path.with_name(path.name + ".tmp").unlink(missing_ok=True)
                else:
                    _write_atomic(path, was)
            if self.git:
                # A successful `git add` leaves the content staged: unstage it, or the
                # next whole-index commit silently swallows the aborted operation (C-4).
                self._git(
                    "reset",
                    "--",
                    *(str(p.relative_to(self.root)) for p in files),
                    check=False,
                )
            if isinstance(exc, subprocess.CalledProcessError):
                raise StoreError(f"git failed: {exc.stderr.strip()}") from exc
            raise

    @contextmanager
    def _locked(self):
        """One writer at a time across threads and processes (the `.lock` file)."""
        self.root.mkdir(parents=True, exist_ok=True)
        fh = open(self._lock_path, "a+b")
        try:
            deadline = time.monotonic() + LOCK_TIMEOUT_S
            while not _try_lock(fh):
                if time.monotonic() > deadline:
                    raise StoreError("knowledge base lock timeout")
                time.sleep(0.05)
            yield
        finally:
            try:
                _unlock(fh)
            finally:
                fh.close()

    # --- serials and ids -----------------------------------------------------------------

    def next_serial(self) -> int:
        """The next network-wide node number, atomic across threads and processes."""
        with self._locked():
            return self._next_serial_locked()

    def _next_serial_locked(self) -> int:
        current = 0
        if self._serial_path.exists():
            text = self._serial_path.read_text(encoding="utf-8").strip()
            if text.isdigit():
                current = int(text)
        for child in self._nodes_dir.iterdir():  # counter lost: recover from the base
            if child.is_dir() and child.name.isdigit():
                current = max(current, int(child.name))
        serial = current + 1
        _write_atomic(self._serial_path, str(serial).encode("utf-8"))
        return serial

    def _next_record_number_locked(self) -> int:
        numbers = [
            int(path.stem[2:]) for path in self._records_dir.glob("k-*.json")
        ]
        return (max(numbers) + 1) if numbers else 1

    # --- nodes ---------------------------------------------------------------------------

    def _node_dir(self, serial: int) -> Path:
        return self._nodes_dir / format_serial(serial)

    def _birth_path(self, serial: int) -> Path:
        return self._node_dir(serial) / "birth.json"

    def _will_path(self, serial: int) -> Path:
        return self._node_dir(serial) / "will.md"

    def _core_path(self, serial: int) -> Path:
        return self._node_dir(serial) / "core.md"

    def _load_birth(self, serial: int) -> BirthFile:
        path = self._birth_path(serial)
        if not path.exists():
            raise UnknownNodeError(f"unknown node {format_serial(serial)}")
        try:
            return BirthFile.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (SchemaError, json.JSONDecodeError) as exc:
            raise StoreError(f"broken birth file of {format_serial(serial)}: {exc}") from exc

    def _require_alive(self, serial: int) -> BirthFile:
        birth = self._load_birth(serial)
        if self._will_path(serial).exists():
            raise AlreadyRetiredError(f"node {format_serial(serial)} is retired")
        return birth

    def birth(
        self, slot: str, traits: Traits, seed: int | None = None, ttl_min: int = DEFAULT_TTL_MIN
    ) -> BirthRecord:
        """Register a newborn node; slot occupancy is the caller's (hive, T-022) sequencing."""
        if seed is not None and seed != traits.seed:
            raise StoreError(
                f"seed {seed} does not match traits.seed {traits.seed}: traits must reproduce from seed"
            )
        with self._locked():
            serial = self._next_serial_locked()
            born_at = self._now()
            data = {
                "serial": serial,
                "slot": slot,
                "born_at": born_at,
                "ttl_min": ttl_min,
                "traits": traits.as_dict(),
            }
            try:
                birth_file = BirthFile.from_dict(data)  # validates before anything is written
            except SchemaError as exc:
                raise StoreError(f"birth rejected: {exc}") from exc
            self._commit_op(
                f"birth: {format_serial(serial)} {slot}",
                {self._birth_path(serial): _json_text(birth_file.to_dict())},
            )
            return BirthRecord(serial=serial, born_at=born_at, ttl_min=ttl_min)

    def retire(self, serial: int, cause: str, will_text: str) -> None:
        """Write the node's will.md and mark it dead (review P1-1).

        `will_text` is the whole will.md the dying mind composed (frontmatter + body,
        `Will.from_markdown` shape; the emergency template included). The store owns the
        service fields: slot, born_at and traits come from its birth.json, cause from the
        argument, lifetime_s is recomputed from hive's born_at to the will's died_at with
        clock skew clamped to zero. Frontmatter serial must match the argument.
        """

        if cause not in CAUSES:
            raise InvalidWillError(f"cause {cause!r} not in {CAUSES}")
        with self._locked():
            birth = self._require_alive(serial)
            try:
                will = Will.from_markdown(will_text)
            except SchemaError as exc:
                raise InvalidWillError(f"broken will text: {exc}") from exc
            if will.meta.serial != serial:
                raise InvalidWillError(
                    f"will frontmatter serial {will.meta.serial} does not match {format_serial(serial)}"
                )
            # The body rides into the heir's prompt (inherited_notes): namespaced ids are
            # refused here too (review R1-3); leak_check is NOT applied — an emergency
            # will assembled from post-perception fields must not die on unseen ids.
            namespaced = NAMESPACED.findall(will.body)
            if namespaced:
                raise InvalidWillError(
                    f"will body: raw game ids {sorted(set(namespaced))} never enter the base"
                )
            died = parse_utc(will.meta.died_at)
            born = parse_utc(birth.born_at)
            lifetime_s = max(0, int((died - born).total_seconds()))
            meta = WillMeta(
                serial=serial,
                slot=birth.slot,
                cause=cause,
                born_at=birth.born_at,
                died_at=will.meta.died_at,
                lifetime_s=lifetime_s,
                emergency=will.meta.emergency,
                traits=birth.traits,
                model=will.meta.model,
            )
            final = Will(meta=meta, body=will.body)
            try:
                Will.from_dict(final.to_dict())  # the assembled will must be valid as-is
            except SchemaError as exc:
                raise InvalidWillError(f"assembled will is invalid: {exc}") from exc
            self._commit_op(
                f"retire: {format_serial(serial)} {cause}",
                {self._will_path(serial): final.to_markdown()},
            )

    def alive(self) -> list[NodeBrief]:
        """Nodes with a birth.json and no will yet."""
        briefs = []
        for child in sorted(self._nodes_dir.iterdir()):
            if not (child.is_dir() and child.name.isdigit()):
                continue
            birth = self._load_birth(int(child.name))
            if not (child / "will.md").exists():
                briefs.append(NodeBrief(serial=int(child.name), slot=birth.slot))
        return briefs

    # --- core memory ---------------------------------------------------------------------

    def core_memory(self, serial: int) -> str:
        """The node's core.md; a node born without one has empty core memory."""
        self._load_birth(serial)
        path = self._core_path(serial)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def store_core_memory(self, serial: int, text: str) -> None:
        if not isinstance(text, str):
            raise StoreError("core memory: expected a string")
        with self._locked():
            self._require_alive(serial)
            self._commit_op(
                f"core: {format_serial(serial)}",
                {self._core_path(serial): fit_core_memory(text)},
            )

    # --- records -------------------------------------------------------------------------

    def records(self) -> list[Record]:
        paths = sorted(
            self._records_dir.glob("k-*.json"), key=lambda p: int(p.stem[2:])
        )
        return [self._read_record(path) for path in paths]

    def record(self, record_id: str) -> Record:
        path = self._records_dir / f"{record_id}.json"
        if not path.exists():
            raise StoreError(f"unknown record {record_id!r}")
        return self._read_record(path)

    def _read_record(self, path: Path) -> Record:
        try:
            return Record.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (SchemaError, json.JSONDecodeError) as exc:
            raise StoreError(f"broken record file {path.name}: {exc}") from exc

    def _refuse_raw_ids(self, text: Any, error: type[StoreError], what: str) -> str:
        if not isinstance(text, str):
            raise error(f"{what}: expected a string")
        namespaced = NAMESPACED.findall(text)
        if namespaced:
            raise error(f"{what}: raw game ids {sorted(set(namespaced))} never enter the base")
        if self.leak_check is not None:
            leaked = list(self.leak_check(text))
            if leaked:
                raise error(f"{what}: raw ids leaked {leaked}")
        return text

    def _author_evidence(
        self, serial: int, events: Sequence[str], ts: str
    ) -> tuple[Evidence, ...]:
        entries = tuple(
            Evidence(node=serial, event=event or None, ts=ts) for event in events
        )
        return entries or (Evidence(node=serial, event=None, ts=ts),)

    def submit_candidate(self, serial: int, candidate: KnowledgeCandidate) -> Record:
        """A node's candidate: a new hypothesis record, or evidence for its duplicate.

        The duplicate rule is section 4 via dedup.py; near misses become competing
        records. The author's observation is always his record's first evidence (it does
        not confirm anything — only another node's does).
        """

        with self._locked():
            self._require_alive(serial)
            if not isinstance(candidate, KnowledgeCandidate):
                raise InvalidCandidateError("candidate: expected a KnowledgeCandidate")
            self._refuse_raw_ids(candidate.claim, InvalidCandidateError, "candidates.claim")
            ts = self._now()
            evidence = self._author_evidence(serial, candidate.evidence_event_ids, ts)
            # One full validation for both branches, before any dedup (review R1-1): a
            # broken candidate must die at the door even when it would duplicate a record.
            draft = {
                "id": "k-0000",  # placeholder; the real id is issued after the dedup decision
                "kind": candidate.kind,
                "claim": candidate.claim,
                "about": list(candidate.about),
                "source": "node",
                "author": serial,
                "ts": ts,
                "status": "hypothesis",
                "evidence": [e.to_dict() for e in evidence],
                "votes": [],
            }
            try:
                validated = Record.from_dict(draft)
            except SchemaError as exc:
                raise InvalidCandidateError(f"candidate rejected: {exc}") from exc
            records = self.records()
            duplicate = find_duplicate(records, validated.claim, validated.about)
            if duplicate is not None:
                if any(e.node == serial for e in duplicate.evidence):
                    return duplicate  # this node's evidence is already on record
                joined = duplicate.evidence + evidence
                data = duplicate.to_dict()
                data["evidence"] = [e.to_dict() for e in joined]
                # The duplicate's decision rides along (review R1-1): evidence joins never
                # un-decide a record Last Order has ruled on.
                data["status"] = status_of(
                    duplicate.source, duplicate.author, joined, duplicate.votes, duplicate.decision
                )
                try:
                    updated = Record.from_dict(data)
                except SchemaError as exc:
                    raise InvalidCandidateError(f"candidate rejected: {exc}") from exc
                self._write_record(updated, f"evidence: {updated.id} by {serial}")
                return updated
            record_id = f"k-{self._next_record_number_locked():04d}"
            record = replace(validated, id=record_id)
            self._write_record(record, f"record: {record.id} hypothesis by {serial}")
            return record

    def submit_vote(self, serial: int, vote: Vote) -> None:
        """One node's vote on a record; a repeat vote replaces the old one (section 4)."""

        with self._locked():
            self._require_alive(serial)
            if not isinstance(vote, Vote):
                raise InvalidVoteError("vote: expected a Vote")
            if not isinstance(vote.accept, bool):
                raise InvalidVoteError("vote.accept: expected a bool")
            why = self._refuse_raw_ids(vote.why, InvalidVoteError, "votes.why")
            path = self._records_dir / f"{vote.entry_id}.json"
            if not path.exists():
                raise InvalidVoteError(f"unknown record {vote.entry_id!r}")
            record = self._read_record(path)
            if record.source == "primer":
                raise InvalidVoteError(
                    f"{record.id}: primer records are not voted on (section 2)"
                )
            if record.status == "retired":
                raise InvalidVoteError(
                    f"{record.id}: retired is terminal, the fix is a new record"
                )
            votes = tuple(v for v in record.votes if v.node != serial) + (
                RecordVote(node=serial, accept=vote.accept, why=why, ts=self._now()),
            )
            data = record.to_dict()
            data["votes"] = [v.to_dict() for v in votes]
            # The record's decision rides along (review R1-1): a vote never un-decides
            # what Last Order has ruled on — it is recorded, the status stays settled.
            data["status"] = status_of(
                record.source, record.author, record.evidence, votes, record.decision
            )
            try:
                updated = Record.from_dict(data)
            except SchemaError as exc:
                raise InvalidVoteError(f"vote rejected: {exc}") from exc
            self._write_record(updated, f"vote: {updated.id} by {serial}")

    def _write_record(
        self,
        record: Record,
        message: str,
        extra_files: Mapping[Path, str] | None = None,
    ) -> None:
        records = [r for r in self.records() if r.id != record.id] + [record]
        files = {
            self._records_dir / f"{record.id}.json": _json_text(record.to_dict()),
            self._catalog_path: self._catalog_text(records),
        }
        if extra_files:
            files.update(self._layer_files(extra_files))
        self._commit_op(message, files)

    # --- decisions, skills and hive's own layers (T-028) ---------------------------------

    def _layer_path(self, raw: Path | str) -> Path:
        """Resolve one layer path against the root; it must stay inside `base/`."""
        path = Path(raw)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        base = (self.root / "base").resolve()
        if resolved == base or not resolved.is_relative_to(base):
            raise StoreError(f"layer write outside base/: {raw}")
        return resolved

    def _layer_files(self, files: Mapping[Path, str] | None) -> dict[Path, str]:
        """Caller files of a layer write: base/-relative paths only, plain text values.

        The condition of the T-028 plan review: everything `commit_layer` and the decision
        helpers may touch stays inside `base/` — records of nodes and the `nodes/` tree are
        out of reach by construction, so the personality core can never ride this path.
        """
        out: dict[Path, str] = {}
        for raw, text in (files or {}).items():
            if not isinstance(text, str):
                raise StoreError(f"layer write {raw}: expected text")
            out[self._layer_path(raw)] = text
        return out

    def apply_decision(
        self,
        record_id: str,
        *,
        by: str,
        accept: bool,
        why: str,
        extra_files: Mapping[Path, str] | None = None,
        message: str | None = None,
    ) -> Record:
        """Last Order's ruling on a live record (T-028): set `decision`, recompute the
        status (`status_of`), one commit. Refuses unknown records, primer, retired
        (terminal — the fix is a new record) and a second ruling.

        `extra_files`/`message` let the caller (hive's proposal queue) ride its own bookkeeping
        into the same commit, so the decision and its effect never split across commits.
        """
        if not isinstance(why, str) or not why.strip():
            raise StoreError("decision: why is required")
        with self._locked():
            path = self._records_dir / f"{record_id}.json"
            if not path.exists():
                raise StoreError(f"unknown record {record_id!r}")
            record = self._read_record(path)
            if record.source == "primer":
                raise StoreError(f"{record.id}: primer records are not decided (section 2)")
            if record.decision is not None:
                raise StoreError(f"{record.id}: already decided")
            if record.status == "retired":
                raise StoreError(f"{record.id}: retired is terminal, the fix is a new record")
            decision = Decision(by=by, accept=bool(accept), why=why.strip(), ts=self._now())
            data = record.to_dict()
            data["decision"] = decision.to_dict()
            data["status"] = status_of(
                record.source, record.author, record.evidence, record.votes, decision
            )
            try:
                updated = Record.from_dict(data)
            except SchemaError as exc:
                raise StoreError(f"decision rejected: {exc}") from exc
            self._write_record(
                updated,
                message
                or f"decision: {updated.id} {'accept' if accept else 'reject'} by {by}: {why.strip()}",
                extra_files=extra_files,
            )
            return updated

    def write_skill(
        self,
        *,
        author: int,
        description: str,
        script: str,
        by: str,
        why: str,
        extra_files: Mapping[Path, str] | None = None,
        message: str | None = None,
    ) -> Record:
        """An accepted skill proposal becomes a common skill (T-028, ADR-031/034): one
        confirmed `kind=skill` record (the description is its claim) plus the script under
        `base/skills/<record id>`, exactly one commit. The script is stored, never run by
        hive; nodes execute their own copies through the body MCP."""
        if not isinstance(why, str) or not why.strip():
            raise StoreError("decision: why is required")
        with self._locked():
            self._load_birth(author)  # the proposer must be a real node; dead is fine
            self._refuse_raw_ids(description, InvalidCandidateError, "skill.description")
            self._refuse_raw_ids(script, InvalidCandidateError, "skill.script")
            decision = Decision(by=by, accept=True, why=why.strip(), ts=self._now())
            draft = {
                "id": "k-0000",
                "kind": "skill",
                "claim": description,
                "about": [],
                "source": "node",
                "author": author,
                "ts": self._now(),
                "status": "confirmed",
                "evidence": [],
                "votes": [],
                "decision": decision.to_dict(),
            }
            try:
                validated = Record.from_dict(draft)
            except SchemaError as exc:
                raise InvalidCandidateError(f"skill rejected: {exc}") from exc
            record_id = f"k-{self._next_record_number_locked():04d}"
            record = replace(validated, id=record_id)
            files: dict[Path, str] = {
                Path(f"base/skills/{record_id}"): script,
            }
            if extra_files:
                files.update(extra_files)
            self._write_record(
                record,
                message
                or f"decision: skill {record_id} accept by {by}: {why.strip()} (proposer {format_serial(author)})",
                extra_files=files,
            )
            return record

    def commit_layer(self, message: str, files: Mapping[Path, str]) -> None:
        """One locked commit for hive's own layers (T-028): the proposal queue, common
        instructions, the chronicle, network goals. Only hive code calls this — the base
        keeps its single writer (ADR-017) — and every path must live under `base/`."""
        if not isinstance(message, str) or not message.strip():
            raise StoreError("layer commit: message is required")
        with self._locked():
            self._commit_op(message, self._layer_files(files))

    def append_layer(self, relpath: Path | str, text: str, *, message: str) -> None:
        """Append `text` to one base/ layer file as one locked commit. The chronicle
        appends read-modify-write: the read and the write share one hold of the lock,
        so a concurrent commit can never interleave (T-028 checker C-5)."""
        if not isinstance(message, str) or not message.strip():
            raise StoreError("layer commit: message is required")
        if not isinstance(text, str):
            raise StoreError("layer append: expected text")
        with self._locked():
            path = self._layer_path(relpath)
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            self._commit_op(message, {path: old + text})

    # --- catalog and goals -----------------------------------------------------------------

    def _catalog_text(self, records: Sequence[Record]) -> str:
        """Layer-1 catalog: one line per live non-primer record, newest kept when trimmed."""
        lines = [
            f"{r.id} {r.kind} {' '.join(r.about)}".rstrip()
            for r in sorted(records, key=lambda r: int(r.id[2:]))
            if r.source != "primer" and r.status != "retired"
        ]
        kept: list[str] = []
        total = 0
        for line in reversed(lines):  # ids ascend, so reversed drops the oldest first
            cost = estimate_tokens(line + "\n")
            if total + cost > B_CATALOG:
                break
            kept.append(line)
            total += cost
        return "\n".join(reversed(kept)) + "\n" if kept else ""

    def _read_goals(self) -> tuple[NetworkGoal, ...]:
        """base/goals.md as NetworkGoal tuples; T-016 owns the writing, empty is normal."""
        if not self._goals_path.exists():
            return ()
        text = self._goals_path.read_text(encoding="utf-8").strip()
        if not text:
            return ()
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise StoreError(f"broken goals.md: {exc}") from exc
        if not isinstance(data, list):
            raise StoreError("goals.md: expected a YAML list of {text, why}")
        goals = []
        for item in data:
            if not isinstance(item, Mapping) or not isinstance(item.get("text"), str) or not item["text"].strip():
                raise StoreError("goals.md: every goal needs a non-empty text")
            why = item.get("why", "")
            if not isinstance(why, str):
                raise StoreError("goals.md: why must be a string")
            goals.append(NetworkGoal(text=item["text"].strip(), why=why.strip()))
        return tuple(goals)

    # --- what a node sees -------------------------------------------------------------------

    def knowledge_slice(self, serial: int, keys: Sequence[str]) -> KnowledgeSlice:
        """The slice around one step: catalog, goals, records about `keys`, fresh tail, votes."""
        self._require_alive(serial)  # a retired node gets no step slice (C-2)
        for key in keys:
            if not isinstance(key, str) or not ABOUT_KEY.match(key):
                raise StoreError(f"bad visible key {key!r}: want (blk|ent|itm):<id>")
        records = self.records()
        catalog = (
            self._catalog_path.read_text(encoding="utf-8")
            if self._catalog_path.exists()
            else ""
        )
        return build_slice(
            serial=serial,
            keys=tuple(keys),
            records=records,
            catalog=catalog,
            goals=self._read_goals(),
        )

    def inheritance(self, serial: int) -> Inheritance:
        """What a newborn node receives (section 7); hive calls this at birth."""
        self._require_alive(serial)  # the recipient is the newborn, not a retired node (C-2)
        dead: list[tuple[int, Will]] = []
        alive: list[NodeBrief] = []
        for child in sorted(self._nodes_dir.iterdir()):
            if not (child.is_dir() and child.name.isdigit()):
                continue
            node_serial = int(child.name)
            birth_path = child / "birth.json"
            if not birth_path.exists():
                continue
            birth = self._load_birth(node_serial)
            will_path = child / "will.md"
            if will_path.exists():
                try:
                    dead.append(
                        (node_serial, Will.from_markdown(will_path.read_text(encoding="utf-8")))
                    )
                except SchemaError as exc:
                    raise StoreError(
                        f"broken will of {format_serial(node_serial)}: {exc}"
                    ) from exc
            else:
                alive.append(NodeBrief(serial=node_serial, slot=birth.slot))
        terms = [
            (r.claim, r.about[0])
            for r in self.records()
            if r.kind == "term" and r.status != "retired"
        ]
        catalog = (
            self._catalog_path.read_text(encoding="utf-8")
            if self._catalog_path.exists()
            else ""
        )
        return build_inheritance(
            dead=dead, alive=alive, terms=terms, catalog=catalog
        )

    # --- primer ------------------------------------------------------------------------------

    def _load_primer_locked(self, directory: Path) -> None:
        """Load agent primer files into a fresh base as one `primer: load N records` commit."""
        if not directory.is_dir():
            return
        entries: list[PrimerEntry] = []
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise StoreError(f"primer file {path.name}: {exc}") from exc
            if not isinstance(data, list):
                raise StoreError(f"primer file {path.name}: expected a list of entries")
            for raw in data:
                try:
                    entries.append(PrimerEntry.from_dict(raw))
                except SchemaError as exc:
                    raise StoreError(f"primer file {path.name}: {exc}") from exc
        if not entries:
            return
        ts = self._now()
        records = []
        for offset, entry in enumerate(entries):
            record_id = f"k-{self._next_record_number_locked() + offset:04d}"
            records.append(entry.to_record(record_id, ts))
        files = {
            self._records_dir / f"{r.id}.json": _json_text(r.to_dict()) for r in records
        }
        files[self._catalog_path] = self._catalog_text(records)
        self._commit_op(f"primer: load {len(records)} records", files)
