"""C1, the executor contour (T-025, NETWORK.md sections 2 and 5).

Every test drives `_tick()` by hand with a fake clock and a fake body — no sleeping on
wall time (one test runs the real loop thread briefly). Covers the acceptance items: the
intent lifecycle (done/failed, the 1 s arbitration), the no-LLM threat rules and the
return to the intent after calm, the death path (stop, runner-only respawn, no second
death), the idle watchdog with retire_stuck, life.expiring, the event queue
collapse/limit, and the log.jsonl format.
"""

from __future__ import annotations

import dataclasses
import json
import math
import time as _time
from datetime import datetime, timedelta, timezone

import pytest

from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.mind.executor import (
    DEAD_ACT_ERROR,
    LIFE_WARN_S,
    QUEUE_LIMIT,
    EventQueue,
    Executor,
    Intent,
    JsonlLog,
)
from runtime.ports import BirthRecord, BlockInfo, EntityInfo, Event, ItemStack


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeWall:
    def __init__(self, base: datetime | None = None):
        self.now = base or datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += timedelta(seconds=dt)


@dataclasses.dataclass(frozen=True)
class SimpleState:
    in_game: bool = True
    hp: float = 20.0
    food: float = 20.0
    pos: tuple = (0.0, 64.0, 0.0)
    held: str = "minecraft:wooden_axe"
    looking_at: str = "minecraft:zombie"
    max_hp: float = 20.0
    dimension: str = "overworld"
    on_ground: bool = True

    @property
    def dead(self) -> bool:
        return self.in_game and self.hp <= 0.0


