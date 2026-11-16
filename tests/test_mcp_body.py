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
import os
import signal
import tempfile
import threading
import time
import types
from datetime import datetime

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from runtime.mind.login import SessionLogin
from runtime.mind.mcp_body import (
    ANSWER_MAX_CHARS,
    DEFAULT_DENY,
    OBSERVE_ENTITIES_MAX,
    OBSERVE_POS_PER_NAME,
    SAY_MAX_LEN,
    ToolError,
    visible_blocks,
    build_mind,
    check_say,
    check_server_command,
    parse_deny,
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
        return (ItemStack(name="minecraft:wooden_axe", count=1, slot=0), *self.extra_items)

    extra_items: tuple = ()
    # the last build batch as uc would report it; tests script it
    build: dict = {"queued": 0, "placed": 0, "already": 0, "deferred": 0, "reasons": {}}

    def build_status(self) -> dict:
        return dict(self.build)

    # the world a build's site check reads: (x, y, z) -> block id; everything else is air
    world: dict = {}

    def block_at(self, x: int, y: int, z: int) -> dict:
        block = self.world.get((x, y, z))
        return {"id": block or "air", "air": block is None, "replaceable": block == "short_grass"}

    def recent_chat(self, lines: int = 5):
        return tuple(self.chat[-lines:])

    def act(self, lever: str, args: dict | None = None) -> None:
        self.acts.append((lever, dict(args or {})))
        if lever == "destroy_block":
            self.world = {k: v for k, v in self.world.items()
                          if k != (args["x"], args["y"], args["z"]) or v == "bedrock"}
        if lever == "build_blocks":  # uc places into air and replaceable cells, skips solid ones
            for x, y, z, block in args["blocks"]:
                if self.world.get((x, y, z)) in (None, "short_grass"):
                    self.world = {**self.world, (x, y, z): block}

    def restock(self, needs: dict) -> dict:
        self.acts.append(("__restock__", dict(needs)))
        hotbar: dict = {}  # the whole inventory is in reach: a hotbar of plenty
        for stack in self.inventory():
            name = stack.name.split(":")[-1]
            hotbar[name] = hotbar.get(name, 0) + stack.count
        return {"swaps": 0, "hotbar": hotbar}

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
        deny_commands: tuple[str, ...] | None = None,
        wait_max_s: float | None = None,
        login: "SessionLogin | None" = None,
    ):
        self.body = body or FakeBody()
        # the temp dir, not the cwd: a stack that never reaches close() left these
        # files in the repo root (and every worktree)
        self._tmp = os.path.join(tempfile.gettempdir(), f".mcp-body-test-{time.monotonic_ns()}.jsonl")
        self.calls_file = JsonlLog(self._tmp)
        extra: dict = {}
        if deny_commands is not None:
            extra["deny_commands"] = deny_commands
        if wait_max_s is not None:
            extra["wait_max_s"] = wait_max_s
        server, self.impl, self.executor = build_mind(
            self.body,
            username="LastOrder01",
            call_log=self.calls_file,
            tick_interval=0.25,
            idle_s=idle_s,  # the watchdog has its own tests; keep it quiet here
            runner_key=runner_key,
            operator_name=operator_name,
            login=login,
            **extra,
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
        # T-054: the death tests script hp = 0 — wait for C1's first living poll, so
        # the stale-corpse gate never races the test's own death.
        self.wait_until(lambda: self.executor._saw_alive)

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
        ("build_blocks", {"blocks": [[0, 64, 0, "stone"]]}),  # only through the build tool
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
        "build",
        "build_status",
        "observe",
        "retire_stuck",
        "say",
        "server_command",
        "stop",
        "wait_events",
    ]


HUT = {
    "name": "hut",
    "size": [3, 3, 3],
    "palette": ["oak planks"],
    "regions": [
        {"from": [0, 0, 0], "to": [2, 2, 2], "block": "oak planks"},
        {"from": [1, 1, 0], "to": [1, 1, 1], "block": "air"},
    ],
}


def test_build_refuses_a_block_missing_from_the_inventory(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 10, "y": 64, "z": 10}))
    text = error_text(result)
    assert "not in the inventory: oak_planks" in text and "still to place: oak_planks 25" in text
    assert not [a for a in stack.body.acts if a[0] == "build_blocks"]


