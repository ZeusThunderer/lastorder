"""Py4jBody: the GameBody port (runtime/ports.py) on the unionclef py4j bridge.

One object per slot, one place for the E1 traps: commands carry the `@` prefix,
the gateway is closed with close() and never shutdown(), reconnects back off
exponentially and end in BodyUnavailable instead of looping, and chat keeps
uc's service lines flagged for perception to drop. Raw uc names pass through
untouched; translation is perception's job (ADR-020). The one shared piece is
is_service_line, imported from runtime/perception/chat.py (T-009). Sources: the E1 probe
(tools/body_probe.py), docs/agents/unionclef-bridge.md, CLAUDE.md "Ловушки".
"""

from __future__ import annotations

import logging
import os
import socket
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from typing import Any

from py4j.java_gateway import (
    DEFAULT_ADDRESS,
    DEFAULT_PYTHON_PROXY_PORT,
    CallbackServerParameters,
    GatewayParameters,
    JavaGateway,
)
from py4j.protocol import Py4JError, Py4JNetworkError

from runtime.perception.chat import is_service_line
from runtime.ports import BlockInfo, EntityInfo, Event, GameState, ItemStack

log = logging.getLogger("mind.py4j")

# uc pins the gateway port in altoclef_settings.json; if it is busy uc shifts by +2,
# and the mind cannot find it — so the compose stack never runs two bodies in one
# network namespace. The default GatewayParameters port matches 25333.
GATEWAY_PORT = 25333


class BodyUnavailable(RuntimeError):
    """The bridge did not come back after all reconnect attempts."""


@dataclass(frozen=True)
class BodyState(GameState):
    """getGameState as the body sees it: the port fields plus what uc reports raw."""

    max_hp: float = 0.0
    armor: float = 0.0
    saturation: float = 0.0
    on_ground: bool = False
    dimension: str = ""
    yaw: float = 0.0
    pitch: float = 0.0
    vel: tuple[float, float, float] = ()

    @property
    def dead(self) -> bool:
        # Out of game the hp is unknown (0.0), and an absent body is not a corpse.
        return self.in_game and self.hp <= 0.0


class BodyCallback:
    """adris.altoclef.PythonCallback: uc pushes events from its own callback thread."""

    def __init__(self, sink: Callable[[str, str, bool], None]) -> None:
        self._sink = sink
        # ADR-055: a voice slot also hears — phrases of other players become voice.line
        from runtime.voice.listener import listener_from_env

        self._listener = listener_from_env(
            lambda speaker, text: self._sink("voice.line", f"<{speaker}> {text}", False)
        )

    def isStarted(self):
        return True

    def onChatMessage(self, s=""):
        self._sink("chat.line", s, is_service_line(s))
        return ""

    def onVerifedChat(self, m=None):
        return m

    def onUpdateServerInfo(self, m=None):
        self._sink("server.info", str(m), True)
        return m

    def onDeath(self, s=""):
        # Silent for mob/fall/lava deaths (killer undefined); the death path
        # watches hp = 0 plus the vanilla chat line instead (E1).
        self._sink("game.death", s, False)

    def onKill(self, s=""):
        self._sink("game.kill", s, False)

    def onAutoclefEvent(self, t="", s=""):
        self._sink("game.event", f"{t}: {s}", True)

    def onDamage(self, s=0.0):
        self._sink("game.damage", str(s), False)

    def onDamageConfirmed(self, damaged="", attacker="", amount=0.0):
        self._sink("game.damage_confirmed", f"{damaged} by {attacker}: {amount}", False)

    def onCaptchaSolveRequest(self, image_bytes=None):
        pass

    def onVoiceFeed(self, s="", audio=None):
        # Voice of another player heard through SVC (ADR-055, PCM16LE 48 kHz from uc)
        if self._listener is not None:
            self._listener.feed(str(s or ""), bytes(audio) if audio is not None else None)

    def agentCommandRequest(self, s=""):
        # Chat and voice are observation, never commands (ADR-012).
        return ""

    class Java:
        implements = ["adris.altoclef.PythonCallback"]