class FakeHive:
    def __init__(self, fail_with: Exception | None = None):
        self.requests: list[tuple[int, str]] = []
        self.fail_with = fail_with

    def request_death(self, serial: int, cause: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.requests.append((serial, cause))


class FakeBody:
    """The Py4jBody surface, scripted per test (busy flag, hp, pos, hostiles)."""

    def __init__(self) -> None:
        self._state = SimpleState()
        self.chat: list[str] = []
        self.acts: list[tuple[str, dict]] = []
        self.busy_flag = False
        self.stops = 0
        self.respawn_calls = 0
        self.respawn_fail_times = 0
        self._events: list[Event] = []
        self._seq = 0
        self.hostiles: list[EntityInfo] = []
        self.inv: list[ItemStack] = []

    def state(self) -> SimpleState:
        return self._state

    def set_state(self, **changes) -> None:
        self._state = dataclasses.replace(self._state, **changes)

    def push_event(self, type_: str, text: str = "", service: bool = False) -> None:
        self._seq += 1
        self._events.append(
            Event(
                id=f"{type_}#{self._seq}",
                ts_mono=0.0,
                ts_wall="",
                source="body",
                type=type_,
                payload={"text": text, "service": service},
            )
        )

    def events(self):
        return tuple(self._events)

    def entities(self, radius: int = 16):
        return tuple(self.hostiles) + (
            EntityInfo(kind="animal", name="Cow", dist_m=8.0, pos=(5, 64, 5)),
        )

    def blocks_around(self, radius: int = 4):
        return (BlockInfo(name="minecraft:grass_block", pos=(0, 63, 0)),)

    def inventory(self):
        return tuple(self.inv)

    def recent_chat(self, lines: int = 5):
        return tuple(self.chat[-lines:])

    def act(self, lever: str, args: dict | None = None) -> None:
        self.acts.append((lever, dict(args or {})))

    def busy(self) -> bool:
        return self.busy_flag

    def stop(self) -> None:
        self.stops += 1
        self.busy_flag = False
        self.acts.append(("__stop__", {}))

    def respawn(self, timeout: float = 60.0, poll: float = 1.0) -> None:
        self.respawn_calls += 1
        if self.respawn_fail_times > 0:
            self.respawn_fail_times -= 1
            raise RuntimeError(f"scripted respawn failure #{self.respawn_calls}")
        self.set_state(hp=20.0)


class Rig:
    """An Executor with fake clocks and a fake body; `tick(dt)` advances both clocks."""

    def __init__(self, body: FakeBody | None = None, log_file: str | None = None, **kwargs):
        self.body = body or FakeBody()
        self.clock = FakeClock()
        self.wall = FakeWall()
        self.log = JsonlLog(log_file)
        defaults = dict(username="LastOrder01", idle_s=5.0)
        defaults.update(kwargs)
        self.x = Executor(
            self.body,
            clock=self.clock,
            wall_clock=self.wall,
            sleep=lambda _s: None,
            log_=self.log,
            **defaults,
        )

    def tick(self, dt: float = 0.0) -> None:
        self.clock.advance(dt)
        self.wall.advance(dt)
        with self.x.body_lock:
            self.x._tick()

    def events_of(self, type_: str) -> list[dict]:
        return [e for e in self.x.events.pending() if e["type"] == type_]

    def close(self) -> None:
        self.log.close()


def rig_file(tmp_path) -> tuple[Rig, str]:
    path = str(tmp_path / "log.jsonl")
    return Rig(log_file=path), path


# --- intents -------------------------------------------------------------------------------


def test_goto_intent_done_on_arrival() -> None:
    rig = Rig()
    try:
        intent = rig.x.submit("goto", {"x": 5, "y": 64, "z": 5})
        rig.tick()  # applies the lever
        assert intent.state == Intent.ACTIVE
        assert rig.body.acts[-1] == ("goto", {"x": 5, "y": 64, "z": 5})
        rig.body.busy_flag = True
        rig.tick()
        assert intent.state == Intent.ACTIVE  # the chain is running
        rig.body.busy_flag = False
        rig.body.set_state(pos=(5.0, 64.0, 5.4))
        rig.tick()
        assert intent.state == Intent.DONE
        assert rig.events_of("intent.done")[0]["intent_id"] == intent.id
    finally:
        rig.close()


def test_goto_intent_failed_when_target_not_reached() -> None:
    rig = Rig()
    try:
        intent = rig.x.submit("goto", {"x": 50, "y": 64, "z": 50})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()
        rig.body.busy_flag = False  # the chain ended somewhere else
        rig.body.set_state(pos=(10.0, 64.0, 10.0))
        rig.tick()
        assert intent.state == Intent.FAILED
        failed = rig.events_of("intent.failed")[0]
        assert failed["intent_id"] == intent.id
        assert "target not reached" in failed["reason"]
    finally:
        rig.close()


def test_instant_lever_settles_done_without_busy() -> None:
    rig = Rig()
    try:
        intent = rig.x.submit("look_at", {"x": 1.0, "y": 64.0, "z": 2.0})
        rig.tick()
        assert intent.state == Intent.ACTIVE
        assert rig.body.busy_flag is False
        rig.tick(dt=1.5)  # past INSTANT_GRACE_S with busy never True: settled
        assert intent.state == Intent.DONE
        assert rig.events_of("intent.done")
    finally:
        rig.close()


def test_get_intent_verdict_by_inventory() -> None:
    rig = Rig()
    try:
        rig.body.inv = [ItemStack(name="minecraft:oak_log", count=8, slot=0)]
        ok = rig.x.submit("get", {"item": "oak_log", "count": 8})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()
        rig.body.busy_flag = False
        rig.tick()
        assert ok.state == Intent.DONE

        rig.body.inv = [ItemStack(name="minecraft:oak_log", count=2, slot=0)]
        short = rig.x.submit("get", {"item": "oak_log", "count": 8})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()
        rig.body.busy_flag = False
        rig.tick()
        assert short.state == Intent.FAILED
        assert "collected 2" in rig.events_of("intent.failed")[-1]["reason"]
    finally:
        rig.close()


def test_new_intent_waits_out_chain_then_stops() -> None:
    rig = Rig()
    try:
        rig.body.busy_flag = True  # a uc chain is running
        intent = rig.x.submit("goto", {"x": 1, "y": 64, "z": 1})
        rig.tick(dt=0.5)
        assert intent.state == Intent.PENDING  # inside the 1 s arbitration window
        assert rig.body.stops == 0
        rig.tick(dt=0.6)  # past the window: stopPathing, then the new lever applies
        assert rig.body.stops == 1
        assert intent.state == Intent.ACTIVE
        assert ("goto", {"x": 1, "y": 64, "z": 1}) in rig.body.acts
    finally:
        rig.close()


def test_new_intent_replaces_old_without_verdict() -> None:
    rig = Rig()
    try:
        first = rig.x.submit("goto", {"x": 5, "y": 64, "z": 5})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()
        second = rig.x.submit("goto", {"x": 9, "y": 64, "z": 9})
        assert first.state == Intent.CLEARED  # replaced, no done/failed verdict
        rig.body.busy_flag = False
        rig.tick()
        assert second.state == Intent.ACTIVE
        assert not rig.events_of("intent.failed") and not rig.events_of("intent.done")
    finally:
        rig.close()


# --- threat rules (no LLM; Jev plugs in at T-031) ------------------------------------------


def test_flee_under_low_hp_then_return_to_intent() -> None:
    rig = Rig()
    try:
        intent = rig.x.submit("goto", {"x": 20, "y": 64, "z": 20})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()

        # damage: hp is critical, a hostile 2 m away -> the chain yields, a flee goto runs
        rig.body.set_state(hp=3.0)
        rig.body.hostiles = [
            EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.0, pos=(2, 64, 0))
        ]
        rig.body.push_event("game.damage", "zombie hit")
        rig.tick()
        assert intent.state == Intent.SUSPENDED
        gotos = [a for a in rig.body.acts if a[0] == "goto" and a[1] != {"x": 20, "y": 64, "z": 20}]
        assert gotos, "a flee goto away from the hostile was issued"
        away = gotos[-1][1]
        assert math.dist((away["x"], away["y"], away["z"]), (0, 64, 0)) >= 10.0
        assert not rig.events_of("intent.done") and not rig.events_of("intent.failed")

        # a later tick with the same picture does not spam the same flee goto
        rig.tick()
        rig.tick()
        assert rig.body.acts[-1][0] == "goto" and rig.body.acts[-1][1] == away

        # the hostile moved: the next free tick re-aims the flee at a new target
        rig.body.hostiles = [
            EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.0, pos=(0, 64, 4))
        ]
        rig.tick()
        reaim = [a for a in rig.body.acts if a[0] == "goto" and a[1] != {"x": 20, "y": 64, "z": 20}]
        assert reaim[-1][1] != away

        # calm: no damage for threat_calm_s -> the agent's intent is applied again
        rig.tick(dt=7.0)
        assert intent.state == Intent.ACTIVE
        assert ("goto", {"x": 20, "y": 64, "z": 20}) == rig.body.acts[-1]

        # and it settles exactly once, without a phantom verdict from the maneuver
        rig.body.busy_flag = True
        rig.tick()
        rig.body.busy_flag = False
        rig.body.set_state(pos=(20.0, 64.0, 20.0))
        rig.tick()
        assert intent.state == Intent.DONE
        assert len(rig.events_of("intent.done")) == 1
        assert not rig.events_of("intent.failed")
    finally:
        rig.close()


