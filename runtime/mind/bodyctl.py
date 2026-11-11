"""The mind's own body (ADR-050): the mind starts it, watches it and asks for a new one.

The slot is the sister (node-N) and her mind (mind-N); the body is the mind's
business. The mind holds no Docker access — it asks the host executor through the
board (`POST /board/infra/slot` with the slot's runner key), and the board keeps it
to its own slot and caps the restarts. Every ask is a board entry the operator and
Last Order see.

Which body: BODY_KIND picks the adapter here and the compose service on the
executor's side (runtime/hive/infra.py BODY_KINDS). One game today; another game is
one more adapter and one more body service.

The watch reads `last_ok` — when the last call into the body came back — which C1's
ticks (2 Hz) keep fresh while the body lives:
- never answered since the mind started, past the start grace -> `body_up`;
- answered, then silent for `dead_s` -> `body_restart` (the T-053 recreate);
- after any ask, `boot_s` of patience before the next (a cold body boots for minutes).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from runtime.game.py4j_body import Py4jBody
from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.hive.infra import DEFAULT_BODY_KIND

# kind -> the GameBody adapter of that game (runtime/ports.py GameBody)
BODY_ADAPTERS: dict[str, Callable[[], Any]] = {"minecraft": Py4jBody}

START_GRACE_S = 20.0   # a mind restarted beside a living body needs no ask
DEAD_S = 90.0          # a body silent this long after answering is gone
BOOT_S = 420.0         # the executor's body health budget (dev/infra_bridge.py GRACE_S)
POLL_S = 5.0
UNAVAILABLE_RETRY_S = 30.0  # the hive did not answer: try again soon, not after boot_s


def make_body(kind: str = DEFAULT_BODY_KIND) -> Any:
    """The adapter of `kind`; an unknown kind is a startup error, not a silent default."""
    if kind not in BODY_ADAPTERS:
        raise ValueError(f"BODY_KIND must be one of {sorted(BODY_ADAPTERS)}, got {kind!r}")
    return BODY_ADAPTERS[kind]()


def slot_number(username: str) -> int:
    """LastOrder02 -> 2: the infra slot numbering of the slot account."""
    digits = "".join(ch for ch in str(username)[-2:] if ch.isdigit())
    if not digits:
        raise ValueError(f"cannot read a slot number from {username!r}")
    return int(digits)


class BodySupervisor:
    """`tick()` is the whole policy (tests drive it with a fake clock); `start()`
    runs it on a daemon thread every POLL_S."""

    def __init__(
        self,
        body: Any,
        hive: Any,
        *,
        username: str,
        kind: str = DEFAULT_BODY_KIND,
        log: Any = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        start_grace_s: float = START_GRACE_S,
        dead_s: float = DEAD_S,
        boot_s: float = BOOT_S,
        poll_s: float = POLL_S,
    ):
        self._body = body
        self._hive = hive
        self._username = username
        self._slot = slot_number(username)
        self._kind = kind
        self._log = log
        self._clock = clock
        self._sleep = sleep
        self._grace_s = start_grace_s
        self._dead_s = dead_s
        self._boot_s = boot_s
        self._poll_s = poll_s
        self._started = clock()
        self._quiet_until = 0.0  # no ask before this (an ask in flight, or the hive away)
        self._alarmed_at: float | None = None
        self._stop = threading.Event()

    def tick(self) -> str | None:
        """One look at the body; returns the action asked, if any."""
        now = self._clock()
        if now < self._quiet_until:
            return None
        last = getattr(self._body, "last_ok", None)
        if last is None:
            if now - self._started < self._grace_s:
                return None
            return self._ask("body_up", "the mind is up and its body does not answer", now)
        silent = now - last
        if silent >= self._dead_s:
            return self._ask("body_restart", f"the body has not answered for {int(silent)} s", now)
        return None

    def _ask(self, action: str, why: str, now: float) -> str | None:
        args = {"slot": str(self._slot)}
        if action == "body_up":
            args["kind"] = self._kind
        try:
            self._hive.infra_slot(action, args, text=f"{self._username}: {action} — {why}", why=why)
        except HiveUnavailable as exc:
            self._record("body.ask_failed", action=action, error=str(exc))
            self._quiet_until = now + UNAVAILABLE_RETRY_S
            return None
        except HiveRejected as exc:
            self._record("body.ask_refused", action=action, status=exc.status, error=exc.message)
            if exc.status == 429:
                self._alarm_stuck(why, now)
            self._quiet_until = now + self._boot_s
            return None
        self._record("body.ask", action=action, why=why)
        self._quiet_until = now + self._boot_s
        return action

    def _alarm_stuck(self, why: str, now: float) -> None:
        if self._alarmed_at is not None and now - self._alarmed_at < self._boot_s:
            return
        self._alarmed_at = now
        try:
            self._hive.board_alarm(
                "stuck", f"the body keeps dying and the restarts are used up: {why}",
                slot=self._username,
            )
        except Exception as exc:  # an alarm that cannot go out is logged, not fatal
            self._record("body.alarm_failed", error=f"{type(exc).__name__}: {exc}")

    def _record(self, event: str, **fields: Any) -> None:
        if self._log is not None:
            self._log.record("event", event=event, **fields)

    def start(self) -> None:
        threading.Thread(target=self._run, name="mind-bodyctl", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # the watch must outlive any one bad look
                self._record("body.watch_error", error=f"{type(exc).__name__}: {exc}")
            self._sleep(self._poll_s)
