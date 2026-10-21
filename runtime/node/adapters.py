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
from dataclasses import dataclass, field
from typing import Mapping


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


def quota_exhausted(output: str) -> bool:
    """True when the harness output says the quota/ceiling ran out (not a plain crash)."""
    return any(mark.search(output) for mark in _QUOTA_MARKS)


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
