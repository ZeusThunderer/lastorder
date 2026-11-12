"""Lifetime rules of the hive (T-022, ADR-052): deadlines, stuck asks, lost nodes, restarts.

Fake clock, real store in a tmp directory — the decisions, not the sockets, are the
object here. Since ADR-052 the deadline kills nothing: the scan only observes the
expiry, the mind's own life clock ends the life (the soft term), and mark_dead /
mark_term gate the will for whichever way the life ended. Review findings P1-2,
P1-4 stay pinned by dedicated tests.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.hive.lifecycle import HiveLog, Lifecycle, LifecycleError
from runtime.knowledge.schema import Will
from runtime.knowledge.store import AlreadyRetiredError, KnowledgeStore, UnknownNodeError
from runtime.lastorder.traits import Traits

T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float = 0, seconds: float = 0) -> None:
        self.now = self.now + timedelta(minutes=minutes, seconds=seconds)


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


def will_text(serial: int, born_at: str) -> str:
    meta = dict(
        serial=serial,
        slot="LastOrder01",
        cause="game",
        born_at=born_at,
        died_at=(T0 + timedelta(minutes=10)).isoformat(),
        lifetime_s=600,
        emergency=False,
        traits=traits().as_dict(),
        model="test",
    )
    body = "Урок: не ходить в горы ночью.\nШутка: мне досталась жадность, я не просил."
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()] + ["---", "", body]
    return "\n".join(lines)


def make(tmp_path: Path, *, clock=None, max_life_min=45):
    clock = clock or FakeClock()
    store = KnowledgeStore(tmp_path / "kb", git=False, now=lambda: clock().isoformat())
    log = HiveLog(tmp_path / "run" / "events.jsonl")
    lifecycle = Lifecycle(
        store,
        log=log,
        run_dir=tmp_path / "run",
        clock=clock,
        max_life_min=max_life_min,
    )
    return store, lifecycle, log, clock


def will_of(store: KnowledgeStore, serial: int) -> Will:
    path = store.root / "nodes" / f"{serial:05d}" / "will.md"
    return Will.from_markdown(path.read_text(encoding="utf-8"))


# --- the deadline (ADR-052: the scan observes, the mind's clock acts) ------------------------


def test_scan_marks_expired_once_and_kills_nothing(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    record = life.birth("LastOrder01", traits())
    assert record.ttl_min == 45

    life.scan()  # fresh node: nothing to do
    clock.advance(minutes=44)
    life.scan()
    assert not [e for e in log.read() if e["type"] == "life.expired"]

    clock.advance(minutes=2)  # past the deadline
    life.scan()
    life.scan()  # a second scan must not re-mark
    expired = [e for e in log.read() if e["type"] == "life.expired"]
    assert len(expired) == 1 and expired[0]["payload"]["serial"] == record.serial
    # the deadline killed nothing: the node is not over until the term lands
    assert not life.life_over(record.serial)
    life.mark_term(record.serial)  # the runner's soft closing path
    assert life.life_over(record.serial)


def test_ttl_cause_is_the_minds_own_ruling_at_retire(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    life.birth("LastOrder01", traits())
    clock.advance(minutes=46)
    life.scan()  # expired — but no kill to confirm anything
    born = life._nodes[1].born_at
    life.retire(1, "ttl", will_text(1, born))  # the mind ruled ttl by its own clock
    assert will_of(store, 1).meta.cause == "ttl"


def test_max_life_min_override(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path, max_life_min=5)
    record = life.birth("LastOrder01", traits())
    assert record.ttl_min == 5
    clock.advance(minutes=6)
    life.scan()
    assert "life.expired" in [e["type"] for e in log.read()]


# --- the term and dead marks (T-028, ADR-052) -------------------------------------------------


def test_mark_term_gates_the_life_once(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    record = life.birth("LastOrder01", traits())
    life.mark_term(record.serial)
    life.mark_term(record.serial)  # idempotent: retries after a flaky network
    events = [e for e in log.read() if e["type"] == "life.term_mark"]
    assert len(events) == 1 and events[0]["payload"]["serial"] == record.serial
    assert life.life_over(record.serial)


def test_mark_dead_gates_the_life_once(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    record = life.birth("LastOrder01", traits())
    life.mark_dead(record.serial)
    life.mark_dead(record.serial)
    events = [e for e in log.read() if e["type"] == "life.dead_mark"]
    assert len(events) == 1
    assert life.life_over(record.serial)


def test_life_over_refuses_unknown_and_retired(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    assert life.life_over(99) is False  # unknown: the refusal is require_node's job
    record = life.birth("LastOrder01", traits())
    born = life._nodes[record.serial].born_at
    life.retire(record.serial, "game", will_text(record.serial, born))
    assert life.life_over(record.serial) is False  # retired: gone, not closing


# --- stuck (ADR-026, ADR-052: the ask marks, the mind terms itself) ---------------------------


def test_stuck_ask_marks_without_killing_and_corrects_cause(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    life.birth("LastOrder01", traits())
    life.request_death(1, "stuck")  # no threshold of hive's own, no scan needed
    node = life._nodes[1]
    assert node.stuck is True
    assert not node.term and not node.dead  # the ask itself ends nothing
    life.retire(1, "game", will_text(1, life._nodes[1].born_at))  # whatever the mind said
    assert will_of(store, 1).meta.cause == "stuck"
    assert "life.stuck" in [e["type"] for e in log.read()]


def test_request_death_rejects_other_causes_and_unknown_nodes(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    life.birth("LastOrder01", traits())
    with pytest.raises(LifecycleError):  # only "stuck" is the node's to ask (ADR-026)
        life.request_death(1, "game")
    with pytest.raises(LifecycleError):
        life.request_death(1, "bored")
    with pytest.raises(UnknownNodeError):
        life.request_death(99, "stuck")


# --- lost nodes and slot sequencing (P1-2) ----------------------------------------------------


def test_second_birth_in_slot_closes_the_old_node_with_emergency_will(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    first = life.birth("LastOrder01", traits(seed=1))
    second = life.birth("LastOrder01", traits(seed=2))  # the mind restarted, no will came

    assert second.serial > first.serial  # the serial is never reused
    assert [brief.serial for brief in life.alive()] == [second.serial]
    will = will_of(store, first.serial)
    assert will.meta.emergency is True  # no death without a will (CLAUDE.md)
    assert will.meta.cause == "game"  # `lost` rides the event, not the store's CAUSES
    events = [e for e in log.read() if e["type"] == "life.lost"]
    assert len(events) == 1 and events[0]["payload"]["serial"] == first.serial
    with pytest.raises(AlreadyRetiredError):  # a lost node writes nothing anymore
        store.store_core_memory(first.serial, "leftover")
    with pytest.raises(AlreadyRetiredError):
        life.request_death(first.serial, "stuck")


def test_parallel_births_into_one_slot_leave_one_living_node(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda seed: life.birth("LastOrder01", traits(seed=seed)), (1, 2)))
    serials = sorted(record.serial for record in records)
    living = [brief.serial for brief in life.alive()]
    assert len(living) == 1 and living[0] == serials[1]  # the newest stays (P1-4)
    assert len([e for e in log.read() if e["type"] == "life.lost"]) == 1


def test_failed_lost_close_is_retried_by_the_next_birth(tmp_path: Path) -> None:
    from runtime.knowledge.store import StoreError

    store, life, log, clock = make(tmp_path)
    first = life.birth("LastOrder01", traits(seed=1))

    real_retire = store.retire
    store.retire = lambda *a, **k: (_ for _ in ()).throw(StoreError("git failed"))
    with pytest.raises(StoreError):  # the emergency will did not land
        life.birth("LastOrder01", traits(seed=2))
    assert life._nodes[first.serial].lost  # marked, still waiting to be closed
    with pytest.raises(UnknownNodeError):  # and no longer a participant (R1-8)
        life.request_death(first.serial, "stuck")
    with pytest.raises(UnknownNodeError):
        life.require_node(first.serial)

    store.retire = real_retire
    second = life.birth("LastOrder01", traits(seed=3))  # retries the close
    assert [b.serial for b in life.alive()] == [second.serial]
    assert will_of(store, first.serial).meta.emergency is True
    assert len([e for e in log.read() if e["type"] == "life.lost"]) == 1  # said once


def test_startup_reconcile_closes_slot_duplicates(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    # two living nodes in one slot behind lifecycle's back: only the store knows
    store.birth("LastOrder01", traits(seed=1))
    store.birth("LastOrder01", traits(seed=2))

    _, life2, log2, _ = make(tmp_path)  # same run dir, same store
    living = [brief.serial for brief in life2.alive()]
    assert living == [2]  # the newest survived, the older was closed as lost
    assert will_of(store, 1).meta.emergency is True
    assert len([e for e in log2.read() if e["type"] == "life.lost"]) == 1


# --- restarts ---------------------------------------------------------------------------------


def test_state_recovers_after_restart(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    life.birth("LastOrder01", traits(seed=1))
    life.birth("LastOrder02", traits(seed=2))
    clock.advance(minutes=46)
    life.mark_term(1)  # slot 1 already closed its soft term before the restart

    _, life2, log2, clock2 = make(tmp_path)  # same run dir, same store
    clock2.advance(minutes=46)
    assert [brief.serial for brief in life2.alive()] == [1, 2]
    assert life2.life_over(1)  # the term mark survived the restart
    life2.scan()
    expired = {e["payload"]["serial"] for e in log2.read() if e["type"] == "life.expired"}
    assert expired == {1, 2}  # both past the deadline, both just observed
    assert not life2.life_over(2)  # and neither was killed by the scan


def test_pre_adr052_state_file_with_kill_fields_loads(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path)
    record = life.birth("LastOrder01", traits())
    state_path = tmp_path / "run" / "lifecycle.json"
    doc = json.loads(state_path.read_text(encoding="utf-8"))
    # a state file written before ADR-052 carries kill_* fields nobody reads now
    doc["nodes"][0]["kill_reason"] = "ttl"
    doc["nodes"][0]["kill_confirmed"] = False
    doc["nodes"][0]["kill_error"] = "error: connection refused"
    state_path.write_text(json.dumps(doc), encoding="utf-8")

    _, life2, _, _ = make(tmp_path)  # same run dir: the startup reconcile loads it
    assert [b.serial for b in life2.alive()] == [record.serial]
    life2.mark_term(record.serial)  # the node is a full participant, not a crash
    assert life2.life_over(record.serial)
