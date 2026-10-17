"""HiveClient (T-022): the ports over HTTP, and the section 9 offline contract.

The port-level scenarios run against `HiveClient` whose transport is a `TestClient`
of a real app — no network (the criterion), while `tests/test_hive_api.py` pins the
endpoints themselves. Offline behaviour is exercised through a switchable transport:
reads fall back to the last successful answer marked `stale`; the four bufferable
writes land in `run/hive_outbox.jsonl` and are flushed strictly in order before any
next call and before `retire` (review P1-3); a permanent 4xx drops with a client
event, 5xx and connect errors retry (P1-5); a read timeout is not retried
immediately (P1-7).
"""

from __future__ import annotations

import httpx
import json
import types
from pathlib import Path

import pytest

from runtime.hive.client import HiveClient, HiveRejected, HiveUnavailable
from runtime.ports import (
    KnowledgeCandidate,
    Memory,
    NodeComms,
    NodeMessage,
    NodeRegistry,
    Vote,
)
from runtime.knowledge.store import StoreError
from test_hive_api import make_app_stack, traits, will_text


def outbox_lines(client: HiveClient) -> list[dict]:
    path = client._outbox_path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def client_events(client: HiveClient) -> list[dict]:
    path = client._events_path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def born_via_client(client: HiveClient, slot: str = "LastOrder01", seed: int = 42):
    return client.birth(slot, traits(seed))


def candidate_id_of(client: HiveClient, serial: int, handle: str) -> str:
    sl = client.knowledge(serial, [handle])
    for entry in sl.pending_votes + sl.about + sl.fresh:
        return entry.id
    raise AssertionError("no record to vote on")


# --- the ports ----------------------------------------------------------------------------


