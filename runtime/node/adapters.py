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

from runtime.node.workplace import HERMES_HOME, drop_hermes_config


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)


# The continuation nudge when a harness ended its turn while the body still lives
# (the spike saw qwen "closing" a session four times per shift).
CONTINUE_MESSAGE = "Continue your shift session: observe, act, speak, wait_events."

# zcode: HTTP 429 and the plan's rate-limit code 1302 (setup README, "Грабли" #1);
# opencode through the gateway: the virtual-key ceiling answers 429. Text marks cover
# both harnesses' phrasings.
_QUOTA_MARKS = (
    re.compile(r"\b429\b"),
    re.compile(r"\b1302\b"),
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
# -t/--toolsets value is ALSO the MCP server spawn allowlist
# (hermes_cli/mcp_startup.set_mcp_server_filter matches bare server names): without
# `body`/`hive` in the list the servers never start — the T-032 run drove the body
# and the network through 803 terminal calls with zero MCP. The bare names are the
# aliases of the mcp-<name> toolsets the servers register
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
