"""ADR-055: other players' voice → phrases → text, as an observation."""

from __future__ import annotations

import time
from array import array
from math import sin, tau

from runtime.voice.listener import BYTES_PER_S, VoiceListener, _music_caption

PACKET = array("h", (int(4500 * sin(tau * 150 * i / 48000)
                         + 3000 * sin(tau * 700 * i / 48000)) for i in range(960))).tobytes()


def test_only_full_length_music_captions_are_suppressed():
    assert _music_caption(20 * BYTES_PER_S, "[Музыка]")
    assert not _music_caption(BYTES_PER_S, "Музыка.")
    assert not _music_caption(20 * BYTES_PER_S, "Слышишь, играет музыка?")


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


def test_open_mic_splits_at_silence_and_keeps_short_internal_pause():
    heard = []
    import threading
    done = threading.Event()
    def emit(speaker, text):
        heard.append(speaker)
        if len(heard) == 2:
            done.set()
    listener = VoiceListener(transcribe=lambda _: "тест", emit=emit)
    silence = bytes(len(PACKET))
    for frame, count in [(silence, 60), (PACKET, 20), (silence, 10), (PACKET, 25),
                         (silence, 50), (PACKET, 25), (silence, 50)]:
        for _ in range(count):
            listener.feed("Player", frame)
            listener.feed("Other", silence)
    assert done.wait(2)
    assert heard == ["Player", "Player"]
