"""C1, the executor contour of one mind (NETWORK.md sections 2 and 5; T-025).

One thread at 1-4 Hz owns the body between the agent's turns (ADR-032): it applies the
agent's intent through the `Py4jBody` levers with the section-2 arbitration (a new intent
waits out a running uc chain for at most a second, then stopPathing), reacts to damage
with the no-LLM rule fallback (flee under low hp, strike a hostile in reach, otherwise
keep the intent; after the threat calms down it returns to the intent), detects death on
every tick (hp = 0 plus the vanilla line — onDeath is silent for mob deaths), stops the
body and hands the death to the runner; respawn happens ONLY by the runner's command.
The idle watchdog arms `retire_stuck`, and the life clock warns 3 minutes before ttl_min.

Threat maneuvers are C1's own: while one runs, the agent's intent is suspended and busy
transitions belong to the maneuver, never to the intent — the intent's verdict is counted
only after it is applied again on calm (review P1-3). Names that reach the agent pass
through perception (`plain`, ADR-037); the JSONL log may keep raw ids (review P1-4).

T-031 (ADR-027): with a `JevGate` wired in, the contour asks Jev at threat onset and then
no more often than the gate's floor. The snapshot and the code-built options (flee / fight /
keep, plus the agent's `policy/threat.md` via the gate) are taken under the body lock, the
call itself runs with the lock released, and the pick is applied only if the picture it was
made for is still live — otherwise, on timeout or on any error, the rules decide and a
`jev.fallback` line lands in the log (plan review P1-1..P1-3).

Everything with a clock is injectable, so the tests drive `_tick()` by hand with a fake
clock and no threads. All body access — from this thread and from the MCP tool threads —
goes through one lock (review P1-6).
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime.lastorder.death import DeathDetector, classify_death
from runtime.mind.jev import JevGate
from runtime.perception.chat import is_service_line, parse_speaker, strip_formatting
from runtime.perception.topics import TopicFilter
from runtime.perception.translate import Perception
from runtime.ports import BirthRecord, GameState, Option
from runtime.hive.client import HiveClient, HiveRejected, HiveUnavailable

log = logging.getLogger("mind.executor")

# --- limits (config: env in mcp_body.main; the module defaults keep 1-4 Hz) ---------------

TICK_MIN_S, TICK_MAX_S = 0.25, 1.0  # the contour's frequency corridor (NETWORK section 2)
TICK_S_DEFAULT = 0.5  # 2 Hz
ACT_BURST_INTERVAL_S = 0.2  # right after an applied act: poll ~5 Hz for 5 s (spike P2-4)
ACT_BURST_SPAN_S = 5.0
INTENT_SWITCH_WAIT_S = 1.0  # section 2 arbitration: wait out a uc chain, then stopPathing
INSTANT_GRACE_S = 1.0  # a lever that never turned busy True settles within this window
ARRIVE_RADIUS_M = 1.5  # goto-family arrival check
FLEE_HP_DEFAULT = 6.0
FIGHT_RADIUS_M_DEFAULT = 3.0
THREAT_CALM_S_DEFAULT = 6.0
EVADE_M_DEFAULT = 12.0
STRIKE_INTERVAL_S = 1.0
IDLE_S_DEFAULT = 120.0
LIFE_WARN_S = 180.0  # life.expiring this many seconds before ttl_min
QUEUE_LIMIT = 200
RESPAWN_MAX_ATTEMPTS = 3  # bounded respawn retries, then the body stays dead (spike R1-4)

# Levers whose outcome C1 can verify; everything else settles as done on completion.
TARGET_LEVERS = ("goto", "goto_far", "bridge_to", "pillar_to")  # arrival by position
GET_LEVER = "get"  # arrival by inventory count
DEAD_ACT_ERROR = "the body is dead: the runner decides the respawn, the intent is not accepted"

# What counts as a threat source: uc's getEntitiesInfo reports the entity TYPE id
# ("zombie", "cow", "item"…, Py4jEntryPoint.getEntitiesInfo), never a hostility flag,
# so the overworld/nether hostiles are listed here. Environment knowledge for the rule
# fallback, not character behavior (ADR-032). Items, drops, animals and players never match.
HOSTILE_TYPES = frozenset(
    {
        "zombie", "zombie_villager", "husk", "drowned", "skeleton", "stray", "bogged",
        "creeper", "spider", "cave_spider", "witch", "enderman", "phantom", "slime",
        "magma_cube", "silverfish", "endermite", "pillager", "vindicator", "evoker",
        "ravager", "vex", "illusioner", "guardian", "elder_guardian", "shulker",
        "blaze", "ghast", "hoglin", "piglin", "piglin_brute", "zombified_piglin",
        "zoglin", "breeze", "creaking", "warden", "wither", "ender_dragon", "giant",
    }
)


# --- the JSONL log of one mind ------------------------------------------------------------


class JsonlLog:
    """One JSON line per record in `log.jsonl` (volume `run`): tool calls, events,
    intents, deaths, C1 maneuvers and body errors. `ts` is wall epoch, always present."""

    def __init__(self, path: str | None = None):
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8") if path else None

    def record(self, kind: str, **fields: Any) -> None:
        entry = {"ts": time.time(), "kind": kind, **fields}
        with self._lock:
            if self._fh:
                self._fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                self._fh.flush()
        log.info("%s", json.dumps(entry, ensure_ascii=False, default=str))

    def close(self) -> None:
        if self._fh:
            self._fh.close()


# --- the event queue (NETWORK section 5) ---------------------------------------------------

BOARD_LINES_MAX = 10  # one `board` event: the rest folds into the "more" marker
_BOARD_MORE_RE = re.compile(r"^… (\d+) more, `board`$")

CHAT_LINES_MAX = 20  # one `chat.line` event: the newest stay, the rest folds
_CHAT_DROPPED_RE = re.compile(r"^… (\d+) earlier lines dropped$")

# Slot names are `LastOrderNN` (CLAUDE.md): never a valid operator name, whatever the case.
_SLOT_NAME_RE = re.compile(r"^lastorder\d{2}$", re.IGNORECASE)


def _board_marker(dropped: int) -> str:
    return f"… {dropped} more, `board`"


def _split_board_lines(lines: Any) -> tuple[list[str], int]:
    """Peel a trailing "more" marker back into (lines, dropped)."""
    if isinstance(lines, list) and lines and _BOARD_MORE_RE.match(lines[-1] or ""):
        return list(lines[:-1]), int(_BOARD_MORE_RE.match(lines[-1]).group(1))
    return list(lines) if isinstance(lines, list) else [], 0


def cap_board_lines(lines: list[str], dropped: int = 0) -> list[str]:
    """A `board` event never carries more than BOARD_LINES_MAX envelope lines
    (TASKBOARD.md section 11); `dropped` counts what earlier caps already hid."""
    if len(lines) <= BOARD_LINES_MAX and not dropped:
        return list(lines)
    hidden = dropped + max(0, len(lines) - BOARD_LINES_MAX)
    return [*lines[:BOARD_LINES_MAX], _board_marker(hidden)]


def _chat_dropped_marker(dropped: int) -> dict[str, Any]:
    """A marker entry: no `from` — it is the environment's, not a speaker's."""
    return {"text": f"… {dropped} earlier lines dropped"}


def _split_chat_entries(entries: Any) -> tuple[list[dict[str, Any]], int]:
    """Peel a trailing "dropped" marker back into (entries, dropped)."""
    if isinstance(entries, list) and entries:
        last = entries[-1]
        if isinstance(last, dict) and "from" not in last:
            match = _CHAT_DROPPED_RE.match(str(last.get("text") or ""))
            if match:
                return list(entries[:-1]), int(match.group(1))
    return list(entries) if isinstance(entries, list) else [], 0


def cap_chat_entries(entries: list[dict[str, Any]], dropped: int = 0) -> list[dict[str, Any]]:
    """A `chat.line` event keeps the newest CHAT_LINES_MAX entries (T-051): a chatty
    stretch between two `wait_events` loses its oldest lines, never the fresh ones."""
    if len(entries) <= CHAT_LINES_MAX and not dropped:
        return list(entries)
    hidden = dropped + max(0, len(entries) - CHAT_LINES_MAX)
    return [*entries[-CHAT_LINES_MAX:], _chat_dropped_marker(hidden)]


class EventQueue:
    """Events wait here between `wait_events` calls, capped; same-type events collapse —
    a newer one replaces the queued fields and keeps the queue position. Two types
    accumulate instead: `board` (T-050, first 10 lines kept) and `chat.line` (T-051,
    newest 20 kept) — batches between two `wait_events` never eat each other."""

    def __init__(self, limit: int = QUEUE_LIMIT):
        self._items: list[dict[str, Any]] = []
        self._limit = limit
        self._cond = threading.Condition()

    def put(self, type_: str, **fields: Any) -> None:
        with self._cond:
            if type_ == "board":
                self._put_board(fields.get("lines"))
            elif type_ == "chat.line":
                self._put_chat(fields.get("lines"))
            else:
                for i, queued in enumerate(self._items):
                    if queued["type"] == type_:
                        self._items[i] = {"type": type_, **fields}
                        break
                else:
                    self._append({"type": type_, **fields})
            self._cond.notify_all()

    def _put_board(self, lines: Any) -> None:
        add, add_dropped = _split_board_lines(lines)
        for queued in self._items:
            if queued["type"] == "board":
                held, held_dropped = _split_board_lines(queued.get("lines"))
                queued["lines"] = cap_board_lines(held + add, held_dropped + add_dropped)
                return
        self._append({"type": "board", "lines": cap_board_lines(add, add_dropped)})

    def _put_chat(self, lines: Any) -> None:
        add, add_dropped = _split_chat_entries(lines)
        for queued in self._items:
            if queued["type"] == "chat.line":
                held, held_dropped = _split_chat_entries(queued.get("lines"))
                queued["lines"] = cap_chat_entries(held + add, held_dropped + add_dropped)
                return
        self._append({"type": "chat.line", "lines": cap_chat_entries(add, add_dropped)})

    def _append(self, item: dict[str, Any]) -> None:
        if len(self._items) >= self._limit:
            self._items.pop(0)
        self._items.append(item)

    def clear(self) -> None:
        """Drop everything: a respawned body starts a new life with a fresh queue —
        the next session must not replay the previous node's death and chat."""
        with self._cond:
            self._items.clear()
            self._cond.notify_all()

    def pop(self, timeout_s: float, clock: Callable[[], float]) -> dict[str, Any] | None:
        """The first accumulated event, or None when the timeout runs out."""
        deadline = clock() + timeout_s
        with self._cond:
            while not self._items:
                remaining = deadline - clock()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)
            return self._items.pop(0)

    def pending(self) -> tuple[dict[str, Any], ...]:
        with self._cond:
            return tuple(self._items)


