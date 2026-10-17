"""Slices and inheritance assembled from records (KNOWLEDGE.md sections 7 and 9, T-012)."""

from pathlib import Path

from runtime.knowledge import budget
from runtime.knowledge.schema import Record, Will, WillMeta
from runtime.knowledge.slice import (
    build_inheritance,
    build_slice,
    entry_line,
    format_serial,
)
from runtime.lastorder.traits import Traits
from runtime.ports import KnowledgeCandidate, NetworkGoal, NodeBrief, Vote

T0 = "2026-10-17T10:00:00+00:00"


def traits(seed: int = 42) -> Traits:
    return Traits(
        bravery=3,
        greed=7,
        talkativeness=5,
        trust_in_chat_and_nodes=4,
        superstition=9,
        quirk="считает крипера родственником",
        seed=seed,
    )


def record(record_id: str, **over) -> Record:
    number = int(record_id.split("-")[1])
    data = {
        "id": record_id,
        "kind": "fact",
        "claim": f"Запись {record_id} про поведение существ у воды.",
        "about": ["ent:zombie"],
        "source": "node",
        "author": 1,
        "ts": f"2026-10-17T1{number % 10}:00:00+00:00",
        "status": "hypothesis",
        "evidence": [],
        "votes": [],
    }
    data.update(over)
    return Record.from_dict(data)


def a_will(serial: int, died_at: str, body: str = "Урок: не ходить в горы ночью.\nШутка: шумный сосед.") -> Will:
    meta = WillMeta(
        serial=serial,
        slot=f"LastOrder0{serial % 3 + 1:02d}",
        cause="game",
        born_at=T0,
        died_at=died_at,
        lifetime_s=600,
        emergency=False,
        traits=traits(),
        model="deepseek/deepseek-v4.1-flash",
    )
    return Will(meta=meta, body=body)


# --- the step slice -------------------------------------------------------------------------


def test_slice_about_follows_visible_keys():
    zombie = record("k-0001", about=["ent:zombie"])
    creeper = record("k-0002", about=["ent:creeper"], claim="Крипер взрывается вблизи.")
    both = record("k-0003", about=["ent:zombie", "ent:creeper"])
    retired = record("k-0004", about=["ent:zombie"], status="retired")
    primer = record(
        "k-0005", about=["ent:zombie"], source="primer", author="primer", status="confirmed"
    )
    slice_ = build_slice(
        serial=9, keys=["ent:zombie"], records=[zombie, creeper, both, retired, primer],
        catalog="", goals=(),
    )
    ids = [e.id for e in slice_.about]
    assert "k-0001" in ids and "k-0003" in ids
    assert "k-0002" not in ids  # not about the visible key
    assert "k-0004" not in ids  # retired is gone from slices (section 4)
    assert "k-0005" not in ids  # primer never enters a slice (P1-2)


def test_slice_about_caps_per_key():
    records = [
        record(f"k-{i:04d}", about=["ent:zombie"], ts=f"2026-10-17T10:{i:02d}:00+00:00")
        for i in range(1, budget.ABOUT_ENTRIES + 3)
    ]
    slice_ = build_slice(serial=9, keys=["ent:zombie"], records=records, catalog="", goals=())
    assert len(slice_.about) == budget.ABOUT_ENTRIES
    # the two records that did not fit the per-key cap are still news, they ride the tail
    assert [e.id for e in slice_.fresh] == [f"k-{i:04d}" for i in range(2, 0, -1)]


def test_slice_fresh_tail_skips_about_and_primer():
    visible = record("k-0001", about=["ent:zombie"])
    fresh = [
        record(f"k-{i:04d}", about=["ent:creeper"], ts=f"2026-10-17T11:{i:02d}:00+00:00")
        for i in range(2, 9)
    ]
    primer = record(
        "k-0009", about=["itm:bread"], source="primer", author="primer", status="confirmed"
    )
    slice_ = build_slice(
        serial=9, keys=["ent:zombie"], records=[visible, *fresh, primer], catalog="", goals=()
    )
    assert [e.id for e in slice_.fresh] == [f"k-{i:04d}" for i in range(8, 3, -1)][
        : budget.FRESH_ENTRIES
    ]
    assert all(e.id != "k-0009" for e in slice_.fresh)  # primer is not fresh tail either
    assert all(e.id != "k-0001" for e in slice_.fresh)  # already in about


