#!/usr/bin/env python3
"""Spend report of the llm-gateway: per key and per model over a window, with the $/h rate.

    uv run python dev/gateway_report.py --since 1h

Numbers come from the gateway's spend tables (LiteLLM proxy + Postgres), read through
`docker compose exec` like dev/gateway_keys.py — the port is internal, the master key never
leaves the container. `--since` accepts combinations like 90m, 2h30m, 1d; the $/h rate is the
window spend divided by the window length, so short windows are noisy — this is an operator
view of ADR-035 ceilings, not an invoice.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gateway_keys import run_in_gateway  # noqa: E402


def parse_since(text: str) -> float:
    """Window length in seconds: sum of <N>d/<N>h/<N>m units, e.g. 2h30m."""
    seconds = 0.0
    for count, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhm])", text.lower()):
        seconds += float(count) * {"d": 86400, "h": 3600, "m": 60}[unit]
    if seconds <= 0 or re.sub(r"(\d+(?:\.\d+)?)\s*[dhm]|[\s,]", "", text.lower()):
        sys.exit(f"gateway_report: cannot parse --since {text!r} (want e.g. 90m, 2h30m, 1d)")
    return seconds


def fetch(since_s: float) -> dict:
    """Ask the proxy for spend by key, by model and in total over the window."""
    start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=since_s)).isoformat()
    end = dt.datetime.now(dt.timezone.utc).isoformat()
    per_key = run_in_gateway({"method": "GET",
                              "path": f"/global/spend/keys?start_date={start}&end_date={end}",
                              "body": None})
    per_model = run_in_gateway({"method": "GET",
                                "path": f"/global/spend/models?start_date={start}&end_date={end}",
                                "body": None})
    info = run_in_gateway({"method": "GET", "path": "/key/info?size=1000", "body": None})
    return {"per_key": per_key, "per_model": per_model, "keys": info}


def _rows(payload, total_field, name_fields) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for entry in payload if isinstance(payload, list) else payload.get("data", []):
        total = entry.get(total_field)
        if total is None:
            continue
        name = next((str(entry[f]) for f in name_fields if entry.get(f)), "unknown")
        rows.append((name, float(total)))
    return sorted(rows, key=lambda r: -r[1])


def render(data: dict, since_s: float) -> str:
    aliases = {}
    for entry in data["keys"].get("keys", data["keys"] if isinstance(data["keys"], list) else []):
        for field in ("key", "token", "api_key"):
            if entry.get(field):
                aliases[str(entry[field])] = entry.get("key_alias") or str(entry.get("key_alias") or "-")

    def named(row: tuple[str, float]) -> tuple[str, float]:
        return aliases.get(row[0], row[0]), row[1]

    key_rows = [named(r) for r in _rows(data["per_key"], "total_spend", ("api_key", "key", "token"))]
    model_rows = _rows(data["per_model"], "total_spend", ("model", "model_group"))
    total = sum(spend for _, spend in key_rows) or sum(spend for _, spend in model_rows)
    hours = since_s / 3600

    lines = [f"window: last {since_s / 3600:g} h"]
    lines.append("by key:")
    lines.extend(f"  {name:24} ${spend:.4f}" for name, spend in key_rows or [("(nothing spent)", 0.0)])
    lines.append("by model:")
    lines.extend(f"  {name:44} ${spend:.4f}" for name, spend in model_rows or [("(nothing spent)", 0.0)])
    lines.append(f"total ${total:.4f} -> ${total / hours:.4f}/h over the window")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="1h", help="window, e.g. 90m, 2h30m, 1d (default 1h)")
    parser.add_argument("--json", action="store_true", help="raw JSON instead of the table")
    args = parser.parse_args(argv)
    since_s = parse_since(args.since)
    data = fetch(since_s)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(render(data, since_s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
