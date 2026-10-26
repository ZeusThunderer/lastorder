"""The board tools over the real MCP transport and the operator SSE stream (T-042).

The full app (`create_app`) runs under uvicorn on port 0 in a thread, the same harness
as `tests/test_hive_mcp.py`: the sisters' `/node/mcp` and Last Order's `/last-order/mcp`
carry the board tool set of TASKBOARD.md section 10, and the operator's `/board/stream`
is read the way the page of T-049 will read it.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from runtime.hive.app import create_app
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.store import KnowledgeStore
from runtime.lastorder.traits import Traits

T0 = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
KEY1 = "sk-node-one"  # slot LastOrder01
KEY2 = "sk-node-two"  # slot LastOrder02
KEY_LO = "sk-last-order"
KEY_OP = "op-stand-key"
OP = {"X-Operator-Key": KEY_OP}


class FakeClock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeRcon:
    def kill(self, player: str) -> str:
        return "Killed LastOrder01"


def traits(seed: int) -> Traits:
    return Traits(
        bravery=3, greed=7, talkativeness=5, trust_in_chat_and_nodes=4, superstition=9,
        quirk="считает крипера родственником", seed=seed,
    )


class Stack:
    def __init__(self, tmp_path: Path):
        self.clock = FakeClock()
        self.rcon = FakeRcon()
        self.store = KnowledgeStore(tmp_path / "kb", git=False, now=lambda: self.clock().isoformat())
        self.run_dir = tmp_path / "run"
        self.log = HiveLog(self.run_dir / "events.jsonl")
        self.app = create_app(
            store=self.store, rcon=self.rcon, log=self.log,
            run_dir=self.run_dir, clock=self.clock,
            node_keys={KEY1: "LastOrder01", KEY2: "LastOrder02"},
            lo_key=KEY_LO, operator_key=KEY_OP, runner_keys={}, stream_poll_s=0.05,
        )
        self.board = self.app.state.board
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error")
        self.uvicorn = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.uvicorn.run, daemon=True)
        self.thread.start()
        for _ in range(500):
            if self.uvicorn.started:
                break
            time.sleep(0.02)
        self.port = self.uvicorn.servers[0].sockets[0].getsockname()[1]

    def http(self, method: str, path: str, json_body: dict | None = None) -> httpx.Response:
        return httpx.request(
            method, f"http://127.0.0.1:{self.port}{path}", json=json_body,
            headers=OP, timeout=30.0,
        )

    def birth(self, slot: str) -> int:
        response = httpx.post(
            f"http://127.0.0.1:{self.port}/registry/birth",
            json={"slot": slot, "traits": traits(hash(slot) % 1000).as_dict()},
            timeout=30.0,
        )
        assert response.status_code == 200, response.text
        return int(response.json()["serial"])

    def node_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/node/mcp"

    def lo_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/last-order/mcp"

    def call(self, url: str, headers: dict, fn):
        return asyncio.run(self._call(url, headers, fn))

    async def _call(self, url: str, headers: dict, fn):
        http = httpx.AsyncClient(headers=headers, timeout=30.0)
        try:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                return await fn(client)
        finally:
            await http.aclose()

    def close(self) -> None:
        self.uvicorn.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture()
def stack(tmp_path: Path) -> Stack:
    s = Stack(tmp_path)
    try:
        yield s
    finally:
        s.close()


def node_call(stack: Stack, key: str, name: str, args: dict) -> dict:
    async def run(client: Client) -> dict:
        result = await client.call_tool(name, args)
        text = result.content[0].text
        assert not result.is_error, text
        return json.loads(text)

    return stack.call(stack.node_url(), {"X-Node-Key": key}, run)


def node_error(stack: Stack, key: str, name: str, args: dict) -> str:
    async def run(client: Client) -> str:
        result = await client.call_tool(name, args)
        assert result.is_error, f"expected a tool error, got {result.content[0].text}"
        return result.content[0].text

    return stack.call(stack.node_url(), {"X-Node-Key": key}, run)


def lo_call(stack: Stack, name: str, args: dict) -> dict:
    async def run(client: Client) -> dict:
        result = await client.call_tool(name, args)
        text = result.content[0].text
        assert not result.is_error, text
        return json.loads(text)

    return stack.call(stack.lo_url(), {"X-Node-Key": KEY_LO}, run)


# --- board(filter) (section 10) ----------------------------------------------------------------


def test_board_glance_is_capped_and_short(stack: Stack) -> None:
    stack.birth("LastOrder01")
    long_text = "Построить общее убежище у спавна, покрыть крышу, зажечь освещение." * 8
    for i in range(20):  # the operator has no write cap
        response = stack.http("POST", "/board/entries",
                              {"kind": "task", "to": "net", "text": f"{i} {long_text}"})
        assert response.status_code == 200, response.text
    glance = node_call(stack, KEY1, "board", {"filter": "net"})
    lines = glance["entries"]
    assert len(lines) == 15  # <= 15 rows: a glance, not a dump
    for line in lines:
        head, _, text = line.partition(": ")
        assert head.startswith("#") and " task " in head
        assert len(text) <= 120, line  # the text cut


def test_board_all_is_last_order_alone(stack: Stack) -> None:
    stack.birth("LastOrder01")
    message = node_error(stack, KEY1, "board", {"filter": "all"})
    assert "Last Order" in message
    stack.http("POST", "/board/entries", {"kind": "task", "to": "net", "text": "летопись"})
    chronicle = lo_call(stack, "board", {"filter": "all"})
    assert any("летопись" in line for line in chronicle["entries"])


def test_board_by_id_is_the_full_entry(stack: Stack) -> None:
    serial = stack.birth("LastOrder01")
    posted = stack.http("POST", "/board/entries",
                        {"kind": "task", "to": str(serial), "text": "Личное дело."})
    entry_id = posted.json()["id"]
    full = node_call(stack, KEY1, "board", {"filter": str(entry_id)})
    assert full["entry"]["id"] == entry_id
    assert full["entry"]["thread"] == []


# --- the write path over MCP (section 10) -------------------------------------------------------


def test_mcp_board_roundtrip(stack: Stack) -> None:
    author, taker = stack.birth("LastOrder01"), stack.birth("LastOrder02")

    posted = node_call(stack, KEY1, "post",
                       {"kind": "task", "to": "net", "text": "Собрать тростник у болота."})
    entry_id = posted["id"]
    full = node_call(stack, KEY1, "board", {"filter": str(entry_id)})
    assert full["entry"]["from"] == str(author)  # the key stamped the serial, not a body field

    claimed = node_call(stack, KEY2, "claim", {"id": entry_id})
    assert claimed["status"] == "taken" and claimed["claimed_by"] == str(taker)

    reply = node_call(stack, KEY1, "reply", {"id": entry_id, "text": "тростник у юга"})
    assert reply["seq"] == 1

    done = node_call(stack, KEY2, "update",
                     {"id": entry_id, "status": "done", "text": "собрано два стака"})
    assert done["status"] == "done"

    # the author's feed carries the claim and the done move: her cursor was set by the
    # first read (the snapshot of a new life — her own post is not in it, by design)
    first = httpx.get(f"http://127.0.0.1:{stack.port}/board/feed",
                      headers={"X-Node-Key": KEY1}, timeout=30.0)
    assert first.status_code == 200, first.text
    assert first.json()["snapshot"] is True and first.json()["events"] == []
    feed = httpx.get(f"http://127.0.0.1:{stack.port}/board/feed",
                     headers={"X-Node-Key": KEY1}, timeout=30.0)
    ops = [e["op"] for e in feed.json()["events"] if e["entry_id"] == entry_id]
    assert "claim" in ops and "transition" in ops


def test_mcp_post_refuses_the_wrong_shapes(stack: Stack) -> None:
    stack.birth("LastOrder01")
    message = node_error(stack, KEY1, "post",
                         {"kind": "message", "to": "net", "text": "нет права"})
    assert "forbidden" in message


# --- the operator SSE stream (section 9) ---------------------------------------------------------


def test_stream_emits_events_for_new_records(stack: Stack) -> None:
    stack.birth("LastOrder01")
    with httpx.Client(timeout=httpx.Timeout(15.0, read=10.0)) as http:
        with http.stream("GET", f"http://127.0.0.1:{stack.port}/board/stream",
                         headers=OP) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.iter_lines()
            assert next(lines) == "retry: 3000"  # the stream is live before the post lands
            posted = stack.http("POST", "/board/entries",
                                {"kind": "task", "to": "net", "text": "стрим проба"})
            assert posted.status_code == 200
            data = None
            for line in lines:  # bounded by the read timeout above
                if line.startswith("data:"):
                    data = json.loads(line[len("data:"):])
                    break
            assert data is not None and data["op"] == "post"
            assert data["entry_id"] == posted.json()["id"]
            assert data["actor"] == "operator"
