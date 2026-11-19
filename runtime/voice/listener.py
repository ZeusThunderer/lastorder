"""What a sister hears (ADR-055): other players' voices from SVC, cut into phrases and
turned into text — observation like chat (ADR-012), never a command.

uc's onVoiceFeed hands 20 ms packets of PCM s16le 48 kHz mono per speaker; SVC sends
only while someone speaks, so a pause in packets ends a phrase. Each phrase goes to the
voice service's /transcribe; the text comes back to the body as a `voice.line` event,
`<speaker> text`, and the executor treats it like a chat line marked `voice`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from queue import Full, Queue

log = logging.getLogger("voice.listener")

BYTES_PER_S = 48000 * 2
PAUSE_S = 0.8        # no packets this long: the phrase is over
MIN_PHRASE_S = 0.5   # shorter is a cough or a click
MAX_PHRASE_S = 20.0  # a monologue is cut here and goes on as the next phrase


class VoiceListener:
    def __init__(
        self,
        *,
        transcribe: Callable[[bytes], str],
        emit: Callable[[str, str], None],
        ignore: tuple[str, ...] = (),
        clock: Callable[[], float] = time.monotonic,
    ):
        self._transcribe = transcribe
        self._emit = emit
        self._ignore = {n.lower() for n in ignore if n}
        self._clock = clock
        self._lock = threading.Lock()
        self._open: dict[str, tuple[bytearray, float]] = {}
        self._phrases: Queue[tuple[str, bytes, float]] = Queue(maxsize=8)
        threading.Thread(target=self._cut_loop, name="voice-cut", daemon=True).start()
        threading.Thread(target=self._stt_loop, name="voice-stt", daemon=True).start()

    def feed(self, speaker: str, audio: bytes | None) -> None:
        if not speaker or not audio or speaker.lower() in self._ignore:
            return
        now = self._clock()
        with self._lock:
            buf, _ = self._open.get(speaker, (bytearray(), now))
            buf += bytes(audio)
            self._open[speaker] = (buf, now)
            if len(buf) >= MAX_PHRASE_S * BYTES_PER_S:
                self._close(speaker)

    def _close(self, speaker: str) -> None:  # under self._lock
        buf, last_packet = self._open.pop(speaker)
        if len(buf) < MIN_PHRASE_S * BYTES_PER_S:
            return
        try:
            self._phrases.put_nowait((speaker, bytes(buf), last_packet))
        except Full:
            log.warning("voice: STT behind, phrase of %s dropped", speaker)

    def _cut_loop(self) -> None:
        while True:
            time.sleep(0.2)
            now = self._clock()
            with self._lock:
                for speaker in [s for s, (_, t) in self._open.items() if now - t >= PAUSE_S]:
                    self._close(speaker)

    def _stt_loop(self) -> None:
        while True:
            speaker, pcm, last_packet = self._phrases.get()
            started = self._clock()
            try:
                text = self._transcribe(pcm).strip()
            except Exception as exc:
                log.warning("voice: stt failed for %s: %s", speaker, exc)
                continue
            log.info("voice heard: %s (%.1f s, end_to_stt=%.3f s, stt=%.3f s): %r",
                     speaker, len(pcm) / BYTES_PER_S, started - last_packet,
                     self._clock() - started, text[:80])
            if text:
                self._emit(speaker, text)


def http_transcribe(url: str, timeout_s: float) -> Callable[[bytes], str]:
    import httpx

    client = httpx.Client(base_url=url.rstrip("/"), timeout=timeout_s)

    def transcribe(pcm: bytes) -> str:
        resp = client.post("/transcribe", content=pcm, headers={"content-type": "application/octet-stream"})
        resp.raise_for_status()
        return str(resp.json().get("text", ""))

    return transcribe


def listener_from_env(emit: Callable[[str, str], None]) -> VoiceListener | None:
    """VOICE_ENABLED=1 (the slot's BODY_VOICE_N) — the same switch as the voice out."""
    if os.environ.get("VOICE_ENABLED", "") != "1":
        return None
    url = os.environ.get("TTS_URL", "http://tts:8790")
    ignore = tuple(n.strip() for n in os.environ.get("VOICE_IGNORE", "").split(",")) + (
        os.environ.get("MC_USERNAME", ""),
    )
    log.info("voice listener armed: stt %s", url)
    return VoiceListener(transcribe=http_transcribe(url, 30.0), emit=emit, ignore=ignore)
