"""The grant vault (T-046, ADR-041, TASKBOARD.md section 5.3): every acceptance item.

`request_grant` runs over the real MCP transport of Last Order's server (the same
harness as tests/test_hive_board_mcp.py); approvals, the vault routes and the
no-leak greps run against the HTTP surface with the operator and tool keys set.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from runtime.hive.app import create_app
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.store import KnowledgeStore

T0 = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
KEY_LO = "sk-last-order"
KEY_OP = "op-stand-key"
KEY_TOOL = "sk-tool-process"
OP = {"X-Operator-Key": KEY_OP}
TOOL = {"X-Tool-Key": KEY_TOOL}
SECRET_VALUE = "sk-stand-secret-value-Q046"


class FakeClock:
    def __init__(self, now: datetime = T0):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeRcon:
    def kill(self, player: str) -> str:
        return "Killed LastOrder01"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Stack:
    """TestClient for the HTTP surface + the real MCP transport for LO's tools."""

    def __init__(self, tmp_path: Path, *, tool_key: str | None = KEY_TOOL):
        self.clock = FakeClock()
        self.run_dir = tmp_path / "run"
        self.app = create_app(
            store=KnowledgeStore(tmp_path / "kb", git=False,
                                 now=lambda: self.clock().isoformat()),
            rcon=FakeRcon(),
            log=HiveLog(self.run_dir / "events.jsonl"),
            run_dir=self.run_dir,
            clock=self.clock,
            node_keys={"sk-node-one": "LastOrder01"},
            lo_key=KEY_LO,
            operator_key=KEY_OP,
            runner_keys={},
            tool_key=tool_key,
        )
        self.board = self.app.state.board
        self.grants = self.app.state.grants
        self.client = TestClient(self.app)
        self.port = free_port()
        self.uvicorn = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1",
                                                     port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.uvicorn.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=1.0)
                break
            except httpx.HTTPError:
                time.sleep(0.05)

    def lo_call(self, name: str, args: dict) -> dict:
        async def run(client: Client) -> dict:
            result = await client.call_tool(name, args)
            text = result.content[0].text
            assert not result.is_error, text
            return json.loads(text)

        return asyncio.run(self._call(run))

    def lo_error(self, name: str, args: dict) -> str:
        async def run(client: Client) -> str:
            result = await client.call_tool(name, args)
            assert result.is_error, f"expected a refusal, got {result.content[0].text}"
            return result.content[0].text

        return asyncio.run(self._call(run))

    async def _call(self, fn):
        http = httpx.AsyncClient(headers={"X-Node-Key": KEY_LO}, timeout=30.0)
        try:
            url = f"http://127.0.0.1:{self.port}/last-order/mcp"
            async with Client(streamable_http_client(url, http_client=http)) as client:
                return await fn(client)
        finally:
            await http.aclose()

    def close(self) -> None:
        self.uvicorn.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture()
def stack(tmp_path: Path):
    s = Stack(tmp_path)
    try:
        yield s
    finally:
        s.close()


def request_grant(stack: Stack, kind: str, what: str, why: str = "why not") -> int:
    return stack.lo_call("request_grant", {"kind": kind, "what": what, "why": why})["id"]


def approve(stack: Stack, entry_id: int, **extra) -> object:
    return stack.client.post(f"/board/entries/{entry_id}/transition",
                             headers=OP, json={"to_status": "approved", **extra})


# --- the request side ------------------------------------------------------------------------


def test_request_grant_posts_a_board_request(stack: Stack) -> None:
    entry_id = request_grant(stack, "egress", "api.example.com", "для инструмента связи")
    entry = stack.client.get(f"/board/entries/{entry_id}", headers=OP).json()
    assert entry["from"] == "last-order" and entry["to"] == "operator"
    assert entry["request"]["action"] == "grant"
    assert entry["request"]["allowed"] == ["approve", "deny"]
    assert entry["grant"] == {"kind": "egress", "what": "api.example.com"}


def test_request_grant_input_rules(stack: Stack) -> None:
    assert "kind must be" in stack.lo_error("request_grant", {"kind": "money", "what": "x"})
    assert "plain domain" in stack.lo_error(
        "request_grant", {"kind": "egress", "what": "https://api.example.com/x"})
    assert "snake_case" in stack.lo_error(
        "request_grant", {"kind": "secret", "what": "Telegram Token"})
    # a sister's key does not open LO's tools at all — the refusal is structural
    listed = stack.lo_call("grants", {})
    assert listed == {"grants": []}


# --- the approval side -----------------------------------------------------------------------


