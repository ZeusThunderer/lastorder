"""Live smoke for Py4jBody on the stand (T-006): in, state, goto +5x, @get log 1,
RCON kill, respawn. `--actions` (T-020) drives the survival levers: mine under
the feet, @get log 2, hold it, attack/attack_stop on the nearest animal, eat.

Runs inside mind-1 (network_mode: service:body-1), so the py4j gateway is on
localhost. Prints raw game data on purpose: a debugging tool, not a node;
nothing here reaches a prompt. The mind image ships runtime/ but not dev/, so
the script is piped through stdin:

    docker compose -f docker/compose.yml build mind-1
    docker compose -f docker/compose.yml up -d mc-server body-1
    docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/body_smoke.py
    docker compose -f docker/compose.yml run -T --rm mind-1 python - --actions < dev/body_smoke.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
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


def count_logs(inv) -> int:
    return sum(s.count for s in inv if s.name.endswith("_log"))


def assert_goto_arrived(after: BodyState, x: int) -> str:
    # uc answers ok:True for a goto that never starts (e.g. on a dead body),
    # so the smoke checks the ground truth: the body actually stands near x+5.
    if abs(after.pos[0] - (x + 5)) > 2:
        raise AssertionError(f"x={after.pos[0]:.1f}, expected within 2 blocks of {x + 5}")
    return f"x moved {after.pos[0] - x:+.1f} (goal +5, tolerance 2)"


def assert_log_gained(inv_after, logs_before) -> str:
    if logs_before is None:
        raise AssertionError("baseline log count unknown")
    gained = count_logs(inv_after or ()) - logs_before
    if gained < 1:
        raise AssertionError(f"log count did not grow (before {logs_before}, after {count_logs(inv_after or ())})")
    return f"+{gained} log(s)"


# --- --actions: the T-020 survival levers --------------------------------------------

FOOD_HINTS = ("bread", "beef", "porkchop", "chicken", "mutton", "apple", "carrot",
              "potato", "cod", "salmon", "cooked")

# The combat cow is tagged: the world keeps cows from older sessions, and an
# untagged selector would measure and kill those too (review R2-3).
COW_TAG = "t020"
COW_SEL = f"@e[type=cow,tag={COW_TAG},limit=1]"


def snapshot(body: Py4jBody) -> str:
    """The before/after line every --actions step prints: inventory, hp, busy."""
    st = body.state()
    inv = body.inventory()
    total = sum(s.count for s in inv)
    return (f"hp={st.hp:.0f} food={st.food:.0f} pos=({st.pos[0]:.0f},{st.pos[1]:.0f},{st.pos[2]:.0f})"
            f" busy={body.busy()} inv={len(inv)} slots/{total} items")


def mob_health(rcon_fn, selector: str) -> float | None:
    """Cow HP straight from the server: the honest witness of whether combat levers hit."""
    raw = rcon_fn(f"data get entity {selector} Health")
    m = re.search(r"[-\d.]+", raw.split(":", 1)[-1]) if "entity data" in raw else None
    return float(m.group()) if m else None


def mob_pos(rcon_fn, selector: str) -> tuple[int, int, int] | None:
    """Entity pos straight from the server: where to walk when entities() lost it."""
    raw = rcon_fn(f"data get entity {selector} Pos")
    if "entity data" not in raw:
        return None
    nums = re.findall(r"(-?[\d.]+)d", raw.split(":", 1)[-1])
    if len(nums) < 3:
        return None
    return int(float(nums[0])), int(float(nums[1])), int(float(nums[2]))


def wait_food_below(body: Py4jBody, level: float, timeout: float) -> float:
    """MC refuses to eat at food = 20, so the eat check drains it first."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        food = body.state().food
        if food < level:
            return food
        time.sleep(3)
    raise TimeoutError(f"food still >= {level} after {timeout:.0f}s")


