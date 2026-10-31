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
            EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=3.5, pos=(1, 64, 1)),
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

    def __init__(
        self,
        body: FakeBody | None = None,
        idle_s: float = 10_000.0,
        runner_key: str = "",
        operator_name: str = "",
    ):
        self.body = body or FakeBody()
        self._tmp = f".mcp-body-test-{time.monotonic_ns()}.jsonl"
        self.calls_file = JsonlLog(self._tmp)
        server, self.impl, self.executor = build_mind(
            self.body,
            username="LastOrder01",
            call_log=self.calls_file,
            tick_interval=0.25,
            idle_s=idle_s,  # the watchdog has its own tests; keep it quiet here
            runner_key=runner_key,
            operator_name=operator_name,
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

    def runner(
        self, method: str, path: str, json_body: dict | None = None, key: str | None = None
    ) -> httpx.Response:
        headers = {"X-Runner-Key": key} if key is not None else {}
        return httpx.request(
            method, f"http://127.0.0.1:{self.port}{path}", json=json_body,
            headers=headers, timeout=30.0,
        )

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
        {"from": "LastOrder01", "text": "привет"},
        {"text": "LastOrder01 was slain by Zombie"},  # system: no `from`
        {"text": "minecraft birch logs are nice"},
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
    assert parse(result) == {
        "type": "chat.line", "lines": [{"from": "LastOrder01", "text": "реплика"}]
    }

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
    assert ev["lines"] == [{"from": "Steve", "text": "дай oak_log"}]


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
    assert first["lines"] == [{"from": "Steve", "text": "первая"}]
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
    # one arming per life (review R1-2): a second birth must not overwrite the serial
    second = stack.runner("POST", "/runner/birth", {"serial": 666, "born_at": born, "ttl_min": 45})
    assert second.status_code == 409
    assert stack.runner("GET", "/runner/status").json()["serial"] == 8
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


def test_retire_stuck_reaches_the_gated_hive_with_the_runner_key(tmp_path) -> None:
    """R2-3: with the clock armed through /runner/birth (no NODE_SERIAL in compose),
    retire_stuck still has a hive client — and it carries X-Runner-Key of the slot, so
    the gated /registry/request_death (R2-2) accepts it."""
    from pathlib import Path

    from runtime.hive.client import HiveClient
    from test_hive_api import make_app_stack, traits

    runner_key = "rk-mind-one"
    keyed = make_app_stack(tmp_path, {runner_key: "LastOrder01"})
    record = keyed.client.post(
        "/registry/birth",
        json={"slot": "LastOrder01", "traits": traits().as_dict()},
        headers={"X-Runner-Key": runner_key},
    )
    assert record.status_code == 200, record.text
    serial = record.json()["serial"]

    stack = Stack()
    try:
        stack.runner("POST", "/runner/birth", {"serial": serial, "born_at": datetime.now().isoformat(), "ttl_min": 45})
        stack.executor._idle_s = 0.5
        stack.wait_until(lambda: stack.executor.retire_allowed)
        # without the key the gated hive refuses: the tool reports it, no silent no-op
        stack.executor._hive = HiveClient(
            transport=keyed.client, run_dir=Path(tmp_path) / "run-keyless", retries=0
        )
        result = stack.call(lambda c: c.call_tool("retire_stuck", {}))
        assert "rejected" in error_text(result)
        # with the slot's runner key the same call lands in the real hive
        stack.executor._hive = HiveClient(
            transport=keyed.client,
            run_dir=Path(tmp_path) / "run-keyed",
            retries=0,
            runner_key=runner_key,
        )
        result = stack.call(lambda c: c.call_tool("retire_stuck", {}))
        assert parse(result) == {"requested": True, "serial": serial, "cause": "stuck"}
        assert keyed.rcon.kills == ["LastOrder01"]  # request_death killed in the hive
    finally:
        stack.close()


def test_main_refuses_an_empty_mind_runner_key(monkeypatch, tmp_path) -> None:
    """R2-6, mind side: an empty MIND_RUNNER_KEY would serve /runner/respawn and
    /runner/birth ungated — main() refuses to start instead (NODE_DEV_OPEN=1 excepts)."""
    import os
    import subprocess
    import sys

    from runtime.config import REPO_ROOT

    env = dict(os.environ)
    # set explicitly empty (not popped): runtime.config loads the repo .env into
    # os.environ for absent keys only, and must not resurrect a key over this test
    env["MIND_RUNNER_KEY"] = ""
    env.pop("NODE_DEV_OPEN", None)
    env.pop("NODE_SERIAL", None)
    # an invalid Jev backend is the deterministic stop AFTER the key check: the dev
    # exception must get that far instead of refusing on the empty key
    env["JEV_BACKEND"] = "no-such-backend"
    code = "import sys; sys.path.insert(0, '.'); from runtime.mind.mcp_body import main; main()"
    refused = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=120,
    )
    assert refused.returncode != 0
    assert "MIND_RUNNER_KEY is empty" in refused.stderr
    dev_open = subprocess.run(
        [sys.executable, "-c", code], env={**env, "NODE_DEV_OPEN": "1"},
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert dev_open.returncode != 0
    assert "MIND_RUNNER_KEY is empty" not in dev_open.stderr
    assert "Jev" in dev_open.stderr or "backend" in dev_open.stderr  # past the key check


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


# --- the runner key of /runner/* (T-027 plan review P1-4) ---------------------------------------


def test_runner_commands_need_the_runner_key() -> None:
    """With MIND_RUNNER_KEY set, respawn and birth answer 401 to a keyless caller
    (a sister with bash must not respawn her dead body under the runner's will
    session), and the status stays open: the death cause is the agent's own data."""
    s = Stack(runner_key="rk-mind-one")
    try:
        s.body.set_state(hp=0.0)
        s.body.chat = ["LastOrder01 was killed"]
        s.wait_until(lambda: s.executor.dead())
        assert s.runner("GET", "/runner/status").status_code == 200
        assert s.runner("POST", "/runner/respawn").status_code == 401
        assert s.runner("POST", "/runner/respawn", key="rk-wrong").status_code == 401
        assert s.body.respawn_calls == 0
        assert (
            s.runner("POST", "/runner/birth", {"serial": 8, "born_at": "2026-10-21T00:00:00+00:00", "ttl_min": 45}).status_code
            == 401
        )
        assert s.runner("POST", "/runner/birth", {"serial": "x"}, key="rk-mind-one").status_code == 400
        # the right key opens both commands
        assert s.runner("POST", "/runner/respawn", key="rk-mind-one").status_code == 200
        s.wait_until(lambda: s.body.respawn_calls == 1)
        assert (
            s.runner(
                "POST", "/runner/birth", {"serial": 8, "born_at": "2026-10-21T00:00:00+00:00", "ttl_min": 45}, key="rk-mind-one"
            ).status_code
            == 200
        )
    finally:
        s.close()


def test_runner_event_boards_the_queue_behind_the_runner_key() -> None:
    """T-050: the board bridge's batch — one `board` event into the queue; the key
    gates it, and the sister reads the lines out of `wait_events` like any event."""
    s = Stack(runner_key="rk-mind-one")
    try:
        lines = ["[board #17 task · from operator · to net · open] Build a shelter."]
        assert s.runner("POST", "/runner/event", {"type": "board", "lines": lines}).status_code == 401
        assert (
            s.runner("POST", "/runner/event", {"type": "board", "lines": lines}, key="rk-wrong").status_code
            == 401
        )
        response = s.runner(
            "POST", "/runner/event", {"type": "board", "lines": lines}, key="rk-mind-one"
        )
        assert response.status_code == 200 and response.json()["lines"] == 1
        event = parse(s.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0})))
        assert event["type"] == "board" and event["lines"] == lines
    finally:
        s.close()


