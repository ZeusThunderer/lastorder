"""Node lifetime bookkeeping of the hive (NETWORK.md section 7; ADR-018, ADR-026).

The store of T-012 owns the durable facts (birth.json, will.md); this module owns
the decisions on top of them:

- the 45-minute deadline: a background scan marks expired nodes and RCON-kills
  them, once per node, retrying while the kill stays unconfirmed;
- `request_death(serial, "stuck")`: the node chose to remove itself, hive kills
  immediately without a threshold of its own (ADR-026);
- cause truth at `retire`: an RCON death looks like `game` to the mind, so hive
  corrects the cause to `stuck` (asked) or `ttl` (confirmed deadline kill);
- slot sequencing (ADR-018): a birth into a slot whose node is still alive means
  the old mind restarted without a will — the old node is marked `lost` and closed
  with an emergency will (there is no death without a will, CLAUDE.md; review
  P1-2), the serial is never reused (the store's counter).

Marks (`lost`, `stuck`, pending kills) live in `lifecycle.json` in the run volume;
on startup they are reconciled with the store's living nodes, including slot
duplicates left by a crash. The store offers no public born_at reader and
`runtime/knowledge/` is untouchable from T-022, so reconciliation reads the
documented `nodes/<serial>/birth.json` layout through the store's own parser,
read-only. Every decision lands in the event log (`events.jsonl`, `HiveLog`).

All state is guarded by one lock: sync FastAPI endpoints run in a thread pool,
the scan runs via `asyncio.to_thread`, and both race the same dictionaries
(review P1-4). No LLM is involved anywhere here.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from runtime.config import env
from runtime.knowledge.schema import BirthFile, emergency_will
from runtime.knowledge.slice import format_serial, parse_utc
from runtime.knowledge.store import AlreadyRetiredError, KnowledgeStore, UnknownNodeError
from runtime.lastorder.naming import node_name, preset as naming_preset
from runtime.lastorder.traits import Traits
from runtime.ports import BirthRecord, NodeBrief
from runtime.hive.rcon import RconClient, RconError, kill_confirmed

MAX_LIFE_MIN = 45  # CLAUDE.md: forced death after this many minutes; env MAX_LIFE_MIN overrides
TTL_SCAN_S = 30  # background scan cadence (task T-022); env HIVE_TTL_SCAN_S overrides
CAUSE_STUCK = "stuck"  # ADR-026: the only cause request_death accepts
# The emergency will of a lost node needs an addressee; NamingPreset (ADR-018)
# carries no heir form and runtime/lastorder/ is outside T-022, so the address is
# preset-neutral — the node's own name does come from the configured preset.
HEIR_ADDRESS = "наследник"


class LifecycleError(ValueError):
    """The request contradicts the lifecycle rules; nothing happened."""


class HiveLog:
    """Append-only JSONL of hive events in the run volume; one line per decision."""

    def __init__(self, path: Path, now: Callable[[], str] | None = None):
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())
        self._lock = threading.Lock()

    def event(self, type: str, payload: Mapping[str, object] | None = None) -> None:
        line = json.dumps(
            {"ts": self._now(), "type": type, "payload": dict(payload or {})},
            ensure_ascii=False,
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


@dataclass
class NodeState:
    serial: int
    slot: str
    born_at: str  # ISO-8601, from BirthRecord
    ttl_min: int
    lost: bool = False
    stuck: bool = False  # the node asked for its own death (ADR-026)
    kill_reason: str | None = None  # "ttl" (deadline passed) | "stuck" (the node asked)
    kill_confirmed: bool = False  # the server answered a real kill
    kill_error: str | None = None  # last failure signature, to not spam the event log (R1-3)


class Lifecycle:
    """Who lives, who is due to die, and who died how — behind one lock (P1-4)."""

    def __init__(
        self,
        store: KnowledgeStore,
        rcon: RconClient,
        *,
        log: HiveLog,
        run_dir: Path,
        clock: Callable[[], datetime] | None = None,
        max_life_min: int | None = None,
    ):
        self._store = store
        self._rcon = rcon
        self._log = log
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_life_min = int(
            max_life_min if max_life_min is not None else (env("MAX_LIFE_MIN") or MAX_LIFE_MIN)
        )
        self._state_path = Path(run_dir) / "lifecycle.json"
        self._lock = threading.RLock()
        self._nodes: dict[int, NodeState] = {}
        self._load_and_reconcile()

    # --- persistence -----------------------------------------------------------------

    def _save(self) -> None:
        payload = {"nodes": [asdict(n) for n in sorted(self._nodes.values(), key=lambda n: n.serial)]}
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_name(self._state_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_path)

    def _birth_file(self, serial: int) -> BirthFile:
        path = self._store.root / "nodes" / format_serial(serial) / "birth.json"
        return BirthFile.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def _store_living(self) -> dict[int, BirthFile]:
        """Living nodes straight from the documented layout: birth.json, no will.md."""
        living: dict[int, BirthFile] = {}
        nodes_dir = self._store.root / "nodes"
        for child in sorted(nodes_dir.iterdir()):
            if not (child.is_dir() and child.name.isdigit()):
                continue
            if not (child / "birth.json").exists() or (child / "will.md").exists():
                continue
            birth = BirthFile.from_dict(
                json.loads((child / "birth.json").read_text(encoding="utf-8"))
            )
            living[birth.serial] = birth
        return living

    def _load_and_reconcile(self) -> None:
        state: dict[int, NodeState] = {}
        if self._state_path.exists():
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            for item in raw.get("nodes", []):
                node = NodeState(**item)
                state[node.serial] = node
        living = self._store_living()
        for serial, birth in living.items():
            if serial in state:
                state[serial].slot = birth.slot
                state[serial].born_at = birth.born_at
                state[serial].ttl_min = birth.ttl_min
            else:  # born before this run (or the state file was lost): recover it
                state[serial] = NodeState(
                    serial=serial, slot=birth.slot, born_at=birth.born_at, ttl_min=birth.ttl_min
                )
        for serial in [s for s in state if s not in living]:  # retired meanwhile
            del state[serial]
        self._nodes = state
        # A marked-lost node without a will, or a slot with two living nodes (a crash
        # between the two writes), is closed with an emergency will (review P1-2).
        for serial, node in sorted(self._nodes.items()):
            if node.lost:
                self._close_lost(node)
        by_slot: dict[str, list[NodeState]] = {}
        for node in self._nodes.values():
            by_slot.setdefault(node.slot, []).append(node)
        for nodes in by_slot.values():
            for node in sorted(nodes, key=lambda n: n.serial)[:-1]:  # newest stays
                node.lost = True
                self._log.event(
                    "life.lost",
                    {"serial": node.serial, "slot": node.slot, "reason": "startup reconcile"},
                )
                self._close_lost(node)
        self._save()

    def _close_lost(self, node: NodeState) -> None:
        naming = naming_preset(env("NAME_PRESET") or "lab")
        will = emergency_will(
            serial=node.serial,
            slot=node.slot,
            cause="game",  # `lost` is not one of the store's CAUSES; the fact rides the event
            born_at=node.born_at,
            died_at=self._clock().isoformat(),
            traits=self._birth_file(node.serial).traits,
            last_action="",
            last_events=[],
            node_name=node_name(naming, node.serial),
            heir_address=HEIR_ADDRESS,
        )
        self._store.retire(node.serial, "game", will.to_markdown())
        self._nodes.pop(node.serial, None)

    # --- the ports' calls ---------------------------------------------------------------

    def birth(self, slot: str, traits: Traits) -> BirthRecord:
        with self._lock:
            # Every node of the slot, including one already marked lost whose close
            # failed mid-way (a store error): the next birth retries it (R1-8).
            for node in [n for n in self._nodes.values() if n.slot == slot]:
                first_lost = not node.lost
                node.lost = True
                if first_lost:
                    self._log.event(
                        "life.lost",
                        {
                            "serial": node.serial,
                            "slot": slot,
                            "reason": "mind restarted without a will",
                        },
                    )
                self._close_lost(node)
            record = self._store.birth(slot, traits, ttl_min=self._max_life_min)
            self._nodes[record.serial] = NodeState(
                serial=record.serial,
                slot=slot,
                born_at=record.born_at,
                ttl_min=record.ttl_min,
            )
            self._save()
            self._log.event(
                "life.birth", {"serial": record.serial, "slot": slot, "ttl_min": record.ttl_min}
            )
            return record

    def alive(self) -> list[NodeBrief]:
        with self._lock:
            return [
                NodeBrief(serial=node.serial, slot=node.slot)
                for serial, node in sorted(self._nodes.items())
                if not node.lost
            ]

    def require_node(self, serial: int) -> NodeState:
        node = self._nodes.get(serial)
        if node is not None:
            if node.lost:  # closed or closing: not a participant anymore (R1-8)
                raise UnknownNodeError(f"node {format_serial(serial)} is lost")
            return node
        if (self._store.root / "nodes" / format_serial(serial) / "will.md").exists():
            raise AlreadyRetiredError(f"node {format_serial(serial)} is retired")
        raise UnknownNodeError(f"unknown node {format_serial(serial)}")

    def request_death(self, serial: int, cause: str) -> None:
        if cause != CAUSE_STUCK:
            raise LifecycleError(
                f'request_death accepts only "{CAUSE_STUCK}" (ADR-026), got {cause!r}'
            )
        with self._lock:
            node = self.require_node(serial)  # also refuses lost nodes (R1-8)
            node.stuck = True
            if node.kill_reason is None:
                node.kill_reason = CAUSE_STUCK
            self._log.event("life.stuck", {"serial": serial, "slot": node.slot})
            self._save()
            self._try_kill(node)  # immediately, without a threshold of its own

    def retire(self, serial: int, cause: str, will_text: str) -> None:
        with self._lock:
            node = self._nodes.get(serial)
            effective = cause
            if node is not None:
                if node.stuck:
                    effective = CAUSE_STUCK  # the RCON death looked like `game` to the mind
                elif node.kill_reason == "ttl" and node.kill_confirmed:
                    effective = "ttl"
            self._store.retire(serial, effective, will_text)
            self._nodes.pop(serial, None)
            self._save()
            self._log.event("life.retire", {"serial": serial, "cause": effective})

    # --- the deadline scan ----------------------------------------------------------------

    def scan(self, now: datetime | None = None) -> None:
        """Mark expired nodes and retry pending kills; called from the background loop."""
        now = now or self._clock()
        with self._lock:
            for serial, node in sorted(self._nodes.items()):
                if node.lost:
                    continue
                deadline = parse_utc(node.born_at) + timedelta(minutes=node.ttl_min)
                if node.kill_reason is None and now >= deadline:
                    node.kill_reason = "ttl"
                    self._log.event(
                        "life.expired",
                        {"serial": serial, "slot": node.slot, "ttl_min": node.ttl_min},
                    )
                    self._save()
                if node.kill_reason is not None and not node.kill_confirmed:
                    self._try_kill(node)

    def _try_kill(self, node: NodeState) -> None:
        try:
            response = self._rcon.kill(node.slot)
        except RconError as exc:
            ok, detail_key, detail = False, "error", str(exc)
        else:
            ok, detail_key, detail = kill_confirmed(response), "response", response[:200]
        if ok:
            node.kill_confirmed = True
            node.kill_error = None
            self._save()
            self._log.event(
                "life.killed",
                {"serial": node.serial, "slot": node.slot, "reason": node.kill_reason},
            )
            return
        # Retried every scan, but logged once per failure and again only when the
        # failure changes — a 30-second event spam helps nobody (review R1-3).
        signature = f"{detail_key}: {detail}"
        if node.kill_error != signature:
            node.kill_error = signature
            self._save()
            self._log.event(
                "life.rcon_failed",
                {"serial": node.serial, "slot": node.slot, detail_key: detail},
            )