def test_fight_when_hostile_in_reach() -> None:
    rig = Rig()
    try:
        rig.body.set_state(hp=15.0)
        rig.body.hostiles = [
            EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.5, pos=(3, 64, 1))
        ]
        rig.body.push_event("game.damage", "zombie hit")
        rig.tick()
        strikes = [a for a in rig.body.acts if a[0] == "strike"]
        assert strikes and strikes[0][1]["pos"] == [3.0, 64.0, 1.0]
    finally:
        rig.close()


def test_damage_event_text_and_source_are_plain() -> None:
    # review P1-4: raw ids and service lines never reach the agent through events
    rig = Rig()
    try:
        rig.body.hostiles = [
            EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.0, pos=(2, 64, 0))
        ]
        rig.body.push_event("game.damage", "§cукусил minecraft:zombie")
        rig.body.push_event("chat.line", "§7[Alto Clef]§r finished @get 1")
        rig.tick()
        damage = rig.events_of("game.damage")[0]
        assert damage["text"] == "укусил zombie"
        assert damage["source"] == "ent:zombie zombie"
        assert "minecraft:" not in json.dumps(rig.x.events.pending(), ensure_ascii=False)
        assert not rig.events_of("chat.line")  # the uc service line was dropped
    finally:
        rig.close()


# --- death ----------------------------------------------------------------------------------


def test_death_stops_body_and_only_runner_respawns() -> None:
    rig = Rig()
    try:
        intent = rig.x.submit("goto", {"x": 20, "y": 64, "z": 20})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()

        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 was slain by Zombie"]
        rig.tick()

        assert rig.x.dead()
        assert rig.body.stops == 1  # the body is stopped on death
        assert intent.state == Intent.CLEARED  # no intent.failed on death (review P1-2)
        death = rig.events_of("game.death")[0]
        assert death["cause"] == "game"
        assert "slain by Zombie" in death["text"]

        rig.tick()
        rig.tick(dt=30.0)
        assert len(rig.events_of("game.death")) == 1  # no second death from one corpse
        assert rig.body.respawn_calls == 0  # never respawns on its own
        with pytest.raises(RuntimeError, match=DEAD_ACT_ERROR):
            rig.x.submit("look_at", {"x": 1.0, "y": 64.0, "z": 1.0})
        assert not rig.events_of("task.idle")  # the watchdog is silent while dead (P1-2)

        assert rig.x.runner_respawn() == {"respawned": True}
        assert rig.body.respawn_calls == 1
        assert not rig.x.dead()
        assert rig.x.death_status()["alive"] is True
    finally:
        rig.close()


