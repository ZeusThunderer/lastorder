"""ADR-050: the mind owns its body — it asks the executor for it and for a new one.

The supervisor reads `last_ok` (when the last uc call came back) and asks through the
board with the slot's runner key; the board keeps a slot to its own body and mind and
caps the restarts. The bridge addresses make the mind a peer of its body on link-N.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.game.py4j_body import bridge_addresses
from runtime.hive.app import create_app
from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.store import KnowledgeStore
from runtime.mind.bodyctl import BOOT_S, BodySupervisor, make_body, slot_number


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Body:
    last_ok: float | None = None


class Hive:
    def __init__(self, fail: Exception | None = None) -> None:
        self.asks: list[tuple[str, dict]] = []
        self.alarms: list[str] = []
        self.fail = fail

    def infra_slot(self, action, args, text, why=""):
        if self.fail is not None:
            raise self.fail
        self.asks.append((action, dict(args)))
        return {"id": len(self.asks)}

    def board_alarm(self, alarm_type, text, *, slot="", serial=None):
        self.alarms.append(alarm_type)


def rig(fail: Exception | None = None):
    clock, body, hive = Clock(), Body(), Hive(fail)
    sup = BodySupervisor(body, hive, username="LastOrder02", clock=clock, sleep=lambda _s: None)
    return sup, clock, body, hive


def test_a_mind_without_a_body_asks_for_one_after_the_grace() -> None:
    sup, clock, _body, hive = rig()
    assert sup.tick() is None            # a mind restarted beside a living body: wait a moment
    clock.t += 25
    assert sup.tick() == "body_up"
    assert hive.asks == [("body_up", {"slot": "2", "kind": "minecraft"})]
    clock.t += 60
    assert sup.tick() is None            # the body boots: patience, no second ask
    clock.t += BOOT_S
    assert sup.tick() == "body_up"       # still nothing after a whole boot: ask again


def test_a_body_that_answered_and_went_silent_is_recreated() -> None:
    sup, clock, body, hive = rig()
    body.last_ok = clock.t
    clock.t += 30
    assert sup.tick() is None            # a short silence is a laggy tick, not a death
    clock.t += 70
    assert sup.tick() == "body_restart"
    assert hive.asks == [("body_restart", {"slot": "2"})]


def test_the_cap_turns_into_one_stuck_alarm() -> None:
    sup, clock, body, hive = rig(HiveRejected(429, "rate_limited", "3 restarts in 10 min"))
    body.last_ok = clock.t
    clock.t += 200
    assert sup.tick() is None
    assert hive.alarms == ["stuck"]
    clock.t += 10
    sup.tick()                           # quiet until the boot window passes
    assert hive.alarms == ["stuck"]


def test_a_hive_away_retries_soon() -> None:
    sup, clock, _body, hive = rig(HiveUnavailable("down"))
    clock.t += 25
    assert sup.tick() is None
    hive.fail = None
    clock.t += 31
    assert sup.tick() == "body_up"


def test_the_kind_picks_the_adapter_and_the_slot_reads_from_the_account() -> None:
    assert slot_number("LastOrder03") == 3
    with pytest.raises(ValueError):
        make_body("chess")
    assert type(make_body("minecraft")).__name__ == "Py4jBody"


def test_the_bridge_addresses_make_the_mind_a_peer() -> None:
    assert bridge_addresses({}) == ("127.0.0.1", 25333, "127.0.0.1", 25334)   # the old layout
    assert bridge_addresses(
        {"BODY_HOST": "body-2", "BODY_PORT": "25433", "CALLBACK_ADDRESS": "10.250.2.3"}
    ) == ("body-2", 25433, "10.250.2.3", 25334)


def test_the_slot_route_takes_the_runner_key_and_its_own_slot(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "kb", git=False)
    app = create_app(
        store=store, rcon=None, log=HiveLog(tmp_path / "run" / "events.jsonl"),
        run_dir=tmp_path / "run", node_keys={}, lo_key="lo", operator_key="op",
        runner_keys={"rk-2": "LastOrder02"}, infra_key="infra", stream_poll_s=0.05,
    )
    client = TestClient(app)

    def ask(key: str, action: str, args: dict):
        return client.post(
            "/board/infra/slot", headers={"X-Runner-Key": key},
            json={"text": "ask", "request": {"action": action, "args": args, "why": "test"}},
        )

    ok = ask("rk-2", "body_up", {"slot": "2", "kind": "minecraft"})
    assert ok.status_code == 200 and ok.json()["from"] == "runner:LastOrder02"
    assert ask("rk-2", "body_up", {"slot": "1"}).status_code == 403    # another slot's body
    assert ask("nope", "body_up", {"slot": "2"}).status_code == 401
    pending = client.get("/board/infra/pending", headers={"X-Infra-Key": "infra"}).json()
    assert [r["request"]["action"] for r in pending["requests"]] == ["body_up"]
