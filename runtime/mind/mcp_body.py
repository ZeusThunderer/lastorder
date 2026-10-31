"""The body MCP server of one slot (NETWORK.md section 4, ADR-035, ADR-037; T-024 spike,
full set in T-025).

The agent sees the game ONLY through these tools — no raw uc commands, no `@gamer`, no
server commands. The tool set is exactly section 4: `observe`, `act`, `stop`, `say`,
`wait_events`, `retire_stuck`. C1 (`runtime/mind/executor.py`) owns the body; this file is
thin async glue: validation of one call, one JSONL line, and handoff. All body access —
from C1's thread and from tool calls — goes through the executor's body lock (review P1-6).

Next to `/mcp` the same port serves the runner's endpoints (review P1-1): the runner in
`node-N` has no docker access, so `GET /runner/status` carries the death (cause included,
NETWORK section 6.3) and `POST /runner/respawn` / `POST /runner/birth` command the body
after it. The port lives in the body's network namespace and is never published to the
host; respawn outside the dead state is a 409 (review P1-7).

Run: `python -m runtime.mind` (or `python -m runtime.mind.mcp_body`) — streamable HTTP on
0.0.0.0:${MCP_BODY_PORT:-8700}/mcp.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from runtime.game.py4j_body import LEVERS
from runtime.mind.executor import (
    DEAD_ACT_ERROR,
    TICK_MAX_S,
    TICK_MIN_S,
    Executor,
    JsonlLog,
)
from runtime.mind.jev import JevGate
from runtime.perception.translate import Perception

log = logging.getLogger("mcp_body")

# --- limits (rate caps move to the gateway in T-026) --------------------------------------

SAY_MAX_LEN = 256  # vanilla rejects longer chat anyway
SAY_MIN_INTERVAL_S = 4.0  # one line per 4 s; no chatter director anymore (ADR-035)
WAIT_TIMEOUT_MAX_S = 600.0
ITEM_RE = re.compile(r"^[a-z0-9_]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
COORD_ABS_MAX = 30_000_000

# Levers the agent may pull: everything in LEVERS except the raw escape hatches (ADR-037).
FORBIDDEN_LEVERS = ("command", "chat")

# tap_key/hold_key allowlist: movement keys only; no chat, no console, no inventory screens
# (spike review P1-8). Values are uc KeyboardKey names as the levers accept them.
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


# --- validation (pure; unit-tested) --------------------------------------------------------


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
        _err(f"{what} must be within {lo}..{hi}, got {fv}")
    return fv


def _as_item(value: Any, what: str) -> str:
    """A plain item name ("oak log", "oak_log") to its registry id ("oak_log").

    Anything that is not a plain name — a command tail, stray punctuation, a bare number —
    is a rejection, so an injected `@` command can never ride into `@get` (spike P1-2)."""
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
    values are rejections, not silent fixes: the agent sees the reason and can retry."""
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
    """Validate one chat line; returns the cleaned text (spike review P1-3).

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


# --- the tools ----------------------------------------------------------------------------


class BodyMcp:
    """The six section-4 tools over one Executor. The MCP layer is async glue; C1 owns
    the body, this class only validates and forwards, one JSONL line per call."""

    def __init__(
        self,
        executor: Executor,
        *,
        perception: Perception | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._x = executor
        self._perception = perception or Perception(descriptions={}, salt="", mode="plain")
        self._clock = clock
        self._last_say_at = -1e9

    @property
    def executor(self) -> Executor:
        return self._x

    async def _tool(self, name: str, args: Mapping[str, Any], fn: Callable[[], Any]) -> Any:
        """One logged call: sync body work in a thread, one JSONL line either way."""
        entry: dict[str, Any] = {"tool": name, "args": dict(args), "ts": time.time()}
        try:
            result = await asyncio.to_thread(fn)
        except ToolError as exc:
            self._x.journal.record("call", ok=False, error=str(exc), t_end=time.time(), **entry)
            raise
        except Exception as exc:
            self._x.journal.record(
                "call", ok=False, error=f"{type(exc).__name__}: {exc}", t_end=time.time(), **entry
            )
            raise
        fields: dict[str, Any] = {"ok": True, "t_end": time.time()}
        if isinstance(result, dict):
            if name == "wait_events":
                fields["woke_by"] = result.get("type")
            elif name == "act":
                fields["intent_id"] = result.get("intent_id")
        self._x.journal.record("call", **fields, **entry)
        return result

    def _seen(self, raw: str, kind: str) -> str:
        return self._perception.see(raw, kind).render()

    # -- section 4 tools (sync bodies; the async wrappers in build_server add logging) ---

    def observe(self) -> dict[str, Any]:
        body = self._x.body
        with self._x.body_lock:  # one writer with C1 at a time (review P1-6)
            state = body.state()
            entities = tuple(body.entities(radius=16))
            blocks = tuple(body.blocks_around(radius=4))
            inventory = tuple(body.inventory())
            chat = tuple(body.recent_chat(5))
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
                for e in entities
            ],
            "blocks": [
                {"name": self._seen(b.name, "block"), "pos": list(b.pos) if b.pos else None}
                for b in blocks
            ],
            "inventory": [
                {"name": self._seen(i.name, "item"), "count": int(i.count), "slot": int(i.slot)}
                for i in inventory
            ],
            # the same entries `chat.line` events carry: `from` marks the speaker the
            # server's `<Name>` prefix names (`operator` when configured, T-051)
            "chat": [entry for entry in (self._x.chat_entry(line) for line in chat) if entry],
        }

    def act(self, intent: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        cleaned = validate_args(intent, args)
        try:
            submitted = self._x.submit(intent, cleaned)
        except RuntimeError as exc:  # the dead state refuses intents (review P1-2)
            raise ToolError(str(exc) or DEAD_ACT_ERROR) from exc
        return {"intent_id": submitted.id}

    def stop(self) -> dict[str, Any]:
        with self._x.body_lock:
            self._x.stop_intent()
        return {"stopped": True}

    def say(self, text: str) -> dict[str, Any]:
        cleaned = check_say(text)
        now = self._clock()
        if now - self._last_say_at < SAY_MIN_INTERVAL_S:
            _err(f"say: too fast, one line per {SAY_MIN_INTERVAL_S:.0f} s")
        self._last_say_at = now
        with self._x.body_lock:
            self._x.body.act("chat", {"text": cleaned})
        return {"said": True, "text": cleaned}

    def wait_events(self, timeout_s: float) -> dict[str, Any]:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            _err("wait_events: timeout_s must be a number")
        timeout_s = float(timeout_s)
        if not 0.0 < timeout_s <= WAIT_TIMEOUT_MAX_S:
            _err(f"wait_events: timeout_s must be within 0 < t <= {WAIT_TIMEOUT_MAX_S:.0f}")
        event = self._x.events.pop(timeout_s, self._clock)
        if event is None:
            return {"type": "timeout", "timeout_s": timeout_s}
        return {"type": event["type"], **{k: v for k, v in event.items() if k != "type"}}

    def retire_stuck(self) -> dict[str, Any]:
        try:
            return self._x.retire_stuck()
        except RuntimeError as exc:
            _err(str(exc))


# --- wiring -------------------------------------------------------------------------------


def build_server(
    impl: BodyMcp,
    *,
    start_c1: bool = True,
    runner_key: str = "",
) -> tuple[MCPServer, BodyMcp]:
    """Wire the tools and the runner endpoints; returns (server, impl).

    `runner_key` (env MIND_RUNNER_KEY) gates /runner/respawn, /runner/birth and
    /runner/event (T-027 plan review P1-4, T-050); empty keeps the endpoints open
    for dev/tests."""
    executor = impl.executor
    server = MCPServer(
        name="body",
        version="0.2.0",
        instructions=(
            "The body of one slot, held by C1 between your turns. Tools: observe (state, "
            "entities, blocks, inventory, filtered chat), act (an intent from the levers; C1 "
            "applies it and may interrupt it for a threat, then returns to it), stop, say "
            "(player chat), wait_events (sleep until an event or the timeout — you pick the "
            "rhythm), retire_stuck (only after a task.idle event). Names are plain game "
            "names; uc service lines are already filtered out."
        ),
    )

    async def observe() -> dict[str, Any]:
        """See the world through plain names: self (hp, food, pos, held...), entities,
        blocks, inventory and the last filtered chat lines."""
        return await impl._tool("observe", {}, impl.observe)

    async def act(intent: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Give C1 the next intent. `intent` is EXACTLY one lever name: goto, goto_far,
        look_at, select_hotbar, place_block, bridge_to, pillar_to, tap_key, hold_key,
        mine, attack, attack_stop, strike, use_held, eat, get, equip. All parameters go
        in the `args` object, e.g. {"intent": "goto", "args": {"x": 5, "y": 64, "z": 5}},
        {"intent": "get", "args": {"item": "oak log", "count": 8}}. C1 executes it, may
        interrupt it for a threat and returns to it; you learn the outcome from
        intent.done / intent.failed events. Returns the intent id."""
        return await impl._tool("act", {"intent": intent, "args": dict(args or {})}, lambda: impl.act(intent, args))

    async def stop() -> dict[str, Any]:
        """Drop the current intent and stop the body."""
        return await impl._tool("stop", {}, impl.stop)

    async def say(text: str) -> dict[str, Any]:
        """One line into the player chat (no / or @, limited length and rate)."""
        return await impl._tool("say", {"text": text}, lambda: impl.say(text))

    async def wait_events(timeout_s: float = 30.0) -> dict[str, Any]:
        """Sleep until the next event or the timeout — you pick your own rhythm. Events:
        game.damage, game.death, chat.line, intent.done, intent.failed, task.idle (arms
        retire_stuck), life.expiring, board (fresh task-board lines). chat.line carries
        `lines`: {from: speaker, text} entries, the operator's with operator: true.
        Events of one type collapse (board and chat.line batches accumulate their
        lines); the queue is capped."""
        return await impl._tool(
            "wait_events", {"timeout_s": timeout_s}, lambda: impl.wait_events(timeout_s)
        )

    async def retire_stuck() -> dict[str, Any]:
        """Eliminate yourself (request_death "stuck"). Works only right after a task.idle
        event; any new intent disarms it. Refused otherwise."""
        return await impl._tool("retire_stuck", {}, impl.retire_stuck)

    server.add_tool(observe, name="observe")
    server.add_tool(act, name="act")
    server.add_tool(stop, name="stop")
    server.add_tool(say, name="say")
    server.add_tool(wait_events, name="wait_events")
    server.add_tool(retire_stuck, name="retire_stuck")

    # --- the runner's endpoints (review P1-1, P1-7); same port, same body network ------
    # The commands are the node runner's alone (T-027 plan review P1-4): with a runner key
    # configured they need X-Runner-Key, so a sister with bash cannot respawn her dead
    # body under the runner's will session or arm the life clock of another serial.
    # /runner/status stays open: the death cause is the dying agent's own data.

    def _runner_key_ok(request: Request) -> bool:
        return not runner_key or request.headers.get("x-runner-key", "").strip() == runner_key

    @server.custom_route("/runner/status", methods=["GET"])
    async def runner_status(request: Request) -> Response:
        return JSONResponse(executor.death_status())

    @server.custom_route("/runner/respawn", methods=["POST"])
    async def runner_respawn(request: Request) -> Response:
        if not _runner_key_ok(request):
            return JSONResponse({"error": "missing or wrong X-Runner-Key"}, status_code=401)
        if not executor.dead():
            return JSONResponse({"error": "the body is not dead"}, status_code=409)
        try:
            result = await asyncio.to_thread(executor.runner_respawn)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(result)

    @server.custom_route("/runner/birth", methods=["POST"])
    async def runner_birth(request: Request) -> Response:
        if not _runner_key_ok(request):
            return JSONResponse({"error": "missing or wrong X-Runner-Key"}, status_code=401)
        try:
            payload = await request.json()
            serial, born_at, ttl_min = (
                int(payload["serial"]),
                str(payload["born_at"]),
                int(payload["ttl_min"]),
            )
            result = await asyncio.to_thread(executor.runner_birth, serial, born_at, ttl_min)
        except (ValueError, KeyError, TypeError) as exc:
            return JSONResponse({"error": f"bad payload: {exc}"}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(result)

    @server.custom_route("/runner/event", methods=["POST"])
    async def runner_event(request: Request) -> Response:
        """The board bridge's batch (T-050): one `board` event into the queue. The
        body is `{"type": "board", "lines": [str]}` alone — the runner cannot hand
        the sister a death, a chat line or any other event type."""
        if not _runner_key_ok(request):
            return JSONResponse({"error": "missing or wrong X-Runner-Key"}, status_code=401)
        try:
            payload = await request.json()
            type_, lines = payload["type"], payload["lines"]
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "bad payload"}, status_code=400)
        if (
            type_ != "board"
            or not isinstance(lines, list)
            or not lines
            or any(not isinstance(line, str) for line in lines)
        ):
            return JSONResponse(
                {"error": "the body is {type: board, lines: [str]} alone"}, status_code=400
            )
        executor.events.put("board", lines=list(lines))
        return JSONResponse({"queued": True, "lines": len(lines)})

    if start_c1:
        executor.start()
    return server, impl