def test_death_cause_ttl_by_life_clock() -> None:
    rig = Rig(birth=BirthRecord(serial=7, born_at="2026-10-18T11:10:00+00:00", ttl_min=45))
    try:
        rig.body.set_state(hp=0.0)  # wall now is 12:00, the deadline was 11:55
        rig.body.chat = ["LastOrder01 was killed"]
        rig.tick()
        assert rig.x.death_status()["cause"] == "ttl"
    finally:
        rig.close()


def test_death_status_shape_and_respawn_refusal() -> None:
    rig = Rig()
    try:
        with pytest.raises(RuntimeError, match="not dead"):
            rig.x.runner_respawn()
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 fell from a high place"]
        rig.tick()
        status = rig.x.death_status()
        assert status["dead"] is True and status["alive"] is False
        assert status["cause"] == "game"
        assert "fell from a high place" in status["death_text"]
        assert status["dead_since"]  # an ISO timestamp for the runner
    finally:
        rig.close()


def test_respawn_retries_are_bounded() -> None:
    rig = Rig()
    try:
        rig.body.respawn_fail_times = 99
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 died"]
        rig.tick()
        for _ in range(3):
            with pytest.raises(RuntimeError):
                rig.x.runner_respawn()
        assert rig.body.respawn_calls == 3
        with pytest.raises(RuntimeError, match="gave up"):
            rig.x.runner_respawn()  # the fourth attempt is refused without touching the body
        assert rig.body.respawn_calls == 3
    finally:
        rig.close()


def test_runner_birth_arms_next_life_after_respawn() -> None:
    rig = Rig()
    try:
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 died"]
        rig.tick()
        rig.x.runner_respawn()
        born = (FakeWall().now - timedelta(minutes=42)).isoformat()
        assert rig.x.runner_birth(8, born, 45)["armed"] is True
        rig.tick()  # 3 minutes left: life.expiring fires once
        assert rig.events_of("life.expiring")
    finally:
        rig.close()


def test_runner_birth_refused_while_dead() -> None:
    rig = Rig()
    try:
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 died"]
        rig.tick()
        with pytest.raises(RuntimeError, match="still dead"):
            rig.x.runner_birth(9, "2026-10-18T12:00:00+00:00", 45)
    finally:
        rig.close()


# --- the idle watchdog and retire_stuck ------------------------------------------------------


def test_watchdog_fires_task_idle_and_arms_retire_stuck() -> None:
    hive = FakeHive()
    rig = Rig(hive=hive)
    try:
        rig.tick()
        rig.tick(dt=5.1)  # idle_s=5.0 in the rig; nothing happened in between
        idle = rig.events_of("task.idle")
        assert idle and idle[0]["retire_allowed"] is True
        rig.tick(dt=1.0)
        assert len(rig.events_of("task.idle")) == 1  # one signal per idle period, no spam

        with pytest.raises(RuntimeError, match="does not know its serial"):
            rig.x.retire_stuck()
        rig.x.runner_birth(7, FakeWall().now.isoformat(), 45)
        assert rig.x.retire_stuck() == {"requested": True, "serial": 7, "cause": "stuck"}
        assert hive.requests == [(7, "stuck")]
    finally:
        rig.close()


def test_retire_stuck_refusals_without_signal_or_hive() -> None:
    rig = Rig()
    try:
        with pytest.raises(RuntimeError, match="no idle signal"):
            rig.x.retire_stuck()
        rig.tick(dt=5.1)  # the watchdog fires
        with pytest.raises(RuntimeError, match="does not know its serial"):
            rig.x.retire_stuck()  # no birth armed: the serial is unknown
        rig.x.runner_birth(7, FakeWall().now.isoformat(), 45)
        with pytest.raises(RuntimeError, match="no hive client configured"):
            rig.x.retire_stuck()  # serial known, but no hive to ask
    finally:
        rig.close()


def test_watchdog_not_reset_by_waiting_and_reset_by_act() -> None:
    rig = Rig()
    try:
        rig.tick()
        rig.tick(dt=5.1)
        assert rig.events_of("task.idle")  # plain waiting past the threshold is idle (P1-5)
        rig.x.submit("goto", {"x": 1, "y": 64, "z": 1})
        assert rig.x.retire_allowed is False  # an intent re-arms the node
        rig.tick()
        rig.body.busy_flag = True
        rig.tick(dt=10.0)
        assert len(rig.events_of("task.idle")) == 1  # a busy body is not idle
    finally:
        rig.close()