def test_runner_event_refuses_everything_but_a_board_batch() -> None:
    """The runner cannot hand the sister a death, a chat line or a shapeless body."""
    s = Stack(runner_key="rk-mind-one")
    try:
        bad_bodies = [
            {"type": "game.death", "text": "x"},  # no other event type rides this route
            {"type": "chat.line", "lines": ["x"]},
            {"type": "board", "lines": []},
            {"type": "board", "lines": "x"},
            {"type": "board", "lines": [1, 2]},
            {"type": "board"},
            {},
            "not-a-dict",
        ]
        for body in bad_bodies:
            assert (
                s.runner("POST", "/runner/event", body, key="rk-mind-one").status_code == 400
            ), body
    finally:
        s.close()


# --- the operator's chat lines (T-051) ---------------------------------------------------------


def test_observe_and_events_mark_the_operator() -> None:
    s = Stack(operator_name="ZeusThunderer")
    try:
        s.body.chat = ["<ZeusThunderer> собери дерево", "<Bob> привет"]
        obs = parse(s.call(lambda c: c.call_tool("observe", {})))
        assert obs["chat"] == [
            {"from": "ZeusThunderer", "operator": True, "text": "собери дерево"},
            {"from": "Bob", "text": "привет"},
        ]
        s.body.push_event("chat.line", "<ZeusThunderer> как дела?")
        s.wait_until(
            lambda: any(e["type"] == "chat.line" for e in s.executor.events.pending())
        )
        ev = parse(s.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0})))
        assert ev["type"] == "chat.line"
        assert ev["lines"] == [{"from": "ZeusThunderer", "operator": True, "text": "как дела?"}]
    finally:
        s.close()


def test_several_chat_lines_reach_one_wait_events_call(stack: Stack) -> None:
    stack.body.push_event("chat.line", "<Steve> раз")
    stack.body.push_event("chat.line", "<Bob> два")
    stack.body.push_event("chat.line", "<Steve> три")
    stack.wait_until(
        lambda: any(e["type"] == "chat.line" for e in stack.executor.events.pending())
    )
    ev = parse(stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0})))
    assert ev["type"] == "chat.line"
    assert ev["lines"] == [
        {"from": "Steve", "text": "раз"},
        {"from": "Bob", "text": "два"},
        {"from": "Steve", "text": "три"},
    ]