def build_mind(
    body: Any,
    *,
    username: str,
    perception: Perception | None = None,
    call_log: JsonlLog | None = None,
    clock: Callable[[], float] = time.monotonic,
    start_c1: bool = True,
    **executor_kwargs: Any,
) -> tuple[MCPServer, BodyMcp, Executor]:
    """Assemble the whole mind of one slot; tests use the impl and executor directly."""
    # runner_key is build_server's, not the Executor's: lift it out before wiring C1.
    runner_key = str(executor_kwargs.pop("runner_key", ""))
    executor = Executor(
        body,
        username=username,
        log_=call_log or JsonlLog(),
        perception=perception,
        clock=clock,
        **executor_kwargs,
    )
    impl = BodyMcp(executor, perception=perception, clock=clock)
    server, impl = build_server(impl, start_c1=start_c1, runner_key=runner_key)
    return server, impl, executor


def main() -> None:
    import uvicorn

    from runtime.config import env
    from runtime.game.py4j_body import Py4jBody
    from runtime.hive.client import HiveClient
    from runtime.ports import BirthRecord

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    port = int(env("MCP_BODY_PORT", default="8700") or "8700")
    username = env("MC_USERNAME", default="LastOrder01") or "LastOrder01"
    log_path = env("MCP_LOG_PATH", default="") or None

    def _env_float(name: str, default: float) -> float:
        raw = env(name, default=str(default))
        try:
            return float(raw)
        except ValueError:
            raise ValueError(f"{name} must be a number, got {raw!r}")

    tick = _env_float("MIND_TICK_S", 0.5)
    if not TICK_MIN_S <= tick <= TICK_MAX_S:
        raise ValueError(f"MIND_TICK_S must keep C1 within 1-4 Hz ({TICK_MIN_S}..{TICK_MAX_S} s), got {tick}")

    serial_raw = env("NODE_SERIAL", default="")
    birth: BirthRecord | None = None
    if serial_raw:
        serial = int(serial_raw)  # a bad value is a startup error, not a silent no-clock
        born_at = env("NODE_BORN_AT", default="")
        ttl_raw = env("NODE_TTL_MIN", default="")
        if not born_at or not ttl_raw:
            raise ValueError("NODE_SERIAL set: NODE_BORN_AT and NODE_TTL_MIN are required too")
        birth = BirthRecord(serial=serial, born_at=born_at, ttl_min=int(ttl_raw))
        log.info("life clock armed: serial %d, ttl %s min", serial, ttl_raw)

    # The hive client is NOT tied to a startup clock: compose arms the life clock through
    # POST /runner/birth, and retire_stuck must work in that mode too (review R2-3) —
    # before, a mind without NODE_SERIAL answered "no hive client configured". The client
    # carries the slot's runner key so /registry/request_death passes the hive gate.
    runner_key = env("MIND_RUNNER_KEY", default="") or ""
    if not runner_key and (env("NODE_DEV_OPEN", default="") or "") != "1":
        # fail-closed (review R2-6): an empty key would leave /runner/respawn and
        # /runner/birth ungated; NODE_DEV_OPEN=1 is the explicit dev exception
        raise ValueError(
            "MIND_RUNNER_KEY is empty: the /runner commands would be open."
            " Set the slot's runner key or NODE_DEV_OPEN=1 for a dev run."
        )
    hive = HiveClient(
        base_url=env("HIVE_URL", default="http://hive:8000") or "http://hive:8000",
        runner_key=runner_key,
    )

    call_log = JsonlLog(log_path)
    body = Py4jBody()
    # Jev (T-031, ADR-027): JEV_BACKEND=rules keeps C1 on its own rules; cloud/local wire
    # the decider with the mind's virtual key through the gateway. An unknown backend or a
    # non-number JEV_* is a startup error, not a silent fallback.
    jev = JevGate.from_env(log_=call_log)
    if jev.enabled:
        log.info("Jev wired: backend %s (threat tactics advise C1, rules stay the fallback)", jev.backend_name)
    server, _impl, _executor = build_mind(
        body,
        username=username,
        call_log=call_log,
        birth=birth,
        hive=hive,
        jev=jev,
        tick_interval=tick,
        flee_hp=_env_float("MIND_FLEE_HP", 6.0),
        fight_radius_m=_env_float("MIND_FIGHT_RADIUS_M", 3.0),
        threat_calm_s=_env_float("MIND_THREAT_CALM_S", 6.0),
        evade_m=_env_float("MIND_EVADE_M", 12.0),
        idle_s=_env_float("MIND_IDLE_S", 120.0),
        runner_key=env("MIND_RUNNER_KEY", default="") or "",
        operator_name=env("OPERATOR_MC_NAME", default="") or "",
    )
    # host="0.0.0.0" keeps the SDK's localhost-only DNS-rebinding protection off: the harness
    # reaches the port by service name (body-1:8700) inside the docker network, not via localhost.
    app = server.streamable_http_app(stateless_http=True, streamable_http_path="/mcp", host="0.0.0.0")
    log.info(
        "body MCP for %s on 0.0.0.0:%d/mcp (+/runner/*) log: %s", username, port, log_path or "stderr"
    )
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