def test_retire_stuck_keeps_signal_when_hive_fails() -> None:
    rig = Rig()
    try:
        rig.tick(dt=5.1)
        assert rig.x.retire_allowed
        rig.x.runner_birth(7, FakeWall().now.isoformat(), 45)
        rig.x._hive = FakeHive(fail_with=HiveUnavailable("connection refused"))
        with pytest.raises(RuntimeError, match="hive unreachable"):
            rig.x.retire_stuck()
        assert rig.x.retire_allowed  # the signal stays armed for a retry
        rig.x._hive = FakeHive(fail_with=HiveRejected(409, "no_node", "unknown serial"))
        with pytest.raises(RuntimeError, match="hive rejected"):
            rig.x.retire_stuck()
        assert rig.x.retire_allowed
    finally:
        rig.close()


# --- life.expiring ---------------------------------------------------------------------------


def test_life_expiring_fires_once_three_minutes_before_ttl() -> None:
    rig = Rig(birth=BirthRecord(serial=7, born_at="2026-10-18T11:18:00+00:00", ttl_min=45))
    try:
        rig.tick()  # wall now is 12:00: 42 minutes lived, 3 left
        warn = rig.events_of("life.expiring")
        assert warn and 0.0 < warn[0]["remaining_min"] <= 3.1
        rig.tick()
        rig.tick(dt=30.0)
        assert len(rig.events_of("life.expiring")) == 1  # exactly one warning per life
    finally:
        rig.close()


def test_life_expiring_silent_until_the_window() -> None:
    rig = Rig(birth=BirthRecord(serial=7, born_at="2026-10-18T11:30:00+00:00", ttl_min=45))
    try:
        rig.tick(dt=LIFE_WARN_S)  # 15 minutes left
        assert not rig.events_of("life.expiring")
    finally:
        rig.close()


# --- the event queue ---------------------------------------------------------------------------


def test_queue_collapses_same_type_and_keeps_limit() -> None:
    clock = FakeClock()
    queue = EventQueue(limit=3)
    for i in range(5):
        queue.put("game.damage", text=f"hit {i}")
    assert queue.pending() == ({"type": "game.damage", "text": "hit 4"},)  # newest survives
    # chat.line accumulates instead of collapsing (T-051); its own tests cover the cap
    queue.put("chat.line", lines=[{"from": "Steve", "text": "a"}])
    queue.put("game.death", text="died")
    assert len(queue.pending()) == 3  # at the limit
    queue.put("task.idle", idle_s=1.0)
    assert len(queue.pending()) == 3  # the oldest is dropped, never above the limit
    first = queue.pop(0.0, clock)
    assert first["type"] == "chat.line"  # order kept for different types
    assert QUEUE_LIMIT == 200


def test_wait_events_returns_first_accumulated_immediately() -> None:
    clock = FakeClock(100.0)
    queue = EventQueue()
    queue.put("chat.line", lines=[{"from": "Steve", "text": "копилось между ходами"}])
    assert queue.pop(30.0, clock) == {
        "type": "chat.line", "lines": [{"from": "Steve", "text": "копилось между ходами"}]
    }
    assert queue.pop(0.0, clock) is None  # and nothing else waits behind it


# --- the board event (T-050) -------------------------------------------------------------------


def test_board_batches_accumulate_while_others_collapse() -> None:
    queue = EventQueue()
    queue.put("board", lines=["[board #1] a", "[board #2] b"])
    queue.put("board", lines=["[board #3] c"])
    queue.put("game.damage", text="first")
    queue.put("game.damage", text="second")  # still collapses: the newest wins
    assert queue.pending() == (
        {"type": "board", "lines": ["[board #1] a", "[board #2] b", "[board #3] c"]},
        {"type": "game.damage", "text": "second"},
    )


def test_board_accumulation_keeps_the_ten_line_ceiling() -> None:
    queue = EventQueue()
    first = [f"[board #{i}] line {i}" for i in range(1, 10)] + ["… 2 more, `board`"]
    queue.put("board", lines=first)  # a capped batch: 10 lines, 2 already hidden
    queue.put("board", lines=["[board #11] k", "[board #12] l"])
    (event,) = queue.pending()
    assert event["lines"] == [
        *[f"[board #{i}] line {i}" for i in range(1, 10)],
        "[board #11] k",
        "… 3 more, `board`",  # 2 hidden before + the one cut now
    ]


def test_a_single_board_batch_is_capped_too() -> None:
    queue = EventQueue()
    queue.put("board", lines=[f"[board #{i}] t{i}" for i in range(1, 13)])
    (event,) = queue.pending()
    assert event["lines"][-1] == "… 2 more, `board`"
    assert event["lines"][:3] == ["[board #1] t1", "[board #2] t2", "[board #3] t3"]


# --- log.jsonl ---------------------------------------------------------------------------------


