"""The network MCP of the hive (T-028) — the real streamable HTTP transport.

The full app (`create_app` with the mounted `/node/mcp` and `/last-order/mcp`) runs under
uvicorn on port 0 in a thread, and MCP clients reach it the way the harnesses will: with
per-client virtual keys in headers. This file checks the acceptance items: the sisters'
tool set, the Last Order tool set (absent for a sister structurally), the key-to-slot
identity (a rebirth moves the key; a bare runner header is nothing), `submit_will` gates,
`send` limits, and the two-client run — one sister proposes, Last Order decides, the
other sister's slice shows the result.
"""

from __future__ import annotations

import asyncio
import difflib
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
from runtime.hive.lifecycle import HiveLog, Lifecycle
from runtime.knowledge.schema import Will
from runtime.knowledge.store import KnowledgeStore
from runtime.lastorder.traits import Traits
from runtime.lastorder.will import assemble_will

T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
KEY1 = "sk-node-one"  # slot LastOrder01
KEY2 = "sk-node-two"  # slot LastOrder02
KEY_LO = "sk-last-order"
RKEY1 = "rk-runner-one"  # the node runner of slot LastOrder01 (T-027)

SISTER_TOOLS = {
    "knowledge", "inheritance", "propose", "vote", "send", "inbox", "submit_will",
    "board", "post", "claim", "update", "reply",  # the task board (T-042, TASKBOARD.md 10)
}
LO_TOOLS = {"proposals", "decide", "set_goals", "chronicle",
            "board", "post", "claim", "update", "reply",
            "send", "inbox",  # T-043: the desk answers the sisters on Misaka-net
            "request_grant", "grants"}  # the grant vault (T-046, TASKBOARD.md 5.3)


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


def traits(seed: int) -> Traits:
    return Traits(
        bravery=3,
        greed=7,
        talkativeness=5,
        trust_in_chat_and_nodes=4,
        superstition=9,
        quirk="считает крипера родственником",
        seed=seed,
    )


WILL_BODY = "Урок: не ходить в горы ночью.\nШутка: мне досталась жадность, я не просил."


def will_markdown(serial: int, slot: str = "LastOrder01") -> str:
    meta = dict(
        serial=serial,
        slot=slot,
        cause="game",
        born_at=T0.isoformat(),
        died_at=(T0 + timedelta(minutes=10)).isoformat(),
        lifetime_s=600,
        emergency=False,
        traits=traits(42).as_dict(),
        model="test",
    )
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()] + ["---", "", WILL_BODY]
    return "\n".join(lines)


class Stack:
    """The whole hive in-process: store, lifecycle, app with both MCP servers, uvicorn."""

    def __init__(self, tmp_path: Path, runner_keys: dict[str, str] | None = None):
        self.clock = FakeClock()
        self.rcon = FakeRcon()
        self.store = KnowledgeStore(tmp_path / "kb", git=False, now=lambda: self.clock().isoformat())
        self.log = HiveLog(tmp_path / "run" / "events.jsonl")
        self.run_dir = tmp_path / "run"
        self.runner_key = (runner_keys or {}).get("rk-slot") or next(iter(runner_keys or {}), "")
        self.lifecycle = Lifecycle(
            self.store, self.rcon, log=self.log, run_dir=self.run_dir, clock=self.clock
        )
        self.app = create_app(
            store=self.store,
            lifecycle=self.lifecycle,
            rcon=self.rcon,
            log=self.log,
            run_dir=self.run_dir,
            clock=self.clock,
            node_keys={KEY1: "LastOrder01", KEY2: "LastOrder02"},
            lo_key=KEY_LO,
            # explicit map (empty default = the open test mode of review R2-6); None
            # would read the env and refuse to start on an empty HIVE_RUNNER_KEYS
            runner_keys=runner_keys if runner_keys is not None else {},
        )
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error")
        self.uvicorn = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.uvicorn.run, daemon=True)
        self.thread.start()
        for _ in range(500):
            if self.uvicorn.started:
                break
            time.sleep(0.02)
        self.port = self.uvicorn.servers[0].sockets[0].getsockname()[1]

    # -- plumbing --------------------------------------------------------------------------

    def http(self, method: str, path: str, json_body: dict | None = None) -> httpx.Response:
        # the registry writes carry the runner key of the slot when one is configured (T-027)
        headers = {}
        if path.startswith("/registry/") and self.runner_key:
            headers["X-Runner-Key"] = self.runner_key
        return httpx.request(
            method, f"http://127.0.0.1:{self.port}{path}", json=json_body,
            headers=headers, timeout=30.0,
        )

    def birth(self, slot: str) -> int:
        response = self.http("POST", "/registry/birth", {"slot": slot, "traits": traits(hash(slot) % 1000).as_dict()})
        assert response.status_code == 200, response.text
        return int(response.json()["serial"])

    def retire(self, serial: int) -> None:
        response = self.http(
            "POST",
            "/registry/retire",
            {"serial": serial, "cause": "game", "will_text": will_markdown(serial)},
        )
        assert response.status_code == 200, response.text

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


