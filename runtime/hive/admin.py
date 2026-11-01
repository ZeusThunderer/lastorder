"""Last Order's alarm and short sessions (T-030; ADR-036, plan review P1-1..P1-7).

The alarm is file-driven and single-threaded: one session at a time, and events that
land during a session accumulate into the next wake (ADR-036 "события во время сессии
копятся"): the whole baseline — deaths, pending proposals, the digests of the operator
files — is taken at SESSION START, so whatever lands while the session runs is still
"new" on the next poll. The operator files are root-owned and read-only for the agent
(P1-2), so a digest change can only be the operator's doing, and the agent's own memory
writes are not watched at all.

Wake sources, all read-only against the knowledge volume (the layout lives in
`HiveLayout`, P1-5; the hive stays the only writer, ADR-017):

- death: a new `nodes/<serial>/will.md` — there is no death without a will, so the will
  IS the signal;
- batch: new pending proposals (`base/proposals/p-NNNN.json`, status read from the file
  content) with the open queue at >= ADMIN_PROPOSALS_BATCH;
- operator: `INBOX.md` or `GOALS.md` changed in the workplace;
- clock: ADMIN_EVERY_MIN since the last session, plus a floor pause between sessions
  (ADMIN_SESSION_MIN_GAP_S) — the budget guard of P1-6.

The session is a short harness run in the workplace: the runner's adapters (runtime/
node/adapters.py — reused, never modified). Since T-059 the primary is pi on the Z.ai
Coding Plan — the same subscription and the same agent-dir render the sisters ride
(T-066) — with opencode through the gateway (`smart` tier) as the quota failover; MCP
`/last-order/mcp` with the Last Order key is rendered into the session's configs the
same way a sister's node key is (T-029). The core files and skills are
rewritten from the image on every start: tampering is undone, `memory/` is never
touched (P1-3). Children run under the sister uid with a fresh environment — no key of
this container rides into a session.

No LLM call happens outside the session itself (P1-6); everything the agent decides, it
decides through the MCP tools of T-028.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.config import REPO_ROOT, env
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.slice import format_serial, parse_utc
from runtime.lastorder.naming import node_name, preset as naming_preset
from runtime.node.adapters import (
    PI_SESSION_DIR,
    Command,
    OpenCodeAdapter,
    PiAdapter,
    quota_exhausted,
)
from runtime.node.runner import SessionProcess
from runtime.node.workplace import (
    PI_CONTEXT_WINDOW,
    PI_KEY_FILE,
    PI_KEEP_RECENT_TOKENS,
    READ_ONLY_DIR,
    READ_ONLY_FILE,
    SISTER_DIR,
    SISTER_FILE,
    WORKPLACE_DIR_MODE,
    _chmod,
    _chmod_tree,
    _force_rmtree,
    _own,
    _write_file_no_links,
    _write_readonly,
    render_pi_configs,
)

# --- the knowledge layout this module reads (P1-5: one place, read-only) --------------------

PROPOSALS_REL = Path("base") / "proposals"
PROPOSAL_GLOB = "p-*.json"
WILL_FILE = "will.md"
NODES_REL = Path("nodes")
GOALS_REL = Path("base") / "goals.md"
CHRONICLE_REL = Path("base") / "chronicle.md"

# --- the workplace --------------------------------------------------------------------------

ADMIN_DIR = ".admin"  # the alarm's own state and events, root-owned
STATE_FILE = "state.json"
OPENCODE_CONFIG = "opencode.json"
CORE_FILES = ("PERSONA.md", "AGENTS.md", "SESSION_PROMPT.md")  # rewritten every start (P1-3)
CORE_DIRS = ("skills",)
OPERATOR_FILES = ("INBOX.md", "GOALS.md")  # created once, then root's alone (P1-2)
AGENT_DIRS = ("memory",)  # the agent's writable zone
WORKPLACE_TEMPLATE = REPO_ROOT / "agent" / "network"

PROMPT_PREFIX = "Last Order session."
SESSION_TITLE = "lo-session"
HARNESSES = ("pi", "opencode")  # the desk's harness pair (runtime/node/config.py)
SUMMARY_EXCERPT_CHARS = 1500
CHRONICLE_TAIL_LINES = 5
QUEUE_SNAPSHOT_MAX = 20


class HiveLayout:
    """The one reader of the knowledge store's layout the alarm depends on (P1-5).
    Read-only, defensive: a torn or foreign file is not a wake signal."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def will_serials(self) -> frozenset[int]:
        """Serials with a filed will (`nodes/<serial>/will.md`): the closed lives."""
        nodes = self.root / NODES_REL
        out: set[int] = set()
        if not nodes.is_dir():
            return frozenset(out)
        for child in nodes.iterdir():
            if child.is_dir() and child.name.isdigit() and (child / WILL_FILE).exists():
                out.add(int(child.name))
        return frozenset(out)

    def pending_proposals(self) -> list[dict[str, Any]]:
        """Open proposals (status from the file content, never the name), oldest first."""
        directory = self.root / PROPOSALS_REL
        out: list[dict[str, Any]] = []
        if not directory.is_dir():
            return out
        for path in sorted(directory.glob(PROPOSAL_GLOB)):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue  # torn between the store's commit steps: not a signal
            if isinstance(doc, dict) and doc.get("status") == "pending":
                out.append(doc)
        out.sort(key=lambda doc: str(doc.get("id", "")))
        return out

    def excerpt(self, rel: Path, limit: int = SUMMARY_EXCERPT_CHARS) -> str:
        """A capped read of one store file for the session summary."""
        try:
            return (self.root / rel).read_text(encoding="utf-8")[:limit]
        except OSError:
            return ""

    def chronicle_tail(self, lines: int = CHRONICLE_TAIL_LINES) -> str:
        try:
            content = (self.root / CHRONICLE_REL).read_text(encoding="utf-8")
        except OSError:
            return ""
        return "".join(content.splitlines(keepends=True)[-lines:]).strip()


