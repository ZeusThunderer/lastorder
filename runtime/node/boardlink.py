"""The temporary board bridge of one life (T-050): runner-side board delivery.

Until `misaka-link` (T-048) ships, the runner of a slot polls the hive board feed
with the node key and pushes what it finds into the mind as ONE `board` event —
the C1 event queue plays the role of the Pi session. Everything here is transport
and envelope (TASKBOARD.md section 11): no board semantics, no behaviour — the
agent reads the board itself through her `hive` MCP tools, and the rules live in
her instructions (section 13, ADR-032).

The loop, one cycle:
- `GET /board/feed?since=` (node key). A new life starts with no cursor: the hive
  answers once with the snapshot of open `net` and addressed tasks. Delivery is
  at-least-once — the hive repeats an un-acked snapshot, so the bridge remembers
  the last 200 `#id:seq` it handed to the mind and skips the repeats.
- The events fold into section-11 envelope lines (the prefix is built from the
  structured fields; the author's text is whitespace-collapsed onto the same line,
  so it cannot forge another envelope), capped at 10 lines, the rest as the
  `… N more, `board`` marker.
- `POST /runner/event` (X-Runner-Key) puts the batch into the mind's queue. Only
  after a 200 does `POST /board/ack` move the cursor: a mind that answered with
  an error gets the same batch again on the next cycle.
- The hive down does not end the life: the pause doubles up to 60 s; after 5
  failures in a row the bridge raises `delivery_failing` once the hive answers.

The bridge runs only while the life session runs — never in the will session and
never between lives (the runner owns the start/stop around `_life`).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable

from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.lastorder.naming import NamingPreset, node_name
from runtime.node.remotes import BaseApi

POLL_S = 10.0  # the healthy feed pace; the section-11 ceiling is 15 s
RETRY_PAUSE_S = 2.0  # the hive is down: 2, 4, 8 ... capped
RETRY_MAX_S = 60.0
ALARM_AFTER = 5  # consecutive feed failures before delivery_failing
SEEN_MAX = 200  # the #id:seq pairs one life remembers (at-least-once repeats)
LINES_MAX = 10  # one batch; the rest folds into the "more" marker


def cap_lines(lines: list[str], limit: int = LINES_MAX) -> list[str]:
    """At most `limit` envelope lines; the rest becomes the marker line
    (TASKBOARD.md section 11). The mind's EventQueue keeps its own copy of this
    shape — the format lives in the spec, not in a shared import."""
    if len(lines) <= limit:
        return list(lines)
    return [*lines[:limit], f"… {len(lines) - limit} more, `board`"]


def _who(name: Any, naming: NamingPreset) -> str:
    """A board actor in the envelope: a serial becomes the preset name (ADR-018)."""
    text = str(name if name is not None else "")
    if text.isdigit():
        return node_name(naming, int(text))
    return text or "?"


def _text(value: Any) -> str:
    """The author's text on one line: every whitespace run collapses, so a text
    can never break into a second line and pose as another envelope."""
    return " ".join(str(value or "").split())


def envelope(event: dict[str, Any], naming: NamingPreset) -> str | None:
    """One section-11 line for one feed event, or None for an op the bridge does
    not speak. The prefix comes from the structured fields alone — the source is
    the environment's, the author's text only ever trails it."""
    op = str(event.get("op") or "")
    entry = event.get("entry") if isinstance(event.get("entry"), dict) else {}
    entry_id = event.get("entry_id")
    kind = str(entry.get("kind") or "entry")
    after = event.get("after") if isinstance(event.get("after"), dict) else {}
    actor = _who(event.get("actor"), naming)
    if op in ("post", "snapshot"):
        head = (
            f"[board #{entry_id} {kind} · from {_who(entry.get('from'), naming)}"
            f" · to {_who(entry.get('to'), naming)} · {entry.get('status')}]"
        )
        body = _text(entry.get("text"))
        return f"{head} {body}" if body else head
    if op == "claim":
        return f"[board #{entry_id} · taken by {_who(after.get('claimed_by'), naming) or actor}]"
    if op == "reply":
        head = f"[board #{entry_id} {kind} · reply by {actor}]"
        body = _text(after.get("text"))
        return f"{head} {body}" if body else head
    if op == "transition":
        head = f"[board #{entry_id} {kind} · {after.get('status') or entry.get('status')} by {actor}]"
        body = _text(after.get("text"))
        return f"{head} {body}" if body else head
    if op == "readdress":
        return f"[board #{entry_id} · to {_who(after.get('to') or entry.get('to'), naming)}]"
    return None


def envelope_lines(
    events: list[dict[str, Any]], naming: NamingPreset, limit: int = LINES_MAX
) -> list[str]:
    lines = [line for line in (envelope(e, naming) for e in events) if line]
    return cap_lines(lines, limit)


class MindEventPoster(BaseApi):
    """mind-N's `POST /runner/event`: the bridge's batch as one `board` event."""

    def event(self, type_: str, lines: list[str]) -> dict[str, Any]:
        return self._request("post", "/runner/event", {"type": type_, "lines": lines}).json()


class BoardLink:
    """One life's bridge: feed → envelope → mind → ack, with the section-11
    at-least-once rules. `start()`/`stop()` bracket the life session; the thread
    is a daemon, so a stuck HTTP call never holds the death path hostage."""

    def __init__(
        self,
        feed: Any,
        mind: Any,
        *,
        serial: int,
        slot: str,
        naming: NamingPreset,
        log: Callable[[str, dict[str, Any] | None], None] | None = None,
    ):
        self._feed = feed
        self._mind = mind
        self._serial = serial
        self._slot = slot
        self._naming = naming
        self._log = log or (lambda type, payload=None: None)
        self._cursor: int | None = None  # None: a new life, the hive snapshots once
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._failures = 0
        self._alarm_pending = 0  # the failure count waiting to become an alarm
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"boardlink-{self._slot}", daemon=True
        )
        self._thread.start()
        self._log("boardlink.start", {"serial": self._serial, "slot": self._slot})

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                self._log("boardlink.stop_late", {"serial": self._serial})

    def _run(self) -> None:
        while not self._stop.is_set():
            pause = self.poll_once()
            if self._stop.wait(pause):
                break

    # --- one cycle -----------------------------------------------------------------------

    @staticmethod
    def _key(event: dict[str, Any]) -> str:
        return f"{event.get('entry_id')}:{event.get('seq')}"

    def _remember(self, key: str) -> None:
        self._seen.pop(key, None)
        self._seen[key] = None
        while len(self._seen) > SEEN_MAX:
            self._seen.popitem(last=False)

    def _hive_up_again(self) -> None:
        failed, self._failures = self._failures, 0
        if failed:
            self._log("boardlink.recovered", {"serial": self._serial, "after": failed})
        if not self._alarm_pending:
            return
        try:
            self._feed.alarm_delivery_failing(
                f"board delivery to slot {self._slot} failed "
                f"{self._alarm_pending} times in a row",
                slot=self._slot,
                serial=self._serial,
            )
            self._alarm_pending = 0
            self._log("boardlink.alarm", {"serial": self._serial})
        except (HiveUnavailable, HiveRejected) as exc:
            self._log("boardlink.alarm_failed", {"serial": self._serial, "error": str(exc)})

    def poll_once(self) -> float:
        """One feed → mind → ack cycle; returns the seconds to the next one."""
        try:
            response = self._feed.feed(self._cursor)
        except HiveUnavailable as exc:
            self._failures += 1
            if self._failures >= ALARM_AFTER:
                self._alarm_pending = self._failures
            self._log(
                "boardlink.feed_failed",
                {"serial": self._serial, "attempt": self._failures, "error": str(exc)},
            )
            return min(RETRY_PAUSE_S * (2 ** (self._failures - 1)), RETRY_MAX_S)
        except HiveRejected as exc:
            # a permanent refusal (an unknown key, no live node): a configuration
            # matter for the operator, not a delivery outage — keep the life going
            self._log("boardlink.feed_refused", {"serial": self._serial, "error": str(exc)})
            return POLL_S
        self._hive_up_again()
        events = response.get("events") if isinstance(response, dict) else None
        if not events:
            return POLL_S
        fresh = [e for e in events if self._key(e) not in self._seen]
        top = max(int(e.get("seq") or 0) for e in events)
        if fresh:
            lines = envelope_lines(fresh, self._naming)
            try:
                self._mind.event("board", lines)
            except (HiveUnavailable, HiveRejected) as exc:
                # no ack, no memory: the next cycle delivers the same batch
                self._log(
                    "boardlink.mind_failed",
                    {"serial": self._serial, "lines": len(lines), "error": str(exc)},
                )
                return POLL_S
            for event in fresh:
                self._remember(self._key(event))
            self._log(
                "boardlink.delivered", {"serial": self._serial, "lines": len(lines), "seq": top}
            )
        try:
            self._feed.ack(top)  # only after the mind took the batch
        except (HiveUnavailable, HiveRejected) as exc:
            self._log("boardlink.ack_failed", {"serial": self._serial, "error": str(exc)})
            return POLL_S
        self._cursor = top
        return POLL_S