def node_call(stack: Stack, key: str, name: str, args: dict, extra: dict | None = None):
    """One sister tool call over real MCP HTTP; returns the parsed JSON or raises."""

    async def run(client: Client) -> dict:
        result = await client.call_tool(name, args)
        text = result.content[0].text
        assert not result.is_error, text
        return json.loads(text)

    return stack.call(stack.node_url(), {"X-Node-Key": key, **(extra or {})}, run)


def node_error(stack: Stack, key: str, name: str, args: dict, extra: dict | None = None) -> str:
    async def run(client: Client) -> str:
        result = await client.call_tool(name, args)
        assert result.is_error, f"expected a tool error, got {result.content[0].text}"
        return result.content[0].text

    return stack.call(stack.node_url(), {"X-Node-Key": key, **(extra or {})}, run)


def lo_call(stack: Stack, name: str, args: dict, key: str = KEY_LO):
    async def run(client: Client) -> dict:
        result = await client.call_tool(name, args)
        text = result.content[0].text
        assert not result.is_error, text
        return json.loads(text)

    return stack.call(stack.lo_url(), {"X-Node-Key": key}, run)


def lo_error(stack: Stack, name: str, args: dict, key: str = KEY_LO) -> str:
    async def run(client: Client) -> str:
        result = await client.call_tool(name, args)
        assert result.is_error, f"expected a tool error, got {result.content[0].text}"
        return result.content[0].text

    return stack.call(stack.lo_url(), {"X-Node-Key": key}, run)


def tool_names(stack: Stack, url: str, key: str) -> set[str]:
    async def run(client: Client) -> set[str]:
        result = await client.list_tools()
        return {tool.name for tool in result.tools}

    return stack.call(url, {"X-Node-Key": key}, run)


# --- the two tool sets ------------------------------------------------------------------------


def test_sister_tool_set(stack: Stack) -> None:
    stack.birth("LastOrder01")
    assert tool_names(stack, stack.node_url(), KEY1) == SISTER_TOOLS


def test_last_order_tool_set_and_sister_lack(stack: Stack) -> None:
    stack.birth("LastOrder01")
    assert tool_names(stack, stack.lo_url(), KEY_LO) == LO_TOOLS
    # a sister never even sees the admin tools; the board set (T-042) and the
    # Misaka-net pair (T-043) are shared by design
    shared = {"board", "post", "claim", "update", "reply", "send", "inbox"}
    assert not ((SISTER_TOOLS - shared) & (LO_TOOLS - shared))


def test_cross_key_refusals(stack: Stack) -> None:
    stack.birth("LastOrder01")
    # a sister key on the Last Order server
    assert "Last Order" in lo_error(stack, "proposals", {}, key=KEY1)
    # the Last Order key is not a node key
    assert "Last Order" in node_error(stack, KEY_LO, "inbox", {})


def test_health_still_serves_next_to_the_mounts(stack: Stack) -> None:
    response = stack.http("GET", "/health")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


# --- identity (review P1-1, P1-2) --------------------------------------------------------------


def test_identity_needs_a_known_key(stack: Stack) -> None:
    stack.birth("LastOrder01")
    assert "X-Node-Key" in node_error(stack, "", "inbox", {})
    assert "unknown node key" in node_error(stack, "sk-nobody", "inbox", {})


