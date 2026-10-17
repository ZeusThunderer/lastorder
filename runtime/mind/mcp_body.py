"""Minimal body MCP server for the harness spike (T-024; NETWORK.md section 4, ADR-035, ADR-037).

One service per slot: the agent (OpenCode or ZCode in its container) sees the game ONLY through
these tools — no raw uc commands, no `@gamer`, no server commands. Names pass through perception
(`plain`, ADR-037); uc's own chat lines are dropped before anything reaches a prompt, and `say`
is the only way into the player chat, guarded (no `/`, `@`, leading `#`, control characters or
`§`; length and rate caps).

The spike keeps C1 thin on purpose (the full one is T-025): a ~1 Hz poller feeds the death
detector (hp = 0 plus the vanilla line — onDeath is silent for mob deaths), collects body
callback events into the `wait_events` queue, times "act -> effect" with a short 5 Hz burst
after each act, notices intent completion (`busy` True -> False) and — after a death has been
served — respawns the body itself, because uc does not auto-respawn without a running task (E1).
Every tool call is one JSONL line in the call log: the spike's numbers come from there.

Run: `python -m runtime.mind.mcp_body` (streamable HTTP, 0.0.0.0:${MCP_BODY_PORT:-8700}/mcp).
The port is published inside the body's network namespace; the py4j bridge stays on localhost
and is NOT reachable from the harness network (checked during the spike run, review P1-1).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import deque
from typing import Any, Callable, Mapping

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError

from runtime.game.py4j_body import LEVERS
from runtime.lastorder.death import DeathDetector
from runtime.perception.chat import clean_chat
from runtime.perception.translate import Perception

log = logging.getLogger("mcp_body")

# --- limits (spike values; rate caps move to the gateway in T-026) ---------------------

SAY_MAX_LEN = 256  # vanilla rejects longer chat anyway
SAY_MIN_INTERVAL_S = 4.0  # one line per 4 s; no chatter director anymore (ADR-035)
WAIT_TIMEOUT_MAX_S = 600.0
POLL_INTERVAL_S = 1.0
ACT_BURST_INTERVAL_S = 0.2  # after each act: poll ~5 Hz for 5 s to time "act -> effect" (P2-4)
ACT_BURST_SPAN_S = 5.0
RESPAWN_DELAY_S_DEFAULT = 45.0  # the will session needs time to react to the death event
ITEM_RE = re.compile(r"^[a-z0-9_]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
COORD_ABS_MAX = 30_000_000

# Levers the agent may pull: everything in LEVERS except the raw escape hatches (ADR-037).
FORBIDDEN_LEVERS = ("command", "chat")

# tap_key/hold_key allowlist: movement keys only; no chat, no console, no inventory screens
# (review P1-8). Values are uc KeyboardKey names as the levers accept them.
KEY_ALLOWLIST = frozenset({"w", "a", "s", "d", "space", "shift"})

INT_ARGS: dict[tuple[str, str], tuple[int, int]] = {
    ("goto", "x"): (-COORD_ABS_MAX, COORD_ABS_MAX),
    ("goto", "y"): (-64, 320),
    ("goto", "z"): (-COORD_ABS_MAX, COORD_ABS_MAX),
    ("goto_far", "x"): (-COORD_ABS_MAX, COORD_ABS_MAX),
    ("goto_far", "y"): (-64, 320),
    ("goto_far", "z"): (-COORD_ABS_MAX, COORD_ABS_MAX),
    ("goto_far", "horizon"): (1, 512),
    ("select_hotbar", "slot"): (0, 8),
    ("pillar_to", "y"): (-64, 320),
    ("hold_key", "ms"): (1, 5000),
    ("eat", "slot"): (0, 8),
    ("eat", "ms"): (100, 5000),
    ("get", "count"): (1, 64),
    ("place_block", "x"): (-COORD_ABS_MAX, COORD_ABS_MAX),
    ("place_block", "y"): (-64, 320),
    ("place_block", "z"): (-COORD_ABS_MAX, COORD_ABS_MAX),
    ("bridge_to", "x"): (-COORD_ABS_MAX, COORD_ABS_MAX),
    ("bridge_to", "y"): (-64, 320),
    ("bridge_to", "z"): (-COORD_ABS_MAX, COORD_ABS_MAX),
}
FLOAT_ARGS: dict[tuple[str, str], tuple[float, float]] = {
    ("look_at", "x"): (-3e7, 3e7),
    ("look_at", "y"): (-3e7, 3e7),
    ("look_at", "z"): (-3e7, 3e7),
}
ITEM_ARGS: set[tuple[str, str]] = {("get", "item"), ("equip", "item")}
KEY_ARGS: set[tuple[str, str]] = {("tap_key", "key"), ("hold_key", "key")}
NAMELIST_ARGS: set[tuple[str, str]] = {("attack", "allow"), ("attack", "avoid")}
# strike takes one float triple "pos"; mine takes int cells "pos"/"positions" (see below).
LEVER_REQUIRED: dict[str, frozenset[str]] = {
    "goto": frozenset({"x", "y", "z"}),
    "goto_far": frozenset({"x", "y", "z"}),
    "look_at": frozenset({"x", "y", "z"}),
    "select_hotbar": frozenset({"slot"}),
    "place_block": frozenset({"x", "y", "z"}),
    "bridge_to": frozenset({"x", "y", "z"}),
    "pillar_to": frozenset({"y"}),
    "tap_key": frozenset({"key"}),
    "hold_key": frozenset({"key"}),
    "strike": frozenset({"pos"}),
    "eat": frozenset({"slot"}),
    "get": frozenset({"item"}),
    "equip": frozenset({"item"}),
    # mine: "pos" or "positions" — checked in its own validator
}
_KNOWN_LEVERS = set(LEVERS) - set(FORBIDDEN_LEVERS)
_ARG_KEYS = set(INT_ARGS) | set(FLOAT_ARGS) | set(ITEM_ARGS) | set(KEY_ARGS) | set(NAMELIST_ARGS)


class ToolError(SdkToolError, ValueError):
    """A rejected tool call. Inherits the SDK's ToolError so the MCP layer reports it
    to the agent with this message (is_error=True) instead of hiding it as a crash."""


def _err(message: str) -> None:
    raise ToolError(message)


# --- validation (pure; unit-tested) ----------------------------------------------------


def _as_int(value: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _err(f"{what} must be a number, got {type(value).__name__}")
    iv = int(value)
    if not lo <= iv <= hi:
        _err(f"{what} must be within {lo}..{hi}, got {iv}")
    return iv


def _as_float(value: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _err(f"{what} must be a number, got {type(value).__name__}")
    fv = float(value)
    if not lo <= fv <= hi:
        _err(f"{what} must be within {lo}0..{hi}0, got {fv}")
    return fv


def _as_item(value: Any, what: str) -> str:
    """A plain item name ("oak log", "oak_log") to its registry id ("oak_log").

    Anything that is not a plain name — a command tail, stray punctuation, a bare number —
    is a rejection, so an injected `@` command can never ride into `@get` (review P1-2)."""
    if not isinstance(value, str):
        _err(f"{what} must be a string")
    tokens = value.strip().lower().split()
    if not 1 <= len(tokens) <= 3:
        _err(f"{what}: plain item name expected (1-3 words), got {value!r}")
    for token in tokens:
        if not re.fullmatch(r"[a-z0-9_:.-]+", token):
            _err(f"{what}: plain item name expected, got {value!r}")
        if not any(c.isalpha() for c in token):
            _err(f"{what}: a bare number is not an item name: {value!r}")
    if tokens[0].startswith("minecraft:"):
        tokens[0] = tokens[0].split(":", 1)[1]
    if any(":" in token for token in tokens):
        _err(f"{what}: plain item name expected, got {value!r}")
    name = "_".join(tokens).replace("-", "_").replace(".", "_")
    if not ITEM_RE.fullmatch(name):
        _err(f"{what}: plain item name expected, got {value!r}")
    return name


def _as_cell(value: Any, what: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        _err(f"{what} must be [x, y, z]")
    return [_as_int(v, what, -COORD_ABS_MAX, COORD_ABS_MAX) for v in value]


def validate_args(lever: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Check one act() call against the lever specs; returns the cleaned args dict.

    Unknown levers, forbidden levers, unknown argument names, wrong types and out-of-range
    values are rejections, not silent fixes: the agent sees the reason and can retry (P1-2)."""
    if lever in FORBIDDEN_LEVERS:
        _err(f"lever {lever!r} is not available to the agent (ADR-037)")
    if lever not in _KNOWN_LEVERS:
        _err(f"unknown lever {lever!r}; known: {', '.join(sorted(_KNOWN_LEVERS))}")
    args = dict(args or {})
    if lever == "mine":
        return _validate_mine(args)
    allowed = {name for (lv, name) in _ARG_KEYS if lv == lever}
    if lever == "strike":
        allowed.add("pos")
    extra = set(args) - allowed
    if extra:
        _err(f"unknown argument(s) {sorted(extra)} for lever {lever!r}; allowed: {sorted(allowed)}")
    missing = LEVER_REQUIRED.get(lever, frozenset()) - set(args)
    if missing:
        _err(f"{lever}: missing required argument(s) {sorted(missing)}")
    out: dict[str, Any] = {}
    for name, value in args.items():
        if (lever, name) in INT_ARGS:
            lo, hi = INT_ARGS[(lever, name)]
            out[name] = _as_int(value, f"{lever}.{name}", lo, hi)
        elif (lever, name) in FLOAT_ARGS:
            lo, hi = FLOAT_ARGS[(lever, name)]
            out[name] = _as_float(value, f"{lever}.{name}", lo, hi)
        elif (lever, name) in ITEM_ARGS:
            out[name] = _as_item(value, f"{lever}.{name}")
        elif (lever, name) in KEY_ARGS:
            if not isinstance(value, str) or value.strip().lower() not in KEY_ALLOWLIST:
                _err(f"{lever}.{name}: allowed keys are {sorted(KEY_ALLOWLIST)}, got {value!r}")
            out[name] = value.strip().lower()
        elif (lever, name) in NAMELIST_ARGS:
            if not isinstance(value, (list, tuple)):
                _err(f"{lever}.{name} must be a list of player names")
            cleaned = []
            for item in value:
                if not isinstance(item, str) or not NAME_RE.match(item):
                    _err(f"{lever}.{name}: bad player name {item!r}")
                cleaned.append(item)
            out[name] = cleaned
        elif lever == "strike" and name == "pos":
            if not isinstance(value, (list, tuple)) or len(value) != 3:
                _err("strike.pos must be [x, y, z]")
            out["pos"] = [_as_float(v, "strike.pos", -3e7, 3e7) for v in value]
        else:  # pragma: no cover - the extra check above keeps this unreachable
            _err(f"unknown argument {name!r} for lever {lever!r}")
    return out


