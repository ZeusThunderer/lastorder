#!/usr/bin/env python3
"""Sample docker stats for the stand services: mean and max RAM and CPU.

    python dev/measure_resources.py [--services mc-server,body-1] [--samples 10] [--interval 5]

Runs on the host against a running stand (docker CLI on PATH). RAM is the container
memory docker reports, in MiB; CPU is docker's % of one core. The output table is
markdown, meant for the task report. Docker stats itself samples over ~1s per call,
so keep --interval >= 2.

Exit code is non-zero if any requested service is missing from docker stats.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time

MEM_UNITS = {  # to MiB
    "b": 1 / 1024**2,
    "kb": 1 / 1024,
    "mb": 1.0,
    "gb": 1024.0,
    "tb": 1024.0**2,
    "kib": 1 / 1024,
    "mib": 1.0,
    "gib": 1024.0,
    "tib": 1024.0**2,
}


def parse_mem(value: str) -> float:
    """'1234MiB / 3GiB' -> 1234.0 (MiB, the used part only)."""
    used = value.split("/")[0].strip()
    number, unit = used[:-3], used[-3:].lower()
    if unit not in MEM_UNITS:
        raise ValueError(f"unknown memory unit in {value!r}")
    return float(number) * MEM_UNITS[unit]


def parse_cpu(value: str) -> float:
    """'30.50%' -> 30.5."""
    return float(value.strip().rstrip("%"))


def docker_stats() -> dict[str, dict[str, str]]:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {row["Name"]: row for row in map(json.loads, out.splitlines())}


def match(rows: dict[str, dict[str, str]], project: str, service: str) -> str:
    """Compose names a container {project}-{service}-{n}; one running instance expected."""
    prefix = f"{project}-{service}"
    names = [n for n in rows if n == prefix or n.startswith(prefix + "-")]
    if not names:
        raise LookupError(f"no container for service {service!r} (looked for {prefix}*)")
    return names[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--services", default="mc-server,body-1",
                    help="comma-separated compose service names (default %(default)s)")
    ap.add_argument("--project", default="lastorder", help="compose project name (default %(default)s)")
    ap.add_argument("--samples", type=int, default=10, help="how many docker stats snapshots")
    ap.add_argument("--interval", type=float, default=5, help="seconds between snapshots")
    ap.add_argument("--label", default="", help="free-form run label printed above the table")
    args = ap.parse_args()

    services = [s.strip() for s in args.services.split(",") if s.strip()]
    series: dict[str, list[tuple[float, float]]] = {s: [] for s in services}

    # Fail fast on a missing service before burning the sampling window.
    first = docker_stats()
    names = {}
    for service in services:
        try:
            names[service] = match(first, args.project, service)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    started = time.strftime("%Y-%m-%d %H:%M:%S")
    for i in range(args.samples):
        rows = docker_stats()
        for service, name in names.items():
            row = rows[name]
            series[service].append((parse_mem(row["MemUsage"]), parse_cpu(row["CPUPerc"])))
        if i < args.samples - 1:
            time.sleep(args.interval)

    label = args.label or "docker stats"
    print(f"\n### {label} — {started}, {args.samples} samples x {args.interval:g}s\n")
    print("| service | RAM avg | RAM max | CPU avg | CPU max |")
    print("|---|---|---|---|---|")
    for service in services:
        mems = [m for m, _ in series[service]]
        cpus = [c for _, c in series[service]]
        print(
            f"| {service} | {statistics.mean(mems):.0f} MiB | {max(mems):.0f} MiB "
            f"| {statistics.mean(cpus):.1f}% | {max(cpus):.1f}% |"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