def test_build_refuses_a_broken_schematic_with_the_reason(stack: Stack) -> None:
    bad = {**HUT, "palette": ["oak stairs"]}
    result = stack.call(lambda c: c.call_tool("build", {"schematic": bad, "x": 0, "y": 64, "z": 0}))
    assert "cannot place oak_stairs" in error_text(result)


def test_build_hands_absolute_cells_to_c1_and_settles_by_the_queue(stack: Stack) -> None:
    stack.body.extra_items = (ItemStack(name="minecraft:oak_planks", count=64, slot=1),)
    result = stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 10, "y": 64, "z": -3}))
    answer = parse(result)
    assert answer["blocks"] == 25 and answer["materials"] == {"oak_planks": 25}
    assert answer["origin"] == [10, 64, -3]
    stack.wait_until(lambda: any(a[0] == "build_blocks" for a in stack.body.acts))
    (lever, args), = [a for a in stack.body.acts if a[0] == "build_blocks"]
    assert len(args["blocks"]) == 25 and [10, 64, -3, "oak_planks"] in args["blocks"]
    assert [11, 65, -3, "oak_planks"] not in args["blocks"]  # the doorway stays air
    # the world holds every planned block (the fake places them): the intent settles done
    stack.wait_until(lambda: any(
        line.get("kind") == "intent" and line.get("phase") == "done" for line in stack.log_lines()
    ), timeout=10)
    assert ("__restock__", {"oak_planks": 25}) in stack.body.acts  # hotbar first, then place
    # the journal carries the cell count, not the 25-cell list
    applied = [l for l in stack.log_lines() if l.get("kind") == "intent" and l.get("phase") == "applied"]
    assert applied[-1]["args"]["blocks"] == 25
    stack.body.build = {"queued": 0, "placed": 25, "already": 0, "deferred": 0, "reasons": {}}
    status = parse(stack.call(lambda c: c.call_tool("build_status", {})))
    assert status["placed"] == 25


def test_build_refuses_a_far_site_the_body_cannot_see(stack: Stack) -> None:
    stack.body.extra_items = (ItemStack(name="minecraft:oak_planks", count=64, slot=1),)
    result = stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 200, "y": 64, "z": 0}))
    assert "walk within 32 m of it first" in error_text(result)


def test_build_refuses_a_site_that_is_not_clear(stack: Stack) -> None:
    stack.body.extra_items = (ItemStack(name="minecraft:oak_planks", count=64, slot=1),)
    # a log where the doorway goes, leaves where a planned plank goes; grass under the floor is fine
    stack.body.world = {(11, 65, 10): "oak_log", (11, 66, 11): "oak_leaves", (10, 64, 10): "short_grass"}
    result = stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 10, "y": 64, "z": 10}))
    text = error_text(result)
    assert "not clear — 2 cell(s)" in text and "oak_log 1" in text and "oak_leaves 1" in text
    assert "clear: true" in text
    assert not [a for a in stack.body.acts if a[0] in ("build_blocks", "destroy_block")]


def test_build_with_clear_digs_the_site_top_down_then_builds(stack: Stack) -> None:
    stack.body.extra_items = (ItemStack(name="minecraft:oak_planks", count=64, slot=1),)
    # a log in the doorway (plan: air), stone in the wall (plan: planks), bedrock that stays
    stack.body.world = {(11, 65, 10): "oak_log", (12, 66, 12): "stone", (10, 64, 10): "bedrock"}
    answer = parse(stack.call(lambda c: c.call_tool(
        "build", {"schematic": HUT, "x": 10, "y": 64, "z": 10, "clear": True})))
    assert answer["clearing"] == 3
    stack.wait_until(lambda: any(a[0] == "build_blocks" for a in stack.body.acts), timeout=15)
    digs = [(a[1]["x"], a[1]["y"], a[1]["z"]) for a in stack.body.acts if a[0] == "destroy_block"]
    assert digs == [(12, 66, 12), (11, 65, 10), (10, 64, 10)]  # top-down
    stack.wait_until(lambda: any(
        line.get("kind") == "intent" and line.get("phase") == "failed" for line in stack.log_lines()
    ), timeout=20)
    failed = [l for l in stack.log_lines() if l.get("kind") == "intent" and l.get("phase") == "failed"]
    reason = failed[-1]["reason"]
    # the world check: bedrock where a plank goes; one repair round tried, then it stopped
    assert "24 of 25 planned blocks in place; 1 missing (oak_planks at 10,64,10)" in reason
    assert "1 site cell(s) could not be dug out (10,64,10)" in reason
    repairs = [l for l in stack.log_lines() if l.get("kind") == "intent" and l.get("phase") == "repair"]
    assert len(repairs) == 1


