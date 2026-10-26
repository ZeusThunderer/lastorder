"""The hive task board (T-042, docs/architecture/TASKBOARD.md): every acceptance item.

The HTTP surface runs under `TestClient` against `create_app` with the operator key,
the node keys and the Last Order key configured — the same gates the stand runs. The
MCP tool set over the real streamable transport lives in `tests/test_hive_board_mcp.py`;
here the service layer (`app.state.board`) carries the identity of a sister where the
HTTP surface cannot express it (a sister writes through MCP alone).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from runtime.hive.app import create_app
from runtime.hive.board import AGENT_MUTATIONS_PER_LIFE, OPERATOR_MSGS_PER_LIFE
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.store import KnowledgeStore
from runtime.lastorder.traits import Traits

T0 = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
KEY1 = "sk-node-one"  # slot LastOrder01
KEY2 = "sk-node-two"  # slot LastOrder02
KEY_LO = "sk-last-order"
KEY_OP = "op-stand-key"
RKEY1 = "rk-runner-one"  # the node runner of slot LastOrder01

OP = {"X-Operator-Key": KEY_OP}


class FakeClock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float = 0) -> None:
        self.now = self.now + timedelta(minutes=minutes)


class FakeRcon:
    def __init__(self, response: str = "Killed LastOrder01"):
        self.kills: list[str] = []
        self.response = response

    def kill(self, player: str) -> str:
        self.kills.append(player)
        return self.response


def traits(seed: int = 42) -> Traits:
    return Traits(
        bravery=3, greed=7, talkativeness=5, trust_in_chat_and_nodes=4, superstition=9,
        quirk="считает крипера родственником", seed=seed,
    )


class Stack:
    def __init__(self, tmp_path: Path, *, runner_keys: dict[str, str] | None = None):
        self.clock = FakeClock()
        self.rcon = FakeRcon()
        self.store = KnowledgeStore(tmp_path / "kb", git=False, now=lambda: self.clock().isoformat())
        self.run_dir = tmp_path / "run"
        self.log = HiveLog(self.run_dir / "events.jsonl")
        self.runner_keys = runner_keys if runner_keys is not None else {}
        self.app = create_app(
            store=self.store, rcon=self.rcon, log=self.log,
            run_dir=self.run_dir, clock=self.clock,
            node_keys={KEY1: "LastOrder01", KEY2: "LastOrder02"},
            lo_key=KEY_LO, operator_key=KEY_OP, runner_keys=self.runner_keys,
            stream_poll_s=0.05,
        )
        self.board = self.app.state.board
        self.client = TestClient(self.app)

    # -- plumbing ----------------------------------------------------------------------

    def birth(self, slot: str = "LastOrder01") -> int:
        headers = {"X-Runner-Key": RKEY1} if self.runner_keys and slot == "LastOrder01" else {}
        response = self.client.post(
            "/registry/birth", json={"slot": slot, "traits": traits().as_dict()}, headers=headers
        )
        assert response.status_code == 200, response.text
        return int(response.json()["serial"])

    def op(self, method: str, path: str, body: dict | None = None):
        return self.client.request(method, path, json=body, headers=OP)

    def node(self, method: str, path: str, body: dict | None = None, key: str = KEY1):
        return self.client.request(method, path, json=body, headers={"X-Node-Key": key})

    def post_op(self, **fields) -> dict:
        response = self.op("POST", "/board/entries", fields)
        assert response.status_code == 200, response.text
        return response.json()

    def op_task(self, to: str = "net", text: str = "Build a shelter by the spawn.") -> dict:
        return self.post_op(kind="task", to=to, text=text)


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


# --- identity (section 2) ---------------------------------------------------------------------


def test_from_in_the_body_is_refused(stack: Stack) -> None:
    response = stack.op("POST", "/board/entries",
                        {"kind": "task", "to": "net", "text": "x", "from": "operator"})
    assert response.status_code == 422
    assert error_of(response)["kind"] == "validation"


def test_http_without_operator_key_is_401(stack: Stack, sister: int) -> None:
    for method, path in (
        ("GET", "/board/entries"),
        ("POST", "/board/entries"),
        ("GET", "/board/stream"),
        ("POST", f"/board/entries/{sister}/transition"),
    ):
        response = stack.client.request(method, path, json={})
        assert response.status_code == 401, (method, path, response.text)
        assert error_of(response)["kind"] == "operator_key"
    # the dual-auth routes refuse a keyless caller too (operator or agent key required)
    for method, path in (("GET", "/board/feed"), ("GET", f"/board/entries/{sister}")):
        assert stack.client.request(method, path, json={}).status_code == 401


def test_sister_writes_from_her_serial_and_the_key_maps_to_it(stack: Stack) -> None:
    serial = stack.birth()
    entry = stack.board.post(str(serial), kind="task", to="net", text="Собрать сахарный тростник.")
    assert entry["from"] == str(serial)
    # the HTTP identity path: the slot's key maps to the live serial in the feed
    task = stack.op_task(to=str(serial))
    feed = stack.node("GET", "/board/feed")
    assert feed.status_code == 200, feed.text
    events = feed.json()["events"]
    assert [e["entry_id"] for e in events] == [task["id"]]
    assert events[0]["entry"]["to"] == str(serial)


def test_slot_without_live_node_is_refused(stack: Stack) -> None:
    response = stack.node("GET", "/board/feed")
    assert response.status_code == 409
    assert error_of(response)["kind"] == "no_live_node"


# --- the posting matrix (section 3.3) -----------------------------------------------------------

# (actor, kind, target class): the allowed cells of the section 3.3 table.
ALLOWED = {
    ("operator", "task", "node"),
    ("operator", "task", "net"),
    ("operator", "task", "last-order"),
    ("operator", "message", "net"),
    ("last-order", "task", "node"),
    ("last-order", "task", "net"),
    ("last-order", "request", "operator"),
    ("last-order", "message", "operator"),
    ("last-order", "message", "net"),
    ("node", "task", "node"),
    ("node", "task", "net"),
    ("node", "task", "last-order"),
    ("node", "request", "operator"),
    ("node", "request", "last-order"),
    ("node", "message", "operator"),
}


def test_post_matrix_allowed_cells_work(stack: Stack) -> None:
    one, two = stack.birth(), stack.birth("LastOrder02")
    targets = {"node": str(two), "net": "net", "last-order": "last-order", "operator": "operator"}
    for actor, kind, cls in sorted(ALLOWED):
        who = str(one) if actor == "node" else actor
        request = {"action": "use_lava_bucket", "args": {}, "why": "test"} if kind == "request" else None
        entry = stack.board.post(who, kind=kind, to=targets[cls], text="матрица", request=request)
        assert entry["from"] == who, (actor, kind, cls)


def test_post_matrix_every_no_is_refused(stack: Stack) -> None:
    one, two = stack.birth(), stack.birth("LastOrder02")
    targets = {"node": str(two), "net": "net", "last-order": "last-order", "operator": "operator"}
    for actor in ("operator", "last-order", str(one), "runner:LastOrder01", "admin"):
        role = "node" if actor == str(one) else actor
        for kind in ("task", "request", "message"):
            for cls, to in targets.items():
                request = {"action": "a_thing"} if kind == "request" else None
                if (role, kind, cls) in ALLOWED:
                    continue
                with pytest.raises(Exception) as caught:
                    stack.board.post(actor, kind=kind, to=to, text="нет", request=request)
                assert caught.value.status in (403, 422), (actor, kind, cls)


def test_sister_cannot_approve_a_request(stack: Stack) -> None:
    one, two = stack.birth(), stack.birth("LastOrder02")
    request = stack.board.post(str(one), kind="request", to="operator",
                               text="Можно ли сжечь лес?", request={"action": "burn_forest"})
    with pytest.raises(Exception) as caught:
        stack.board.transition(str(two), request["id"], "approved", text="")
    assert caught.value.status == 403


def test_grant_from_a_sister_is_422(stack: Stack) -> None:
    serial = stack.birth()
    with pytest.raises(Exception) as caught:
        stack.board.post(str(serial), kind="request", to="operator", text="пустить наружу",
                         request={"action": "a_thing", "grant": {"kind": "egress", "what": "tg"}})
    assert caught.value.status == 422
    # Last Order's own grant rides (5.3): accepted and stored in its own field
    entry = stack.board.post("last-order", kind="request", to="operator", text="решение по сети",
                             request={"action": "a_thing", "grant": {"kind": "egress", "what": "tg"}})
    assert entry["grant"] == {"kind": "egress", "what": "tg"}
    assert "grant" not in entry["request"]


# --- transitions (section 3.2) -------------------------------------------------------------------


def test_task_move_set_allowed(stack: Stack) -> None:
    moves = [
        # (start, move, actor class, task target, text)
        ("open", "taken", "taker", "net", ""),
        ("open", "declined", "taker", "personal", "не могу: рука занята"),
        ("taken", "done", "taker", "net", "готово"),
        ("taken", "failed", "taker", "net", "криперы"),
        ("taken", "needs_input", "taker", "net", "где брёвна?"),
        ("needs_input", "taken", "author", "net", "у реки"),
        ("taken", "open", "taker", "net", "не успеваю"),
        ("open", "canceled", "operator", "net", ""),
        ("taken", "canceled", "operator", "net", ""),
        ("needs_input", "canceled", "operator", "net", ""),
    ]
    for start, move, who, target, text in moves:
        doer = stack.birth()  # a fresh life per row: the held-task cap never bites
        to = str(doer) if target == "personal" else target
        task = stack.op_task(to=to)
        if start != "open":
            stack.board.claim(str(doer), task["id"])
        if start == "needs_input":
            stack.board.transition(str(doer), task["id"], "needs_input", text="вопрос")
        actor = {"taker": str(doer), "author": "operator", "operator": "operator"}[who]
        moved = stack.board.transition(actor, task["id"], move, text=text)
        assert moved["status"] == move, (start, move)
        if (start, move) == ("needs_input", "taken"):
            # the author's answer returns the task to the taker, not to the author (R1-2)
            assert moved["claimed_by"] == str(doer)


def test_task_move_set_refused(stack: Stack) -> None:
    for pair in (("open", "done"), ("open", "failed"), ("taken", "declined"),
                 ("needs_input", "done"), ("done", "open")):
        serial = stack.birth()
        task = stack.op_task(to=str(serial))
        if pair[0] in ("taken", "needs_input", "done"):
            stack.board.claim(str(serial), task["id"])
        if pair[0] == "needs_input":
            stack.board.transition(str(serial), task["id"], "needs_input", text="вопрос")
        if pair[0] == "done":
            stack.board.transition(str(serial), task["id"], "done", text="итог")
        with pytest.raises(Exception) as caught:
            stack.board.transition(str(serial), task["id"], pair[1], text="x")
        assert caught.value.status == 409 and caught.value.kind == "bad_transition", pair


def test_final_statuses_are_frozen(stack: Stack) -> None:
    for final, text in (("done", "итог"), ("failed", "причина"), ("declined", "причина")):
        serial = stack.birth()
        task = stack.op_task(to=str(serial))
        if final == "declined":
            stack.board.transition(str(serial), task["id"], "declined", text=text)
        else:
            stack.board.claim(str(serial), task["id"])
            stack.board.transition(str(serial), task["id"], final, text=text)
        for retry in ("open", "taken", "canceled"):
            with pytest.raises(Exception) as caught:
                stack.board.transition(str(serial), task["id"], retry, text="x")
            assert caught.value.status == 409


@pytest.mark.parametrize("move", ["declined", "failed", "needs_input", "open"])
def test_text_mandatory_moves_without_text_are_422(stack: Stack, sister: int, move: str) -> None:
    # declined is a personal-task move; the release/failed/needs_input set starts from taken
    task = stack.op_task(to=str(sister)) if move == "declined" else stack.op_task(to="net")
    if move in ("failed", "needs_input", "open"):  # these start from taken
        stack.board.claim(str(sister), task["id"])
    with pytest.raises(Exception) as caught:
        stack.board.transition(str(sister), task["id"], move, text="")
    assert caught.value.status == 422


def test_declining_a_net_task_is_a_reply_not_a_move(stack: Stack, sister: int) -> None:
    task = stack.op_task(to="net")
    with pytest.raises(Exception) as caught:
        stack.board.transition(str(sister), task["id"], "declined", text="не берусь")
    assert caught.value.status == 409
    reply = stack.board.reply(str(sister), task["id"], "не берусь: руки в Dirt")
    assert reply["seq"] == 1
    fresh = stack.board.entry_for(str(sister), task["id"])
    assert fresh["status"] == "open"  # still open: others may claim


# --- review round 1: who may move a held task ---------------------------------------------------


def test_r1_1_only_the_claimer_moves_a_taken_task(stack: Stack) -> None:
    one, two = stack.birth(), stack.birth("LastOrder02")
    task = stack.op_task(to="net")
    stack.board.claim(str(one), task["id"])
    for move, text in (("done", "итог"), ("failed", "причина"),
                       ("needs_input", "вопрос"), ("open", "отпускаю")):
        with pytest.raises(Exception) as caught:
            stack.board.transition(str(two), task["id"], move, text=text)
        assert caught.value.status == 403, move
    with pytest.raises(Exception) as caught:  # Last Order neither: she did not take it
        stack.board.transition("last-order", task["id"], "done", text="итог")
    assert caught.value.status == 403
    assert stack.board.transition(str(one), task["id"], "done", text="итог")["status"] == "done"


def test_r1_2_answer_returns_the_task_to_the_taker(stack: Stack) -> None:
    one, two = stack.birth(), stack.birth("LastOrder02")
    task = stack.op_task(to="net")
    stack.board.claim(str(one), task["id"])
    stack.board.transition(str(one), task["id"], "needs_input", text="где копать?")
    answered = stack.board.transition("operator", task["id"], "taken", text="у реки")
    assert answered["status"] == "taken" and answered["claimed_by"] == str(one)


def test_r1_2_dead_author_is_answered_by_operator_or_last_order(stack: Stack) -> None:
    author, taker = stack.birth(), stack.birth("LastOrder02")
    task = stack.board.post(str(author), kind="task", to=str(taker), text="Наведи порядок.")
    stack.board.claim(str(taker), task["id"])
    stack.board.transition(str(taker), task["id"], "needs_input", text="что с сундуком?")
    # the author is alive: the answer is his alone — not the taker's, not the operator's
    with pytest.raises(Exception) as caught:
        stack.board.transition(str(taker), task["id"], "taken", text="жду ответа")
    assert caught.value.status == 403
    with pytest.raises(Exception) as caught:
        stack.board.transition("operator", task["id"], "taken", text="сундук общий")
    assert caught.value.status == 403
    answered = stack.board.transition(str(author), task["id"], "taken", text="сундук общий")
    assert answered["claimed_by"] == str(taker)
    # the author dies mid-question: the operator and Last Order speak for him (4.3)
    stack.board.transition(str(taker), task["id"], "needs_input", text="ещё вопрос")
    stack.client.post("/registry/mark_dead", json={"serial": author})
    for actor in ("operator", "last-order"):
        answered = stack.board.transition(actor, task["id"], "taken", text="сундук общий")
        assert answered["claimed_by"] == str(taker)
        stack.board.transition(str(taker), task["id"], "needs_input", text="ещё вопрос")


def test_r1_3_last_order_cap_is_a_sliding_hour(stack: Stack) -> None:
    from runtime.hive.board import LO_MUTATIONS_PER_HOUR

    for i in range(LO_MUTATIONS_PER_HOUR):
        stack.board.post("last-order", kind="task", to="net", text=f"задача {i}")
    with pytest.raises(Exception) as caught:
        stack.board.post("last-order", kind="task", to="net", text="сверх часа")
    assert caught.value.status == 429 and caught.value.kind == "record_cap"
    stack.clock.advance(minutes=61)  # the window slides: the board opens again
    assert stack.board.post("last-order", kind="task", to="net", text="новый час")["status"] == "open"


# --- atomic claim (section 4) ----------------------------------------------------------------------


def test_ten_threads_one_winner(stack: Stack) -> None:
    task = stack.op_task(to="net")
    results: list[tuple[int, str | None]] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            stack.board.claim("last-order", task["id"])
            outcome = (200, None)
        except Exception as exc:
            outcome = (getattr(exc, "status", 0), getattr(exc, "kind", type(exc).__name__))
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    taken = [r for r in results if r == (200, None)]
    refused = [r for r in results if r != (200, None)]
    assert len(taken) == 1, results
    assert len(refused) == 9
    assert all(r == (409, "already_taken") for r in refused), refused


def test_third_taken_task_is_refused(stack: Stack) -> None:
    serial = stack.birth()
    first, second, third = (stack.op_task(to="net")["id"] for _ in range(3))
    stack.board.claim(str(serial), first)
    stack.board.claim(str(serial), second)
    with pytest.raises(Exception) as caught:
        stack.board.claim(str(serial), third)
    assert caught.value.status == 409 and caught.value.kind == "too_many_taken"
    # releasing one frees the slot again
    stack.board.transition(str(serial), first, "open", text="отпускаю")
    assert stack.board.claim(str(serial), third)["status"] == "taken"


# --- death (section 4.3) ---------------------------------------------------------------------------


def test_death_sweep_frees_everything_once(stack: Stack) -> None:
    a, other = stack.birth(), stack.birth("LastOrder02")
    net_taken = stack.op_task(to="net")
    stack.board.claim(str(a), net_taken["id"])
    personal = stack.op_task(to=str(a), text="Личное поручение.")
    request = stack.board.post(str(a), kind="request", to="operator", text="Можно факел?",
                               request={"action": "place_torch"})
    authored = stack.board.post(str(a), kind="task", to=str(other), text="Передай соседке.")

    response = stack.client.post("/registry/mark_dead", json={"serial": a})
    assert response.status_code == 200, response.text

    freed = stack.board.entry_for("operator", net_taken["id"])
    assert freed["status"] == "open" and freed["claimed_by"] is None
    assert any(r["from"] == "system" and "умер" in r["text"] for r in freed["thread"])

    moved = stack.board.entry_for("operator", personal["id"])
    assert moved["to"] == "net" and moved["readdressed_from"] == str(a)

    canceled = stack.board.entry_for("operator", request["id"])
    assert canceled["status"] == "canceled"
    assert canceled["thread"][-1]["text"] == "poster_dead"

    stays = stack.board.entry_for("operator", authored["id"])
    assert stays["status"] == "open" and stays["to"] == str(other)

    # idempotent: the retire that follows a body death sweeps nothing new
    response = stack.client.post("/registry/mark_dead", json={"serial": a})
    assert response.status_code == 200
    again = stack.board.entry_for("operator", net_taken["id"])
    assert len(again["thread"]) == len(freed["thread"])


# --- requests (section 5) --------------------------------------------------------------------------


def test_request_shape_limits(stack: Stack, sister: int) -> None:
    with pytest.raises(Exception) as caught:
        stack.board.post(str(sister), kind="request", to="operator", text="х",
                         request={"action": "a" * 65})
    assert caught.value.status == 422
    with pytest.raises(Exception) as caught:
        stack.board.post(str(sister), kind="request", to="operator", text="х",
                         request={"action": "a_thing", "args": {f"k{i}": "v" for i in range(11)}})
    assert caught.value.status == 422


def test_edit_approve_returns_the_corrected_args(stack: Stack, sister: int) -> None:
    request = stack.board.post(str(sister), kind="request", to="operator",
                               text="Копать у деревни?",
                               request={"action": "dig_where", "args": {"where": "village"}})
    moved = stack.board.transition("operator", request["id"], "approved",
                                   args={"where": "x=120 z=-40"})
    assert moved["status"] == "approved"
    assert moved["request"]["args"] == {"where": "x=120 z=-40"}


def test_respond_leaves_it_answered(stack: Stack, sister: int) -> None:
    request = stack.board.post(str(sister), kind="request", to="operator", text="Копать?",
                               request={"action": "dig_where"})
    moved = stack.board.transition("operator", request["id"], "answered",
                                   text="Сначала закончи с плотом.")
    assert moved["status"] == "answered"
    assert moved["thread"][-1]["text"] == "Сначала закончи с плотом."


def test_allowed_answers_are_enforced(stack: Stack, sister: int) -> None:
    request = stack.board.post(str(sister), kind="request", to="operator", text="Х",
                               request={"action": "a_thing", "allowed": ["approve", "deny"]})
    with pytest.raises(Exception) as caught:
        stack.board.transition("operator", request["id"], "answered", text="текст")
    assert caught.value.status == 403
    assert stack.board.transition("operator", request["id"], "denied", text="нет")["status"] == "denied"


# --- limits (section 6) ----------------------------------------------------------------------------


def test_sixth_message_to_operator_is_429(stack: Stack) -> None:
    serial = stack.birth()
    for i in range(OPERATOR_MSGS_PER_LIFE):
        stack.board.post(str(serial), kind="message", to="operator", text=f"донесение {i}")
    with pytest.raises(Exception) as caught:
        stack.board.post(str(serial), kind="message", to="operator", text="шестое")
    assert caught.value.status == 429 and caught.value.kind == "operator_cap"
    # tasks to the network are not messages: still fine
    stack.board.post(str(serial), kind="task", to="net", text="ещё задача")
    # a new life (a new serial in the slot) starts the message cap over
    reborn = stack.birth()
    assert reborn != serial
    stack.board.post(str(reborn), kind="message", to="operator", text="первое с новой жизни")


def test_agent_write_cap_is_30_per_life(stack: Stack) -> None:
    serial = stack.birth()
    for i in range(AGENT_MUTATIONS_PER_LIFE):
        stack.board.post(str(serial), kind="task", to="net", text=f"задача {i}")
    with pytest.raises(Exception) as caught:
        stack.board.post(str(serial), kind="task", to="net", text="сверх лимита")
    assert caught.value.status == 429 and caught.value.kind == "record_cap"


def test_text_length_caps_refuse_without_cutting(stack: Stack) -> None:
    serial = stack.birth()
    entry = stack.board.post(str(serial), kind="task", to="net", text="ы" * 800)
    assert len(entry["text"]) == 800  # the cap itself lets the full text through
    with pytest.raises(Exception) as caught:
        stack.board.post(str(serial), kind="task", to="net", text="ы" * 801)
    assert caught.value.status == 422
    task = stack.board.post(str(serial), kind="task", to="net", text="задача с репликой")
    with pytest.raises(Exception) as caught:
        stack.board.reply(str(serial), task["id"], "ы" * 401)
    assert caught.value.status == 422


# --- alarms (section 7) -----------------------------------------------------------------------------


class RunnerStack(Stack):
    """A stand with the runner-key gate on: the alarms arrive as the stand sends them."""

    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path, runner_keys={RKEY1: "LastOrder01"})


@pytest.fixture()
def rstack(tmp_path: Path):
    return RunnerStack(tmp_path)


def test_runner_alarm_repeats_fold_then_expire(rstack: RunnerStack) -> None:
    serial = rstack.birth()
    body = {"type": "stuck", "text": "узел не отвечает", "slot": "LastOrder01", "serial": serial}
    first = rstack.client.post("/board/alarms", json=body, headers={"X-Runner-Key": RKEY1})
    assert first.status_code == 200, first.text
    assert first.json()["from"] == "runner:LastOrder01"
    rstack.clock.advance(minutes=3)
    second = rstack.client.post("/board/alarms", json=body, headers={"X-Runner-Key": RKEY1})
    assert second.status_code == 200
    assert second.json()["alarm"]["count"] == 2
    assert len(rstack.board.entries(kind="alarm")) == 1
    # the window slides from the LAST occurrence: 11 quiet minutes open a new entry
    rstack.clock.advance(minutes=11)
    third = rstack.client.post("/board/alarms", json=body, headers={"X-Runner-Key": RKEY1})
    assert third.status_code == 200
    assert len(rstack.board.entries(kind="alarm")) == 2


def test_alarm_rights(rstack: RunnerStack) -> None:
    serial = rstack.birth()
    # admin rides the Last Order key: harness_failover is its type
    admin = rstack.client.post("/board/alarms", json={"type": "harness_failover", "text": "failover"},
                               headers={"X-Node-Key": KEY_LO})
    assert admin.status_code == 200, admin.text
    assert admin.json()["from"] == "admin"
    # a node key raises delivery_failing alone
    ok = rstack.client.post("/board/alarms", json={"type": "delivery_failing", "text": "доставка"},
                            headers={"X-Node-Key": KEY1})
    assert ok.status_code == 200, ok.text
    for bad_type in ("stuck", "quota", "harness_failover"):
        refused = rstack.client.post("/board/alarms", json={"type": bad_type, "text": "x",
                                                            "slot": "LastOrder01", "serial": serial},
                                     headers={"X-Node-Key": KEY1})
        assert refused.status_code == 403, (bad_type, refused.text)
    no_key = rstack.client.post("/board/alarms", json={"type": "stuck", "text": "x"})
    assert no_key.status_code == 401
    alarm_id = ok.json()["id"]
    acked = rstack.board.transition("operator", alarm_id, "acked")
    assert acked["status"] == "acked"


# --- journal and events (section 8) ------------------------------------------------------------------


def test_every_mutation_is_journaled_and_logged_without_text(stack: Stack, sister: int) -> None:
    task = stack.op_task(to="net")
    stack.board.claim(str(sister), task["id"])
    stack.board.reply(str(sister), task["id"], "начал")
    rows = stack.board.log_rows()
    ops = [r["op"] for r in rows]
    assert ops[0] == "post" and "claim" in ops and "reply" in ops
    assert rows[0]["after"]["kind"] == "task" and rows[0]["before"] is None
    claim_row = next(r for r in rows if r["op"] == "claim")
    assert claim_row["before"]["status"] == "open" and claim_row["after"]["status"] == "taken"
    board_events = [e for e in stack.log.read() if e["type"].startswith("board.")]
    assert {e["type"] for e in board_events} >= {"board.post", "board.claim", "board.reply"}
    for event in board_events:
        assert "text" not in event["payload"], event  # text never rides the event log
    for with_len in ("board.post", "board.reply"):
        event = next(e for e in board_events if e["type"] == with_len)
        assert "len" in event["payload"]


def test_restart_keeps_the_board(tmp_path: Path) -> None:
    first = Stack(tmp_path)
    serial = first.birth()
    task = first.op_task(to="net")
    first.board.claim(str(serial), task["id"])

    second = Stack(tmp_path)  # the same run dir: a fresh create_app over the same SQLite
    kept = second.board.entries(kind="task", status="taken")
    assert [e["id"] for e in kept] == [task["id"]]
    assert kept[0]["claimed_by"] == str(serial)


# --- the feed (section 9) ----------------------------------------------------------------------------


def test_feed_delivers_own_and_net_not_own_actions(stack: Stack) -> None:
    one, two = stack.birth(), stack.birth("LastOrder02")
    net_task = stack.op_task(to="net", text="общая задача")
    personal = stack.op_task(to=str(one), text="личная задача")

    feed = stack.node("GET", "/board/feed", key=KEY1).json()
    assert feed["snapshot"] is True
    assert {e["entry_id"] for e in feed["events"]} == {net_task["id"], personal["id"]}
    cursor = feed["cursor"]

    stack.board.post(str(one), kind="task", to="net", text="моя правка не мне")
    stack.node("POST", "/board/ack", {"seq": cursor}, key=KEY1)
    own = stack.node("GET", "/board/feed", key=KEY1).json()
    assert own["events"] == []  # her own post never comes back to her

    stack.board.claim(str(one), net_task["id"])
    stack.op("POST", f"/board/entries/{net_task['id']}/transition",
             {"to_status": "canceled", "text": ""})
    stranger = stack.op_task(to="net", text="чужая")
    stack.op("POST", f"/board/entries/{stranger['id']}/transition",
             {"to_status": "canceled", "text": ""})
    delivered = stack.node("GET", "/board/feed", key=KEY1).json()
    events = delivered["events"]
    # the new net task itself arrives (to=net is hers to see); its CANCEL does not:
    # she is neither its author nor its taker
    assert any(e["op"] == "post" and e["entry_id"] == stranger["id"] for e in events)
    assert not [e for e in events if e["op"] == "transition" and e["entry_id"] == stranger["id"]]
    # the cancel of the task she claimed reaches her
    assert any(e["op"] == "transition" and e["entry_id"] == net_task["id"] for e in events)


def test_new_life_gets_open_net_tasks_once(stack: Stack) -> None:
    a = stack.birth()
    readdressed = stack.op_task(to=str(a), text="поручение умершему")
    fresh = stack.op_task(to="net", text="новая общая")
    stack.client.post("/registry/mark_dead", json={"serial": a})
    b = stack.birth()  # the same slot, a new serial and a new life

    feed = stack.node("GET", "/board/feed", key=KEY1).json()
    assert feed["snapshot"] is True
    assert {e["entry_id"] for e in feed["events"]} == {readdressed["id"], fresh["id"]}
    # at-least-once (R1-6): until the snapshot is acked, it is re-sent, not lost
    again = stack.node("GET", "/board/feed", key=KEY1).json()
    assert again["snapshot"] is True and again["events"]
    stack.node("POST", "/board/ack", {"seq": feed["cursor"]}, key=KEY1)
    settled = stack.node("GET", "/board/feed", key=KEY1).json()
    assert settled["events"] == []  # the ack moved the cursor; the snapshot is done


def test_operator_feed_sees_everything(stack: Stack) -> None:
    serial = stack.birth()
    stack.op_task(to="net")
    stack.board.post(str(serial), kind="message", to="operator", text="донесение")
    feed = stack.op("GET", "/board/feed").json()
    kinds = [e["entry"]["kind"] for e in feed["events"]]
    assert kinds == ["task", "message"]


# --- raw ids ------------------------------------------------------------------------------------------


def test_raw_game_ids_never_enter_the_board(stack: Stack, sister: int) -> None:
    with pytest.raises(Exception) as caught:
        stack.board.post(str(sister), kind="task", to="net", text="мимо ползёт minecraft:creeper")
    assert caught.value.status == 422
    task = stack.op_task(to="net")
    with pytest.raises(Exception) as caught:
        stack.board.reply(str(sister), task["id"], "вижу minecraft:zombie")
    assert caught.value.status == 422
    # the request payload is agent text too (R1-7): why and args values, same rule
    with pytest.raises(Exception) as caught:
        stack.board.post(str(sister), kind="request", to="operator", text="х",
                         request={"action": "a_thing", "why": "вижу minecraft:spider"})
    assert caught.value.status == 422
    with pytest.raises(Exception) as caught:
        stack.board.post(str(sister), kind="request", to="operator", text="х",
                         request={"action": "a_thing", "args": {"where": "minecraft:chest"}})
    assert caught.value.status == 422
