"""The infrastructure bridge (T-057, ADR-044, TASKBOARD.md 5.4): every acceptance item.

The board side runs under `TestClient` against `create_app` with all four stand keys
configured (operator, node, Last Order, infra) — the same gates the stand runs; the
service layer (`app.state.board`) carries the identities HTTP cannot express. The
executor itself (`dev/infra_bridge.py`) is exercised through its own functions with a
fake transport and a fake runner: what it may run is argv lists of the closed set,
nothing else.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from runtime.hive.app import create_app
from runtime.hive.board import BoardError, LAST_ORDER, OPERATOR
from runtime.hive.lifecycle import HiveLog
from runtime.hive.mcp import HiveMcp
from runtime.hive.proposals import CommonLayers, ProposalBook
from runtime.knowledge.store import KnowledgeStore
from runtime.lastorder.traits import Traits

T0 = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
KEY1 = "sk-node-one"  # slot LastOrder01
KEY_LO = "sk-last-order"
KEY_OP = "op-stand-key"
KEY_INFRA = "sk-infra-bridge"

OP = {"X-Operator-Key": KEY_OP}
INFRA = {"X-Infra-Key": KEY_INFRA}


class FakeClock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeRcon:
    def kill(self, player: str) -> str:
        return "Killed LastOrder01"


def traits(seed: int = 42) -> Traits:
    return Traits(
        bravery=3, greed=7, talkativeness=5, trust_in_chat_and_nodes=4, superstition=9,
        quirk="считает крипера родственником", seed=seed,
    )


class Stack:
    def __init__(self, tmp_path: Path, *, infra_key: str | None = KEY_INFRA):
        self.clock = FakeClock()
        self.rcon = FakeRcon()
        self.store = KnowledgeStore(tmp_path / "kb", git=False, now=lambda: self.clock().isoformat())
        self.run_dir = tmp_path / "run"
        self.log = HiveLog(self.run_dir / "events.jsonl")
        self.app = create_app(
            store=self.store, rcon=self.rcon, log=self.log,
            run_dir=self.run_dir, clock=self.clock,
            node_keys={KEY1: "LastOrder01"}, lo_key=KEY_LO, operator_key=KEY_OP,
            runner_keys={}, infra_key=infra_key, stream_poll_s=0.05,
        )
        self.board = self.app.state.board
        self.client = TestClient(self.app)

    def birth(self) -> int:
        response = self.client.post(
            "/registry/birth", json={"slot": "LastOrder01", "traits": traits().as_dict()}
        )
        assert response.status_code == 200, response.text
        return int(response.json()["serial"])

    def lo_infra(self, action: str, args: dict | None = None, **extra) -> dict:
        request = {"action": action, "args": args or {}, "why": "a slot is stuck", **extra}
        entry = self.board.post(
            LAST_ORDER, kind="request", to=OPERATOR,
            text=f"infra {action}", request=request,
        )
        return entry


def error_of(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}, f"not the single error envelope: {body}"
    return body["error"]


@pytest.fixture()
def stack(tmp_path: Path):
    return Stack(tmp_path)


@pytest.fixture()
def sister(stack: Stack) -> int:
    return stack.birth()


# --- creation rights (TASKBOARD.md 5.4) -----------------------------------------------------


def test_last_order_creates_infra_requests(stack: Stack) -> None:
    entry = stack.lo_infra("slot_restart", {"slot": "2"})
    assert entry["status"] == "open"
    assert entry["request"]["action"] == "slot_restart"
    assert entry["request"]["allowed"] == []  # the executor answers, not the board


def test_a_sister_cannot_create_infra_requests(stack: Stack, sister: int) -> None:
    with pytest.raises(BoardError) as raised:
        stack.board.post(
            str(sister), kind="request", to=OPERATOR, text="infra",
            request={"action": "slot_up", "args": {"slot": "1"}, "why": ""},
        )
    assert raised.value.status == 403


def test_the_operator_cannot_create_infra_requests(stack: Stack) -> None:
    with pytest.raises(BoardError) as raised:  # the matrix: no operator requests at all
        stack.board.post(
            OPERATOR, kind="request", to=OPERATOR, text="infra",
            request={"action": "mc_restart", "args": {}, "why": ""},
        )
    assert raised.value.status == 403


@pytest.mark.parametrize(
    "action,args",
    [
        ("slot_up", {"slot": "4"}),          # beyond the stand's slots
        ("slot_up", {"slot": "0"}),
        ("slot_up", {"slot": "x"}),
        ("slot_up", {}),                     # the slot argument is missing
        ("slot_up", {"slot": "1", "extra": "1"}),
        ("slot_server", {"slot": "1", "server": "host with spaces:25565"}),
        ("slot_server", {"slot": "1", "server": "host;rm -rf"}),
        ("slot_server", {"slot": "1", "server": "a://b"}),
        ("slot_server", {"slot": "1", "server": "host:99999"}),
        ("slot_server", {"slot": "1"}),     # the address is missing
        ("logs", {"service": "body 1"}),    # not a compose service name
        ("logs", {"service": "BODY-1"}),
        ("logs", {}),                       # the service is missing
        ("logs", {"service": "hive", "lines": "0"}),
        ("logs", {"service": "hive", "lines": "501"}),
        ("logs", {"service": "hive", "lines": "many"}),
        ("status", {"slot": "1"}),          # status takes no arguments
    ],
)
def test_bad_actions_and_args_are_refused(stack: Stack, action: str, args: dict) -> None:
    with pytest.raises(BoardError) as raised:
        stack.lo_infra(action, args)
    assert raised.value.status == 422


def test_infra_request_takes_no_board_answers_and_no_grant(stack: Stack) -> None:
    with pytest.raises(BoardError) as raised:
        stack.lo_infra("mc_restart", allowed=["deny"])
    assert raised.value.status == 422
    with pytest.raises(BoardError) as raised:
        stack.lo_infra("mc_restart", grant={"kind": "egress", "what": "a.example"})
    assert raised.value.status == 422


def test_non_infra_actions_stay_free_form_for_sisters(stack: Stack, sister: int) -> None:
    entry = stack.board.post(
        str(sister), kind="request", to=OPERATOR, text="may I?",
        request={"action": "use_lava_bucket_near_base", "args": {"where": "x=1"}, "why": ""},
    )
    assert entry["request"]["action"] == "use_lava_bucket_near_base"


# --- the executor's cycle (take once, report once) ------------------------------------------


def test_take_result_and_the_double_run_refusal(stack: Stack) -> None:
    entry = stack.lo_infra("slot_restart", {"slot": "2"})
    pending = stack.board.infra_pending()
    assert [r["id"] for r in pending] == [entry["id"]]

    taken = stack.board.infra_take("infra", entry["id"])
    assert taken["status"] == "taken"
    assert taken["claimed_by"] == "infra"
    assert stack.board.infra_pending() == []  # taken is not the queue anymore
    with pytest.raises(BoardError) as raised:
        stack.board.infra_take("infra", entry["id"])
    assert raised.value.status == 409

    done = stack.board.infra_result("infra", entry["id"], True, "== slot 2: done\nslot_restart: done (2)")
    assert done["status"] == "done"
    assert done["thread"][-1]["from"] == "infra"
    assert "slot_restart: done (2)" in done["thread"][-1]["text"]
    with pytest.raises(BoardError) as raised:  # a repeat execution attempt is a refusal
        stack.board.infra_result("infra", entry["id"], True, "again")
    assert raised.value.status == 409


def test_the_output_is_truncated_never_refused(stack: Stack) -> None:
    entry = stack.lo_infra("status")
    stack.board.infra_take("infra", entry["id"])
    done = stack.board.infra_result("infra", entry["id"], False, "x" * 5000)
    assert len(done["thread"][-1]["text"]) == 800


def test_raw_ids_in_the_output_are_masked(stack: Stack) -> None:
    entry = stack.lo_infra("logs", {"service": "body-1"})
    stack.board.infra_take("infra", entry["id"])
    done = stack.board.infra_result("infra", entry["id"], True, "picked minecraft:oak_log")
    assert "minecraft:" not in done["thread"][-1]["text"]


def test_the_board_does_not_answer_an_infra_request(stack: Stack, sister: int) -> None:
    entry = stack.lo_infra("mc_restart")
    with pytest.raises(BoardError) as raised:  # the operator's approve has no move
        stack.board.transition(OPERATOR, entry["id"], "approved", text="ok")
    assert raised.value.status == 409
    canceled = stack.board.transition(  # the author may still cancel while open
        LAST_ORDER, entry["id"], "canceled"
    )
    assert canceled["status"] == "canceled"


def test_only_the_infra_actor_takes_and_finishes(stack: Stack, sister: int) -> None:
    entry = stack.lo_infra("status")
    for actor in (OPERATOR, LAST_ORDER, str(sister), "runner:LastOrder01", "admin"):
        with pytest.raises(BoardError) as raised:
            stack.board.infra_take(actor, entry["id"])
        assert raised.value.status == 403


def test_taken_by_the_bridge_cannot_be_canceled_late(stack: Stack) -> None:
    entry = stack.lo_infra("status")
    stack.board.infra_take("infra", entry["id"])
    with pytest.raises(BoardError) as raised:
        stack.board.transition(LAST_ORDER, entry["id"], "canceled")
    assert raised.value.status == 409


# --- the switch (TASKBOARD.md 5.4: off waits, the reason is visible) ------------------------


def test_switch_off_keeps_requests_waiting_and_shows_the_reason(stack: Stack) -> None:
    entry = stack.lo_infra("status")
    state = stack.board.infra_switch_get()
    assert state["on"] is True  # the default: the executor works out of the box

    off = stack.board.infra_switch_set(OPERATOR, False, "стенд занят прогоном T-060")
    assert off["on"] is False
    assert off["reason"] == "стенд занят прогоном T-060"

    assert stack.board.infra_pending()[0]["id"] == entry["id"]  # the request waits
    with pytest.raises(BoardError) as raised:  # not the executor's to flip
        stack.board.infra_switch_set("infra", True)
    assert raised.value.status == 403


def test_switch_survives_a_hive_restart(tmp_path: Path) -> None:
    first = Stack(tmp_path)
    first.board.infra_switch_set(OPERATOR, False, "maintenance")
    first.app.state.board.close()
    second = Stack(tmp_path)
    assert second.board.infra_switch_get() == {
        "on": False, "reason": "maintenance", "at": second.board.infra_switch_get()["at"],
        "by": OPERATOR,
    }


# --- the HTTP gates --------------------------------------------------------------------------


def test_infra_routes_need_the_infra_key(stack: Stack) -> None:
    paths = [
        ("GET", "/board/infra/pending"),
        ("GET", "/board/infra/taken"),
        ("GET", "/board/infra/switch"),
        ("POST", "/board/infra/1/take"),
        ("POST", "/board/infra/1/result"),
    ]
    for method, path in paths:
        response = stack.client.request(method, path, json={})
        assert response.status_code == 401, (method, path, response.text)
        response = stack.client.request(method, path, json={}, headers={"X-Infra-Key": "wrong"})
        assert response.status_code == 401, (method, path, response.text)
    # a sister's or the operator's key opens nothing here either
    for headers in ({"X-Node-Key": KEY1}, {"X-Node-Key": KEY_LO}, OP):
        response = stack.client.get("/board/infra/pending", headers=headers)
        assert response.status_code == 401, headers


def test_infra_http_cycle_and_the_switch_routes(stack: Stack) -> None:
    entry = stack.lo_infra("logs", {"service": "hive", "lines": "10"})
    response = stack.client.get("/board/infra/pending", headers=INFRA)
    assert response.status_code == 200
    assert [r["id"] for r in response.json()["requests"]] == [entry["id"]]

    response = stack.client.post(f"/board/infra/{entry['id']}/take", headers=INFRA)
    assert response.status_code == 200
    response = stack.client.post(f"/board/infra/{entry['id']}/take", headers=INFRA)
    assert response.status_code == 409 and error_of(response)["kind"] == "already_taken"

    response = stack.client.post(
        f"/board/infra/{entry['id']}/result", headers=INFRA,
        json={"ok": True, "text": "hive logs here"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "done"

    response = stack.client.post("/board/infra/switch", headers=INFRA, json={"on": False})
    assert response.status_code == 401  # the executor cannot switch itself
    response = stack.client.post(
        "/board/infra/switch", headers=OP, json={"on": False, "reason": "пауза"}
    )
    assert response.status_code == 200 and response.json()["on"] is False
    response = stack.client.get("/board/infra/switch", headers=INFRA)
    assert response.json()["reason"] == "пауза"
    response = stack.client.get("/board/infra/switch", headers=OP)  # the page reads it too
    assert response.status_code == 200


def test_a_duplicated_infra_key_refuses_to_start(tmp_path: Path) -> None:
    for duplicate in (KEY1, KEY_LO, KEY_OP):
        with pytest.raises(ValueError):
            Stack(tmp_path, infra_key=duplicate)


# --- Last Order's view: infra_state ------------------------------------------------------------


def fake_ctx(key: str) -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(
        headers={"x-node-key": key}
    )))


def test_infra_state_tells_last_order_why_nothing_runs(
    stack: Stack, tmp_path: Path
) -> None:
    from runtime.hive.app import CommsBook, HiveService
    from runtime.hive.lifecycle import Lifecycle

    lifecycle = Lifecycle(
        stack.store, stack.rcon, log=stack.log, run_dir=stack.run_dir, clock=stack.clock
    )
    mcp = HiveMcp(
        stack.store, lifecycle,
        HiveService(stack.store, lifecycle, CommsBook(lifecycle, log=stack.log, clock=stack.clock)),
        ProposalBook(stack.store, now=lambda: stack.clock().isoformat()),
        CommonLayers(stack.store, now=lambda: stack.clock().isoformat()),
        node_keys={KEY1: "LastOrder01"}, lo_key=KEY_LO,
        run_dir=stack.run_dir, log=stack.log, board=stack.board,
    )
    entry = stack.lo_infra("status")
    state = mcp.infra_state(fake_ctx(KEY_LO))
    assert state["on"] is True
    assert f"#{entry['id']} status" in state["pending"][0]

    stack.board.infra_switch_set(OPERATOR, False, "стенд занят")
    state = mcp.infra_state(fake_ctx(KEY_LO))
    assert state["on"] is False
    assert state["reason"] == "стенд занят"

    with pytest.raises(Exception):
        mcp.infra_state(fake_ctx(KEY1))  # a sister's key opens nothing here


# --- the executor itself (dev/infra_bridge.py) ------------------------------------------------

SPEC = importlib.util.spec_from_file_location(
    "infra_bridge", Path(__file__).resolve().parent.parent / "dev" / "infra_bridge.py"
)
bridge = importlib.util.module_from_spec(SPEC)
sys.modules["infra_bridge"] = bridge
SPEC.loader.exec_module(bridge)


def fake_runner(results):
    """Returns queued CompletedProcess-like results; records every argv it got."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        result = results.pop(0)
        return subprocess.CompletedProcess(
            argv, result.get("rc", 0),
            stdout=result.get("out", ""), stderr=result.get("err", ""),
        )

    return run, calls


