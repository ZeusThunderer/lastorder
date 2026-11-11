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
import json
import logging
import math
import re
import signal
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from mcp.types import CallToolResult, TextContent
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
from runtime.mind.login import SessionLogin
from runtime.perception.topics import TopicFilter
from runtime.perception.translate import Perception

log = logging.getLogger("mcp_body")

# --- limits (rate caps move to the gateway in T-026) --------------------------------------

SAY_MAX_LEN = 256  # vanilla rejects longer chat anyway
SAY_MIN_INTERVAL_S = 4.0  # one line per 4 s; no chatter director anymore (ADR-035)
# The absolute ceiling of one wait_events ask. The stand configures the real cap per
# harness through WAIT_EVENTS_MAX_S (T-054): zcode kills a tool call after 30 s, so
# compose keeps it below that with a margin; a larger ask clamps, it is not an error.
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
    # attack is uc's punkAny: it hunts PLAYERS, never mobs, and with no names it takes
    # the nearest player of all — a sister on the stand, a stranger on a public server
    # (k-0120, k-0149: two sisters killed that way). The names are the target (T-072).
    "attack": frozenset({"allow"}),
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
            if lever == "attack" and name == "allow" and not cleaned:
                _err("attack.allow: name the player(s) to attack — attack hunts players only; "
                     "for a mob use strike {pos}")
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
        _err("say: server commands are not available here — use server_command (ADR-043)")
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


# --- player commands to the server (ADR-043, T-053) ----------------------------------------

# The stand's deny list, always on top of whatever the operator configures: the
# login/password commands are the environment's (mind-N logs in itself), and a
# sister spending the slot's credential must stay impossible. `skin` likewise:
# the skin is the operator's pick, applied by the stand (T-061) — never a
# sister's own move on this or any server.
DEFAULT_DENY = ("login", "l", "register", "reg", "changepassword", "unregister", "skin")


def parse_deny(*values: str | None) -> tuple[str, ...]:
    """MC_COMMANDS_DENY / MC_COMMANDS_DENY_SLOT into names: comma or space
    separated, slashes optional, case lost on purpose."""
    names: list[str] = []
    for value in values:
        for token in re.split(r"[,\s]+", str(value or "")):
            token = token.strip().lstrip("/").lower()
            if token and token not in names:
                names.append(token)
    return tuple(names)


def _command_variants(name: str) -> set[str]:
    """`authme:login` answers as both the full form and the bare name, so the
    plugin-prefix disguise of a denied command does not slip through."""
    variants = {name}
    if ":" in name:
        variants.add(name.rsplit(":", 1)[-1])
    return variants


def check_server_command(command: Any, deny: tuple[str, ...] = DEFAULT_DENY) -> str:
    """Validate one player command (ADR-043): anything a player may type, minus the
    stand's deny list. The command rides the body's chat lever — MessageSender
    auto-detects the leading `/`, strips it and sends a command packet, never a
    chat line (unionclef MessageSender.java:104-116, verified live: `/me` executed
    with no raw slash line in the server log)."""
    if not isinstance(command, str):
        _err("server_command: command must be a string")
    cleaned = command.strip()
    if not cleaned:
        _err("server_command: empty command")
    if not cleaned.startswith("/"):
        _err("server_command: a server command starts with /")
    if not cleaned[1:].strip():
        _err("server_command: empty command name")
    if CONTROL_RE.search(cleaned):
        _err("server_command: control characters and line breaks are not allowed")
    if ";" in cleaned:
        _err("server_command: one command per call, ';' is not allowed")
    if "§" in cleaned:
        _err("server_command: formatting codes are not allowed")
    if len(cleaned) > SAY_MAX_LEN:
        _err(f"server_command: longer than {SAY_MAX_LEN} characters")
    name = cleaned[1:].split()[0].lower()
    # the login/password default can never be configured away: a stand's list only
    # ever ADDS names (ADR-043 — the operator may deny / wholesale, never open /login)
    denied = _command_variants(name) & (set(DEFAULT_DENY) | set(deny))
    if denied:
        _err(
            f"server_command: '{next(iter(sorted(denied)))}' is on this stand's deny list"
            " (ADR-043); the login commands are the environment's"
        )
    return cleaned


