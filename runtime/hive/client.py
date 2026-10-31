"""HiveClient: the mind's side of the hive HTTP API (ADR-024, NETWORK.md section 9).

Implements the `Memory`, `NodeRegistry` and `NodeComms` ports of `runtime/ports.py`
over HTTP, so a mind depends on interfaces, not on the service. The wire codecs at
the top of this file are the single source of the JSON shape: the server
(`runtime/hive/app.py`) imports them too, which keeps payload, endpoint and client
from drifting apart. Payloads are the frozen dataclasses of `runtime/ports.py`;
tuples become JSON lists and are rebuilt as tuples on the way back.

Offline contract (NETWORK.md section 9, reviews P1-3/P1-7, R1-1/R1-2):
- connect errors are retried with a growing pause; a read timeout is not retried
  immediately (the request may have landed — a duplicate `send` is possible and
  accepted: a message is an observation, and the store-side writes are idempotent);
- while hive is unreachable, `submit_candidate`, `submit_vote`, `send` and
  `store_core_memory` go to an on-disk buffer (`run/hive_outbox.jsonl`, one JSON
  per line, flushed strictly in order before any next call and always before
  `retire` — a buffered write that only lands after its node retired is dropped
  with a client event, never silently). If the buffer cannot be drained, nothing
  overtakes it: a new buffered write joins the end of the queue, and the direct
  writes (`birth`, `request_death`, `retire`) refuse with `HiveUnavailable`;
- reads answer from the last successful response with `client.stale = True`
  (payload dataclasses are frozen, so the mark cannot ride the value itself);
  a read with no cached answer raises `HiveUnavailable`. Two reads are never
  cached: `inbox` (consuming — offline it answers [] with stale=True, so delivered
  messages are not replayed as new) and `chatter_turn` (a permission — offline it
  answers None, so no turn is granted around the director's limit, ADR-022);
- `birth`, `request_death`, `retire` are never buffered: the serial, the kill and
  the will belong to hive alone; when it is down they raise `HiveUnavailable` and
  the mind falls back to its local emergency path (T-013).
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Sequence

import httpx

from runtime.config import env
from runtime.lastorder.persona import Inheritance
from runtime.lastorder.traits import Traits
from runtime.ports import (
    BirthRecord,
    ChatterTurn,
    KnowledgeCandidate,
    KnowledgeEntry,
    KnowledgeSlice,
    NetworkGoal,
    NodeBrief,
    NodeMessage,
    Vote,
)


class HiveUnavailable(RuntimeError):
    """Hive did not answer within the retries; the caller falls back (section 9)."""


class HiveRejected(RuntimeError):
    """Hive answered a permanent 4xx; retrying the same payload cannot help."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(f"{status} {kind}: {message}")
        self.status = status
        self.kind = kind
        self.message = message


# --- wire codecs of the port payloads (shared with runtime/hive/app.py) ------------------

_INHERITANCE_FIELDS = (
    "predecessors_summary",
    "active_nodes_summary",
    "inherited_notes",
    "collective_terms",
    "knowledge_catalog",
)


class WireError(ValueError):
    """A JSON body does not match the dataclass it claims to be."""


def _want(data: Any, keys: tuple[str, ...]) -> dict:
    if not isinstance(data, dict) or any(key not in data for key in keys):
        raise WireError(f"expected an object with keys {keys}")
    return data


def _int(data: dict, key: str, minimum: int = 0) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise WireError(f"{key} must be an integer >= {minimum}")
    return value


def _str(data: dict, key: str, can_be_empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not value.strip() and not can_be_empty):
        raise WireError(f"{key} must be a non-empty string")
    return value


def _bool(data: dict, key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise WireError(f"{key} must be a boolean")
    return value


def _str_tuple(data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key, ())
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) for v in value):
        raise WireError(f"{key} must be a list of strings")
    return tuple(value)


def traits_from_json(data: Any) -> Traits:
    data = _want(data, (*Traits.__dataclass_fields__,))
    numbers = {}
    for name in ("bravery", "greed", "talkativeness", "trust_in_chat_and_nodes", "superstition"):
        value = _int(data, name, 0)
        if value > 10:
            raise WireError(f"{name} must be an integer 0-10")
        numbers[name] = value
    return Traits(quirk=_str(data, "quirk"), seed=_int(data, "seed", 0), **numbers)


def traits_to_json(traits: Traits) -> dict:
    return traits.as_dict()


def candidate_from_json(data: Any) -> KnowledgeCandidate:
    data = _want(data, ("kind", "claim"))
    return KnowledgeCandidate(
        kind=_str(data, "kind"),
        claim=_str(data, "claim"),
        about=_str_tuple(data, "about"),
        evidence_event_ids=_str_tuple(data, "evidence_event_ids"),
    )