class FakeHive:
    """Records every call the bridge makes; answers from a script."""

    def __init__(self, switch=None, pending=(), taken=()):
        self.base = "fake-board"
        self.switch = switch or {"on": True, "reason": ""}
        self.pending = list(pending)
        self.taken = list(taken)
        self.posts = []

    def call(self, method, path, body=None):
        if method == "GET" and path == "/board/infra/switch":
            return 200, self.switch
        if method == "GET" and path == "/board/infra/pending":
            return 200, {"requests": self.pending}
        if method == "GET" and path == "/board/infra/taken":
            return 200, {"requests": self.taken}
        if method == "POST":
            self.posts.append((path, body))
            if path.endswith("/take"):
                return (409, {"error": {"kind": "already_taken"}}) if path in [p for p, _ in self.posts[:-1]] else (200, {})
            return 200, {}
        return 404, {}


def request_of(entry_id, action, args):
    return {"id": entry_id, "request": {"action": action, "args": args}}


def test_the_command_surface_is_the_closed_set() -> None:
    assert bridge.commands_for("mc_restart", {})[0].argv[-2:] == ["restart", "mc-server"]
    assert bridge.commands_for("status", {})[0].argv[-1] == "ps"
    logs = bridge.commands_for("logs", {"service": "mind-2"})[0].argv
    assert logs[-3:] == ["--tail", "50", "mind-2"]
    assert bridge.commands_for("slot_restart", {"slot": "2"})[0].argv[-1] == "2"
    down = bridge.commands_for("slot_down", {"slot": "3"})[0].argv[-3:]
    assert down == ["mind-3", "node-3", "body-3"]
    up = bridge.commands_for("slot_up", {"slot": "1"})
    assert up[0].argv[-2:] == ["--no-deps", "body-1"]
    assert up[1].argv[-2:] == ["-q", "body-1"] and up[1].capture == "cid"
    assert up[2].argv[1].endswith("wait_for.sh") and "{cid}" in up[2].argv[-1]
    server = bridge.commands_for("slot_server", {"slot": "1", "server": "mc.example:25565"})
    assert server[0].argv[-2:] == ["stop", "mind-1"]
    assert server[1].env == {"MC_SERVER_1": "mc.example:25565"}
    assert server[1].argv[-2:] == ["--no-deps", "body-1"]
    with pytest.raises(bridge.InfraError):
        bridge.commands_for("shell", {"cmd": "rm -rf /"})  # there is no such verb