def test_log_jsonl_format(tmp_path) -> None:
    rig, path = rig_file(tmp_path)
    try:
        rig.x.submit("goto", {"x": 5, "y": 64, "z": 5})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()
        rig.body.push_event("chat.line", "<Steve> осторожнее")
        rig.tick()  # the chat event lands in the log as kind=event
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 died"]
        rig.tick()  # death
        rig.tick()
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
    finally:
        rig.close()
    kinds = {line["kind"] for line in lines}
    assert {"intent", "event", "death"} <= kinds
    assert all(isinstance(line["ts"], float) for line in lines)
    assert all({"ts", "kind"} <= set(line) for line in lines)
    death = [line for line in lines if line["kind"] == "death"][0]
    assert death["event"] == "game.death"  # the spike orchestrator's grep keeps working
    applied = [line for line in lines if line["kind"] == "intent" and line["phase"] == "applied"]
    assert applied[0]["lever"] == "goto"


def test_c1_maneuvers_and_py4j_errors_land_in_the_log(tmp_path) -> None:
    rig, path = rig_file(tmp_path)
    try:
        rig.body.set_state(hp=3.0)
        rig.body.hostiles = [
            EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.0, pos=(2, 64, 0))
        ]
        rig.body.push_event("game.damage", "zombie hit")
        rig.tick()  # a flee maneuver runs

        rig.body.push_event("chat.line", "<Steve> ok")  # not an error, just context
        original = rig.body.state

        def boom():
            raise RuntimeError("py4j is gone")

        rig.body.state = boom  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="py4j is gone"):
            rig.x._tick()  # a direct call: the exception propagates out of the tick
        rig.body.state = original  # type: ignore[method-assign]
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
    finally:
        rig.close()
    maneuvers = [line for line in lines if line["kind"] == "c1"]
    assert maneuvers and maneuvers[0]["action"] == "flee" and maneuvers[0]["lever"] == "goto"


def test_run_loop_survives_a_broken_tick(tmp_path) -> None:
    rig, path = rig_file(tmp_path)
    try:
        calls = {"n": 0}
        original_tick = rig.x._tick

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("bridge reset")
            original_tick()

        rig.x._tick = flaky  # type: ignore[method-assign]
        rig.x.start()
        deadline = _time.monotonic() + 5.0
        while calls["n"] < 3 and _time.monotonic() < deadline:
            _time.sleep(0.02)
        rig.x.stop()
        assert calls["n"] >= 3  # the loop survived the first tick's crash
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh if line.strip()]
        assert any(
            line["kind"] == "error" and "bridge reset" in line["error"] for line in lines
        )
    finally:
        rig.close()


def test_bad_tick_interval_is_a_startup_error() -> None:
    with pytest.raises(ValueError, match="1-4 Hz"):
        Rig(tick_interval=2.0)


def test_nearest_hostile_matches_real_uc_types() -> None:
    # uc reports the entity TYPE id ("zombie", "cow", "item"), never a hostility flag
    # (Py4jEntryPoint.getEntitiesInfo); the threat source must match those real values.
    rig = Rig()
    try:
        from runtime.mind.executor import HOSTILE_TYPES

        rig.body.hostiles = [
            EntityInfo(kind="item", name="minecraft:oak_log", dist_m=0.5, pos=(1, 64, 1)),
            EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=4.0, pos=(4, 64, 4)),
        ]
        rig.body.set_state(hp=3.0)
        rig.body.push_event("game.damage", "hit")
        rig.tick()
        damage = rig.events_of("game.damage")[0]
        assert damage["source"] == "ent:zombie zombie"  # the item drop at 0.5 m is not a threat
        assert "hostile" not in HOSTILE_TYPES  # no fictional kinds in the table
    finally:
        rig.close()


def test_respawn_clears_the_event_queue() -> None:
    # Live stand lesson: the death event left in the queue was served to every next
    # session, and the dying session's own consumption logged "woke_by: game.death"
    # again — the T-024 orchestrator read that as another death. A respawned body
    # starts a new life with a fresh queue.
    rig = Rig()
    try:
        rig.body.push_event("chat.line", "<Steve> старая жизнь")
        rig.tick()
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 died"]
        rig.tick()  # death; the queued chat line and game.death are still pending
        assert rig.x.events.pending()
        rig.x.runner_respawn()
        assert rig.x.events.pending() == ()
        rig.body.push_event("chat.line", "<Steve> новая жизнь")
        rig.tick()
        served = [e for e in rig.x.events.pending() if e["type"] == "chat.line"]
        assert served == [
            {"type": "chat.line", "lines": [{"from": "Steve", "text": "новая жизнь"}]}
        ]
    finally:
        rig.close()


# --- review round 1: R1-1…R1-4 ------------------------------------------------------------


