#!/usr/bin/env python3
"""The infrastructure bridge executor (T-057, ADR-044; docs/architecture/TASKBOARD.md 5.4).

    uv run python dev/infra_bridge.py [--url URL] [--key KEY] [--interval S]
                                      [--dry-run] [--once] [--project NAME]

Runs on the HOST beside the stand — the operator starts it by hand; nothing inside
the docker network can. It polls the hive board for open infra requests (created by
Last Order alone), re-validates the closed action set of runtime/hive/infra.py — the
network in between is not trusted — and runs `docker compose` / `dev/slot_restart.sh`
as plain argv lists: no shell strings, no docker beyond the seven verbs. The outcome
returns to the board (`done`/`failed` with the command output truncated to 800
characters), where Last Order and the operator read it.

The switch: the operator turns the executor off on the board page. An off executor
takes nothing and runs nothing — the requests wait, and Last Order sees the reason
through her `infra_state` tool.

A crash mid-run leaves its request `taken`: the next start closes every such entry
as `failed` before polling new work, so nothing hangs forever and nothing re-runs.

`--dry-run` reads the switch and the queue and prints the commands it would run,
touching nothing. Exit codes: 0 worked, 1 the board is unreachable, 2 bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from runtime.hive.infra import (  # noqa: E402  (dev/ is not a package; same trick as tests)
    BODY_KINDS,
    DEFAULT_BODY_KIND,
    INFRA_ACTIONS,
    LINES_DEFAULT,
    RESULT_MAX,
    InfraError,
    body_service,
    check as check_infra,
)

STEP_TIMEOUT_S = 600  # one command; slot_restart waits for health itself (420 s inside)
GRACE_S = 420  # the body healthcheck budget, same number as dev/slot_restart.sh
# Last Order's server moves (T-072): docker/node/up.sh reads this file before .env, so
# a moved slot stays moved through any later restart; a slot sent home leaves it.
# In .stand/ — the one repo directory the infra container may write (ADR-050).
SERVERS_FILE = ROOT / ".stand" / "servers.env"
HOME_SERVER = "mc-server:25565"
# the poll loop touches it every round; the infra container's healthcheck reads its age
HEARTBEAT_FILE = Path(os.environ.get("INFRA_HEARTBEAT", "") or (ROOT / ".stand" / "heartbeat"))


@dataclass
class Step:
    """One command of an action: a plain argv list, never a shell string — or `fn`,
    a check in code that decides by itself (the world's on-demand stop)."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    capture: str = ""  # stdout's last line lands in this name for later {name} slots
    fn: Callable[..., tuple[bool, str]] | None = None


def server_of(slot: str, *, servers: Path | None = None, env_file: Path | None = None) -> str:
    """Where slot N's body plays: Last Order's recorded move, else MC_SERVER_N of the
    root .env, else the home world — the same order docker/node/up.sh reads them."""
    key = f"MC_SERVER_{slot}"
    try:
        for line in (servers or SERVERS_FILE).read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return env_file_value(key, env_file) or HOME_SERVER


def bash_argv(script: Path, *rest: str) -> list[str]:
    """`bash <script> …` with a bash that can read the script's path.

    On Windows a bare `bash` may resolve to the WSL stub, which lives in the Linux
    namespace and cannot open `C:/…` script paths (seen live: exit 127 before the
    script's first line). Git's bash runs them; INFRA_BRIDGE_BASH overrides the pick.
    """
    exe = "bash"
    if os.name == "nt":
        for candidate in (
            os.environ.get("INFRA_BRIDGE_BASH", ""),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if candidate and Path(candidate).is_file():
                exe = candidate
                break
    return [exe, script.as_posix(), *rest]


def commands_for(action: str, args: dict[str, str], *, project: str = "") -> list[Step]:
    """The argv sequence of one action — the whole docker surface of the bridge.

    ADR-050: a slot is node-N + mind-N; the body is the mind's (it asks for body_up /
    body_restart itself); the home world `mc-server` runs on demand — raised before a
    body that plays there, stopped when no body is left."""
    if action not in INFRA_ACTIONS:
        raise InfraError(f"unknown action {action!r}")
    # posix paths throughout: `bash` and `docker` both take forward slashes, while
    # a Windows backslash path dies inside bash (seen live: exit 127, nothing ran)
    compose = ["docker", "compose", "-f", (ROOT / "docker" / "compose.yml").as_posix()]
    if project:
        compose += ["-p", project]
    wait_healthy = bash_argv(ROOT / "dev" / "wait_for.sh", str(GRACE_S), "--healthy")
    # bring-ups ride docker/node/up.sh (T-072): it lifts the slot's keys, login
    # password, camera and server from the root .env — a bare `compose up` sees none
    # of them and a fail-closed mind refuses to start without its runner key
    up = bash_argv(ROOT / "docker" / "node" / "up.sh")
    up_env = {"COMPOSE_PROJECT_NAME": project} if project else {}
    every = compose + ["--profile", "*"]  # slots, bodies and the world have profiles

    def world_up(server: str) -> list[Step]:
        if server != HOME_SERVER:
            return []  # a foreign server needs no home world
        return [
            Step(up + ["--no-deps", "mc-server"], env=up_env),
            Step(compose + ["ps", "-q", "mc-server"], capture="wcid"),
            Step(wait_healthy + ["{wcid}"]),
        ]

    def world_gc() -> Step:
        return Step(["(stop the home world when no body is left)"], fn=_world_gc(every))

    if action == "mc_restart":
        return [Step(compose + ["restart", "mc-server"])]
    if action == "status":
        return [Step(every + ["ps", "-a", "--format", STATUS_FORMAT])]
    if action == "logs":
        lines = args.get("lines") or str(LINES_DEFAULT)
        return [Step(every + ["logs", "--tail", lines, args["service"]])]
    slot = args["slot"]
    bodies = [service.format(slot=slot) for service in BODY_KINDS.values()]
    if action == "slot_up":
        # the mind first (healthy), then the sister; the mind raises its own body
        return [
            Step(up + ["--no-deps", f"mind-{slot}"], env=up_env),
            Step(compose + ["ps", "-q", f"mind-{slot}"], capture="cid"),
            Step(wait_healthy + ["{cid}"]),
            Step(up + ["--no-deps", f"node-{slot}"], env=up_env),
        ]
    if action == "slot_down":
        # the sister first, then the mind (it closes the bridge gracefully), then the body
        return [
            Step(every + ["stop", f"node-{slot}", f"mind-{slot}", *bodies]),
            world_gc(),
        ]
    if action == "slot_restart":
        # the mind alone: the sister's session and the body live on, her runner
        # re-arms the life clock (T-072) and the mind reconnects to its body
        return [Step(up + ["--force-recreate", "--no-deps", f"mind-{slot}"], env=up_env)]
    if action == "body_up":
        service = body_service(slot, args.get("kind") or DEFAULT_BODY_KIND)
        return [*world_up(server_of(slot)), Step(up + ["--no-deps", service], env=up_env)]
    if action == "body_down":
        return [Step(every + ["stop", *bodies]), world_gc()]
    if action == "body_restart":
        # the T-053 recreate: a fresh body connects at once, a restarted worn one may not
        return [
            *world_up(server_of(slot)),
            Step(up + ["--force-recreate", "--no-deps", body_service(slot)], env=up_env),
        ]
    if action == "slot_server":
        # The sister and her mind live through the move: only the body is recreated
        # on the new address (the mind's bridge reconnects by itself). The address is
        # recorded in SERVERS_FILE after success (`process`), so later restarts keep
        # it; the login password a foreign server asks for stays the operator's
        # MC_LOGIN_PASSWORD_N in .env (a secret, ADR-041).
        host = args["server"]
        return [
            *world_up(host),
            Step(
                up + ["--force-recreate", "--no-deps", body_service(slot)],
                env={**up_env, f"MC_SERVER_{slot}": host},
            ),
            world_gc(),
        ]
    raise InfraError(f"unknown action {action!r}")


STATUS_FORMAT = "table {{.Service}}\t{{.State}}\t{{.Status}}"
CAPTURE_SLOT = re.compile(r"\{(\w+)\}")  # a {cid} slot; a {{.Go}} template never matches


def _world_gc(every: list[str]) -> Callable[..., tuple[bool, str]]:
    """The home world stops when no body of any slot is running (ADR-050)."""

    def check(runner: Callable[..., subprocess.CompletedProcess]) -> tuple[bool, str]:
        proc = runner(
            every + ["ps", "--status", "running", "--services"],
            cwd=str(ROOT), env=dict(os.environ), capture_output=True, text=True,
            timeout=STEP_TIMEOUT_S,
        )
        if proc.returncode != 0:
            return False, ((proc.stdout or "") + (proc.stderr or "")).strip()
        running = (proc.stdout or "").split()
        if "mc-server" not in running:
            return True, "the home world is already down"
        if any(name.startswith("body") for name in running):
            return True, "the home world stays: a body still plays"
        stop = runner(
            every + ["stop", "mc-server"],
            cwd=str(ROOT), env=dict(os.environ), capture_output=True, text=True,
            timeout=STEP_TIMEOUT_S,
        )
        out = ((stop.stdout or "") + (stop.stderr or "")).strip()
        note = "no body left: the home world stopped"
        return stop.returncode == 0, note + ("\n" + out if out else "")

    return check


def run_steps(
    steps: list[Step],
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout_s: int = STEP_TIMEOUT_S,
) -> tuple[bool, str]:
    """Run the sequence, return (ok, transcript). The transcript keeps the tail."""
    captures: dict[str, str] = {}
    lines: list[str] = []
    for step in steps:
        if step.fn is not None:
            ok, text = step.fn(runner)
            lines.append(text)
            if not ok:
                return False, "\n".join(lines)
            continue
        # only {name} slots of earlier captures: a docker Go template ({{.State}})
        # passes through untouched (str.format collapsed it to {.State}, seen live)
        argv = [
            CAPTURE_SLOT.sub(lambda m: captures.get(m.group(1), m.group(0)), part)
            for part in step.argv
        ]
        env = {**os.environ, **step.env}
        lines.append("$ " + " ".join(argv))
        try:
            proc = runner(
                argv, cwd=str(ROOT), env=env, capture_output=True, text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            lines.append(f"(no exit: over {timeout_s}s timeout)")
            return False, "\n".join(lines)
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if out:
            lines.append(out)
        if proc.returncode != 0:
            lines.append(f"(exit {proc.returncode})")
            return False, "\n".join(lines)
        if step.capture:
            stdout = (proc.stdout or "").strip()
            captures[step.capture] = stdout.splitlines()[-1] if stdout else ""
    return True, "\n".join(lines)


class Hive:
    """The board's HTTP face under the executor key — no proxy for the loopback."""

    def __init__(self, base: str, key: str, *, timeout_s: int = 30):
        self.base = base.rstrip("/")
        self.key = key
        self.timeout_s = timeout_s
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={
                "X-Infra-Key": self.key,
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                payload = response.read().decode("utf-8", "replace")
                return response.status, json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:  # a refusal is data, not a crash
            payload = exc.read().decode("utf-8", "replace")
            try:
                return exc.code, json.loads(payload) if payload else {}
            except ValueError:
                return exc.code, {"error": {"message": payload[:200]}}


def echo(note: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {note}", flush=True)


def process(
    hive: Hive,
    request: dict,
    *,
    project: str,
    dry: bool,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> None:
    """Take one request, run it, report. A dry run prints instead of touching."""
    entry_id = request["id"]
    payload = request.get("request") or {}
    action, args = payload.get("action", ""), payload.get("args") or {}
    try:
        cleaned = check_infra(action, args)
        steps = commands_for(action, cleaned, project=project)
    except InfraError as exc:
        note = f"refused by the closed set: {exc}"
        echo(f"#{entry_id} {action}: {note}")
        if not dry:
            hive.call("POST", f"/board/infra/{entry_id}/result", {"ok": False, "text": note})
        return
    if dry:
        echo(f"#{entry_id} {action} {cleaned}: dry run, nothing executed")
        for step in steps:
            echo("  $ " + " ".join(step.argv))
        return
    status, body = hive.call("POST", f"/board/infra/{entry_id}/take", {})
    if status == 409:
        echo(f"#{entry_id}: taken elsewhere, skipping")
        return
    if status != 200:
        echo(f"#{entry_id}: take refused — {status} {body.get('error', {}).get('kind', '')}")
        return
    echo(f"#{entry_id} {action} {cleaned}: running")
    ok, transcript = run_steps(steps, runner=runner)
    if ok and action == "slot_server":
        record_server(cleaned["slot"], cleaned["server"])
    text = transcript[-RESULT_MAX:]
    status, body = hive.call(
        "POST", f"/board/infra/{entry_id}/result", {"ok": ok, "text": text}
    )
    echo(f"#{entry_id}: {'done' if ok else 'failed'}"
         + ("" if status == 200 else f" (report {status}: {body.get('error', {}).get('kind', '')})"))


def record_server(slot: str, host: str, path: Path | None = None) -> None:
    """Keep Last Order's move for later restarts (docker/node/up.sh reads the file):
    one MC_SERVER_N line per moved slot; the home server drops the line."""
    path = path or SERVERS_FILE
    key = f"MC_SERVER_{slot}"
    lines = []
    if path.exists():
        lines = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith(f"{key}=")
        ]
    if host != HOME_SERVER:
        lines.append(f"{key}={host}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if lines:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    elif path.exists():
        path.unlink()


def env_file_value(name: str, path: Path | None = None) -> str:
    """One value from the root .env, quotes stripped — the operator runs the bridge
    without exporting the key by hand (the same lift docker/node/up.sh does)."""
    path = path or ROOT / ".env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def beat(path: Path | None = None) -> None:
    """Touch the heartbeat: the infra container is healthy while it stays fresh."""
    path = path or HEARTBEAT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        pass  # a read-only checkout on the host: the heartbeat is the container's


def close_leftovers(hive: Hive) -> None:
    """Entries a crashed run took but never reported: close them, run nothing twice."""
    status, body = hive.call("GET", "/board/infra/taken")
    if status != 200:
        echo(f"cannot list taken requests — {status}")
        return
    for request in body.get("requests", []):
        entry_id = request["id"]
        hive.call(
            "POST", f"/board/infra/{entry_id}/result",
            {"ok": False, "text": "bridge restarted before the result"},
        )
        echo(f"#{entry_id}: closed as failed (bridge restarted mid-run)")


def main_with(
    hive: Hive,
    *,
    interval: float = 15.0,
    dry_run: bool = False,
    once: bool = False,
    project: str = "",
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    """The poll loop against an injected board — the tests drive a fake one."""
    echo(f"infra bridge: {hive.base} · interval {interval}s" + (" · dry run" if dry_run else ""))
    if not dry_run:
        close_leftovers(hive)
    was_off: bool | None = None
    while True:
        beat()
        status, switch = hive.call("GET", "/board/infra/switch")
        if status != 200:
            echo(f"cannot read the switch — {status} {switch.get('error', {}).get('kind', '')}")
            return 1
        if not switch.get("on"):
            if was_off is not True:
                echo(f"executor off: {switch.get('reason') or 'no reason given'} — requests wait")
            was_off = True
        else:
            was_off = False
            status, body = hive.call("GET", "/board/infra/pending")
            if status != 200:
                echo(f"cannot read the queue — {status}")
                return 1
            for request in body.get("requests", []):
                # the operator may switch off mid-batch: re-read before each take
                if not dry_run and not hive.call("GET", "/board/infra/switch")[1].get("on"):
                    break
                process(hive, request, project=project, dry=dry_run, runner=runner)
        if dry_run or once:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The Last Order infra bridge executor")
    parser.add_argument("--url", default=os.environ.get("HIVE_URL", "http://127.0.0.1:18080"))
    parser.add_argument(
        "--key", default=os.environ.get("HIVE_INFRA_KEY", "") or env_file_value("HIVE_INFRA_KEY")
    )
    parser.add_argument("--interval", type=float, default=15.0, help="poll seconds")
    parser.add_argument("--dry-run", action="store_true", help="print commands, touch nothing")
    parser.add_argument("--once", action="store_true", help="one poll cycle and exit")
    parser.add_argument("--project", "-p", default="", help="a parallel stand's compose project")
    args = parser.parse_args(argv)
    if not args.key:
        print("infra_bridge: --key or HIVE_INFRA_KEY is required", file=sys.stderr)
        return 2
    return main_with(
        Hive(args.url, args.key),
        interval=args.interval, dry_run=args.dry_run, once=args.once, project=args.project,
    )


if __name__ == "__main__":
    raise SystemExit(main())
