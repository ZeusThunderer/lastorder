"""Py4jBody against a fake gateway: the E1 traps closed in one place, no Minecraft.

Raw shapes below mirror Py4jEntryPoint.java (getGameState/getEntitiesInfo/
getBlocksAround/getInventoryFull) as they came back in the E1 run; no raw E1
transcript is stored in the repo, so these fixtures are the E1-verified shapes.
"""

from __future__ import annotations

import pytest
from py4j.protocol import Py4JNetworkError

from runtime import ports
from runtime.game import BodyState, BodyUnavailable, Py4jBody, is_service_line
from runtime.game.py4j_body import parse_game_state

E1_GAME_STATE = {
    "inGame": True,
    "self": {
        "hp": 16.0,
        "maxHp": 20.0,
        "armor": 2.0,
        "food": 18,
        "saturation": 0.6,
        "pos": "107.5,71.0,-12.3",
        "onGround": True,
        "dimension": "minecraft:overworld",
        "held": "minecraft:birch_log",
        "blocks": 12,
        "yaw": -17.3,
        "pitch": 6.7,
        "vel": "0.00,-0.08,0.00",
        "lookingAt": "grass_block@105,70,-14",
        "msSinceBreakProgress": 1523,
    },
    "players": [],
    "playerCount": 0,
    "beds": [],
}
DEAD_GAME_STATE = {"inGame": True, "self": {**E1_GAME_STATE["self"], "hp": 0.0}}
NOT_IN_GAME_STATE = {"inGame": False}

E1_ENTITIES = [
    {
        "type": "zombie",
        "distance": "8.5",
        "position": "100, 70, -10",
        "is_item": "false",
        "is_hostile": "true",
        "is_mob": "true",
        "health": "20",
        "name": "Zombie",
    },
    # item entities carry no display name: the kind stands in
    {"type": "item", "distance": "1.2", "position": "108, 71, -13", "is_item": "true",
     "item": "birch_log", "count": "2"},
]

E1_BLOCKS = [
    {"position": "106, 70, -13", "block": "grass block", "hardness": "0.60"},
    {"position": "107, 71, -14", "block": "birch leaves", "hardness": "0.20"},
]

E1_INVENTORY = {
    "ok": True,
    "slots": [
        {"index": 0, "empty": True},
        {"index": 1, "empty": False, "item": "minecraft:birch_log", "name": "Birch Log", "count": 12},
        {"index": 2, "empty": False, "item": "minecraft:dirt", "name": "Dirt", "count": 3},
    ],
    "selectedHotbar": 1,
    "heldItem": {"index": 1, "empty": False, "item": "minecraft:birch_log", "name": "Birch Log", "count": 12},
}


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeEntryPoint:
    """Records everything; getGameState plays a script and sticks to the last frame."""

    def __init__(self, states: list | None = None):
        self.log: list[tuple] = []
        self.states = list(states or [E1_GAME_STATE])
        self.busy = False
        self.chat = [
            "LastOrder01 joined the game",
            "§2§l§o[Alto Clef] Task started",
            "LastOrder01 was slain by Zombie",
        ]
        self.raise_on_get_state: Exception | None = None

    def _rec(self, name, *args):
        self.log.append((name, *args))

    def inGame(self):
        self._rec("inGame")
        return bool(self.states[-1].get("inGame"))

    def InitPythonCallback(self):
        self._rec("InitPythonCallback")
        return None

    def getGameState(self):
        self._rec("getGameState")
        if self.raise_on_get_state is not None:
            raise self.raise_on_get_state
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    def getEntitiesInfo(self, limit):
        self._rec("getEntitiesInfo", limit)
        return E1_ENTITIES[:limit]

    def getBlocksAround(self, radius):
        self._rec("getBlocksAround", radius)
        return E1_BLOCKS

    def getInventoryFull(self):
        self._rec("getInventoryFull")
        return E1_INVENTORY

    def getRecentChat(self, n):
        self._rec("getRecentChat", n)
        return self.chat[-n:]

    def getServerInfoDict(self):
        self._rec("getServerInfoDict")
        return {"server": "mc-server"}

    def hasActiveTask(self):
        self._rec("hasActiveTask")
        return self.busy

    def ExecuteCommand(self, cmd):
        self._rec("ExecuteCommand", cmd)
        return None

    def respawnPlayer(self):
        self._rec("respawnPlayer")
        return True

    def stopPathing(self):
        self._rec("stopPathing")
        return {"ok": True}

    def gotoXYZ(self, x, y, z):
        self._rec("gotoXYZ", x, y, z)
        return {"goal": f"{x},{y},{z}", "ok": True}

    def ChatMessage(self, msg):
        self._rec("ChatMessage", msg)
        return None


class FakeGateway:
    def __init__(self, entry_point):
        self.entry_point = entry_point
        self.calls: list[str] = []

    def close(self):
        self.calls.append("close")

    def shutdown_callback_server(self):
        self.calls.append("shutdown_callback_server")

    def shutdown(self):  # must never be called: it stops uc's server until client restart
        self.calls.append("shutdown")