def test_slice_pending_votes_rules():
    mine = record("k-0001", author=9)  # the node's own candidate
    voted = record("k-0002", author=1, votes=[{"node": 9, "accept": True, "why": "", "ts": T0}])
    queue = [
        record(f"k-{i:04d}", author=1, ts=f"2026-10-17T12:{i:02d}:00+00:00")
        for i in range(3, 7)
    ]
    slice_ = build_slice(
        serial=9, keys=[], records=[mine, voted, *queue], catalog="", goals=()
    )
    assert [e.id for e in slice_.pending_votes] == ["k-0006", "k-0005"]  # newest first, <= 2
    assert all(e.id not in ("k-0001", "k-0002") for e in slice_.pending_votes)


def test_slice_pending_votes_hypotheses_before_disputes():
    hypothesis = record("k-0001", author=1, ts="2026-10-17T09:00:00+00:00")
    old_dispute = record("k-0002", author=2, status="disputed", ts="2026-10-17T10:00:00+00:00")
    new_dispute = record("k-0003", author=3, status="disputed", ts="2026-10-17T11:00:00+00:00")
    slice_ = build_slice(
        serial=9, keys=[], records=[new_dispute, old_dispute, hypothesis], catalog="", goals=()
    )
    # Review P1-6: hypotheses first, then disputes settle by votes; two slots total
    assert [e.id for e in slice_.pending_votes] == ["k-0001", "k-0003"]


def test_slice_about_block_fits_budget():
    # every claim alone is ~120 tokens: B_ABOUT cannot hold even the per-key cap
    records = [
        record(f"k-{i:04d}", about=["ent:zombie"], claim="к" * 200, ts=f"2026-10-17T{i:02d}:00:00+00:00")
        for i in range(1, 11)
    ]
    slice_ = build_slice(serial=9, keys=["ent:zombie"], records=records, catalog="", goals=())
    lines = [entry_line(e) for e in slice_.about] + [entry_line(e) for e in slice_.fresh]
    tokens = sum(budget.estimate_tokens(line + "\n") for line in lines)
    assert tokens <= budget.B_ABOUT
    assert slice_.fresh == ()  # the fresh tail is cut before about entries (section 9)
    assert slice_.about  # and something survived the cut


def test_slice_votes_block_fits_budget():
    records = [
        record(f"k-{i:04d}", author=1, claim="д" * 200, ts=f"2026-10-17T1{i}:00:00+00:00")
        for i in range(1, 5)
    ]
    slice_ = build_slice(serial=9, keys=[], records=records, catalog="", goals=())
    tokens = sum(budget.estimate_tokens(entry_line(e) + "\n") for e in slice_.pending_votes)
    assert tokens <= budget.B_VOTES


def test_slice_passes_catalog_and_goals_through():
    goals = (NetworkGoal(text="проверить болото", why="нужна глина"),)
    slice_ = build_slice(
        serial=9, keys=[], records=[], catalog="k-0001 fact ent:zombie\n", goals=goals
    )
    assert slice_.catalog == "k-0001 fact ent:zombie\n"
    assert slice_.network_goals == goals


# --- inheritance ------------------------------------------------------------------------------


def test_inheritance_wills_summaries_and_terms():
    dead = [
        (i, a_will(i, died_at=f"2026-10-18T{i:02d}:00:00+00:00", body=f"Завещание узла {i:05d}.\nШутка."))
        for i in range(1, 13)  # twelve dead: 10 lines plus a counter (section 7)
    ]
    alive = [NodeBrief(serial=13, slot="LastOrder02"), NodeBrief(serial=14, slot="LastOrder03")]
    terms = [(f"шёпот-{i}", "ent:zombie") for i in range(12)]  # short lines: the count cap fires
    inheritance = build_inheritance(
        dead=dead, alive=alive, terms=terms, catalog="k-0001 fact ent:zombie\n"
    )
    lines = inheritance.predecessors_summary.splitlines()
    assert len(lines) == budget.PREDECESSOR_LINES + 1  # ten lines plus the counter
    assert lines[0].startswith(format_serial(12))  # the latest dead first
    assert lines[-1] == "… ещё 2 раньше"
    assert inheritance.active_nodes_summary == "00013 LastOrder02\n00014 LastOrder03"
    # K latest wills, whole bodies, latest first
    assert inheritance.inherited_notes == "Завещание узла 00012.\nШутка.\n\nЗавещание узла 00011.\nШутка.\n\nЗавещание узла 00010.\nШутка."
    assert len(inheritance.collective_terms.splitlines()) == budget.INHERITANCE_TERMS
    assert inheritance.knowledge_catalog == "k-0001 fact ent:zombie\n"


