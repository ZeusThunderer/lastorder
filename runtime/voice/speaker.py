"""One sister's voice (ADR-055): her `say` lines, spoken in order, each line in chat at the
moment its voice starts.

    say(text) ─► lines ─► [synth thread] tts /speak ─► ready (1 slot) ─► [play thread]
                                                                          │ connect inlet
                                                                          │ first chunk
                                                                          │ chat ◄── here
                                                                          │ rest, half-close
                                                                          └ wait EOF = done

- One `Speaker` per mind, so a sister never talks over herself; two sisters (two minds) may.
- The next line is synthesized while the current one plays: no gap for the synthesis.
- The body's inlet (`socat` → `paplay`) closes the connection when the audio has drained:
  that EOF is "finished", the next line starts only after it.
- A line is never lost: no voice (tts down, too slow, nothing speakable) or no inlet →
  the line goes to chat silently and the journal says why.
- More than `max_pending` lines waiting to start → `SpeakerBusy`; the agent hears
  "still speaking" instead of a growing backlog of stale lines.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from queue import Queue
from typing import Any, Protocol

log = logging.getLogger("voice.speaker")

RATE = 48000                 # PCM s16le mono, what the tts service returns and paplay plays
BYTES_PER_S = RATE * 2
FIRST_CHUNK = BYTES_PER_S // 10   # 100 ms of audio go out before the chat line
DRAIN_MARGIN_S = 5.0          # how long past the clip's end the inlet may take to close
MAX_PENDING = 3


class SpeakerBusy(Exception):
    """Too many lines are waiting to be spoken."""


class Journal(Protocol):
    def record(self, kind: str, **fields: Any) -> None: ...


@dataclass
class _Line:
    text: str
    pcm: bytes | None = None
    why_silent: str = ""     # set when pcm is None: the reason it goes to chat without voice
    synth_s: float = 0.0


def http_synth(url: str, timeout_s: float) -> Callable[[str], tuple[bytes | None, str]]:
    """The tts service client: (pcm, "") or (None, reason)."""
    import httpx

    client = httpx.Client(base_url=url.rstrip("/"), timeout=timeout_s)

    def synth(text: str) -> tuple[bytes | None, str]:
        try:
            resp = client.post("/speak", json={"text": text})
        except httpx.TimeoutException:
            return None, f"tts timeout ({timeout_s:.0f} s)"
        except httpx.HTTPError as exc:
            return None, f"tts unreachable: {exc.__class__.__name__}"
        if resp.status_code == 204:
            return None, "nothing to voice"
        if resp.status_code != 200 or not resp.content:
            return None, f"tts {resp.status_code}: {resp.text[:120]}"
        return resp.content, ""

    return synth


def tcp_inlet(host: str, port: int, connect_timeout_s: float = 3.0) -> Callable[[], socket.socket]:
    """The body's voice inlet on the link network (ADR-055)."""

    def connect() -> socket.socket:
        return socket.create_connection((host, port), timeout=connect_timeout_s)

    return connect


class Speaker:
    def __init__(
        self,
        *,
        synth: Callable[[str], tuple[bytes | None, str]],
        connect: Callable[[], Any],
        send_chat: Callable[[str], None],
        journal: Journal,
        max_pending: int = MAX_PENDING,
        drain_margin_s: float = DRAIN_MARGIN_S,
    ):
        self._synth = synth
        self._connect = connect
        self._send_chat = send_chat
        self._journal = journal
        self._max_pending = max_pending
        self._drain_margin_s = drain_margin_s
        self._cv = threading.Condition()
        self._lines: deque[_Line] = deque()
        self._waiting = 0             # accepted, not yet in chat
        self._ready: Queue[_Line | None] = Queue(maxsize=1)
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._synth_loop, name="voice-synth", daemon=True),
            threading.Thread(target=self._play_loop, name="voice-play", daemon=True),
        ]

    def start(self) -> "Speaker":
        for t in self._threads:
            t.start()
        return self

    def close(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        try:
            self._ready.put_nowait(None)
        except Exception:
            pass

    def say(self, text: str) -> int:
        """Queue one line; returns how many lines are ahead of it (0: it is next)."""
        with self._cv:
            if self._waiting >= self._max_pending:
                raise SpeakerBusy(
                    f"say: still speaking, {self._waiting} lines waiting; try again after they are said"
                )
            ahead = self._waiting
            self._waiting += 1
            self._lines.append(_Line(text))
            self._cv.notify()
        return ahead

    # --- threads ---------------------------------------------------------------------

    def _synth_loop(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while not self._lines and not self._stop.is_set():
                    self._cv.wait()
                if self._stop.is_set():
                    return
                line = self._lines.popleft()
            t0 = time.monotonic()
            try:
                line.pcm, line.why_silent = self._synth(line.text)
            except Exception as exc:  # the client is ours, but a line must never be lost
                line.pcm, line.why_silent = None, f"tts error: {exc}"
            line.synth_s = time.monotonic() - t0
            self._ready.put(line)  # blocks while the slot is full: one line ahead at most

    def _play_loop(self) -> None:
        while not self._stop.is_set():
            line = self._ready.get()
            if line is None:
                return
            try:
                self._speak(line)
            except Exception:  # pragma: no cover - _speak handles its own failures
                log.exception("voice: unexpected failure on %r", line.text[:60])

    def _chat(self, line: _Line) -> bool:
        with self._cv:
            self._waiting -= 1
        try:
            self._send_chat(line.text)
            return True
        except Exception as exc:
            self._journal.record("voice", ok=False, text=line.text, error=f"chat failed: {exc}")
            return False

    def _speak(self, line: _Line) -> None:
        if line.pcm is None:
            if self._chat(line):
                self._journal.record("voice", ok=False, text=line.text, silent=line.why_silent,
                                     synth_s=round(line.synth_s, 2))
            return
        duration_s = len(line.pcm) / BYTES_PER_S
        try:
            sock = self._connect()
        except OSError as exc:
            if self._chat(line):
                self._journal.record("voice", ok=False, text=line.text,
                                     silent=f"inlet unreachable: {exc.__class__.__name__}")
            return
        chatted = False
        t0 = time.monotonic()
        try:
            sock.settimeout(duration_s + self._drain_margin_s)
            sock.sendall(line.pcm[:FIRST_CHUNK])
            chatted = self._chat(line)  # the line appears as the voice starts
            sock.sendall(line.pcm[FIRST_CHUNK:])
            sock.shutdown(socket.SHUT_WR)
            while sock.recv(4096):      # paplay drains, exits, the inlet closes: EOF
                pass
            self._journal.record("voice", ok=True, text=line.text, duration_s=round(duration_s, 2),
                                 synth_s=round(line.synth_s, 2),
                                 played_s=round(time.monotonic() - t0, 2))
        except OSError as exc:
            if not chatted:
                chatted = self._chat(line)
            self._journal.record("voice", ok=False, text=line.text,
                                 error=f"inlet: {exc.__class__.__name__}: {exc}",
                                 duration_s=round(duration_s, 2))
        finally:
            try:
                sock.close()
            except OSError:
                pass