def test_runner_header_is_a_crosscheck_only(stack: Stack) -> None:
    serial = stack.birth("LastOrder01")
    # the header alone is not identity
    assert "X-Node-Key" in node_error(stack, "", "inbox", {}, extra={"X-Runner-Node": str(serial)})
    # a header agreeing with the key passes
    out = node_call(stack, KEY1, "inbox", {}, extra={"X-Runner-Node": str(serial)})
    assert out == {"messages": []}
    # a header claiming somebody else's serial is a refusal
    assert "does not match" in node_error(
        stack, KEY1, "inbox", {}, extra={"X-Runner-Node": str(serial + 500)}
    )
    assert "X-Runner-Node" in node_error(stack, KEY1, "inbox", {}, extra={"X-Runner-Node": "abc"})


def test_key_follows_the_slot_through_rebirth(stack: Stack) -> None:
    first = stack.birth("LastOrder01")
    second = stack.birth("LastOrder02")
    p1 = node_call(
        stack, KEY1, "propose",
        {"kind": "knowledge", "payload": {"kind": "danger", "claim": "Лава светится и убивает.", "about": ["itm:lava"]}},
    )
    queue = lo_call(stack, "proposals", {})["proposals"]
    assert queue[0]["author"] == f"{first:05d}"
    stack.retire(first)
    reborn = stack.birth("LastOrder01")
    assert reborn != first
    p2 = node_call(
        stack, KEY2, "send", {"to": str(reborn), "text": "проверка ключа после перерождения"},
    )
    inbox = node_call(stack, KEY1, "inbox", {})["messages"]
    assert [m["from_serial"] for m in inbox] == [second]  # the same key now answers for the new node
    assert p1["proposal_id"].startswith("p-")
    assert p2["sent"] == [f"{reborn:05d}"]


# --- the sisters' tools -------------------------------------------------------------------------


def test_knowledge_slice_and_query(stack: Stack) -> None:
    first = stack.birth("LastOrder01")
    stack.birth("LastOrder02")
    node_call(
        stack, KEY1, "propose",
        {"kind": "knowledge", "payload": {"kind": "danger", "claim": "Крипер взрывается у самого бока.", "about": ["ent:creeper"]}},
    )
    out = node_call(stack, KEY2, "knowledge", {"about": ["ent:creeper"], "query": "крипер"})
    assert out["catalog"], "the catalog lists the new record"
    about_ids = [e["id"] for e in out["about"]]
    assert about_ids or [e["id"] for e in out["pending_votes"]]
    assert any("Крипер" in e["claim"] for e in out["matched"])
    assert all(e["status"] != "retired" for e in out["matched"])


def test_vote_is_the_service_wrapper(stack: Stack) -> None:
    stack.birth("LastOrder01")
    second = stack.birth("LastOrder02")
    proposal = node_call(
        stack, KEY1, "propose",
        {"kind": "knowledge", "payload": {"kind": "fact", "claim": "Каменный уголь встречается в склонах.", "about": ["itm:coal"]}},
    )
    out = node_call(stack, KEY2, "vote", {"entry_id": proposal["record_id"], "accept": True, "why": "видел то же"})
    assert out == {"voted": proposal["record_id"], "accept": True}
    record = stack.store.record(proposal["record_id"])
    assert [(v.node, v.accept) for v in record.votes] == [(second, True)]


def test_duplicate_proposal_is_evidence(stack: Stack) -> None:
    stack.birth("LastOrder01")
    stack.birth("LastOrder02")
    payload = {"kind": "danger", "claim": "Скелет стреляет с расстояния.", "about": ["ent:skeleton"]}
    first = node_call(stack, KEY1, "propose", {"kind": "knowledge", "payload": payload})
    second = node_call(stack, KEY2, "propose", {"kind": "knowledge", "payload": payload})
    assert second["record_id"] == first["record_id"]
    assert len(stack.store.record(first["record_id"]).evidence) == 2


def test_send_inbox_and_limits(stack: Stack) -> None:
    first = stack.birth("LastOrder01")
    second = stack.birth("LastOrder02")
    assert "not alive" in node_error(stack, KEY1, "send", {"to": "99", "text": "ты там?"})
    assert "yourself" in node_error(stack, KEY1, "send", {"to": str(first), "text": "привет я"})
    assert "160" in node_error(stack, KEY1, "send", {"to": str(second), "text": "ы" * 161})
    assert "serial number or 'all'" in node_error(stack, KEY1, "send", {"to": "зачем", "text": "?"})
    out = node_call(stack, KEY1, "send", {"to": str(second), "text": "столб света у реки — мой"})
    assert out == {"sent": [f"{second:05d}"]}
    inbox = node_call(stack, KEY2, "inbox", {})["messages"]
    assert [(m["from_serial"], m["text"]) for m in inbox] == [(first, "столб света у реки — мой")]
    assert node_call(stack, KEY2, "inbox", {}) == {"messages": []}  # delivered once