def candidate_to_json(candidate: KnowledgeCandidate) -> dict:
    return {
        "kind": candidate.kind,
        "claim": candidate.claim,
        "about": list(candidate.about),
        "evidence_event_ids": list(candidate.evidence_event_ids),
    }


def vote_from_json(data: Any) -> Vote:
    data = _want(data, ("entry_id", "accept"))
    return Vote(entry_id=_str(data, "entry_id"), accept=_bool(data, "accept"), why=data.get("why", ""))


def vote_to_json(vote: Vote) -> dict:
    return {"entry_id": vote.entry_id, "accept": vote.accept, "why": vote.why}


def birth_record_from_json(data: Any) -> BirthRecord:
    data = _want(data, ("serial", "born_at", "ttl_min"))
    return BirthRecord(
        serial=_int(data, "serial", 1), born_at=_str(data, "born_at"), ttl_min=_int(data, "ttl_min", 1)
    )


def birth_record_to_json(record: BirthRecord) -> dict:
    return {"serial": record.serial, "born_at": record.born_at, "ttl_min": record.ttl_min}


def node_brief_from_json(data: Any) -> NodeBrief:
    data = _want(data, ("serial", "slot"))
    return NodeBrief(serial=_int(data, "serial", 1), slot=_str(data, "slot"))


def node_brief_to_json(brief: NodeBrief) -> dict:
    return {"serial": brief.serial, "slot": brief.slot}


def message_from_json(data: Any) -> NodeMessage:
    data = _want(data, ("from_serial", "to_serial", "text"))
    return NodeMessage(
        from_serial=_int(data, "from_serial", 1),
        to_serial=_int(data, "to_serial", 1),
        text=data["text"] if isinstance(data.get("text"), str) else "",
        ts=data.get("ts", "") if isinstance(data.get("ts", ""), str) else "",
    )


def message_to_json(message: NodeMessage) -> dict:
    return {
        "from_serial": message.from_serial,
        "to_serial": message.to_serial,
        "text": message.text,
        "ts": message.ts,
    }


def chatter_turn_from_json(data: Any) -> ChatterTurn | None:
    if data is None:
        return None
    data = _want(data, ("topic", "max_lines"))
    return ChatterTurn(topic=_str(data, "topic"), max_lines=_int(data, "max_lines", 1))


def chatter_turn_to_json(turn: ChatterTurn | None) -> dict | None:
    if turn is None:
        return None
    return {"topic": turn.topic, "max_lines": turn.max_lines}


def _entry_from_json(data: Any) -> KnowledgeEntry:
    data = _want(data, ("id", "kind", "claim", "status"))
    return KnowledgeEntry(
        id=_str(data, "id"),
        kind=_str(data, "kind"),
        claim=data["claim"] if isinstance(data.get("claim"), str) else "",
        status=_str(data, "status"),
        about=_str_tuple(data, "about"),
    )


def _entry_to_json(entry: KnowledgeEntry) -> dict:
    return {
        "id": entry.id,
        "kind": entry.kind,
        "claim": entry.claim,
        "status": entry.status,
        "about": list(entry.about),
    }


# The public face of the codec above (T-028): the network MCP renders matched records
# with it too, and a private import across modules would hide that dependency.
entry_to_json = _entry_to_json


def _goal_from_json(data: Any) -> NetworkGoal:
    data = _want(data, ("text",))
    return NetworkGoal(
        text=_str(data, "text", can_be_empty=True) or "",
        why=data.get("why", "") if isinstance(data.get("why", ""), str) else "",
    )


def slice_from_json(data: Any) -> KnowledgeSlice:
    data = _want(data, ("catalog",))
    entries = data.get("about", []) + data.get("fresh", []) + data.get("pending_votes", [])
    if not isinstance(entries, list):
        raise WireError("about/fresh/pending_votes must be lists")
    goals = data.get("network_goals", [])
    if not isinstance(goals, list):
        raise WireError("network_goals must be a list")
    catalog = data.get("catalog")
    if not isinstance(catalog, str):
        raise WireError("catalog must be a string")
    return KnowledgeSlice(
        catalog=catalog,
        network_goals=tuple(_goal_from_json(g) for g in goals),
        about=tuple(_entry_from_json(e) for e in data.get("about", [])),
        fresh=tuple(_entry_from_json(e) for e in data.get("fresh", [])),
        pending_votes=tuple(_entry_from_json(e) for e in data.get("pending_votes", [])),
    )


