"""One sister's voice (ADR-055): her `say` lines, spoken sentence by sentence, each line in
chat at the moment its voice starts.

    say(text) ─► parts (sentences) ─► [synth thread] tts /speak/stream ─► ready (1 part ahead)
                                                                     │ [play thread]
                                                                     │ connect inlet, first chunk
                                                                     │ chat ◄── first part only
                                                                     │ rest, half-close
                                                                     └ wait EOF = part done

- One `Speaker` per mind, so a sister never talks over herself; two sisters (two minds) may.
- A line is cut into sentences (defence-plan W1): the first sentence plays while the next is
  synthesized, so the first sound comes after one sentence of synthesis, not the whole line,
  and each request stays well inside the tts timeout.
- A new line REPLACES the lines still waiting to start, it does not queue behind them: by the
  time a stale line would be spoken the moment has passed (Pyhtun, showcase §3). The line
  already playing finishes. `interrupt()` drops the waiting lines the same way — a player
  spoke to her (§8).
- A line is never lost: replaced, interrupted, no voice (tts down, too slow, nothing
  speakable) or no inlet — it goes to chat as text and the journal says why.
- The body's inlet (`socat` → `paplay`) closes the connection when the audio has drained:
  that EOF is "finished", the next part starts only after it.
"""

from __future__ import annotations

import logging
import re
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger("voice.speaker")

RATE = 48000                 # PCM s16le mono, what the tts service returns and paplay plays
BYTES_PER_S = RATE * 2
FIRST_CHUNK = BYTES_PER_S // 10   # 100 ms of audio go out before the chat line
DRAIN_MARGIN_S = 5.0          # how long past the clip's end the inlet may take to close
MIN_PART = 25                 # a shorter sentence rides with the next one ("Да. Иду.")
MAX_PART = 180                # a longer sentence is cut at a comma or a dash

_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–-]\s)")


def split_parts(text: str) -> list[str]:
    """A line as the pieces it is synthesized in: sentences, the short ones joined to the
    next, the long ones cut at a clause boundary. Joined back with spaces they are the line."""
    out: list[str] = []
    for sentence in _SENTENCE.split(text.strip()):
        if len(sentence) <= MAX_PART:
            pieces = [sentence]
        else:
            pieces, cur = [], ""
            for clause in _CLAUSE.split(sentence):
                if cur and len(cur) + 1 + len(clause) > MAX_PART:
                    pieces.append(cur)
                    cur = clause
                else:
                    cur = f"{cur} {clause}".strip()
            if cur:
                pieces.append(cur)
        for piece in pieces:
            if out and len(out[-1]) < MIN_PART:
                out[-1] = f"{out[-1]} {piece}"
            else:
                out.append(piece)
    if len(out) > 1 and len(out[-1]) < MIN_PART:
        out[-2] = f"{out[-2]} {out.pop()}"
    return [p for p in out if p]


class Journal(Protocol):
    def record(self, kind: str, **fields: Any) -> None: ...


@dataclass
class _Line:
    id: int
    text: str
    said_at: float
    state: str = "waiting"    # waiting → playing → done, or waiting → dropped
    heard_at: float | None = None
    synth: Callable | None = None


@dataclass
class _Part:
    line: _Line
    text: str
    first: bool
    last: bool
    pcm: bytes | Iterator[bytes] | None = None
    why_silent: str = ""      # set when pcm is None
    synth_s: float = 0.0
    first_pcm_at: float | None = None


@dataclass
class _Prefetch:
    chunks: Iterator[bytes]

    def collect(self, cancelled, clock=time.monotonic):
        data = bytearray()
        first_at = None
        try:
            for block in self.chunks:
                if cancelled():
                    break
                if first_at is None:
                    first_at = clock()
                data.extend(block)
        finally:
            self.chunks.close()
        return bytes(data), first_at