def test_damage_beyond_rules_reach_keeps_intent_alive() -> None:
    # R1-1, the supervisor's example: a skeleton shooting from 10 m while hp is high.
    # The old code suspended the intent on any damage with a hostile around, but no
    # maneuver ran — no done/failed, and the stuck intent silenced the idle watchdog.
    rig = Rig()
    try:
        intent = rig.x.submit("goto", {"x": 5, "y": 64, "z": 5})
        rig.tick()
        rig.body.busy_flag = True
        rig.tick()

        rig.body.set_state(hp=15.0)
        rig.body.hostiles = [
            EntityInfo(kind="skeleton", name="minecraft:skeleton", dist_m=10.0, pos=(10, 64, 0))
        ]
        rig.body.push_event("game.damage", "arrow")
        rig.tick()
        assert intent.state == Intent.ACTIVE  # NOT suspended: no maneuver owns the body
        rig.tick(dt=1.0)  # the damage keeps coming from beyond the reach
        assert intent.state == Intent.ACTIVE
        assert not [a for a in rig.body.acts if a[0] in ("goto", "strike") and a != ("goto", {"x": 5, "y": 64, "z": 5})]

        # and the intent settles on its own, the watchdog stays armed (R1-1's tail)
        rig.body.busy_flag = False
        rig.body.set_state(pos=(5.0, 64.0, 5.4))
        rig.tick(dt=7.0)  # past the calm window and the instant grace
        assert intent.state == Intent.DONE
        assert rig.events_of("intent.done")
        rig.tick(dt=6.0)  # nothing happens: idle past the threshold
        assert rig.events_of("task.idle"), "the watchdog must stay reachable"
    finally:
        rig.close()


def test_runner_birth_refused_while_armed() -> None:
    # R1-2: the endpoint is reachable from the agent's sandbox; an overwrite would aim
    # the node's retire_stuck at another sister's serial. One arming per life.
    rig = Rig()
    try:
        born = FakeWall().now.isoformat()
        assert rig.x.runner_birth(7, born, 45)["armed"] is True
        with pytest.raises(RuntimeError, match="already armed for serial 7"):
            rig.x.runner_birth(8, born, 45)
        assert rig.x._birth.serial == 7  # the armed clock did not change
    finally:
        rig.close()


def test_respawn_drops_any_intent_that_survived_the_death() -> None:
    # R1-3: a submit racing the death tick could arm an intent for the NEXT node's body.
    # The check now lives inside the lock; whatever still survived dies at the respawn.
    rig = Rig()
    try:
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 died"]
        rig.tick()
        rig.x._current = Intent(99, "goto", {"x": 1, "y": 64, "z": 1}, rig.clock.t)  # the raced intent
        rig.x.runner_respawn()
        assert rig.x._current is None
        rig.tick()
        assert ("goto", {"x": 1, "y": 64, "z": 1}) not in rig.body.acts  # never applied
    finally:
        rig.close()


def test_stuck_request_rules_its_own_death_cause() -> None:
    # R1-4: after a successful retire_stuck the mind rules its death "stuck", not "game".
    rig = Rig(hive=FakeHive())
    try:
        rig.x.runner_birth(7, FakeWall().now.isoformat(), 45)
        rig.tick(dt=5.1)  # the watchdog fires
        assert rig.x.retire_stuck()["requested"] is True
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 was killed"]  # the RCON kill hive sent
        rig.tick()
        status = rig.x.death_status()
        assert status["cause"] == "stuck"
        # the flag belongs to one life: after the respawn the ruling is honest again
        rig.x.runner_respawn()
        rig.x.runner_birth(8, FakeWall().now.isoformat(), 45)
        rig.tick()  # a living tick re-arms the death detector (production: C1 ticks nonstop)
        rig.body.set_state(hp=0.0)
        rig.body.chat = ["LastOrder01 drowned"]
        rig.tick()
        assert rig.x.death_status()["cause"] == "game"
    finally:
        rig.close()


# --- the operator's chat lines (T-051) ---------------------------------------------------------


def test_operator_lines_are_marked_by_the_speakers_name() -> None:
    """The mark comes from the server's `<Name>` prefix alone: the text quoting
    another `<Name>` stays text, a system line carries no `from`."""
    rig = Rig(operator_name="ZeusThunderer")
    try:
        rig.body.push_event("chat.line", "<ZeusThunderer> как дела?")
        rig.body.push_event("chat.line", "<Bob> <ZeusThunderer> hi")
        rig.body.push_event("chat.line", "LastOrder01 was slain by Zombie")
        rig.tick()
        (chat,) = rig.events_of("chat.line")
        assert chat["lines"] == [
            {"from": "ZeusThunderer", "operator": True, "text": "как дела?"},
            {"from": "Bob", "text": "<ZeusThunderer> hi"},
            {"text": "LastOrder01 was slain by Zombie"},
        ]
    finally:
        rig.close()


