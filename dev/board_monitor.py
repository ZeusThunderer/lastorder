"""Read-only stand observation, independent of command execution."""

from __future__ import annotations

import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor


SLOTS = ("1", "2", "3")
SERVICES = ("last-order", "tts", "infra", *(
    f"{name}-{slot}" for slot in SLOTS for name in ("node", "mind", "body")
))
INSPECT = (
    '{{index .Config.Labels "com.docker.compose.service"}} {{.Id}} {{.State.Running}} '
    '{{with index .State "Health"}}{{.Status}}{{else}}-{{end}}'
)


def observe(project: str, *, busy: bool, run=subprocess.run) -> dict:
    def docker(*args: str) -> str | None:
        try:
            result = run(["docker", *args], text=True, capture_output=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout if result.returncode == 0 else None

    listed = docker("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}")
    ids = [word for word in (listed or "").split() if re.fullmatch(r"[a-f0-9]{12,64}", word)]
    inspected = docker("inspect", "--format", INSPECT, *ids) if ids else listed
    docker_available = inspected is not None
    containers = {}
    for line in (inspected or "").splitlines():
        fields = line.split()
        if len(fields) != 4:
            continue
        service, ident, running, health = fields
        if service in SERVICES and re.fullmatch(r"[a-f0-9]{64}", ident):
            if service not in containers or running == "true":
                containers[service] = (ident, running == "true", None if health == "-" else health)

    def container(name: str) -> dict:
        _, running, health = containers.get(name, ("", False if docker_available else None, None))
        return {"running": running, "health": health if running else None}

    def running_id(name: str) -> str | None:
        ident, running, _ = containers.get(name, ("", False, None))
        return ident if running else None

    def probe(name: str, port: int, path: str) -> dict | None:
        ident = running_id(name)
        if not ident:
            return None
        code = (
            "from urllib.request import urlopen; import sys; "
            "sys.stdout.buffer.write(urlopen(sys.argv[1], timeout=2).read(65536))"
        )
        raw = docker("exec", ident, "python3", "-c", code, f"http://127.0.0.1:{port}{path}")
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def lo_process() -> bool | None:
        ident = running_id("last-order")
        if not ident:
            return False if docker_available else None
        processes = docker("top", ident, "-eo", "args")
        if processes is None:
            return None
        return bool(re.search(
            r"^\s*(?:\S*/)?python(?:[0-9.]*)\s+-m\s+runtime\.hive\.admin(?:\s|$)",
            processes, flags=re.MULTILINE,
        ))

    with ThreadPoolExecutor(max_workers=5) as pool:
        lo = pool.submit(lo_process)
        voice = pool.submit(probe, "tts", 8790, "/health")
        runners = {s: pool.submit(probe, f"mind-{s}", 8700, "/runner/status") for s in SLOTS}
        health = voice.result()
        slots = {}
        for slot, runner in runners.items():
            status = runner.result() or {}
            slots[slot] = {
                **{name: container(f"{name}-{slot}") for name in ("mind", "node", "body")},
                "runner": {key: status.get(key) for key in ("serial", "dead", "term", "body")},
            }
        return {
            "docker": docker_available,
            "lo": {**container("last-order"), "process": lo.result()},
            "executor": {**container("infra"), "busy": busy},
            "voice": {
                **container("tts"),
                "reachable": (health is not None) if docker_available else None,
                "tts_ready": str(health["ready"]).lower() == "true" if health and "ready" in health else None,
                "stt_ready": health.get("stt_ready") if health else None,
                **({key: health.get(key) for key in ("backend", "model", "gpu", "gpu_uuid")}
                   if health else {}),
            },
            "slots": slots,
        }


def monitor(hive, project: str, stop, busy, log) -> None:
    unavailable = False
    while not stop.is_set():
        start = time.monotonic()
        try:
            code, _ = hive.call("POST", "/board/infra/status", observe(project, busy=busy.is_set()))
            healthy = code == 200
        except (OSError, ValueError):
            healthy = False
        if not healthy and not unavailable:
            log("board monitor: snapshot delivery unavailable")
        unavailable = not healthy
        stop.wait(max(0, 10 - (time.monotonic() - start)))