def slice_to_json(sl: KnowledgeSlice) -> dict:
    return {
        "catalog": sl.catalog,
        "network_goals": [{"text": g.text, "why": g.why} for g in sl.network_goals],
        "about": [_entry_to_json(e) for e in sl.about],
        "fresh": [_entry_to_json(e) for e in sl.fresh],
        "pending_votes": [_entry_to_json(e) for e in sl.pending_votes],
    }


def inheritance_from_json(data: Any) -> Inheritance:
    data = _want(data, _INHERITANCE_FIELDS)
    return Inheritance(**{key: data[key] if isinstance(data[key], str) else "" for key in _INHERITANCE_FIELDS})


def inheritance_to_json(inheritance: Inheritance) -> dict:
    return {key: getattr(inheritance, key) for key in _INHERITANCE_FIELDS}


# --- the client ---------------------------------------------------------------------------


class HiveClient:
    """`Memory` + `NodeRegistry` + `NodeComms` over HTTP, with the section 9 fallbacks."""

    def __init__(
        self,
        base_url: str = "http://hive:8000",
        *,
        transport: httpx.Client | None = None,
        run_dir: Path | str | None = None,
        timeout_s: float = 5.0,
        retries: int = 2,
        backoff_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        runner_key: str = "",
        lo_key: str | None = None,
    ):
        self._http = transport if transport is not None else httpx.Client(
            base_url=base_url, timeout=timeout_s
        )
        # With the runner-key gates configured on the hive (T-027), the mind's writes
        # (retire_stuck's request_death) must carry the slot's runner key too; empty
        # leaves the client as it was (review R2-3).
        self._runner_key = runner_key
        # The admin variant of the board alarm (T-042) speaks with the Last Order key
        # over plain HTTP; the sisters' clients never set it.
        self._lo_key = lo_key if lo_key is not None else (env("HIVE_MCP_LO_KEY") or "")
        self._run_dir = Path(run_dir if run_dir is not None else (env("RUN_DIR") or "run"))
        self._outbox_path = self._run_dir / "hive_outbox.jsonl"
        self._events_path = self._run_dir / "hive_client_events.jsonl"
        self._retries = retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._lock = threading.RLock()
        self._cache: dict[str, Any] = {}
        self.stale = False  # True after a read answered from the last successful response

    def close(self) -> None:
        self._http.close()

    # --- transport ---------------------------------------------------------------------

    _CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

    def _request(
        self,
        method: str,
        path: str,
        payload: Any,
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        last = "hive unreachable"
        headers = {"X-Runner-Key": self._runner_key} if self._runner_key else {}
        if extra_headers:
            headers.update(extra_headers)
        headers = headers or None
        for attempt in range(self._retries + 1):
            try:
                response = (
                    self._http.post(path, json=payload, headers=headers)
                    if method == "post"
                    else self._http.get(path, headers=headers)
                )
            except HiveClient._CONNECT_ERRORS as exc:  # certainly not delivered: retry
                last = f"{type(exc).__name__}: {exc}"
                self._sleep(self._backoff_s * (2**attempt))
                continue
            except httpx.HTTPError as exc:  # read timeout and friends: no immediate retry
                raise HiveUnavailable(f"{type(exc).__name__}: {exc}") from exc
            if response.status_code >= 500:  # transient store trouble (review P1-5)
                last = f"HTTP {response.status_code}"
                self._sleep(self._backoff_s * (2**attempt))
                continue
            if response.status_code >= 400:
                error = {}
                try:
                    body = response.json()
                    if isinstance(body, dict) and isinstance(body.get("error"), dict):
                        error = body["error"]
                except ValueError:
                    pass
                raise HiveRejected(
                    response.status_code,
                    str(error.get("kind", "http")),
                    str(error.get("message", response.text[:200])),
                )
            return response
        raise HiveUnavailable(last)

    def _client_event(self, type: str, payload: dict) -> None:
        self._events_path.parent.mkdir(parents=True, exist_ok=True)
        with self._events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": type, **payload}, ensure_ascii=False) + "\n")

    def _append_outbox(self, method: str, path: str, payload: Any) -> None:
        self._outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with self._outbox_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"method": method, "path": path, "payload": payload}) + "\n")

    def _outbox_lines(self) -> list[str]:
        if not self._outbox_path.exists():
            return []
        return [line for line in self._outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _write_outbox(self, lines: list[str]) -> None:
        tmp = self._outbox_path.with_name(self._outbox_path.name + ".tmp")
        tmp.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        tmp.replace(self._outbox_path)

    def _flush_locked(self) -> bool:
        """Deliver buffered writes strictly in order; 4xx drops with an event, the
        rest of the file waits for the next call (review P1-3/P1-5). True when the
        buffer is empty afterwards."""
        lines = self._outbox_lines()
        if not lines:
            return True
        remaining = True
        kept: list[str] = []
        for line in lines:
            if not remaining:
                kept.append(line)
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                self._client_event("outbox.drop", {"why": "corrupt", "line": line[:200]})
                continue
            try:
                self._request(entry["method"], entry["path"], entry["payload"])
            except HiveRejected as exc:
                self._client_event(
                    "outbox.drop",
                    {"why": "rejected", "status": exc.status, "kind": exc.kind, "path": entry["path"]},
                )
            except HiveUnavailable:
                remaining = False
                kept.append(line)
        self._write_outbox(kept)
        return not kept

    def _call(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        decode: Callable[[httpx.Response], Any] | None = None,
        cache_key: str | None = None,
        buffered: bool = False,
        offline: Any | None = None,
        direct: bool = False,
        extra_headers: Mapping[str, str] | None = None,
    ) -> Any:
        with self._lock:
            flushed = self._flush_locked()
            if not flushed:
                # Order first (review R1-2): nothing new may overtake the buffer. A
                # buffered write joins the end of the queue; a direct write (birth,
                # request_death, retire — `direct`, which matters for birth: it has
                # a `decode` but must still refuse) never goes past older ones.
                if buffered:
                    self._append_outbox(method, path, payload)
                    return None
                if direct or decode is None:
                    raise HiveUnavailable("hive outbox not flushed; write order preserved")
            try:
                response = self._request(method, path, payload, extra_headers=extra_headers)
            except HiveUnavailable:
                if buffered:
                    self._append_outbox(method, path, payload)
                    return None
                if offline is not None:  # a consuming read answers empty, never from cache
                    self.stale = True
                    return offline()
                if decode is not None and cache_key is not None and cache_key in self._cache:
                    self.stale = True
                    return self._cache[cache_key]
                raise
            self.stale = False
            if decode is None:
                return None
            value = decode(response)
            if cache_key is not None:
                self._cache[cache_key] = value
            return value

    @staticmethod
    def _text(response: httpx.Response) -> str:
        value = response.json()
        return value if isinstance(value, str) else ""

    # --- Memory ------------------------------------------------------------------------

    def inheritance(self, serial: int) -> Inheritance:
        return self._call(
            "post",
            "/memory/inheritance",
            {"serial": serial},
            decode=lambda r: inheritance_from_json(r.json()),
            cache_key=f"inheritance:{serial}",
        )

    def knowledge(self, serial: int, handles: Sequence[str]) -> KnowledgeSlice:
        return self._call(
            "post",
            "/memory/knowledge",
            {"serial": serial, "handles": list(handles)},
            decode=lambda r: slice_from_json(r.json()),
            cache_key=f"knowledge:{serial}:{tuple(handles)}",
        )

    def core_memory(self, serial: int) -> str:
        return self._call(
            "post",
            "/memory/core_memory",
            {"serial": serial},
            decode=self._text,
            cache_key=f"core_memory:{serial}",
        )

    def store_core_memory(self, serial: int, text: str) -> None:
        self._call("post", "/memory/store_core_memory", {"serial": serial, "text": text}, buffered=True)

    def submit_candidate(self, serial: int, candidate: KnowledgeCandidate) -> None:
        self._call(
            "post",
            "/memory/submit_candidate",
            {"serial": serial, "candidate": candidate_to_json(candidate)},
            buffered=True,
        )

    def submit_vote(self, serial: int, vote: Vote) -> None:
        self._call(
            "post",
            "/memory/submit_vote",
            {"serial": serial, "vote": vote_to_json(vote)},
            buffered=True,
        )

    # --- NodeRegistry --------------------------------------------------------------------

    def birth(self, slot: str, traits: Traits) -> BirthRecord:
        # `direct`: a birth must not overtake the buffer either — the lost-close of
        # the old node in this slot would 409-drop its queued writes (checker C-1).
        return self._call(
            "post",
            "/registry/birth",
            {"slot": slot, "traits": traits_to_json(traits)},
            decode=lambda r: birth_record_from_json(r.json()),
            direct=True,
        )

    def request_death(self, serial: int, cause: str) -> None:
        self._call("post", "/registry/request_death", {"serial": serial, "cause": cause}, direct=True)

    def retire(self, serial: int, cause: str, will_text: str) -> None:
        self._call(
            "post",
            "/registry/retire",
            {"serial": serial, "cause": cause, "will_text": will_text},
            direct=True,
        )

    def alive(self) -> Sequence[NodeBrief]:
        return self._call(
            "get",
            "/registry/alive",
            decode=lambda r: [node_brief_from_json(item) for item in r.json()],
            cache_key="alive",
        )

    # --- NodeComms -------------------------------------------------------------------------

    def inbox(self, serial: int) -> Sequence[NodeMessage]:
        # A consuming read (review R1-1): never cached — offline answers [] with
        # stale=True instead of replaying already delivered messages as new.
        return self._call(
            "post",
            "/comms/inbox",
            {"serial": serial},
            decode=lambda r: [message_from_json(item) for item in r.json()],
            offline=list,
        )

    def send(self, message: NodeMessage) -> None:
        self._call("post", "/comms/send", {"message": message_to_json(message)}, buffered=True)

    def chatter_turn(self, serial: int) -> ChatterTurn | None:
        # Never cached either (review R1-1): a replayed turn would grant chatter
        # every step around the director's limit (ADR-022). Offline -> None.
        return self._call(
            "post",
            "/comms/chatter_turn",
            {"serial": serial},
            decode=lambda r: chatter_turn_from_json(r.json()),
            offline=lambda: None,
        )

    # --- the board alarms (TASKBOARD.md section 7; T-042) ----------------------------------
    # The runner and the admin process raise the environment's alarms; the callers
    # themselves are wired by T-043 and T-045. Repeats fold into `count` on the hive.

    def board_alarm(
        self, alarm_type: str, text: str, *, slot: str = "", serial: int | None = None
    ) -> None:
        """Raise an environment alarm as this slot's runner (X-Runner-Key)."""
        payload: dict[str, Any] = {"type": alarm_type, "text": text, "slot": slot}
        if serial is not None:
            payload["serial"] = serial
        self._call("post", "/board/alarms", payload, buffered=True)

    def board_alarm_admin(
        self, alarm_type: str, text: str, *, slot: str = "", serial: int | None = None
    ) -> None:
        """Raise an environment alarm as the admin (the Last Order key over HTTP)."""
        payload: dict[str, Any] = {"type": alarm_type, "text": text, "slot": slot}
        if serial is not None:
            payload["serial"] = serial
        self._call(
            "post", "/board/alarms", payload, buffered=True,
            extra_headers={"X-Node-Key": self._lo_key},
        )


class BoardFeedClient:
    """The board feed reader of one slot's bridge (T-050): `GET /board/feed` and
    `POST /board/ack` with the node key, plus the node's own `delivery_failing`
    alarm (TASKBOARD.md sections 9 and 11 — the node key is the only author that
    may raise it). One attempt per call: the pacing, the retries and the alarm
    belong to the bridge. Connect errors and 5xx answer `HiveUnavailable`, a 4xx
    `HiveRejected` — the same taxonomy the bridge's callers already read."""

    def __init__(
        self,
        base_url: str = "http://hive:8000",
        *,
        node_key: str = "",
        transport: httpx.Client | None = None,
        timeout_s: float = 5.0,
    ):
        self._http = transport if transport is not None else httpx.Client(
            base_url=base_url, timeout=timeout_s
        )
        self._node_key = node_key

    def close(self) -> None:
        self._http.close()

    def _request(
        self, method: str, path: str, payload: Any = None, params: Any = None
    ) -> httpx.Response:
        headers = {"X-Node-Key": self._node_key} if self._node_key else {}
        try:
            response = (
                self._http.post(path, json=payload, headers=headers)
                if method == "post"
                else self._http.get(path, params=params, headers=headers)
            )
        except httpx.HTTPError as exc:
            raise HiveUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500:
            raise HiveUnavailable(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            error = {}
            try:
                body = response.json()
                if isinstance(body, dict) and isinstance(body.get("error"), dict):
                    error = body["error"]
            except ValueError:
                pass
            raise HiveRejected(
                response.status_code,
                str(error.get("kind", "http")),
                str(error.get("message", response.text[:200])),
            )
        return response

    def feed(self, since: int | None) -> dict:
        """Events after `since` for this node; `None` on a new life lets the hive
        answer with the one-time snapshot of open `net` and addressed tasks."""
        params = {"since": since} if since is not None else None
        return self._request("get", "/board/feed", params=params).json()

    def ack(self, seq: int) -> None:
        """Everything up to `seq` reached the agent — the only cursor move."""
        self._request("post", "/board/ack", {"seq": int(seq)})

    def alarm_delivery_failing(
        self, text: str, *, slot: str = "", serial: int | None = None
    ) -> None:
        payload: dict[str, Any] = {"type": "delivery_failing", "text": text, "slot": slot}
        if serial is not None:
            payload["serial"] = serial
        self._request("post", "/board/alarms", payload)
