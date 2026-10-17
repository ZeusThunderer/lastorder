"""Lifetime rules of the hive (T-022): deadlines, stuck deaths, lost nodes, restarts.

Fake clock, fake RCON, real store in a tmp directory — the decisions, not the
sockets, are the object here. Review findings P1-2, P1-4, P1-6, P1-9 are pinned
by dedicated tests.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.hive.lifecycle import HiveLog, Lifecycle, LifecycleError
from runtime.hive.rcon import RconClient, RconError, kill_confirmed
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


class FakeRcon:
    """Records kills; can fail at the socket or answer like an offline player."""

    def __init__(self, response: str = "Killed LastOrder01"):
        self.kills: list[str] = []
        self.fail = False
        self.response = response

    def kill(self, player: str) -> str:
        self.kills.append(player)
        if self.fail:
            raise RconError("connection refused")
        return self.response


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


def make(tmp_path: Path, *, rcon=None, clock=None, max_life_min=45):
    clock = clock or FakeClock()
    store = KnowledgeStore(tmp_path / "kb", git=False, now=lambda: clock().isoformat())
    log = HiveLog(tmp_path / "run" / "events.jsonl")
    lifecycle = Lifecycle(
        store,
        rcon if rcon is not None else FakeRcon(),
        log=log,
        run_dir=tmp_path / "run",
        clock=clock,
        max_life_min=max_life_min,
    )
    return store, lifecycle, log, clock


def will_of(store: KnowledgeStore, serial: int) -> Will:
    path = store.root / "nodes" / f"{serial:05d}" / "will.md"
    return Will.from_markdown(path.read_text(encoding="utf-8"))


# --- the deadline ------------------------------------------------------------------------


def test_scan_kills_expired_node_once(tmp_path: Path) -> None:
    rcon = FakeRcon()
    store, life, log, clock = make(tmp_path, rcon=rcon)
    record = life.birth("LastOrder01", traits())
    assert record.ttl_min == 45

    life.scan()  # fresh node: nothing to do
    assert rcon.kills == []

    clock.advance(minutes=44)
    life.scan()
    assert rcon.kills == []

    clock.advance(minutes=2)  # past the deadline
    life.scan()
    life.scan()  # a second scan must not kill twice
    assert rcon.kills == ["LastOrder01"]
    types = [e["type"] for e in log.read()]
    assert "life.expired" in types and "life.killed" in types
    assert types.count("life.killed") == 1


def test_scan_retries_after_rcon_failure(tmp_path: Path) -> None:
    rcon = FakeRcon()
    rcon.fail = True
    store, life, log, clock = make(tmp_path, rcon=rcon)
    life.birth("LastOrder01", traits())
    clock.advance(minutes=46)
    life.scan()
    assert rcon.kills == ["LastOrder01"]  # attempted...
    node = life._nodes[1]
    assert node.kill_confirmed is False  # ...but not confirmed
    types = [e["type"] for e in log.read()]
    assert types.count("life.rcon_failed") == 1

    rcon.fail = False
    life.scan()  # the next pass retries while the node lives
    assert rcon.kills == ["LastOrder01", "LastOrder01"]
    assert life._nodes[1].kill_confirmed is True


def test_offline_player_answer_is_not_a_confirmed_kill(tmp_path: Path) -> None:
    rcon = FakeRcon(response="No entity was found")
    store, life, log, clock = make(tmp_path, rcon=rcon)
    life.birth("LastOrder01", traits())
    clock.advance(minutes=46)
    life.scan()
    assert life._nodes[1].kill_confirmed is False  # P1-6: transport ok, no kill
    # no confirmed ttl kill -> retire keeps the mind's own cause
    life.retire(1, "game", will_text(1, life._nodes[1].born_at))
    assert will_of(store, 1).meta.cause == "game"


def test_kill_confirmed_helper() -> None:
    assert kill_confirmed("Killed LastOrder01")
    assert not kill_confirmed("No entity was found")
    assert not kill_confirmed("")


def test_ttl_kill_corrects_the_cause_at_retire(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path, rcon=FakeRcon())
    life.birth("LastOrder01", traits())
    clock.advance(minutes=46)
    life.scan()
    born = life._nodes[1].born_at
    life.retire(1, "game", will_text(1, born))  # the mind saw a plain game death
    assert will_of(store, 1).meta.cause == "ttl"


# --- stuck (ADR-026) -----------------------------------------------------------------------


def test_stuck_request_kills_immediately_and_corrects_cause(tmp_path: Path) -> None:
    rcon = FakeRcon()
    store, life, log, clock = make(tmp_path, rcon=rcon)
    life.birth("LastOrder01", traits())
    life.request_death(1, "stuck")  # no threshold of hive's own, no scan needed
    assert rcon.kills == ["LastOrder01"]
    life.retire(1, "game", will_text(1, life._nodes[1].born_at))
    assert will_of(store, 1).meta.cause == "stuck"
    assert "life.stuck" in [e["type"] for e in log.read()]


def test_request_death_rejects_other_causes_and_unknown_nodes(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path, rcon=FakeRcon())
    life.birth("LastOrder01", traits())
    with pytest.raises(LifecycleError):  # only "stuck" is the node's to ask (ADR-026)
        life.request_death(1, "game")
    with pytest.raises(LifecycleError):
        life.request_death(1, "bored")
    with pytest.raises(UnknownNodeError):
        life.request_death(99, "stuck")


# --- lost nodes and slot sequencing (P1-2) ----------------------------------------------------


def test_second_birth_in_slot_closes_the_old_node_with_emergency_will(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path, rcon=FakeRcon())
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
    store, life, log, clock = make(tmp_path, rcon=FakeRcon())
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda seed: life.birth("LastOrder01", traits(seed=seed)), (1, 2)))
    serials = sorted(record.serial for record in records)
    living = [brief.serial for brief in life.alive()]
    assert len(living) == 1 and living[0] == serials[1]  # the newest stays (P1-4)
    assert len([e for e in log.read() if e["type"] == "life.lost"]) == 1


def test_startup_reconcile_closes_slot_duplicates(tmp_path: Path) -> None:
    store, life, log, clock = make(tmp_path, rcon=FakeRcon())
    # two living nodes in one slot behind lifecycle's back: only the store knows
    store.birth("LastOrder01", traits(seed=1))
    store.birth("LastOrder01", traits(seed=2))

    _, life2, log2, _ = make(tmp_path, rcon=FakeRcon())  # same run dir, same store
    living = [brief.serial for brief in life2.alive()]
    assert living == [2]  # the newest survived, the older was closed as lost
    assert will_of(store, 1).meta.emergency is True
    assert len([e for e in log2.read() if e["type"] == "life.lost"]) == 1


# --- restarts ---------------------------------------------------------------------------------


def test_state_recovers_after_restart(tmp_path: Path) -> None:
    rcon = FakeRcon()
    store, life, log, clock = make(tmp_path, rcon=rcon)
    life.birth("LastOrder01", traits(seed=1))
    life.birth("LastOrder02", traits(seed=2))
    clock.advance(minutes=46)

    rcon2 = FakeRcon()
    store2, life2, log2, clock2 = make(tmp_path, rcon=rcon2)
    clock2.advance(minutes=46)
    assert [brief.serial for brief in life2.alive()] == [1, 2]
    life2.scan()
    assert sorted(rcon2.kills) == ["LastOrder01", "LastOrder02"]


def test_max_life_min_override(tmp_path: Path) -> None:
    rcon = FakeRcon()
    store, life, log, clock = make(tmp_path, rcon=rcon, max_life_min=5)
    record = life.birth("LastOrder01", traits())
    assert record.ttl_min == 5
    clock.advance(minutes=6)
    life.scan()
    assert rcon.kills == ["LastOrder01"]


# --- secrets (P1-9) -----------------------------------------------------------------------------


def test_rcon_password_stays_out_of_repr_and_events(tmp_path: Path) -> None:
    secret = "secret-rcon-pass"
    client = RconClient(host="127.0.0.1", port=1, password=secret, timeout_s=1.0)
    assert secret not in repr(client)
    store, life, log, clock = make(tmp_path, rcon=client)
    life.birth("LastOrder01", traits())
    clock.advance(minutes=46)
    life.scan()  # the unreachable server turns into an event, not into a leak
    text = json.dumps(log.read(), ensure_ascii=False)
    assert secret not in text and "LastOrder01" in text
