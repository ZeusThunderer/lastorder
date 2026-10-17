"""Py4jBody: the GameBody port (runtime/ports.py) on the unionclef py4j bridge.

One object per slot, one place for the E1 traps: commands carry the `@` prefix,
the gateway is closed with close() and never shutdown(), reconnects back off
exponentially and end in BodyUnavailable instead of looping, and chat keeps
uc's service lines flagged for perception to drop. Raw uc names pass through
untouched; translation is perception's job (ADR-020). Sources: the E1 probe
(tools/body_probe.py), docs/agents/unionclef-bridge.md, CLAUDE.md "Ловушки".
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from typing import Any

from py4j.java_gateway import CallbackServerParameters, GatewayParameters, JavaGateway
from py4j.protocol import Py4JNetworkError

from runtime.ports import BlockInfo, EntityInfo, Event, GameState, ItemStack

# uc pins the gateway port in altoclef_settings.json; if it is busy uc shifts by +2,
# and the mind cannot find it — so the compose stack never runs two bodies in one
# network namespace. The default GatewayParameters port matches 25333.
GATEWAY_PORT = 25333

_SERVICE_MARKERS = ("[Alto Clef]", "[Tungsten]")
_COLOR_CODE = re.compile("§.")


def is_service_line(text: str) -> bool:
    """uc prefixes its own bookkeeping with these markers, after the color codes."""
    stripped = _COLOR_CODE.sub("", text).lstrip()
    return stripped.startswith(_SERVICE_MARKERS)


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
        pass

    def agentCommandRequest(self, s=""):
        # Chat and voice are observation, never commands (ADR-012).
        return ""

    class Java:
        implements = ["adris.altoclef.PythonCallback"]


def _default_gateway_factory(callback: BodyCallback) -> Callable[[], JavaGateway]:
    def connect() -> JavaGateway:
        return JavaGateway(
            gateway_parameters=GatewayParameters(auto_convert=True),
            callback_server_parameters=CallbackServerParameters(),
            python_server_entry_point=callback,
            start_callback_server=True,
        )

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
            gw = self._connect()  # may raise Py4JNetworkError: same reconnect path
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

    def _call(self, fn: Callable[[Any], Any]) -> Any:
        """Run one uc call, reconnecting with backoff on Py4JNetworkError.

        The retry repeats the whole call: a command uc already accepted before the
        error can execute twice. Observation calls are idempotent; `act`, `command`
        and `respawn` are not, and their callers (C1/T-014) must tolerate a repeat.
        """
        attempt = 0
        while True:
            try:
                return fn(self._ensure())
            except Py4JNetworkError as exc:
                self._drop()
                if attempt >= self._retries:
                    raise BodyUnavailable(
                        f"body bridge unreachable after {attempt + 1} attempts: {exc}"
                    ) from exc
                self._sleep(min(self._base_delay * 2**attempt, self._max_delay))
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
        return self._call(lambda mc: handler(mc, dict(args or {})))

    def busy(self) -> bool:
        """Two engines can hold the body: an altoclef task and a tungsten fight
        (punk). A task with no fight and a fight with no task both read busy."""
        if self._call(lambda mc: bool(mc.hasActiveTask())):
            return True
        return bool(dict(self._call(lambda mc: mc.punkStatus())).get("active"))

    def stop(self) -> None:
        self._call(lambda mc: mc.stopPathing())
        self.command("stop")
        self._call(lambda mc: mc.punkStop())

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
