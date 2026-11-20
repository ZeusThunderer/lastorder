"""ADR-055: one sister's voice — lines in order, never over each other, each line in chat
as its voice starts, never lost when the voice fails."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from runtime.mind.mcp_body import BodyMcp, ToolError
from runtime.voice.speaker import BYTES_PER_S, FIRST_CHUNK, Speaker, split_parts


class Events:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: list[tuple[str, str]] = []
        self.journal: list[dict] = []
        self.playing = 0
        self.max_playing = 0

    def add(self, kind: str, text: str) -> None:
        with self.lock:
            self.items.append((kind, text))

    def record(self, kind: str, **fields) -> None:
        with self.lock:
            self.journal.append({"kind": kind, **fields})


class FakeInlet:
    """Stands in for socat→paplay: plays for `play_s` after the half-close, then EOF."""

    def __init__(self, ev: Events, play_s: float = 0.05):
        self.ev, self.play_s = ev, play_s
        self.buf = bytearray()
        self.started = False

    def settimeout(self, t):
        pass

    def sendall(self, data: bytes):
        if not self.started:
            self.started = True
            with self.ev.lock:
                self.ev.playing += 1
                self.ev.max_playing = max(self.ev.max_playing, self.ev.playing)
            self.ev.add("audio", bytes(data[:8]).decode("ascii", "replace"))
        self.buf += data

    def shutdown(self, how):
        pass

    def recv(self, n):
        time.sleep(self.play_s)
        with self.ev.lock:
            self.ev.playing -= 1
        return b""

    def close(self):
        pass


def pcm_for(text: str, seconds: float = 0.2) -> bytes:
    """Audio whose first bytes name the line, so the inlet can tell lines apart."""
    tag = text.encode("ascii", "replace")[:8].ljust(8, b" ")
    return tag + b"\0" * (int(BYTES_PER_S * seconds) - 8)


def make(ev: Events, *, synth=None, connect=None, play_s=0.05) -> Speaker:
    return Speaker(
        synth=synth or (lambda text: (pcm_for(text), "")),
        connect=connect or (lambda: FakeInlet(ev, play_s)),
        send_chat=lambda text: ev.add("chat", text),
        journal=ev,
    ).start()


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def test_lines_are_spoken_in_order_and_never_overlap():
    ev = Events()
    sp = make(ev)
    for text in ("line-1", "line-2", "line-3"):   # each said once the previous one has started
        sp.say(text)
        wait_for(lambda: ("chat", text) in ev.items)
    wait_for(lambda: len(ev.journal) == 3)
    sp.close()
    assert [t for k, t in ev.items if k == "chat"] == ["line-1", "line-2", "line-3"]
    assert ev.max_playing == 1
    assert all(j["ok"] for j in ev.journal)


def test_the_chat_line_follows_the_start_of_its_audio():
    ev = Events()
    sp = make(ev)
    sp.say("hello")
    wait_for(lambda: ("chat", "hello") in ev.items)
    sp.close()
    assert ev.items.index(("audio", "hello   ")) < ev.items.index(("chat", "hello"))


def test_the_whole_clip_reaches_the_inlet():
    ev = Events()
    inlets: list[FakeInlet] = []

    def connect():
        inlets.append(FakeInlet(ev))
        return inlets[-1]

    sp = make(ev, connect=connect)
    sp.say("full")
    wait_for(lambda: ("chat", "full") in ev.items and ev.journal)
    sp.close()
    assert len(inlets[0].buf) == len(pcm_for("full")) > FIRST_CHUNK


def test_no_voice_means_a_silent_chat_line_not_a_lost_one():
    ev = Events()
    sp = make(ev, synth=lambda text: (None, "tts timeout (12 s)"))
    sp.say("quiet")
    wait_for(lambda: ("chat", "quiet") in ev.items and ev.journal)
    sp.close()
    assert ev.journal[0]["ok"] is False and "timeout" in ev.journal[0]["silent"]


def test_an_unreachable_inlet_still_sends_the_line():
    ev = Events()

    def refuse():
        raise ConnectionRefusedError("no inlet")

    sp = make(ev, connect=refuse)
    sp.say("no-body-audio")
    wait_for(lambda: ("chat", "no-body-audio") in ev.items and ev.journal)
    sp.close()
    assert "inlet unreachable" in ev.journal[0]["silent"]


def test_a_line_is_cut_into_sentences_short_ones_ride_along():
    text = "Да. Я вижу дом у реки, он почти достроен. Крыша ещё не готова, но стены стоят!"
    parts = split_parts(text)
    assert parts == ["Да. Я вижу дом у реки, он почти достроен.", "Крыша ещё не готова, но стены стоят!"]
    assert " ".join(parts) == text
    long = "раз, " * 60 + "всё."
    assert all(len(p) <= 180 for p in split_parts(long)) and len(split_parts(long)) > 1


def test_each_sentence_is_its_own_clip_and_the_chat_line_comes_once_whole():
    ev = Events()
    synthesized: list[str] = []

    def synth(text):
        synthesized.append(text)
        return pcm_for(text), ""

    sp = make(ev, synth=synth)
    line = "Первое предложение здесь. Второе предложение тоже здесь."
    sp.say(line)
    wait_for(lambda: len(ev.journal) == 2)
    sp.close()
    assert synthesized == ["Первое предложение здесь.", "Второе предложение тоже здесь."]
    assert [t for k, t in ev.items if k == "chat"] == [line]
    assert [j["ok"] for j in ev.journal] == [True, True]
    assert sum(kind == "audio" for kind, _ in ev.items) == 1
    assert ev.journal[1]["inlet_gap_s"] == 0
    assert "wait_s" in ev.journal[0] and "wait_s" not in ev.journal[1]


def test_queue_overflow_drops_only_oldest_waiting_line():
    ev = Events()
    gate = threading.Event()

    def synth(text):
        if text == "first":
            gate.wait(5)   # the first line is being synthesized while two more arrive
        return pcm_for(text), ""

    sp = make(ev, synth=synth)
    assert sp.say("first") == 0
    time.sleep(0.05)                # "first" is in synthesis
    for text in ("second", "third", "fourth"):
        assert sp.say(text) == 0
    assert sp.say("newest") == 1
    gate.set()
    wait_for(lambda: len(ev.journal) == 5)
    sp.close()
    chats = [t for k, t in ev.items if k == "chat"]
    assert chats == ["first", "second", "third", "fourth", "newest"]
    spoken = [t for k, t in ev.items if k == "audio"]
    assert spoken == ["second  ", "third   ", "fourth  ", "newest  "]
    assert ev.journal[0]["silent"] == "voice queue full"


def test_interrupt_drops_the_waiting_lines_and_lets_the_playing_one_finish():
    ev = Events()
    sp = make(ev, play_s=0.3)
    sp.say("playing")
    wait_for(lambda: ("chat", "playing") in ev.items)
    sp.say("waiting")
    time.sleep(0.05)
    assert sp.interrupt("Steve spoke") == 1
    wait_for(lambda: len(ev.journal) == 2)
    sp.close()
    assert [t for k, t in ev.items if k == "audio"] == ["playing "]
    assert ("chat", "waiting") in ev.items
    assert {j["text"]: j["ok"] for j in ev.journal} == {"playing": True, "waiting": False}


def test_two_consecutive_body_calls_speak_even_if_first_is_still_synthesizing():
    ev = Events()
    gate = threading.Event()
    sp = make(ev, synth=lambda t: (gate.wait(5), (pcm_for(t), ""))[1])
    impl = BodyMcp(SimpleNamespace(bodiless=False), speaker=sp)
    assert impl.say("привет") == {"said": True, "text": "привет", "voice": "speaking"}
    time.sleep(0.05)
    assert impl.say("и ещё") == {"said": True, "text": "и ещё", "voice": "speaking"}
    gate.set()
    wait_for(lambda: len(ev.journal) == 2)
    sp.close()
    assert [(j["text"], j["ok"]) for j in ev.journal] == [("привет", True), ("и ещё", True)]


def test_the_executor_tells_listeners_only_about_other_players():
    from runtime.mind.executor import Executor

    heard: list[dict] = []
    x = SimpleNamespace(_username="LastOrder01", _heard=[heard.append])
    Executor._notify_heard(x, {"from": "LastOrder01", "text": "моя"})
    Executor._notify_heard(x, {"text": "LastOrder01 was slain"})
    Executor._notify_heard(x, {"from": "Steve", "text": "эй"})
    assert heard == [{"from": "Steve", "text": "эй"}]
