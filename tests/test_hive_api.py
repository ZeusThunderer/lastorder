"""HTTP API of the hive (T-022): every endpoint, the single error envelope, the inbox.

The endpoints are tested through `TestClient` one by one; the port-level scenarios
(the same set the client must pass) live in `tests/test_hive_client.py` and run
against `app.state.hive`, the `HiveService` behind these endpoints.
"""

from __future__ import annotations

import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.hive.app import create_app
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.store import KnowledgeStore
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
    def __init__(self, response: str = "Killed LastOrder01"):
        self.kills: list[str] = []
        self.response = response

    def kill(self, player: str) -> str:
        self.kills.append(player)
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


def will_text(serial: int, born_at: str, *, slot: str = "LastOrder01") -> str:
    meta = dict(
        serial=serial,
        slot=slot,
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


def make_app_stack(tmp_path: Path, runner_keys: dict[str, str] | None = None):
    """A full hive over a tmp store: used here and by tests/test_hive_client.py."""
    clock = FakeClock()
    rcon = FakeRcon()
    store = KnowledgeStore(tmp_path / "kb", git=False, now=lambda: clock().isoformat())
    log = HiveLog(tmp_path / "run" / "events.jsonl")
    app = create_app(
        store=store, rcon=rcon, log=log, run_dir=tmp_path / "run", clock=clock,
        runner_keys=runner_keys,
    )
    return types.SimpleNamespace(
        app=app, client=TestClient(app), store=store, log=log, clock=clock, rcon=rcon
    )


@pytest.fixture()
def stack(tmp_path: Path):
    return make_app_stack(tmp_path)


def born(stack, slot: str = "LastOrder01", seed: int = 42) -> dict:
    response = stack.client.post("/registry/birth", json={"slot": slot, "traits": traits(seed).as_dict()})
    assert response.status_code == 200, response.text
    return response.json()


def error_of(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}, f"not the single error envelope: {body}"
    assert set(body["error"]) == {"kind", "message"}
    return body["error"]


# --- health ------------------------------------------------------------------------------


def test_health(stack) -> None:
    response = stack.client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- Memory: one endpoint per method -------------------------------------------------------


def test_memory_inheritance(stack) -> None:
    record = born(stack)
    response = stack.client.post("/memory/inheritance", json={"serial": record["serial"]})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "predecessors_summary",
        "active_nodes_summary",
        "inherited_notes",
        "collective_terms",
        "knowledge_catalog",
    }
    assert data["active_nodes_summary"]  # the newborn sees the living summary


