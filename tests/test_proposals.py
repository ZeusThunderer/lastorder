"""The proposal queue and hive's common layers (T-028) — unit level, no HTTP.

Everything runs against a real KnowledgeStore in a tmp directory (git off unless a test
needs the commit messages). The MCP wrapping, the identity rules and the two-client
acceptance run live in tests/test_hive_mcp.py; here it is the queue's own contract:
the three proposal kinds, the duplicate-as-evidence rule (dedup.py), the decisions and
their git trail, the diff guards (the personality core among them), goals and the
chronicle.
"""

from __future__ import annotations

import difflib
import json
import subprocess
from pathlib import Path

import pytest

from runtime.hive.proposals import (
    CommonLayers,
    ProposalBook,
    ProposalError,
    apply_unified_diff,
    parse_unified_diff,
)
from runtime.knowledge.store import KnowledgeStore, StoreError
from runtime.lastorder.traits import Traits


def traits(seed: int) -> Traits:
    return Traits(
        bravery=3,
        greed=7,
        talkativeness=5,
        trust_in_chat_and_nodes=4,
        superstition=9,
        quirk="считает крипера родственником",
        seed=seed,
    )


class Book:
    """A store, two born sisters and the book under test."""

    def __init__(self, tmp_path: Path, *, git: bool = False):
        self.store = KnowledgeStore(tmp_path / "kb", git=git)
        self.book = ProposalBook(self.store)
        self.layers = CommonLayers(self.store)
        self.first = self.store.birth("LastOrder01", traits(11)).serial
        self.second = self.store.birth("LastOrder02", traits(22)).serial


@pytest.fixture()
def book(tmp_path: Path) -> Book:
    return Book(tmp_path)


@pytest.fixture()
def git_book(tmp_path: Path) -> Book:
    return Book(tmp_path, git=True)


def knowledge_payload(claim: str = "Крипер взрывается у самого бока.", about: list | None = None):
    return {
        "kind": "danger",
        "claim": claim,
        "about": about if about is not None else ["ent:creeper"],
        "evidence_event_ids": ["e-1"],
    }


def diff_of(old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/base/instructions.md",
            tofile="b/base/instructions.md",
        )
    )


INSTRUCTIONS = "Свет дороже гриба.\nФакелы заготовить днём.\n"
INSTRUCTIONS_NEW = "Свет дороже гриба.\nФакелы заготовить до заката.\nУкрытие до ночи.\n"


# --- knowledge proposals --------------------------------------------------------------------


def test_knowledge_propose_queues_and_records(book: Book) -> None:
    before = len(book.store.records())
    proposal = book.book.submit(book.first, "knowledge", knowledge_payload())
    assert proposal.status == "pending"
    assert proposal.record_id and proposal.record_id.startswith("k-")
    record = book.store.record(proposal.record_id)
    assert record.status == "hypothesis"
    assert record.author == book.first
    assert record.claim == "Крипер взрывается у самого бока."
    assert [p.id for p in book.book.pending()] == [proposal.id]
    assert len(book.store.records()) == before + 1


def test_duplicate_knowledge_is_evidence_not_a_record(book: Book) -> None:
    first = book.book.submit(book.first, "knowledge", knowledge_payload())
    before = len(book.store.records())
    second = book.book.submit(book.second, "knowledge", knowledge_payload())
    assert second.record_id == first.record_id  # the duplicate rule of section 4
    record = book.store.record(first.record_id)
    assert {e.node for e in record.evidence} == {book.first, book.second}
    assert len(book.store.records()) == before  # no new record was created
    assert [p.id for p in book.book.pending()] == [first.id, second.id]


def test_knowledge_propose_rejects_bad_payload(book: Book) -> None:
    with pytest.raises(ProposalError, match="not in"):
        book.book.submit(book.first, "knowledge", {"kind": "warning", "claim": "Опасно!"})
    with pytest.raises(ProposalError, match="claim"):
        book.book.submit(book.first, "knowledge", {"kind": "fact", "claim": " "})
    with pytest.raises(ProposalError, match="unknown fields"):
        book.book.submit(book.first, "knowledge", {"kind": "fact", "claim": "x", "why": "y"})
    with pytest.raises(ProposalError, match="skill"):
        book.book.submit(book.first, "knowledge", {"kind": "skill", "claim": "x"})
    with pytest.raises(StoreError):  # the store's own validation (about keys, raw ids)
        book.book.submit(book.first, "knowledge", {"kind": "fact", "claim": "Видел minecraft:creeper"})
    assert book.book.pending() == []