def test_build_digs_its_own_scaffolding_and_fills_a_hole(stack: Stack) -> None:
    stack.body.extra_items = (ItemStack(name="minecraft:oak_planks", count=64, slot=20),)
    answer = parse(stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 10, "y": 64, "z": 10})))
    assert "clearing" not in answer
    stack.wait_until(lambda: any(a[0] == "build_blocks" for a in stack.body.acts), timeout=10)
    # as uc leaves it: a pillar outside the box, a hole in the wall
    stack.body.world = {**{k: v for k, v in stack.body.world.items() if k != (12, 65, 11)},
                        (13, 64, 12): "oak_planks", (13, 65, 12): "oak_planks"}
    stack.wait_until(lambda: any(
        line.get("kind") == "intent" and line.get("phase") in ("done", "failed") for line in stack.log_lines()
    ), timeout=20)
    phases = [l.get("phase") for l in stack.log_lines() if l.get("kind") == "intent"]
    assert "repair" in phases and phases[-1] == "done", phases
    digs = [(a[1]["x"], a[1]["y"], a[1]["z"]) for a in stack.body.acts if a[0] == "destroy_block"]
    assert digs == [(13, 65, 12), (13, 64, 12)]  # the pillar, top-down
    assert stack.body.world.get((12, 65, 11)) == "oak_planks"  # the hole refilled
    assert (13, 64, 12) not in stack.body.world


def test_build_withholds_cells_the_hotbar_cannot_cover_and_places_them_next_round(stack: Stack) -> None:
    # uc places whatever is in hand once the named block runs out of the hotbar (the
    # Birch Cottage's planked-over windows): a round never sends more than the hotbar holds
    stack.body.extra_items = (ItemStack(name="minecraft:oak_planks", count=64, slot=20),)
    first = [{"oak_planks": 10}]
    real = stack.body.restock

    def restock(needs: dict) -> dict:
        out = real(needs)
        if first:
            out["hotbar"] = first.pop()
        return out

    stack.body.restock = restock
    parse(stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 10, "y": 64, "z": 10})))
    stack.wait_until(lambda: any(
        line.get("kind") == "intent" and line.get("phase") in ("done", "failed") for line in stack.log_lines()
    ), timeout=20)
    batches = [a[1]["blocks"] for a in stack.body.acts if a[0] == "build_blocks"]
    assert [len(b) for b in batches] == [10, 15]  # the second round: only what is missing
    phases = [l.get("phase") for l in stack.log_lines() if l.get("kind") == "intent"]
    assert "withheld" in phases and phases[-1] == "done", phases


def test_build_again_bills_only_the_cells_still_missing(stack: Stack) -> None:
    from runtime.mind.build import parse_schematic

    cells = parse_schematic(HUT).placed_at((10, 64, 10))
    stack.body.world = {tuple(c[:3]): c[3] for c in cells[2:]}  # two holes left
    stack.body.extra_items = (ItemStack(name="minecraft:oak_planks", count=2, slot=20),)
    answer = parse(stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 10, "y": 64, "z": 10})))
    assert "short" not in answer
    stack.wait_until(lambda: any(
        line.get("kind") == "intent" and line.get("phase") == "done" for line in stack.log_lines()
    ), timeout=20)
    stack.body.extra_items = ()
    stack.body.world = {}
    result = stack.call(lambda c: c.call_tool("build", {"schematic": HUT, "x": 10, "y": 64, "z": 10}))
    assert "still to place: oak_planks 25" in error_text(result)


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


def test_wait_events_clamps_an_oversized_ask_to_the_cap() -> None:
    # T-054: zcode kills a tool call after 30 s (7 killed waits on node-2 in the
    # T-052 run); the stand caps wait_events below that. A larger ask is not an
    # error: it waits the cap and the answer names the applied value.
    s = Stack(wait_max_s=0.3)
    try:
        started = time.monotonic()
        result = parse(s.call(lambda c: c.call_tool("wait_events", {"timeout_s": 600.0})))
        assert result == {"type": "timeout", "timeout_s": 0.3}
        assert time.monotonic() - started < 10.0  # the cap bounded the wait, not the ask
    finally:
        s.close()