def _validate_mine(args: Mapping[str, Any]) -> dict[str, Any]:
    extra = set(args) - {"pos", "positions"}
    if extra:
        _err(f"mine: unknown argument(s) {sorted(extra)}; allowed: pos, positions")
    cells: list[list[int]] = []
    if "pos" in args:
        cells.append(_as_cell(args["pos"], "mine.pos"))
    if "positions" in args:
        raw = args["positions"]
        if not isinstance(raw, (list, tuple)):
            _err("mine.positions must be a list of [x, y, z]")
        for cell in raw:
            cells.append(_as_cell(cell, "mine.positions"))
    if not cells:
        _err("mine: pos or positions is required")
    if len(cells) > 64:
        _err(f"mine: at most 64 cells per call, got {len(cells)}")
    return {"positions": cells}


def check_say(text: Any) -> str:
    """Validate one chat line; returns the cleaned text (review P1-3).

    Vanilla kicks the player for `§` and control characters — that would lose the body
    mid-run; `#` at the start is Baritone's chat command prefix in this fork (unionclef
    Settings), so it is rejected too."""
    if not isinstance(text, str):
        _err("say: text must be a string")
    cleaned = text.strip()
    if not cleaned:
        _err("say: empty text")
    if cleaned.startswith("/"):
        _err("say: server commands are not available (ADR-037)")
    if cleaned.startswith("#"):
        _err("say: '#' at the start of a chat line is a pathfinder command prefix")
    if "@" in cleaned:
        _err("say: '@' is not allowed in chat (raw mod commands are not available)")
    if CONTROL_RE.search(cleaned):
        _err("say: control characters and line breaks are not allowed")
    if "§" in cleaned:
        _err("say: formatting codes are not allowed")
    if len(cleaned) > SAY_MAX_LEN:
        _err(f"say: text longer than {SAY_MAX_LEN} characters")
    return cleaned