def test_inheritance_budgets_hold():
    dead = [
        (i, a_will(i, died_at=f"2026-10-18T{i:02d}:00:00+00:00", body="у" * 100 + "\nшутка здесь"))
        for i in range(1, 16)
    ]
    terms = [(f"термин номер {i} объясняет имя подлиннее", "ent:creeper") for i in range(20)]
    inheritance = build_inheritance(dead=dead, alive=[], terms=terms, catalog="каталог " * 30)
    summaries = inheritance.predecessors_summary + "\n" + inheritance.active_nodes_summary
    assert budget.estimate_tokens(summaries) <= budget.INHERITANCE_SUMMARY_TOKENS
    assert budget.estimate_tokens(inheritance.collective_terms) <= budget.INHERITANCE_TERMS_TOKENS
    wills = inheritance.inherited_notes.split("\n\n")
    assert len(wills) == budget.INHERITANCE_WILLS
    for will in wills:
        assert budget.estimate_tokens(will) <= budget.WILL_BODY_MAX_TOKENS


def test_inheritance_empty_base():
    inheritance = build_inheritance(dead=[], alive=[], terms=[], catalog="")
    assert inheritance.predecessors_summary == ""
    assert inheritance.inherited_notes == ""
    assert inheritance.collective_terms == ""


# --- the store end to end -----------------------------------------------------------------------


def _store(tmp_path: Path):
    from runtime.knowledge.store import KnowledgeStore

    return KnowledgeStore(tmp_path / "knowledge", git=False)


def test_store_slice_and_inheritance_together(tmp_path: Path):
    store = _store(tmp_path)
    serial_a = store.birth("LastOrder01", traits()).serial
    serial_b = store.birth("LastOrder02", traits(7)).serial
    record = store.submit_candidate(
        serial_a,
        KnowledgeCandidate(
            kind="danger",
            claim="Стонущий прямоходящий бьёт на сближении; держать дистанцию.",
            about=("ent:zombie",),
            evidence_event_ids=("e-1",),
        ),
    )
    store.submit_vote(serial_b, Vote(entry_id=record.id, accept=True, why=""))
    slice_ = store.knowledge_slice(serial_b, ["ent:zombie"])
    assert [e.id for e in slice_.about] == [record.id]
    assert slice_.fresh == ()  # the only record is already in about
    assert slice_.network_goals == ()  # goals.md is empty until T-016 writes it
    assert slice_.catalog == "k-0001 danger ent:zombie\n"

    inheritance = store.inheritance(serial_b)
    assert inheritance.active_nodes_summary == "00001 LastOrder01\n00002 LastOrder02"
    assert inheritance.predecessors_summary == ""  # nobody died yet

    store.retire(serial_a, "game", _will_markdown(serial_a))
    inheritance = store.inheritance(serial_b)
    assert "00001 game" in inheritance.predecessors_summary
    assert "Урок" in inheritance.inherited_notes


def _will_markdown(serial: int) -> str:
    meta = WillMeta(
        serial=serial,
        slot="LastOrder01",
        cause="game",
        born_at="2026-10-17T10:00:00+00:00",
        died_at="2026-10-17T10:30:00+00:00",
        lifetime_s=1800,
        emergency=False,
        traits=traits(),
        model="deepseek/deepseek-v4.1-flash",
    )
    return Will(meta=meta, body="Урок: факелы заготовить днём.\nШутка: жадность не лечится.").to_markdown()


def test_store_goals_read_as_network_goals(tmp_path: Path):
    store = _store(tmp_path)
    serial = store.birth("LastOrder01", traits()).serial
    (store.root / "base" / "goals.md").write_text(
        "- text: проверить болото\n  why: нужна глина\n", encoding="utf-8"
    )
    slice_ = store.knowledge_slice(serial, [])
    assert slice_.network_goals == (NetworkGoal(text="проверить болото", why="нужна глина"),)
