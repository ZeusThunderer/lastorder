"""The task board of the network (docs/architecture/TASKBOARD.md; ADR-042; T-042).

One SQLite store (`board.sqlite` in RUN_DIR, WAL) holds the entries, the append-only
`board_log` (audit journal and the feed source, sections 8-9) and the delivery cursors.
Every mutation is one `BEGIN IMMEDIATE` transaction under one process lock — the claim
race of section 4.1 is settled by `UPDATE … WHERE status='open'` plus the row count,
not by the lock alone. The board is NOT the knowledge base (ADR-017): nothing here is
git-tracked, and hive is the only writer — agents act through the MCP tools
(`runtime/hive/mcp.py`) and the HTTP routes (`runtime/hive/app.py`).

Identity is the actor string the caller already resolved from its key: `operator`,
`last-order`, a node's serial as a string (`"7"`), `runner:<slot>`, `admin` — and
`system` for the death sweep. `from` is never taken from a body (section 2): the HTTP
and MCP layers stamp it. A sister's serial must be live (born, not dead, not lost) for
every write — the will session a dead node still holds is for `submit_will`, not for
the board.

Anti-spam caps (section 6) are counted from `board_log` itself: a serial never repeats,
so "per life" is "per serial" and needs no counter table. Text lengths refuse instead
of truncating (the agent must know it did not land), and raw `minecraft:` ids are
refused like everywhere else that agent text is stored (ADR-020).

The feed (section 9) is `board_log` rows after a cursor, filtered to what the reader
owns: new entries addressed to her or `net`, replies and status changes on entries
where she is the author or the taker — her own actions never come back. The first read
of a life gets no backlog: the cursor starts at "now" and the open `net` (including
readdressed) tasks addressed to the network are handed over once as a snapshot.
Delivery is at-least-once; the cursor moves only on `ack`.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from runtime.config import env
from runtime.hive.lifecycle import HiveLog, Lifecycle
from runtime.knowledge.slice import parse_utc
from runtime.lastorder.naming import node_name, preset as naming_preset
from runtime.perception.translate import NAMESPACED

# --- limits (TASKBOARD.md sections 3, 5, 6; the numbers are section 14 assumptions) --------

TEXT_MAX = 800  # entry text; longer is 422, never truncated
REPLY_MAX = 400  # one thread reply
ACTION_MAX = 64  # request action
ARGS_MAX_KEYS = 10  # request args
WHY_MAX = 400  # request why
OPERATOR_MSGS_PER_LIFE = 5  # sister -> operator messages (per serial)
LO_MSGS_PER_HOUR = 20  # Last Order -> operator messages (rolling hour)
AGENT_MUTATIONS_PER_LIFE = 30  # every board write of one agent life, replies included
HELD_TASKS_MAX = 2  # tasks one sister may hold (taken + needs_input)
ALARM_REPEAT_WINDOW_S = 600  # same (type, from) repeats fold into `count` for 10 min
FEED_LIMIT = 500  # one feed read; the rest waits for the next poll
LIST_LIMIT = 15  # the MCP `board` tool: a glance, not a dump
LIST_TEXT = 120  # the MCP `board` line cuts the text here
ENTRIES_LIMIT_MAX = 200  # the operator listing

KINDS = ("task", "request", "message", "alarm")
ALARM_TYPES = (
    "harness_failover",
    "session_missing",
    "stuck",
    "delivery_failing",
    "quota",
    "emergency_will",
)
_TERMINAL = {"done", "failed", "declined", "canceled", "approved", "denied", "answered", "acked"}

# section 3.2: (from, to) -> who may, text mandatory. Missing pairs are `bad_transition`.
_TASK_MOVES: dict[tuple[str, str], str] = {
    ("open", "taken"): "taker",
    ("open", "declined"): "addressee",
    ("taken", "done"): "taker",
    ("taken", "failed"): "taker",
    ("taken", "needs_input"): "taker",
    ("needs_input", "taken"): "author",
    ("taken", "open"): "taker",
    ("open", "canceled"): "author-or-operator",
    ("taken", "canceled"): "author-or-operator",
    ("needs_input", "canceled"): "author-or-operator",
}
_TASK_TEXT_REQUIRED = {
    ("open", "declined"),
    ("taken", "done"),
    ("taken", "failed"),
    ("taken", "needs_input"),
    ("needs_input", "taken"),
    ("taken", "open"),
}
_REQUEST_MOVES: dict[tuple[str, str], str] = {
    ("open", "approved"): "addressee-or-operator",
    ("open", "denied"): "addressee-or-operator",
    ("open", "answered"): "addressee-or-operator",
    ("open", "canceled"): "author",
}
_REQUEST_TEXT_REQUIRED = {("open", "denied"), ("open", "answered")}

# section 3.3: who may post which (kind -> target class). Everything else is a refusal.
_POST_RIGHTS = {
    ("operator", "task", "node"),
    ("operator", "task", "net"),
    ("operator", "task", "last-order"),
    ("operator", "message", "net"),
    ("last-order", "task", "node"),
    ("last-order", "task", "net"),
    ("last-order", "request", "operator"),
    ("last-order", "message", "operator"),
    ("last-order", "message", "net"),
    ("node", "task", "node"),
    ("node", "task", "net"),
    ("node", "task", "last-order"),
    ("node", "request", "operator"),
    ("node", "request", "last-order"),
    ("node", "message", "operator"),
}

# section 7: which alarm author may raise which type. `delivery_failing` is the node's
# own (misaka-link with the node key); the environment's alarms belong to the runners.
_ALARM_RIGHTS: dict[str, set[str]] = {
    "runner": {"harness_failover", "session_missing", "stuck", "quota", "emergency_will"},
    "admin": {"harness_failover", "quota"},
    "node": {"delivery_failing"},
}

_ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REQUEST_ANSWERS = ("approve", "deny", "respond", "edit")
_MUTATION_OPS = ("post", "reply", "transition", "claim")  # what an agent's cap counts

OPERATOR = "operator"
LAST_ORDER = "last-order"
SYSTEM = "system"
NET = "net"


class BoardError(Exception):
    """The refused board call; carries the HTTP mapping (status, kind) with it."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message