# --- call log --------------------------------------------------------------------------


class JsonlCallLog:
    """One JSON line per tool call; the spike's numbers come from here (review P1-5)."""

    def __init__(self, path: str | None = None):
        self._lock = threading.Lock()
        self._fh = open(path, "a", encoding="utf-8") if path else None
        self.entries: list[dict[str, Any]] = []

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.entries.append(dict(entry))
            if self._fh:
                self._fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                self._fh.flush()
        log.info("call %s", json.dumps(entry, ensure_ascii=False, default=str))

    def close(self) -> None:
        if self._fh:
            self._fh.close()


# --- the server ------------------------------------------------------------------------


class BodyMcp:
    """The five spike tools over one body. The MCP layer is async glue; the body is sync."""

    def __init__(
        self,
        body: Any,
        *,
        username: str,
        perception: Perception | None = None,
        call_log: JsonlCallLog | None = None,
        poll_interval: float = POLL_INTERVAL_S,
        respawn_delay: float = RESPAWN_DELAY_S_DEFAULT,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._body = body
        self._username = username
        self._perception = perception or Perception(descriptions={}, salt="", mode="plain")
        self._calls = call_log or JsonlCallLog()
        self._clock = clock
        self._poll_interval = poll_interval
        self._respawn_delay = respawn_delay
        self._detector = DeathDetector(username)
        self._queue: deque[dict[str, Any]] = deque(maxlen=200)
        self._queue_cond = threading.Condition()
        self._events_watermark = 0
        self._intent_seq = 0
        self._last_say_at = -1e9
        self._busy = False
        self._hp = -1.0
        self._last_damage_at = -1e9
        self._dead = False
        self._respawn_at: float | None = None
        self._respawned_for_death = False
        self._pending_act: dict[str, Any] | None = None
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None

    # -- lifecycle ------------------------------------------------------------------

    def start_poller(self) -> None:
        if self._poll_thread is not None:
            return
        self._poll_thread = threading.Thread(target=self._poll_loop, name="mcp-body-poll", daemon=True)
        self._poll_thread.start()

    def stop_poller(self) -> None:
        self._stop_event.set()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            started = self._clock()
            try:
                self._poll_once()
            except Exception as exc:  # a flaky bridge must not kill the poller
                log.warning("poll failed: %s: %s", type(exc).__name__, exc)
            interval = ACT_BURST_INTERVAL_S if self._pending_act else self._poll_interval
            delay = interval - (self._clock() - started)
            if delay > 0:
                time.sleep(delay)

    def _poll_once(self) -> None:
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
                self._emit_event("chat.line", text=str(payload.get("text", "")))
            elif event.type == "game.damage" and not payload.get("service"):
                self._last_damage_at = self._clock()
                self._emit_event("game.damage", text=str(payload.get("text", "")))
            # server.info and game.event are uc bookkeeping: dropped (ADR-037)

        state = self._body.state()
        hp = float(getattr(state, "hp", 0.0) or 0.0)
        if self._hp >= 0.0 and 0.0 < hp < self._hp and self._clock() - self._last_damage_at > 2.0:
            self._emit_event("game.damage", hp_from=self._hp, hp_to=hp)  # poll-fed fallback
        self._hp = hp

        death = self._detector.feed(state, list(self._body.recent_chat(10)))
        if death is not None:
            self._dead = True
            self._respawn_at = self._clock() + self._respawn_delay
            self._respawned_for_death = False
            self._pending_act = None
            self._emit_event("game.death", text=death.text)

        if self._dead and state.in_game and hp > 0.0:
            self._dead = False  # a living body again — the respawn worked
        if (
            self._dead
            and self._respawn_at is not None
            and not self._respawned_for_death
            and self._clock() >= self._respawn_at
        ):
            self._respawned_for_death = True  # exactly one respawn per death (review P1-4)
            self._respawn_at = None
            try:
                self._body.respawn()
            except Exception as exc:
                log.warning("respawn failed: %s: %s", type(exc).__name__, exc)

        busy = bool(self._body.busy())
        if self._pending_act is not None:
            pend = self._pending_act
            pos = tuple(round(float(c), 2) for c in getattr(state, "pos", ()) or ())
            moved = pend["pos"] is not None and pos != pend["pos"]
            changed = busy != pend["busy"]
            if changed or moved or self._clock() - pend["t"] >= ACT_BURST_SPAN_S:
                entry = dict(pend["entry"])
                entry["effect_ms"] = int((self._clock() - pend["t"]) * 1000)
                entry["effect_reason"] = "moved" if moved else ("busy_change" if changed else "span_end")
                self._calls.record(entry)
                self._pending_act = None
        # intent completion: an active task (busy) that just ended. Instant levers need no
        # event — the act() reply already told the agent the body is free.
        if self._busy and not busy and not self._dead:
            self._emit_event("intent.done")
        self._busy = busy

    def _emit_event(self, type_: str, **fields: Any) -> None:
        with self._queue_cond:
            self._queue.append({"type": type_, **fields})
            self._queue_cond.notify_all()

    # -- tools -----------------------------------------------------------------------

    async def _tool(self, name: str, args: Mapping[str, Any], fn: Callable[[], Any]) -> Any:
        """One logged call: sync body work in a thread, JSONL line either way (P1-5)."""
        entry: dict[str, Any] = {"ts": time.time(), "tool": name, "args": dict(args)}
        try:
            result = await asyncio.to_thread(fn)
        except ToolError as exc:
            entry.update(t_end=time.time(), ok=False, error=str(exc))
            self._calls.record(entry)
            raise
        except Exception as exc:
            entry.update(t_end=time.time(), ok=False, error=f"{type(exc).__name__}: {exc}")
            self._calls.record(entry)
            raise
        entry.update(t_end=time.time(), ok=True)
        if isinstance(result, dict):
            if name == "wait_events":
                entry["woke_by"] = result.get("type")
            elif name == "act":
                entry["intent_id"] = result.get("intent_id")
        self._calls.record(entry)
        return result

    def _seen(self, raw: str, kind: str) -> str:
        seen = self._perception.see(raw, kind)  # type: ignore[arg-type]
        return seen.render()

    def observe(self) -> dict[str, Any]:
        state = self._body.state()
        return {
            "self": {
                "in_game": bool(getattr(state, "in_game", False)),
                "hp": round(float(getattr(state, "hp", 0.0)), 1),
                "max_hp": round(float(getattr(state, "max_hp", 0.0)), 1),
                "food": round(float(getattr(state, "food", 0.0)), 1),
                "pos": [round(float(c), 1) for c in getattr(state, "pos", ()) or ()],
                "held": self._seen(str(getattr(state, "held", "") or "air"), "item"),
                "looking_at": self._seen(str(getattr(state, "looking_at", "") or "nothing"), "entity"),
                "on_ground": bool(getattr(state, "on_ground", False)),
                "dimension": str(getattr(state, "dimension", "")),
                "dead": bool(getattr(state, "dead", False)),
            },
            "entities": [
                {
                    "kind": str(e.kind),
                    "name": self._seen(e.name, "entity"),
                    "dist_m": round(float(e.dist_m), 1),
                    "pos": list(e.pos) if e.pos else None,
                }
                for e in self._body.entities(radius=16)
            ],
            "blocks": [
                {"name": self._seen(b.name, "block"), "pos": list(b.pos) if b.pos else None}
                for b in self._body.blocks_around(radius=4)
            ],
            "inventory": [
                {"name": self._seen(i.name, "item"), "count": int(i.count), "slot": int(i.slot)}
                for i in self._body.inventory()
            ],
            "chat": [self._perception.scrub(line) for line in clean_chat(self._body.recent_chat(5))],
        }

    def act(self, intent: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        cleaned = validate_args(intent, args)
        self._intent_seq += 1
        intent_id = self._intent_seq
        self._body.act(intent, cleaned)
        state = self._body.state()
        self._pending_act = {
            "t": self._clock(),
            "busy": bool(self._body.busy()),
            "pos": tuple(round(float(c), 2) for c in getattr(state, "pos", ()) or ()) or None,
            "entry": {
                "ts": time.time(),
                "tool": "act",
                "args": {"intent": intent, **cleaned},
                "t_end": time.time(),
                "ok": True,
                "intent_id": intent_id,
            },
        }
        return {"intent_id": intent_id, "busy": self._body.busy()}

    def stop(self) -> dict[str, Any]:
        self._body.stop()
        self._pending_act = None
        return {"stopped": True}

    def say(self, text: str) -> dict[str, Any]:
        cleaned = check_say(text)
        now = self._clock()
        if now - self._last_say_at < SAY_MIN_INTERVAL_S:
            _err(f"say: too fast, one line per {SAY_MIN_INTERVAL_S:.0f} s")
        self._last_say_at = now
        self._body.act("chat", {"text": cleaned})
        return {"said": True, "text": cleaned}

    def wait_events(self, timeout_s: float) -> dict[str, Any]:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            _err("wait_events: timeout_s must be a number")
        timeout_s = float(timeout_s)
        if not 0.0 < timeout_s <= WAIT_TIMEOUT_MAX_S:
            _err(f"wait_events: timeout_s must be within 0 < t <= {WAIT_TIMEOUT_MAX_S:.0f}")
        deadline = self._clock() + timeout_s
        with self._queue_cond:
            while not self._queue:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return {"type": "timeout", "timeout_s": timeout_s}
                self._queue_cond.wait(remaining)
            event = self._queue.popleft()
        return {"type": event["type"], **{k: v for k, v in event.items() if k != "type"}}


# --- wiring ----------------------------------------------------------------------------


def build_server(
    body: Any,
    *,
    username: str,
    perception: Perception | None = None,
    call_log: JsonlCallLog | None = None,
    poll_interval: float = POLL_INTERVAL_S,
    respawn_delay: float = RESPAWN_DELAY_S_DEFAULT,
    clock: Callable[[], float] = time.monotonic,
    start_poller: bool = True,
) -> tuple[MCPServer, BodyMcp]:
    """Wire BodyMcp into an MCPServer; returns (server, impl) — tests use the impl directly."""
    impl = BodyMcp(
        body,
        username=username,
        perception=perception,
        call_log=call_log,
        poll_interval=poll_interval,
        respawn_delay=respawn_delay,
        clock=clock,
    )
    server = MCPServer(
        name="body",
        version="0.1.0",
        instructions=(
            "Minimal body of one slot for the harness spike. Tools: observe (state, entities, "
            "blocks, inventory, filtered chat), act (levers), stop, say (player chat), "
            "wait_events (sleep until something happens or the timeout ends). Names are plain "
            "game names; uc service lines are already filtered out."
        ),
    )

    async def observe() -> dict[str, Any]:
        return await impl._tool("observe", {}, impl.observe)

    async def act(intent: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        return await impl._tool("act", {"intent": intent, "args": dict(args or {})}, lambda: impl.act(intent, args))

    async def stop() -> dict[str, Any]:
        return await impl._tool("stop", {}, impl.stop)

    async def say(text: str) -> dict[str, Any]:
        return await impl._tool("say", {"text": text}, lambda: impl.say(text))

    async def wait_events(timeout_s: float = 30.0) -> dict[str, Any]:
        return await impl._tool(
            "wait_events", {"timeout_s": timeout_s}, lambda: impl.wait_events(timeout_s)
        )

    server.add_tool(observe, name="observe")
    server.add_tool(act, name="act")
    server.add_tool(stop, name="stop")
    server.add_tool(say, name="say")
    server.add_tool(wait_events, name="wait_events")
    if start_poller:
        impl.start_poller()
    return server, impl


def main() -> None:
    import uvicorn

    from runtime.config import env
    from runtime.game.py4j_body import Py4jBody

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    port = int(env("MCP_BODY_PORT", default="8700") or "8700")
    username = env("MC_USERNAME", default="LastOrder01") or "LastOrder01"
    log_path = env("MCP_LOG_PATH", default="") or None
    respawn_delay = float(env("MCP_RESPAWN_DELAY_S", default=str(RESPAWN_DELAY_S_DEFAULT)))

    call_log = JsonlCallLog(log_path)
    body = Py4jBody()
    server, _impl = build_server(body, username=username, call_log=call_log, respawn_delay=respawn_delay)
    # host="0.0.0.0" keeps the SDK's localhost-only DNS-rebinding protection off: the harness
    # reaches the port by service name (body-1:8700) inside the docker network, not via localhost.
    app = server.streamable_http_app(stateless_http=True, streamable_http_path="/mcp", host="0.0.0.0")
    log.info("body MCP for %s on 0.0.0.0:%d/mcp (log: %s)", username, port, log_path or "stderr")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
