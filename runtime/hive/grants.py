"""The grant vault of the network (ADR-041, TASKBOARD.md section 5.3; T-046).

A grant is a record hive creates when the OPERATOR approves a board request
carrying a `grant` ({kind: egress|secret, what}). The board approval alone grants
nothing (A2A auth-required): this store is the single source of what is open —
the egress proxy asks `egress_domains()`, the tool process pulls a secret with
`secret_of()`. Secrets live in `grants.sqlite` inside RUN_DIR (the hive-run
volume): never in git, never in the knowledge base, never in any answer, log or
event — only their length rides the journal.

Revocation flips the record to `revoked` (the board entry stays `approved` with
a reply, TASKBOARD.md 5.3) and the domain closes on the proxy's next check —
no rebuild, no restart. A later approval of the same (kind, what) reactivates it.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.hive.lifecycle import HiveLog

SECRET_NAME_MAX = 64
DOMAIN_MAX = 253
KINDS = ("egress", "secret")


class GrantError(Exception):
    """The refused grant call; carries the HTTP mapping (status, kind) with it."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message


class Grants:
    """Grant records, secrets and the grant journal behind one lock + SQLite."""

    def __init__(
        self,
        path: Path | str,
        *,
        log: HiveLog,
        clock: Callable[[], datetime],
    ):
        self._path = Path(path)
        self._log = log
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, timeout=10.0, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._create()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _create(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS grant (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    what TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    revoked_at TEXT,
                    revoked_by TEXT,
                    secret TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS grant_pair ON grant(kind, what);
                CREATE TABLE IF NOT EXISTS grant_log (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    grant_id INTEGER NOT NULL,
                    op TEXT NOT NULL,
                    note TEXT
                );
                """
            )

    def _now(self) -> str:
        return self._clock().isoformat()

    # --- writes --------------------------------------------------------------------------

    def approve(
        self,
        *,
        entry_id: int,
        kind: str,
        what: str,
        actor: str,
        secret: str | None = None,
    ) -> dict:
        """Create or reactivate the grant of one approved board request."""
        if kind not in KINDS:
            raise GrantError(422, "validation", f"grant kind must be one of {KINDS}")
        if not isinstance(what, str) or not what.strip():
            raise GrantError(422, "validation", "grant what must be a non-empty string")
        if kind == "secret" and (not isinstance(secret, str) or not secret.strip()):
            raise GrantError(422, "validation", "a secret grant needs the secret value")
        if kind == "egress" and secret:
            raise GrantError(422, "validation", "an egress grant carries no secret")
        at = self._now()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT id, status FROM grant WHERE kind=? AND what=?", (kind, what)
                ).fetchone()
                if row is None:
                    cursor = self._conn.execute(
                        "INSERT INTO grant (entry_id, kind, what, status, approved_at,"
                        " approved_by, secret) VALUES (?,?,?,?,?,?,?)",
                        (entry_id, kind, what, "approved", at, actor, secret),
                    )
                    grant_id = cursor.lastrowid
                else:  # a re-approval reactivates the pair and turns the secret over
                    grant_id = row["id"]
                    self._conn.execute(
                        "UPDATE grant SET status='approved', approved_at=?, approved_by=?,"
                        " revoked_at=NULL, revoked_by=NULL, secret=?, entry_id=? WHERE id=?",
                        (at, actor, secret, entry_id, grant_id),
                    )
                self._conn.execute(
                    "INSERT INTO grant_log (at, actor, grant_id, op, note) VALUES (?,?,?,?,?)",
                    (at, actor, grant_id, "approve", json.dumps({"entry": entry_id})),
                )
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            # the event carries names and lengths alone; the value never leaves the vault
            self._log.event("grants.approve", {
                "kind": kind, "what": what, "entry": entry_id,
                **({"secret_len": len(secret)} if secret else {}),
            })
            return self._record(grant_id)

    def revoke(self, *, kind: str, what: str, actor: str) -> dict:
        at = self._now()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, status FROM grant WHERE kind=? AND what=?", (kind, what)
            ).fetchone()
            if row is None:
                raise GrantError(404, "unknown_grant", f"no {kind} grant for {what!r}")
            if row["status"] == "revoked":
                return self._record(row["id"])
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE grant SET status='revoked', revoked_at=?, revoked_by=? WHERE id=?",
                    (at, actor, row["id"]),
                )
                self._conn.execute(
                    "INSERT INTO grant_log (at, actor, grant_id, op, note) VALUES (?,?,?,?,?)",
                    (at, actor, row["id"], "revoke", None),
                )
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            self._log.event("grants.revoke", {"kind": kind, "what": what})
            return self._record(row["id"])

    # --- reads --------------------------------------------------------------------------

    def _strip(self, row: sqlite3.Row) -> dict:
        """The public shape: everything about the grant except the secret value."""
        return {
            "id": row["id"],
            "entry_id": row["entry_id"],
            "kind": row["kind"],
            "what": row["what"],
            "status": row["status"],
            "approved_at": row["approved_at"],
            "approved_by": row["approved_by"],
            "revoked_at": row["revoked_at"],
        }

    def _record(self, grant_id: int) -> dict:
        row = self._conn.execute("SELECT * FROM grant WHERE id=?", (grant_id,)).fetchone()
        return self._strip(row)

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM grant ORDER BY id").fetchall()
            return [self._strip(r) for r in rows]

    def egress_domains(self) -> list[str]:
        """What the proxy may open right now (the active egress grants alone)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT what FROM grant WHERE kind='egress' AND status='approved' ORDER BY what"
            ).fetchall()
            return [r["what"] for r in rows]

    def secret_of(self, name: Any) -> str:
        """The value of an ACTIVE secret grant — the tool process's call alone."""
        if not isinstance(name, str) or not name.strip():
            raise GrantError(422, "validation", "name must be a secret grant name")
        with self._lock:
            row = self._conn.execute(
                "SELECT secret, status FROM grant WHERE kind='secret' AND what=?",
                (name.strip(),),
            ).fetchone()
            if row is None or row["status"] != "approved" or not row["secret"]:
                raise GrantError(404, "unknown_grant", f"no active secret grant {name!r}")
            return row["secret"]


__all__ = ["DOMAIN_MAX", "GrantError", "Grants", "KINDS", "SECRET_NAME_MAX"]