# --- the size of an answer (T-056) --------------------------------------------------------
# Every tool answer stays in the sister's context for the rest of the session, so its
# size is paid on every later request. The SDK prints a dict with indent=2; the answer
# goes out as compact JSON instead (the structured content is the same dict).

OBSERVE_RADIUS = 4
OBSERVE_POS_PER_NAME = 8  # nearest positions kept per block name; `count` keeps the total
OBSERVE_ENTITIES_MAX = 12  # nearest entities kept; `entities_more` counts the rest
ANSWER_MAX_CHARS = 6000  # an observe answer over this drops positions, then far block groups
# Fluids do not hide what is behind them: a block next to water is still in view.
_SEE_THROUGH = ("water", "lava")


def compact_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def compact_answer(result: Any) -> Any:
    """A dict answer as compact text plus the same structured content."""
    if not isinstance(result, dict):
        return result
    return CallToolResult(
        content=[TextContent(type="text", text=compact_json(result))], structured_content=result
    )


def _see_through(raw: str) -> bool:
    low = raw.lower()
    return any(word in low for word in _SEE_THROUGH)


def visible_blocks(
    blocks: Iterable[Any], center: tuple[int, int, int] | None, radius: int = OBSERVE_RADIUS
) -> tuple[list[Any], int]:
    """Blocks with at least one open side inside the scanned cube, and the number of
    buried ones. The body lists every non-air block of the cube; a side is open when
    the neighbour inside the cube is not listed (air) or is a fluid. A neighbour
    outside the cube is unknown and counts as closed; without a center nothing is
    dropped."""
    blocks = [b for b in blocks if b.pos]
    if center is None:
        return blocks, 0
    solid = {tuple(b.pos) for b in blocks if not _see_through(b.name)}
    lo = [c - radius for c in center]
    hi = [c + radius for c in center]
    shown, hidden = [], 0
    for b in blocks:
        x, y, z = b.pos
        open_side = False
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            n = (x + dx, y + dy, z + dz)
            if all(lo[i] <= n[i] <= hi[i] for i in range(3)) and n not in solid:
                open_side = True
                break
        if open_side:
            shown.append(b)
        else:
            hidden += 1
    return shown, hidden


def _dist2(pos: Iterable[float], origin: tuple[float, float, float] | None) -> float:
    if origin is None:
        return 0.0
    return sum((float(a) - float(b)) ** 2 for a, b in zip(pos, origin))


def group_blocks(
    named: Iterable[tuple[str, tuple[int, int, int]]],
    origin: tuple[float, float, float] | None,
    per_name: int = OBSERVE_POS_PER_NAME,
) -> list[dict[str, Any]]:
    """(name, pos) pairs as one entry per name: count, nearest positions first; the
    groups go nearest first."""
    groups: dict[str, list[tuple[int, int, int]]] = {}
    for name, pos in named:
        groups.setdefault(name, []).append(tuple(pos))
    out = []
    for name, cells in groups.items():
        cells.sort(key=lambda p: _dist2(p, origin))
        out.append({"name": name, "count": len(cells), "pos": [list(p) for p in cells[:per_name]]})
    out.sort(key=lambda g: _dist2(g["pos"][0], origin))
    return out