def test_approval_creates_the_grant_and_keeps_the_secret_hidden(stack: Stack) -> None:
    entry_id = request_grant(stack, "secret", "telegram_token", "выход в канал")
    response = approve(stack, entry_id, secret=SECRET_VALUE)
    assert response.status_code == 200, response.text
    records = stack.grants.list()
    assert [r["kind"] for r in records] == ["secret"]
    assert records[0]["what"] == "telegram_token" and records[0]["status"] == "approved"
    assert "secret" not in records[0]  # the public shape never carries the value

    # the vault hands the value to the tool process alone
    pulled = stack.client.get("/grants/secret", params={"name": "telegram_token"},
                              headers=TOOL)
    assert pulled.status_code == 200 and pulled.json()["secret"] == SECRET_VALUE

    # the leak greps: board journal, events, every answer the board and MCP give
    dumped = str(sqlite3.connect(stack.run_dir / "board.sqlite")
                 .execute("SELECT * FROM board_log").fetchall())
    events = (stack.run_dir / "events.jsonl").read_text(encoding="utf-8")
    entry_view = json.dumps(stack.client.get(f"/board/entries/{entry_id}",
                                             headers=OP).json(), ensure_ascii=False)
    grants_view = json.dumps(stack.lo_call("grants", {}))
    for where, text in (("board_log", dumped), ("events.jsonl", events),
                        ("entry", entry_view), ("grants()", grants_view)):
        assert SECRET_VALUE not in text, where


def test_deny_creates_no_grant(stack: Stack) -> None:
    entry_id = request_grant(stack, "egress", "api.example.com")
    denied = stack.client.post(f"/board/entries/{entry_id}/transition",
                               headers=OP, json={"to_status": "denied", "text": "не надо"})
    assert denied.status_code == 200
    assert stack.grants.list() == []


def test_hook_failure_leaves_the_entry_approved_without_access(stack: Stack) -> None:
    def broken_hook(entry_id: int, grant: dict, actor: str, secret) -> None:
        raise RuntimeError("vault down")

    entry_id = request_grant(stack, "egress", "api.example.com")
    stack.board.set_grant_hook(broken_hook)
    response = approve(stack, entry_id)
    assert response.status_code == 200  # the board keeps its answer (5.3)
    assert response.json()["status"] == "approved"
    assert stack.grants.list() == []          # grants() shows nothing
    assert stack.grants.egress_domains() == []  # and the proxy list stays closed
    assert "grants.hook_failed" in (stack.run_dir / "events.jsonl").read_text()


def test_secret_field_rules(stack: Stack) -> None:
    secret_id = request_grant(stack, "secret", "telegram_token")
    no_value = approve(stack, secret_id)
    assert no_value.status_code == 422
    egress_id = request_grant(stack, "egress", "api.example.com")
    with_value = approve(stack, egress_id, secret=SECRET_VALUE)
    assert with_value.status_code == 422
    denied_id = request_grant(stack, "secret", "another_token")
    on_deny = stack.client.post(f"/board/entries/{denied_id}/transition", headers=OP,
                                json={"to_status": "denied", "text": "нет",
                                      "secret": SECRET_VALUE})
    assert on_deny.status_code == 422


# --- revocation ------------------------------------------------------------------------------


def test_revoke_closes_the_domain_and_notes_the_entry(stack: Stack) -> None:
    entry_id = request_grant(stack, "egress", "api.example.com")
    assert approve(stack, entry_id).status_code == 200
    assert stack.client.get("/grants/egress", headers=TOOL).json() == \
        {"domains": ["api.example.com"]}
    revoked = stack.client.post("/grants/revoke", headers=OP,
                                json={"kind": "egress", "what": "api.example.com"})
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert stack.client.get("/grants/egress", headers=TOOL).json() == {"domains": []}
    entry = stack.client.get(f"/board/entries/{entry_id}", headers=OP).json()
    assert entry["status"] == "approved"  # the board keeps its history (5.3)
    assert entry["thread"][-1]["text"] == "грант отозван"
    unknown = stack.client.post("/grants/revoke", headers=OP,
                                json={"kind": "egress", "what": "no.example"})
    assert unknown.status_code == 404


# --- the vault's own gates -------------------------------------------------------------------


def test_the_grants_api_needs_the_tool_key(tmp_path: Path) -> None:
    stack = Stack(tmp_path)  # key configured
    for headers in ({}, {"X-Tool-Key": "wrong"}, {"X-Operator-Key": KEY_OP}):
        response = stack.client.get("/grants/egress", headers=headers)
        assert response.status_code == 401, headers
    stack.close()

    keyless = Stack(tmp_path / "two", tool_key="")  # nothing configured: closed too
    assert keyless.client.get("/grants/egress", headers={"X-Tool-Key": "x"}).status_code == 401
    keyless.close()