def _callback_port_free(port: int, address: str = DEFAULT_ADDRESS) -> bool:
    """True when the callback port can be taken. The probe binds with
    SO_REUSEADDR exactly like py4j's own callback server: TIME_WAIT leftovers
    of a dropped gateway (uc dials back on InitPythonCallback, python closes
    first) must not read as a squatter — without it the reconnect loop dead-
    ends for a minute after the first drop (seen live, T-055). A LIVE
    listener still blocks the bind."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind((address, port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def bridge_addresses(env: Mapping[str, str] | None = None) -> tuple[str, int, str, int]:
    """Where the mind finds uc and where uc finds the mind (ADR-050).

    The mind and its body are peer containers on the slot's private link: uc's
    gateway binds localhost inside the body, so the body relays BODY_HOST:BODY_PORT
    to it, and relays uc's localhost callback dial to this mind's CALLBACK_ADDRESS.
    The defaults are the old one-namespace layout (everything on 127.0.0.1)."""
    env = os.environ if env is None else env
    return (
        env.get("BODY_HOST") or DEFAULT_ADDRESS,
        int(env.get("BODY_PORT") or GATEWAY_PORT),
        env.get("CALLBACK_ADDRESS") or DEFAULT_ADDRESS,
        int(env.get("CALLBACK_PORT") or DEFAULT_PYTHON_PROXY_PORT),
    )


def _default_gateway_factory(callback: BodyCallback) -> Callable[[], JavaGateway]:
    body_host, body_port, cb_address, cb_port = bridge_addresses()

    def connect() -> JavaGateway:
        gw = JavaGateway(
            gateway_parameters=GatewayParameters(
                address=body_host, port=body_port, auto_convert=True
            ),
            # eager_load=False: explicit CallbackServerParameters default to
            # eager_load=True, and py4j then starts the callback server INSIDE this
            # constructor — before the probe below, on py4j's own failure path (the
            # one that SHUTDOWNs uc's gateway). Found in ADR-050's rework.
            callback_server_parameters=CallbackServerParameters(
                address=cb_address, port=cb_port, eager_load=False
            ),
            python_server_entry_point=callback,
            start_callback_server=False,  # started below, on our own failure path
        )
        # Bind-probe the callback port before py4j does (T-055, T-052 c): py4j's
        # own failed start calls gateway.shutdown(), which sends the SHUTDOWN
        # command to uc's server — the bridge then dies until a whole client
        # restart (CLAUDE.md trap) — and its cleanup crashes on the callback
        # thread being None. A busy port fails HERE, quietly, instead.
        if not _callback_port_free(cb_port, cb_address):
            gw.close()  # no callback server exists: closes the client sockets only
            raise Py4JNetworkError(
                f"callback port {cb_port} is busy (a live listener holds it)"
            )
        try:
            gw.start_callback_server()
        except Exception as exc:
            # The probe closes the common case (a live squatter); what remains
            # is the microsecond window where the port is taken between the
            # probe and this bind. There py4j's own cleanup DOES run and may
            # send SHUTDOWN to uc (the gateway then dies until a client
            # restart) — a known, accepted residue: starting the callback
            # server ourselves would mean driving py4j private API. Ours stays
            # quiet and reports the bridge failure (review R1-2).
            gw.close()
            raise Py4JNetworkError(f"callback server failed to start: {exc}") from exc
        return gw

    return connect


# --- lever table ---------------------------------------------------------------------
# The set C1 uses today (unionclef-bridge.md "Примитивы"); everything else uc can do
# goes through the `command` lever. A new lever is added here, not in callers.


def _lever_goto(mc, a):
    return mc.gotoXYZ(int(a["x"]), int(a["y"]), int(a["z"]))


def _lever_goto_far(mc, a):
    return mc.gotoFar(int(a["x"]), int(a["y"]), int(a["z"]), int(a.get("horizon", 64)))


def _lever_look_at(mc, a):
    return mc.lookAt(float(a["x"]), float(a["y"]), float(a["z"]))


def _lever_select_hotbar(mc, a):
    return mc.selectHotbar(int(a["slot"]))


def _lever_place_block(mc, a):
    return mc.placeBlockAt(int(a["x"]), int(a["y"]), int(a["z"]))


def _lever_bridge_to(mc, a):
    return mc.bridgeTo(int(a["x"]), int(a["y"]), int(a["z"]))


def _lever_pillar_to(mc, a):
    return mc.pillarTo(int(a["y"]))


def _lever_tap_key(mc, a):
    return mc.tapKey(str(a["key"]))


def _lever_hold_key(mc, a):
    return mc.holdKey(str(a["key"]), int(a.get("ms", 100)))


def _lever_chat(mc, a):
    # Text as-is: a caller that means a uc command says act("command", ...) or command().
    return mc.ChatMessage(str(a["text"]))


def _lever_command(mc, a):
    cmd = str(a["cmd"])
    if not cmd.startswith("@"):
        cmd = "@" + cmd
    return mc.ExecuteCommand(cmd)


def _lever_mine(mc, a):
    # {"pos": [x,y,z]} for one block or {"positions": [[x,y,z], ...]} for a patch;
    # uc's mineBlocks takes the list form and queues every cell into the break queue.
    cells = [list(a["pos"])] if "pos" in a else [list(p) for p in a.get("positions") or ()]
    queue = [[int(x), int(y), int(z)] for x, y, z in cells]
    return mc.mineBlocks(queue)


def _lever_attack(mc, a):
    # tungsten punkAny: allow/avoid are player NAME lists (lowercased inside uc);
    # mobs never match — PunkPlayerTask.isAcceptable takes PlayerEntity only.
    return mc.punkAny(list(a.get("allow") or ()), list(a.get("avoid") or ()))


def _lever_attack_stop(mc, a):
    return mc.punkStop()


def _lever_strike(mc, a):
    # One melee hit at whatever entity stands at pos: lookAt it, then a physical
    # left click, which vanilla routes to the entity under the crosshair. The
    # mob-combat path — punkAny and every name-based lever in uc hit players only.
    x, y, z = (float(c) for c in a["pos"])
    mc.lookAt(x, y, z)
    return mc.mouseClick("left")


def _lever_use_held(mc, a):
    return mc.useHeldItem()


def _lever_eat(mc, a):
    # Eating needs the use key HELD (32 game ticks): one useHeldItem() is a single
    # interaction and leaves food and count untouched (stand, review R2-4).
    mc.selectHotbar(int(a["slot"]))
    return mc.holdKey("use", int(a.get("ms", 1700)))


def _lever_get(mc, a):
    return mc.ExecuteCommand(f"@get {a['item']} {int(a.get('count', 1))}")


def _lever_equip(mc, a):
    return mc.ExecuteCommand(f"@equip {a['item']}")


def _lever_build_blocks(mc, a):
    # [[x, y, z, raw_id], ...] absolute cells (runtime/mind/build.py makes them). uc starts
    # a NEW batch: the previous one is dropped, its counters reset; cells already solid are
    # skipped, so the same call after an interruption resumes the build.
    cells = [[int(x), int(y), int(z), str(name)] for x, y, z, name in a["blocks"]]
    return mc.buildBlocks(cells)


def _lever_destroy_block(mc, a):
    # altoclef's own DestroyBlockTask: walks up, picks a tool, mines one cell (a build's
    # site clearing); a user task, so busy() reads it through hasActiveTask
    return mc.destroyBlockAt(int(a["x"]), int(a["y"]), int(a["z"]))


def _protect_box(mc, box):
    # uc avoidBreakingBox (x1,y1,z1,x2,y2,z2): one replaceable slot, released by
    # allowBreakingAnywhere. A uc without it (<= 0.95.66) gets the cube over the box.
    x0, y0, z0, x1, y1, z1 = box
    try:
        return mc.avoidBreakingBox(x0, y0, z0, x1, y1, z1)
    except Py4JNetworkError:
        raise
    except Py4JError:
        cx, cy, cz = (x0 + x1) // 2, (y0 + y1) // 2, (z0 + z1) // 2
        r = max(cx - x0, x1 - cx, cy - y0, y1 - cy, cz - z0, z1 - cz)
        return mc.avoidBreakingRegion(cx, cy, cz, r)


LEVERS: Mapping[str, Callable[[Any, dict[str, Any]], Any]] = {
    "goto": _lever_goto,
    "goto_far": _lever_goto_far,
    "look_at": _lever_look_at,
    "select_hotbar": _lever_select_hotbar,
    "place_block": _lever_place_block,
    "bridge_to": _lever_bridge_to,
    "pillar_to": _lever_pillar_to,
    "tap_key": _lever_tap_key,
    "hold_key": _lever_hold_key,
    "chat": _lever_chat,
    "command": _lever_command,
    "mine": _lever_mine,
    "attack": _lever_attack,
    "attack_stop": _lever_attack_stop,
    "strike": _lever_strike,
    "use_held": _lever_use_held,
    "eat": _lever_eat,
    "get": _lever_get,
    "equip": _lever_equip,
    "build_blocks": _lever_build_blocks,
    "destroy_block": _lever_destroy_block,
}


# --- parsing (pure; shapes fixed by Py4jEntryPoint.java and the E1 run) ---------------


def _parse_pos(value: Any) -> tuple[float, float, float]:
    if not value:
        return (0.0, 0.0, 0.0)
    try:
        x, y, z = str(value).replace(" ", "").split(",")
        return (float(x), float(y), float(z))
    except ValueError:
        return (0.0, 0.0, 0.0)


def _parse_cell_pos(value: Any) -> tuple[int, int, int] | None:
    """A cell coordinate from getEntitiesInfo/getBlocksAround ("x, y, z").

    uc formats both with %.0f / block ints, so a whole tuple fits. A broken or
    missing string is an observation gap, not an exception: None, and the caller
    falls back to name+distance.
    """
    if not value:
        return None
    try:
        x, y, z = str(value).replace(" ", "").split(",")
        return (int(float(x)), int(float(y)), int(float(z)))
    except ValueError:
        return None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_game_state(raw: Mapping[str, Any]) -> BodyState:
    in_game = bool(raw.get("inGame"))
    me = dict(raw.get("self") or {})
    return BodyState(
        in_game=in_game,
        hp=_to_float(me.get("hp")),
        food=_to_float(me.get("food")),
        pos=_parse_pos(me.get("pos")),
        held=str(me.get("held", "")),
        looking_at=str(me.get("lookingAt", "")),
        max_hp=_to_float(me.get("maxHp")),
        armor=_to_float(me.get("armor")),
        saturation=_to_float(me.get("saturation")),
        on_ground=bool(me.get("onGround", False)),
        dimension=str(me.get("dimension", "")),
        yaw=_to_float(me.get("yaw")),
        pitch=_to_float(me.get("pitch")),
        vel=_parse_pos(me.get("vel")),
    )


class Py4jBody:
    """The GameBody port on the uc bridge. One per slot, the mind's only door to the game.

    gateway_factory, clock and sleep are injectable so tests run without Minecraft;
    every uc call goes through _call, which reconnects with a growing delay on
    Py4JNetworkError and raises BodyUnavailable after `retries` failed reconnects.
    """

    def __init__(
        self,
        gateway_factory: Callable[[], Any] | None = None,
        *,
        retries: int = 5,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        event_history: int = 1000,
    ) -> None:
        self._events: deque[Event] = deque(maxlen=event_history)
        self._ids = count(1)
        self._clock = clock
        self._sleep = sleep
        self._retries = retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._gateway: Any = None
        self._mc: Any = None
        self.callback = BodyCallback(self._emit)
        if gateway_factory is None:
            gateway_factory = _default_gateway_factory(self.callback)
        self._connect = gateway_factory
        # when the last uc call came back (clock()); None until the first one — the
        # mind's body supervisor reads it to tell "not up yet" from "gone" (ADR-050)
        self.last_ok: float | None = None
        # the reply of the last build_blocks lever; empty until a build ever ran, so the
        # build queue stays out of every busy()/stop() of a body that never builds
        self._last_build: dict[str, Any] = {}
        # the box a build's dig steps keep from being broken through (see act); None when
        # nothing is protected
        self._protected: tuple[int, ...] | None = None

    # -- connection ----------------------------------------------------------------

    def _init_callbacks(self, mc: Any) -> bool:
        try:
            mc.InitPythonCallback()
            return True
        except Py4JNetworkError:
            raise  # the bridge is gone: let _call reconnect
        except Exception:
            return False  # uc not ready yet; bind_callbacks() retries

    def _ensure(self) -> Any:
        if self._mc is None:
            try:
                gw = self._connect()  # may raise Py4JNetworkError: same reconnect path
            except Py4JNetworkError:
                raise
            except Exception as exc:
                # py4j 0.10.9.9 can surface a failed callback start as an
                # AttributeError ('NoneType' object has no attribute 'join',
                # T-052 c): a bridge failure, not a caller bug — route it into
                # the same reconnect path instead of crashing the tick.
                raise Py4JNetworkError(
                    f"gateway factory failed: {type(exc).__name__}: {exc}"
                ) from exc
            self._gateway = gw
            self._mc = gw.entry_point
            self._init_callbacks(self._mc)
        return self._mc

    def _drop(self) -> None:
        gw, self._gateway, self._mc = self._gateway, None, None
        if gw is None:
            return
        # close(), never shutdown(): shutdown() also stops uc's own GatewayServer,
        # and the port comes back only after the whole client restarts (E1).
        for teardown in (gw.close, gw.shutdown_callback_server):
            try:
                teardown()
            except Exception:
                pass  # a half-dead gateway must not break teardown
        # py4j 0.10.9.9: when a callback server never started (or died early),
        # its shutdown() dies on the thread being None without closing the
        # listening socket (T-052 c) — close the socket directly; quiet and
        # repeatable, so a second _drop is a no-op.
        try:
            server = gw.get_callback_server()
        except Exception:
            server = None
        sock = getattr(server, "server_socket", None)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _call(self, fn: Callable[[Any], Any]) -> Any:
        """Run one uc call, reconnecting with backoff on Py4JNetworkError.

        The retry repeats the whole call: a command uc already accepted before the
        error can execute twice. Observation calls are idempotent; `act`, `command`
        and `respawn` are not, and their callers (C1/T-014) must tolerate a repeat.
        """
        attempt = 0
        while True:
            try:
                result = fn(self._ensure())
                self.last_ok = self._clock()  # the mind's body watch (ADR-050, bodyctl)
                return result
            except Py4JNetworkError as exc:
                self._drop()
                if attempt >= self._retries:
                    raise BodyUnavailable(
                        f"body bridge unreachable after {attempt + 1} attempts: {exc}"
                    ) from exc
                pause = min(self._base_delay * 2**attempt, self._max_delay)
                log.warning(
                    "bridge reconnect attempt %d in %.1fs: %s", attempt + 1, pause, exc
                )
                self._sleep(pause)
                attempt += 1

    def bind_callbacks(self) -> bool:
        """(Re)bind the callback server inside uc; repeatable, safe before in-game."""
        return self._init_callbacks(self._ensure())

    def close(self) -> None:
        """Tear the bridge down. Never calls shutdown(): see _drop."""
        self._drop()

    def wait_in_game(self, timeout: float = 600.0, poll: float = 5.0) -> BodyState:
        """Wait for the body to join the server (uc autoConnect), then return state.

        A freshly started client opens its py4j gateway only after a minute or more,
        so BodyUnavailable from a not-yet-up bridge is not an error here: keep polling
        until the outer timeout, like the E1 probe did.
        """
        deadline = self._clock() + timeout
        while True:
            try:
                if self._call(lambda mc: bool(mc.inGame())):
                    return self.state()
            except BodyUnavailable:
                pass
            if self._clock() >= deadline:
                raise BodyUnavailable(f"body did not enter the game within {timeout:.0f}s")
            self._sleep(poll)

    # -- events --------------------------------------------------------------------

    def _emit(self, type_: str, text: str, service: bool) -> None:
        self._events.append(
            Event(
                id=f"{type_}#{next(self._ids)}",
                ts_mono=self._clock(),
                ts_wall=datetime.now().isoformat(timespec="seconds"),
                source="body",
                type=type_,
                payload={"text": text, "service": service},
            )
        )

    def events(self) -> tuple[Event, ...]:
        """Callback queue since start, newest last; nothing is deleted here."""
        return tuple(self._events)

    # -- observation (GameBody port) ------------------------------------------------

    def state(self) -> BodyState:
        return parse_game_state(dict(self._call(lambda mc: mc.getGameState())))

    def player_distances(self) -> dict[str, float]:
        return {str(row["name"]): float(row["distance"])
                for row in self._call(lambda mc: mc.getPlayersInfo(32))
                if row.get("name") and row.get("distance") is not None}

    def entities(self, radius: int = 16, limit: int = 32) -> tuple[EntityInfo, ...]:
        """Entities within `radius` metres.

        The T-006 defect fixed here: getEntitiesInfo(int) caps the COUNT, it is not
        a radius, so the limit (uc's own arg) and the distance filter are separate —
        uc hands back the nearest `limit`, Python drops the ones beyond `radius`.
        """
        raw = self._call(lambda mc: mc.getEntitiesInfo(limit))
        out = []
        for e in raw:
            e = dict(e)
            kind = str(e.get("type", "unknown"))
            dist = _to_float(e.get("distance"))
            if dist > radius:
                continue
            out.append(
                EntityInfo(
                    kind=kind,
                    name=str(e.get("name") or kind),
                    dist_m=dist,
                    pos=_parse_cell_pos(e.get("position")),
                )
            )
        return tuple(out)

    def blocks_around(self, radius: int = 4) -> tuple[BlockInfo, ...]:
        raw = self._call(lambda mc: mc.getBlocksAround(radius))
        return tuple(
            BlockInfo(name=str(dict(b).get("block", "")), pos=_parse_cell_pos(dict(b).get("position")))
            for b in raw
        )

    def inventory(self) -> tuple[ItemStack, ...]:
        raw = dict(self._call(lambda mc: mc.getInventoryFull()))
        out = []
        for slot in raw.get("slots") or ():
            slot = dict(slot)
            if slot.get("empty") or not slot.get("item"):
                continue
            out.append(
                ItemStack(
                    name=str(slot["item"]),
                    count=int(slot.get("count", 1)),
                    slot=int(slot.get("index", -1)),
                )
            )
        return tuple(out)

    def recent_chat(self, lines: int = 5) -> tuple[str, ...]:
        return tuple(str(s) for s in self._call(lambda mc: mc.getRecentChat(lines)))

    def server(self) -> dict[str, str]:
        """The info dict uc keeps via onUpdateServerInfo (ARCHITECTURE 5.3)."""
        info = self._call(lambda mc: mc.getServerInfoDict())
        return {str(k): str(v) for k, v in dict(info).items()}

    # -- action (GameBody port) ----------------------------------------------------

    def command(self, cmd: str) -> None:
        """Run an altoclef command. uc drops unprefixed commands silently, so the
        prefix is added here rather than trusted to every caller."""
        if not cmd.startswith("@"):
            cmd = "@" + cmd
        self._call(lambda mc: mc.ExecuteCommand(cmd))

    def act(self, lever: str, args: Mapping[str, Any] | None = None) -> Any:
        """Pull one lever from LEVERS; returns uc's raw reply for the smoke tests.

        A reconnect retries the lever (see _call): a lever uc already accepted may
        fire twice, so levers must stay tolerable to repeat. And on a dead body uc
        answers ok: True while no task starts — the caller watches state().dead
        first (a note carried to C1/T-014).
        """
        handler = LEVERS.get(lever)
        if handler is None:
            raise ValueError(f"unknown lever: {lever!r}")
        # A build digs its site with "protect": its own box. While the steps run, uc breaks
        # nothing inside the box that only stands in the way (the target itself is still dug):
        # unprotected, the miner cut through the house wall and the window to reach a stray
        # block inside (T-076 uc-plan.md, run B against run C). Any other lever ends it.
        protect = (args or {}).get("protect") if lever == "destroy_block" else None
        if protect:
            box = tuple(int(c) for c in protect)
            if box != self._protected:
                self._call(lambda mc: _protect_box(mc, box))
                self._protected = box
        elif self._protected is not None:
            self._release_protection()
        result = self._call(lambda mc: handler(mc, dict(args or {})))
        if lever == "build_blocks":
            # the cells skipped as already solid are known only from this reply
            self._last_build = dict(result) if result is not None else {}
        return result

    def busy(self) -> bool:
        """Two engines can hold the body: an altoclef task and a tungsten fight
        (punk). A task with no fight and a fight with no task both read busy."""
        if self._call(lambda mc: bool(mc.hasActiveTask())):
            return True
        if bool(dict(self._call(lambda mc: mc.punkStatus())).get("active")):
            return True
        # the third engine: uc's tick-driven build queue (buildBlocks) runs with no task
        return bool(self._last_build) and int(
            dict(self._call(lambda mc: mc.buildQueue())).get("queued", 0) or 0
        ) > 0

    def stop(self) -> None:
        self._call(lambda mc: mc.stopPathing())
        self.command("stop")
        self._call(lambda mc: mc.punkStop())
        if self._last_build:
            self._call(lambda mc: mc.buildQueueClear())
        if self._protected is not None:
            self._release_protection()

    def _release_protection(self) -> None:
        self._call(lambda mc: mc.allowBreakingAnywhere())
        self._protected = None

    def block_at(self, x: int, y: int, z: int) -> dict[str, Any]:
        """One cell of the loaded world: {"id": "oak_log", "air": False, "replaceable":
        False} (uc getBlockAt). An unloaded or failed read raises."""
        m = dict(self._call(lambda mc: mc.getBlockAt(int(x), int(y), int(z))))
        if m.get("error"):
            raise RuntimeError(f"getBlockAt {x},{y},{z}: {m['error']}")
        raw = str(m.get("id") or m.get("block") or "")
        raw = raw.split(":", 1)[1] if raw.startswith("minecraft:") else raw
        air = str(m.get("is_air", "")).lower() == "true"
        return {
            "id": "air" if air else raw,
            "air": air,
            "replaceable": str(m.get("replaceable", "")).lower() == "true",
        }

    def inventory_slots(self) -> dict[int, tuple[str, int]]:
        """Player inventory by slot: 0-8 hotbar, 9-35 main -> (raw id, count)."""
        inv = dict(self._call(lambda mc: mc.getInventoryFull()))
        out: dict[int, tuple[str, int]] = {}
        for raw in inv.get("slots") or ():
            s = dict(raw)
            index = int(s.get("index", -1))
            if 0 <= index <= 35 and not s.get("empty"):
                out[index] = (str(s.get("item", "")).split(":")[-1], int(s.get("count", 0)))
        return out

    def restock(self, needs: Mapping[str, int]) -> dict[str, Any]:
        """Bring the blocks a build needs from the main inventory into the hotbar (uc's
        builder takes from slots 0-8 only): vanilla's hotbar swap, one click per stack.
        First every needed block gets one hotbar slot — uc places whatever is in hand
        when the named block is not in the hotbar (a window of planks) — then free slots
        take more stacks of the biggest shortfalls. A slot is free when it is empty,
        holds something the build does not use, or holds a second stack of a block."""
        slots = self.inventory_slots()
        keep: set[int] = set()
        for item in needs:  # one stack of each needed block already up there stays
            have = [i for i in range(9) if slots.get(i, ("", 0))[0] == item]
            if have:
                keep.add(max(have, key=lambda i: slots[i][1]))

        def hot(item: str) -> int:
            return sum(c for i, (it, c) in slots.items() if i < 9 and it == item)

        def free_slot() -> int | None:
            free = [i for i in range(9) if i not in keep]
            for good in (lambda i: i not in slots,
                         lambda i: slots[i][0] not in needs,
                         lambda i: True):
                pick = next((i for i in free if good(i)), None)
                if pick is not None:
                    return pick
            return None

        def swap_in(item: str) -> bool:
            source = next((i for i in range(9, 36) if slots.get(i, ("", 0))[0] == item), None)
            target = free_slot()
            if source is None or target is None:
                return False
            self._call(lambda mc, s=source, t=target: mc.clickUiSlot(s, t, "SWAP"))
            moved, back = slots.get(source), slots.get(target)
            slots[target] = moved
            if back is None:
                slots.pop(source, None)
            else:
                slots[source] = back
            keep.add(target)
            self._sleep(0.15)  # the slot handler paces clicks
            return True

        swaps = 0
        for item, _ in sorted(needs.items(), key=lambda kv: -kv[1]):
            if not hot(item) and swap_in(item):
                swaps += 1
        while True:
            short = sorted(((need - hot(item), item) for item, need in needs.items()
                            if hot(item) < need
                            and any(slots.get(i, ("", 0))[0] == item for i in range(9, 36))),
                           reverse=True)
            if not short or not swap_in(short[0][1]):
                break
            swaps += 1
        hotbar: dict[str, int] = {}
        for i in range(9):
            if i in slots:
                hotbar[slots[i][0]] = hotbar.get(slots[i][0], 0) + slots[i][1]
        return {"swaps": swaps, "hotbar": hotbar}

    def build_status(self) -> dict[str, Any]:
        """The last build_blocks batch: placed by the body, already solid at the start,
        still queued, handed back and why (uc buildQueue). uc's own `alreadyFilled`
        also counts the cells the queue just placed, so it is not reported."""
        q = dict(self._call(lambda mc: mc.buildQueue()))
        start = self._last_build or {}
        return {
            "queued": int(q.get("queued", 0) or 0),
            "placed": int(q.get("placed", 0) or 0),
            "already": int(start.get("already", 0) or 0),
            "deferred": int(q.get("deferredCount", 0) or 0),
            "reasons": {
                "no_material": int(q.get("deferNoMaterial", 0) or 0),
                "no_face": int(q.get("deferNoFace", 0) or 0),
                "no_support": int(q.get("deferNoSupport", 0) or 0),
                "protected": int(q.get("deferProtected", 0) or 0),
                "timeout": int(q.get("deferTimeout", 0) or 0),
            },
            "deferred_cells": list(q.get("deferred") or [])[:16],
        }

    def respawn(self, timeout: float = 60.0, poll: float = 1.0) -> BodyState:
        """respawnPlayer() and wait for a living body: uc does not auto-respawn
        while no task is running (E1), so the respawn is the mind's move."""
        self._call(lambda mc: mc.respawnPlayer())
        deadline = self._clock() + timeout
        while True:
            st = self.state()
            if st.in_game and st.hp > 0:
                return st
            if self._clock() >= deadline:
                raise BodyUnavailable(f"no living body within {timeout:.0f}s after respawn")
            self._sleep(poll)