def make_body(entry_point=None, **kwargs):
    ep = entry_point or FakeEntryPoint()
    gateways: list[FakeGateway] = []

    def factory():
        gw = FakeGateway(ep)
        gateways.append(gw)
        return gw

    clock = FakeClock()
    body = Py4jBody(factory, clock=clock, sleep=clock.sleep, **kwargs)
    return body, ep, gateways, clock


# --- the port ------------------------------------------------------------------------


def test_py4j_body_satisfies_the_game_body_port():
    body, *_ = make_body()
    assert isinstance(body, ports.GameBody)


# --- command prefix ------------------------------------------------------------------


def test_command_adds_the_at_prefix():
    body, ep, _, _ = make_body()
    body.command("get log 1")
    assert ("ExecuteCommand", "@get log 1") in ep.log


def test_command_keeps_the_existing_prefix():
    body, ep, _, _ = make_body()
    body.command("@get log 1")
    assert ep.log.count(("ExecuteCommand", "@get log 1")) == 1


# --- close -------------------------------------------------------------------------


def test_close_never_calls_shutdown():
    body, ep, gws, _ = make_body()
    body.state()  # connect first
    body.close()
    assert gws[0].calls == ["close", "shutdown_callback_server"]
    assert "shutdown" not in gws[0].calls


def test_close_without_a_connection_never_dials():
    body, ep, gws, _ = make_body()
    body.close()
    assert gws == []


# --- reconnect ---------------------------------------------------------------------


def test_reconnect_backoff_grows_and_ends_in_body_unavailable():
    ep = FakeEntryPoint()
    ep.raise_on_get_state = Py4JNetworkError("socket down")
    body, ep, gws, clock = make_body(ep, retries=3, base_delay=0.5, max_delay=2.0)

    with pytest.raises(BodyUnavailable):
        body.state()

    # 1 first try + 3 reconnects, a pause between each pair, delays doubling to the cap
    assert clock.sleeps == [0.5, 1.0, 2.0]
    assert len(gws) == 4
    for gw in gws:
        assert gw.calls == ["close", "shutdown_callback_server"]


def test_reconnect_recovers_after_a_transient_network_error():
    ep = FakeEntryPoint()
    original = ep.getGameState
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Py4JNetworkError("blip")
        return original()

    ep.getGameState = flaky
    body, ep2, gws, clock = make_body(ep, retries=3)

    st = body.state()

    assert st.hp == pytest.approx(16.0)
    assert len(gws) == 2  # one drop, one reconnect
    assert clock.sleeps == [0.5]


# --- state -------------------------------------------------------------------------


def test_state_parses_the_e1_sample():
    st = parse_game_state(E1_GAME_STATE)
    assert isinstance(st, ports.GameState)
    assert st.in_game is True
    assert st.hp == pytest.approx(16.0)
    assert st.food == pytest.approx(18.0)
    assert st.pos == (107.5, 71.0, -12.3)
    assert st.held == "minecraft:birch_log"
    assert st.looking_at == "grass_block@105,70,-14"
    assert st.on_ground is True
    assert st.dimension == "minecraft:overworld"
    assert st.vel == (0.0, -0.08, 0.0)
    assert st.dead is False


def test_state_dead_when_hp_is_zero():
    st = parse_game_state(DEAD_GAME_STATE)
    assert st.in_game and st.hp <= 0
    assert st.dead is True


def test_state_out_of_game_is_not_dead():
    st = parse_game_state(NOT_IN_GAME_STATE)
    assert st.in_game is False
    assert st.hp == 0.0
    assert st.dead is False  # hp is unknown out of game, and that is not a corpse


def test_body_state_is_a_game_state_with_the_port_fields():
    st = BodyState(in_game=True, hp=1.0, food=2, pos=(1, 2, 3), held="x", looking_at="y")
    assert st.dead is False


# --- respawn -----------------------------------------------------------------------


def test_respawn_calls_respawn_player_and_waits_for_hp():
    ep = FakeEntryPoint(states=[DEAD_GAME_STATE, DEAD_GAME_STATE, E1_GAME_STATE])
    body, ep, _, clock = make_body(ep, base_delay=99)  # reconnect delays must not fire here

    st = body.respawn(timeout=30, poll=1.0)

    assert ("respawnPlayer",) in ep.log
    assert st.hp == pytest.approx(16.0)
    assert st.dead is False
    assert clock.sleeps == [1.0, 1.0]


def test_respawn_times_out_when_hp_never_returns():
    ep = FakeEntryPoint(states=[DEAD_GAME_STATE])
    body, ep, _, clock = make_body(ep)

    with pytest.raises(BodyUnavailable):
        body.respawn(timeout=3, poll=1.0)

    assert clock.sleeps == [1.0, 1.0, 1.0]


# --- chat and events ----------------------------------------------------------------


def test_recent_chat_passes_lines_through():
    body, ep, _, _ = make_body()
    assert body.recent_chat(2) == ("§2§l§o[Alto Clef] Task started", "LastOrder01 was slain by Zombie")