def test_wait_events_tool_description_names_the_cap() -> None:
    s = Stack(wait_max_s=25.0)
    try:
        tools = s.call(lambda c: c.list_tools())
        (tool,) = [t for t in tools.tools if t.name == "wait_events"]
        assert "capped at 25 s" in tool.description
    finally:
        s.close()


def test_wait_events_zero_is_still_rejected(stack: Stack) -> None:
    result = stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 0}))
    assert "must be > 0" in error_text(result)


# --- the SIGTERM path (T-055) ---------------------------------------------------------------


class FakeUvi:
    def __init__(self) -> None:
        self.should_exit = False


class FakeThread:
    def __init__(self) -> None:
        self.joined: list[float | None] = []

    def join(self, timeout: float | None = None) -> None:
        self.joined.append(timeout)


def test_teardown_mind_stops_in_order_and_closes_the_bridge() -> None:
    # SIGTERM: the MCP server first, then C1, then the bridge (close() — the
    # SIGKILL alternative leaves uc's Java gateway dead, T-052 a). The join is
    # bounded by TEARDOWN_GRACE_S, under compose's stop_grace_period.
    from runtime.mind.mcp_body import TEARDOWN_GRACE_S, teardown_mind

    uvi, thread = FakeUvi(), FakeThread()
    calls: list[str] = []
    executor = types.SimpleNamespace(stop=lambda: calls.append("stop"))
    body = types.SimpleNamespace(close=lambda: calls.append("close"))

    teardown_mind(uvi, thread, executor, body)

    assert uvi.should_exit is True
    assert thread.joined == [TEARDOWN_GRACE_S]
    assert calls == ["stop", "close"]  # the bridge closes LAST


def test_stop_handler_sets_the_event() -> None:
    from runtime.mind.mcp_body import stop_handler

    stop = threading.Event()
    stop_handler(stop)(signal.SIGTERM, None)
    assert stop.is_set()


def test_serve_mind_exits_nonzero_when_the_server_dies_unsignal() -> None:
    # R1-1: a failed server start (port taken, lifespan error) sys.exit's
    # INSIDE the thread — swallowed there. The mind must exit non-zero anyway,
    # or restart: on-failure never brings it back and the slot silently loses
    # its mind.
    from runtime.mind.mcp_body import serve_mind

    class DeadServer:
        should_exit = False

        def run(self) -> None:
            return  # dies at once, no signal ever arrives

    executor = types.SimpleNamespace(stop=lambda: None)
    body = types.SimpleNamespace(close=lambda: None)
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with pytest.raises(SystemExit) as caught:
            serve_mind(None, 8700, executor, body, make_server=DeadServer)
        assert (caught.value.code != 0) is True
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)


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