def actor_role(actor: str) -> str:
    """`operator` | `last-order` | `admin` | `runner` | `node` | `system`."""
    if actor in (OPERATOR, LAST_ORDER, SYSTEM, "admin"):
        return actor
    if actor.startswith("runner:"):
        return "runner"
    return "node"


def actor_serial(actor: str) -> int | None:
    """The node serial of a node actor, else None."""
    if actor_role(actor) != "node":
        return None
    try:
        return int(actor)
    except ValueError:
        return None


def _target_class(to: str) -> str | None:
    if to in (NET, OPERATOR, LAST_ORDER):
        return to
    if to.isdigit() and int(to) >= 1:
        return "node"
    return None


class Board:
    """Entries, transitions, alarms, the journal and the feed behind one lock + SQLite."""

    def __init__(
        self,
        path: Path | str,
        *,
        lifecycle: Lifecycle,
        log: HiveLog,
        clock: Callable[[], datetime],
    ):
        self._path = Path(path)
        self._lifecycle = lifecycle
        self._log = log
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, timeout=10.0, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- schema --------------------------------------------------------------------------

    def _create(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS board_entry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    from_ TEXT NOT NULL,
                    to_ TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    claimed_by TEXT,
                    request_json TEXT,
                    grant_json TEXT,
                    readdressed_from TEXT,
                    alarm_type TEXT,
                    alarm_count INTEGER NOT NULL DEFAULT 1,
                    alarm_at TEXT,
                    alarm_slot TEXT,
                    alarm_serial INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS board_entry_status ON board_entry(status);
                CREATE INDEX IF NOT EXISTS board_entry_to ON board_entry(to_);
                CREATE TABLE IF NOT EXISTS board_reply (
                    entry_id INTEGER NOT NULL,
                    seq INTEGER NOT NULL,
                    from_ TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status_change TEXT,
                    at TEXT NOT NULL,
                    PRIMARY KEY (entry_id, seq)
                );
                CREATE TABLE IF NOT EXISTS board_log (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    entry_id INTEGER NOT NULL,
                    op TEXT NOT NULL,
                    before TEXT,
                    after TEXT
                );
                CREATE TABLE IF NOT EXISTS board_cursor (
                    who TEXT PRIMARY KEY,
                    seq INTEGER NOT NULL
                );
                """
            )

    # --- plumbing -----------------------------------------------------------------------

    def _tx(self) -> Iterator[sqlite3.Cursor]:
        """`with self._tx() as cur:` — one BEGIN IMMEDIATE mutation transaction."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn.cursor()
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
    _tx = contextlib.contextmanager(_tx)

    def _now(self) -> str:
        return self._clock().isoformat()

    def _live_serials(self) -> set[int]:
        alive = {brief.serial for brief in self._lifecycle.alive()}
        return {s for s in alive if not self._lifecycle.body_dead(s)}

    def _require_live_node(self, actor: str) -> None:
        serial = actor_serial(actor)
        if serial is None or serial not in self._live_serials():
            raise BoardError(409, "no_live_node", f"{actor} has no live node to write for")

    def _check_text(self, text: Any, limit: int, what: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise BoardError(422, "validation", f"{what} must be a non-empty string")
        if len(text) > limit:
            raise BoardError(
                422, "validation", f"{what} is {len(text)} characters, over the {limit} cap"
            )
        leaked = sorted(set(NAMESPACED.findall(text)))
        if leaked:
            raise BoardError(422, "validation", f"{what}: raw game ids {leaked} never enter the board")
        return text

    def _entry_row(self, cur: sqlite3.Cursor, entry_id: int) -> sqlite3.Row | None:
        return cur.execute("SELECT * FROM board_entry WHERE id=?", (entry_id,)).fetchone()

    def _entry(self, cur: sqlite3.Cursor, entry_id: int, *, with_thread: bool = True) -> dict:
        row = self._entry_row(cur, entry_id)
        if row is None:
            raise BoardError(404, "unknown_entry", f"no board entry #{entry_id}")
        return self._to_json(cur, row, with_thread=with_thread)

    def _to_json(self, cur: sqlite3.Cursor, row: sqlite3.Row, *, with_thread: bool) -> dict:
        out: dict[str, Any] = {
            "id": row["id"],
            "kind": row["kind"],
            "from": row["from_"],
            "to": row["to_"],
            "text": row["text"],
            "status": row["status"],
            "claimed_by": row["claimed_by"],
            "request": json.loads(row["request_json"]) if row["request_json"] else None,
            "grant": json.loads(row["grant_json"]) if row["grant_json"] else None,
            "readdressed_from": row["readdressed_from"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if row["kind"] == "alarm":
            out["alarm"] = {
                "type": row["alarm_type"],
                "count": row["alarm_count"],
                "slot": row["alarm_slot"],
                "serial": row["alarm_serial"],
            }
        if with_thread:
            replies = cur.execute(
                "SELECT * FROM board_reply WHERE entry_id=? ORDER BY seq", (row["id"],)
            ).fetchall()
            out["thread"] = [
                {
                    "seq": r["seq"],
                    "from": r["from_"],
                    "text": r["text"],
                    "status_change": json.loads(r["status_change"]) if r["status_change"] else None,
                    "at": r["at"],
                }
                for r in replies
            ]
        return out

    def _log_row(
        self,
        cur: sqlite3.Cursor,
        actor: str,
        entry_id: int,
        op: str,
        before: Any,
        after: Any,
        *,
        text_len: int | None = None,
    ) -> None:
        cur.execute(
            "INSERT INTO board_log (at, actor, entry_id, op, before, after) VALUES (?,?,?,?,?,?)",
            (
                self._now(),
                actor,
                entry_id,
                op,
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
            ),
        )
        payload: dict[str, Any] = {"entry": entry_id, "op": op, "actor": actor}
        if text_len is not None:
            payload["len"] = text_len
        self._log.event(f"board.{op}", payload)

    # --- caps (section 6) -----------------------------------------------------------------

    def _cap_write(self, cur: sqlite3.Cursor, actor: str) -> None:
        """The per-life mutation cap of an agent (posts, replies, moves, claims)."""
        if actor_role(actor) != "node" and actor != LAST_ORDER:
            return  # operator, runners, admin, system: no agent caps
        used = cur.execute(
            "SELECT COUNT(*) FROM board_log WHERE actor=? AND op IN (?,?,?,?)",
            (actor, *_MUTATION_OPS),
        ).fetchone()[0]
        if used >= AGENT_MUTATIONS_PER_LIFE:
            raise BoardError(
                429,
                "record_cap",
                f"{used} board writes this life, over the {AGENT_MUTATIONS_PER_LIFE} cap",
            )

    def _cap_operator_message(self, cur: sqlite3.Cursor, actor: str) -> None:
        role = actor_role(actor)
        if role not in ("node", "last-order"):
            return
        if role == "node":
            used = cur.execute(
                "SELECT COUNT(*) FROM board_log l JOIN board_entry e ON e.id=l.entry_id"
                " WHERE l.actor=? AND l.op='post' AND e.kind='message' AND e.to_='operator'",
                (actor,),
            ).fetchone()[0]
            if used >= OPERATOR_MSGS_PER_LIFE:
                raise BoardError(
                    429,
                    "operator_cap",
                    f"{used} messages to the operator this life"
                    f" (cap {OPERATOR_MSGS_PER_LIFE}; a new life resets it)",
                )
        else:
            cutoff = (self._clock() - timedelta(hours=1)).isoformat()
            row = cur.execute(
                "SELECT MIN(l.at) FROM board_log l JOIN board_entry e ON e.id=l.entry_id"
                " WHERE l.actor=? AND l.op='post' AND e.kind='message' AND e.to_='operator'"
                " AND l.at>=?",
                (actor, cutoff),
            ).fetchone()
            oldest = row[0] if row else None
            used = cur.execute(
                "SELECT COUNT(*) FROM board_log l JOIN board_entry e ON e.id=l.entry_id"
                " WHERE l.actor=? AND l.op='post' AND e.kind='message' AND e.to_='operator'"
                " AND l.at>=?",
                (actor, cutoff),
            ).fetchone()[0]
            if used >= LO_MSGS_PER_HOUR:
                reset = "the hour window"
                if oldest:
                    try:
                        reset = (parse_utc(oldest) + timedelta(hours=1)).isoformat()
                    except ValueError:
                        pass
                raise BoardError(
                    429,
                    "operator_cap",
                    f"{used} messages to the operator this hour (cap {LO_MSGS_PER_HOUR},"
                    f" resets {reset})",
                )

    # --- posting (sections 3.1, 3.3, 5.1) --------------------------------------------------

    def post(
        self,
        actor: str,
        *,
        kind: Any,
        to: Any,
        text: Any,
        request: dict | None = None,
    ) -> dict:
        if kind not in ("task", "request", "message"):
            raise BoardError(422, "validation", f"kind must be task|request|message, got {kind!r}")
        if actor_role(actor) == "node":
            self._require_live_node(actor)
        if not isinstance(to, str) or not to.strip():
            raise BoardError(422, "validation", "to must be a serial, net, last-order or operator")
        to = to.strip()
        target = _target_class(to)
        if target is None:
            raise BoardError(422, "validation", f"to {to!r} is not a serial, net, last-order or operator")
        role = actor_role(actor)
        if (role, kind, target) not in _POST_RIGHTS:
            raise BoardError(403, "forbidden", f"{actor} may not post {kind} -> {to}")
        if target == "node":
            live = self._live_serials()
            serial_to = int(to)
            if serial_to not in live:
                raise BoardError(422, "validation", f"node {to} is not live")
            if role == "node" and serial_to == actor_serial(actor):
                raise BoardError(422, "validation", "addressing yourself")
        text = self._check_text(text, TEXT_MAX, "text")
        request_json, grant_json = self._check_request_payload(kind, request, actor)
        at = self._now()
        with self._lock, self._tx() as cur:
            self._cap_write(cur, actor)
            if kind == "message" and to == OPERATOR:
                self._cap_operator_message(cur, actor)
            cur.execute(
                "INSERT INTO board_entry (kind, from_, to_, text, status, request_json,"
                " grant_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (kind, actor, to, text, "open" if kind != "message" else "posted",
                 request_json, grant_json, at, at),
            )
            entry_id = cur.lastrowid
            entry = self._entry(cur, entry_id)
            self._log_row(cur, actor, entry_id, "post", None, entry, text_len=len(text))
        return entry

    def _check_request_payload(
        self, kind: str, request: dict | None, actor: str
    ) -> tuple[str | None, str | None]:
        if kind != "request":
            if request not in (None, {}):
                raise BoardError(422, "validation", "request payload fits kind=request only")
            return None, None
        if not isinstance(request, dict):
            raise BoardError(422, "validation", "kind=request needs a request object")
        action = request.get("action")
        if not isinstance(action, str) or not _ACTION_RE.match(action):
            raise BoardError(
                422, "validation",
                f"request.action must be snake_case of at most {ACTION_MAX} characters",
            )
        args = request.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict) or len(args) > ARGS_MAX_KEYS:
            raise BoardError(
                422, "validation", f"request.args must be a flat dict of at most {ARGS_MAX_KEYS} keys"
            )
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in args.items()):
            raise BoardError(422, "validation", "request.args maps strings to strings")
        why = request.get("why", "")
        if not isinstance(why, str) or len(why) > WHY_MAX:
            raise BoardError(422, "validation", f"request.why must be a string of at most {WHY_MAX} characters")
        allowed = request.get("allowed", list(_REQUEST_ANSWERS))
        if not isinstance(allowed, list) or not allowed or any(a not in _REQUEST_ANSWERS for a in allowed):
            raise BoardError(
                422, "validation", f"request.allowed must be a subset of {list(_REQUEST_ANSWERS)}"
            )
        grant = request.get("grant")
        if grant is not None:
            if actor != LAST_ORDER:
                raise BoardError(422, "validation", "grant belongs to Last Order's requests alone")
            if not isinstance(grant, dict) or grant.get("kind") not in ("egress", "secret") \
                    or not isinstance(grant.get("what"), str) or not grant["what"].strip():
                raise BoardError(422, "validation", "grant must be {kind: egress|secret, what}")
        cleaned = {"action": action, "args": args, "why": why, "allowed": allowed}
        return (
            json.dumps(cleaned, ensure_ascii=False),
            json.dumps(grant, ensure_ascii=False) if grant is not None else None,
        )

    # --- claim (section 4) ------------------------------------------------------------------

    def claim(self, actor: str, entry_id: int) -> dict:
        if actor_role(actor) == "node":
            self._require_live_node(actor)
        if actor_role(actor) not in ("node", LAST_ORDER):
            raise BoardError(403, "forbidden", f"{actor} does not take tasks")
        with self._lock, self._tx() as cur:
            row = self._entry_row(cur, entry_id)
            if row is None:
                raise BoardError(404, "unknown_entry", f"no board entry #{entry_id}")
            if row["kind"] != "task" or row["status"] != "open":
                if row["status"] in _TERMINAL or row["status"] == "taken":
                    raise BoardError(
                        409, "already_taken", f"#{entry_id} is {row['status']}"
                        + (f" by {row['claimed_by']}" if row["claimed_by"] else "")
                    )
                raise BoardError(409, "bad_transition", f"#{entry_id} is {row['status']}, not open")
            if row["to_"] != actor and row["to_"] != NET:
                raise BoardError(403, "forbidden", f"#{entry_id} is addressed to {row['to_']}")
            if actor_role(actor) == "node":
                held = cur.execute(
                    "SELECT COUNT(*) FROM board_entry WHERE kind='task'"
                    " AND status IN ('taken','needs_input') AND claimed_by=?",
                    (actor,),
                ).fetchone()[0]
                if held >= HELD_TASKS_MAX:
                    raise BoardError(
                        409, "too_many_taken",
                        f"{held} tasks held already, over the {HELD_TASKS_MAX} cap",
                    )
            self._cap_write(cur, actor)
            changed = cur.execute(
                "UPDATE board_entry SET status='taken', claimed_by=?, updated_at=?"
                " WHERE id=? AND status='open'",
                (actor, self._now(), entry_id),
            ).rowcount
            if changed != 1:  # the honest race answer, even under the process lock
                current = self._entry_row(cur, entry_id)
                raise BoardError(
                    409, "already_taken",
                    f"#{entry_id} was taken"
                    + (f" by {current['claimed_by']}" if current and current["claimed_by"] else ""),
                )
            self._log_row(
                cur, actor, entry_id, "claim",
                {"status": "open", "claimed_by": row["claimed_by"]},
                {"status": "taken", "claimed_by": actor},
            )
            return self._entry(cur, entry_id)

    # --- transitions (sections 3.2, 5.2) -----------------------------------------------------

    def transition(
        self, actor: str, entry_id: int, to_status: Any, text: Any = "", args: Any = None
    ) -> dict:
        if not isinstance(to_status, str):
            raise BoardError(422, "validation", "to_status must be a string")
        text = text if isinstance(text, str) else ""
        if actor_role(actor) == "node":
            self._require_live_node(actor)
        with self._lock, self._tx() as cur:
            row = self._entry_row(cur, entry_id)
            if row is None:
                raise BoardError(404, "unknown_entry", f"no board entry #{entry_id}")
            kind, status = row["kind"], row["status"]
            moves = _TASK_MOVES if kind == "task" else (
                _REQUEST_MOVES if kind == "request" else (
                    {("open", "acked"): "operator"} if kind == "alarm" else {}
                )
            )
            who = moves.get((status, to_status))
            if who is None:
                if status in _TERMINAL:
                    raise BoardError(
                        409, "bad_transition",
                        f"#{entry_id} is {status}, a final status; repeat work is a new entry",
                    )
                raise BoardError(409, "bad_transition", f"{status} -> {to_status} is not a move")
            if kind == "task" and to_status == "declined" and row["to_"] == NET:
                # section 3.2: declining a net task is a comment, others may still claim
                raise BoardError(
                    409, "bad_transition",
                    "a net task is not declined: leave a reply and leave it open",
                )
            self._check_move_rights(row, actor, who)
            needs_text = (kind == "task" and (status, to_status) in _TASK_TEXT_REQUIRED) or (
                kind == "request" and (status, to_status) in _REQUEST_TEXT_REQUIRED
            )
            if needs_text and not text.strip():
                raise BoardError(422, "validation", f"{status} -> {to_status} needs a text")
            if text.strip():
                self._check_text(text, REPLY_MAX, "text")
            if kind == "request":
                self._check_allowed_answer(row, to_status, args)
            # approve-with-edit (5.2): the corrected args are what the agent receives
            new_request = None
            if kind == "request" and to_status == "approved" and args is not None:
                if not isinstance(args, dict) or len(args) > ARGS_MAX_KEYS or any(
                    not isinstance(k, str) or not isinstance(v, str) for k, v in args.items()
                ):
                    raise BoardError(
                        422, "validation",
                        f"args must be a flat string dict of at most {ARGS_MAX_KEYS} keys",
                    )
                request = json.loads(row["request_json"]) if row["request_json"] else {}
                request["args"] = args
                new_request = json.dumps(request, ensure_ascii=False)
            if (kind, status, to_status) == ("task", "open", "taken") \
                    and actor_role(actor) == "node":
                held = cur.execute(
                    "SELECT COUNT(*) FROM board_entry WHERE kind='task'"
                    " AND status IN ('taken','needs_input') AND claimed_by=?",
                    (actor,),
                ).fetchone()[0]
                if held >= HELD_TASKS_MAX:
                    raise BoardError(
                        409, "too_many_taken",
                        f"{held} tasks held already, over the {HELD_TASKS_MAX} cap",
                    )
            self._cap_write(cur, actor)
            sets = {"status": to_status, "updated_at": self._now()}
            if kind == "task":
                if to_status == "taken":
                    sets["claimed_by"] = actor
                elif to_status == "open":  # released: back to the pool
                    sets["claimed_by"] = None
            if new_request is not None:
                sets["request_json"] = new_request
            before = {"status": status, "claimed_by": row["claimed_by"]}
            cur.execute(
                f"UPDATE board_entry SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                (*sets.values(), entry_id),
            )
            if text.strip():
                self._add_reply_locked(cur, entry_id, actor, text, (status, to_status))
            after = {"status": to_status, "claimed_by": sets.get("claimed_by", row["claimed_by"])}
            if new_request is not None:
                after["request"] = json.loads(new_request)
            op = "claim" if (kind, status, to_status) == ("task", "open", "taken") else "transition"
            self._log_row(cur, actor, entry_id, op, before, after, text_len=len(text) or None)
            return self._entry(cur, entry_id)

    @staticmethod
    def _allowed_answers(row: sqlite3.Row) -> list[str]:
        try:
            return list(json.loads(row["request_json"]).get("allowed", _REQUEST_ANSWERS))
        except (ValueError, TypeError, AttributeError):
            return list(_REQUEST_ANSWERS)

    def _check_allowed_answer(self, row: sqlite3.Row, to_status: str, args: Any) -> None:
        """The author offers which answers fit (5.1); anything else is a refusal."""
        if to_status == "canceled":
            return  # cancel is the author's own move, not an answer
        answer = {"approved": "approve", "denied": "deny", "answered": "respond"}[to_status]
        if args is not None and to_status == "approved":
            answer = "edit"
        if answer not in self._allowed_answers(row):
            raise BoardError(
                403, "forbidden", f"#{row['id']} allows {self._allowed_answers(row)} answers alone"
            )

    def _check_move_rights(self, row: sqlite3.Row, actor: str, who: str) -> None:
        role = actor_role(actor)
        me = actor in (row["from_"], row["claimed_by"])
        addressee = actor == row["to_"]
        ok = {
            "taker": (role in ("node", "last-order")) and (
                addressee or (row["to_"] == NET)
            ),
            "addressee": addressee,
            "author": actor == row["from_"],
            "author-or-operator": actor == row["from_"] or role == "operator",
            "addressee-or-operator": addressee or role == "operator",
            "operator": role == "operator",
        }[who]
        if not ok:
            raise BoardError(403, "forbidden", f"{actor} may not move #{row['id']} this way")

    # --- replies -------------------------------------------------------------------------------

    def reply(self, actor: str, entry_id: int, text: Any) -> dict:
        text = self._check_text(text, REPLY_MAX, "text")
        if actor_role(actor) == "node":
            self._require_live_node(actor)
        with self._lock, self._tx() as cur:
            row = self._entry_row(cur, entry_id)
            if row is None:
                raise BoardError(404, "unknown_entry", f"no board entry #{entry_id}")
            if actor_role(actor) != "operator":
                involved = actor in (row["from_"], row["to_"], row["claimed_by"]) or row["to_"] == NET
                if not involved:
                    raise BoardError(403, "forbidden", f"{actor} is not part of #{entry_id}")
            self._cap_write(cur, actor)
            reply = self._add_reply_locked(cur, entry_id, actor, text, None)
            self._log_row(
                cur, actor, entry_id, "reply", None, reply, text_len=len(text)
            )
            return reply

    def _add_reply_locked(
        self, cur: sqlite3.Cursor, entry_id: int, actor: str, text: str, change: tuple[str, str] | None
    ) -> dict:
        seq = cur.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM board_reply WHERE entry_id=?", (entry_id,)
        ).fetchone()[0]
        payload = {
            "seq": seq,
            "from": actor,
            "text": text,
            "status_change": {"from": change[0], "to": change[1]} if change else None,
            "at": self._now(),
        }
        cur.execute(
            "INSERT INTO board_reply (entry_id, seq, from_, text, status_change, at)"
            " VALUES (?,?,?,?,?,?)",
            (entry_id, seq, actor, text,
             json.dumps(payload["status_change"], ensure_ascii=False) if change else None,
             payload["at"]),
        )
        return payload

    # --- alarms (section 7) ----------------------------------------------------------------------

    def alarm(
        self,
        actor: str,
        alarm_type: Any,
        text: Any,
        *,
        slot: str = "",
        serial: int | None = None,
    ) -> dict:
        role = actor_role(actor)
        if alarm_type not in ALARM_TYPES:
            raise BoardError(
                422, "validation", f"alarm type must be one of {list(ALARM_TYPES)}, got {alarm_type!r}"
            )
        if alarm_type not in _ALARM_RIGHTS.get(role, set()):
            raise BoardError(403, "forbidden", f"{actor} does not raise {alarm_type}")
        if role == "node":
            self._require_live_node(actor)
        text = self._check_text(text, TEXT_MAX, "text")
        at = self._now()
        with self._lock, self._tx() as cur:
            open_same = cur.execute(
                "SELECT * FROM board_entry WHERE kind='alarm' AND status='open'"
                " AND alarm_type=? AND from_=? ORDER BY id DESC LIMIT 1",
                (alarm_type, actor),
            ).fetchone()
            if open_same is not None and self._within_alarm_window(open_same["alarm_at"]):
                count = open_same["alarm_count"] + 1
                cur.execute(
                    "UPDATE board_entry SET alarm_count=?, alarm_at=?, updated_at=?, text=? WHERE id=?",
                    (count, at, at, text, open_same["id"]),
                )
                self._log_row(
                    cur, actor, open_same["id"], "alarm_repeat",
                    {"count": open_same["alarm_count"]}, {"count": count}, text_len=len(text),
                )
                return self._entry(cur, open_same["id"])
            cur.execute(
                "INSERT INTO board_entry (kind, from_, to_, text, status, alarm_type, alarm_count,"
                " alarm_at, alarm_slot, alarm_serial, created_at, updated_at)"
                " VALUES ('alarm', ?, 'operator', ?, 'open', ?, 1, ?, ?, ?, ?, ?)",
                (actor, text, alarm_type, at, slot or None, serial, at, at),
            )
            entry_id = cur.lastrowid
            entry = self._entry(cur, entry_id)
            self._log_row(cur, actor, entry_id, "alarm", None, entry, text_len=len(text))
        return entry

    def _within_alarm_window(self, alarm_at: str | None) -> bool:
        if not alarm_at:
            return False
        try:
            return (self._clock() - parse_utc(alarm_at)).total_seconds() <= ALARM_REPEAT_WINDOW_S
        except ValueError:
            return False

    # --- reads --------------------------------------------------------------------------------------

    def entries(
        self,
        *,
        to: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        limit = max(1, min(int(limit), ENTRIES_LIMIT_MAX))
        queries, params = ["1=1"], []
        if to:
            queries.append("to_=?")
            params.append(to)
        if kind:
            queries.append("kind=?")
            params.append(kind)
        if status:
            queries.append("status=?")
            params.append(status)
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM board_entry WHERE {' AND '.join(queries)}"
                " ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
            return [self._to_json(self._conn, r, with_thread=False) for r in rows]

    def entry_for(self, actor: str, entry_id: int) -> dict:
        """One entry for a participant (author, addressee, taker) or the operator."""
        with self._lock:
            row = self._entry_row(self._conn, entry_id)
            if row is None:
                raise BoardError(404, "unknown_entry", f"no board entry #{entry_id}")
            if actor_role(actor) != "operator":
                involved = (
                    actor in (row["from_"], row["to_"], row["claimed_by"]) or row["to_"] == NET
                )
                if not involved:
                    raise BoardError(403, "forbidden", f"{actor} is not part of #{entry_id}")
            return self._to_json(self._conn, row, with_thread=True)

    # --- the MCP glance (section 10) ------------------------------------------------------------------

    def glance(self, actor: str, filter_: Any) -> dict:
        """`board(filter)`: <= LIST_LIMIT one-line entries, or one full entry by id."""
        if isinstance(filter_, int) or (isinstance(filter_, str) and filter_.strip().isdigit()):
            with self._lock:
                return {"entry": self.entry_for(actor, int(filter_))}
        if not isinstance(filter_, str) or filter_.strip() not in ("mine", "net", "open", "all"):
            raise BoardError(
                422, "validation", "filter must be mine, net, open, all or an entry id"
            )
        where, params = {
            "mine": (
                "(from_=? OR to_=? OR claimed_by=?)",
                (actor, actor, actor),
            ),
            "net": ("to_='net'", ()),
            "open": ("status='open'", ()),
            "all": ("1=1", ()),
        }[filter_.strip()]
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM board_entry WHERE {where} ORDER BY id DESC LIMIT ?",
                (*params, LIST_LIMIT),
            ).fetchall()
            lines = [
                f"#{r['id']} {r['kind']} {r['from_']}->{r['to_']} {r['status']}:"
                f" {r['text'][:LIST_TEXT]}"
                for r in rows
            ]
            return {"entries": lines}

    # --- the feed and cursors (section 9) ----------------------------------------------------------------

    def feed(self, who: str, since: Any = None) -> dict:
        """Events after `since` for one reader; the first read of a life is the snapshot."""
        if not isinstance(since, int) or isinstance(since, bool) or since < 0:
            try:
                since = int(since)
            except (TypeError, ValueError):
                since = None
        with self._lock:
            cursor = self._conn.execute(
                "SELECT seq FROM board_cursor WHERE who=?", (who,)
            ).fetchone()
            if cursor is None and who != OPERATOR:
                return self._first_feed(who)
            effective = since if since is not None else (cursor[0] if cursor else 0)
            return {"events": self._feed_events(who, effective), "cursor": effective, "snapshot": False}

    def _first_feed(self, who: str) -> dict:
        """A new life starts at "now": open net (and own) tasks once, no backlog."""
        top = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM board_log").fetchone()[0]
        self._conn.execute(
            "INSERT INTO board_cursor (who, seq) VALUES (?, ?)"
            " ON CONFLICT(who) DO UPDATE SET seq=MAX(seq, excluded.seq)",
            (who, top),
        )
        rows = self._conn.execute(
            "SELECT * FROM board_entry WHERE kind='task' AND status='open'"
            " AND to_ IN (?, ?) AND from_<>? ORDER BY id",
            (NET, who, who),
        ).fetchall()
        events = [
            {
                "seq": top,
                "at": r["updated_at"],
                "actor": r["from_"],
                "entry_id": r["id"],
                "op": "snapshot",
                "before": None,
                "after": None,
                "entry": self._brief(self._conn, r),
            }
            for r in rows
        ]
        return {"events": events, "cursor": top, "snapshot": True}

    def _feed_events(self, who: str, since: int) -> list[dict]:
        agent = who not in (OPERATOR, "operator")
        if agent:
            where = (
                "(l.op='post' AND e.to_ IN (?, 'net') AND l.actor<>?)"
                " OR (l.op IN ('reply','transition','claim','readdress')"
                " AND (e.from_=? OR e.claimed_by=?) AND l.actor<>?)"
            )
            params = (who, who, who, who, who)
        else:
            where, params = "1=1", ()
        rows = self._conn.execute(
            "SELECT l.*, e.from_ AS e_from, e.to_ AS e_to, e.claimed_by AS e_claimed_by,"
            " e.kind AS e_kind, e.status AS e_status, e.text AS e_text"
            " FROM board_log l JOIN board_entry e ON e.id=l.entry_id"
            f" WHERE l.seq>? AND {where} ORDER BY l.seq LIMIT ?",
            (since, *params, FEED_LIMIT),
        ).fetchall()
        events = []
        for r in rows:
            events.append(
                {
                    "seq": r["seq"],
                    "at": r["at"],
                    "actor": r["actor"],
                    "entry_id": r["entry_id"],
                    "op": r["op"],
                    "before": json.loads(r["before"]) if r["before"] else None,
                    "after": json.loads(r["after"]) if r["after"] else None,
                    "entry": {
                        "kind": r["e_kind"],
                        "from": r["e_from"],
                        "to": r["e_to"],
                        "status": r["e_status"],
                        "claimed_by": r["e_claimed_by"],
                        "text": r["e_text"],
                    },
                }
            )
        return events

    @staticmethod
    def _brief(cur: sqlite3.Cursor, row: sqlite3.Row) -> dict:
        return {
            "kind": row["kind"],
            "from": row["from_"],
            "to": row["to_"],
            "status": row["status"],
            "claimed_by": row["claimed_by"],
            "text": row["text"],
        }

    def ack(self, who: str, seq: Any) -> None:
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise BoardError(422, "validation", "seq must be a non-negative integer")
        with self._lock:
            self._conn.execute(
                "INSERT INTO board_cursor (who, seq) VALUES (?, ?)"
                " ON CONFLICT(who) DO UPDATE SET seq=MAX(seq, excluded.seq)",
                (who, seq),
            )

    def log_rows(self, since: int = 0, limit: int = FEED_LIMIT) -> list[dict]:
        """Unfiltered journal rows after `seq` — the SSE stream of the operator page."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM board_log WHERE seq>? ORDER BY seq LIMIT ?", (since, limit)
            ).fetchall()
            return [
                {
                    "seq": r["seq"],
                    "at": r["at"],
                    "actor": r["actor"],
                    "entry_id": r["entry_id"],
                    "op": r["op"],
                    "before": json.loads(r["before"]) if r["before"] else None,
                    "after": json.loads(r["after"]) if r["after"] else None,
                }
                for r in rows
            ]

    def max_seq(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM board_log").fetchone()[0]

    # --- death (section 4.3) -------------------------------------------------------------------------

    def on_death(self, serial: int) -> None:
        """Free the dead node's hold on the board — one transaction (section 4.3).

        Idempotent: a body-death mark followed by a confirmed kill (or a retire) sweeps
        an already-swept board and changes nothing.
        """
        dead = str(serial)
        naming = naming_preset(env("NAME_PRESET") or "lab")
        freed_line = f"{node_name(naming, serial)} умер, задача свободна"
        with self._lock, self._tx() as cur:
            taken = cur.execute(
                "SELECT id, status FROM board_entry WHERE kind='task'"
                " AND status IN ('taken','needs_input') AND claimed_by=? ORDER BY id",
                (dead,),
            ).fetchall()
            for row in taken:
                cur.execute(
                    "UPDATE board_entry SET status='open', claimed_by=NULL, updated_at=? WHERE id=?",
                    (self._now(), row["id"]),
                )
                self._add_reply_locked(cur, row["id"], SYSTEM, freed_line,
                                       (row["status"], "open"))
                self._log_row(
                    cur, SYSTEM, row["id"], "transition",
                    {"status": row["status"], "claimed_by": dead},
                    {"status": "open", "claimed_by": None},
                    text_len=len(freed_line),
                )
            personal = cur.execute(
                "SELECT id FROM board_entry WHERE kind='task' AND status='open' AND to_=?"
                " ORDER BY id",
                (dead,),
            ).fetchall()
            for row in personal:
                cur.execute(
                    "UPDATE board_entry SET to_='net', readdressed_from=?, updated_at=? WHERE id=?",
                    (dead, self._now(), row["id"]),
                )
                self._log_row(
                    cur, SYSTEM, row["id"], "readdress", {"to": dead}, {"to": NET, "from": dead}
                )
            requests = cur.execute(
                "SELECT id FROM board_entry WHERE kind='request' AND status='open' AND from_=?"
                " ORDER BY id",
                (dead,),
            ).fetchall()
            for row in requests:
                cur.execute(
                    "UPDATE board_entry SET status='canceled', updated_at=? WHERE id=?",
                    (self._now(), row["id"]),
                )
                self._add_reply_locked(cur, row["id"], SYSTEM, "poster_dead", ("open", "canceled"))
                self._log_row(
                    cur, SYSTEM, row["id"], "transition", {"status": "open"},
                    {"status": "canceled"}, text_len=len("poster_dead"),
                )
            if taken or personal or requests:
                self._log.event(
                    "board.death",
                    {"serial": serial, "freed": len(taken), "readdressed": len(personal),
                     "canceled": len(requests)},
                )



__all__ = [
    "ALARM_TYPES",
    "AGENT_MUTATIONS_PER_LIFE",
    "Board",
    "BoardError",
    "LAST_ORDER",
    "LIST_LIMIT",
    "LIST_TEXT",
    "NET",
    "OPERATOR",
    "OPERATOR_MSGS_PER_LIFE",
    "SYSTEM",
    "actor_role",
    "actor_serial",
]