def fit_observe(answer: dict[str, Any], limit: int = ANSWER_MAX_CHARS) -> dict[str, Any]:
    """Keep an observe answer under `limit` chars: one position per block group, then
    drop the farthest groups (`blocks_more` counts them). self, inventory and chat stay."""
    if len(compact_json(answer)) <= limit:
        return answer
    for group in answer["blocks"]:
        del group["pos"][1:]
    dropped = 0
    while answer["blocks"] and len(compact_json(answer)) > limit:
        answer["blocks"].pop()
        dropped += 1
    if dropped:
        answer["blocks_more"] = dropped
    return answer


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
        deny_commands: tuple[str, ...] = DEFAULT_DENY,
        wait_max_s: float = WAIT_TIMEOUT_MAX_S,
    ):
        self._x = executor
        self._perception = perception or Perception(descriptions={}, salt="", mode="plain")
        self._clock = clock
        self._last_say_at = -1e9
        self._last_command_at = -1e9
        self._deny = tuple(deny_commands) or DEFAULT_DENY
        self._wait_max_s = float(wait_max_s)

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

    async def _reply(self, name: str, args: Mapping[str, Any], fn: Callable[[], Any]) -> Any:
        """A logged call whose dict answer goes out as compact JSON (T-056)."""
        return compact_answer(await self._tool(name, args, fn))

    def _seen(self, raw: str, kind: str, fallback: str | None = None) -> str:
        if kind == "entity":  # a name on a banned topic never reaches the agent (ADR-048)
            raw = self._x.masked_name(raw, fallback or "entity")
        return self._perception.see(raw, kind).render()

    # -- section 4 tools (sync bodies; the async wrappers in build_server add logging) ---

    def observe(self) -> dict[str, Any]:
        body = self._x.body
        with self._x.body_lock:  # one writer with C1 at a time (review P1-6)
            state = body.state()
            entities = tuple(body.entities(radius=16))
            blocks = tuple(body.blocks_around(radius=OBSERVE_RADIUS))
            inventory = tuple(body.inventory())
            chat = tuple(body.recent_chat(5))
        raw_pos = [float(c) for c in getattr(state, "pos", ()) or ()]
        origin = tuple(raw_pos) if len(raw_pos) == 3 else None
        center = tuple(math.floor(c) for c in raw_pos) if origin else None
        shown, hidden = visible_blocks(blocks, center)
        entities = tuple(sorted(entities, key=lambda e: float(e.dist_m)))
        answer: dict[str, Any] = {
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
                    "name": self._seen(e.name, "entity", str(e.kind)),
                    "dist_m": round(float(e.dist_m), 1),
                    "pos": list(e.pos) if e.pos else None,
                }
                for e in entities[:OBSERVE_ENTITIES_MAX]
            ],
            "blocks": group_blocks(((self._seen(b.name, "block"), b.pos) for b in shown), origin),
            "blocks_hidden": hidden,
            "inventory": [
                {"name": self._seen(i.name, "item"), "count": int(i.count), "slot": int(i.slot)}
                for i in inventory
            ],
            # the same entries `chat.line` events carry: `from` marks the speaker the
            # server's `<Name>` prefix names (`operator` when configured, T-051)
            "chat": [entry for entry in (self._x.chat_entry(line) for line in chat) if entry],
        }
        if len(entities) > OBSERVE_ENTITIES_MAX:
            answer["entities_more"] = len(entities) - OBSERVE_ENTITIES_MAX
        return fit_observe(answer)

    def act(self, intent: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
        cleaned = validate_args(intent, args)
        if intent == "attack":
            me = self._x.username.lower()
            if any(n.lower() == me for n in cleaned.get("allow", ())):
                raise ToolError("attack.allow names this body itself")
            # never her own body, whatever the list says
            cleaned["avoid"] = sorted({*cleaned.get("avoid", ()), self._x.username})
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

    def server_command(self, command: str) -> dict[str, Any]:
        """One player command to the server (ADR-043): anything a player may type,
        minus the stand's deny list; the server enforces the rights. The answer,
        if any, arrives as ordinary chat lines."""
        cleaned = check_server_command(command, self._deny)
        now = self._clock()
        if now - self._last_command_at < SAY_MIN_INTERVAL_S:
            _err(f"server_command: too fast, one per {SAY_MIN_INTERVAL_S:.0f} s")
        self._last_command_at = now
        with self._x.body_lock:
            self._x.body.act("chat", {"text": cleaned})
        return {"sent": True, "command": cleaned}

    def wait_events(self, timeout_s: float) -> dict[str, Any]:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            _err("wait_events: timeout_s must be a number")
        timeout_s = float(timeout_s)
        if timeout_s <= 0.0:
            _err(f"wait_events: timeout_s must be > 0 (the cap is {self._wait_max_s:.0f} s)")
        # A larger ask is not an error (T-054): the harness kills long tool calls
        # (zcode: 30 s), so the wait clamps to the configured cap and the answer
        # carries the applied value.
        applied = min(timeout_s, self._wait_max_s)
        event = self._x.events.pop(applied, self._clock)
        if event is None:
            return {"type": "timeout", "timeout_s": applied}
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
            "(player chat), server_command (a player command to the server, minus the "
            "stand's deny list), wait_events (sleep until an event or the timeout — you "
            "pick the rhythm), retire_stuck (only after a task.idle event). Names are plain "
            "game names; uc service lines are already filtered out."
        ),
    )

    async def observe() -> dict[str, Any]:
        """See the world through plain names: self (hp, food, pos, held...), the nearest
        entities, blocks in view within 4 m (one entry per name: count, nearest
        positions first; blocks_hidden counts the buried ones), inventory and the last
        filtered chat lines."""
        return await impl._reply("observe", {}, impl.observe)

    async def act(intent: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Give C1 the next intent. `intent` is EXACTLY one lever name: goto, goto_far,
        look_at, select_hotbar, place_block, bridge_to, pillar_to, tap_key, hold_key,
        mine, attack, attack_stop, strike, use_held, eat, get, equip. attack hunts PLAYERS
        by name until attack_stop ({"allow": ["Name"]}); a mob is hit with strike {"pos":
        [x, y, z]}. All parameters go
        in the `args` object, e.g. {"intent": "goto", "args": {"x": 5, "y": 64, "z": 5}},
        {"intent": "get", "args": {"item": "oak log", "count": 8}}. C1 executes it, may
        interrupt it for a threat and returns to it; you learn the outcome from
        intent.done / intent.failed events. Returns the intent id."""
        return await impl._reply("act", {"intent": intent, "args": dict(args or {})}, lambda: impl.act(intent, args))

    async def stop() -> dict[str, Any]:
        """Drop the current intent and stop the body."""
        return await impl._reply("stop", {}, impl.stop)

    async def say(text: str) -> dict[str, Any]:
        """One line into the player chat (no / or @, limited length and rate)."""
        return await impl._reply("say", {"text": text}, lambda: impl.say(text))

    async def server_command(command: str) -> dict[str, Any]:
        """One player command to the server, e.g. "/tpa LastOrder02" or "/sethome".
        Anything a player may type is allowed except this stand's deny list (the
        login/password commands: the environment logs in itself); the server
        enforces the rights. One command per call, no ';' or line breaks, same
        rate cap as say. The reply, if any, comes back as ordinary chat lines."""
        return await impl._reply(
            "server_command", {"command": command}, lambda: impl.server_command(command)
        )

    async def wait_events(timeout_s: float = 30.0) -> dict[str, Any]:
        return await impl._reply(
            "wait_events", {"timeout_s": timeout_s}, lambda: impl.wait_events(timeout_s)
        )

    # __doc__ is assigned, not an f-string literal: the SDK reads the description off
    # fn.__doc__, and the cap must name the configured limit (T-054).
    wait_events.__doc__ = (
        "Sleep until the next event or the timeout — you pick your own rhythm. Events: "
        "game.damage, game.death, chat.line, intent.done, intent.failed, task.idle (arms "
        "retire_stuck), life.expiring, board (fresh task-board lines). chat.line carries "
        "`lines`: {from: speaker, text} entries, the operator's with operator: true. "
        "Events of one type collapse (board and chat.line batches accumulate their "
        "lines); the queue is capped. The timeout is capped at "
        f"{impl._wait_max_s:.0f} s: a larger ask waits that long, and the answer "
        "carries the applied value."
    )

    async def retire_stuck() -> dict[str, Any]:
        """Eliminate yourself (request_death "stuck"). Works only right after a task.idle
        event; any new intent disarms it. Refused otherwise."""
        return await impl._reply("retire_stuck", {}, impl.retire_stuck)

    server.add_tool(observe, name="observe")
    server.add_tool(act, name="act")
    server.add_tool(stop, name="stop")
    server.add_tool(say, name="say")
    server.add_tool(server_command, name="server_command")
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
    deny_commands: tuple[str, ...] = DEFAULT_DENY,
    wait_max_s: float = WAIT_TIMEOUT_MAX_S,
    login: "SessionLogin | None" = None,
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
        login=login,
        **executor_kwargs,
    )
    impl = BodyMcp(
        executor,
        perception=perception,
        clock=clock,
        deny_commands=deny_commands,
        wait_max_s=wait_max_s,
    )
    server, impl = build_server(impl, start_c1=start_c1, runner_key=runner_key)
    return server, impl, executor


# --- serving and the SIGTERM path (T-055) --------------------------------------------------

# The teardown budget inside the mind: compose gives it stop_grace_period on top.
TEARDOWN_GRACE_S = 10.0


def stop_handler(stop: threading.Event) -> Callable[[int, Any], None]:
    """SIGTERM/SIGINT → one event set; the ordered teardown is `teardown_mind`."""

    def handler(signum: Any, frame: Any) -> None:
        stop.set()

    return handler


def teardown_mind(
    uvi: Any,
    thread: threading.Thread,
    executor: Executor,
    body: Any,
    grace_s: float = TEARDOWN_GRACE_S,
) -> None:
    """The SIGTERM path (T-055): stop the MCP server, stop C1, then close the
    bridge (close(), never shutdown() — the E1 trap). docker stop gives the mind
    its stop_grace_period and then SIGKILLs; a SIGKILLed python partner leaves
    uc's Java gateway dead forever (T-052 a), so the graceful close is what
    keeps a slot restart cheap. Idempotent on a second call."""
    uvi.should_exit = True
    thread.join(timeout=grace_s)
    executor.stop()
    body.close()
    log.info("mind stopped: server down, C1 stopped, bridge closed")


def serve_mind(
    app: Any,
    port: int,
    executor: Executor,
    body: Any,
    *,
    make_server: Callable[[], Any] | None = None,
) -> None:
    """Serve the MCP app until SIGTERM/SIGINT, then tear down in order.

    uvicorn runs in a thread: it installs its own signal handlers only in the
    main thread, so ours own SIGTERM here and drive the shutdown. A server
    that dies on its own (port taken, lifespan error) sys.exit's INSIDE the
    thread — swallowed there — so the non-zero exit is raised here, and
    compose's restart: on-failure brings the mind back (review R1-1)."""
    import uvicorn

    if make_server is None:
        def make_server() -> Any:
            return uvicorn.Server(
                uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
            )

    uvi = make_server()
    thread = threading.Thread(target=uvi.run, name="mind-mcp", daemon=True)
    thread.start()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop_handler(stop))
    try:
        while thread.is_alive():
            if stop.wait(0.5):
                break
    finally:
        teardown_mind(uvi, thread, executor, body)
    if not stop.is_set():
        # the server thread ended without a signal: the mind is down for a
        # reason, and docker must see a failure (not a clean stop)
        raise SystemExit(1)


