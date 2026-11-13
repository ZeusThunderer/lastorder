"""ADR-055: one sister's voice — lines in order, never over each other, each line in chat
as its voice starts, never lost when the voice fails."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from runtime.mind.mcp_body import BodyMcp, ToolError
from runtime.voice.speaker import BYTES_PER_S, FIRST_CHUNK, Speaker, SpeakerBusy


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


def make(ev: Events, *, synth=None, connect=None, max_pending=3, play_s=0.05) -> Speaker:
    return Speaker(
        synth=synth or (lambda text: (pcm_for(text), "")),
        connect=connect or (lambda: FakeInlet(ev, play_s)),
        send_chat=lambda text: ev.add("chat", text),
        journal=ev,
        max_pending=max_pending,
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
    for text in ("line-1", "line-2", "line-3"):
        sp.say(text)
    wait_for(lambda: sum(1 for k, _ in ev.items if k == "chat") == 3)
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


def test_too_many_waiting_lines_are_refused():
    ev = Events()
    gate = threading.Event()

    def slow_synth(text):
        gate.wait(5)
        return pcm_for(text), ""

    sp = make(ev, synth=slow_synth, max_pending=2)
    assert sp.say("a") == 0
    assert sp.say("b") == 1
    with pytest.raises(SpeakerBusy):
        sp.say("c")
    gate.set()
    wait_for(lambda: sum(1 for k, _ in ev.items if k == "chat") == 2)
    assert sp.say("d") == 0  # room again once the lines are said
    sp.close()


def test_body_mcp_say_queues_through_the_speaker():
    ev = Events()
    gate = threading.Event()
    sp = make(ev, synth=lambda t: (gate.wait(5), (pcm_for(t), ""))[1], max_pending=1)
    impl = BodyMcp(SimpleNamespace(bodiless=False), speaker=sp)
    answer = impl.say("привет")
    assert answer == {"said": True, "text": "привет", "voice": "queued", "ahead": 0}
    with pytest.raises(ToolError, match="still speaking"):
        impl.say("ещё")
    gate.set()
    wait_for(lambda: ("chat", "привет") in ev.items)
    sp.close()