# --- the wake state and its diff ------------------------------------------------------------


@dataclass(frozen=True)
class WakeReason:
    kind: str  # death | proposals | operator | timer
    detail: str


@dataclass(frozen=True)
class WakeState:
    """The persisted baseline (`workplace/.admin/state.json`)."""

    deaths: frozenset[int]  # serials with a will at the last session's start
    proposals: frozenset[str]  # pending proposal ids at the last session's start
    operator: tuple[str, ...]  # digests of OPERATOR_FILES at the last session's start
    last_session_end: str | None  # ISO-8601; None until the first session ever
    boot_at: str = ""  # ISO-8601; the timer origin while no session has run yet


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def operator_digests(workplace: Path) -> tuple[str, ...]:
    out: list[str] = []
    for name in OPERATOR_FILES:
        try:
            out.append(_digest((workplace / name).read_text(encoding="utf-8")))
        except OSError:
            out.append("")
    return tuple(out)


class Wake:
    """The pure diff of the world against a `WakeState`: what happened since."""

    def __init__(self, *, layout: HiveLayout, workplace: Path, every_min: float, batch: int):
        self._layout = layout
        self._workplace = workplace
        self._every_min = every_min
        self._batch = batch

    def snapshot(self, now: datetime) -> WakeState:
        return WakeState(
            deaths=self._layout.will_serials(),
            proposals=frozenset(str(doc.get("id", "")) for doc in self._layout.pending_proposals()),
            operator=operator_digests(self._workplace),
            last_session_end=None,
            boot_at=now.isoformat(),
        )

    def reasons(self, state: WakeState, now: datetime) -> list[WakeReason]:
        out: list[WakeReason] = []
        new_deaths = sorted(self._layout.will_serials() - state.deaths)
        if new_deaths:
            out.append(
                WakeReason(
                    "death",
                    ", ".join(format_serial(serial) for serial in new_deaths),
                )
            )
        pending = self._layout.pending_proposals()
        new_ids = {str(doc.get("id", "")) for doc in pending} - set(state.proposals)
        if new_ids and len(pending) >= self._batch:
            out.append(
                WakeReason("proposals", f"{len(pending)} pending, {len(new_ids)} new")
            )
        current = operator_digests(self._workplace)
        changed = [
            name
            for name, was, is_ in zip(OPERATOR_FILES, state.operator, current)
            if was != is_
        ]
        if changed:
            out.append(WakeReason("operator", ", ".join(changed)))
        origin = state.last_session_end or state.boot_at
        if origin:
            try:
                end = parse_utc(origin)
            except ValueError:
                end = None
            if end is not None and now - end >= timedelta(minutes=self._every_min):
                out.append(
                    WakeReason(
                        "timer",
                        f"{self._every_min:g} min since the last session",
                    )
                )
        return out