def test_send_all_broadcasts_to_the_living(stack: Stack) -> None:
    first = stack.birth("LastOrder01")
    second = stack.birth("LastOrder02")
    third = stack.birth("LastOrder03")
    out = node_call(stack, KEY1, "send", {"to": "all", "text": "ночью не расходиться"})
    assert out == {"sent": [f"{second:05d}", f"{third:05d}"]}
    assert len(node_call(stack, KEY2, "inbox", {})["messages"]) == 1
    assert node_call(stack, KEY1, "inbox", {}) == {"messages": []}  # not her own addressee


def test_inheritance_lists_the_dead(stack: Stack) -> None:
    first = stack.birth("LastOrder01")
    stack.retire(first)
    second = stack.birth("LastOrder01")
    out = node_call(stack, KEY1, "inheritance", {})
    assert f"{first:05d} game" in out["predecessors_summary"]
    assert "не ходить в горы ночью" in out["inherited_notes"]


def test_answers_are_compact_json(stack: Stack) -> None:
    """T-056: every answer stays in the sister's context, so it goes out without the
    SDK's indent=2; the structured side is the same dict."""
    first = stack.birth("LastOrder01")
    stack.retire(first)
    stack.birth("LastOrder01")

    async def run(client: Client):
        return await client.call_tool("inheritance", {})

    result = stack.call(stack.node_url(), {"X-Node-Key": KEY1}, run)
    text = result.content[0].text
    assert not result.is_error, text
    assert "\n" not in text.strip() and '": ' not in text
    assert json.loads(text) == result.structured_content


# --- submit_will ---------------------------------------------------------------------------------


def test_submit_will_gates(stack: Stack) -> None:
    serial = stack.birth("LastOrder01")
    will = will_markdown(serial)
    # alive: even the will-session header does not help
    assert "not dead" in node_error(
        stack, KEY1, "submit_will", {"text": will}, extra={"X-Runner-Session": "will"}
    )
    # dead (the mark mind-N will owe hive; T-027 wires the call) but no session header
    stack.lifecycle.mark_dead(serial)
    assert "will session" in node_error(stack, KEY1, "submit_will", {"text": will})
    # somebody else's serial in the frontmatter
    assert "names node" in node_error(
        stack, KEY1, "submit_will", {"text": will_markdown(serial + 1)}, extra={"X-Runner-Session": "will"}
    )
    # the real submission stages once
    out = node_call(stack, KEY1, "submit_will", {"text": will}, extra={"X-Runner-Session": "will"})
    assert out == {"staged": True, "serial": serial}
    staged = stack.run_dir / "pending_wills" / f"{serial:05d}.md"
    assert staged.read_text(encoding="utf-8") == WILL_BODY  # only the body is staged (T-040)
    assert "already submitted" in node_error(
        stack, KEY1, "submit_will", {"text": will}, extra={"X-Runner-Session": "will"}
    )


def test_submit_will_takes_the_body_alone(stack: Stack) -> None:
    """T-040: a sister sends just her 2-4 lines — no frontmatter, no service fields."""
    serial = stack.birth("LastOrder01")
    stack.lifecycle.mark_dead(serial)
    out = node_call(stack, KEY1, "submit_will", {"text": WILL_BODY}, extra={"X-Runner-Session": "will"})
    assert out == {"staged": True, "serial": serial}
    staged = stack.run_dir / "pending_wills" / f"{serial:05d}.md"
    assert staged.read_text(encoding="utf-8") == WILL_BODY


def test_submit_will_refuses_broken_bodies_in_one_sentence(stack: Stack) -> None:
    """T-040: one plain sentence per refusal — no schema field names; nothing is
    staged by a refusal, so the fixed body still goes through."""
    serial = stack.birth("LastOrder01")
    stack.lifecycle.mark_dead(serial)

    def err(text: str) -> str:
        return node_error(
            stack, KEY1, "submit_will", {"text": text}, extra={"X-Runner-Session": "will"}
        )

    assert "trait numbers" in err("Урок: не ходить в горы ночью.\nШутка: жадность 7, я не просил.")
    assert "2-4 lines" in err("один\nдва\nтри\nчетыре\nпять")
    assert "blank lines" in err("Урок: не ходить в горы ночью.\n\nШутка: молчу.")
    out = node_call(stack, KEY1, "submit_will", {"text": WILL_BODY}, extra={"X-Runner-Session": "will"})
    assert out == {"staged": True, "serial": serial}