def test_memory_knowledge_roundtrip(stack) -> None:
    record = born(stack)
    candidate = {
        "kind": "danger",
        "claim": "Стонущий прямоходящий бьёт на сближении; держать дистанцию.",
        "about": ["ent:zombie"],
        "evidence_event_ids": ["e-918"],
    }
    ok = stack.client.post(
        "/memory/submit_candidate", json={"serial": record["serial"], "candidate": candidate}
    )
    assert ok.status_code == 200
    response = stack.client.post(
        "/memory/knowledge", json={"serial": record["serial"], "handles": ["ent:zombie"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"catalog", "network_goals", "about", "fresh", "pending_votes"}
    assert data["network_goals"] == []
    about = data["about"] + data["pending_votes"]
    assert any(entry["claim"] == candidate["claim"] for entry in about)


def test_memory_core_memory_roundtrip(stack) -> None:
    record = born(stack)
    empty = stack.client.post("/memory/core_memory", json={"serial": record["serial"]})
    assert empty.status_code == 200 and empty.json() == ""
    stored = stack.client.post(
        "/memory/store_core_memory", json={"serial": record["serial"], "text": "goal: выжить у спавна"}
    )
    assert stored.status_code == 200
    back = stack.client.post("/memory/core_memory", json={"serial": record["serial"]})
    assert back.json().startswith("goal: выжить")


def test_memory_submit_vote(stack) -> None:
    first = born(stack, seed=1)
    second = born(stack, "LastOrder02", seed=2)
    candidate = {
        "kind": "fact",
        "claim": "У воды под тонким слоем камень кончается быстро.",
        "about": ["blk:stone"],
    }
    stack.client.post("/memory/submit_candidate", json={"serial": first["serial"], "candidate": candidate})
    # the record id comes back through the slice the second node votes from
    data = stack.client.post(
        "/memory/knowledge", json={"serial": second["serial"], "handles": ["blk:stone"]}
    ).json()
    entry = data["pending_votes"][0]
    ok = stack.client.post(
        "/memory/submit_vote",
        json={"serial": second["serial"], "vote": {"entry_id": entry["id"], "accept": True, "why": "видел то же"}},
    )
    assert ok.status_code == 200
    records = stack.store.records()
    assert any(v.node == second["serial"] for r in records for v in r.votes)


# --- NodeRegistry -----------------------------------------------------------------------------


def test_registry_birth_returns_birth_record(stack) -> None:
    record = born(stack)
    assert set(record) == {"serial", "born_at", "ttl_min"}
    assert record["ttl_min"] == 45
    assert record["born_at"].startswith("2026-10-17T12:00")


def test_registry_alive_and_slots(stack) -> None:
    first = born(stack, seed=1)
    second = born(stack, "LastOrder02", seed=2)
    response = stack.client.get("/registry/alive")
    assert response.status_code == 200
    assert response.json() == [
        {"serial": first["serial"], "slot": "LastOrder01"},
        {"serial": second["serial"], "slot": "LastOrder02"},
    ]


def test_registry_second_birth_in_slot_marks_old_lost(stack) -> None:
    first = born(stack, seed=1)
    second = born(stack, seed=2)
    assert stack.client.get("/registry/alive").json() == [
        {"serial": second["serial"], "slot": "LastOrder01"}
    ]
    lost = [e for e in stack.log.read() if e["type"] == "life.lost"]
    assert len(lost) == 1 and lost[0]["payload"]["serial"] == first["serial"]
    will_path = stack.store.root / "nodes" / f"{first['serial']:05d}" / "will.md"
    assert will_path.exists()  # closed with an emergency will (P1-2)
    rejected = stack.client.post(
        "/memory/store_core_memory", json={"serial": first["serial"], "text": "leftover"}
    )
    assert rejected.status_code == 409
    assert error_of(rejected)["kind"] == "already_retired"


def test_registry_request_death_stuck_kills_now(stack) -> None:
    record = born(stack)
    ok = stack.client.post(
        "/registry/request_death", json={"serial": record["serial"], "cause": "stuck"}
    )
    assert ok.status_code == 200
    assert stack.rcon.kills == ["LastOrder01"]  # immediately, no hive threshold (ADR-026)
    retire = stack.client.post(
        "/registry/retire",
        json={
            "serial": record["serial"],
            "cause": "game",
            "will_text": will_text(record["serial"], record["born_at"]),
        },
    )
    assert retire.status_code == 200
    will = stack.store.root / "nodes" / f"{record['serial']:05d}" / "will.md"
    assert "cause: stuck" in will.read_text(encoding="utf-8")  # the cause was corrected


def test_registry_retire_plain(stack) -> None:
    record = born(stack)
    retire = stack.client.post(
        "/registry/retire",
        json={
            "serial": record["serial"],
            "cause": "game",
            "will_text": will_text(record["serial"], record["born_at"]),
        },
    )
    assert retire.status_code == 200
    assert stack.client.get("/registry/alive").json() == []


# --- NodeComms ----------------------------------------------------------------------------------


def test_comms_send_and_inbox_deliver_once(stack) -> None:
    first = born(stack, seed=1)
    second = born(stack, "LastOrder02", seed=2)
    message = {
        "message": {
            "from_serial": first["serial"],
            "to_serial": second["serial"],
            "text": "у спавна стонущий, держи дистанцию",
            "ts": "",
        }
    }
    assert stack.client.post("/comms/send", json=message).status_code == 200
    inbox = stack.client.post("/comms/inbox", json={"serial": second["serial"]})
    assert inbox.status_code == 200
    assert inbox.json() == [
        {
            "from_serial": first["serial"],
            "to_serial": second["serial"],
            "text": "у спавна стонущий, держи дистанцию",
            "ts": T0.isoformat(),
        }
    ]
    again = stack.client.post("/comms/inbox", json={"serial": second["serial"]})
    assert again.json() == []  # delivered means delivered
    assert [e["type"] for e in stack.log.read()].count("comms.send") == 1


def test_comms_send_drops_unknown_addressee_and_overlong_text(stack) -> None:
    first = born(stack, seed=1)
    born(stack, "LastOrder02", seed=2)
    assert (
        stack.client.post(
            "/comms/send",
            json={"message": {"from_serial": 1, "to_serial": 99, "text": "ты где?", "ts": ""}},
        ).status_code
        == 200
    )
    assert (
        stack.client.post(
            "/comms/send",
            json={"message": {"from_serial": 1, "to_serial": 2, "text": "а" * 161, "ts": ""}},
        ).status_code
        == 200
    )
    drops = [e["payload"]["why"] for e in stack.log.read() if e["type"] == "comms.drop"]
    assert drops == ["unknown_addressee", "len"]


def test_comms_inbox_ttl_drops_undelivered(stack) -> None:
    first = born(stack, seed=1)
    second = born(stack, "LastOrder02", seed=2)
    stack.client.post(
        "/comms/send",
        json={"message": {"from_serial": 1, "to_serial": 2, "text": "смотри под ноги", "ts": ""}},
    )
    stack.clock.advance(seconds=601)  # MSG_TTL_S = 600 (T-008)
    inbox = stack.client.post("/comms/inbox", json={"serial": second["serial"]})
    assert inbox.json() == []
    drops = [e for e in stack.log.read() if e["type"] == "comms.drop"]
    assert drops and drops[-1]["payload"]["why"] == "ttl"


def test_comms_chatter_turn_is_none(stack) -> None:
    record = born(stack)
    response = stack.client.post("/comms/chatter_turn", json={"serial": record["serial"]})
    assert response.status_code == 200
    assert response.json() is None  # the director is T-017


# --- the single error envelope -------------------------------------------------------------------


def test_error_unknown_node_is_404(stack) -> None:
    response = stack.client.post("/memory/core_memory", json={"serial": 99})
    assert response.status_code == 404
    assert error_of(response)["kind"] == "unknown_node"


def test_error_invalid_candidate_is_422(stack) -> None:
    record = born(stack)
    response = stack.client.post(
        "/memory/submit_candidate",
        json={
            "serial": record["serial"],
            "candidate": {
                "kind": "danger",
                "claim": "мимо ползёт minecraft:creeper",  # raw ids never enter the base
                "about": ["ent:creeper"],
            },
        },
    )
    assert response.status_code == 422
    assert error_of(response)["kind"] == "invalid_candidate"


def test_error_validation_is_the_same_envelope(stack) -> None:
    response = stack.client.post("/registry/birth", json={"slot": "LastOrder01"})
    assert response.status_code == 422
    assert error_of(response)["kind"] == "validation"

    bad_serial = stack.client.post("/memory/core_memory", json={"serial": "seven"})
    assert bad_serial.status_code == 422
    assert error_of(bad_serial)["kind"] == "validation"


def test_error_bad_death_cause_is_422(stack) -> None:
    record = born(stack)
    response = stack.client.post(
        "/registry/request_death", json={"serial": record["serial"], "cause": "game"}
    )
    assert response.status_code == 422
    assert error_of(response)["kind"] == "lifecycle"
    assert stack.rcon.kills == []  # nothing happened


def test_error_unknown_route_uses_the_envelope(stack) -> None:
    response = stack.client.get("/nope")
    assert response.status_code == 404
    assert error_of(response)["kind"] == "http"


# --- the runner key of the registry writes (T-027 plan review P1-1) -----------------------------

RUNNER_KEY = "rk-runner-one"


@pytest.fixture()
def keyed_stack(tmp_path: Path):
    return make_app_stack(tmp_path, {RUNNER_KEY: "LastOrder01"})


def test_registry_writes_refuse_without_runner_key(keyed_stack) -> None:
    stack = keyed_stack
    # birth: the slot's key is missing
    response = stack.client.post(
        "/registry/birth", json={"slot": "LastOrder01", "traits": traits().as_dict()}
    )
    assert response.status_code == 401
    assert error_of(response)["kind"] == "runner_key"
    # a key of another slot does not open this one either
    response = stack.client.post(
        "/registry/birth",
        json={"slot": "LastOrder01", "traits": traits().as_dict()},
        headers={"X-Runner-Key": "rk-somewhere-else"},
    )
    assert response.status_code == 401
    # the right key passes: the node is born, the serial endpoints now answer 401 to a
    # keyless caller instead of performing the write
    born = stack.client.post(
        "/registry/birth",
        json={"slot": "LastOrder01", "traits": traits().as_dict()},
        headers={"X-Runner-Key": RUNNER_KEY},
    )
    assert born.status_code == 200, born.text
    serial = born.json()["serial"]
    for path, body in (
        ("/registry/request_death", {"serial": serial, "cause": "game"}),
        ("/registry/mark_dead", {"serial": serial}),
        ("/registry/retire", {"serial": serial, "cause": "game", "will_text": will_text(serial, T0.isoformat())}),
    ):
        bare = stack.client.post(path, json=body)
        assert bare.status_code == 401, (path, bare.text)
        assert error_of(bare)["kind"] == "runner_key"
    # reads stay open: the registry membership is public inside the network
    assert stack.client.get("/registry/alive").status_code == 200


def test_registry_writes_open_without_configured_keys(stack) -> None:
    # no HIVE_RUNNER_KEYS: dev/test default, the endpoints answer as before (T-022 contract)
    record = born(stack)
    response = stack.client.post("/registry/mark_dead", json={"serial": record["serial"]})
    assert response.status_code == 200