# --- the session adapter ----------------------------------------------------------------------


class LastOrderAdapter(OpenCodeAdapter):
    """The runner's opencode adapter (runtime/node/adapters.py), reused untouched, with
    the desk's own session title: `opencode run --model <smart> --format json`. The
    failover harness since T-059 — the primary is pi on the Z.ai subscription."""

    def session(self, prompt: str) -> Command:
        return Command(
            (
                "opencode", "run",
                "--model", self._model,
                "--format", "json",
                "--title", SESSION_TITLE,
                prompt,
            )
        )


class LastOrderPiAdapter(PiAdapter):
    """The runner's Pi adapter (runtime/node/adapters.py, T-066), reused untouched in
    argv and env — `pi -p --mode json --no-approve --offline --no-skills --skill skills
    -- <prompt>`, the key in the agent-dir zai-key file, never in the child env — with
    the desk's own agent-dir render (T-059): the desk has no body, so mcp.json carries
    the hive MCP alone, `direct`, at `/last-order/mcp` with the Last Order key."""

    def _render(self) -> None:
        agent_dir = Path(self._agent_dir)
        configs = render_pi_configs(
            self._model, self._body_base, self._hive_url, self._node_key,
            context_window=self._context_window,
            keep_recent_tokens=self._keep_recent_tokens,
            key_file=str(agent_dir / PI_KEY_FILE),
        )
        configs["mcp.json"] = {
            "autoEnableCodemode": False,
            "mcpServers": {
                "hive": {
                    "url": f"{self._hive_url.rstrip('/')}/last-order/mcp",
                    "headers": {"X-Node-Key": self._node_key},
                    "timeout": 10,
                    "exposure": "direct",
                },
            },
        }
        # the drop_pi_agent_dir write path (runtime/node/workplace.py): the dir is the
        # agent's own home territory, so every file is written link-safe, root-owned
        # 0444 — except zai-key, her 0400 credential, the T-066 exposure
        fresh_parent = not agent_dir.parent.exists()
        agent_dir.mkdir(parents=True, exist_ok=True)
        if fresh_parent:
            _own(agent_dir.parent, self._uid, self._gid)
            _chmod(agent_dir.parent, 0o700)
        if self._zai_key:
            _write_file_no_links(
                agent_dir / PI_KEY_FILE, self._zai_key, mode=0o400,
                uid=self._uid, gid=self._gid,
            )
        for name, config in configs.items():
            _write_file_no_links(
                agent_dir / name, json.dumps(config, indent=2),
                mode=READ_ONLY_FILE, uid=0, gid=0,
            )
        _own(agent_dir, self._uid, self._gid)
        _chmod(agent_dir, 0o700)

    def session(self, prompt: str) -> Command:
        self._render()  # every session starts from the desk's own render
        return self._command(prompt, None)


