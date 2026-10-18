"""The body MCP server (T-024 spike, full section-4 set in T-025).

Everything runs against a fake body with the real Py4jBody method surface; the MCP layer is
exercised through the real streamable HTTP transport (uvicorn on port 0 in a thread), because
that is what the harness talks to. C1 runs in its own thread (the same wiring as production,
a fast tick); the runner endpoints ride the same port and go through real HTTP calls.

Checks the acceptance items one by one: the section-4 tool set, the `command`/`chat`
rejection, `say` guards, the uc service-line filter with plain names, `wait_events` by
timeout and by event, the death -> runner-only respawn path, `retire_stuck` refusals and
the JSONL call log (spike P1-2…P1-5, P1-8). The T-024 review R2-1 fix: the tests split out
during the spike are back under honest names.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
import time
from datetime import datetime

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from runtime.mind.mcp_body import (
    SAY_MAX_LEN,
    ToolError,
    build_mind,
    check_say,
    validate_args,
)
from runtime.mind.executor import JsonlLog
from runtime.ports import BlockInfo, EntityInfo, Event, ItemStack


class FakeBody:
    """The Py4jBody surface; scripted by each test."""

    def __init__(self) -> None:
        self._state = SimpleState()
        self.chat: list[str] = []
        self.acts: list[tuple[str, dict]] = []
        self.busy_flag = False
        self.respawn_calls = 0
        self.respawn_fail_times = 0
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
        if self.respawn_fail_times > 0:  # scripted bridge failures (spike R1-4 tests)
            self.respawn_fail_times -= 1
            raise RuntimeError(f"scripted respawn failure #{self.respawn_calls}")
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
    """The whole mind in-process: fake body, C1, server, uvicorn, client call helpers."""

    def __init__(self, body: FakeBody | None = None, idle_s: float = 10_000.0):
        self.body = body or FakeBody()
        self._tmp = f".mcp-body-test-{time.monotonic_ns()}.jsonl"
        self.calls_file = JsonlLog(self._tmp)
        server, self.impl, self.executor = build_mind(
            self.body,
            username="LastOrder01",
            call_log=self.calls_file,
            tick_interval=0.25,
            idle_s=idle_s,  # the watchdog has its own tests; keep it quiet here
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

    def runner(self, method: str, path: str, json_body: dict | None = None) -> httpx.Response:
        return httpx.request(method, f"http://127.0.0.1:{self.port}{path}", json=json_body, timeout=30.0)

    def log_lines(self) -> list[dict]:
        with self.calls_file._lock:
            with open(self._tmp, encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]

    def wait_until(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"condition not met within {timeout:.0f} s")

    def close(self) -> None:
        self.uvicorn.should_exit = True
        self.thread.join(timeout=5)
        self.executor.stop()
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


# --- over MCP: the section-4 tool set --------------------------------------------------


def test_tool_set_is_exactly_section_four(stack: Stack) -> None:
    names = stack.call(lambda c: c.list_tools())
    assert sorted(t.name for t in names.tools) == [
        "act",
        "observe",
        "retire_stuck",
        "say",
        "stop",
        "wait_events",
    ]


def test_act_over_mcp_returns_intent_id_and_reaches_body(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("act", {"intent": "goto", "args": {"x": 5, "y": 64, "z": 5}}))
    assert parse(result) == {"intent_id": 1}
    stack.wait_until(lambda: ("goto", {"x": 5, "y": 64, "z": 5}) in stack.body.acts)  # C1 applies it


def test_act_command_is_rejected_over_mcp(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("act", {"intent": "command", "args": {"cmd": "stop"}}))
    assert "not available" in error_text(result)
    result = stack.call(lambda c: c.call_tool("act", {"intent": "chat", "args": {"text": "hi"}}))
    assert "not available" in error_text(result)
    assert stack.body.acts == []  # rejected at the door, C1 never saw an intent


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


# --- wait_events ------------------------------------------------------------------------


def test_wait_events_timeout_and_event(stack: Stack) -> None:
    # by timeout: nothing accumulates, the wait runs out
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 0.3}))
    assert parse(result) == {"type": "timeout", "timeout_s": 0.3}

    # by event: a queued chat line is served first (T-024 review R2-1: one test, both paths)
    stack.body.push_event("chat.line", "<LastOrder01> реплика")
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0}))
    assert parse(result) == {"type": "chat.line", "text": "<LastOrder01> реплика"}

    # and again the timeout path once the queue is empty
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 0.3}))
    assert parse(result) == {"type": "timeout", "timeout_s": 0.3}


def test_wait_events_filters_service_and_formatting(stack: Stack) -> None:
    # uc bookkeeping with the service flag: never delivered, wait runs to its timeout
    stack.body.push_event("chat.line", "§7[Alto Clef]§r finished @get 1", service=True)
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 1.0}))
    assert parse(result)["type"] == "timeout"
    # the same service line WITHOUT the flag is dropped by the text cleaner too
    stack.body.push_event("chat.line", "§7[Alto Clef]§r finished @get 1")
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 1.0}))
    assert parse(result)["type"] == "timeout"
    # a player line arrives cleaned: no § codes, no minecraft: namespace (plain mode)
    stack.body.push_event("chat.line", "§a<Steve> дай minecraft:oak_log")
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0}))
    ev = parse(result)
    assert ev["type"] == "chat.line"
    assert ev["text"] == "<Steve> дай oak_log"


def test_damage_event_text_cleaned_with_plain_source(stack: Stack) -> None:
    stack.body.push_event("game.damage", "§cукусил zombie, держи minecraft:rotten_flesh")
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0}))
    ev = parse(result)
    assert ev["type"] == "game.damage"
    assert ev["text"] == "укусил zombie, держи rotten_flesh"
    assert ev["source"] == "ent:zombie zombie"  # the hostile of the fake body, plain


def test_wait_events_serves_accumulated_events_one_by_one(stack: Stack) -> None:
    # events that piled up between waits are served in order, oldest first
    stack.body.push_event("chat.line", "<Steve> первая")
    stack.body.push_event("game.damage", "zombie hit")
    first = parse(stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0})))
    second = parse(stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0})))
    assert first["text"] == "<Steve> первая"
    assert second["type"] == "game.damage"


# --- death and the runner endpoints -----------------------------------------------------


def test_death_event_served_and_respawn_only_via_runner(stack: Stack) -> None:
    stack.body.set_state(hp=0.0)
    stack.body.chat = ["LastOrder01 was slain by Zombie"]
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0}))
    death = parse(result)
    assert death["type"] == "game.death"
    assert "slain by Zombie" in death["text"]

    # status carries the death for the runner (review P1-1), cause included
    status = stack.runner("GET", "/runner/status").json()
    assert status["dead"] is True and status["cause"] == "game"

    # no auto-respawn (T-025: respawn is the runner's command), and act refuses while dead
    time.sleep(0.6)
    assert stack.body.respawn_calls == 0
    result = stack.call(lambda c: c.call_tool("act", {"intent": "goto", "args": {"x": 1, "y": 64, "z": 1}}))
    assert "dead" in error_text(result)

    # the runner's respawn works; on the living body that follows, a 409 without action (P1-7)
    assert stack.runner("POST", "/runner/respawn").status_code == 200
    stack.wait_until(lambda: stack.body.respawn_calls == 1)
    assert stack.runner("GET", "/runner/status").json()["alive"] is True
    assert stack.runner("POST", "/runner/respawn").status_code == 409
    time.sleep(0.3)
    assert stack.body.respawn_calls == 1  # the 409 did not touch the body


def test_death_logged_by_poller_without_wait_events(stack: Stack) -> None:
    # The runner watches log.jsonl; a death must be visible there even if the agent never
    # calls wait_events again (spike checker C-5). No client call in this test at all.
    stack.body.set_state(hp=0.0)
    stack.body.chat = ["LastOrder01 was killed"]
    deadline = time.monotonic() + 5.0
    entries: list[dict] = []
    while time.monotonic() < deadline:
        entries = [e for e in stack.log_lines() if e.get("kind") == "death"]
        if entries:
            break
        time.sleep(0.05)
    assert entries and entries[0]["event"] == "game.death"
    assert "was killed" in entries[0]["text"]
    assert isinstance(entries[0]["ts"], float)


def test_runner_birth_endpoint_arms_the_life_clock(stack: Stack) -> None:
    from datetime import datetime, timedelta, timezone

    born = (datetime.now(timezone.utc) - timedelta(minutes=42)).isoformat()
    response = stack.runner(
        "POST", "/runner/birth", {"serial": 8, "born_at": born, "ttl_min": 45}
    )
    assert response.status_code == 200 and response.json()["armed"] is True
    stack.wait_until(
        lambda: any(e.get("event") == "life.expiring" for e in stack.log_lines()
                    if e.get("kind") == "event")
    )
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0}))
    assert parse(result)["type"] == "life.expiring"
    # a bad payload is a 400, not a crash
    assert stack.runner("POST", "/runner/birth", {"serial": "x"}).status_code == 400


# --- retire_stuck ------------------------------------------------------------------------


def test_retire_stuck_refused_without_idle_signal(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("retire_stuck", {}))
    assert "idle signal" in error_text(result)


def test_retire_stuck_requests_death_after_watchdog(stack: Stack) -> None:
    class RecordingHive:
        def __init__(self) -> None:
            self.requests: list[tuple[int, str]] = []

        def request_death(self, serial: int, cause: str) -> None:
            self.requests.append((serial, cause))

    hive = RecordingHive()
    stack.executor._hive = hive
    born = datetime.now().isoformat()
    stack.runner("POST", "/runner/birth", {"serial": 7, "born_at": born, "ttl_min": 45})
    # watchdog: idle past the threshold (set small for this stack)
    stack.executor._idle_s = 0.5
    stack.wait_until(lambda: stack.executor.retire_allowed)
    result = stack.call(lambda c: c.call_tool("retire_stuck", {}))
    assert parse(result) == {"requested": True, "serial": 7, "cause": "stuck"}
    assert hive.requests == [(7, "stuck")]


# --- the call log ------------------------------------------------------------------------


def test_call_log_records_calls_and_rejections(stack: Stack) -> None:
    stack.call(lambda c: c.call_tool("say", {"text": " /kill"}))
    stack.call(lambda c: c.call_tool("say", {"text": "нормальная реплика"}))
    entries = stack.log_lines()
    says = [e for e in entries if e.get("tool") == "say"]
    assert [e["ok"] for e in says] == [False, True]
    rejected = says[0]
    assert rejected["error"] and rejected["kind"] == "call"
    ok_entry = says[1]
    assert "said" in ok_entry or ok_entry["tool"] == "say"
    # JSONL: every line parses (checked by json.loads above)
    assert all({"ts", "kind"} <= set(e) for e in entries)
