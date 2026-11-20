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
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Full, Queue

import webrtcvad

log = logging.getLogger("voice.listener")

BYTES_PER_S = 48000 * 2
PAUSE_S = 0.45
FRAME_BYTES = 1920   # 20 ms, 48 kHz mono, signed 16-bit
MIN_SPEECH_FRAMES = 8
MAX_PHRASE_S = 20.0  # a monologue is cut here and goes on as the next phrase


def _music_caption(pcm_bytes: int, text: str) -> bool:
    # Whisper can label an entire open-mic segment with a single ambient caption.
    # A short spoken «музыка» and questions about music still reach the sister.
    return (pcm_bytes >= MAX_PHRASE_S * BYTES_PER_S
            and text.strip(" .!?…[]()♪").casefold() in {"музыка", "music"})


@dataclass
class _Capture:
    vad: webrtcvad.Vad = field(default_factory=lambda: webrtcvad.Vad(2))
    prefix: deque = field(default_factory=lambda: deque(maxlen=8))
    pending: bytearray = field(default_factory=bytearray)
    phrase: bytearray = field(default_factory=bytearray)
    speech_frames: int = 0
    silent_frames: int = 0
    last_speech: float = 0
    last_packet: float = 0


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
        self._open: dict[str, _Capture] = {}
        self._phrases: Queue[tuple[str, bytes, float]] = Queue(maxsize=8)
        threading.Thread(target=self._cut_loop, name="voice-cut", daemon=True).start()
        threading.Thread(target=self._stt_loop, name="voice-stt", daemon=True).start()

    def feed(self, speaker: str, audio: bytes | None) -> None:
        if not speaker or not audio or speaker.lower() in self._ignore:
            return
        now = self._clock()
        with self._lock:
            capture = self._open.get(speaker)
            if capture is None:
                capture = self._open[speaker] = _Capture()
                log.info("voice packets: %s", speaker)
            capture.last_packet = now
            capture.pending.extend(audio)
            while len(capture.pending) >= FRAME_BYTES:
                frame = bytes(capture.pending[:FRAME_BYTES])
                del capture.pending[:FRAME_BYTES]
                if capture.vad.is_speech(frame, 48000):
                    if not capture.phrase:
                        capture.phrase.extend(b"".join(capture.prefix))
                        capture.prefix.clear()
                        log.debug("voice activity: %s", speaker)
                    capture.last_speech = now
                    capture.speech_frames += 1
                    capture.silent_frames = 0
                elif not capture.phrase:
                    capture.prefix.append(frame)
                    continue
                else:
                    capture.silent_frames += 1
                capture.phrase.extend(frame)
                if (capture.silent_frames * 0.02 >= PAUSE_S
                        or len(capture.phrase) >= MAX_PHRASE_S * BYTES_PER_S):
                    self._close(speaker, capture)

    def _close(self, speaker: str, capture: _Capture) -> None:
        buf, capture.phrase = capture.phrase, bytearray()
        frames, capture.speech_frames = capture.speech_frames, 0
        capture.silent_frames = 0
        if frames < MIN_SPEECH_FRAMES:
            return
        try:
            self._phrases.put_nowait((speaker, bytes(buf), capture.last_speech))
        except Full:
            log.warning("voice: STT behind, phrase of %s dropped", speaker)

    def _cut_loop(self) -> None:
        while True:
            time.sleep(0.025)
            now = self._clock()
            with self._lock:
                for speaker, capture in list(self._open.items()):
                    if capture.phrase and now - capture.last_speech >= PAUSE_S:
                        self._close(speaker, capture)
                    if now - capture.last_packet >= PAUSE_S:
                        del self._open[speaker]

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
            if _music_caption(len(pcm), text):
                log.info("voice ambient music caption: %s", speaker)
            elif text:
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