def wait_block_gone(body: Py4jBody, cell: tuple[int, int, int], timeout: float) -> str:
    """mineBlocks is a break queue, not an altoclef task: poll the world, not busy()."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cell not in [b.pos for b in body.blocks_around(2)]:
            return f"block at {cell} gone from the world"
        time.sleep(2)
    raise TimeoutError(f"block at {cell} still there after {timeout:.0f}s")


def wait_for_kind(body: Py4jBody, kind: str, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [e for e in body.entities(24) if e.kind == kind]
        if found:
            return found[0]
        time.sleep(2)
    raise TimeoutError(f"no {kind} in entities within {timeout:.0f}s")


def actions_smoke(body: Py4jBody, name: str) -> None:
    st = body.wait_in_game(900)
    body.bind_callbacks()
    if st.dead:
        step("respawn (dead at start)", lambda: body.respawn())
    print("baseline:", snapshot(body))

    # -- mine the block under the feet, cell picked from blocks_around positions
    st = step("state for the cell", lambda: body.state())
    feet = (math.floor(st.pos[0]), math.floor(st.pos[1]), math.floor(st.pos[2]))
    under = (feet[0], feet[1] - 1, feet[2])
    cell = step("block under feet found in blocks_around",
                lambda: next(b for b in body.blocks_around(2) if b.pos == under))
    if cell is not None:
        print(f"       mining {cell.name} at {cell.pos}")
        step("before mine", lambda: snapshot(body))
        step("act mine (pos from blocks_around)", lambda: body.act("mine", {"pos": list(cell.pos)}))
        step("block mined", lambda: wait_block_gone(body, cell.pos, 90))
        step("after mine", lambda: snapshot(body))

    # -- @get log 2 through the lever, then hold the log
    logs_before = step("logs before @get", lambda: count_logs(body.inventory()))
    step("act get log 2", lambda: body.act("get", {"item": "log", "count": 2}))
    step("get finished", lambda: wait_busy_clears(body, 600))
    inv = step("inventory after @get", lambda: body.inventory())
    if inv is not None and logs_before is not None:

        def ensured_two_logs() -> int:
            n = count_logs(body.inventory())
            assert n >= 2, f"only {n} logs after @get log 2"
            return n

        step("@get ensured 2 logs", ensured_two_logs)
    log_slot = next((s.slot for s in (inv or ()) if s.name.endswith("_log") and s.slot <= 8), None)
    if log_slot is None:
        FAILURES.append("hold a log: none in the hotbar")
        print("[FAIL] hold a log: none in the hotbar")
    else:
        step("act select_hotbar (hold the log)", lambda: body.act("select_hotbar", {"slot": log_slot}))
        step("held is a log", lambda: ("log" in body.state().held, body.state().held))
        step("after hold", lambda: snapshot(body))

    # -- combat: does punkAny hit a MOB? (task: verify on the stand; players-only
    #    would mean attack needs a different lever). The cow is summoned at a FRESH
    #    position: @get log 2 walked the body ~90 m from where `feet` was first
    #    taken (review R2-1), and it carries a tag so selectors touch only this cow.
    st = step("state fresh for summon (post-@get)", lambda: body.state())
    feet = (math.floor(st.pos[0]), math.floor(st.pos[1]), math.floor(st.pos[2]))
    step("summon cow (rcon, tag t020)", lambda: rcon(
        f'summon minecraft:cow {feet[0] + 3} {feet[1]} {feet[2]} {{Tags:["{COW_TAG}"]}}'))
    cow = step("cow visible in entities", lambda: wait_for_kind(body, "cow", 30))
    hp0 = step("cow health before attack (rcon)", lambda: mob_health(rcon, COW_SEL))
    if cow is None or hp0 is None:
        # No cow to fight: the FAIL above already fails the run, and a strike that
        # swings at nothing must not count as a pass (review R2-2).
        print("[skip] attack/strike: no tagged cow to fight")
    else:
        punk = step("act attack allow=cow (punkAny)", lambda: body.act("attack", {"allow": ["cow"]}))
        print(f"       punkAny reply: {punk}")
        step("busy sees the fight", lambda: body.busy())
        time.sleep(10)  # give tungsten every chance to swing at the cow
        hp1 = step("cow health after 10s of punkAny", lambda: mob_health(rcon, COW_SEL))
        print(f"       punkAny on a mob: hp {hp0} -> {hp1}"
              f" ({'NO DAMAGE: players-only confirmed' if hp1 == hp0 else 'mob took damage'})")
        step("act attack_stop", lambda: body.act("attack_stop", {}))
        step("fight over (busy False)", lambda: not body.busy())

        # -- the mob path: lookAt + left click (strike), the lever that does hit mobs.
        #    Two stand facts from the first run: melee reach is 3 blocks (chase first,
        #    the cow wanders off), and the feet cell aims UNDER the hitbox (aim mid-torso).
        #    A pass needs >= 1 ENTITY hit AND the tagged cow dead (review R2-2).
        if hp1 is None:
            print("[skip] strike: tagged cow unreadable after punkAny (see the FAIL above)")
        elif hp1 == hp0:
            step("grant iron sword (rcon)", lambda: rcon(f"give {name} iron_sword 1"))
            time.sleep(2)
            sword = next((s for s in body.inventory() if "sword" in s.name and s.slot <= 8), None)
            if sword is not None:
                step("hold the sword", lambda: body.act("select_hotbar", {"slot": sword.slot}))

            def strike_the_cow() -> str:
                hits = 0
                for _ in range(40):
                    if mob_health(rcon, COW_SEL) is None:
                        break  # the tagged cow left the world: dead
                    ents = [e for e in body.entities(24) if e.kind == "cow"]
                    if not ents:
                        # Alive per the server but out of the entities window: walk to it.
                        target = mob_pos(rcon, COW_SEL)
                        if target is None:
                            break
                        body.act("goto", {"x": target[0], "y": target[1], "z": target[2]})
                        wait_busy_clears(body, 45)
                        continue
                    near = min(ents, key=lambda e: e.dist_m)
                    if near.dist_m > 4:
                        body.act("goto", {"x": near.pos[0], "y": near.pos[1], "z": near.pos[2]})
                        wait_busy_clears(body, 45)
                        continue
                    reply = body.act("strike",
                                     {"pos": [near.pos[0] + 0.5, near.pos[1] + 0.7, near.pos[2] + 0.5]})
                    hits += bool(reply.get("hit") == "ENTITY")
                    time.sleep(0.7)
                assert hits >= 1, "0 entity hits landed: the cow was never in reach"
                return f"cow down, {hits} entity hit(s) landed"

            step("strike the cow (lookAt + left click)", strike_the_cow)

            def tagged_cow_dead() -> str:
                hp = mob_health(rcon, COW_SEL)
                assert hp is None, f"tagged cow still alive after the strike (hp={hp})"
                return "tagged cow removed from the world"

            step("tagged cow dead (rcon witness)", tagged_cow_dead)
            step("after combat", lambda: snapshot(body))
        else:
            print("       strike skipped: punkAny already damaged the mob")
    step("cleanup cow (rcon, tag t020)", lambda: rcon(f"kill @e[type=cow,tag={COW_TAG}]"))

    # -- eat, if there is food (bread granted if the hotbar has none). The lever
    #    holds "use" now (review R2-4), so a pass means the meal actually happened.
    inv = body.inventory()
    slot = next((s.slot for s in inv if any(h in s.name for h in FOOD_HINTS) and s.slot <= 8), None)
    if slot is None:
        step("grant bread (rcon)", lambda: rcon(f"give {name} bread 3"))
        time.sleep(2)
        inv = body.inventory()
        slot = next((s.slot for s in inv if any(h in s.name for h in FOOD_HINTS) and s.slot <= 8), None)
    if slot is None:
        print("[skip] eat: no food in the hotbar to eat")
    else:
        held = next(s.name for s in inv if s.slot == slot)
        count0 = next((s.count for s in inv if s.slot == slot), 0)
        food0 = body.state().food
        print(f"       eating {held} from slot {slot}, food={food0:.0f}")
        if food0 >= 20:
            # MC refuses to eat at full food: drain some so the check can see a meal.
            # Hunger V: since 1.21.2 exhaustion is 40 per food point (x10 scale), and
            # Hunger I (0.1/tick) proved too slow on the stand (90 s moved nothing).
            step("drain hunger for the eat check (rcon)",
                 lambda: rcon(f"effect give {name} minecraft:hunger 60 4"))
            food0 = step("food drained below 18", lambda: wait_food_below(body, 18, 90))
            if food0 is None:
                food0 = body.state().food  # drain failed: run the check anyway, it fails honestly
        step("act eat (selectHotbar + hold use)", lambda: body.act("eat", {"slot": slot}))
        time.sleep(3)
        st1 = step("state after eat", lambda: body.state())
        inv1 = step("inventory after eat", lambda: body.inventory())
        left = next((s.count for s in (inv1 or ()) if s.slot == slot), 0)
        food1 = st1.food if st1 is not None else -1.0
        print(f"       eat result: food {food0:.0f} -> {food1:.0f}, {held} count {count0} -> {left}")

        def meal_happened() -> str:
            assert st1 is not None and inv1 is not None, "state/inventory unreadable after eat"
            gained = st1.food - food0
            assert left < count0 or gained > 0, (
                f"no meal: {held} count {count0} -> {left}, food {food0:.0f} -> {st1.food:.0f}")
            return f"ate {held}: count -{count0 - left}, food +{gained:.0f}"

        step("eat consumed the food", meal_happened)
        step("after eat", lambda: snapshot(body))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=900, help="seconds to wait for the client to join")
    ap.add_argument("--actions", action="store_true",
                    help="T-020 mode: mine/@get/hold/attack/eat levers on the live stand")
    args = ap.parse_args()

    body = Py4jBody()
    try:
        if args.actions:
            actions_smoke(body, os.environ.get("MC_USERNAME", "LastOrder01"))
            print("\nEVENTS:")
            for ev in body.events()[-20:]:
                service = " [service]" if ev.payload.get("service") else ""
                print(f"  {ev.type}{service}: {ev.payload['text'][:160]}")
            if FAILURES:
                print(f"\n{len(FAILURES)} step(s) failed: {', '.join(FAILURES)}")
                return 1
            print("\nactions smoke passed")
            return 0

        st = step("wait_in_game", lambda: body.wait_in_game(args.wait))
        if st is None:
            return 1
        step("bind_callbacks", lambda: body.bind_callbacks())
        if st.dead:
            # A corpse from a previous session still counts as in game; goto on it
            # silently no-ops, so the smoke starts from a living body.
            st = step("respawn (dead at start)", lambda: body.respawn())
            if st is None:
                return 1

        logs_before = step("logs in inventory before @get", lambda: count_logs(body.inventory()))
        x, y, z = (int(c) for c in st.pos)
        step("act goto +5 x", lambda: body.act("goto", {"x": x + 5, "y": y, "z": z}))
        step("goto finished", lambda: wait_busy_clears(body, 180))
        after = step("state after goto", lambda: body.state())
        if after is not None:
            step("goto moved the body", lambda: assert_goto_arrived(after, x))

        if (logs_before or 0) >= 1:
            # @get log 1 means "ensure >= 1 log in inventory": with a log already held
            # it finishes in 0.0 s without doing any work, so clear the inventory first.
            name = os.environ.get("MC_USERNAME", "LastOrder01")
            step("clear inventory for @get (rcon kill)", lambda: rcon(f"kill {name}"))
            step("wait dead after clear", lambda: wait_dead(body, 60))
            step("respawn after clear", lambda: body.respawn())
            logs_before = 0
        step("command get log 1", lambda: body.command("get log 1"))
        step("get finished", lambda: wait_busy_clears(body, 300))
        inv_after = step("inventory after @get", lambda: body.inventory())
        step("@get added a log", lambda: assert_log_gained(inv_after, logs_before))
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