# --- config ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminConfig:
    workplace: Path
    template_dir: Path
    knowledge: Path
    hive_url: str
    gateway_url: str
    lo_key: str
    model: str
    # T-059: the primary harness is pi on the Z.ai Coding Plan (the operator's
    # 2026-10-31 pick: the model gives the desk its abilities — GLM-5.3, not Flash);
    # opencode through the gateway is the failover.
    harness: str = "pi"
    pi_model: str = "GLM-5.3"
    pi_agent_dir: Path = Path("/home/sister/.pi/agent")
    # the compaction budget of the desk's sessions (T-066's sister values by default:
    # a desk session is short, the cap is a guard rail; T-043 may retune it)
    pi_context_window: int = PI_CONTEXT_WINDOW
    pi_keep_recent_tokens: int = PI_KEEP_RECENT_TOKENS
    failover: str = "opencode"
    # readiness of a switch target, "the same way as node-N": the entrypoint's
    # root-only copy of the Z.ai token, and the opencode auth it renders into the
    # agent's home (both 0400/0600 files, never the process environment)
    zai_key_file: Path = Path("/run/node/secrets/zai_api_key")
    opencode_auth_file: Path = Path("/home/sister/.local/share/opencode/auth.json")
    naming: str = "lab"
    every_min: float = 10.0
    batch: int = 3
    poll_s: float = 5.0
    session_timeout_s: float = 600.0
    session_min_gap_s: float = 30.0
    kill_grace_s: float = 5.0
    sister_uid: int | None = None
    sister_gid: int | None = None

    def __post_init__(self) -> None:
        # the runner's validation (runtime/node/config.py): a typo in a harness name
        # must fail the boot, not surface as a KeyError on the first session
        if self.harness not in HARNESSES:
            raise ValueError(f"ADMIN_HARNESS must be one of {HARNESSES}, got {self.harness!r}")
        if self.failover and self.failover not in HARNESSES:
            raise ValueError(
                f"ADMIN_HARNESS_FAILOVER must be one of {HARNESSES} or empty, got {self.failover!r}"
            )
        if self.failover == self.harness:
            raise ValueError("ADMIN_HARNESS_FAILOVER must differ from ADMIN_HARNESS")

    @classmethod
    def from_env(cls) -> "AdminConfig":
        def _num(name: str, default: float) -> float:
            try:
                return float(env(name) or default)
            except ValueError:
                return default

        def _uid(name: str) -> int | None:
            value = (env(name) or "").strip()
            try:
                return int(value) if value else None
            except ValueError:
                return None

        harness = env("ADMIN_HARNESS") or "pi"
        return cls(
            workplace=Path(env("ADMIN_WORKPLACE") or "/workplace"),
            template_dir=Path(env("ADMIN_TEMPLATE_DIR") or WORKPLACE_TEMPLATE),
            knowledge=Path(env("ADMIN_KNOWLEDGE_DIR") or "/knowledge"),
            hive_url=env("HIVE_URL") or "http://hive:8000",
            gateway_url=env("GATEWAY_URL") or "http://llm-gateway:4000",
            lo_key=env("HIVE_MCP_LO_KEY") or "",
            model=env("ADMIN_MODEL") or "z-ai/glm-5.3-flash",
            harness=harness,
            pi_model=env("ADMIN_PI_MODEL") or "GLM-5.3",
            pi_agent_dir=Path(env("ADMIN_PI_AGENT_DIR") or "/home/sister/.pi/agent"),
            pi_context_window=int(_num("ADMIN_PI_CONTEXT_WINDOW", PI_CONTEXT_WINDOW)),
            pi_keep_recent_tokens=int(_num("ADMIN_PI_KEEP_RECENT_TOKENS", PI_KEEP_RECENT_TOKENS)),
            # the desk's failover is always the OTHER harness unless told otherwise
            failover=env("ADMIN_HARNESS_FAILOVER")
            or ("opencode" if harness == "pi" else "pi"),
            zai_key_file=Path(env("ZAI_KEY_FILE") or "/run/node/secrets/zai_api_key"),
            opencode_auth_file=Path(
                env("OPENCODE_AUTH_FILE")
                or "/home/sister/.local/share/opencode/auth.json"
            ),
            naming=env("NAME_PRESET") or "lab",
            every_min=_num("ADMIN_EVERY_MIN", 10.0),
            batch=max(1, int(_num("ADMIN_PROPOSALS_BATCH", 3))),
            poll_s=_num("ADMIN_POLL_S", 5.0),
            session_timeout_s=_num("ADMIN_SESSION_TIMEOUT_S", 600.0),
            session_min_gap_s=_num("ADMIN_SESSION_MIN_GAP_S", 30.0),
            kill_grace_s=_num("ADMIN_KILL_GRACE_S", 5.0),
            sister_uid=_uid("SISTER_UID"),
            sister_gid=_uid("SISTER_GID"),
        )


