"""The minimal body MCP server of the harness spike (T-024).

Everything runs against a fake body with the real Py4jBody method surface; the MCP layer is
exercised through the real streamable HTTP transport (uvicorn on port 0 in a thread), because
that is what OpenCode and ZCode will talk to. Checks the acceptance items one by one: the tool
set, the `command` rejection, `say` guards, the uc service-line filter with plain names, and
`wait_events` by timeout and by event — plus the death -> respawn path and the JSONL call log
(review P1-2…P1-5, P1-8).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
import time
import uuid
from datetime import datetime

import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from runtime.mind.mcp_body import (
    SAY_MAX_LEN,
    JsonlCallLog,
    ToolError,
    build_server,
    check_say,
    validate_args,
)
from runtime.ports import BlockInfo, EntityInfo, Event, ItemStack


class FakeBody:
    """The Py4jBody surface; scripted by each test."""

    def __init__(self) -> None:
        self._state = SimpleState()
        self.chat: list[str] = []
        self.acts: list[tuple[str, dict]] = []
        self.busy_flag = False
        self.respawn_calls = 0
        self._events: list[Event] = []
        self._seq = 0

    def state(self) -> SimpleState:
        return self._state

    def set_state(self, **changes) -> None:
        self._state = dataclasses.replace(self._state, **changes)

    def push_event(self, type_: str, text: str = "", service: bool = False) -> None:
        self._seq += 1
        self._events.append(
            Event(
                id=f"{type_}#{self._seq}",
                ts_mono=time.monotonic(),
                ts_wall=datetime.now().isoformat(),
                source="body",
                type=type_,
                payload={"text": text, "service": service},
            )
        )

    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def entities(self, radius: int = 16):
        return (
            EntityInfo(kind="hostile", name="minecraft:zombie", dist_m=3.5, pos=(1, 64, 1)),
            EntityInfo(kind="animal", name="Cow", dist_m=8.0, pos=(5, 64, 5)),
        )

    def blocks_around(self, radius: int = 4):
        return (BlockInfo(name="minecraft:grass_block", pos=(0, 63, 0)),)

    def inventory(self):
        return (ItemStack(name="minecraft:wooden_axe", count=1, slot=0),)

    def recent_chat(self, lines: int = 5):
        return tuple(self.chat[-lines:])

    def act(self, lever: str, args: dict | None = None) -> None:
        self.acts.append((lever, dict(args or {})))

    def busy(self) -> bool:
        return self.busy_flag

    def stop(self) -> None:
        self.busy_flag = False
        self.acts.append(("__stop__", {}))

    def respawn(self, timeout: float = 60.0, poll: float = 1.0):
        self.respawn_calls += 1
        self.set_state(hp=20.0)


@dataclasses.dataclass(frozen=True)
class SimpleState:
    in_game: bool = True
    hp: float = 20.0
    food: float = 20.0
    pos: tuple = (0.0, 64.0, 0.0)
    held: str = "minecraft:wooden_axe"
    looking_at: str = "minecraft:zombie"
    max_hp: float = 20.0
    armor: float = 0.0
    saturation: float = 5.0
    on_ground: bool = True
    dimension: str = "overworld"
    yaw: float = 0.0
    pitch: float = 0.0
    vel: tuple = (0.0, 0.0, 0.0)

    @property
    def dead(self) -> bool:
        return self.in_game and self.hp <= 0.0


class Stack:
    """The whole spike stack in-process: fake body, server, uvicorn, a client call helper."""

    def __init__(self, body: FakeBody | None = None, respawn_delay: float = 0.3):
        self.body = body or FakeBody()
        self.calls = JsonlCallLog()
        self._tmp = f".mcp-body-test-{uuid.uuid4().hex[:8]}.jsonl"
        self.calls_file = JsonlCallLog(self._tmp)
        server, self.impl = build_server(
            self.body,
            username="LastOrder01",
            call_log=self.calls_file,
            poll_interval=0.05,
            respawn_delay=respawn_delay,
            start_poller=True,
        )
        app = server.streamable_http_app(stateless_http=True, streamable_http_path="/mcp")
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
        self.uvicorn = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.uvicorn.run, daemon=True)
        self.thread.start()
        for _ in range(500):
            if self.uvicorn.started:
                break
            time.sleep(0.02)
        self.port = self.uvicorn.servers[0].sockets[0].getsockname()[1]

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def call(self, coro_fn):
        return asyncio.run(self._call(coro_fn))

    async def _call(self, coro_fn):
        async with Client(streamable_http_client(self.url())) as client:
            return await coro_fn(client)

    def log_lines(self) -> list[dict]:
        with self.calls_file._lock:
            return [json.loads(line) for line in open(self._tmp, encoding="utf-8")]

    def close(self) -> None:
        self.uvicorn.should_exit = True
        self.thread.join(timeout=5)
        self.impl.stop_poller()
        self.calls_file.close()
        import os

        os.unlink(self._tmp)


@pytest.fixture()
def stack() -> Stack:
    s = Stack()
    try:
        yield s
    finally:
        s.close()


def parse(result) -> dict:
    assert not result.is_error, [b.text for b in result.content]
    return json.loads(result.content[0].text)


def error_text(result) -> str:
    assert result.is_error, "expected a tool error"
    return result.content[0].text


# --- pure validation -------------------------------------------------------------------


def test_validate_args_plain_item_and_ranges() -> None:
    assert validate_args("get", {"item": "oak log", "count": 8}) == {"item": "oak_log", "count": 8}
    assert validate_args("equip", {"item": "minecraft:iron_pickaxe"}) == {"item": "iron_pickaxe"}
    assert validate_args("goto", {"x": 10, "y": 64, "z": -5}) == {"x": 10, "y": 64, "z": -5}
    assert validate_args("select_hotbar", {"slot": 3}) == {"slot": 3}
    assert validate_args("mine", {"positions": [[0, 63, 0], [1, 63, 0]]}) == {
        "positions": [[0, 63, 0], [1, 63, 0]]
    }
    assert validate_args("tap_key", {"key": "Space "}) == {"key": "space"}


@pytest.mark.parametrize(
    "lever,args",
    [
        ("command", {"cmd": "goto 0 0 0"}),
        ("chat", {"text": "hi"}),
        ("get", {"item": "dirt; @gamer"}),
        ("get", {"item": "dirt\n@goto 0 0 0"}),
        ("get", {"item": "minecraft:dirt 1 x"}),
        ("get", {"item": "dirt", "count": 999}),
        ("goto", {"x": 10, "y": 64}),  # missing z
        ("goto", {"x": 10, "y": 64, "z": 1, "speed": 5}),  # unknown arg
        ("tap_key", {"key": "t"}),  # opens chat — not in the allowlist
        ("hold_key", {"key": "w", "ms": 999999}),
        ("select_hotbar", {"slot": 9}),
        ("fly", {}),
    ],
)
def test_validate_args_rejections(lever: str, args: dict) -> None:
    with pytest.raises(ToolError):
        validate_args(lever, args)


@pytest.mark.parametrize(
    "text",
    [
        " /kill",  # leading space then a command
        "привет\n/op",
        "§kx",
        "привет @get dirt",
        "#goto 0 0 0",
        "x" * (SAY_MAX_LEN + 1),
        "",
    ],
)
def test_check_say_rejections(text: str) -> None:
    with pytest.raises(ToolError):
        check_say(text)


def test_check_say_accepts_plain_line() -> None:
    assert check_say("  Иду на север. ") == "Иду на север."


# --- over MCP --------------------------------------------------------------------------


def test_tool_set_is_exactly_the_spike_five(stack: Stack) -> None:
    names = stack.call(lambda c: c.list_tools())

    def pick(tools):
        return sorted(t.name for t in tools.tools)

    assert pick(names) == ["act", "observe", "say", "stop", "wait_events"]


def test_act_over_mcp_returns_intent_id_and_reaches_body(stack: Stack) -> None:
    stack.body.busy_flag = True
    result = stack.call(lambda c: c.call_tool("act", {"intent": "goto", "args": {"x": 5, "y": 64, "z": 5}}))
    assert parse(result) == {"intent_id": 1, "busy": True}
    assert stack.body.acts == [("goto", {"x": 5, "y": 64, "z": 5})]


def test_act_command_is_rejected_over_mcp(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("act", {"intent": "command", "args": {"cmd": "stop"}}))
    assert "not available" in error_text(result)
    result = stack.call(lambda c: c.call_tool("act", {"intent": "chat", "args": {"text": "hi"}}))
    assert "not available" in error_text(result)
    assert stack.body.acts == []


def test_say_guards_over_mcp(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("say", {"text": " /kill"}))
    assert "server commands" in error_text(result)
    result = stack.call(lambda c: c.call_tool("say", {"text": "привет, @get dirt"}))
    assert "'@'" in error_text(result)
    result = stack.call(lambda c: c.call_tool("say", {"text": "первая реплика узла"}))
    assert parse(result)["said"] is True
    result = stack.call(lambda c: c.call_tool("say", {"text": "вторая реплика сразу"}))
    assert "too fast" in error_text(result)
    sent = [args for lever, args in stack.body.acts if lever == "chat"]
    assert sent == [{"text": "первая реплика узла"}]


def test_observe_filters_service_lines_and_plain_names(stack: Stack) -> None:
    stack.body.chat = [
        "§7[Alto Clef]§r task started: @get birch_log 8",
        "<LastOrder01> привет",
        "§7[Tungsten]§r path found",
        "LastOrder01 was slain by Zombie",
        "minecraft birch logs are nice",
    ]
    obs = parse(stack.call(lambda c: c.call_tool("observe", {})))
    assert obs["chat"] == [
        "<LastOrder01> привет",
        "LastOrder01 was slain by Zombie",
        "minecraft birch logs are nice",
    ]
    joined = json.dumps(obs, ensure_ascii=False)
    assert "[Alto Clef]" not in joined and "[Tungsten]" not in joined
    # plain mode (ADR-037): a stable key plus the readable name, never a raw `minecraft:` id
    assert obs["self"]["held"] == "itm:wooden_axe wooden axe"
    assert obs["entities"][0]["name"] == "ent:zombie zombie"
    assert obs["blocks"][0]["name"] == "blk:grass_block grass block"
    assert obs["inventory"][0]["name"] == "itm:wooden_axe wooden axe"
    assert obs["self"]["hp"] == 20.0


def test_wait_events_timeout_and_event(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 0.3}))
    assert parse(result) == {"type": "timeout", "timeout_s": 0.3}

    stack.body.push_event("chat.line", "<LastOrder01> реплика")
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0}))
    assert parse(result) == {"type": "chat.line", "text": "<LastOrder01> реплика"}


def test_death_event_then_single_respawn(stack: Stack) -> None:
    stack.body.set_state(hp=0.0)
    stack.body.chat = ["LastOrder01 was slain by Zombie"]
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0}))
    assert parse(result)["type"] == "game.death"
    assert "slain by Zombie" in parse(result)["text"]
    deadline = time.monotonic() + 5.0
    while stack.body.respawn_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stack.body.respawn_calls == 1
    time.sleep(0.4)  # a second poll cycle must not respawn again
    assert stack.body.respawn_calls == 1


def test_call_log_records_calls_and_rejections(stack: Stack) -> None:
    stack.call(lambda c: c.call_tool("say", {"text": " /kill"}))
    stack.call(lambda c: c.call_tool("say", {"text": "нормальная реплика"}))
    entries = stack.log_lines()
    assert [e["ok"] for e in entries if e["tool"] == "say"] == [False, True]
    rejected = entries[0]
    assert rejected["error"] and rejected["t_end"] >= rejected["ts"]
    ok_entry = entries[1]
    assert "said" in ok_entry or ok_entry["tool"] == "say"
    # JSONL: every line parses (checked by json.loads above)
    assert all({"ts", "tool", "args", "t_end", "ok"} <= set(e) for e in entries)