def test_client_is_all_three_ports(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    client = HiveClient(transport=stack.client, run_dir=tmp_path / "mind-run")
    assert isinstance(client, Memory)
    assert isinstance(client, NodeRegistry)
    assert isinstance(client, NodeComms)


def test_memory_over_the_wire(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    client = HiveClient(transport=stack.client, run_dir=tmp_path / "mind-run")
    node = born_via_client(client)

    inheritance = client.inheritance(node.serial)
    assert inheritance.active_nodes_summary  # a real Inheritance, not a dict

    client.store_core_memory(node.serial, "goal: выжить у спавна")
    assert client.core_memory(node.serial).startswith("goal:")

    client.submit_candidate(
        node.serial,
        KnowledgeCandidate(
            kind="danger",
            claim="Стонущий прямоходящий бьёт на сближении; держать дистанцию.",
            about=("ent:zombie",),
            evidence_event_ids=("e-918",),
        ),
    )
    sl = client.knowledge(node.serial, ["ent:zombie"])
    assert any("Стонущий" in e.claim for e in sl.about + sl.pending_votes + sl.fresh)

    other = born_via_client(client, "LastOrder02", seed=7)
    entry_id = candidate_id_of(client, other.serial, "ent:zombie")
    client.submit_vote(other.serial, Vote(entry_id=entry_id, accept=True, why="видел то же"))
    assert any(
        v.node == other.serial for r in stack.store.records() for v in r.votes
    )
    assert client.stale is False


def test_registry_over_the_wire(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    client = HiveClient(transport=stack.client, run_dir=tmp_path / "mind-run")
    node = born_via_client(client)
    assert [(b.serial, b.slot) for b in client.alive()] == [(node.serial, "LastOrder01")]

    other = born_via_client(client, "LastOrder02", seed=7)
    assert other.serial == node.serial + 1 and other.ttl_min == 45

    client.request_death(other.serial, "stuck")  # the node asked (ADR-026)
    assert stack.rcon.kills == ["LastOrder02"]
    client.retire(other.serial, "game", will_text(other.serial, other.born_at))
    assert [b.serial for b in client.alive()] == [node.serial]


def test_comms_over_the_wire(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    client = HiveClient(transport=stack.client, run_dir=tmp_path / "mind-run")
    node = born_via_client(client)
    other = born_via_client(client, "LastOrder02", seed=7)

    client.send(NodeMessage(from_serial=node.serial, to_serial=other.serial, text="у спавна стонущий"))
    inbox = client.inbox(other.serial)
    assert len(inbox) == 1 and inbox[0].text == "у спавна стонущий"
    assert client.inbox(other.serial) == []  # delivered once
    assert client.chatter_turn(node.serial) is None  # the director is T-017


# --- the switchable transport ------------------------------------------------------------------


class SwitchableTransport:
    """Wraps the TestClient: `down` mimics an unreachable hive, `flaky` fails N times."""

    def __init__(self, inner):
        self.inner = inner
        self.down = False
        self.flaky = 0

    def post(self, path, json=None):
        return self._send(lambda: self.inner.post(path, json=json))

    def get(self, path):
        return self._send(lambda: self.inner.get(path))

    def _send(self, fn):
        if self.down:
            raise httpx.ConnectError("hive is down")
        if self.flaky > 0:
            self.flaky -= 1
            raise httpx.ConnectError("connect refused")
        return fn()


@pytest.fixture()
def offline(tmp_path: Path):
    stack = make_app_stack(tmp_path)
    switch = SwitchableTransport(stack.client)
    client = HiveClient(transport=switch, run_dir=tmp_path / "mind-run", backoff_s=0.01, retries=2)
    return types.SimpleNamespace(stack=stack, switch=switch, client=client, run_dir=tmp_path / "mind-run")


def test_reads_fall_back_to_cache_marked_stale(offline) -> None:
    client = offline.client
    node = born_via_client(client)
    client.store_core_memory(node.serial, "goal: дойти до воды")
    warm_core = client.core_memory(node.serial)
    warm_alive = client.alive()
    assert client.stale is False

    offline.switch.down = True
    assert client.core_memory(node.serial) == warm_core
    assert client.stale is True
    assert list(client.alive()) == list(warm_alive)
    assert client.stale is True

    offline.switch.down = False
    assert client.core_memory(node.serial) == warm_core
    assert client.stale is False  # a live answer clears the mark


def test_reads_without_cache_raise_and_chatter_degrades(offline) -> None:
    client = offline.client
    offline.switch.down = True
    with pytest.raises(HiveUnavailable):
        client.inheritance(1)
    assert client.chatter_turn(1) is None  # a turn is skippable


def test_writes_buffer_to_disk_in_order_and_flush(offline) -> None:
    stack, client = offline.stack, offline.client
    node = born_via_client(client)
    other = born_via_client(client, "LastOrder02", seed=7)
    client.submit_candidate(
        node.serial,
        KnowledgeCandidate(kind="fact", claim="Вода под тонким слоем кончается камнем.", about=("blk:stone",)),
    )
    entry_id = candidate_id_of(client, other.serial, "blk:stone")

    offline.switch.down = True
    client.store_core_memory(node.serial, "goal: выжить")
    client.submit_vote(other.serial, Vote(entry_id=entry_id, accept=True, why="вижу так же"))
    client.send(NodeMessage(from_serial=node.serial, to_serial=other.serial, text="смотри под ноги"))

    lines = outbox_lines(client)
    assert [line["path"] for line in lines] == [
        "/memory/store_core_memory",
        "/memory/submit_vote",
        "/comms/send",
    ]  # strictly in order

    offline.switch.down = False
    inbox = client.inbox(other.serial)  # any next call flushes first
    assert outbox_lines(client) == []
    assert client.core_memory(node.serial).startswith("goal: выжить")
    assert any(v.node == other.serial for r in stack.store.records() for v in r.votes)
    assert len(inbox) == 1 and inbox[0].text == "смотри под ноги"


def test_retire_flushes_the_buffer_first(offline) -> None:
    # P1-3: knowledge buffered while offline must land before the node is retired,
    # otherwise the flush would hit 409 and the dead node's candidate would be lost.
    stack, client = offline.stack, offline.client
    node = born_via_client(client)

    offline.switch.down = True
    client.submit_candidate(
        node.serial,
        KnowledgeCandidate(kind="danger", claim="Лава под гравием выдаёт себя светом.", about=("blk:lava",)),
    )
    offline.switch.down = False
    client.retire(node.serial, "game", will_text(node.serial, node.born_at))
    assert outbox_lines(client) == []
    assert any(
        r.claim.startswith("Лава под гравием") for r in stack.store.records()
    ), "the buffered candidate must survive its author's retire"


def test_critical_calls_are_not_buffered(offline) -> None:
    client = offline.client
    offline.switch.down = True
    with pytest.raises(HiveUnavailable):
        client.birth("LastOrder01", traits())
    with pytest.raises(HiveUnavailable):
        client.request_death(1, "stuck")
    with pytest.raises(HiveUnavailable):
        client.retire(1, "game", "will")
    assert outbox_lines(client) == []


def test_rejected_buffered_write_drops_with_event(offline) -> None:
    client = offline.client
    node = born_via_client(client)

    offline.switch.down = True
    # raw ids never enter the base: the store rejects it, the client drops it
    client.submit_candidate(
        node.serial,
        KnowledgeCandidate(kind="danger", claim="мимо ползёт minecraft:creeper", about=("ent:creeper",)),
    )
    offline.switch.down = False
    client.alive()  # triggers the flush
    assert outbox_lines(client) == []  # dropped, not retried forever (P1-5/P1-9)
    events = client_events(client)
    assert events and events[-1]["type"] == "outbox.drop"
    assert events[-1]["kind"] == "invalid_candidate"


def test_transient_store_error_is_retried_not_dropped(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    client = HiveClient(transport=stack.client, run_dir=tmp_path / "mind-run", backoff_s=0.01)
    node = born_via_client(client)

    real_store = stack.app.state.hive.store
    state = {"failures_left": 2}

    def flaky_core_memory(serial: int) -> str:
        if state["failures_left"] > 0:
            state["failures_left"] -= 1
            raise StoreError("knowledge base lock timeout")  # maps to 503, not 4xx
        return real_store.core_memory(serial)

    stack.app.state.hive.store = _PatchedStore(real_store, flaky_core_memory)
    text = client.core_memory(node.serial)  # two 503s, then the real answer
    assert isinstance(text, str) and state["failures_left"] == 0
    assert client.stale is False


class _PatchedStore:
    """Everything from the real store except one flaky method."""

    def __init__(self, real, core_memory):
        self._real = real
        self._core_memory = core_memory

    def core_memory(self, serial):
        return self._core_memory(serial)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_connect_errors_retry_with_growing_pause(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    switch = SwitchableTransport(stack.client)
    pauses: list[float] = []
    client = HiveClient(
        transport=switch,
        run_dir=tmp_path / "mind-run",
        retries=2,
        backoff_s=0.05,
        sleep=lambda s: pauses.append(round(s, 4)),
    )
    switch.flaky = 2
    node = born_via_client(client)  # fails twice, then lands
    assert node.serial == 1
    assert pauses == [0.05, 0.1]  # growing


class TimeoutOnce:
    """A read timeout on the first submit_candidate: maybe-delivered, no fast retry."""

    def __init__(self, inner):
        self.inner = inner
        self.timed_out = False

    def post(self, path, json=None):
        if not self.timed_out and path == "/memory/submit_candidate":
            self.timed_out = True
            raise httpx.ReadTimeout("read timed out")
        return self.inner.post(path, json=json)

    def get(self, path):
        return self.inner.get(path)


def test_read_timeout_buffers_without_immediate_retry(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    pauses: list[float] = []
    client = HiveClient(
        transport=TimeoutOnce(stack.client),
        run_dir=tmp_path / "mind-run",
        retries=2,
        backoff_s=0.05,
        sleep=lambda s: pauses.append(s),
    )
    node = born_via_client(client)
    client.submit_candidate(
        node.serial,
        KnowledgeCandidate(kind="fact", claim="Таймаут не порождает дубль.", about=("blk:stone",)),
    )
    assert pauses == []  # P1-7: a timeout goes to the buffer, not into fast retries
    assert [line["path"] for line in outbox_lines(client)] == ["/memory/submit_candidate"]


def test_rejected_direct_call_raises(tmp_path: Path) -> None:
    stack = make_app_stack(tmp_path)
    client = HiveClient(transport=stack.client, run_dir=tmp_path / "mind-run")
    with pytest.raises(HiveRejected) as info:
        client.core_memory(99)
    assert info.value.status == 404 and info.value.kind == "unknown_node"