# --- the alarm -----------------------------------------------------------------------------------


class AdminAlarm:
    """All wiring, no policy: what wakes the agent is arithmetic on files and the
    clock; what the agent does is entirely its instructions (ADR-032)."""

    def __init__(
        self,
        cfg: AdminConfig,
        *,
        layout: HiveLayout | None = None,
        adapters: Mapping[str, Any] | None = None,
        spawn: Callable[[tuple[str, ...], Path, dict[str, str]], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        log: HiveLog | None = None,
        post_alarm: Callable[[str, str], Any] | None = None,
    ):
        self.cfg = cfg
        self._layout = layout or HiveLayout(cfg.knowledge)
        # Both harnesses are built up front: pi on the subscription is the primary
        # (T-059), opencode on the gateway is the failover. The failover's `--model`
        # must carry the provider prefix ("llm-gateway/<model>"): a bare
        # "z-ai/glm-5.3-flash" resolves to opencode's BUILT-IN z-ai provider, which
        # holds no key on this sandbox — proven on the stand (T-030 report). The pi
        # agent reads the token the entrypoint materialized for this container (the
        # root-only tmpfs copy, the same lift as node-N); no token means pi is not
        # ready and the desk waits on it (the runner's `_adapter_ready` gate).
        zai_key = ""
        try:
            zai_key = cfg.zai_key_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        self._adapters = dict(
            adapters
            or {
                "pi": LastOrderPiAdapter(
                    cfg.pi_model,
                    # the desk has no body — the body URL never leaves this render's
                    # discarded mcp.json half (LastOrderPiAdapter._render overrides it)
                    body_base="http://no-body",
                    hive_url=cfg.hive_url,
                    node_key=cfg.lo_key,
                    zai_key=zai_key,
                    context_window=cfg.pi_context_window,
                    keep_recent_tokens=cfg.pi_keep_recent_tokens,
                    agent_dir=str(cfg.pi_agent_dir),
                    uid=cfg.sister_uid,
                    gid=cfg.sister_gid,
                ),
                "opencode": LastOrderAdapter(f"llm-gateway/{cfg.model}"),
            }
        )
        # The runner's rule (runner.py `_life`): a life starts on the primary harness;
        # quota marks switch to the failover for the sessions that follow, and the
        # next life is back on the primary. The desk's "life" is this process — the
        # switch lives here, a restart lands on pi again.
        self._harness_name = cfg.harness
        self._post_alarm = post_alarm or self._post_board_alarm
        self._spawn = spawn or self._spawn_child
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        cfg.workplace.mkdir(parents=True, exist_ok=True)
        self._log = log or HiveLog(cfg.workplace / ADMIN_DIR / "events.jsonl")
        self._wake = Wake(
            layout=self._layout,
            workplace=cfg.workplace,
            every_min=cfg.every_min,
            batch=cfg.batch,
        )

    # --- the workplace (P1-2, P1-3) --------------------------------------------------------

    def ensure_workplace(self) -> None:
        """Bootstrap and REPAIR: the core and the skills are rewritten from the image on
        every start (a tampered core is undone), the operator files are created once and
        then never touched by the alarm, `memory/` is only ever created. The wake
        baseline is taken here too: the world as it is at process start is no event."""
        root = self.cfg.workplace
        root.mkdir(parents=True, exist_ok=True)
        for name in CORE_FILES:
            self._rewrite_readonly(
                root / name, (self.cfg.template_dir / name).read_text(encoding="utf-8")
            )
        for name in CORE_DIRS:
            _force_rmtree(root / name)
            _copy_readonly_tree(self.cfg.template_dir / name, root / name)
        for name in OPERATOR_FILES:
            path = root / name
            if not path.exists():
                self._rewrite_readonly(
                    path, (self.cfg.template_dir / name).read_text(encoding="utf-8")
                )
        for name in AGENT_DIRS:
            (root / name).mkdir(parents=True, exist_ok=True)
            self._own_tree(root / name)
        self._rewrite_readonly(root / OPENCODE_CONFIG, json.dumps(self._opencode_config(), indent=2))
        (root / ADMIN_DIR).mkdir(parents=True, exist_ok=True)
        os.chmod(root / ADMIN_DIR, 0o755)
        self._load_state(self._clock())
        # the workplace root last: sticky + agent-writable, the T-027 boundary
        os.chmod(root, WORKPLACE_DIR_MODE)

    def _rewrite_readonly(self, path: Path, text: str) -> None:
        """The runner's `_write_readonly`, ROOT-owned (P1-2, stand finding): a file
        owned by the agent's uid is chmod-able by her — the read-only mode alone would
        not hold. The mode comes off for the instant of the atomic replace (a read-only
        TARGET cannot be replaced on Windows; the alarm runs as root, she cannot)."""
        if path.exists():
            try:
                os.chmod(path, 0o644)
            except OSError:
                pass
        _write_readonly(path, text)

    def _own_tree(self, path: Path) -> None:
        """The agent's zone: her uid and her modes, nothing else changed."""
        for item in _iter_no_links(path):
            if item.is_symlink():
                continue
            _own(item, self.cfg.sister_uid, self.cfg.sister_gid)
            os.chmod(item, SISTER_DIR if item.is_dir() else SISTER_FILE)

    def _opencode_config(self) -> dict[str, Any]:
        model = self.cfg.model
        return {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "model": f"llm-gateway/{model}",
            "provider": {
                "llm-gateway": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "LLM Gateway",
                    "options": {"baseURL": f"{self.cfg.gateway_url.rstrip('/')}/v1"},
                    "models": {model: {"name": "smart tier"}},
                }
            },
            "mcp": {
                "hive": {
                    "type": "remote",
                    "url": f"{self.cfg.hive_url.rstrip('/')}/last-order/mcp",
                    "enabled": True,
                    "headers": {"X-Node-Key": self.cfg.lo_key},
                }
            },
        }

    # --- state ------------------------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.cfg.workplace / ADMIN_DIR / STATE_FILE

    def _load_state(self, now: datetime) -> WakeState:
        """The persisted baseline; a fresh volume starts from the current world — the
        backlog present at boot is no wake (the timer picks it up within every_min)."""
        try:
            doc = json.loads(self._state_path().read_text(encoding="utf-8"))
            return WakeState(
                deaths=frozenset(int(serial) for serial in doc["deaths"]),
                proposals=frozenset(str(pid) for pid in doc["proposals"]),
                operator=tuple(str(d) for d in doc["operator"]),
                last_session_end=doc.get("last_session_end"),
                boot_at=str(doc.get("boot_at") or ""),
            )
        except (OSError, ValueError, KeyError, TypeError):
            state = self._wake.snapshot(now)
            state = WakeState(
                deaths=state.deaths,
                proposals=state.proposals,
                operator=state.operator,
                last_session_end=None,
                boot_at=now.isoformat(),
            )
            self._save_state(state)
            return state

    def _save_state(self, state: WakeState) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        payload = {
            "deaths": sorted(state.deaths),
            "proposals": sorted(state.proposals),
            "operator": list(state.operator),
            "last_session_end": state.last_session_end,
            "boot_at": state.boot_at,
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    # --- the loop ---------------------------------------------------------------------------

    def run(self) -> None:
        self.ensure_workplace()
        self._log.event(
            "admin.start",
            {
                "workplace": str(self.cfg.workplace),
                "every_min": self.cfg.every_min,
                "batch": self.cfg.batch,
                "harness": self.cfg.harness,
                "model": self.cfg.model,
            },
        )
        while True:
            try:
                self.run_once()
            except Exception as exc:  # service-grade: the loop outlives any single poll
                self._log.event("admin.error", {"error": f"{type(exc).__name__}: {exc}"})
            self._sleep(self.cfg.poll_s)

    def run_once(self) -> list[WakeReason]:
        """One poll; returns the reasons a session ran for (empty: no session)."""
        now = self._clock()
        state = self._load_state(now)
        reasons = self._wake.reasons(state, now)
        if not reasons:
            return []
        if state.last_session_end:
            try:
                end = parse_utc(state.last_session_end)
            except ValueError:
                end = None
            if end is not None and now - end < timedelta(seconds=self.cfg.session_min_gap_s):
                return []  # the budget floor: the events stay unconsumed and re-fire
        new_deaths = sorted(self._layout.will_serials() - state.deaths)
        # the whole baseline is taken NOW, at session start: whatever lands while the
        # session runs — a will, a proposal, an operator line — stays "new" for the
        # next poll. The operator files cannot be changed by the agent (P1-2), so a
        # changed digest is always the operator's and never the session's own echo.
        baseline = self._wake.snapshot(now)
        self._log.event(
            "session.start",
            {
                "reasons": [{"kind": r.kind, "detail": r.detail} for r in reasons],
                "new_deaths": [format_serial(s) for s in new_deaths],
            },
        )
        self._session(reasons, new_deaths)
        end_now = self._clock()
        self._save_state(
            WakeState(
                deaths=baseline.deaths,
                proposals=baseline.proposals,
                operator=baseline.operator,
                last_session_end=end_now.isoformat(),
                boot_at=baseline.boot_at,
            )
        )
        return reasons

    # --- the session ------------------------------------------------------------------------

    def _summary(self, reasons: list[WakeReason], new_deaths: list[int]) -> str:
        naming = naming_preset(self.cfg.naming)
        lines = ["Wake reasons: " + "; ".join(f"{r.kind} ({r.detail})" for r in reasons)]
        if new_deaths:
            names = ", ".join(
                f"{node_name(naming, serial)} ({format_serial(serial)})" for serial in new_deaths
            )
            lines.append(f"Deaths since your last session, wills filed: {names}.")
        pending = self._layout.pending_proposals()
        lines.append(f"Pending proposals: {len(pending)}")
        for doc in pending[:QUEUE_SNAPSHOT_MAX]:
            author = doc.get("author")
            author_text = format_serial(int(author)) if isinstance(author, int) else "?"
            lines.append(f"- {doc.get('id', '?')} · {doc.get('kind', '?')} · by {author_text}")
        inbox = self._workplace_excerpt(OPERATOR_FILES[0])
        if inbox.strip():
            lines.append(f"Operator INBOX.md:\n{inbox.strip()}")
        goals = self._workplace_excerpt(OPERATOR_FILES[1])
        if goals.strip():
            lines.append(f"Operator GOALS.md:\n{goals.strip()}")
        base_goals = self._layout.excerpt(GOALS_REL)
        if base_goals.strip():
            lines.append(f"Current network goals (base/goals.md):\n{base_goals.strip()}")
        tail = self._layout.chronicle_tail()
        if tail:
            lines.append(f"Chronicle tail:\n{tail}")
        return "\n\n".join(lines)

    def _workplace_excerpt(self, name: str) -> str:
        try:
            return (self.cfg.workplace / name).read_text(encoding="utf-8")[:SUMMARY_EXCERPT_CHARS]
        except OSError:
            return ""

    def _session(self, reasons: list[WakeReason], new_deaths: list[int]) -> None:
        adapter = self._adapters[self._harness_name]
        prompt = (
            f"{PROMPT_PREFIX}\n\n{self._summary(reasons, new_deaths)}\n\n"
            + (self.cfg.workplace / "SESSION_PROMPT.md").read_text(encoding="utf-8")
        )
        process = self._safe_spawn(adapter.session(prompt))
        output = ""
        if process is not None:
            deadline = time.monotonic() + self.cfg.session_timeout_s
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if process.poll() is None:
                process.terminate()
                if process.poll() is None:
                    process.wait(self.cfg.kill_grace_s)
                process.kill()
                self._log.event(
                    "session.timeout",
                    {"timeout_s": self.cfg.session_timeout_s},
                )
                if process.poll() is None:
                    # SIGKILL is delivered but the child sits in UNINTERRUPTIBLE IO
                    # (a D-state on a stalled host volume, seen on the stand): waiting
                    # for it would freeze the alarm forever. It is condemned — it dies
                    # the moment the IO returns — so the desk moves on without it.
                    process.wait(self.cfg.kill_grace_s)
                    if process.poll() is None:
                        self._log.event("session.abandoned", {})
                        return
            process.wait()
            output = process.output()
            quota = quota_exhausted(output)
            self._log.event(
                "session.done",
                {
                    "code": process.returncode(),
                    "harness": self._harness_name,
                    "quota": quota,
                    "tail": " ".join(output[-300:].split()),
                },
            )
            if quota:
                self._quota_switch()

    def _adapter_ready(self, name: str) -> bool:
        """A switch target must have its credential rendered (the runner's
        `_adapter_ready`): the Z.ai token the entrypoint materialized for pi, the
        gateway auth it rendered for opencode. Missing means not ready — switching
        there would only stack spawn failures."""
        if name == "pi":
            return self.cfg.zai_key_file.exists()
        if name == "opencode":
            return self.cfg.opencode_auth_file.exists()
        return True

    def _quota_switch(self) -> None:
        """Quota marks in a session's output move the NEXT session to the failover
        harness (the runner's `quota.switch`) and raise the board alarm — the desk
        left the subscription, the operator should know. Without a ready failover
        the desk waits on the same harness (the runner's `quota.waits`)."""
        target = self.cfg.failover
        if not target or target == self._harness_name or not self._adapter_ready(target):
            self._log.event("quota.waits", {"harness": self._harness_name})
            return
        self._log.event("quota.switch", {"from": self._harness_name, "to": target})
        self._harness_name = target
        try:
            self._post_alarm(
                "harness_failover",
                "last-order harness failover: the Z.ai quota ran out, sessions moved to "
                f"{target} through the gateway until the process restarts",
            )
        except OSError as exc:
            self._log.event("alarm.post_failed", {"error": f"{type(exc).__name__}: {exc}"})

    def _post_board_alarm(self, alarm_type: str, text: str) -> None:
        """The environment's alarm channel (ADR-036): the Last Order key rides
        X-Node-Key, the board stamps the author `admin`."""
        url = f"{self.cfg.hive_url.rstrip('/')}/board/alarms"
        request = urllib.request.Request(
            url,
            data=json.dumps({"type": alarm_type, "text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Node-Key": self.cfg.lo_key},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()

    def _safe_spawn(self, command: Command):
        try:
            return self._spawn(command.argv, self.cfg.workplace, self._child_env(command.env))
        except OSError as exc:
            self._log.event("session.spawn_failed", {"error": f"{type(exc).__name__}: {exc}"})
            return None

    def _child_env(self, extra: Mapping[str, str]) -> dict[str, str]:
        """A fresh environment for the harness: nothing of this container's env rides
        into a session — the keys live in files and configs, not in the child env."""
        home = "/home/sister" if Path("/home/sister").exists() else str(Path.home())
        env_ = {
            "HOME": home,
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "NO_COLOR": "1",
            "NO_UPDATE_NOTIFIER": "1",
        }
        env_.update(extra)
        return env_

    def _spawn_child(self, argv: tuple[str, ...], cwd: Path, env: dict[str, str]):
        preexec = None
        if self.cfg.sister_uid is not None and hasattr(os, "setuid"):
            uid, gid = self.cfg.sister_uid, self.cfg.sister_gid or self.cfg.sister_uid

            def _drop() -> None:  # the session runs as the agent's uid (P1-3)
                os.setgroups([])
                os.setgid(gid)
                os.setuid(uid)
                os.umask(0o022)

            preexec = _drop
        popen = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
            preexec_fn=preexec,
        )
        return SessionProcess(popen)


def _iter_no_links(root: Path):
    yield root
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            yield Path(dirpath) / name


def _copy_readonly_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst)
    _chmod_tree(dst, READ_ONLY_DIR, READ_ONLY_FILE)


def main() -> None:
    AdminAlarm(AdminConfig.from_env()).run()


if __name__ == "__main__":
    main()