def main() -> None:
    from runtime.config import env
    from runtime.hive.client import HiveClient
    from runtime.hive.infra import DEFAULT_BODY_KIND
    from runtime.mind.bodyctl import BodySupervisor, make_body
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

    # The wait_events cap of this stand (T-054): zcode kills a tool call after 30 s,
    # so compose pins it below that; a value above the absolute ceiling is a startup
    # error, not a silent lift.
    wait_max = _env_float("WAIT_EVENTS_MAX_S", WAIT_TIMEOUT_MAX_S)
    if not 0.0 < wait_max <= WAIT_TIMEOUT_MAX_S:
        raise ValueError(
            f"WAIT_EVENTS_MAX_S must be within 0 < t <= {WAIT_TIMEOUT_MAX_S:.0f} s, got {wait_max}"
        )

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
    # ADR-050: the body is the mind's — BODY_KIND picks the game's adapter here and
    # the body service on the executor's side; the supervisor below starts it
    body_kind = env("BODY_KIND", default=DEFAULT_BODY_KIND) or DEFAULT_BODY_KIND
    body = make_body(body_kind)
    # Jev (T-031, ADR-027): JEV_BACKEND=rules keeps C1 on its own rules; cloud/local wire
    # the decider with the mind's virtual key through the gateway. An unknown backend or a
    # non-number JEV_* is a startup error, not a silent fallback.
    jev = JevGate.from_env(log_=call_log)
    if jev.enabled:
        log.info("Jev wired: backend %s (threat tactics advise C1, rules stay the fallback)", jev.backend_name)
    # Player commands (ADR-043): the stand's deny lists — the common one plus this
    # slot's — always on top of the login/password default. The password (one
    # slot's value, injected by compose into this mind alone) never touches a log:
    # SessionLogin sends /login itself and masks any echo.
    # DEFAULT_DENY is not passed here: it is unremovable inside the check itself
    deny = parse_deny(
        env("MC_COMMANDS_DENY", default="") or "",
        env("MC_COMMANDS_DENY_SLOT", default="") or "",
    )
    password = env("MC_LOGIN_PASSWORD", default="") or ""
    # T-061: the skin command an external server expects after /login (compose
    # `MC_SKIN_COMMAND_N` → `MC_SKIN_COMMAND`); empty — the default — sends nothing.
    skin_command = env("MC_SKIN_COMMAND", default="") or ""
    login = (
        SessionLogin(
            password,
            send=lambda text: body.act("chat", {"text": text}),
            skin_command=skin_command,
        )
        if password
        else None
    )
    if password:
        log.info(
            "login armed: mind-N logs into the server itself (the password is never logged)%s",
            "; a skin command follows each login" if skin_command else "",
        )

    # Banned chat topics (ADR-048): the operator's file, mounted into this container
    # alone. Set but unreadable or broken — a startup error, never an open filter;
    # a later broken edit keeps the last good list and says so in the journal.
    topics_path = env("CHAT_TOPICS_PATH", default="") or ""
    topic_filter = None
    if topics_path:
        topic_filter = TopicFilter(
            path=topics_path,
            on_reload_error=lambda err: call_log.record("error", scope="chat_topics", error=err),
        )
        log.info(
            "chat topics armed: %d topics, %d patterns from %s",
            len(topic_filter.topics), topic_filter.patterns, topics_path,
        )
    else:
        log.warning("CHAT_TOPICS_PATH is empty: other players' chat reaches the agent unfiltered")

    server, _impl, _executor = build_mind(
        body,
        username=username,
        call_log=call_log,
        topic_filter=topic_filter,
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
        deny_commands=deny,
        wait_max_s=wait_max,
        login=login,
    )
    # The mind owns its body (ADR-050): it asks the executor for it at start and for a
    # new one when it dies. BODY_SUPERVISE=0 leaves the body to whoever raised it.
    if (env("BODY_SUPERVISE", default="1") or "1") != "0":
        BodySupervisor(body, hive, username=username, kind=body_kind, log=call_log).start()
        log.info("body supervisor armed: %s body of %s", body_kind, username)
    # host="0.0.0.0" keeps the SDK's localhost-only DNS-rebinding protection off: the harness
    # reaches the port by service name (mind-1:8700, ADR-050) inside the docker network, not via localhost.
    app = server.streamable_http_app(stateless_http=True, streamable_http_path="/mcp", host="0.0.0.0")
    log.info(
        "body MCP for %s on 0.0.0.0:%d/mcp (+/runner/*) log: %s", username, port, log_path or "stderr"
    )
    serve_mind(app, port, _executor, body)


if __name__ == "__main__":
    main()
