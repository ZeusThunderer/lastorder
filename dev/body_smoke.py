"""Live smoke for Py4jBody on the stand (T-006): in, state, goto +5x, @get log 1,
RCON kill, respawn.

Runs inside mind-1 (network_mode: service:body-1), so the py4j gateway is on
localhost. Prints raw game data on purpose: a debugging tool, not a node;
nothing here reaches a prompt. The mind image ships runtime/ but not dev/, so
the script is piped through stdin:

    docker compose -f docker/compose.yml build mind-1
    docker compose -f docker/compose.yml up -d mc-server body-1
    docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
from typing import Literal

from runtime.game import BodyState, Py4jBody
from runtime.game.py4j_body import is_service_line

FAILURES: list[str] = []


def step(name: str, fn):
    t = time.monotonic()
    try:
        value = fn()
        print(f"[ok]   {name} ({(time.monotonic() - t) * 1000:.0f} ms): "
              f"{json.dumps(value, ensure_ascii=False, default=str)[:600]}")
        return value
    except Exception as exc:
        FAILURES.append(name)
        print(f"[FAIL] {name}: {exc}")
        return None


def rcon(command: str) -> str:
    """Minimal Source RCON client; the server is ours, password from env."""
    host, port, password = os.environ.get("RCON_HOST", "mc-server"), 25575, os.environ["RCON_PASSWORD"]

    def packet(rid: int, kind: int, body: str) -> bytes:
        data = struct.pack("<ii", rid, kind) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(data)) + data

    with socket.create_connection((host, port), timeout=10) as s:
        s.sendall(packet(1, 3, password))
        s.recv(4096)
        s.sendall(packet(2, 2, command))
        raw = s.recv(4096)
        return raw[12:-2].decode(errors="replace")


def wait_busy_clears(body: Py4jBody, timeout: float) -> Literal[True]:
    # An enqueued task registers as active only on a later tick: give it a beat
    # before polling, or busy() reads False and the wait is over before it began.
    time.sleep(3)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not body.busy():
            return True
        time.sleep(5)
    raise TimeoutError(f"task still active after {timeout:.0f}s")


def wait_dead(body: Py4jBody, timeout: float) -> BodyState:
    """hp = 0 in getGameState is the death signal that survives mob/fall/lava kills."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = body.state()
        if st.dead:
            return st
        time.sleep(2)
    raise TimeoutError(f"body did not die within {timeout:.0f}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=900, help="seconds to wait for the client to join")
    args = ap.parse_args()

    body = Py4jBody()
    try:
        st = step("wait_in_game", lambda: body.wait_in_game(args.wait))
        if st is None:
            return 1
        step("bind_callbacks", lambda: body.bind_callbacks())
        step("state", lambda: body.state())

        x, y, z = (int(c) for c in st.pos)
        step("act goto +5 x", lambda: body.act("goto", {"x": x + 5, "y": y, "z": z}))
        step("goto finished", lambda: wait_busy_clears(body, 180))
        step("state after goto", lambda: body.state())

        step("command get log 1", lambda: body.command("get log 1"))
        step("get finished", lambda: wait_busy_clears(body, 300))
        step("inventory after @get", lambda: body.inventory())
        chat = step("recent_chat", lambda: body.recent_chat(5))
        if chat is not None:
            print("       service flags:", [(line[:40], is_service_line(line)) for line in chat])

        name = os.environ.get("MC_USERNAME", "LastOrder01")
        step("rcon kill", lambda: rcon(f"kill {name}"))
        dead = step("wait dead (hp = 0)", lambda: wait_dead(body, 60))
        if dead is not None:
            print(f"       dead={dead.dead} hp={dead.hp}")
        step("chat after kill", lambda: body.recent_chat(5))

        alive = step("respawn", lambda: body.respawn(timeout=60))
        if alive is not None:
            print(f"       hp after respawn: {alive.hp}, dead={alive.dead}")

        print("\nEVENTS:")
        for ev in body.events()[-20:]:
            service = " [service]" if ev.payload.get("service") else ""
            print(f"  {ev.type}{service}: {ev.payload['text'][:160]}")
    finally:
        body.close()

    if FAILURES:
        print(f"\n{len(FAILURES)} step(s) failed: {', '.join(FAILURES)}")
        return 1
    print("\nsmoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