def test_staged_body_becomes_a_full_will_in_the_base(stack: Stack) -> None:
    """The T-040 contract end to end: the body alone is staged, the runner assembles
    will.md from the fields the environment owns, retire writes it to the base with
    emergency: false."""
    serial = stack.birth("LastOrder01")
    birth = json.loads(
        (stack.store.root / "nodes" / f"{serial:05d}" / "birth.json").read_text(encoding="utf-8")
    )
    stack.lifecycle.mark_dead(serial)
    out = node_call(stack, KEY1, "submit_will", {"text": WILL_BODY}, extra={"X-Runner-Session": "will"})
    assert out == {"staged": True, "serial": serial}
    # the runner's part (NodeRunner._will_of): the staged body plus the service fields
    died_at = (T0 + timedelta(minutes=45)).isoformat()
    will = assemble_will(
        serial=serial,
        slot="LastOrder01",
        cause="ttl",
        born_at=birth["born_at"],
        died_at=died_at,
        traits=Traits(**birth["traits"]),
        body=WILL_BODY,
        model="GLM-5.3-Flash",
        emergency=False,
    )
    response = stack.http(
        "POST", "/registry/retire",
        {"serial": serial, "cause": "ttl", "will_text": will.to_markdown()},
    )
    assert response.status_code == 200, response.text
    final = Will.from_markdown(
        (stack.store.root / "nodes" / f"{serial:05d}" / "will.md").read_text(encoding="utf-8")
    )
    assert final.body == WILL_BODY
    assert final.meta.emergency is False
    assert final.meta.serial == serial
    assert final.meta.slot == "LastOrder01"
    assert final.meta.cause == "ttl"
    assert final.meta.born_at == birth["born_at"]
    assert final.meta.died_at == died_at
    assert final.meta.lifetime_s == 45 * 60
    assert final.meta.traits == Traits(**birth["traits"])
    assert final.meta.model == "GLM-5.3-Flash"


def test_mark_dead_endpoint_opens_the_will_path(stack: Stack) -> None:
    serial = stack.birth("LastOrder01")
    will = will_markdown(serial)
    # alive: even the will-session header does not help
    assert "not dead" in node_error(
        stack, KEY1, "submit_will", {"text": will}, extra={"X-Runner-Session": "will"}
    )
    # the death signal mind-N owes hive (review R1-3): POST /registry/mark_dead
    response = stack.http("POST", "/registry/mark_dead", {"serial": serial})
    assert response.status_code == 200
    response = stack.http("POST", "/registry/mark_dead", {"serial": serial})  # idempotent
    assert response.status_code == 200
    out = node_call(stack, KEY1, "submit_will", {"text": will}, extra={"X-Runner-Session": "will"})
    assert out == {"staged": True, "serial": serial}
    # unknown serial: the single error envelope
    response = stack.http("POST", "/registry/mark_dead", {"serial": 999})
    assert response.status_code == 404
    assert response.json()["error"]["kind"] == "unknown_node"


# --- Last Order's tools --------------------------------------------------------------------------


def test_lo_decide_skill_and_layers(stack: Stack) -> None:
    stack.birth("LastOrder01")
    proposal = node_call(
        stack, KEY1, "propose",
        {"kind": "skill", "payload": {"description": "Мост через ущелье", "script": "#!/bin/sh\necho bridge"}},
    )
    queue = lo_call(stack, "proposals", {})["proposals"]
    assert [q["id"] for q in queue] == [proposal["proposal_id"]]
    decided = lo_call(
        stack, "decide",
        {"proposal_id": proposal["proposal_id"], "accept": True, "why": "годится"},
    )
    record = stack.store.record(decided["record_id"])
    assert record.kind == "skill" and record.status == "confirmed"
    assert "already accepted" in lo_error(
        stack, "decide", {"proposal_id": proposal["proposal_id"], "accept": False, "why": "передумал"}
    )
    lo_call(stack, "set_goals", {"goals": [{"text": "Найти железо", "why": "инструменты"}]})
    lo_call(stack, "chronicle", {"text": "Первый общий навык принят."})
    goals = (stack.store.root / "base" / "goals.md").read_text(encoding="utf-8")
    assert "Найти железо" in goals
    chronicle = (stack.store.root / "base" / "chronicle.md").read_text(encoding="utf-8")
    assert "Первый общий навык принят." in chronicle


