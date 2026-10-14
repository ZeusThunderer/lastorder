"""E1 spike: can a mind drive a headless unionclef body over py4j?

Runs inside mind-N (network_mode: service:body-N), so the gateway is on localhost.
Prints raw game data on purpose: this is a debugging tool, not a node; nothing here reaches a prompt.

    python tools/body_probe.py [--wait 600] [--kill]
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time

from py4j.java_gateway import CallbackServerParameters, GatewayParameters, JavaGateway

EVENTS: list[tuple[str, str]] = []


class Callback:
    def isStarted(self):
        return True

    def onChatMessage(self, s=""):
        EVENTS.append(("chat", s))
        return ""

    def onVerifedChat(self, m=None):
        return m

    def onUpdateServerInfo(self, m=None):
        EVENTS.append(("server", str(m)))
        return m

    def onDeath(self, s=""):
        EVENTS.append(("death", s))

    def onKill(self, s=""):
        EVENTS.append(("kill", s))

    def onAutoclefEvent(self, t="", s=""):
        EVENTS.append(("event", f"{t}: {s}"))

    def onDamage(self, s=0.0):
        EVENTS.append(("damage", str(s)))

    def onDamageConfirmed(self, damaged="", attacker="", amount=0.0):
        EVENTS.append(("damage_confirmed", f"{damaged} by {attacker}: {amount}"))

    def onCaptchaSolveRequest(self, image_bytes=None):
        pass

    def onVoiceFeed(self, s="", audio=None):
        pass

    def agentCommandRequest(self, s=""):
        return ""

    class Java:
        implements = ["adris.altoclef.PythonCallback"]


def rcon(command: str) -> str:
    """Minimal Source RCON client (the server is ours; password from env)."""
    host, port, password = os.environ.get("RCON_HOST", "mc-server"), 25575, os.environ.get("RCON_PASSWORD", "lastorder")

    def packet(rid: int, kind: int, body: str) -> bytes:
        data = struct.pack("<ii", rid, kind) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(data)) + data

    with socket.create_connection((host, port), timeout=10) as s:
        s.sendall(packet(1, 3, password))
        s.recv(4096)
        s.sendall(packet(2, 2, command))
        raw = s.recv(4096)
        return raw[12:-2].decode(errors="replace")


def step(name: str, fn):
    t = time.monotonic()
    try:
        value = fn()
        print(f"[ok]   {name} ({(time.monotonic() - t) * 1000:.0f} ms): {json.dumps(value, ensure_ascii=False, default=str)[:600]}")
        return value
    except Exception as exc:
        print(f"[FAIL] {name}: {exc}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=900, help="seconds to wait for the client to join")
    ap.add_argument("--kill", action="store_true", help="also kill the body via RCON and watch the respawn")
    args = ap.parse_args()

    gw = JavaGateway(
        gateway_parameters=GatewayParameters(auto_convert=True),
        callback_server_parameters=CallbackServerParameters(),
        python_server_entry_point=Callback(),
        start_callback_server=True,
    )
    mc = gw.entry_point

    deadline = time.monotonic() + args.wait
    while True:
        try:
            if mc.inGame():
                break
        except Exception:
            pass
        if time.monotonic() > deadline:
            print("[FAIL] client never got in game")
            return 1
        time.sleep(5)
    print("[ok]   in game")
    step("InitPythonCallback", lambda: mc.InitPythonCallback())
    time.sleep(2)
    step("IsCallbackServerStarted", lambda: mc.IsCallbackServerStarted())

    state = step("getGameState", lambda: dict(mc.getGameState()))
    step("getEntitiesInfo", lambda: [dict(e) for e in mc.getEntitiesInfo(8)])
    step("getBlocksAround(2)", lambda: [dict(b) for b in mc.getBlocksAround(2)][:10])
    step("getInventoryFull", lambda: str(mc.getInventoryFull())[:300])
    step("getRecentChat", lambda: list(mc.getRecentChat(5)))

    pos = None
    if state and state.get("self"):
        pos = dict(state["self"]).get("pos")
        print("       pos:", pos)
    step("ChatMessage", lambda: mc.ChatMessage("probe: body online"))
    # uc respawns on its own only while a task runs (TaskRunner idles otherwise), so respawn explicitly.
    step("respawnPlayer (in case a previous run left a corpse)", lambda: mc.respawnPlayer())
    time.sleep(2)

    if pos:
        x, y, z = (int(float(c)) for c in pos.split(","))
        step("gotoXYZ +6 x", lambda: mc.gotoXYZ(x + 6, y, z))
        for _ in range(15):
            time.sleep(2)
            status = step("pathStatus", lambda: dict(mc.pathStatus()))
            if status and status.get("arrived"):
                break

    # Commands must carry the prefix, otherwise CommandExecutor silently drops them.
    step("ExecuteCommand @get log 1", lambda: mc.ExecuteCommand("@get log 1"))
    for _ in range(36):
        time.sleep(5)
        busy = step("hasActiveTask", lambda: mc.hasActiveTask())
        if not busy:
            break
    step("getInventoryFull after @get", lambda: str(mc.getInventoryFull())[:300])

    if args.kill:
        name = os.environ.get("MC_USERNAME", "LastOrder01")
        step("rcon kill", lambda: rcon(f"kill {name}"))
        for _ in range(5):
            time.sleep(2)
            step("getGameState", lambda: hp_pos(mc))
        step("getRecentChat after kill", lambda: list(mc.getRecentChat(5)))
        step("respawnPlayer", lambda: mc.respawnPlayer())
        for _ in range(5):
            time.sleep(2)
            step("getGameState after respawn", lambda: hp_pos(mc))

    print("\nEVENTS:")
    for kind, text in EVENTS[-30:]:
        print(f"  {kind}: {text[:200]}")
    # close(), not shutdown(): py4j shutdown() also stops uc's GatewayServer until the client restarts.
    gw.close()
    gw.shutdown_callback_server()
    return 0


def hp_pos(mc) -> dict:
    me = dict(dict(mc.getGameState()).get("self") or {})
    return {k: me.get(k) for k in ("hp", "pos")}


if __name__ == "__main__":
    sys.exit(main())