def test_run_steps_wires_the_captured_container_id() -> None:
    steps = bridge.commands_for("slot_up", {"slot": "1"})
    runner, calls = fake_runner([
        {"out": ""},                                   # up body-1
        {"out": "abc123\n"},                           # ps -q body-1 -> cid
        {"out": "healthy"},                            # wait_for
        {"out": ""}, {"out": ""},                      # mind, node
    ])
    ok, transcript = bridge.run_steps(steps, runner=runner)
    assert ok, transcript
    assert calls[2][-1] == "abc123"  # {cid} filled from the capture, first try
    assert "$ " in transcript


def test_run_steps_stops_at_the_first_failure() -> None:
    steps = bridge.commands_for("mc_restart", {})
    runner, calls = fake_runner([{"rc": 1, "err": "no such service: mc-server"}])
    ok, transcript = bridge.run_steps(steps, runner=runner)
    assert not ok
    assert "no such service" in transcript and "(exit 1)" in transcript


def test_process_reports_done_and_failed_with_the_tail() -> None:
    hive = FakeHive(pending=[request_of(41, "status", {})])
    runner, _ = fake_runner([{"out": "Name  State\nmc-server  running"}])
    bridge.process(hive, hive.pending[0], project="", dry=False, runner=runner)
    take, result = hive.posts
    assert take[0] == "/board/infra/41/take"
    assert result[0] == "/board/infra/41/result" and result[1]["ok"] is True
    assert "mc-server  running" in result[1]["text"]

    hive = FakeHive(pending=[request_of(42, "mc_restart", {})])
    runner, _ = fake_runner([{"rc": 1, "err": "daemon down"}])
    bridge.process(hive, hive.pending[0], project="", dry=False, runner=runner)
    assert hive.posts[-1][1]["ok"] is False
    assert "daemon down" in hive.posts[-1][1]["text"]