def test_lo_decide_instructions(stack: Stack) -> None:
    stack.birth("LastOrder01")
    old, new = "Свет дороже гриба.\n", "Свет дороже гриба.\nУкрытие до ночи.\n"
    stack.store.commit_layer("seed", {"base/instructions.md": old})  # the initial common instructions
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/base/instructions.md",
            tofile="b/base/instructions.md",
        )
    )
    proposal = node_call(stack, KEY1, "propose", {"kind": "instructions", "payload": {"diff": diff}})
    lo_call(stack, "decide", {"proposal_id": proposal["proposal_id"], "accept": True, "why": "разумно"})
    instructions = (stack.store.root / "base" / "instructions.md").read_text(encoding="utf-8")
    assert instructions == new


def test_lo_tools_need_the_lo_key(stack: Stack) -> None:
    stack.birth("LastOrder01")
    assert "Last Order key" in lo_error(stack, "proposals", {}, key="sk-node-one")


# --- the acceptance run (task criterion 6) --------------------------------------------------------


def test_two_clients_proposal_decision_slice(stack: Stack) -> None:
    stack.birth("LastOrder01")
    stack.birth("LastOrder02")
    # sister one proposes knowledge
    proposal = node_call(
        stack, KEY1, "propose",
        {"kind": "knowledge", "payload": {"kind": "danger", "claim": "Глубокая темнота полна стонущих.", "about": ["ent:zombie"]}},
    )
    # Last Order decides
    decided = lo_call(
        stack, "decide",
        {"proposal_id": proposal["proposal_id"], "accept": True, "why": "подтверждено двумя"},
    )
    assert decided["status"] == "accepted"
    # sister two sees the confirmed knowledge in her slice
    out = node_call(stack, KEY2, "knowledge", {"about": ["ent:zombie"], "query": "стонущих"})
    matched = [e for e in out["matched"] if e["id"] == proposal["record_id"]]
    assert matched and matched[0]["status"] == "confirmed"


# --- the runner key behind the will-session mark (T-027 plan review P1-1) ------------------------


def test_submit_will_session_mark_needs_the_runner_key(tmp_path: Path) -> None:
    """With HIVE_RUNNER_KEYS configured, X-Runner-Session: will is worthless without the
    slot's runner key: a sister with bash cannot forge her own will session."""
    stack = Stack(tmp_path, runner_keys={RKEY1: "LastOrder01"})
    try:
        serial = stack.birth("LastOrder01")
        stack.lifecycle.mark_dead(serial)
        will = will_markdown(serial)
        # the session mark alone no longer opens the gate
        assert "runner key" in node_error(
            stack, KEY1, "submit_will", {"text": will}, extra={"X-Runner-Session": "will"}
        )
        # a runner key of another slot does not either
        assert "runner key" in node_error(
            stack, KEY1, "submit_will", {"text": will},
            extra={"X-Runner-Session": "will", "X-Runner-Key": "rk-someone-else"},
        )
        # the node key still identifies, the runner key opens: the will stages once
        out = node_call(
            stack, KEY1, "submit_will", {"text": will},
            extra={"X-Runner-Session": "will", "X-Runner-Key": RKEY1},
        )
        assert out == {"staged": True, "serial": serial}
    finally:
        stack.uvicorn.should_exit = True
        stack.thread.join(timeout=10)


# --- the desk's Misaka-net pair (T-043) -------------------------------------------------------


def test_last_order_answers_a_sister_from_address_zero(stack: Stack) -> None:
    serial = stack.birth("LastOrder01")
    # the desk speaks as `last-order` (address 0), never as a registry serial
    out = lo_call(stack, "send", {"to": str(serial), "text": "Network note: the lava lake moved."})
    assert out == {"sent": ["00001"]}
    messages = node_call(stack, KEY1, "inbox", {})["messages"]
    assert [m["from_serial"] for m in messages] == [0]
    assert "lava lake" in messages[0]["text"]
    # the desk's own inbox drains once and is empty: nothing addresses address 0 yet
    assert lo_call(stack, "inbox", {}) == {"messages": []}
    # a sister key does not open the desk's send — each server keeps its own pair
    assert "Last Order" in lo_error(stack, "send", {"to": "1", "text": "hi"}, key=KEY1)


