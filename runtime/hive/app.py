"""The hive HTTP service (ADR-024): FastAPI endpoints one-to-one with the ports.

`create_app` wires the T-012 `KnowledgeStore`, the `Lifecycle` of this task and the
in-memory Misaka-net inboxes into `HiveService` — one object implementing the
`Memory`, `NodeRegistry` and `NodeComms` ports directly — and exposes it over HTTP:

    POST /memory/inheritance | /memory/knowledge | /memory/core_memory
    POST /memory/store_core_memory | /memory/submit_candidate | /memory/submit_vote
    POST /registry/birth | /registry/request_death | /registry/mark_dead | /registry/retire
    GET  /registry/alive
    POST /comms/inbox | /comms/send | /comms/chatter_turn
    GET  /health

Next to the ports (T-028) the same app serves the network MCP of NETWORK.md section 4 —
stateless streamable HTTP, the T-024 format: `/node/mcp` carries the sisters' tools and
`/last-order/mcp` the administrator's (`runtime/hive/mcp.py`); the queue behind `propose`
and `decide` lives in `runtime/hive/proposals.py`. Mounted Starlette apps get no lifespan
events, so the MCP session managers start in this app's lifespan.

Endpoints are a mechanical JSON bridge (codecs from `runtime/hive/client.py`);
every decision lives in the service. One error schema everywhere, including FastAPI's
own validation (review P1-5): `{"error": {"kind": ..., "message": ...}}` with
UnknownNodeError → 404, AlreadyRetiredError → 409, invalid payloads and StoreError
subclasses of the Invalid* family → 422, anything else from the store (lock timeout,
git failure — transient) → 503 so the client retries instead of dropping.

Sync endpoints run in FastAPI's thread pool and the deadline scan runs through
`asyncio.to_thread`; both are serialized by the locks inside Lifecycle and
CommsBook, which is why uvicorn must run exactly one worker (review P1-4). No LLM
call is made or proxied here: the goal council is T-016, the chatter director
T-017 (`chatter_turn` is always None until then).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import StreamingResponse

from runtime.config import env
from runtime.comms.schema import MSG_MAX_CHARS, MSG_TTL_S
from runtime.hive.board import OPERATOR, Board, BoardError
from runtime.hive.client import (
    WireError,
    birth_record_to_json,
    candidate_from_json,
    chatter_turn_to_json,
    inheritance_to_json,
    message_from_json,
    message_to_json,
    node_brief_to_json,
    slice_to_json,
    traits_from_json,
    vote_from_json,
)
from runtime.hive.lifecycle import HiveLog, Lifecycle, LifecycleError, TTL_SCAN_S
from runtime.hive.mcp import HiveMcp, build_mcp, parse_node_keys
from runtime.hive.proposals import CommonLayers, ProposalBook
from runtime.hive.rcon import RconClient, RconError
from runtime.knowledge.store import (
    AlreadyRetiredError,
    InvalidCandidateError,
    InvalidVoteError,
    InvalidWillError,
    KnowledgeStore,
    StoreError,
    UnknownNodeError,
)
from runtime.lastorder.traits import Traits
from runtime.ports import ChatterTurn, KnowledgeCandidate, NodeMessage, Vote


class ApiError(Exception):
    """Carries the HTTP mapping straight to the single error envelope."""

    def __init__(self, status: int, kind: str, message: str):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.message = message


class CommsBook:
    """Inboxes of the Misaka-net (GOALS_COMMS.md section 3): plain observations.

    `send` refuses nothing loudly: an over-long text or a dead addressee is dropped
    with a `comms.drop` event (`len` | `unknown_addressee`), because the limits are
    the mind's to keep and hive is the safety net. `inbox` delivers once — returned
    means delivered. Undelivered messages die at `MSG_TTL_S` (T-008) with
    `comms.drop:ttl`, both on read and in the background sweep. The broadcast
    addressee `all` is not expressible in `NodeMessage` and stays T-017's business.
    """

    def __init__(self, lifecycle: Lifecycle, *, log: HiveLog, clock: Callable[[], datetime]):
        self._lifecycle = lifecycle
        self._log = log
        self._clock = clock
        self._lock = threading.RLock()
        self._inboxes: dict[int, list[tuple[NodeMessage, datetime]]] = {}

    def send(self, message: NodeMessage) -> None:
        with self._lock:
            if not message.text.strip() or len(message.text) > MSG_MAX_CHARS:
                self._log.event(
                    "comms.drop",
                    {"why": "len", "from": message.from_serial, "to": message.to_serial},
                )
                return
            if message.to_serial not in {brief.serial for brief in self._lifecycle.alive()}:
                self._log.event(
                    "comms.drop",
                    {"why": "unknown_addressee", "from": message.from_serial, "to": message.to_serial},
                )
                return
            ts = message.ts or self._clock().isoformat()
            stored = NodeMessage(
                from_serial=message.from_serial,
                to_serial=message.to_serial,
                text=message.text,
                ts=ts,
            )
            self._inboxes.setdefault(message.to_serial, []).append(
                (stored, self._clock() + timedelta(seconds=MSG_TTL_S))
            )
            self._log.event(
                "comms.send",
                {"from": message.from_serial, "to": message.to_serial, "len": len(message.text)},
            )

    def inbox(self, serial: int) -> list[NodeMessage]:
        with self._lock:
            self._sweep_locked()
            pending = self._inboxes.pop(serial, [])
            return [message for message, _ in pending]

    def sweep(self) -> None:
        with self._lock:
            self._sweep_locked()

    def _sweep_locked(self) -> None:
        now = self._clock()
        for serial in list(self._inboxes):
            keep, drop = [], []
            for item in self._inboxes[serial]:
                (keep if item[1] > now else drop).append(item)
            for message, _ in drop:
                self._log.event(
                    "comms.drop",
                    {"why": "ttl", "from": message.from_serial, "to": message.to_serial},
                )
            if keep:
                self._inboxes[serial] = keep
            else:
                self._inboxes.pop(serial, None)


class HiveService:
    """The three ports behind one door; the endpoints and the tests both call this."""

    def __init__(self, store: KnowledgeStore, lifecycle: Lifecycle, comms: CommsBook):
        self.store = store
        self.lifecycle = lifecycle
        self.comms = comms

    # Memory
    def inheritance(self, serial: int):
        # no require_node: the store itself demands a born, unretired recipient
        return self.store.inheritance(serial)

    def knowledge(self, serial: int, handles: Sequence[str]):
        self.lifecycle.require_node(serial)
        return self.store.knowledge_slice(serial, tuple(handles))

    def core_memory(self, serial: int) -> str:
        self.lifecycle.require_node(serial)
        return self.store.core_memory(serial)

    def store_core_memory(self, serial: int, text: str) -> None:
        self.lifecycle.require_node(serial)
        self.store.store_core_memory(serial, text)

    def submit_candidate(self, serial: int, candidate: KnowledgeCandidate) -> None:
        self.lifecycle.require_node(serial)
        self.store.submit_candidate(serial, candidate)

    def submit_vote(self, serial: int, vote: Vote) -> None:
        self.lifecycle.require_node(serial)
        self.store.submit_vote(serial, vote)

    # NodeRegistry
    def birth(self, slot: str, traits: Traits):
        return self.lifecycle.birth(slot, traits)

    def request_death(self, serial: int, cause: str) -> None:
        self.lifecycle.request_death(serial, cause)

    def mark_dead(self, serial: int) -> None:
        """The death signal mind-N owes hive (T-028 review R1-3): the body died in-game,
        no kill to confirm. Gates `submit_will`; idempotent — retries after a flaky
        network land on an already-marked node without an error. The wiring of the
        caller (the mind's death detector) is T-027."""
        self.lifecycle.mark_dead(serial)

    def retire(self, serial: int, cause: str, will_text: str) -> None:
        self.lifecycle.retire(serial, cause, will_text)

    def alive(self) -> list:
        return self.lifecycle.alive()

    # NodeComms
    def inbox(self, serial: int):
        self.lifecycle.require_node(serial)
        return self.comms.inbox(serial)

    def send(self, message: NodeMessage) -> None:
        self.comms.send(message)

    def chatter_turn(self, serial: int) -> ChatterTurn | None:
        self.lifecycle.require_node(serial)
        return None  # the chatter director is T-017; until then nobody is granted a turn


def create_app(
    store: KnowledgeStore | None = None,
    lifecycle: Lifecycle | None = None,
    *,
    rcon: RconClient | None = None,
    log: HiveLog | None = None,
    run_dir: Path | str | None = None,
    clock: Callable[[], datetime] | None = None,
    knowledge_dir: Path | str | None = None,
    scan_s: float | None = None,
    node_keys: dict[str, str] | None = None,
    lo_key: str | None = None,
    runner_keys: dict[str, str] | None = None,
    operator_key: str | None = None,
    stream_poll_s: float | None = None,
) -> FastAPI:
    clock = clock or (lambda: datetime.now(timezone.utc))
    run_dir = Path(run_dir if run_dir is not None else (env("RUN_DIR") or "run"))
    log = log or HiveLog(run_dir / "events.jsonl")
    if store is None:
        store = KnowledgeStore(Path(knowledge_dir if knowledge_dir is not None else (env("KNOWLEDGE_DIR") or "knowledge")))
    if rcon is None:
        rcon = RconClient.from_env()
    if lifecycle is None:
        lifecycle = Lifecycle(store, rcon, log=log, run_dir=run_dir, clock=clock)
    service = HiveService(store, lifecycle, CommsBook(lifecycle, log=log, clock=clock))
    board = Board(
        run_dir / "board.sqlite", lifecycle=lifecycle, log=log, clock=clock
    )
    # TASKBOARD.md 4.3: every closing point of a node frees its board hold in one
    # transaction; the hook itself never breaks the death path (lifecycle logs the miss).
    lifecycle.set_death_hook(board.on_death)
    scan_seconds = float(scan_s if scan_s is not None else (env("HIVE_TTL_SCAN_S") or TTL_SCAN_S))

    # --- the network MCP (T-028): same port, two mounted servers ------------------------

    keys = (
        dict(node_keys)
        if node_keys is not None
        else parse_node_keys(env("HIVE_MCP_NODE_KEYS") or "")
    )
    if runner_keys is None:
        runner_keys = parse_node_keys(env("HIVE_RUNNER_KEYS") or "", env_name="HIVE_RUNNER_KEYS")
        if not runner_keys and (env("NODE_DEV_OPEN") or "") != "1":
            # Fail-closed (review R2-6): an empty map silently opens every gate below,
            # and the sisters' sandbox is on this very port. Tests pass an explicit map
            # ({} = open); a dev run that really wants no keys sets NODE_DEV_OPEN=1.
            raise ValueError(
                "HIVE_RUNNER_KEYS is empty: the registry, memory and comms gates would"
                " be open. Configure the slot keys or set NODE_DEV_OPEN=1 for a dev run."
            )
    last_order_key = lo_key if lo_key is not None else (env("HIVE_MCP_LO_KEY") or "")
    operator_secret = operator_key if operator_key is not None else (env("HIVE_OPERATOR_KEY") or "")
    stream_poll = float(stream_poll_s if stream_poll_s is not None else (env("BOARD_STREAM_POLL_S") or 1.0))
    now = lambda: clock().isoformat()  # noqa: E731 - one clock for every layer write
    proposals = ProposalBook(store, now=now)
    layers = CommonLayers(store, now=now)
    node_server, lo_server = build_mcp(
        HiveMcp(
            store,
            lifecycle,
            service,
            proposals,
            layers,
            node_keys=keys,
            lo_key=last_order_key,
            run_dir=run_dir,
            log=log,
            now=now,
            runner_keys=runner_keys,
            board=board,
        )
    )
    # host="0.0.0.0" keeps the SDK's localhost-only DNS-rebinding protection off: the
    # harnesses reach the port by service name (hive:8000) inside the docker network,
    # the same call as the body MCP (T-024).
    node_app = node_server.streamable_http_app(
        stateless_http=True, streamable_http_path="/mcp", host="0.0.0.0"
    )
    lo_app = lo_server.streamable_http_app(
        stateless_http=True, streamable_http_path="/mcp", host="0.0.0.0"
    )

    def scan_once() -> None:
        lifecycle.scan(clock())
        service.comms.sweep()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = asyncio.Event()

        async def scan_loop() -> None:
            while not stop.is_set():
                try:  # blocking RCON and store calls stay off the event loop (P1-4)
                    await asyncio.to_thread(scan_once)
                except Exception as exc:  # the loop outlives any single scan
                    log.event("scan.failed", {"error": str(exc)})
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=scan_seconds)

        task = asyncio.create_task(scan_loop())
        try:
            # Mounted Starlette apps receive no lifespan events from FastAPI: the MCP
            # session managers of /node and /last-order start here (T-028).
            async with contextlib.AsyncExitStack() as stack:
                for sub in (node_app, lo_app):
                    await stack.enter_async_context(sub.router.lifespan_context(sub))
                yield
        finally:
            stop.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="last-order hive", lifespan=lifespan)
    app.state.hive = service
    app.state.log = log
    app.state.board = board

    def envelope(status: int, kind: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status, content={"error": {"kind": kind, "message": message}}
        )

    _STORE_MAP: tuple[tuple[type[StoreError], int, str], ...] = (
        (UnknownNodeError, 404, "unknown_node"),
        (AlreadyRetiredError, 409, "already_retired"),
        (InvalidWillError, 422, "invalid_will"),
        (InvalidCandidateError, 422, "invalid_candidate"),
        (InvalidVoteError, 422, "invalid_vote"),
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Any, exc: ApiError) -> JSONResponse:
        return envelope(exc.status, exc.kind, exc.message)

    @app.exception_handler(BoardError)  # the board's refusals carry their own mapping
    async def _board_error(_: Any, exc: BoardError) -> JSONResponse:
        return envelope(exc.status, exc.kind, exc.message)

    @app.exception_handler(StoreError)  # subclasses resolved by isinstance below
    async def _store_error(_: Any, exc: StoreError) -> JSONResponse:
        for cls, status, kind in _STORE_MAP:
            if isinstance(exc, cls):
                return envelope(status, kind, str(exc))
        return envelope(503, "store", str(exc))  # transient: the client retries, never drops

    @app.exception_handler(LifecycleError)
    async def _lifecycle_error(_: Any, exc: LifecycleError) -> JSONResponse:
        return envelope(422, "lifecycle", str(exc))

    @app.exception_handler(RconError)
    async def _rcon_error(_: Any, exc: RconError) -> JSONResponse:
        return envelope(503, "rcon", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Any, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
            for e in exc.errors()
        ]
        return envelope(422, "validation", f"request does not match the endpoint schema: {errors}")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Any, exc: StarletteHTTPException) -> JSONResponse:
        return envelope(exc.status_code, "http", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Any, exc: Exception) -> JSONResponse:
        return envelope(500, "internal", f"{type(exc).__name__}: {exc}")

    # --- request helpers ---------------------------------------------------------------

    def _obj(body: dict, key: str) -> dict:
        value = body.get(key)
        if not isinstance(value, dict):
            raise ApiError(422, "validation", f"{key} must be an object")
        return value

    def _serial(body: dict) -> int:
        value = body.get("serial")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ApiError(422, "validation", "serial must be an integer >= 1")
        return value

    def _text(body: dict, key: str) -> str:
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ApiError(422, "validation", f"{key} must be a non-empty string")
        return value

    def _decode(fn: Callable[[Any], Any], data: Any, what: str) -> Any:
        try:
            return fn(data)
        except WireError as exc:
            raise ApiError(422, "validation", f"{what}: {exc}") from exc

    # --- health and Memory ---------------------------------------------------------------
    # The /memory and /comms routes are gated exactly like /registry when HIVE_RUNNER_KEYS
    # is configured (review R2-2): the sisters' sandbox shares this port, and without the
    # gate a bash call could rewrite another node's core memory, vote or inbox. The key
    # must be the slot's that owns the request's serial; the sisters themselves speak
    # through the MCP mounts (/node, X-Node-Key). /health and /registry/alive stay open.

    def _require_runner_key(request: Request, slot: str) -> None:
        if not runner_keys:
            return
        key = request.headers.get("x-runner-key", "").strip()
        if runner_keys.get(key) != slot:
            raise ApiError(401, "runner_key", f"X-Runner-Key does not open slot {slot}")

    def _require_runner_key_of(request: Request, serial: int) -> None:
        # the 404 of an unknown serial stays ahead of the key check, same trade as
        # /registry/retire: registry membership is public inside the network
        _require_runner_key(request, lifecycle.require_node(serial).slot)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/memory/inheritance")
    def memory_inheritance(request: Request, body: dict) -> dict:
        _require_runner_key_of(request, _serial(body))
        return inheritance_to_json(service.inheritance(_serial(body)))

    @app.post("/memory/knowledge")
    def memory_knowledge(request: Request, body: dict) -> dict:
        serial = _serial(body)
        _require_runner_key_of(request, serial)
        handles = body.get("handles", [])
        if not isinstance(handles, list) or any(not isinstance(h, str) for h in handles):
            raise ApiError(422, "validation", "handles must be a list of strings")
        return slice_to_json(service.knowledge(serial, handles))

    @app.post("/memory/core_memory")
    def memory_core_memory(request: Request, body: dict) -> str:
        _require_runner_key_of(request, _serial(body))
        return service.core_memory(_serial(body))

    @app.post("/memory/store_core_memory")
    def memory_store_core_memory(request: Request, body: dict) -> None:
        serial = _serial(body)
        _require_runner_key_of(request, serial)
        service.store_core_memory(serial, _text(body, "text"))

    @app.post("/memory/submit_candidate")
    def memory_submit_candidate(request: Request, body: dict) -> None:
        serial = _serial(body)
        _require_runner_key_of(request, serial)
        candidate = _decode(candidate_from_json, _obj(body, "candidate"), "candidate")
        service.submit_candidate(serial, candidate)

    @app.post("/memory/submit_vote")
    def memory_submit_vote(request: Request, body: dict) -> None:
        serial = _serial(body)
        _require_runner_key_of(request, serial)
        vote = _decode(vote_from_json, _obj(body, "vote"), "vote")
        service.submit_vote(serial, vote)

    # --- NodeRegistry ----------------------------------------------------------------------
    # The writes of this section belong to the node runner alone (T-027 plan review P1-1):
    # with HIVE_RUNNER_KEYS configured, every mutating call must carry the slot's runner
    # key in X-Runner-Key (the helpers live with the Memory section above). The registry
    # lives on the same port as the sisters' MCP, and a sister with bash could otherwise
    # retire a neighbour or mark herself dead. An explicitly passed empty map (tests)
    # keeps the endpoints open; an empty env is a startup refusal (R2-6).

    @app.post("/registry/birth")
    def registry_birth(request: Request, body: dict) -> dict:
        slot = _text(body, "slot")
        _require_runner_key(request, slot)
        traits = _decode(traits_from_json, _obj(body, "traits"), "traits")
        return birth_record_to_json(service.birth(slot, traits))

    @app.post("/registry/request_death")
    def registry_request_death(request: Request, body: dict) -> None:
        serial = _serial(body)
        _require_runner_key(request, lifecycle.require_node(serial).slot)
        service.request_death(serial, _text(body, "cause"))

    @app.post("/registry/mark_dead")
    def registry_mark_dead(request: Request, body: dict) -> None:
        serial = _serial(body)
        _require_runner_key(request, lifecycle.require_node(serial).slot)
        service.mark_dead(serial)

    @app.post("/registry/retire")
    def registry_retire(request: Request, body: dict) -> None:
        serial = _serial(body)
        # The 404/409 of require_node stay ahead of the key check on purpose: they are
        # the store's answers to a retrying runner, not information a keyless caller
        # gains (the registry's membership is public inside the network).
        _require_runner_key(request, lifecycle.require_node(serial).slot)
        will_text = _text(body, "will_text")
        service.retire(serial, _text(body, "cause"), will_text)

    @app.get("/registry/alive")
    def registry_alive() -> list:
        return [node_brief_to_json(brief) for brief in service.alive()]

    # --- NodeComms ---------------------------------------------------------------------------
    # Same gate as Memory (R2-2): /comms/send speaks for its from_serial, the reads for
    # their serial — the sender's slot key, not a bare POST from the sisters' sandbox.

    @app.post("/comms/inbox")
    def comms_inbox(request: Request, body: dict) -> list:
        serial = _serial(body)
        _require_runner_key_of(request, serial)
        return [message_to_json(m) for m in service.inbox(serial)]

    @app.post("/comms/send")
    def comms_send(request: Request, body: dict) -> None:
        message = _decode(message_from_json, _obj(body, "message"), "message")
        _require_runner_key_of(request, message.from_serial)
        service.send(message)

    @app.post("/comms/chatter_turn")
    def comms_chatter_turn(request: Request, body: dict) -> dict | None:
        serial = _serial(body)
        _require_runner_key_of(request, serial)
        return chatter_turn_to_json(service.chatter_turn(serial))

    # --- the task board (TASKBOARD.md section 9; T-042) -----------------------------------
    # The operator speaks with HIVE_OPERATOR_KEY in X-Operator-Key, agents with their
    # X-Node-Key (a slot's live serial or Last Order's key), the environment's alarms
    # with X-Runner-Key / the Last Order key as `admin`. `from` is always stamped here,
    # never read from a body. The /board page itself is T-049; this is the API under it.

    def _require_operator(request: Request) -> None:
        if not operator_secret:
            raise ApiError(401, "operator_key", "the board needs HIVE_OPERATOR_KEY configured")
        if request.headers.get("x-operator-key", "").strip() != operator_secret:
            raise ApiError(401, "operator_key", "X-Operator-Key does not open the board")

    def _agent_actor(request: Request) -> str:
        """X-Node-Key -> 'last-order' | the live serial, for feed/ack/entry reads."""
        key = request.headers.get("x-node-key", "").strip()
        if not key:
            raise ApiError(401, "node_key", "identify with X-Node-Key")
        if last_order_key and key == last_order_key:
            return "last-order"
        slot = keys.get(key)
        if slot is None:
            raise ApiError(401, "node_key", "unknown node key")
        alive = [b for b in lifecycle.alive() if b.slot == slot]
        live = [b for b in alive if not lifecycle.body_dead(b.serial)]
        if not live:
            raise ApiError(409, "no_live_node", f"slot {slot} has no live node")
        return str(max(b.serial for b in live))

    def _board_actor(request: Request) -> str:
        """Operator or agent identity for the routes both may read."""
        if request.headers.get("x-operator-key", "").strip():
            _require_operator(request)
            return OPERATOR
        return _agent_actor(request)

    def _alarm_author(request: Request) -> str:
        """X-Runner-Key -> runner:<slot>; the Last Order key -> `admin` (the admin
        process of the LO sandbox rides this key over HTTP, ADR-036); a node key ->
        the node (delivery_failing alone, enforced by the board's rights table)."""
        runner_key = request.headers.get("x-runner-key", "").strip()
        if runner_key:
            slot = runner_keys.get(runner_key) if runner_keys else None
            if slot is None:
                raise ApiError(401, "runner_key", "X-Runner-Key does not open any slot")
            return f"runner:{slot}"
        key = request.headers.get("x-node-key", "").strip()
        if last_order_key and key == last_order_key:
            return "admin"
        return _agent_actor(request)

    def _no_from_in(body: dict) -> None:
        if "from" in body:
            raise ApiError(422, "validation", "from is stamped by the server, not sent")

    @app.get("/board/entries")
    def board_entries(request: Request, to: str | None = None, kind: str | None = None,
                      status: str | None = None, limit: int = 50) -> list:
        _require_operator(request)
        return board.entries(to=to, kind=kind, status=status, limit=limit)

    @app.get("/board/entries/{entry_id}")
    def board_entry(request: Request, entry_id: int) -> dict:
        return board.entry_for(_board_actor(request), entry_id)

    @app.post("/board/entries")
    def board_post(request: Request, body: dict) -> dict:
        _require_operator(request)
        _no_from_in(body)
        return board.post(
            OPERATOR, kind=body.get("kind"), to=body.get("to"),
            text=body.get("text"), request=body.get("request"),
        )

    @app.post("/board/entries/{entry_id}/transition")
    def board_transition(request: Request, entry_id: int, body: dict) -> dict:
        _require_operator(request)
        _no_from_in(body)
        return board.transition(
            OPERATOR, entry_id, body.get("to_status"), text=body.get("text", ""),
            args=body.get("args"),
        )

    @app.post("/board/entries/{entry_id}/reply")
    def board_reply(request: Request, entry_id: int, body: dict) -> dict:
        _require_operator(request)
        _no_from_in(body)
        return board.reply(OPERATOR, entry_id, body.get("text"))

    @app.post("/board/alarms")
    def board_alarms(request: Request, body: dict) -> dict:
        _no_from_in(body)
        author = _alarm_author(request)
        serial = body.get("serial")
        if serial is not None and (not isinstance(serial, int) or isinstance(serial, bool)):
            raise ApiError(422, "validation", "serial must be an integer")
        return board.alarm(
            author, body.get("type"), body.get("text"),
            slot=str(body.get("slot", "") or ""), serial=serial,
        )

    @app.get("/board/feed")
    def board_feed(request: Request, since: int | None = None) -> dict:
        who = _board_actor(request)
        return board.feed(who, since)

    @app.get("/board/stream")
    async def board_stream(request: Request) -> StreamingResponse:
        _require_operator(request)

        async def events():
            last = board.max_seq()
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                rows = await asyncio.to_thread(board.log_rows, last)
                for row in rows:
                    last = row["seq"]
                    yield f"event: board\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                await asyncio.sleep(stream_poll)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/board/ack")
    def board_ack(request: Request, body: dict) -> None:
        board.ack(_agent_actor(request), body.get("seq"))

    # --- the network MCP mounts (T-028) ---------------------------------------------------

    app.mount("/node", node_app)
    app.mount("/last-order", lo_app)

    return app