def http_synth(url: str, timeout_s: float, *, gateway_key: str = ""):
    """Prefetch phrases until the synthesizer can reliably feed live playback."""
    import httpx

    headers = {"Authorization": f"Bearer {gateway_key}"} if gateway_key else {}
    client = httpx.Client(base_url=url.rstrip("/"), timeout=timeout_s, headers=headers)
    fast_runs = 0
    speed_lock = threading.Lock()

    def synth(text: str):
        first_at = None

        def chunks():
            nonlocal first_at, fast_runs
            received = 0
            underrun = 0.0
            with client.stream("POST", "/speak/stream", json={"text": text}) as resp:
                if resp.status_code == 204:
                    return
                resp.raise_for_status()
                for block in resp.iter_bytes(chunk_size=FIRST_CHUNK):
                    now = time.monotonic()
                    if first_at is None:
                        first_at = now
                    underrun = max(underrun, now - first_at - received / BYTES_PER_S)
                    received += len(block)
                    yield block
            if first_at is not None:
                elapsed = time.monotonic() - first_at
                fast = underrun < 0.05 and elapsed < 0.8 * received / BYTES_PER_S
                with speed_lock:
                    fast_runs = fast_runs + 1 if fast else 0
        with speed_lock:
            live = fast_runs >= 2
        if live:
            return chunks(), ""
        return _Prefetch(chunks()), ""

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
        synth: Callable[[str], tuple[bytes | _Prefetch | Iterator[bytes] | None, str]],
        connect: Callable[[], Any],
        send_chat: Callable[[str], None],
        journal: Journal,
        drain_margin_s: float = DRAIN_MARGIN_S,
        clock: Callable[[], float] = time.monotonic,
        select_synth: Callable[[], Callable] | None = None,
    ):
        self._synth = synth
        self._select_synth = select_synth
        self._connect = connect
        self._send_chat = send_chat
        self._journal = journal
        self._drain_margin_s = drain_margin_s
        self._clock = clock
        self._cv = threading.Condition()
        self._todo: deque[_Part] = deque()    # not synthesized yet
        self._ready: deque[_Part] = deque()   # synthesized, not played; one part ahead at most
        self._active: _Part | None = None
        self._inflight: _Part | None = None   # being synthesized right now
        self._ids = 0
        self._last_heard: float | None = None
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

    def say(self, text: str) -> int:
        """Speak one line; the lines still waiting to start are replaced (sent to chat as
        text). Returns how many lines it replaced."""
        parts = split_parts(text) or [text]
        with self._cv:
            dropped = self._drop_waiting()
            self._ids += 1
            line = _Line(self._ids, text, self._clock())
            line.heard_at, self._last_heard = self._last_heard, None
            for i, piece in enumerate(parts):
                self._todo.append(_Part(line, piece, first=i == 0, last=i == len(parts) - 1))
            self._cv.notify_all()
        self._flush_dropped(dropped, "replaced by a newer line")
        return len(dropped)

    def heard(self, entry: dict) -> None:
        with self._cv:
            self._last_heard = self._clock()
        self.interrupt(f"{entry.get('from')} spoke")

    def interrupt(self, why: str = "interrupted") -> int:
        """Drop the lines still waiting to start (sent to chat as text); the one playing
        finishes. Returns how many were dropped."""
        with self._cv:
            dropped = self._drop_waiting()
            self._cv.notify_all()
        self._flush_dropped(dropped, why)
        return len(dropped)

    # --- dropping ------------------------------------------------------------------------

    def _drop_waiting(self) -> list[_Line]:
        """Under the lock: every waiting line becomes dropped, its parts leave the queues."""
        lines: dict[int, _Line] = {}
        inflight = [self._inflight] if self._inflight is not None else []
        active = [self._active] if self._active is not None else []
        for part in [*active, *inflight, *self._todo, *self._ready]:
            if part.line.state == "waiting":
                lines[part.line.id] = part.line
        for line in lines.values():
            line.state = "dropped"
        for queue in (self._todo, self._ready):
            kept = [p for p in queue if p.line.state != "dropped"]
            queue.clear()
            queue.extend(kept)
        return sorted(lines.values(), key=lambda line: line.id)

    def _flush_dropped(self, lines: list[_Line], why: str) -> None:
        for line in lines:
            try:
                self._send_chat(line.text)
            except Exception as exc:
                self._journal.record("voice", ok=False, text=line.text, error=f"chat failed: {exc}")
                continue
            self._journal.record("voice", ok=False, text=line.text, silent=why)

    # --- threads ---------------------------------------------------------------------

    def _synth_loop(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while not self._stop.is_set() and (not self._todo or self._ready):
                    self._cv.wait()   # nothing to do, or one part is already waiting to play
                if self._stop.is_set():
                    return
                part = self._todo.popleft()
                self._inflight = part
            t0 = self._clock()
            try:
                if part.line.synth is None:
                    part.line.synth = self._select_synth() if self._select_synth else self._synth
                part.pcm, part.why_silent = part.line.synth(part.text)
                if isinstance(part.pcm, _Prefetch):
                    data, part.first_pcm_at = part.pcm.collect(
                        lambda: self._stop.is_set() or part.line.state == "dropped", self._clock)
                    part.pcm = data or None
                    if not data:
                        part.why_silent = "nothing to voice"
            except Exception as exc:  # the client is ours, but a line must never be lost
                part.pcm, part.why_silent = None, f"tts error: {exc}"
            part.synth_s = self._clock() - t0
            with self._cv:
                self._inflight = None
                if part.line.state != "dropped":   # replaced while it was being synthesized
                    self._ready.append(part)
                    self._cv.notify_all()

    def _play_loop(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while not self._stop.is_set() and not self._ready:
                    self._cv.wait()
                if self._stop.is_set():
                    return
                part = self._ready.popleft()
                self._active = part
                if part.first and (part.pcm is None or isinstance(part.pcm, bytes)):
                    part.line.state = "playing"   # from here on it is not replaced
                self._cv.notify_all()             # the synth may start the next part
            try:
                self._speak(part)
            except Exception:  # pragma: no cover - _speak handles its own failures
                log.exception("voice: unexpected failure on %r", part.text[:60])
            with self._cv:
                self._active = None
                if part.last and part.line.state != "dropped":
                    part.line.state = "done"

    def _chat(self, part: _Part) -> bool:
        if not part.first:
            return True
        try:
            self._send_chat(part.line.text)
            return True
        except Exception as exc:
            self._journal.record("voice", ok=False, text=part.line.text, error=f"chat failed: {exc}")
            return False

    def _record(self, part: _Part, started: float | None = None, **fields: Any) -> None:
        extra: dict[str, Any] = {"line": part.line.id}
        if part.first:
            # say → first sound (or → the silent chat line): the voice share of the reply
            # delay (defence-plan W2)
            at = started if started is not None else self._clock()
            extra["wait_s"] = round(at - part.line.said_at, 2)
        self._journal.record("voice", text=part.text, **extra, **fields)

    def _stream_part(self, part):
        sock, started, chatted, count = None, None, False, 0
        try:
            for pcm in part.pcm:
                with self._cv:
                    if part.line.state == "dropped":
                        return
                    if pcm and part.first:
                        part.line.state = "playing"
                if not pcm:
                    continue
                if sock is None:
                    ready_at = self._clock()
                    sock = self._connect()
                    sock.settimeout(35)
                sock.sendall(pcm)
                count += len(pcm)
                if started is None:
                    started = self._clock()
                    chatted = self._chat(part)
            if sock is None:
                self._chat(part)
                self._record(part, ok=False, silent="nothing to voice")
                return
            sent_at = self._clock()
            sock.shutdown(socket.SHUT_WR)
            while sock.recv(4096):
                pass
            timing = {"stream_s": round(sent_at - started, 3),
                      "inlet_drain_s": round(self._clock() - sent_at, 3)}
            if part.first:
                timing["say_to_pcm_s"] = round(ready_at - part.line.said_at, 3)
                timing["pcm_to_inlet_s"] = round(started - ready_at, 3)
                if part.line.heard_at is not None:
                    timing["last_heard_to_say_s"] = round(part.line.said_at - part.line.heard_at, 3)
            self._record(part, started, ok=True, duration_s=count / BYTES_PER_S, **timing)
        except Exception as exc:
            if not chatted and part.line.state != "dropped":
                self._chat(part)
            self._record(part, started, ok=False, error=f"stream: {type(exc).__name__}: {exc}")
        finally:
            close = getattr(part.pcm, "close", None)
            if close:
                close()
            if sock is not None:
                sock.close()

    def _speak(self, part: _Part) -> None:
        if part.pcm is None:
            if self._chat(part):
                self._record(part, ok=False, silent=part.why_silent, synth_s=round(part.synth_s, 2))
            return
        if not isinstance(part.pcm, bytes):
            self._stream_part(part)
            return
        duration_s = len(part.pcm) / BYTES_PER_S
        try:
            sock = self._connect()
        except OSError as exc:
            if self._chat(part):
                self._record(part, ok=False, silent=f"inlet unreachable: {exc.__class__.__name__}")
            return
        chatted = False
        t0 = started = self._clock()
        try:
            sock.settimeout(duration_s + self._drain_margin_s)
            sock.sendall(part.pcm[:FIRST_CHUNK])
            started = self._clock()
            chatted = self._chat(part)  # the line appears as its voice starts
            sock.sendall(part.pcm[FIRST_CHUNK:])
            sock.shutdown(socket.SHUT_WR)
            while sock.recv(4096):      # paplay drains, exits, the inlet closes: EOF
                pass
            timing = {}
            if part.first and part.first_pcm_at is not None:
                timing["say_to_pcm_s"] = round(part.first_pcm_at - part.line.said_at, 3)
                timing["pcm_to_inlet_s"] = round(started - part.first_pcm_at, 3)
                if part.line.heard_at is not None:
                    timing["last_heard_to_say_s"] = round(part.line.said_at - part.line.heard_at, 3)
            self._record(part, started, ok=True, duration_s=round(duration_s, 2),
                         synth_s=round(part.synth_s, 2), played_s=round(self._clock() - t0, 2),
                         **timing)
        except OSError as exc:
            if not chatted:
                chatted = self._chat(part)
            self._record(part, ok=False, error=f"inlet: {exc.__class__.__name__}: {exc}",
                         duration_s=round(duration_s, 2))
        finally:
            try:
                sock.close()
            except OSError:
                pass