def test_events_queue_carries_chat_death_damage_with_timestamps():
    body, _, _, _ = make_body()
    body.callback.onChatMessage("LastOrder01 was slain by Zombie")
    body.callback.onDeath("Zombie")
    body.callback.onDamage(2.5)

    events = body.events()
    assert [e.type for e in events] == ["chat.line", "game.death", "game.damage"]
    assert [e.source for e in events] == ["body", "body", "body"]
    assert all(e.ts_mono > 0 and e.ts_wall for e in events)
    assert len({e.id for e in events}) == 3
    assert events[2].payload["text"] == "2.5"


def test_service_lines_are_flagged_not_removed():
    body, _, _, _ = make_body()
    body.callback.onChatMessage("§2§l§o[Alto Clef] Task started")
    body.callback.onChatMessage("[Tungsten] path computed")
    body.callback.onChatMessage("LastOrder01 joined the game")

    events = body.events()
    assert [e.payload["service"] for e in events] == [True, True, False]
    assert all(e.payload["text"] for e in events)  # nothing was dropped


@pytest.mark.parametrize(
    "text,service",
    [
        ("§2§l§o[Alto Clef] Task started", True),
        ("[Tungsten] path computed", True),
        ("LastOrder01 was slain by Zombie", False),
        ("somebody said [Alto Clef] mid-sentence", False),
        ("", False),
    ],
)
def test_is_service_line(text, service):
    assert is_service_line(text) is service


# --- observation parsers ------------------------------------------------------------


def test_entities_parse_raw_uc_rows():
    body, _, _, _ = make_body()
    ents = body.entities(8)
    assert ents[0].kind == "zombie"
    assert ents[0].name == "Zombie"
    assert ents[0].dist_m == pytest.approx(8.5)
    assert ents[1].name == "item"  # no display name on item entities: kind stands in


def test_blocks_parse_raw_uc_rows():
    body, _, _, _ = make_body()
    blocks = body.blocks_around(2)
    assert [b.name for b in blocks] == ["grass block", "birch leaves"]


def test_inventory_skips_empty_slots():
    body, _, _, _ = make_body()
    inv = body.inventory()
    assert [(s.name, s.count, s.slot) for s in inv] == [
        ("minecraft:birch_log", 12, 1),
        ("minecraft:dirt", 3, 2),
    ]


# --- levers -------------------------------------------------------------------------


def test_act_goto_passes_coordinates():
    body, ep, _, _ = make_body()
    body.act("goto", {"x": 112, "y": 71, "z": -12})
    assert ("gotoXYZ", 112, 71, -12) in ep.log


def test_act_chat_sends_text_as_is():
    body, ep, _, _ = make_body()
    body.act("chat", {"text": "probe: body online"})
    assert ("ChatMessage", "probe: body online") in ep.log


def test_act_command_lever_normalizes_the_prefix():
    body, ep, _, _ = make_body()
    body.act("command", {"cmd": "get log 1"})
    assert ("ExecuteCommand", "@get log 1") in ep.log


def test_act_unknown_lever_is_loud():
    body, _, _, _ = make_body()
    with pytest.raises(ValueError, match="unknown lever"):
        body.act("dance", {})


# --- wait_in_game ---------------------------------------------------------------------


def test_wait_in_game_survives_a_bridge_that_is_not_up_yet():
    # A fresh client opens its gateway only after a minute or more: the wait must poll
    # through BodyUnavailable until the outer timeout, not die after 5 reconnects.
    ep = FakeEntryPoint()
    ep.raise_on_get_state = Py4JNetworkError("gateway not up yet")
    body, ep, _, clock = make_body(ep, retries=1, base_delay=0.1)

    with pytest.raises(BodyUnavailable, match="did not enter the game"):
        body.wait_in_game(timeout=100, poll=10)

    # several outer polls happened, not just one reconnect burst
    assert clock.sleeps.count(10.0) >= 3


def test_wait_in_game_returns_state_once_in_game():
    ep = FakeEntryPoint(states=[E1_GAME_STATE])
    ep.inGame = lambda: False
    body, ep, _, clock = make_body(ep)
    calls = {"n": 0}
    orig_sleep = clock.sleep

    def counting_sleep(seconds):
        calls["n"] += 1
        if calls["n"] == 2:
            ep.inGame = lambda: True
        orig_sleep(seconds)

    body._sleep = counting_sleep

    st = body.wait_in_game(timeout=100, poll=1.0)

    assert st.in_game is True
    assert calls["n"] == 2


# --- busy and stop ------------------------------------------------------------------


def test_busy_reflects_active_task():
    ep = FakeEntryPoint()
    ep.busy = True
    body, ep, _, _ = make_body(ep)
    assert body.busy() is True


def test_stop_stops_pathing_and_sends_the_stop_command():
    body, ep, _, _ = make_body()
    body.stop()
    assert ("stopPathing",) in ep.log
    assert ("ExecuteCommand", "@stop") in ep.log