# --- intents -------------------------------------------------------------------------------


class Intent:
    """One agent intent. PENDING waits out a uc chain; ACTIVE owns the body; SUSPENDED
    yields to a C1 threat maneuver and is re-applied on calm; DONE/FAILED/CLEARED retire."""

    PENDING, ACTIVE, SUSPENDED, DONE, FAILED, CLEARED = (
        "pending",
        "active",
        "suspended",
        "done",
        "failed",
        "cleared",
    )

    def __init__(self, id_: int, lever: str, args: dict[str, Any], submitted_at: float):
        self.id = id_
        self.lever = lever
        self.args = args
        self.submitted_at = submitted_at
        self.state = Intent.PENDING
        self.applied_at: float | None = None
        self.busy_seen = False

    @property
    def finished(self) -> bool:
        return self.state in (Intent.DONE, Intent.FAILED, Intent.CLEARED)


@dataclass
class _JevRequest:
    """What one Jev call was made for: the locked snapshot, the code-built options and the
    backend name for log records. The call itself runs outside the body lock (P1-1)."""

    snapshot: dict[str, Any]
    options: list[Option]
    backend: str


# --- C1 ------------------------------------------------------------------------------------


class Executor:
    """The executor contour. `run()` is the thread; tests call `_tick()` with a fake clock."""

    def __init__(
        self,
        body: Any,
        *,
        username: str,
        log_: JsonlLog,
        perception: Perception | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
        tick_interval: float = TICK_S_DEFAULT,
        flee_hp: float = FLEE_HP_DEFAULT,
        fight_radius_m: float = FIGHT_RADIUS_M_DEFAULT,
        threat_calm_s: float = THREAT_CALM_S_DEFAULT,
        evade_m: float = EVADE_M_DEFAULT,
        idle_s: float = IDLE_S_DEFAULT,
        birth: BirthRecord | None = None,
        hive: HiveClient | None = None,
        jev: JevGate | None = None,
        operator_name: str = "",
        login: "SessionLogin | None" = None,
        topic_filter: TopicFilter | None = None,
    ):
        if not TICK_MIN_S <= tick_interval <= TICK_MAX_S:
            raise ValueError(
                f"tick_interval must be within {TICK_MIN_S}..{TICK_MAX_S} s (1-4 Hz), got {tick_interval}"
            )
        self._body = body
        self._username = username
        self._log = log_
        # The operator's game name (T-051): the environment marks `<name>` chat lines
        # with it. ANY slot's name refuses (review R1-1: one variable feeds three
        # minds — a sister's voice must never pass for the operator's), whatever the
        # letter case; empty simply leaves the marking off.
        self._operator_name = str(operator_name or "").strip()
        if self._operator_name and (
            self._operator_name.lower() == username.lower() or _SLOT_NAME_RE.match(self._operator_name)
        ):
            refused = self._operator_name
            self._operator_name = ""
            self._log.record(
                "event",
                event="operator_name.ignored",
                reason=f"OPERATOR_MC_NAME {refused!r} is a slot name, not the operator's",
            )
        self._operator_low = self._operator_name.lower()
        self._perception = perception or Perception(descriptions={}, salt="", mode="plain")
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._tick_interval = tick_interval
        self._flee_hp = flee_hp
        self._fight_radius_m = fight_radius_m
        self._threat_calm_s = threat_calm_s
        self._evade_m = evade_m
        self._idle_s = idle_s
        self._birth = birth
        self._hive = hive
        self._jev = jev  # None: pure rules, byte-for-byte the pre-T-031 behavior
        # The environment's own /login (T-053): sends on world entry, masks the
        # password out of everything the agent or a log can see. None — off.
        self._login = login
        # The operator's banned chat topics (ADR-048): a line, a name or a death
        # text on one is dropped HERE, before any event, journal or observe() —
        # the provider never receives it. None or an empty list — off.
        self._topics = topic_filter

        # One lock for the whole body: the C1 thread and the MCP tool threads share it (P1-6).
        self.body_lock = threading.RLock()

        self._detector = DeathDetector(username)
        self.events = EventQueue()
        self._events_watermark = 0
        self._intent_seq = 0
        self._current: Intent | None = None

        self._hp = -1.0
        self._last_damage_at = -1e9
        self._threat: str | None = None  # "flee" | "fight" once a maneuver ran
        self._jev_mode: str | None = None  # a live Jev pick ("flee"|"fight"|"keep") leads the episode (P1-3)
        self._evading = False  # a flee goto of ours is (or was) running
        self._last_evade: dict[str, int] | None = None
        self._last_strike_at = -1e9
        # T-054: the death path arms only after this life has seen the body alive
        # once. A body restored dead (days on the death screen) is a stale corpse,
        # not this node's death — see _stale_dead.
        self._saw_alive = False
        self._stale_dead_marked = False
        # T-072: when this mind process started — a life armed with an older birth
        # was carried over from the previous mind (the runner re-arms after a mind
        # restart), so a corpse under it is ITS death, not a stale one
        self._started_wall = wall_clock()
        self._dead = False
        self._dead_since_mono = 0.0
        self._dead_since_wall: datetime | None = None
        self._death_cause = ""
        self._death_text = ""
        self._respawn_attempts = 0
        self._idle_flag = False
        self._idle_fired_at = -1e9
        self._last_activity_at = self._clock()
        self._life_warned = False
        self._stuck_requested = False  # a successful retire_stuck rules its own death cause (R1-4)
        self._pending_act: dict[str, Any] | None = None  # act->effect burst bookkeeping
        self._busy_last = False

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- what the MCP layer reaches for ---------------------------------------------------

    @property
    def body(self) -> Any:
        return self._body

    @property
    def journal(self) -> JsonlLog:
        return self._log

    # -- lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="mind-c1", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            started = self._clock()
            try:
                # _tick owns the body lock in sections: a Jev call happens with the lock
                # released, so submits, MCP tools and the runner endpoints never wait on
                # JEV_TIMEOUT_S (plan review P1-1).
                self._tick()
            except Exception as exc:  # a flaky bridge must not kill C1
                self._log.record("error", scope="tick", error=f"{type(exc).__name__}: {exc}")
            interval = ACT_BURST_INTERVAL_S if self._pending_act else self._tick_interval
            delay = interval - (self._clock() - started)
            if delay > 0:
                self._sleep(delay)

    # -- agent text (plain names only; ADR-037, review P1-4) ----------------------------

    def _clean_text(self, raw: Any) -> str | None:
        text = str(raw or "")
        if is_service_line(text):
            return None
        clean = self._perception.scrub(strip_formatting(text)).strip()
        if clean and self._banned(clean, scope="text"):
            return None
        return clean or None

    def _banned(self, text: str, *, scope: str, name: bool = False) -> bool:
        """True when `text` is on a banned topic. The journal records the topic and
        the scope alone — never the text: Last Order reads these journals (ADR-048)."""
        if self._topics is None:
            return False
        hit = self._topics.name_topic(text) if name else self._topics.topic(text)
        if hit is None:
            return False
        self._log.record("event", event="chat.filtered", topic=hit, scope=scope)
        return True

    def masked_name(self, raw: Any, fallback: str) -> str:
        """A player or entity name on a banned topic gives way to `fallback` (its
        kind): the agent still sees something is there, never what it is called."""
        text = str(raw or "")
        return fallback if text and self._banned(text, scope="name", name=True) else text

    def chat_entry(self, raw: Any) -> dict[str, Any] | None:
        """One chat line as the agent sees it (T-051): the text scrubbed plain, `from`
        — the speaker the SERVER's `<Name>` prefix names, and `operator: true` on the
        configured operator's lines alone. The mark is its own field (review R2-1): a
        player who happens to be named `operator` is just a player, and the text of a
        line can never forge either field. System lines carry no `from`; service
        lines answer None. The login password is masked before anything else — it
        must never reach a log, an event or observe() (T-053)."""
        text = strip_formatting(str(raw or "")).strip()
        if self._login is not None:
            text = self._login.mask(text)
        if not text or is_service_line(text):
            return None
        speaker, body = (None, text)
        parsed = parse_speaker(text)
        if parsed is not None:
            speaker, body = parsed
        # banned topics (ADR-048): the speaker's name and the whole line, before
        # the scrub — a hit drops the line entirely, as if it was never said
        if speaker is not None and self._banned(speaker, scope="speaker", name=True):
            return None
        if self._banned(text, scope="chat"):
            return None
        clean = self._perception.scrub(body).strip()
        if not clean:
            return None
        if speaker is None:
            return {"text": clean}
        entry: dict[str, Any] = {"from": self._perception.scrub(speaker).strip() or speaker}
        if speaker.lower() == self._operator_low:
            entry["operator"] = True
        entry["text"] = clean
        return entry

    @property
    def username(self) -> str:
        """The slot account this body plays as (LastOrder01)."""
        return self._username

    def _seen(self, raw: str, kind: str, fallback: str | None = None) -> str:
        if kind == "entity":
            raw = self.masked_name(raw, fallback or "entity")
        return self._perception.see(raw, kind).render()

    # -- events -------------------------------------------------------------------------

    def _emit(self, type_: str, **fields: Any) -> None:
        self.events.put(type_, **fields)
        self._log.record("event", event=type_, **fields)

    # -- the tick -----------------------------------------------------------------------

    def _tick(self) -> None:
        with self.body_lock:
            prev_busy = self._busy_last
            self._drain_body_events()
            state = self._body.state()
            hp = float(getattr(state, "hp", 0.0) or 0.0)
            self._hp_fallback_damage(state, hp)

            if self._login is not None:
                # the environment's /login rides the same lever the chat does; it
                # runs before anything else — nothing acts before the server lets it
                try:
                    self._login.on_tick(
                        bool(getattr(state, "in_game", False)),
                        [str(line) for line in self._body.recent_chat(10)],
                    )
                except Exception as exc:  # a login problem must never kill C1 —
                    # and its text must never carry the password into the log (R1-4)
                    self._log.record(
                        "error",
                        scope="login",
                        error=self._login.mask(f"{type(exc).__name__}: {exc}"),
                    )

            # T-054: a body restored dead (days on the death screen) is not this
            # node's death. Until this life has seen the body alive once, hp = 0 in
            # game means a stale corpse: respawn it here — uc's auto-respawn never
            # fires without a task (CLAUDE.md trap) — with no game.death and no
            # will. From the first living state on, the death path is unchanged.
            if not self._saw_alive and bool(getattr(state, "in_game", False)):
                if hp > 0.0 or self._carried_life():
                    # a carried-over life (T-072: a server move or slot restart
                    # re-armed it) died while the mind was away: the corpse is hers
                    # and goes down the death path — a will, not a quiet respawn
                    self._saw_alive = True
                else:
                    self._stale_dead()
                    return
            death = self._detector.feed(state, list(self._body.recent_chat(10)))
            if death is not None:
                self._on_death(death.text)
            if self._dead:
                return  # between death and the runner's respawn C1 holds still (review P1-2)

            self._life_clock()
            busy = bool(self._body.busy())
            request = self._threat_logic(state, hp, busy)
            if request is None:
                self._finish_tick(state, busy, prev_busy)
                return
        # The Jev call runs OUTSIDE the body lock (plan review P1-1), bounded by the
        # backend's JEV_TIMEOUT_S; everything above keeps running on other threads.
        decision = self._jev.advise(request.snapshot, request.options)
        with self.body_lock:
            state = self._body.state()
            hp = float(getattr(state, "hp", 0.0) or 0.0)
            busy = bool(self._body.busy())
            if self._dead or hp <= 0.0:
                if decision is not None:  # too late: the death tick is next, no maneuver
                    self._log.record(
                        "jev.fallback", backend=request.backend, reason="stale: the body died while Jev was thinking"
                    )
            else:
                self._apply_jev(request, decision, state, hp, busy)
            self._finish_tick(state, busy, prev_busy)

    def _finish_tick(self, state: GameState, busy: bool, prev_busy: bool) -> None:
        """The lock-held tail of a tick, shared by the plain and the Jev paths."""
        if self._threat is None:
            self._intent_logic(busy)  # a Jev "keep" leaves _threat None: the intent keeps the body
        self._idle_watchdog(busy)
        self._track_act_effect(state, busy, prev_busy)
        self._busy_last = busy

    def _drain_body_events(self) -> None:
        for event in self._body.events():
            try:
                idx = int(str(event.id).rsplit("#", 1)[1])
            except (IndexError, ValueError):
                idx = 0
            if idx <= self._events_watermark:
                continue
            self._events_watermark = idx
            payload = dict(event.payload or {})
            if event.type == "chat.line" and not payload.get("service"):
                entry = self.chat_entry(payload.get("text"))
                if entry is not None:
                    self._emit("chat.line", lines=[entry])
            elif event.type == "game.damage" and not payload.get("service"):
                self._last_damage_at = self._clock()
                source = self._nearest_hostile()
                fields: dict[str, Any] = {}
                text = self._clean_text(payload.get("text"))
                if text is not None:
                    fields["text"] = text
                if source is not None:
                    fields["source"] = self._seen(source.name, "entity", str(source.kind))
                    fields["source_dist_m"] = round(float(source.dist_m), 1)
                self._emit("game.damage", **fields)
            # server.info and game.event are uc bookkeeping: dropped (ADR-037)

    def _hp_fallback_damage(self, state: GameState, hp: float) -> None:
        if self._hp >= 0.0 and 0.0 < hp < self._hp and self._clock() - self._last_damage_at > 2.0:
            self._last_damage_at = self._clock()
            source = self._nearest_hostile()
            fields: dict[str, Any] = {"hp_from": round(self._hp, 1), "hp_to": round(hp, 1)}
            if source is not None:
                fields["source"] = self._seen(source.name, "entity", str(source.kind))
            self._emit("game.damage", **fields)
        self._hp = hp

    def _nearest_hostile(self) -> Any | None:
        hostiles = [e for e in self._body.entities(radius=16) if str(e.kind) in HOSTILE_TYPES]
        if not hostiles:
            return None
        return min(hostiles, key=lambda e: float(e.dist_m))

    # -- death (review P1-1, P1-2) --------------------------------------------------------

    def _carried_life(self) -> bool:
        """An armed life born before this mind started: the runner re-armed it."""
        if self._birth is None:
            return False
        try:
            born = datetime.fromisoformat(self._birth.born_at)
        except ValueError:
            return False
        if born.tzinfo is None:
            born = born.replace(tzinfo=timezone.utc)
        return born < self._started_wall

    def _stale_dead(self) -> None:
        """The stale-corpse branch of the tick (T-054): one `body.stale_dead` record
        per episode, then a respawn every tick until the body lives — a flaky bridge
        retries on the next tick, and `_after_respawn` re-arms the episode flag for
        the next life. No game.death, no will: this node never lived in this body."""
        if not self._stale_dead_marked:
            self._stale_dead_marked = True
            self._log.record("event", event="body.stale_dead", username=self._username)
        try:
            self._body.respawn()
        except Exception as exc:
            self._log.record(
                "error", scope="stale_respawn", error=f"{type(exc).__name__}: {exc}"
            )

    def _on_death(self, death_text_raw: str) -> None:
        now = self._clock()
        text = self._clean_text(death_text_raw) or ""
        cause = self._rule_cause(text)
        self._dead = True
        self._dead_since_mono = now
        self._dead_since_wall = self._wall_clock()
        self._death_cause = cause
        self._death_text = text
        self._threat = None
        self._jev_mode = None  # a Jev pick dies with the node: the next life starts on rules
        self._evading = False
        if self._current is not None and not self._current.finished:
            self._log.record(
                "intent", phase="cleared", intent_id=self._current.id, lever=self._current.lever
            )
            self._current.state = Intent.CLEARED
        self._current = None
        self._pending_act = None
        try:
            self._body.stop()
        except Exception as exc:
            self._log.record("error", scope="death_stop", error=f"{type(exc).__name__}: {exc}")
        # One record, two contracts: `kind: death` for the mind's log, the `event` key keeps
        # the spike orchestrator's `"event": "game.death"` grep working (T-024 spike.sh).
        self._log.record(
            "death", event="game.death", cause=cause, text=text or "game.death", username=self._username
        )
        self.events.put("game.death", text=text or "game.death", cause=cause)

    def _rule_cause(self, text: str) -> str:
        if self._birth is None:
            # No life clock configured: the honest default is `game` — unless this life asked
            # for its own death (R1-4); hive corrects the ruling at retire either way.
            return "stuck" if self._stuck_requested else "game"
        try:
            return classify_death(self._wall_clock(), self._birth, self._stuck_requested, text).cause
        except Exception as exc:
            self._log.record("error", scope="classify_death", error=f"{type(exc).__name__}: {exc}")
            return "game"

    def dead(self) -> bool:
        return self._dead

    def death_status(self) -> dict[str, Any]:
        """The runner's poll of the death state (GET /runner/status, review P1-1)."""
        return {
            "alive": not self._dead,
            "dead": self._dead,
            "dead_since": self._dead_since_wall.isoformat() if self._dead_since_wall else None,
            "cause": self._death_cause or None,
            "death_text": self._death_text or None,
            "serial": self._birth.serial if self._birth else None,
        }

    def runner_respawn(self) -> dict[str, Any]:
        """The runner's respawn command (POST /runner/respawn). The dead check, the attempt
        bound and the respawn itself are ONE critical section under the body lock (checker
        C-1, review R1-3): the dead C1 tick and a second concurrent POST serialize here,
        so the body is respawned at most once per death."""
        with self.body_lock:
            if not self._dead:
                raise RuntimeError("respawn refused: the body is not dead")
            if self._respawn_attempts >= RESPAWN_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"respawn gave up after {self._respawn_attempts} attempts; the body stays dead"
                )
            try:
                self._body.respawn()
            except Exception as exc:
                self._respawn_attempts += 1
                gave_up = self._respawn_attempts >= RESPAWN_MAX_ATTEMPTS
                self._log.record(
                    "error",
                    scope="respawn",
                    event="respawn.gave_up" if gave_up else "respawn.retry",
                    attempt=self._respawn_attempts,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            self._log.record("event", event="respawn.ok", attempt=self._respawn_attempts + 1)
            self._after_respawn()
        return {"respawned": True}

    def _after_respawn(self) -> None:
        self._dead = False
        self._dead_since_mono = 0.0
        self._dead_since_wall = None
        self._death_cause = ""
        self._death_text = ""
        self._respawn_attempts = 0
        self._hp = -1.0  # the first poll after respawn re-baselines hp
        # T-054: the new life has not seen this body alive yet — a body still dead
        # right after the respawn is again a stale corpse, not the newborn's death.
        self._saw_alive = False
        self._stale_dead_marked = False
        self._idle_flag = False
        self._last_activity_at = self._clock()
        self._threat = None
        self._jev_mode = None
        self._evading = False
        self._stuck_requested = False  # the cause flag belongs to the life that asked (R1-4)
        # Whatever intent object survived into this moment (a submit racing the death tick,
        # review R1-3) dies with the old node: the new life starts with no intent.
        self._current = None
        self.events.clear()  # a new life does not replay the old one's events
        # The life clock belongs to the node that died; the runner arms the next one
        # with POST /runner/birth. Until then life.expiring stays silent.
        self._birth = None
        self._life_warned = False

    def runner_birth(self, serial: int, born_at: str, ttl_min: int) -> dict[str, Any]:
        """Arm the life clock for the next node after a respawn (POST /runner/birth).

        One arming per life (review R1-2): the endpoint is reachable from the agent's
        sandbox, and an overwrite would let a node aim its own retire_stuck at another
        sister's serial — hive does not check the caller. While a birth is armed, a second
        request is a refusal; a fresh life (after the respawn cleared the clock) may arm.
        The whole check-and-arm runs under the body lock, so a birth racing the respawn's
        critical section cannot arm onto the dying life nor be wiped by _after_respawn
        (checker C-1, round 2)."""
        with self.body_lock:
            if self._dead:
                raise RuntimeError("birth refused: the body is still dead")
            if self._birth is not None:
                raise RuntimeError(
                    f"birth refused: the life clock is already armed for serial {self._birth.serial}"
                )
            self._birth = BirthRecord(serial=int(serial), born_at=str(born_at), ttl_min=int(ttl_min))
            self._life_warned = False
            self._log.record(
                "event", event="birth.armed", serial=int(serial), ttl_min=int(ttl_min)
            )
        return {"armed": True, "serial": int(serial)}

    # -- the life clock (life.expiring, 3 min before ttl_min) ------------------------------

    def _life_clock(self) -> None:
        if self._birth is None or self._life_warned:
            return
        try:
            born = datetime.fromisoformat(self._birth.born_at)
        except ValueError as exc:
            self._log.record("error", scope="life_clock", error=f"bad born_at: {exc}")
            self._birth = None
            return
        if born.tzinfo is None:
            born = born.replace(tzinfo=timezone.utc)
        deadline = born + timedelta(minutes=self._birth.ttl_min)
        remaining_s = (deadline - self._wall_clock()).total_seconds()
        if remaining_s <= LIFE_WARN_S:
            self._life_warned = True
            self._emit("life.expiring", remaining_min=round(remaining_s / 60.0, 1))

    # -- threat: rules lead, Jev advises (NETWORK section 2; T-031, ADR-027) ----------------

    def _threat_logic(self, state: GameState, hp: float, busy: bool) -> "_JevRequest | None":
        """Under the body lock. None: the rules (or a carried Jev mode) already ran this
        tick. A `_JevRequest`: C1 should consult Jev now — the snapshot is taken here, the
        call happens outside the lock (plan review P1-1), application after it."""
        now = self._clock()
        damaged = now - self._last_damage_at <= self._threat_calm_s
        if not damaged:
            if self._threat is not None or self._jev_mode is not None:
                self._threat = None
                self._jev_mode = None
                self._evading = False
                self._last_evade = None
                self._last_activity_at = now
            if self._current is not None and self._current.state == Intent.SUSPENDED:
                self._apply(self._current)  # calm: back to the agent's intent
            return None
        source = self._nearest_hostile()
        if source is None:
            return None  # damage without a visible hostile: nothing to aim a maneuver at
        if self._jev is not None and self._jev.due(now):
            return self._build_jev_request(state, hp, source)
        if self._jev_mode is not None:
            self._carry_jev_mode(state, busy, source)  # between Jev calls the pick leads (P1-3)
        else:
            self._threat_rules(state, hp, busy, source)
        return None

    def _build_jev_request(self, state: GameState, hp: float, source: Any) -> "_JevRequest":
        """The locked snapshot for one Jev call. Options are code-built sub-variants of the
        rules' own moves — no raw uc commands, no free text (ADR-037); lever args are
        computed fresh at apply time, what Jev sees is the menu, not the ammunition."""
        options = [
            Option("flee", "break off and run away from the threat", "goto", {}),
            Option("keep", "hold the current intent; no maneuver", "keep", {}),
        ]
        if float(source.dist_m) <= self._fight_radius_m:
            options.insert(1, Option("fight", "strike the hostile", "strike", {}))
        intent = None
        if self._current is not None and not self._current.finished:
            intent = {"lever": self._current.lever, "args": dict(self._current.args)}
        snapshot = {
            "hp": round(hp, 1),
            "food": round(float(getattr(state, "food", 0.0) or 0.0), 1),
            "pos": [round(float(c), 1) for c in getattr(state, "pos", ()) or ()],
            "held": self._seen(str(getattr(state, "held", "") or "air"), "item"),
            "threat": {
                "kind": self._seen(str(source.kind), "entity"),
                "dist_m": round(float(source.dist_m), 1),
            },
            "intent": intent,
        }
        return _JevRequest(snapshot=snapshot, options=options, backend=self._jev.backend_name)

    def _apply_jev(
        self,
        request: "_JevRequest",
        decision: Any | None,
        state: GameState,
        hp: float,
        busy: bool,
    ) -> None:
        """Under the body lock again. A decision survives only if the picture it was made
        for is still live; anything else is `jev.fallback` + the rules for the fresh
        picture (plan review P1-1)."""
        source = self._nearest_hostile()
        live = self._clock() - self._last_damage_at <= self._threat_calm_s and source is not None
        if decision is not None:
            # a stale pick never runs: the picture it answered is gone (P1-1)
            option = next((o for o in request.options if o.id == decision.option_id), None) if live else None
            if option is not None and option.lever == "strike" and float(source.dist_m) > self._fight_radius_m:
                option = None  # fight was offered in reach; the reach is gone
            if option is None:
                self._log.record(
                    "jev.fallback",
                    backend=request.backend,
                    reason="stale: the threat moved on while Jev was thinking",
                )
                decision = None
        if decision is None:
            self._threat_rules(state, hp, busy, source)  # the honest fallback, this tick
            return
        pos = tuple(round(float(c), 1) for c in getattr(state, "pos", ()) or ())
        self._jev_mode = decision.option_id  # carried by the mechanics until calm or the next call (P1-3)
        if decision.option_id == "flee":
            self._apply_flee(pos, source, busy)
        elif decision.option_id == "fight":
            self._apply_fight(source, pos)
        # "keep": no maneuver and no suspension — the intent keeps the body (P1-3)

    def _threat_rules(self, state: GameState, hp: float, busy: bool, source: Any) -> None:
        """The no-LLM rule fallback (NETWORK section 2): flee under low hp, strike a
        hostile in reach, otherwise keep the intent."""
        if source is None:
            return
        pos = tuple(round(float(c), 1) for c in getattr(state, "pos", ()) or ())
        now = self._clock()
        if hp <= self._flee_hp:
            # The maneuver owns the body from here: the intent is suspended and its verdict
            # is counted only after the calm re-apply (review P1-3).
            self._apply_flee(pos, source, busy)
        elif float(source.dist_m) <= self._fight_radius_m and now - self._last_strike_at >= STRIKE_INTERVAL_S:
            self._apply_fight(source, pos)
        # else: damage from beyond the rules' reach (a skeleton at 10 m while hp is high) —
        # an event for the agent, but NO maneuver runs, so the intent is NOT suspended:
        # it keeps the body, settles on its own and the idle watchdog stays armed (R1-1).

    def _carry_jev_mode(self, state: GameState, busy: bool, source: Any) -> None:
        """The Jev pick of this episode keeps leading between calls (P1-3): the same
        mechanics the rules use (_evading/_last_evade, STRIKE_INTERVAL_S), nothing new."""
        pos = tuple(round(float(c), 1) for c in getattr(state, "pos", ()) or ())
        if self._jev_mode == "flee":
            self._apply_flee(pos, source, busy)
        elif self._jev_mode == "fight" and source is not None:
            now = self._clock()
            if (
                float(source.dist_m) <= self._fight_radius_m
                and now - self._last_strike_at >= STRIKE_INTERVAL_S
            ):
                self._apply_fight(source, pos)
        # "keep": nothing — no maneuver, no suspension

    def _apply_flee(self, pos: tuple[float, ...], source: Any, busy: bool) -> None:
        self._suspend_intent()
        self._threat = "flee"
        target = self._evade_target(pos, source)
        if not self._evading:
            if busy:
                self._body.stop()  # the intent's chain yields to the flee (section 2)
            self._maneuver("goto", target, why="flee")
            self._evading = True
            self._last_evade = target
        elif not busy and target != self._last_evade:
            # the previous flee leg ended, the picture changed: re-aim further out
            self._maneuver("goto", target, why="flee")
            self._last_evade = target

    def _apply_fight(self, source: Any, pos: tuple[float, ...]) -> None:
        self._suspend_intent()
        self._threat = "fight"
        self._last_strike_at = self._clock()
        self._maneuver("strike", {"pos": [float(c) for c in (source.pos or pos)]}, why="fight")

    def _evade_target(self, pos: tuple[float, ...], source: Any) -> dict[str, int]:
        sp = tuple(float(c) for c in (source.pos or ())) or (0.0, 0.0, 0.0)
        dx, dy, dz = (pos[0] - sp[0]), (pos[1] - sp[1]), (pos[2] - sp[2])
        norm = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        k = self._evade_m / norm
        return {
            "x": int(round(pos[0] + dx * k)),
            "y": int(round(max(pos[1], -60.0))),
            "z": int(round(pos[2] + dz * k)),
        }

    def _suspend_intent(self) -> None:
        if self._current is not None and self._current.state == Intent.ACTIVE:
            self._current.state = Intent.SUSPENDED

    def _maneuver(self, lever: str, args: dict[str, Any], why: str) -> None:
        self._body.act(lever, args)
        self._last_activity_at = self._clock()
        self._log.record("c1", action=why, lever=lever, args=args)

    # -- intents (section 2 arbitration) ---------------------------------------------------

    def submit(self, lever: str, args: Mapping[str, Any]) -> Intent:
        with self.body_lock:  # intent state changes never race the C1 tick (checker C-2);
            # the dead check is inside the same critical section, so a death landing between
            # the check and the swap cannot arm an intent for the next node's body (R1-3)
            if self._dead:
                raise RuntimeError(DEAD_ACT_ERROR)
            if self._current is not None and not self._current.finished:
                self._log.record(
                    "intent", phase="cleared", intent_id=self._current.id, lever=self._current.lever
                )
                self._current.state = Intent.CLEARED
            self._intent_seq += 1
            intent = Intent(self._intent_seq, lever, dict(args), self._clock())
            self._current = intent
            self._idle_flag = False
            self._last_activity_at = self._clock()
            self._log.record(
                "intent", phase="submitted", intent_id=intent.id, lever=lever, args=intent.args
            )
        return intent

    def stop_intent(self) -> None:
        self._pending_act = None
        if self._current is not None and not self._current.finished:
            self._log.record(
                "intent", phase="cleared", intent_id=self._current.id, lever=self._current.lever
            )
            self._current.state = Intent.CLEARED
        self._current = None
        self._body.stop()
        self._last_activity_at = self._clock()

    def _intent_logic(self, busy: bool) -> None:
        intent = self._current
        if intent is None or intent.finished:
            return
        if intent.state == Intent.PENDING:
            if busy and self._clock() - intent.submitted_at < INTENT_SWITCH_WAIT_S:
                return  # let the running uc chain finish (section 2 arbitration)
            if busy:
                self._body.stop()  # a second over the limit: stopPathing
            self._apply(intent)
            return
        if intent.state == Intent.ACTIVE:
            now = self._clock()
            if busy:
                intent.busy_seen = True
                self._last_activity_at = now
                return
            if intent.busy_seen or now - (intent.applied_at or now) >= INSTANT_GRACE_S:
                self._settle(intent)

    def _apply(self, intent: Intent) -> None:
        self._body.act(intent.lever, intent.args)
        intent.state = Intent.ACTIVE
        intent.applied_at = self._clock()
        intent.busy_seen = False
        self._last_activity_at = intent.applied_at
        state = self._body.state()
        self._pending_act = {
            "t": intent.applied_at,
            "pos": tuple(round(float(c), 2) for c in getattr(state, "pos", ()) or ()) or None,
            "intent_id": intent.id,
        }
        self._log.record(
            "intent", phase="applied", intent_id=intent.id, lever=intent.lever, args=intent.args
        )

    def _settle(self, intent: Intent) -> None:
        verdict, reason = self._verdict(intent)
        intent.state = Intent.DONE if verdict == "done" else Intent.FAILED
        fields: dict[str, Any] = {"intent_id": intent.id, "lever": intent.lever}
        if reason:
            fields["reason"] = reason
        self._log.record("intent", phase=verdict, **fields)
        self._emit(f"intent.{verdict}", **fields)

    def _verdict(self, intent: Intent) -> tuple[str, str]:
        """The honest subset C1 can verify: goto-family by position, get by inventory.
        Everything else settles done — no pretend knowledge of uc's internals."""
        args = intent.args
        if intent.lever in TARGET_LEVERS:
            state = self._body.state()
            pos = tuple(float(c) for c in getattr(state, "pos", ()) or ())
            if intent.lever == "pillar_to":
                arrived = abs(pos[1] - float(args["y"])) <= ARRIVE_RADIUS_M if len(pos) == 3 else False
            else:
                target = (float(args["x"]), float(args["y"]), float(args["z"]))
                arrived = (
                    len(pos) == 3
                    and math.dist(pos, target) <= ARRIVE_RADIUS_M
                )
            if arrived:
                return "done", ""
            now_pos = ", ".join(f"{c:.1f}" for c in pos) or "unknown"
            want = (
                f"y={args['y']}"
                if intent.lever == "pillar_to"
                else f"{args['x']},{args['y']},{args['z']}"
            )
            return "failed", f"target not reached: at ({now_pos}), wanted ({want})"
        if intent.lever == GET_LEVER:
            want_item, want_count = str(args.get("item", "")), int(args.get("count", 1))
            have = sum(
                int(i.count)
                for i in self._body.inventory()
                if str(i.name).split(":")[-1] == want_item
            )
            if have >= want_count:
                return "done", ""
            return "failed", f"collected {have} of {want_item}, wanted {want_count}"
        return "done", ""

    # -- the idle watchdog (task.idle arms retire_stuck; ADR-034) ---------------------------

    def _idle_watchdog(self, busy: bool) -> None:
        now = self._clock()
        if busy:
            self._last_activity_at = now
            self._idle_flag = False
            return
        if self._current is not None and not self._current.finished:
            return  # an intent is in flight or waiting out a chain: not idle
        idle_for = now - self._last_activity_at
        if idle_for < self._idle_s:
            return
        if not self._idle_flag:
            self._idle_flag = True
            self._idle_fired_at = now
            self._emit("task.idle", idle_s=round(idle_for, 1), retire_allowed=True)

    @property
    def retire_allowed(self) -> bool:
        return self._idle_flag

    # -- retire_stuck (ADR-034: only on the watchdog signal) -------------------------------

    def retire_stuck(self) -> dict[str, Any]:
        if not self._idle_flag:
            raise RuntimeError("retire_stuck refused: no idle signal from the watchdog")
        if self._birth is None:
            raise RuntimeError(
                "retire_stuck refused: no NODE_SERIAL/NODE_BORN_AT/NODE_TTL_MIN — the mind "
                "does not know its serial, hive cannot be asked"
            )
        if self._hive is None:
            raise RuntimeError("retire_stuck refused: no hive client configured")
        serial = self._birth.serial
        try:
            self._hive.request_death(serial, "stuck")
        except HiveUnavailable as exc:
            raise RuntimeError(f"retire_stuck: hive unreachable ({exc}); try again") from exc
        except HiveRejected as exc:
            raise RuntimeError(f"retire_stuck: hive rejected the request ({exc})") from exc
        self._idle_flag = False  # one signal retires one node; a refusal keeps the signal armed
        self._stuck_requested = True  # this death is self-chosen: the cause rules as stuck (R1-4)
        self._log.record("event", event="retire.requested", serial=serial, cause="stuck")
        return {"requested": True, "serial": serial, "cause": "stuck"}

    # -- act -> effect burst bookkeeping (spike P2-4) ---------------------------------------

    def _track_act_effect(self, state: GameState, busy: bool, prev_busy: bool) -> None:
        if self._pending_act is None:
            return
        pend = self._pending_act
        pos = tuple(round(float(c), 2) for c in getattr(state, "pos", ()) or ())
        moved = pend["pos"] is not None and pos != pend["pos"]
        changed = busy != prev_busy
        if moved or changed or self._clock() - pend["t"] >= ACT_BURST_SPAN_S:
            self._log.record(
                "call",
                tool="act_effect",
                intent_id=pend["intent_id"],
                effect_ms=int((self._clock() - pend["t"]) * 1000),
                effect_reason="moved" if moved else ("busy_change" if changed else "span_end"),
            )
            self._pending_act = None
