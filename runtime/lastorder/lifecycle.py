"""The death path of one mind: will → retire → respawn → roll → birth → inheritance
(NETWORK.md section 7, T-013). One module, one strict order, no death without a will.

`Lifecycle.on_death` is what the mind loop (T-014) calls when the DeathDetector has ruled
a death. The order is fixed and tested: pause C1/C2 (callback) → the will (one `smart`
call, the emergency template behind it, runtime/lastorder/will.py) → `retire` (or the
pending file when hive is down, NETWORK.md section 9) → `respawn()` (the mind's own move:
uc does not auto-respawn without a task) → new traits (`roll`) → `birth` of the next node
in the same slot → the heir's `inheritance` → resume (callback).

Hive-down contract (section 9): the will is written to `run/<slot>/pending/will-NNNNN.md`
exactly once, the respawn still runs, and the parked will plus the birth are retried with
a growing pause until hive answers. The will reaches hive exactly once: the retry sends
the same captured text, a 409 `already_retired` in the retry loop (the call landed but the
reply was lost) counts as delivered, and after a real success the pending file is removed.
A 409 on the FIRST retire means hive closed the node itself (lost-close, reconcile): the
node's will never landed, so it is parked as superseded and the generation goes on — a
parked file beats a silently lost will (review R1-2).

The birth never runs ahead of the will (review R1-1): a real hive closes a living node of
the slot at `birth` with its own emergency will, so a birth before the delivery would
supersede the node's will and delete its only copy. Until the will is delivered (or
finally settled — superseded, or rejected by a non-409 4xx), a round only retries the
delivery; NETWORK.md section 9 keeps the birth for after the recovery anyway.

A respawn that does not stand the body up is retried once more, then `BodyUnavailable`
goes up — by then the will is already saved (in hive or parked). `pause` is called before
everything, `resume` after the inheritance; on an exception the loop stays paused and the
error propagates: recovery belongs to the caller (T-014).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from runtime.config import CHARACTER_DIR, env
from runtime.game.py4j_body import BodyUnavailable
from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.knowledge.slice import format_serial
from runtime.knowledge.schema import ReflectionInput, Will
from runtime.lastorder.death import DeathCause
from runtime.lastorder.naming import NamingPreset, preset
from runtime.lastorder.persona import Inheritance, Node
from runtime.lastorder.traits import Traits, load_quirks, roll
from runtime.lastorder.will import WillResult, write_will
from runtime.llm.client import CallStats
from runtime.ports import BirthRecord, GameBody, Memory, NodeRegistry

# Retries of the parked-will delivery and the birth while hive is down: with the defaults
# the mind stands in for roughly half an hour (2s doubling, capped at 60s) — NETWORK.md
# section 9 keeps the generation waiting, the mind has nowhere else to go.
DEFAULT_HIVE_RETRIES = 30
DEFAULT_HIVE_BACKOFF_S = 2.0
DEFAULT_HIVE_MAX_PAUSE_S = 60.0
RESPAWN_ATTEMPTS = 2  # one retry: Py4jBody.respawn already waits a minute inside

# Where the will stands after retire attempts (review R1-1/R1-2).
DELIVERED = "hive"  # in the base
PARKED = "parked"  # undelivered, the retry loop owns it
SUPERSEDED = "superseded"  # 409 on the first retire: hive closed the node itself
REJECTED = "rejected"  # a permanent non-409 4xx: parked for the operator, no more retries


@dataclass(frozen=True)
class Rebirth:
    """The next node of the slot, ready for the loop to rebuild the persona."""

    record: BirthRecord
    traits: Traits
    inheritance: Inheritance
    will: Will
    emergency: bool
    model: str
    will_delivered: bool  # False when the will is still parked in run/<slot>/pending/
    will_error: str | None = None  # why the smart reply was refused, when it was
    stats: CallStats | None = None


class Lifecycle:
    """One slot's death path. Holds only wiring; every state is hive's or the body's."""

    def __init__(
        self,
        *,
        slot: str,
        body: GameBody,
        registry: NodeRegistry,
        memory: Memory,
        llm,
        naming: NamingPreset | None = None,
        quirks_path: Path | None = None,
        run_dir: Path | str | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        seed_factory: Callable[[], int] | None = None,
        hive_retries: int = DEFAULT_HIVE_RETRIES,
        hive_backoff_s: float = DEFAULT_HIVE_BACKOFF_S,
        hive_max_pause_s: float = DEFAULT_HIVE_MAX_PAUSE_S,
        respawn_attempts: int = RESPAWN_ATTEMPTS,
        log: Callable[[str, dict], None] | None = None,
    ):
        self._slot = slot
        self._body = body
        self._registry = registry
        self._memory = memory
        self._llm = llm
        self._naming = naming or preset(env("NODE_NAMING_PRESET", default="lab") or "lab")
        self._quirks_path = quirks_path or CHARACTER_DIR / "quirks.txt"
        self._run_dir = Path(run_dir if run_dir is not None else (env("RUN_DIR") or "run"))
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._seed_factory = seed_factory or (lambda: secrets.randbelow(2**31))
        self._hive_retries = hive_retries
        self._hive_backoff_s = hive_backoff_s
        self._hive_max_pause_s = hive_max_pause_s
        self._respawn_attempts = respawn_attempts
        self._log = log or (lambda type, payload=None: None)

    # --- the path ----------------------------------------------------------------------

    def on_death(
        self,
        node: Node,
        birth: BirthRecord,
        cause: DeathCause,
        reflection: ReflectionInput,
        *,
        pause: Callable[[], None] | None = None,
        resume: Callable[[], None] | None = None,
    ) -> Rebirth:
        """The strict order of NETWORK.md section 7.3; see the module docstring."""
        if pause is not None:
            pause()
        died_at = self._now().isoformat()
        written = write_will(
            self._llm,
            node=node,
            naming=self._naming,
            slot=self._slot,
            birth=birth,
            cause=cause.cause,
            died_at=died_at,
            reflection=reflection,
        )
        if written.emergency:
            self._log("will.emergency", {"serial": node.serial, "error": written.error})
        else:
            self._log(
                "will.written",
                {"serial": node.serial, "model": written.model},
            )
        delivery = self._retire_or_park(node.serial, cause.cause, written.will)
        self._respawn()
        traits = self._roll()
        record, delivery = self._birth_and_deliver(
            node.serial, cause.cause, written.will, traits, delivery
        )
        inheritance = self._memory.inheritance(record.serial)
        if resume is not None:
            resume()
        return Rebirth(
            record=record,
            traits=traits,
            inheritance=inheritance,
            will=written.will,
            emergency=written.emergency,
            model=written.model,
            will_delivered=delivery == DELIVERED,
            will_error=written.error,
            stats=written.stats,
        )

    # --- steps -------------------------------------------------------------------------

    def _retire_or_park(self, serial: int, cause: str, will: Will) -> str:
        """The first retire attempt; see the delivery states above the class."""
        try:
            self._registry.retire(serial, cause, will.to_markdown())
            self._log("will.retired", {"serial": serial, "cause": cause})
            return DELIVERED
        except HiveRejected as exc:
            if exc.kind == "already_retired":
                # Nobody retired this node through us — hive closed it itself
                # (lost-close or startup reconcile). The will never landed: park it
                # superseded, a file beats a silent loss (review R1-2).
                self._park(serial, will, f"superseded: {exc.status} {exc.kind}: {exc.message}")
                self._log("will.superseded", {"serial": serial, "cause": cause})
                return SUPERSEDED
            self._park(serial, will, f"rejected {exc.status} {exc.kind}: {exc.message}")
            return REJECTED
        except HiveUnavailable as exc:
            self._park(serial, will, f"unavailable: {exc}")
            return PARKED

    def _park(self, serial: int, will: Will, why: str) -> Path:
        """Write-once: retries never rewrite the will, the file says what was said."""
        path = self._pending_path(serial)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(will.to_markdown(), encoding="utf-8")
            tmp.replace(path)
        self._log("will.parked", {"serial": serial, "path": str(path), "why": why})
        return path

    def _pending_path(self, serial: int) -> Path:
        return self._run_dir / self._slot / "pending" / f"will-{format_serial(serial)}.md"

    def _unpark(self, serial: int) -> None:
        path = self._pending_path(serial)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # the will is in hive; a stale file is noise, not a loss
            self._log("will.unpark_failed", {"serial": serial, "error": str(exc)})

    def _respawn(self) -> None:
        last: Exception | None = None
        for attempt in range(self._respawn_attempts):
            try:
                self._body.respawn()
                self._log("body.respawned", {})
                return
            except BodyUnavailable as exc:
                last = exc
                self._log("body.respawn_failed", {"attempt": attempt + 1, "error": str(exc)})
        raise BodyUnavailable(
            f"body did not stand up after {self._respawn_attempts} respawn attempts: {last}"
        ) from last

    def _roll(self) -> Traits:
        seed = self._seed_factory()
        return roll(seed, load_quirks(self._quirks_path))

    def _birth_and_deliver(
        self, serial: int, cause: str, will: Will, traits: Traits, delivery: str
    ) -> tuple[BirthRecord, str]:
        """Rounds of (delivery, then birth), one growing pause apart.

        A round retries the parked will first and calls `birth` only once the delivery
        is settled — DELIVERED, SUPERSEDED or REJECTED (review R1-1: a real hive closes
        a living node of the slot at birth with its own emergency will, so a birth ahead
        of the delivery would supersede the node's will and drop its only copy). A
        HiveUnavailable from the delivery ends the round right there: no birth this
        round, pause, again — NETWORK.md section 9 keeps the birth for after the
        recovery. A 409 inside the loop is the lost reply of our own landed call, not a
        supersession: it counts as delivered.
        """
        attempt = 0
        while True:
            if delivery == PARKED:
                try:
                    self._registry.retire(serial, cause, will.to_markdown())
                    delivery = DELIVERED
                    self._unpark(serial)
                    self._log("will.retired", {"serial": serial, "cause": cause, "via": "retry"})
                except HiveRejected as exc:
                    if exc.kind == "already_retired":  # our own call landed, reply lost
                        delivery = DELIVERED
                        self._unpark(serial)
                        self._log(
                            "will.retired",
                            {"serial": serial, "cause": cause, "via": "already_retired"},
                        )
                    else:  # permanent: the will stays parked for the operator
                        delivery = REJECTED
                        self._log(
                            "will.delivery_rejected",
                            {"serial": serial, "status": exc.status, "kind": exc.kind},
                        )
                except HiveUnavailable as exc:
                    if attempt >= self._hive_retries:
                        self._log(
                            "delivery.gave_up",
                            {"serial": serial, "attempts": attempt + 1, "error": str(exc)},
                        )
                        raise HiveUnavailable(
                            f"the death path did not land after {attempt + 1} rounds "
                            f"(will delivery): {exc}"
                        ) from exc
                    self._sleep(min(self._hive_backoff_s * (2**attempt), self._hive_max_pause_s))
                    attempt += 1
                    continue  # no birth until the will is settled (R1-1)
            try:
                record = self._registry.birth(self._slot, traits)
                self._log("life.birth", {"serial": record.serial, "slot": self._slot})
                return record, delivery
            except HiveRejected:
                raise  # permanent: retrying the same payload cannot help
            except HiveUnavailable as exc:
                if attempt >= self._hive_retries:
                    self._log(
                        "birth.gave_up",
                        {"slot": self._slot, "attempts": attempt + 1, "error": str(exc)},
                    )
                    raise HiveUnavailable(
                        f"the death path did not land after {attempt + 1} rounds (birth): {exc}"
                    ) from exc
                self._sleep(min(self._hive_backoff_s * (2**attempt), self._hive_max_pause_s))
                attempt += 1