def test_decide_accept_confirms_the_record(book: Book) -> None:
    proposal = book.book.submit(book.first, "knowledge", knowledge_payload())
    updated, note = book.book.decide(proposal.id, accept=True, why="проверил сам")
    assert note == ""
    assert updated.status == "accepted"
    assert updated.decision == {
        "by": "last_order",
        "accept": True,
        "why": "проверил сам",
        "ts": updated.decision["ts"],
    }
    record = book.store.record(proposal.record_id)
    assert record.status == "confirmed"
    assert record.decision.by == "last_order"
    assert record.decision.why == "проверил сам"
    assert book.book.pending() == []
    with pytest.raises(ProposalError, match="already accepted"):
        book.book.decide(proposal.id, accept=False, why="передумал")


def test_decide_reject_retires_the_record(book: Book) -> None:
    proposal = book.book.submit(book.first, "knowledge", knowledge_payload())
    updated, _ = book.book.decide(proposal.id, accept=False, why="не подтвердилось")
    assert updated.status == "rejected"
    record = book.store.record(proposal.record_id)
    assert record.status == "retired"
    assert record.decision.accept is False


def test_decide_requires_a_reason(book: Book) -> None:
    proposal = book.book.submit(book.first, "knowledge", knowledge_payload())
    with pytest.raises(ProposalError, match="why"):
        book.book.decide(proposal.id, accept=True, why="  ")


def test_decide_closes_without_touching_retired_records(book: Book) -> None:
    proposal = book.book.submit(book.first, "knowledge", knowledge_payload())
    # Two refuters with reasons retire the record before Last Order rules (section 4).
    from runtime.ports import Vote

    book.store.submit_vote(book.second, Vote(entry_id=proposal.record_id, accept=False, why="вижу иное"))
    other = book.store.birth("LastOrder03", traits(33)).serial
    book.store.submit_vote(other, Vote(entry_id=proposal.record_id, accept=False, why="тоже"))
    assert book.store.record(proposal.record_id).status == "retired"
    updated, note = book.book.decide(proposal.id, accept=True, why="поздно")
    assert updated.status == "accepted"
    assert "retired by votes" in note
    assert book.store.record(proposal.record_id).status == "retired"  # terminal stays terminal


