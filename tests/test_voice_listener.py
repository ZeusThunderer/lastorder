"""ADR-055: other players' voice → phrases → text, as an observation."""

from __future__ import annotations

import time

from runtime.voice.listener import BYTES_PER_S, VoiceListener

PACKET = b"\x10\x00" * 960  # 20 ms of PCM s16le 48 kHz


def test_a_pause_ends_the_phrase_and_the_text_is_emitted():
    heard: list[tuple[str, str]] = []
    sizes: list[int] = []

    def transcribe(pcm: bytes) -> str:
        sizes.append(len(pcm))
        return "привет"

    lst = VoiceListener(transcribe=transcribe, emit=lambda s, t: heard.append((s, t)), ignore=("LastOrder02",))
    for _ in range(50):  # 1 s of speech
        lst.feed("Player", PACKET)
        lst.feed("LastOrder02", PACKET)  # its own voice is never transcribed
    end = time.monotonic() + 5
    while not heard and time.monotonic() < end:
        time.sleep(0.05)
    assert heard == [("Player", "привет")]
    assert sizes == [50 * len(PACKET)]


def test_a_click_is_not_a_phrase():
    calls: list[bytes] = []
    lst = VoiceListener(transcribe=lambda p: calls.append(p) or "x", emit=lambda s, t: None)
    lst.feed("Player", PACKET)  # 20 ms
    time.sleep(1.3)
    assert calls == [] and len(PACKET) < 0.5 * BYTES_PER_S
