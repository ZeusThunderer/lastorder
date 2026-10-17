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


def mob_pos(rcon_fn, selector: str) -> tuple[float, float, float] | None:
    """Exact entity pos straight from the server (NBT doubles): aim and chase target."""
    raw = rcon_fn(f"data get entity {selector} Pos")
    if "entity data" not in raw:
        return None
    nums = re.findall(r"(-?[\d.]+)d", raw.split(":", 1)[-1])
    if len(nums) < 3:
        return None
    return float(nums[0]), float(nums[1]), float(nums[2])


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

    # -- mine the block under the feet, cell picked from blocks_around positions.
    #    Two stand facts: earlier runs leave the body on dug-up ground (hop until
    #    the under cell is listed), and tungsten's break queue once silently
    #    dropped an under-feet cell (7th run of the day) — a fresh spot works.
    def mine_under_the_feet() -> str:
        for attempt in range(3):
            st = body.state()
            for hop in range(4):
                feet = (math.floor(st.pos[0]), math.floor(st.pos[1]), math.floor(st.pos[2]))
                cell = next((b for b in body.blocks_around(2)
                             if b.pos == (feet[0], feet[1] - 1, feet[2])), None)
                if cell is not None:
                    break
                body.act("goto", {"x": feet[0] + 8, "y": feet[1], "z": feet[2]})
                wait_busy_clears(body, 60)
                st = body.state()
            else:
                raise AssertionError(f"no listed ground under the feet within 4 hops (at {feet})")
            print(f"       before mine: {snapshot(body)}; target {cell.name} at {cell.pos}")
            body.act("mine", {"pos": list(cell.pos)})
            try:
                gone = wait_block_gone(body, cell.pos, 30)
                return f"{gone} (try {attempt + 1})"
            except TimeoutError:
                if attempt < 2:  # the queue dropped this cell: take a fresh spot
                    body.act("goto", {"x": feet[0] + 8, "y": feet[1], "z": feet[2]})
                    wait_busy_clears(body, 60)
        raise AssertionError("under-feet mine timed out on 3 spots")

    if step("mine the block under the feet (blocks_around pos)", mine_under_the_feet) is not None:
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
    # A red run can leave a live tagged cow behind: two t020 cows would make the
    # limit=1 selector flap between them. Kill leftovers by the tag — only ours.
    step("cleanup tagged leftovers (rcon)", lambda: rcon(f"kill @e[type=cow,tag={COW_TAG}]"))
    # Summon at the body's own cell: the body stands on it, so the cow cannot
    # spawn over a dug hole (feet+3 once dropped a cow into an old tunnel).
    step("summon cow (rcon, tag t020)", lambda: rcon(
        f'summon minecraft:cow {feet[0]} {feet[1]} {feet[2]} {{Tags:["{COW_TAG}"]}}'))
    cow = step("cow visible in entities", lambda: wait_for_kind(body, "cow", 30))

    def tagged_cow_readable() -> float:
        hp = mob_health(rcon, COW_SEL)
        assert hp is not None, "tagged cow visible to the client but unreadable by RCON"
        return hp

    hp0 = step("cow health before attack (rcon)", tagged_cow_readable)
    if cow is None or hp0 is None:
        # No cow to fight: the FAIL above already fails the run, and a strike that
        # swings at nothing must not count as a pass (review R2-2).
        print("[skip] attack/strike: no tagged cow to fight")
    else:
        punk = step("act attack allow=cow (punkAny)", lambda: body.act("attack", {"allow": ["cow"]}))
        print(f"       punkAny reply: {punk}")
        step("busy sees the fight", lambda: body.busy())
        time.sleep(10)  # give tungsten every chance to swing at the cow

        def cow_health_after_punk() -> float:
            # An unreadable cow after the fight is an anomaly worth a FAIL, not
            # a value: skipping the strike on it once made a green run without
            # a single entity hit (review R2-2, checker C-1).
            hp = mob_health(rcon, COW_SEL)
            assert hp is not None, "tagged cow lost during punkAny (unreadable by RCON)"
            return hp

        hp1 = step("cow health after 10s of punkAny", cow_health_after_punk)
        if hp1 is not None:
            verdict = "NO DAMAGE: players-only confirmed" if hp1 == hp0 else \
                "hp changed during punkAny (environmental? punkAny is players-only)"
            print(f"       punkAny on a mob: hp {hp0} -> {hp1} ({verdict})")
        step("act attack_stop", lambda: body.act("attack_stop", {}))
        step("fight over (busy False)", lambda: not body.busy())

        if hp1 is None:
            # The probe above FAILed: the cow is gone, and the strike check must not
            # vanish with it — summon a fresh tagged cow and keep the same rules.
            def resummon_cow() -> str:
                st_now = body.state()
                x = math.floor(st_now.pos[0])
                y, z = math.floor(st_now.pos[1]), math.floor(st_now.pos[2])
                rcon(f'summon minecraft:cow {x} {y} {z} {{Tags:["{COW_TAG}"]}}')
                time.sleep(1)
                hp = mob_health(rcon, COW_SEL)
                assert hp is not None, "re-summoned cow unreadable by RCON"
                return f"re-summoned at ({x},{y},{z}), hp={hp}"

            step("re-summon cow for the strike (rcon, tag t020)", resummon_cow)

        # -- the mob path: lookAt + left click (strike), the lever that does hit mobs.
        #    Stand facts: melee reach is 3 blocks (chase first, the cow wanders off),
        #    the feet cell aims UNDER the hitbox (aim mid-torso), and the nearest
        #    `kind == "cow"` can be a stray from an older session (review R3-1).
        #    Runs in every case — probe undamaged, environmentally damaged, or a
        #    re-summon — and a pass needs >= 1 RCON-confirmed hit and a dead tagged cow.
        step("grant iron sword (rcon)", lambda: rcon(f"give {name} iron_sword 1"))
        time.sleep(2)
        sword = next((s for s in body.inventory() if "sword" in s.name and s.slot <= 8), None)
        if sword is not None:
            step("hold the sword", lambda: body.act("select_hotbar", {"slot": sword.slot}))

        def strike_the_cow() -> str:
            # Target the TAGGED cow by its exact server-side pos: the world keeps
            # cows from older sessions, and a strike at the nearest `kind == "cow"`
            # lands on those (review R3-1). A hit counts only when the tagged cow's
            # hp actually dropped (RCON), not when the client reported ENTITY —
            # a stray cow under the crosshair also reports ENTITY.
            hits = 0
            deadline = time.monotonic() + 180  # hops are wait-heavy: bound the chase
            for attempt in range(1, 41):
                hp = mob_health(rcon, COW_SEL)
                if hp is None:
                    break  # gone from the server: dead — the assert below gates the pass
                if attempt == 40 or time.monotonic() > deadline:
                    # A loop that merely ran out is a FAIL, not a "cow down" (R3-2).
                    raise AssertionError(
                        f"tagged cow alive after {attempt} tries (hp={hp}, {hits} confirmed hit(s))")
                rpos = mob_pos(rcon, COW_SEL)
                if rpos is None:
                    raise AssertionError("tagged cow alive but its Pos is unreadable by RCON")
                if math.dist(body.state().pos, rpos) > 4:
                    body.act("goto", {"x": math.floor(rpos[0]), "y": math.floor(rpos[1]),
                                      "z": math.floor(rpos[2])})
                    wait_busy_clears(body, 45)
                    continue
                body.act("strike", {"pos": [rpos[0], rpos[1] + 0.7, rpos[2]]})
                time.sleep(0.6)  # let the hit land server-side before reading hp
                hp_after = mob_health(rcon, COW_SEL)
                if hp_after is None or hp_after < hp:
                    hits += 1
            assert hits >= 1, "tagged cow gone with 0 confirmed hits: the strike never landed"
            return f"cow down, {hits} confirmed hit(s) landed"

        step("strike the tagged cow (lookAt + left click, RCON-confirmed)", strike_the_cow)

        def tagged_cow_dead() -> str:
            hp = mob_health(rcon, COW_SEL)
            assert hp is None, f"tagged cow still alive after the strike (hp={hp})"
            return "tagged cow removed from the world"

        step("tagged cow dead (rcon witness)", tagged_cow_dead)
        step("after combat", lambda: snapshot(body))
    step("cleanup cow (rcon, tag t020)", lambda: rcon(f"kill @e[type=cow,tag={COW_TAG}]"))

    # -- eat, if there is food (bread granted if the hotbar has none). The lever
    #    holds "use" now (review R2-4), so a pass means the meal actually happened.
    inv = body.inventory()
    slot = next((s.slot for s in inv if any(h in s.name for h in FOOD_HINTS) and s.slot <= 8), None)
    if slot is None:
        # /give lands items in the first free slot of the WHOLE inventory: with a
        # hotbar full of smoke junk the bread lands beyond selectHotbar's reach
        # (slot > 8), and the client cache can lag even a hotbar give. Force the
        # slot with `item replace`, poll for it, and fail loud — a green run must
        # not skip the meal it arranged.
        def grant_bread_in_hotbar() -> str:
            taken = {s.slot for s in body.inventory() if s.slot <= 8}
            free = next((i for i in range(9) if i not in taken), 0)
            rcon(f"item replace entity {name} hotbar.{free} with minecraft:bread 3")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if any("bread" in s.name and s.slot == free for s in body.inventory()):
                    return f"3 bread forced into hotbar slot {free}"
                time.sleep(2)
            raise AssertionError(f"bread forced into hotbar.{free} but never visible")

        step("grant bread into the hotbar (rcon item replace)", grant_bread_in_hotbar)
        inv = body.inventory()
        slot = next((s.slot for s in inv if any(h in s.name for h in FOOD_HINTS) and s.slot <= 8), None)
    if slot is None:
        print("[skip] eat: no food in the hotbar to eat (see the FAIL above)")
    else:
        held = next(s.name for s in inv if s.slot == slot)
        count0 = next((s.count for s in inv if s.slot == slot), 0)
        food0 = body.state().food
        print(f"       eating {held} from slot {slot}, food={food0:.0f}")
        if food0 >= 20:
            # MC refuses to eat at full food: drain until eating becomes possible.
            # Hunger V since 1.21.2 (x10 exhaustion scale — Hunger I moved nothing
            # in 90 s); saturation from a previous meal slows it further, so the
            # window is wide. The gate is the meal itself, not the drain speed:
            # FAIL only if food is still 20 and eating is impossible.

            def drain_hunger() -> float:
                rcon(f"effect give {name} minecraft:hunger 120 4")
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline and body.state().food >= 20:
                    time.sleep(3)
                food = body.state().food
                assert food < 20, f"food still {food:.0f} after 120 s of Hunger V: MC will not eat"
                return food

            food0 = step("drain hunger for the eat check (rcon)", drain_hunger)
            if food0 is None:
                food0 = body.state().food  # drain failed: the meal check fails honestly
        step("act eat (selectHotbar + hold use)", lambda: body.act("eat", {"slot": slot}))
        time.sleep(3)
        st1 = step("state after eat", lambda: body.state())
        inv1 = step("inventory after eat", lambda: body.inventory())
        left = next((s.count for s in (inv1 or ()) if s.slot == slot), 0)
        food1 = st1.food if st1 is not None else -1.0
        print(f"       eat result: food {food0:.0f} -> {food1:.0f}, {held} count {count0} -> {left}")

        def meal_happened() -> str:
            assert st1 is not None and inv1 is not None, "state/inventory unreadable after eat"
            # A death mid-meal drops the whole inventory: the count drop would then
            # be the death drop, not a meal (checker C-1). Witness only the living.
            assert not st1.dead and st1.hp > 0, (
                f"body died during the meal (hp={st1.hp}): count drop is not meal evidence")
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
