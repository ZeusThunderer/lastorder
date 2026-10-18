#!/usr/bin/env python3
"""Issue, revoke and list virtual keys of the llm-gateway (T-026, ADR-023/033/035).

    uv run python dev/gateway_keys.py issue --alias node-1 [--rpm 12] [--hourly-usd 0.12] \
        [--models openrouter/qwen/qwen3.7-flash,...]
    uv run python dev/gateway_keys.py revoke --key sk-...
    uv run python dev/gateway_keys.py list

One virtual key per consumer: a slot harness (node-1), Last Order (hive) and the mind of a
slot (cloud Jev). Ceilings ride the key (ADR-035): rpm is turns per minute, hourly-usd is the
recurring per-hour budget — past either the gateway answers 429/403 with an explicit reason
and the caller waits.

The gateway port is internal-only, so the script drives the proxy through
`docker compose exec llm-gateway python -`: the call itself runs inside the container with
stdlib urllib, the master key never leaves the container environment, and no secret ever
lands on an argv or in a file. The freshly issued token is printed once — paste it into .env
as GATEWAY_KEY_<consumer>; it is not recoverable later (that is the point).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT / "docker" / "compose.yml"
SERVICE = "llm-gateway"

# Default ceilings of a key (ADR-035, NETWORK.md section 8): ~2-6 turns/min per node with
# headroom for retries, and a per-hour dollar budget around the $0.35/h-three-nodes target.
DEFAULT_RPM = 12
DEFAULT_HOURLY_USD = 0.12

# Runs inside the llm-gateway container on stdin (`python -`): one admin HTTP call against the
# local proxy with the master key from the container environment. Prints the JSON response, or
# exits nonzero with the HTTP status and body — the host script surfaces that text as-is.
RUNNER = r'''
import json, os, sys, urllib.error, urllib.request

def call(spec):
    master = os.environ.get("LITELLM_MASTER_KEY", "").strip()
    if not master:
        sys.exit("no LITELLM_MASTER_KEY in the llm-gateway container: "
                 "set GATEWAY_MASTER_KEY in .env and recreate the service")
    req = urllib.request.Request(
        "http://127.0.0.1:4000" + spec["path"],
        data=json.dumps(spec["body"]).encode() if spec.get("body") is not None else None,
        headers={"Authorization": "Bearer " + master, "Content-Type": "application/json"},
        method=spec["method"])
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            print(resp.read().decode())
    except urllib.error.HTTPError as exc:
        sys.exit("gateway answered HTTP %s: %s" % (exc.code, exc.read().decode()[:1000]))
    except urllib.error.URLError as exc:
        sys.exit("gateway unreachable: %s" % exc)

call(%r)
'''


def build_issue_payload(alias: str, rpm: int, hourly_usd: float, models: list[str]) -> dict:
    """Payload of POST /key/generate: alias plus the two ADR-035 ceilings on the key."""
    payload = {
        "key_alias": alias,
        "rpm_limit": rpm,
        "max_budget": hourly_usd,
        "budget_duration": "1h",  # the budget recurs hourly, not once for the key's life
    }
    if models:
        payload["models"] = models
    return payload


def build_revoke_payload(key: str) -> dict:
    return {"keys": [key]}


def run_in_gateway(spec: dict) -> dict:
    """Run RUNNER inside the gateway container; returns the parsed JSON response."""
    if not COMPOSE_FILE.exists():
        sys.exit(f"gateway_keys: no {COMPOSE_FILE} (run from the repo root)")
    proc = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "exec", "-T", SERVICE, "python", "-"],
        input=RUNNER % (spec,),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if proc.returncode != 0:
        sys.exit(f"gateway_keys: {(proc.stderr or proc.stdout).strip()}")
    return json.loads(proc.stdout)


def cmd_issue(args: argparse.Namespace) -> int:
    response = run_in_gateway(
        {"method": "POST", "path": "/key/generate",
         "body": build_issue_payload(args.alias, args.rpm, args.hourly_usd, args.models)}
    )
    token = response.get("token") or response.get("key")
    if not token:
        sys.exit(f"gateway_keys: no token in the answer: {json.dumps(response)[:400]}")
    print(f"key alias   : {response.get('key_alias', args.alias)}")
    print(f"rpm limit   : {response.get('rpm_limit', args.rpm)}")
    print(f"hourly budget: {response.get('max_budget', args.hourly_usd)} USD "
          f"(resets {response.get('budget_duration', '1h')})")
    if response.get("models"):
        print(f"models      : {', '.join(response['models'])}")
    print(f"token       : {token}")
    print("paste the token into .env as GATEWAY_KEY_<consumer>; it is shown only once")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    response = run_in_gateway({"method": "POST", "path": "/key/delete",
                               "body": build_revoke_payload(args.key)})
    print(f"revoked: {', '.join(response.get('deleted_keys') or [args.key])}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    response = run_in_gateway({"method": "GET", "path": "/key/info?size=1000", "body": None})
    keys = response.get("keys", response if isinstance(response, list) else [])
    if not keys:
        print("no virtual keys")
        return 0
    print(f"{'alias':16} {'spend, USD':>10} {'rpm':>4} {'$/h':>6}  key")
    for entry in sorted(keys, key=lambda k: str(k.get("key_alias") or "")):
        alias = entry.get("key_alias") or "-"
        spend = entry.get("spend")
        spend = f"{float(spend):.4f}" if spend is not None else "-"
        print(f"{alias:16} {spend:>10} {str(entry.get('rpm_limit') or '-'):>4} "
              f"{str(entry.get('max_budget') or '-'):>6}  {entry.get('key', '-')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue", help="issue a virtual key with rpm and $/h ceilings")
    issue.add_argument("--alias", required=True, help="consumer name, e.g. node-1, hive, mind-1")
    issue.add_argument("--rpm", type=int, default=DEFAULT_RPM, help=f"turns per minute (default {DEFAULT_RPM})")
    issue.add_argument("--hourly-usd", type=float, default=DEFAULT_HOURLY_USD,
                       help=f"recurring budget per hour, USD (default {DEFAULT_HOURLY_USD})")
    issue.add_argument("--models", default="", help="comma-separated model_name allowlist "
                        "(default: every model of docker/gateway/config.yaml)")
    issue.set_defaults(func=cmd_issue)

    revoke = sub.add_parser("revoke", help="revoke a virtual key by its token")
    revoke.add_argument("--key", required=True, help="the sk-... token to revoke")
    revoke.set_defaults(func=cmd_revoke)

    list_keys = sub.add_parser("list", help="list virtual keys with spend and ceilings")
    list_keys.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    if args.command == "issue" and args.models:
        args.models = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        args.models = []
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
