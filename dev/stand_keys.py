#!/usr/bin/env python3
"""Prepare the stand .env: local tokens generated, gateway virtual keys issued (T-036).

    uv run python dev/stand_keys.py init  [--env PATH]   # local tokens only
    uv run python dev/stand_keys.py issue [--env PATH]   # GATEWAY_KEY_* via the gateway

Why: a stand raised without keys is the dev-open failure of T-032 — 11 of 13 wills emergency,
the gateway and last-order never come up. `init` fills the missing LOCAL tokens with
secrets.token_urlsafe (existing values are never overwritten); `issue` asks the running
llm-gateway for the missing GATEWAY_KEY_* virtual keys through dev/gateway_keys.py (ADR-035
ceilings by default) and writes them next to the rest.

Values are never printed — only key names and added/kept/issued, so the output is safe to
paste into a report or a ticket. Provider keys (OPENROUTER_API_KEY, ZAI_API_KEY) are the
operator's: the script does not create or touch them, it only names the missing ones.

Order of a stand bring-up (docs/agents/docker-topology.md, «Ключи стенда»):
    init -> bash docker/gateway/up.sh -> issue -> bash docker/node/up.sh
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gateway_keys  # noqa: E402  (dev/ is not a package; same trick as tests use)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / ".env"

# Local secrets of the stand: generated in place with secrets.token_urlsafe(32). The names are
# what docker/node/up.sh lifts and docker/compose.yml interpolates —
# tests/test_stand_keys.py pins the lists against those files, so a rename here fails a test.
# GATEWAY_MASTER_KEY gets the sk- prefix LiteLLM requires (the recipe docker/gateway/up.sh
# prints when it is missing); GATEWAY_DB_PASSWORD is interpolated into DATABASE_URL as-is, and
# token_urlsafe's alphabet (A-Za-z0-9_-) has none of the @:/#? that break that URL.
# HAPI_CLI_KEY rides as CLI_API_TOKEN into the hapi-hub container (T-044): the hub
# accepts a preset token from env (env > settings.json > generate), the sandboxes' `hapi`
# CLI authenticates with it and the operator logs into the HAPI web page with the same
# value — the web login token never exists as a separate secret.
LOCAL_TOKENS = [
    "GATEWAY_MASTER_KEY",
    "GATEWAY_DB_PASSWORD",
    "HAPI_CLI_KEY",
    "HIVE_MCP_LO_KEY",
    "HIVE_OPERATOR_KEY",
    "HIVE_TOOL_KEY",
    "HIVE_NODE_KEY_1", "HIVE_NODE_KEY_2", "HIVE_NODE_KEY_3",
    "HIVE_RUNNER_KEY_1", "HIVE_RUNNER_KEY_2", "HIVE_RUNNER_KEY_3",
    "RCON_PASSWORD",
]

# One virtual key per gateway consumer (T-026); the alias is the label dev/gateway_keys.py list
# shows. Ceilings stay the ADR-035 defaults of gateway_keys.issue_key.
VIRTUAL_KEYS = {
    "GATEWAY_KEY_HIVE": "hive",
    "GATEWAY_KEY_LO": "last-order",
    "GATEWAY_KEY_MIND_1": "mind-1", "GATEWAY_KEY_MIND_2": "mind-2", "GATEWAY_KEY_MIND_3": "mind-3",
    "GATEWAY_KEY_NODE_1": "node-1", "GATEWAY_KEY_NODE_2": "node-2", "GATEWAY_KEY_NODE_3": "node-3",
}

# Read by the gateway / the zcode harness; bought by the operator, never generated here.
# ZAI_API_KEY is the .env name (docker/node/up.sh lifts it as ZCODE_API_KEY).
PROVIDER_KEYS = ["OPENROUTER_API_KEY", "ZAI_API_KEY"]

KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def effective_value(raw: str) -> str:
    """The value as docker/node/up.sh `lift` sees it: CR stripped, one quote pair removed."""
    val = raw.rstrip("\r\n").strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val


def line_key(line: str) -> str | None:
    match = KEY_LINE.match(line.rstrip("\r\n")) if not line.lstrip().startswith("#") else None
    return match.group(1) if match else None


def generate(name: str) -> str:
    token = secrets.token_urlsafe(32)
    return f"sk-{token}" if name == "GATEWAY_MASTER_KEY" else token


def fill_env(path: Path, values: dict[str, str]) -> tuple[list[str], list[str]]:
    """Write values for keys that are missing or empty; keep everything else byte-for-byte.

    First occurrence wins, like the `head -1` of up.sh lift. Returns (filled, kept) names.
    """
    lines: list[str] = []
    if path.exists():
        with open(path, encoding="utf-8", newline="") as fh:
            lines = fh.readlines()
    filled: list[str] = []
    kept: list[str] = []
    for name, value in values.items():
        idx = next((i for i, line in enumerate(lines) if line_key(line) == name), None)
        if idx is None:
            if lines and not lines[-1].endswith(("\n", "\r\n")):
                lines[-1] += "\n"
            lines.append(f"{name}={value}\n")
        elif effective_value(lines[idx].split("=", 1)[1]):
            kept.append(name)  # an existing value is the operator's; never overwrite
            continue
        else:
            ending = "\r\n" if lines[idx].endswith("\r\n") else "\n"
            lines[idx] = f"{name}={value}{ending}"
        filled.append(name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(lines)
    return filled, kept


def missing_keys(path: Path, names: list[str] | dict[str, str]) -> list[str]:
    """Names from `names` that are absent or empty in the env file."""
    wanted = list(names) if isinstance(names, dict) else names
    if not path.exists():
        return list(wanted)
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    out = []
    for name in wanted:
        line = next((ln for ln in lines if line_key(ln) == name), None)
        if line is None or not effective_value(line.split("=", 1)[1]):
            out.append(name)
    return out


def report(action: str, names: list[str]) -> None:
    for name in names:
        print(f"{action:<7} {name}")


def report_providers(path: Path) -> list[str]:
    """Name the provider keys the stand still needs; they are never generated here."""
    missing = missing_keys(path, PROVIDER_KEYS)
    for name in missing:
        print(f"missing provider key (set it by hand): {name}")
    return missing


def cmd_init(args: argparse.Namespace) -> int:
    env = Path(args.env)
    values = {name: generate(name) for name in LOCAL_TOKENS}
    filled, kept = fill_env(env, values)
    report("added", filled)
    report("kept", kept)
    report_providers(env)
    not_issued = missing_keys(env, VIRTUAL_KEYS)
    if not_issued:
        print("virtual keys not set: " + ", ".join(not_issued))
        print("raise the gateway (bash docker/gateway/up.sh), then: "
              "uv run python dev/stand_keys.py issue")
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    env = Path(args.env)
    todo = missing_keys(env, VIRTUAL_KEYS)
    report("kept", [name for name in VIRTUAL_KEYS if name not in todo])
    if not todo:
        report_providers(env)
        return 0
    tokens: dict[str, str] = {}
    for name in todo:
        alias = VIRTUAL_KEYS[name]
        try:
            # the gateway must be up; gateway_keys exits with the how-to-start hint otherwise
            tokens[name] = gateway_keys.token_of(gateway_keys.issue_key(alias))
        except SystemExit as exc:  # nothing written yet: no half-issued .env
            sys.stderr.write(f"{exc}\n")
            return 1
    fill_env(env, tokens)
    report("issued", list(tokens))
    report_providers(env)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_: str, func) -> None:
        cmd = sub.add_parser(name, help=help_)
        cmd.add_argument("--env", default=str(DEFAULT_ENV),
                         help=f"env file to prepare (default: {DEFAULT_ENV})")
        cmd.set_defaults(func=func)

    add("init", "generate the missing local tokens (secrets.token_urlsafe)", cmd_init)
    add("issue", "issue the missing GATEWAY_KEY_* virtual keys through the gateway", cmd_issue)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
