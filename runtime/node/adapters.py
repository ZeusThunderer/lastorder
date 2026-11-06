"""Harness adapters (ADR-032): the workplace is harness-agnostic, the runner is not.

An adapter turns a session intent into a `Command` (argv + extra child env) and reads
the harness's stdout back: the session id to resume (`zcode --resume <id>`; opencode's
`--continue` needs none) and the quota-exhaustion marks (NETWORK.md section 9: the
subscription ran out, or the gateway's virtual key ceiling answered 429). Everything
else — the workplace, the MCP configs, the prompts — is the runner's.

The Z.ai provider config and the opencode auth store live in the sister's home and are
rendered by the container entrypoint (ADR-033): no key is passed through the child env.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import httpx

from runtime.node.workplace import (
    HERMES_HOME,
    PI_AGENT_DIR,
    drop_hermes_config,
    drop_pi_agent_dir,
)


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)


# The continuation nudge when a harness ended its turn while the body still lives
# (the spike saw qwen "closing" a session four times per shift).
CONTINUE_MESSAGE = "Continue your shift session: observe, act, speak, wait_events."

# zcode: HTTP 429 and the plan's rate-limit code 1302 (setup README, "Грабли" #1), the
# 5-hour window's 1308 "Usage limit reached" (seen 2026-10-31, T-066); opencode through
# the gateway: the virtual-key ceiling answers 429. Text marks cover the harnesses'
# phrasings.
_QUOTA_MARKS = (
    re.compile(r"\b429\b"),
    re.compile(r"\b1302\b"),
    re.compile(r"\b1308\b"),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"\bquota\b", re.IGNORECASE),
)

# A whole harness dump is tens of thousands of JSON events with chat, coordinates and
# counters: a "429" coordinate or the word "quota" in a replica must not switch the
# adapter (review R2-7). Marks are searched only where a harness can state the problem:
# error/result JSON events and the tail of the output (where a fatal crash prints).
_QUOTA_SCAN_TAIL = 2000


def _quota_event(line: str) -> bool:
    line = line.strip()
    if not line.startswith("{"):
        return False
    try:
        doc = json.loads(line)
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    kind = doc.get("type", doc.get("event", ""))
    return isinstance(kind, str) and any(
        word in kind.lower() for word in ("error", "result")
    )


def quota_exhausted(output: str) -> bool:
    """True when the harness output says the quota/ceiling ran out (not a plain crash).
    Only error/result JSON events and the output tail are searched — see R2-7."""
    for line in output.splitlines():
        if _quota_event(line) and any(mark.search(line) for mark in _QUOTA_MARKS):
            return True
    return any(mark.search(output[-_QUOTA_SCAN_TAIL:]) for mark in _QUOTA_MARKS)


class OpenCodeAdapter:
    name = "opencode"

    def __init__(self, model: str):
        self._model = model

    def life_turn(self, prompt: str, session_id: str | None) -> Command:
        if session_id is None:
            return Command(
                (
                    "opencode", "run",
                    "--model", self._model,
                    "--format", "json",
                    "--title", "node-life",
                    prompt,
                )
            )
        return Command(
            (
                "opencode", "run",
                "--model", self._model,
                "--format", "json",
                "--continue",
                CONTINUE_MESSAGE,
            )
        )

    def will_session(self, prompt: str) -> Command:
        return Command(
            (
                "opencode", "run",
                "--model", self._model,
                "--format", "json",
                "--title", "node-will",
                prompt,
            )
        )

    def session_id(self, output: str) -> str | None:
        return None  # `--continue` resumes the latest session of the working directory


class ZCodeAdapter:
    name = "zcode"

    def __init__(self, model: str, mode: str = "yolo"):
        self._model = model
        self._mode = mode

    def _base(self) -> tuple[str, ...]:
        return (
            "--json", "--no-color",
            "--mode", self._mode,
            "--model", f"zai-cp/{self._model}",
        )

    def life_turn(self, prompt: str, session_id: str | None) -> Command:
        if session_id is None:
            return Command(("zcode", "-p", prompt, *self._base()))
        # A resume needs the id the last run printed; without one there is nothing to
        # resume and a fresh first prompt rebuilds the context from the workplace files
        # (the same call the spike's loop makes).
        return Command(("zcode", "--resume", session_id, "-p", CONTINUE_MESSAGE, *self._base()))

    def will_session(self, prompt: str) -> Command:
        return Command(("zcode", "-p", prompt, *self._base()))

    def session_id(self, output: str) -> str | None:
        """The last sessionId of the JSON lines on stdout (one line per headless run)."""
        found: str | None = None
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            value = doc.get("sessionId")
            if isinstance(value, str) and value:
                found = value
        return found


# Pi (ADR-041, the primary node harness; T-066 runs it directly, T-045 wraps it in
# `hapi pi` when the console hub is reachable). Direct mode: one `pi -p --mode json`
# per session intent, like zcode, the prompt as the last argv word. Hapi mode: ONE
# LONG `hapi pi` process per life — stdin belongs to HAPI (the operator speaks into
# the session mid-life, ADR-042), so the adapter never passes a prompt through argv:
# a fresh session gets its opener delivered over the hub's REST (`deliver_prompt`,
# the way T-044 verified: POST /api/sessions/{id}/messages), and everything the
# runner needs off stdout in direct mode — the session id, the spend — is read from
# Pi's own session files instead (`sessions/<cwd-slug>/<ts>_<id>.jsonl`; the first
# record carries the id, assistant records carry `usage`, verified on the t048
# stand). The agent-dir configs (models/mcp/settings) are re-rendered before EVERY
# session in both modes — the dir is the sister's and the will session points hive
# at the proxy (the Hermes contract, R1-1), so a replaced file (R2-3) never survives
# into the next session. `--no-approve` ignores project `.pi/` resources (settings,
# MCP, extensions the sister could plant in her workplace); AGENTS.md still loads —
# context files ignore trust (Pi docs security.md). `--no-skills` + `--skill skills`:
# exactly the workplace skills, no stubs. The Z.ai key never rides the process env
# (a bash tool's `env` would copy it into the session file and the workplace
# archive): it lives in the sister's 0400 zai-key file in the agent dir, cat'ed at
# request time by models.json's `!command` apiKey — the zcode provider-config
# exposure (ADR-033 exception).
PI_SESSION_DIR = ".pi-sessions"
HAPI_HUB_URL = "http://hapi-hub:3006"
HAPI_HOME = Path("/home/sister/.hapi")
HAPI_BOOT_TIMEOUT_S = 90.0  # waiting for the wrapper to report its session
# the wrapper's boot report (the same lines T-043's admin lives on): an rpc Pi
# writes NO session file until its first turn lands (verified live on the stand:
# an idle session leaves the sessions dir empty), so the log is the only early
# signal that the session exists. TWO ids ride it: the HUB session id («Reporting
# session … to runner» — what the hub's REST addresses) and the PI session id
# («Session ID persisted to metadata» — what `--session-id` resumes, and the name
# of the session file that appears once turns land; verified: a metadata-id POST
# answers 404, the T-043 desk resumes on the pi id).
HAPI_SESSION_RE = re.compile(r"Session ID persisted to metadata: ([0-9a-fA-F-]{8,64})")
HAPI_HUB_SESSION_RE = re.compile(r"Reporting session ([0-9a-fA-F-]{8,64}) to runner")


@dataclass(frozen=True)
class HapiBoot:
    pi_session_id: str  # the resume key; the spend file is <ts>_<this>.jsonl
    hub_session_id: str  # what /api/sessions/<id>/messages addresses


class PiAdapter:
    name = "pi"

    def __init__(
        self,
        model: str,
        *,
        body_base: str,
        hive_url: str,
        node_key: str,
        zai_key: str,
        context_window: int,
        keep_recent_tokens: int,
        agent_dir: str = str(PI_AGENT_DIR),
        uid: int | None = None,
        gid: int | None = None,
        slot: str = "",
        hapi_url: str = "",
        hapi_token: str = "",
        hapi_home: Path | None = None,
        boot_timeout_s: float = HAPI_BOOT_TIMEOUT_S,
        transport: httpx.BaseClient | None = None,
    ):
        if hapi_token and ":" not in hapi_token:
            # fail-closed (T-044 R1-1/R1-3): the BASE hub token reads every session
            # of the network and writes into them as the operator; only the
            # namespace form `<token>:node-N` may enter a sister's sandbox
            raise ValueError(
                "hapi_token must be the namespaced form (<token>:node-N), "
                "not the base console token"
            )
        self._model = model
        self._body_base = body_base
        self._hive_url = hive_url
        self._node_key = node_key
        self._zai_key = zai_key
        self._context_window = context_window
        self._keep_recent_tokens = keep_recent_tokens
        self._agent_dir = agent_dir
        self._uid = uid
        self._gid = gid
        self._slot = slot
        self._hapi_url = hapi_url
        self._hapi_token = hapi_token
        self._hapi_home = Path(hapi_home) if hapi_home is not None else HAPI_HOME
        self._boot_timeout_s = boot_timeout_s
        self._http = transport if transport is not None else httpx.Client(
            base_url=hapi_url, timeout=15.0
        )

    @property
    def hapi(self) -> bool:
        """The console wrapper is armed: a hub url AND a namespaced token."""
        return bool(self._hapi_url and self._hapi_token)

    @property
    def long_running(self) -> bool:
        """True when one process IS the whole session (rpc mode never ends its
        turn): the runner must not wait for an exit, it watches the session file
        and the death status instead."""
        return self.hapi

    def _render(self) -> None:
        drop_pi_agent_dir(
            Path(self._agent_dir),
            model=self._model,
            body_base=self._body_base,
            hive_url=self._hive_url,
            node_key=self._node_key,
            zai_key=self._zai_key,
            slot=self._slot,
            context_window=self._context_window,
            keep_recent_tokens=self._keep_recent_tokens,
            uid=self._uid,
            gid=self._gid,
        )

    def _command(self, prompt: str, resume: str | None) -> Command:
        if self.hapi:
            # stdin is HAPI's: no prompt in argv (deliver_prompt posts the opener),
            # the token rides the env of THIS child alone (the namespaced form —
            # its reach is this slot's sessions only, T-044)
            argv: tuple[str, ...] = ("hapi", "pi")
            if resume is not None:
                argv += ("--session-id", resume)
            return Command(
                argv,
                env={
                    "PI_CODING_AGENT_DIR": self._agent_dir,
                    "HAPI_API_URL": self._hapi_url,
                    "CLI_API_TOKEN": self._hapi_token,
                    "HAPI_HOME": str(self._hapi_home),
                },
            )
        argv = (
            "pi", "-p", "--mode", "json",
            "--session-dir", PI_SESSION_DIR,
            "--no-approve", "--offline",
            "--no-skills", "--skill", "skills",
        )
        if resume is not None:
            argv += ("--session", resume)
        # no ZAI_API_KEY here: the key travels as the zai-key file (see _render),
        # never through an env a bash tool could print
        return Command(
            argv + ("--", prompt),
            env={"PI_CODING_AGENT_DIR": self._agent_dir},
        )

    def life_turn(self, prompt: str, session_id: str | None) -> Command:
        self._render()  # back onto the real hive after a will, heals a replaced file
        if session_id is None:
            return self._command(prompt, None)
        return self._command(CONTINUE_MESSAGE, session_id)

    def will_session(self, prompt: str) -> Command:
        # No render here: set_will_mode has just pointed the hive MCP at the proxy.
        return self._command(prompt, None)

    def session_id(self, output: str) -> str | None:
        """The id of the last `session` header on stdout (the first JSON-mode record).
        Direct mode only: hapi owns the wrapper's stdout, so in hapi mode the ids
        come from the session files (`new_session_ids`/`session_file`)."""
        found: str | None = None
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if not isinstance(doc, dict) or doc.get("type") != "session":
                continue
            value = doc.get("id")
            if isinstance(value, str) and value:
                found = value
        return found

    # --- the Pi session files (hapi mode's source of truth) -----------------------------

    def _sessions_root(self) -> Path:
        return Path(self._agent_dir) / "sessions"

    def _session_files(self) -> list[Path]:
        try:
            return sorted(self._sessions_root().glob("*/*.jsonl"))
        except OSError:
            return []

    def boot_report(self, spawned_at: float) -> HapiBoot | None:
        """The wrapper's report for THIS spawn, off the hapi logs written after
        `spawned_at` (a wall-clock epoch). One log file per wrapper process, so
        an old spawn's ids cannot leak in; a file carrying several session lines
        keeps the last of each (the admin's rule, T-043). None until BOTH ids
        are there: the hub session exists but the agent is not up yet."""
        pi_id = ""
        hub_id = ""
        for path in self._hapi_log_paths(spawned_at):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            pi_matches = HAPI_SESSION_RE.findall(text)
            hub_matches = HAPI_HUB_SESSION_RE.findall(text)
            if pi_matches:
                pi_id = pi_matches[-1]
            if hub_matches:
                hub_id = hub_matches[-1]
        if pi_id and hub_id:
            return HapiBoot(pi_session_id=pi_id, hub_session_id=hub_id)
        return None

    def _hapi_log_paths(self, spawned_at: float) -> list[Path]:
        try:
            return [
                path
                for path in (self._hapi_home / "logs").glob("*.log")
                if path.stat().st_mtime >= spawned_at - 1.0
            ]
        except OSError:
            return []

    def session_file(self, session_id: str) -> Path | None:
        """The Pi session file of one id (`<ts>_<id>.jsonl` under a cwd slug)."""
        suffix = f"_{session_id}.jsonl"
        for path in self._session_files():
            if path.name.endswith(suffix):
                return path
        return None

    def session_progress(self, session_id: str) -> int | None:
        """The session file's size in bytes — the `stuck` watch's progress signal:
        every model turn appends records. None when there is no file yet."""
        path = self.session_file(session_id)
        try:
            return path.stat().st_size if path is not None else None
        except OSError:
            return None

    def session_spend(self, session_id: str) -> dict[str, float] | None:
        """The session's usage summed over its assistant records (the shape
        verified on the t048 stand: `usage.totalTokens` and `usage.cost.total`),
        or None without a file. Telemetry for the report — the file is the
        sister's, so a forged record can only lie about her own spend."""
        path = self.session_file(session_id)
        if path is None:
            return None
        tokens = 0
        cost = 0.0
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        for line in lines:
            line = line.strip()
            if not line.startswith("{") or '"usage"' not in line:
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            message = doc.get("message") if isinstance(doc, dict) else None
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                continue
            tokens += int(usage.get("totalTokens") or 0)
            price = usage.get("cost")
            if isinstance(price, dict):
                cost += float(price.get("total") or 0.0)
        return {"tokens": tokens, "cost_usd": cost}

    def output_of(self, process_output: str, spawned_at: float) -> str:
        """What quota_exhausted should scan: the child's own stdout plus — in
        hapi mode — the wrapper's log for THIS spawn (the wrapper writes provider
        errors to ~/.hapi/logs/*.log, not to stdout; verified on the stand,
        T-043/T-048). Direct mode: stdout alone, as before."""
        if not self.hapi:
            return process_output
        return process_output + self._hapi_log_text(spawned_at)

    def _hapi_log_text(self, spawned_at: float) -> str:
        try:
            newest = max(
                self._hapi_log_paths(spawned_at), key=lambda path: path.stat().st_mtime
            )
        except (OSError, ValueError):
            return ""
        try:
            return newest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # --- the hub side of the console (hapi mode) ----------------------------------------

    def deliver_prompt(self, session_id: str, text: str) -> None:
        """Post the session opener into the hub session: the operator's own
        channel (POST /api/sessions/{id}/messages, verified in T-044) — the only
        way a prompt can enter a session whose stdin belongs to HAPI. Raises on
        any refusal: the caller decides between a retry and a restart."""
        token = self._hapi_login()
        response = self._http.post(
            f"/api/sessions/{session_id}/messages",
            json={"text": text},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()

    def _hapi_login(self) -> str:
        response = self._http.post("/api/auth", json={"accessToken": self._hapi_token})
        response.raise_for_status()
        token = response.json().get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("the hub answered /api/auth without a token")
        return token


# Hermes Agent (NousResearch, MIT) — the third node harness (ADR-032), on the Z.ai
# GLM Coding Plan like zcode. Everything static lives in HERMES_HOME: config.yaml
# (provider `zai`, both MCP servers, toolsets; laid out by the entrypoint and
# RE-RENDERED by the adapter before every life session — review R1-1, the
# generations of a slot share the one home) and .env with GLM_API_KEY/GLM_BASE_URL.
# The adapter only drives the headless CLI: one oneshot `hermes chat` per session
# intent, the prompt as a file (never through a shell or argv — the SESSION_PROMPT
# is kilobytes), stream-json on stdout for the session id and the quota marks.
# Toolsets are pinned here AND in config.yaml: the CLI flag is the per-run surface,
# the config is what any other Hermes entry would see. In Hermes v0.22.4 the
# -t/--toolsets value does two things (hermes_cli source of the pinned release):
# it is the MCP server spawn allowlist (mcp_startup.set_mcp_server_filter matches
# bare server names) and it rides the gateway chat policy into the agent's
# enabled_toolsets — a server or toolset not named there never reaches the model.
# The bare names are the aliases of the mcp-<name> toolsets the servers register
# (tools/mcp_tool_registration.py), so they double as toolsets legally.
HERMES_TOOLSETS = ("file", "terminal", "body", "hive")


class HermesAdapter:
    name = "hermes"

    def __init__(
        self,
        model: str,
        *,
        body_base: str,
        hive_url: str,
        node_key: str,
        home: str = str(HERMES_HOME),
    ):
        self._model = model
        self._body_base = body_base
        self._hive_url = hive_url
        self._node_key = node_key
        self._home = home
        # One scratch file, overwritten per turn: the runner never runs two sessions
        # at once, and a crash-loop must not litter the tmpfs with prompts.
        self._query_file = Path(tempfile.gettempdir()) / "lastorder-hermes-prompt.txt"

    def _base(self) -> tuple[str, ...]:
        return (
            "--oneshot", "--format", "stream-json", "--yolo",
            "--toolsets", ",".join(HERMES_TOOLSETS),
        )

    def _command(self, prompt: str, resume: str | None) -> Command:
        self._query_file.write_text(prompt, encoding="utf-8")
        argv: tuple[str, ...] = (
            "hermes", "chat", "--query-file", str(self._query_file), *self._base()
        )
        if resume is not None:
            argv += ("--resume", resume)
        return Command(argv, env={"HERMES_HOME": self._home})

    def life_turn(self, prompt: str, session_id: str | None) -> Command:
        # R1-1: one HERMES_HOME serves every generation of the slot and the nodes
        # change without a container restart — the will session of the previous
        # generation left the proxy URL in the config, so re-assert the real hive
        # here, right before hermes starts.
        drop_hermes_config(
            Path(self._home),
            model=self._model,
            body_base=self._body_base,
            hive_url=self._hive_url,
            node_key=self._node_key,
        )
        # A resume carries only the continuation nudge: the session history is in
        # Hermes's state (HERMES_HOME/sessions), the context rebuilds from the
        # workplace files on a fresh first prompt — same contract as zcode.
        if session_id is None:
            return self._command(prompt, None)
        return self._command(CONTINUE_MESSAGE, session_id)

    def will_session(self, prompt: str) -> Command:
        # No config render here: the runner's set_will_mode has just pointed the
        # hive MCP at the will proxy, and a render would clobber it (R1-1).
        return self._command(prompt, None)  # a fresh session, like the other two

    def session_id(self, output: str) -> str | None:
        """The last non-empty session_id of the stream-json events on stdout:
        `system/init` may print an empty one before the gateway binds the real id,
        the terminal `result` event always carries it."""
        found: str | None = None
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            value = doc.get("session_id")
            if isinstance(value, str) and value:
                found = value
        return found
