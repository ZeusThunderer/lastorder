"""Live scenario for the death path (T-013): birth -> RCON /kill -> will -> respawn ->
birth -> request_death(stuck) -> will -> birth, on the real stack.

Runs inside mind-1 (network_mode: service:body-1), like dev/body_smoke.py; the mind
image ships runtime/ but not dev/, so the script is piped through stdin:

    docker compose -f docker/compose.yml build mind-1
    docker compose -f docker/compose.yml up -d mc-server hive body-1
    docker compose -f docker/compose.yml run -T --rm mind-1 python - < dev/death_scenario.py

Env: HIVE_URL (default http://hive:8000), MC_USERNAME (the slot, default LastOrder01 —
the RCON /kill touches this slot only), RCON_HOST/RCON_PASSWORD, RUN_DIR (default run),
NODE_NAMING_PRESET (default lab); the smart tier comes from the LLM_* env or a repo-root
.env (config.load_dotenv, no secrets printed).

Checks printed at the end: two wills in the knowledge base via the heir's inheritance
(causes game and stuck), the serial grown by 2, the slot holding exactly the last node,
no pending wills left, the LLM cost within the task's $0.01.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from runtime.config import CHARACTER_DIR, env, load_dotenv
from runtime.game import Py4jBody
from runtime.hive.client import HiveClient
from runtime.knowledge.schema import ReflectionInput
from runtime.lastorder.death import DeathDetector, classify_death
from runtime.lastorder.lifecycle import Lifecycle
from runtime.lastorder.naming import preset
from runtime.lastorder.persona import Node
from runtime.lastorder.traits import load_quirks, roll
from runtime.llm.client import LLMClient, LLMConfig
from runtime.ports import BirthRecord

POLL_S = 2.0
DEATH_TIMEOUT_S = 300.0

FAILURES: list[str] = []
COSTS: list[tuple[str, object]] = []


def ok(name: str, detail="") -> None:
    print(f"[ok] {name}{': ' + str(detail) if detail else ''}", flush=True)


def fail(name: str, detail="") -> None:
    FAILURES.append(name)
    print(f"[FAIL] {name}{': ' + str(detail) if detail else ''}", flush=True)


def check(name: str, condition: bool, detail="") -> bool:
    if condition:
        ok(name, detail)
    else:
        fail(name, detail)
    return condition


def now() -> datetime:
    return datetime.now(timezone.utc)


def log_event(type: str, payload: dict | None = None) -> None:
    print(f"[life] {type} {json.dumps(payload or {}, ensure_ascii=False)}", flush=True)


# --- minimal Source RCON (the server is ours; the first death kills this slot only) -----


def rcon(command: str) -> str:
    host, port = os.environ.get("RCON_HOST", "mc-server"), 25575
    password = os.environ["RCON_PASSWORD"]

    def packet(rid: int, kind: int, body: str) -> bytes:
        data = struct.pack("<ii", rid, kind) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(data)) + data

    with socket.create_connection((host, port), timeout=10) as s:
        s.sendall(packet(1, 3, password))
        s.recv(4096)
        s.sendall(packet(2, 2, command))
        return s.recv(4096).decode("utf-8", "replace")


# --- the two halves of the scenario ------------------------------------------------------


def reflection_for(record: BirthRecord, cause: str, note: str) -> ReflectionInput:
    return ReflectionInput(
        serial=record.serial,
        kind="death",
        cause=cause,
        last_action="стоял у спавна и слушал чат",
        notes=(note,),
        core_memory="",
        personal_goal="осмотреться и выжить",
        network_goals=(),
    )


def report_rebirth(tag: str, rebirth) -> None:
    will = rebirth.will
    ok(
        f"{tag}: will written",
        f"serial {will.meta.serial:05d}, emergency={rebirth.emergency}, "
        f"model={rebirth.model}, delivered={rebirth.will_delivered}, "
        f"lifetime_s={will.meta.lifetime_s}",
    )
    if rebirth.emergency:
        print(f"  emergency reason: {rebirth.error}", flush=True)
    print("  will body:", flush=True)
    for line in will.body.split("\n"):
        print(f"    | {line}", flush=True)
    stats = rebirth.stats
    if stats is not None:
        COSTS.append((tag, stats))
        ok(
            f"{tag}: smart call stats",
            f"model={stats.model}, in={stats.prompt_tokens}, out={stats.completion_tokens}, "
            f"latency={stats.latency_s:.1f}s, cost={stats.cost_usd}",
        )
    ok(f"{tag}: heir born", f"serial {rebirth.record.serial:05d}, traits seed {rebirth.traits.seed}")


def wait_death(body: Py4jBody, detector: DeathDetector, slot: str, what: str):
    """Poll state+chat until the detector rules a death; one event per death."""
    deadline = time.monotonic() + DEATH_TIMEOUT_S
    while time.monotonic() < deadline:
        state = body.state()
        chat = body.recent_chat(10)
        event = detector.feed(state, chat)
        if event is not None:
            ok(f"{what}: death detected", f"hp={state.hp}, text={event.text!r}")
            return event
        time.sleep(POLL_S)
    fail(f"{what}: no death detected within {DEATH_TIMEOUT_S:.0f}s")
    return None


def main() -> int:
    load_dotenv()
    slot = env("MC_USERNAME") or "LastOrder01"
    run_dir = Path(env("RUN_DIR") or "run")
    naming = preset(env("NODE_NAMING_PRESET", default="lab") or "lab")
    llm = LLMClient(LLMConfig.for_tier("smart"))
    ok("config", f"slot={slot}, preset={naming.key}, smart={llm.config.model}, run_dir={run_dir}")

    body = Py4jBody()
    ok("body in game", str(body.wait_in_game(timeout=600)))

    hive = HiveClient(base_url=env("HIVE_URL") or "http://hive:8000", run_dir=run_dir)
    quirks = load_quirks(CHARACTER_DIR / "quirks.txt")
    life = Lifecycle(
        slot=slot,
        body=body,
        registry=hive,
        memory=hive,
        llm=llm,
        naming=naming,
        run_dir=run_dir,
        log=log_event,
    )

    # --- generation one: born, then killed by the scenario's own RCON ------------------
    traits1 = roll(secrets.randbelow(2**31), quirks)
    record1 = hive.birth(slot, traits1)
    ok("birth 1", f"serial {record1.serial:05d}, ttl_min={record1.ttl_min}, born_at={record1.born_at}")
    node1 = Node(record1.serial, traits1)

    ok("rcon kill (own slot)", rcon(f"kill {slot}"))
    detector1 = DeathDetector(slot)
    event1 = wait_death(body, detector1, slot, "death 1 (game)")
    if event1 is None:
        return 1
    cause1 = classify_death(now(), record1, self_eliminated=False, death_text=event1.text)
    check("death 1 cause is game", cause1.cause == "game", cause1.cause)
    rebirth1 = life.on_death(
        node1,
        record1,
        cause1,
        reflection_for(record1, cause1.cause, "смерть пришла раньше первого шага"),
        pause=lambda: print("[life] C1/C2 paused", flush=True),
        resume=lambda: print("[life] C1/C2 resumed", flush=True),
    )
    report_rebirth("death 1", rebirth1)
    check("body alive after respawn", body.state().hp > 0, f"hp={body.state().hp}")

    # --- generation two: the node asks hive to eliminate it (ADR-026) ------------------
    detector2 = DeathDetector(slot)
    hive.request_death(rebirth1.record.serial, "stuck")
    ok("request_death(stuck) sent", f"serial {rebirth1.record.serial:05d}")
    event2 = wait_death(body, detector2, slot, "death 2 (stuck)")
    if event2 is None:
        return 1
    cause2 = classify_death(
        now(), rebirth1.record, self_eliminated=True, death_text=event2.text
    )
    check("death 2 cause is stuck", cause2.cause == "stuck", cause2.cause)
    rebirth2 = life.on_death(
        Node(rebirth1.record.serial, rebirth1.traits),
        rebirth1.record,
        cause2,
        reflection_for(rebirth1.record, cause2.cause, "путь не шёл, решил устраниться"),
    )
    report_rebirth("death 2", rebirth2)
    check("body alive after respawn", body.state().hp > 0, f"hp={body.state().hp}")

    # --- the promises of the task, read back through the heir --------------------------
    inheritance = hive.inheritance(rebirth2.record.serial)
    notes = inheritance.inherited_notes
    check("heir sees will 1", rebirth1.will.body.split("\n")[0] in notes)
    check("heir sees will 2", rebirth2.will.body.split("\n")[0] in notes)
    summary = inheritance.predecessors_summary
    check("predecessors carry cause game", "game" in summary, summary)
    check("predecessors carry cause stuck", "stuck" in summary, summary)
    check("serial grew by 2", rebirth2.record.serial == record1.serial + 2,
          f"{record1.serial:05d} -> {rebirth2.record.serial:05d}")
    alive = hive.alive()
    mine = [(b.serial, b.slot) for b in alive if b.slot == slot]
    check(
        "the slot holds exactly the last node",
        mine == [(rebirth2.record.serial, slot)],
        str([(b.serial, b.slot) for b in alive]),
    )
    pending = list((run_dir / slot / "pending").glob("*.md")) if (run_dir / slot / "pending").exists() else []
    check("no pending wills left", not pending, str(pending))

    total = sum(stats.cost_usd or 0.0 for _, stats in COSTS)
    known = all(stats.cost_usd is not None for _, stats in COSTS) and COSTS
    check(
        "llm cost within $0.01",
        (not known) or total <= 0.01,
        f"${total:.4f}" + ("" if known else " (provider did not report the price)"),
    )
    tokens = sum((stats.completion_tokens or 0) for _, stats in COSTS)
    ok("llm tokens (completion, both wills)", tokens)

    hive.close()
    body.close()
    print("scenario passed" if not FAILURES else f"scenario FAILED: {FAILURES}", flush=True)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