def test_last_order_broadcast_and_limits_mirror_the_sisters(stack: Stack) -> None:
    stack.birth("LastOrder01")
    stack.birth("LastOrder02")
    out = lo_call(stack, "send", {"to": "all", "text": "Chronicle hour."})
    assert out["sent"]  # both sisters
    assert "not alive" in lo_error(stack, "send", {"to": "999", "text": "hi"})
    assert "non-empty" in lo_error(stack, "send", {"to": "1", "text": "  "})


# --- the desk's wake feed (T-043) --------------------------------------------------------------


def _lo_wake(stack: Stack, method: str, path: str, body: dict | None = None, key: str = KEY_LO):
    return httpx.request(
        method, f"http://127.0.0.1:{stack.port}{path}", json=body,
        headers={"X-Node-Key": key}, timeout=30.0,
    )


def test_wake_feed_appends_reads_and_acks_under_the_desk_key(stack: Stack) -> None:
    first = _lo_wake(stack, "POST", "/lo/wake", {"kind": "timer", "detail": "10 min since"})
    assert first.status_code == 200, first.text
    seq1 = first.json()["seq"]
    seq2 = _lo_wake(stack, "POST", "/lo/wake", {"kind": "death", "detail": "00002"}).json()["seq"]
    assert seq2 == seq1 + 1
    # the feed since 0 carries both, with the top of the whole log
    feed = _lo_wake(stack, "GET", "/lo/wake?since=0").json()
    assert [e["seq"] for e in feed["events"]] == [seq1, seq2]
    assert feed["top"] == seq2
    assert [e["kind"] for e in feed["events"]] == ["timer", "death"]
    # the ack moves the cursor, and since= asks only for what follows
    assert _lo_wake(stack, "POST", "/lo/wake/ack", {"seq": seq1}).status_code == 200
    tail = _lo_wake(stack, "GET", "/lo/wake").json()  # no since: the acked cursor
    assert [e["seq"] for e in tail["events"]] == [seq2]
    # validation
    assert _lo_wake(stack, "POST", "/lo/wake", {"kind": ""}).status_code == 422
    assert _lo_wake(stack, "POST", "/lo/wake/ack", {"seq": -1}).status_code == 422


def test_wake_feed_opens_with_the_desk_key_alone(stack: Stack) -> None:
    stack.birth("LastOrder01")
    # a sister's node key, the runner key, no key at all — all refused
    for key in (KEY1, ""):
        response = _lo_wake(stack, "POST", "/lo/wake", {"kind": "timer"}, key=key or "x")
        assert response.status_code == 401, (key, response.text)
    response = httpx.request(
        "POST", f"http://127.0.0.1:{stack.port}/lo/wake",
        json={"kind": "timer"}, headers={"X-Runner-Key": RKEY1}, timeout=30.0,
    )
    assert response.status_code == 401


def test_wake_feed_survives_a_hive_restart_and_caps_its_tail(stack: Stack, tmp_path: Path) -> None:
    seq = _lo_wake(stack, "POST", "/lo/wake", {"kind": "timer", "detail": "one"}).json()["seq"]
    # a "restart": a fresh app over the same run dir keeps the seq and the cursor
    stack.close()
    stack2 = Stack(tmp_path)
    try:
        feed = _lo_wake(stack2, "GET", "/lo/wake?since=0").json()
        assert [e["seq"] for e in feed["events"]] == [seq]
        # the cap: the tail keeps only the recent history
        from runtime.hive.app import WAKE_FEED_MAX
        for i in range(WAKE_FEED_MAX + 5):
            _lo_wake(stack2, "POST", "/lo/wake", {"kind": "timer", "detail": str(i)})
        events = _lo_wake(stack2, "GET", "/lo/wake?since=0").json()["events"]
        assert len(events) == WAKE_FEED_MAX
        assert events[0]["seq"] > seq  # the oldest were folded away
    finally:
        stack2.close()