def test_decide_commit_message_names_decider_reason_proposer(git_book: Book) -> None:
    book = git_book
    proposal = book.book.submit(book.first, "knowledge", knowledge_payload())
    book.book.decide(proposal.id, accept=True, why="проверено дважды")
    log = subprocess.run(
        ["git", "-C", str(book.store.root), "log", "--format=%s"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.splitlines()
    assert any(
        f"decide {proposal.id} accept by last_order: проверено дважды; "
        f"proposer {book.first:05d}" in line
        for line in log
    ), log


# --- skill proposals ------------------------------------------------------------------------


def test_skill_propose_and_accept(book: Book) -> None:
    before = len(book.store.records())
    proposal = book.book.submit(
        book.first,
        "skill",
        {"description": "Мост через ущелье", "script": "#!/bin/sh\nffmpeg -i bridge"},
    )
    assert proposal.record_id is None  # nothing enters the base before the decision
    assert proposal.payload == {"description": "Мост через ущелье", "script": "#!/bin/sh\nffmpeg -i bridge"}
    updated, _ = book.book.decide(proposal.id, accept=True, why="годится")
    record = book.store.record(updated.record_id)
    assert record.kind == "skill"
    assert record.status == "confirmed"
    assert record.claim == "Мост через ущелье"
    assert record.author == book.first
    assert record.decision.accept is True
    script = (book.store.root / "base" / "skills" / record.id).read_text(encoding="utf-8")
    assert "bridge" in script
    assert len(book.store.records()) == before + 1


def test_skill_reject_writes_nothing(book: Book) -> None:
    before = len(book.store.records())
    proposal = book.book.submit(book.first, "skill", {"description": "x", "script": "y"})
    updated, _ = book.book.decide(proposal.id, accept=False, why="не нужно")
    assert updated.status == "rejected" and updated.record_id is None
    assert len(book.store.records()) == before
    assert not (book.store.root / "base" / "skills").exists()


def test_skill_payload_guards(book: Book) -> None:
    with pytest.raises(ProposalError, match="unknown fields"):
        book.book.submit(book.first, "skill", {"description": "x", "script": "y", "name": "z"})
    with pytest.raises(ProposalError, match="description"):
        book.book.submit(book.first, "skill", {"description": " " * 5, "script": "y"})
    with pytest.raises(ProposalError, match="claim"):  # 280 is the record's claim limit
        book.book.submit(book.first, "skill", {"description": "x" * 281, "script": "y"})
    with pytest.raises(ProposalError, match="script"):
        book.book.submit(book.first, "skill", {"description": "x", "script": " "})
    with pytest.raises(ProposalError, match="raw game ids"):
        book.book.submit(
            book.first, "skill", {"description": "x", "script": "select minecraft:creeper"}
        )


# --- instructions proposals ------------------------------------------------------------------


def test_instructions_propose_and_apply(book: Book) -> None:
    book.store.commit_layer("seed", {"base/instructions.md": INSTRUCTIONS})
    diff = diff_of(INSTRUCTIONS, INSTRUCTIONS_NEW)
    proposal = book.book.submit(book.first, "instructions", {"diff": diff})
    updated, _ = book.book.decide(proposal.id, accept=True, why="факелы важнее")
    assert updated.status == "accepted"
    text = (book.store.root / "base" / "instructions.md").read_text(encoding="utf-8")
    assert "до заката" in text and "Укрытие до ночи." in text
    assert "Свет дороже гриба." in text  # untouched context survives


def test_instructions_reject_keeps_the_file(book: Book) -> None:
    book.store.commit_layer("seed", {"base/instructions.md": INSTRUCTIONS})
    proposal = book.book.submit(book.first, "instructions", {"diff": diff_of(INSTRUCTIONS, INSTRUCTIONS_NEW)})
    book.book.decide(proposal.id, accept=False, why="спорно")
    assert (book.store.root / "base" / "instructions.md").read_text(encoding="utf-8") == INSTRUCTIONS


@pytest.mark.parametrize(
    "diff,match",
    [
        (
            "--- a/agent/last_order/prompt_template.md\n+++ b/agent/last_order/prompt_template.md\n"
            "@@ -1 +1 @@\n-старое\n+новое\n",
            "core is operator-only",
        ),
        (
            "--- a/base/goals.md\n+++ b/base/goals.md\n@@ -1 +1 @@\n-x\n+y\n",
            "only base/instructions.md",
        ),
        ("@@ -1 +1 @@\n-один\n+два\n\\ No newline at end of file\n", "cannot parse"),
        ("--- a/base/instructions.md\n+++ b/base/instructions.md\n", "no @@ hunks"),
        ("просто текст без хунков\n", "content before"),
        (
            "--- a/base/instructions.md\n+++ b/base/instructions.md\n@@ -1 +1 @@\n-старое\n+новое\n"
            "--- a/base/goals.md\n+++ b/base/goals.md\n@@ -1 +1 @@\n-x\n+y\n",
            "multi-file diffs are not accepted",
        ),
        ("", "empty"),
    ],
)
def test_instructions_diff_refusals(book: Book, diff: str, match: str) -> None:
    with pytest.raises(ProposalError, match=match):
        book.book.submit(book.first, "instructions", {"diff": diff})


def test_instructions_accept_fails_cleanly_on_drift(book: Book) -> None:
    book.store.commit_layer("seed", {"base/instructions.md": INSTRUCTIONS})
    proposal = book.book.submit(book.first, "instructions", {"diff": diff_of(INSTRUCTIONS, INSTRUCTIONS_NEW)})
    # The instructions change under the proposal's feet: the context no longer matches.
    book.store.commit_layer("drift", {"base/instructions.md": "Всё иначе.\n"})
    with pytest.raises(ProposalError, match="does not match"):
        book.book.decide(proposal.id, accept=True, why="поздно")
    assert [p.id for p in book.book.pending()] == [proposal.id]  # stays pending
    assert (book.store.root / "base" / "instructions.md").read_text(encoding="utf-8") == "Всё иначе.\n"


def test_unified_diff_roundtrip_on_creation() -> None:
    diff = "".join(
        difflib.unified_diff(
            [],
            ["первая строка\n", "вторая\n"],
            fromfile="a/base/instructions.md",
            tofile="b/base/instructions.md",
        )
    )
    hunks = parse_unified_diff(diff)
    assert apply_unified_diff("", hunks) == "первая строка\nвторая\n"


def test_header_looking_content_inside_a_hunk_stays_content() -> None:
    # A removed line that itself starts with "--" renders as "--- x" inside the hunk;
    # GNU patch reads that as content, and so does this parser (checker C-3 keeps
    # the refusal for real header PAIRS only).
    diff = (
        "--- a/base/instructions.md\n+++ b/base/instructions.md\n"
        "@@ -1,2 +1 @@\n строка\n--- два дефиса\n"
    )
    hunks = parse_unified_diff(diff)
    assert hunks[0].old_lines == ("строка", "-- два дефиса")


# --- the store's new surface ----------------------------------------------------------------


def test_commit_layer_refuses_paths_outside_base(book: Book) -> None:
    with pytest.raises(StoreError, match="outside base"):
        book.store.commit_layer("x", {"base/../nodes/00001/birth.json": "oops"})
    with pytest.raises(StoreError, match="outside base"):
        book.store.commit_layer("x", {Path(book.store.root / "nodes" / "00001" / "birth.json"): "oops"})
    with pytest.raises(StoreError, match="outside base"):
        book.store.commit_layer("x", {"nodes/00001/birth.json": "oops"})
    with pytest.raises(StoreError, match="message"):
        book.store.commit_layer("  ", {"base/chronicle.md": "x"})


def test_apply_decision_refusals(tmp_path: Path, monkeypatch) -> None:
    # A primer record: loaded from a PRIMER_DIR the way T-010 will ship it.
    primer_dir = tmp_path / "primer"
    primer_dir.mkdir()
    (primer_dir / "basics.json").write_text(
        json.dumps(
            [
                {
                    "kind": "fact",
                    "claim": "Дерево рубится топором.",
                    "about": ["itm:wooden_axe"],
                    "extended": False,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PRIMER_DIR", str(primer_dir))
    primed = Book(tmp_path / "primed")
    primer = next(r for r in primed.store.records() if r.source == "primer")
    with pytest.raises(StoreError, match="primer"):
        primed.store.apply_decision(primer.id, by="last_order", accept=True, why="x")
    own = primed.book.submit(primed.first, "knowledge", knowledge_payload())
    with pytest.raises(StoreError, match="unknown record"):
        primed.store.apply_decision("k-9999", by="last_order", accept=True, why="x")
    primed.store.apply_decision(own.record_id, by="last_order", accept=True, why="раз")
    with pytest.raises(StoreError, match="already decided"):
        primed.store.apply_decision(own.record_id, by="last_order", accept=False, why="два")
    with pytest.raises(StoreError, match="why is required"):
        primed.store.apply_decision(own.record_id, by="last_order", accept=True, why="")


# --- goals and the chronicle ------------------------------------------------------------------


def test_set_goals_writes_the_slice_format(book: Book) -> None:
    book.layers.set_goals([{"text": "Найти железо", "why": "лестница инструментов"}])
    slice_ = book.store.knowledge_slice(book.first, ())
    assert [g.text for g in slice_.network_goals] == ["Найти железо"]
    assert [g.why for g in slice_.network_goals] == ["лестница инструментов"]


def test_set_goals_limits(book: Book) -> None:
    with pytest.raises(ProposalError, match="1-3"):
        book.layers.set_goals([])
    with pytest.raises(ProposalError, match="1-3"):
        book.layers.set_goals([{"text": f"цель {i}", "why": ""} for i in range(4)])
    with pytest.raises(ProposalError, match="characters"):
        book.layers.set_goals([{"text": "x" * 121, "why": ""}])
    with pytest.raises(ProposalError, match="expected a list"):
        book.layers.set_goals("нет")


def test_chronicle_appends_one_commit_per_entry(book: Book) -> None:
    book.layers.append_chronicle("Первая запись летописи")
    book.layers.append_chronicle("Вторая запись")
    text = (book.store.root / "base" / "chronicle.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 2
    assert "Первая запись летописи" in lines[0] and "Вторая запись" in lines[1]
    with pytest.raises(ProposalError, match="non-empty"):
        book.layers.append_chronicle("  ")