def test_a_dry_run_touches_nothing() -> None:
    hive = FakeHive(pending=[request_of(7, "slot_restart", {"slot": "2"})])
    runner, calls = fake_runner([])
    bridge.process(hive, hive.pending[0], project="", dry=True, runner=runner)
    assert hive.posts == [] and calls == []


def test_process_revalidates_the_closed_set_before_running() -> None:
    # the board validated at creation; the bridge trusts nothing it polled
    hive = FakeHive(pending=[request_of(9, "slot_up", {"slot": "4"})])
    runner, calls = fake_runner([])
    bridge.process(hive, hive.pending[0], project="", dry=False, runner=runner)
    assert calls == []
    path, body = hive.posts[-1]
    assert path == "/board/infra/9/result" and body["ok"] is False
    assert "closed set" in body["text"]


def test_main_off_switch_runs_nothing_and_leaves_the_queue() -> None:
    pending = [request_of(5, "status", {})]
    hive = FakeHive(switch={"on": False, "reason": "стенд занят прогоном"}, pending=pending)
    code = bridge.main_with(hive, once=True)  # one cycle against the fake board
    assert code == 0
    assert hive.posts == [] and hive.pending == pending  # nothing taken, nothing run


def test_main_switch_off_mid_batch_stops_the_next_take() -> None:
    hive = FakeHive(pending=[request_of(5, "status", {}), request_of(6, "status", {})])

    def run(argv, **kwargs):  # the operator flips the switch while #5 runs
        hive.switch = {"on": False, "reason": "пауза"}
        return subprocess.CompletedProcess(argv, 0, stdout="ps", stderr="")

    assert bridge.main_with(hive, once=True, runner=run) == 0
    assert not any(p[0] == "/board/infra/6/take" for p in hive.posts)


def test_main_closes_the_leftovers_of_a_crashed_run() -> None:
    hive = FakeHive(
        pending=[request_of(6, "status", {})],
        taken=[request_of(3, "slot_down", {"slot": "1"})],
    )
    runner, _ = fake_runner([{"out": "ps"}])
    code = bridge.main_with(hive, once=True, runner=runner)
    assert code == 0
    closing = [p for p in hive.posts if p[0] == "/board/infra/3/result"]
    assert closing and closing[0][1]["ok"] is False
    assert "restarted" in closing[0][1]["text"]
    assert any(p[0] == "/board/infra/6/take" for p in hive.posts)  # then new work


def test_main_without_a_key_is_a_usage_error() -> None:
    assert bridge.main(["--dry-run"]) == 2