def test_operator_name_equal_to_the_slot_username_is_refused_once(tmp_path) -> None:
    """The slot's own name would mark every sister line; refused with exactly one
    warning line in the mind log."""
    path = str(tmp_path / "log.jsonl")
    rig = Rig(log_file=path, operator_name="LastOrder01")
    try:
        assert rig.x._operator_name == ""
        rig.body.push_event("chat.line", "<LastOrder01> моя реплика")
        rig.tick()
        (chat,) = rig.events_of("chat.line")
        assert chat["lines"] == [{"from": "LastOrder01", "text": "моя реплика"}]
        with open(path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        ignored = [e for e in entries if e.get("event") == "operator_name.ignored"]
        assert len(ignored) == 1
    finally:
        rig.close()


def test_no_operator_name_no_marks_no_warning(tmp_path) -> None:
    """Empty is simply off: no marks, and no warning noise on every mind start."""
    path = str(tmp_path / "log.jsonl")
    rig = Rig(log_file=path)
    try:
        rig.body.push_event("chat.line", "<ZeusThunderer> привет")
        rig.tick()
        (chat,) = rig.events_of("chat.line")
        assert chat["lines"] == [{"from": "ZeusThunderer", "text": "привет"}]
        with open(path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        assert not [e for e in entries if e.get("event") == "operator_name.ignored"]
    finally:
        rig.close()


def test_chat_batches_accumulate_all_lines_between_waits() -> None:
    queue = EventQueue()
    entries = [{"from": "Steve", "text": f"line {i}"} for i in range(1, 16)]
    queue.put("chat.line", lines=entries[:10])
    queue.put("chat.line", lines=entries[10:])
    (event,) = queue.pending()
    assert event["type"] == "chat.line"
    assert event["lines"] == entries  # nothing lost under the cap


def test_chat_keeps_the_newest_twenty_and_marks_the_dropped() -> None:
    queue = EventQueue()
    entries = [{"from": "Steve", "text": f"line {i}"} for i in range(1, 31)]
    queue.put("chat.line", lines=entries[:12])
    queue.put("chat.line", lines=entries[12:25])  # 25 held: 5 oldest already dropped
    queue.put("chat.line", lines=entries[25:])  # the marker folds them all
    (event,) = queue.pending()
    lines = event["lines"]
    assert len(lines) == 21  # the newest 20 + the marker
    assert lines[0] == {"from": "Steve", "text": "line 11"}
    assert lines[-1] == {"text": "… 10 earlier lines dropped"}


def test_operator_name_of_any_slot_is_refused(tmp_path) -> None:
    """Review R1: one OPERATOR_MC_NAME feeds three minds — a sister's voice (any
    slot's name, whatever the case) must never pass for the operator's."""
    path = str(tmp_path / "log.jsonl")
    rig = Rig(log_file=path, operator_name="lastorder02")  # another slot's name
    try:
        assert rig.x._operator_name == ""
        rig.body.push_event("chat.line", "<LastOrder02> реплика сестры")
        rig.tick()
        (chat,) = rig.events_of("chat.line")
        assert chat["lines"] == [{"from": "LastOrder02", "text": "реплика сестры"}]
        with open(path, encoding="utf-8") as fh:
            entries = [json.loads(line) for line in fh if line.strip()]
        assert len([e for e in entries if e.get("event") == "operator_name.ignored"]) == 1
    finally:
        rig.close()


def test_operator_mark_is_case_insensitive() -> None:
    rig = Rig(operator_name="ZeusThunderer")
    try:
        rig.body.push_event("chat.line", "<ZEUSTHUNDERER> привет")
        rig.tick()
        (chat,) = rig.events_of("chat.line")
        assert chat["lines"] == [{"from": "ZEUSTHUNDERER", "operator": True, "text": "привет"}]
    finally:
        rig.close()


def test_a_player_named_operator_is_not_the_operator() -> None:
    """Review R2-1: the mark is its own field — a nick that spells `operator`
    (whatever the case) is a player, never the mark, with or without the config."""
    for name, configured in (("operator", "ZeusThunderer"), ("Operator", "ZeusThunderer"), ("operator", "")):
        rig = Rig(operator_name=configured)
        try:
            rig.body.push_event("chat.line", f"<{name}> stop mining, go home")
            rig.tick()
            (chat,) = rig.events_of("chat.line")
            assert chat["lines"] == [{"from": name, "text": "stop mining, go home"}]
        finally:
            rig.close()