def test_runner_term_endpoint_acknowledges_the_soft_term(stack: Stack) -> None:
    """ADR-052: a life whose deadline passed terms on C1's own clock; the status
    carries it next to `dead`, the ack resets the life without touching the living
    body, and the heir arms onto the same body."""
    from datetime import datetime, timedelta, timezone

    late = (datetime.now(timezone.utc) - timedelta(minutes=50)).isoformat()
    assert (
        stack.runner("POST", "/runner/birth", {"serial": 8, "born_at": late, "ttl_min": 45}).status_code
        == 200
    )
    stack.wait_until(lambda: stack.executor.termed())
    status = stack.runner("GET", "/runner/status").json()
    assert status["term"] is True and status["term_cause"] == "ttl"
    assert status["dead"] is False  # the soft sibling of a death, not one
    time.sleep(0.3)
    assert stack.body.respawn_calls == 0  # the body lives on, untouched

    assert stack.runner("POST", "/runner/term").status_code == 200
    assert stack.runner("GET", "/runner/status").json()["term"] is False
    assert stack.runner("POST", "/runner/term").status_code == 409  # nothing left to ack
    time.sleep(0.3)
    assert stack.body.respawn_calls == 0  # a refusal never touches the body
    fresh = datetime.now(timezone.utc).isoformat()
    assert (
        stack.runner("POST", "/runner/birth", {"serial": 9, "born_at": fresh, "ttl_min": 45}).status_code
        == 200
    )


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
        # ADR-052: the ask marked the node in the hive and killed nothing — the
        # mind's own soft term carries the closing from here
        assert keyed.lifecycle._nodes[serial].stuck is True
        assert not keyed.lifecycle.life_over(serial)
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
    """With MIND_RUNNER_KEY set, respawn, term and birth answer 401 to a keyless caller
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
        assert s.runner("POST", "/runner/term").status_code == 401
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
    # C1's drain may split the three pushes across ticks; the queue merges the
    # batches either way, so wait for the COMPLETE batch — a partial first batch
    # is not the accumulation this test asserts (seen flaky under load, T-054).
    stack.wait_until(
        lambda: any(
            e["type"] == "chat.line" and len(e.get("lines", ())) == 3
            for e in stack.executor.events.pending()
        )
    )
    ev = parse(stack.call(lambda c: c.call_tool("wait_events", {"timeout_s": 5.0})))
    assert ev["type"] == "chat.line"
    assert ev["lines"] == [
        {"from": "Steve", "text": "раз"},
        {"from": "Bob", "text": "два"},
        {"from": "Steve", "text": "три"},
    ]


# --- player commands to the server (T-053, ADR-043) ---------------------------------------


def test_check_server_command_accepts_a_player_command() -> None:
    assert check_server_command("  /warp pers ") == "/warp pers"
    assert check_server_command("/tpa LastOrder02") == "/tpa LastOrder02"
    assert check_server_command("/help") == "/help"


@pytest.mark.parametrize(
    "command",
    [
        "/skin set web slim https://x/x.png",  # the skin is the operator's, not a sister's (T-061)
        "/SkinRestorer:skin set player X",
        "/warp; /kill",  # one command per call
        "/warp pers\n/kill",
        "/say §kx",
        "no slash",
        "",
        "x" * (SAY_MAX_LEN + 1),
    ],
)
def test_check_server_command_rejections(command: str) -> None:
    with pytest.raises(ToolError):
        check_server_command(command)


def test_check_server_command_reason_names_the_command() -> None:
    with pytest.raises(ToolError, match="'skin' is on this stand's deny list"):
        check_server_command("/skin set player X")


@pytest.mark.parametrize("command", ["/login pw", "/authme:register pw pw", "/changepassword a b"])
def test_check_server_command_login_is_the_sisters(command: str) -> None:
    # ADR-056: a server password is the sister's own
    assert check_server_command(command) == command


def test_parse_deny_takes_commas_spaces_and_slashes() -> None:
    assert parse_deny("home,  warp ", "/kill", "") == ("home", "warp", "kill")
    assert parse_deny(None, "") == ()


def test_server_command_guards_over_mcp(stack: Stack) -> None:
    """The tool set carries the deny list; the body sees only what passed it."""
    result = stack.call(lambda c: c.call_tool("server_command", {"command": "/skin set player X"}))
    assert "deny list" in error_text(result)
    result = stack.call(lambda c: c.call_tool("server_command", {"command": "warp pers"}))
    assert "starts with /" in error_text(result)
    result = stack.call(lambda c: c.call_tool("server_command", {"command": "/warp pers"}))
    assert parse(result) == {"sent": True, "command": "/warp pers"}
    result = stack.call(lambda c: c.call_tool("server_command", {"command": "/home"}))
    assert "too fast" in error_text(result)
    sent = [args for lever, args in stack.body.acts if lever == "chat"]
    assert sent == [{"text": "/warp pers"}]


def test_server_command_takes_the_stands_slot_list(tmp_path: Path) -> None:
    stack = Stack(deny_commands=("warp",))
    result = stack.call(lambda c: c.call_tool("server_command", {"command": "/warp pers"}))
    assert "deny list" in error_text(result)
    result = stack.call(lambda c: c.call_tool("server_command", {"command": "/skin x"}))
    assert "deny list" in error_text(result)  # the stand's default rides on top


# --- the environment's login (T-053) ------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_session_login_on_world_entry_and_register(tmp_path: Path) -> None:
    sent: list[str] = []
    clock = FakeClock()
    login = SessionLogin("hunter2", sent.append, clock=clock)
    assert login.armed
    login.on_tick(True, [])  # world entry
    assert sent == ["/login hunter2"]
    login.on_tick(True, ["Please register: /register <password> <password>"])
    assert sent[-1] == "/register hunter2 hunter2"
    # a repeated login prompt behind the cooldown only
    login.on_tick(True, ["Use /login to play"])
    assert len(sent) == 2
    clock.now += 31.0
    login.on_tick(True, ["Use /login to play"])
    assert sent[-1] == "/login hunter2"
    # a kick and a rejoin: the login goes out again
    login.on_tick(False, [])
    login.on_tick(True, [])
    assert sent[-1] == "/login hunter2"


def test_session_login_masks_the_password_everywhere() -> None:
    login = SessionLogin("hunter2", lambda text: None)
    assert login.mask("<LastOrder01> /login hunter2") == "<LastOrder01> /login ***"
    assert login.mask("plain line") == "plain line"


def test_session_login_sends_the_skin_command_after_each_entry() -> None:
    """T-061: one configured command rides the environment's own login path on
    external servers; empty — the stand default — sends nothing."""
    sent: list[str] = []
    clock = FakeClock()
    login = SessionLogin("hunter2", sent.append, clock=clock, skin_command="/skin set sister")
    login.on_tick(True, [])  # world entry: the skin command follows the login
    assert sent == ["/login hunter2", "/skin set sister"]
    del sent[:]
    clock.now += 31.0  # a relogin prompt behind the cooldown resends the login only
    login.on_tick(True, ["Use /login to play"])
    assert sent == ["/login hunter2"]
    login.on_tick(False, [])
    login.on_tick(True, [])  # a kick and a rejoin: both go out again
    assert sent[-2:] == ["/login hunter2", "/skin set sister"]


def test_session_login_without_a_skin_command_sends_only_the_login() -> None:
    sent: list[str] = []
    SessionLogin("hunter2", sent.append).on_tick(True, [])
    assert sent == ["/login hunter2"]


def test_the_executor_masks_the_password_out_of_chat_entries(stack: Stack) -> None:
    """chat_entry is the single door into observe() and chat.line: the password
    dies there, never reaching a log, an event or the agent."""
    stack.executor._login = SessionLogin("s3cret", lambda text: None)
    entry = stack.executor.chat_entry("<LastOrder01> /login s3cret")
    assert entry is not None and "s3cret" not in entry["text"]
    assert entry["text"].endswith("/login ***")


def test_the_executor_logs_in_on_world_entry_with_a_fake_body() -> None:
    stack = Stack(login=SessionLogin("hunter2", lambda text: stack.body.act("chat", {"text": text})))
    stack.body.set_state(in_game=False)
    stack.executor._tick()
    stack.body.set_state(in_game=True)
    stack.executor._login._was_in_game = False  # the transition the tick will see
    stack.executor._tick()
    assert ("chat", {"text": "/login hunter2"}) in stack.body.acts


def test_check_server_command_rejects_a_bare_slash() -> None:
    with pytest.raises(ToolError, match="empty command name"):
        check_server_command("/")
    with pytest.raises(ToolError, match="empty command name"):
        check_server_command("  /   ")


# --- the size of an answer (T-056) ------------------------------------------------------


def plains_cube(cx: int = 0, cy: int = 64, cz: int = 0, r: int = 4) -> tuple[BlockInfo, ...]:
    """What getBlocksAround lists on a plain with a tree and a pond: every non-air
    block of the cube, the buried ones included."""
    out = []
    for x in range(cx - r, cx + r + 1):
        for z in range(cz - r, cz + r + 1):
            for y in range(cy - r, cy + r + 1):
                pond = -3 <= x <= -2 and -3 <= z <= -2
                if y == 63:
                    name = "minecraft:water" if pond else "minecraft:grass_block"
                elif y in (61, 62):
                    name = "minecraft:sand" if pond else "minecraft:dirt"
                elif y < 61:
                    name = "minecraft:stone"
                elif (x, z) == (2, 2) and y <= 67:
                    name = "minecraft:oak_log"
                elif 66 <= y <= 68 and abs(x - 2) <= 1 and abs(z - 2) <= 1:
                    name = "minecraft:oak_leaves"
                else:
                    continue
                out.append(BlockInfo(name=name, pos=(x, y, z)))
    return tuple(out)


def raw_observe(stack: Stack):
    async def run(client):
        return await client.call_tool("observe", {})

    return stack.call(run)


def test_observe_is_compact_and_shows_only_blocks_in_view(stack: Stack) -> None:
    cube = plains_cube()
    stack.body.blocks_around = lambda radius=4: cube
    stack.body.entities = lambda radius=16: tuple(
        EntityInfo(kind="animal", name="minecraft:cow", dist_m=float(i), pos=(i, 64, 0))
        for i in range(30, 0, -1)
    )
    result = raw_observe(stack)
    text = result.content[0].text
    obs = json.loads(text)
    assert "\n" not in text and '": ' not in text  # compact, not the SDK's indent=2
    assert obs == result.structured_content  # the structured side carries the same answer
    assert len(text) <= ANSWER_MAX_CHARS
    groups = {g["name"]: g for g in obs["blocks"]}
    # the top of the ground is in view, the dirt and stone under it are buried
    # (81 cells minus 4 of the pond minus the one under the trunk, closed on every side)
    assert groups["blk:grass_block grass block"]["count"] == 81 - 4 - 1
    assert obs["blocks_hidden"] > 200  # three buried layers of 81
    assert "blk:stone stone" not in groups
    # water does not hide: the sand under the pond is in view
    assert groups["blk:sand sand"]["count"] == 4
    # nearest first, the total in count
    grass = groups["blk:grass_block grass block"]["pos"]
    assert len(grass) == OBSERVE_POS_PER_NAME and grass[0] == [0, 63, 0]
    # the nearest entities only, the rest counted
    assert len(obs["entities"]) == OBSERVE_ENTITIES_MAX and obs["entities"][0]["dist_m"] == 1.0
    assert obs["entities_more"] == 30 - OBSERVE_ENTITIES_MAX
    assert obs["self"]["hp"] == 20.0 and obs["inventory"]


def test_observe_answer_has_a_ceiling_on_the_worst_cube(stack: Stack) -> None:
    """Every block of the cube a different name and in view (a lattice): the
    largest answer the body can produce stays under the ceiling."""
    cells = [(x, y, z) for x in range(-4, 5) for y in range(60, 69) for z in range(-4, 5)]
    lattice = tuple(
        BlockInfo(name=f"minecraft:block_{i}", pos=p)
        for i, p in enumerate(cells)
        if sum(p) % 2 == 0  # every neighbour of a listed block is air
    )
    stack.body.blocks_around = lambda radius=4: lattice
    stack.body.inventory = lambda: tuple(
        ItemStack(name=f"minecraft:item_{i}", count=64, slot=i) for i in range(36)
    )
    text = raw_observe(stack).content[0].text
    obs = json.loads(text)
    assert len(text) <= ANSWER_MAX_CHARS
    assert obs["blocks_more"] > 0 and obs["blocks"]
    assert len(obs["inventory"]) == 36  # self, inventory and chat are never cut


def test_visible_blocks_without_a_center_drops_nothing() -> None:
    cube = plains_cube()
    shown, hidden = visible_blocks(cube, None)
    assert len(shown) == len(cube) and hidden == 0



# --- a mind with no body (ADR-054) ------------------------------------------------------------


def test_a_bodiless_mind_answers_honestly() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    _server, impl, executor = build_mind(None, username="LastOrder03", start_c1=False)
    seen = impl.observe()
    assert seen["self"] == {"body": "none"} and "no body is attached" in seen["note"]
    for call in (lambda: impl.act("goto", {"x": 1, "y": 64, "z": 1}), impl.stop,
                 lambda: impl.say("привет"), lambda: impl.server_command("/spawn")):
        with pytest.raises(ToolError, match="no body is attached"):
            call()
    executor.events.put("board", lines=["#5 task from last-order"])
    assert impl.wait_events(1.0)["type"] == "board"  # the network still wakes her


def test_teardown_of_a_bodiless_mind_has_no_bridge_to_close() -> None:
    from runtime.mind.mcp_body import teardown_mind

    uvi, thread = FakeUvi(), FakeThread()
    calls: list[str] = []
    teardown_mind(uvi, thread, types.SimpleNamespace(stop=lambda: calls.append("stop")), None)
    assert calls == ["stop"]
