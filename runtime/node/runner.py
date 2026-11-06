"""NodeRunner: one slot's birth → life → will → retire → archive → respawn loop.

The strict order (NETWORK.md sections 3 and 6, plan reviews P1-1/P1-4):

- birth: `hive.birth` (traits rolled here) → arm the life clock at mind-N
  (`POST /runner/birth`, BEFORE any session: a sister with bash must not arm another
  serial first) → the workplace from the T-029 template → the long session;
- life: poll `GET /runner/status`; a dead harness process restarts on the SAME
  workplace with a growing pause (a stable stretch resets it), quota marks switch the
  adapter or make the node wait (section 9); the board bridge (T-050) runs alongside
  the session and stops with it;
- death: stop the life session → `mark_dead` (bounded retries — the emergency will
  covers a hive that stayed down) → the will session behind `WILL_TIMEOUT_S`, its hive
  MCP pointed at the runner's proxy → the staged `submit_will` text or the emergency
  will (`will.py`) → `retire` (a 409 `already_retired` is a hive-side close: the will
  stays staged; any other 4xx is parked for the operator; unavailability retries with
  a capped pause — nothing overtakes an unsettled will) → the read-only archive →
  `respawn` (a "not dead" refusal counts as already respawned) → the next birth.

The runner is service-grade: any exception logs and restarts the generation loop; it
never sets the agent's rhythm and never decides for her (ADR-035).
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from runtime.hive.client import BoardFeedClient, HiveRejected, HiveUnavailable
from runtime.knowledge.schema import CAUSES, SchemaError, Will, emergency_will
from runtime.lastorder.naming import NamingPreset, node_name
from runtime.lastorder.persona import Node, PersonaProvider
from runtime.lastorder.traits import Traits, roll
from runtime.lastorder.will import (
    HEIR_ADDRESSES,
    HEIR_NEUTRAL,
    WillRejected,
    assemble_will,
    check_will_body,
)
from runtime.node import workplace
from runtime.node.boardlink import BoardLink, MindEventPoster
from runtime.node.adapters import Command, quota_exhausted
from runtime.node.remotes import BaseApi, HiveApi, MindApi
from runtime.node.willproxy import WillProxy

LIFE_PREFIX = "Life session."
WILL_PREFIX = "Will session (you have just died)."


def _tail(text: str, limit: int) -> str:
    """The last `limit` characters, whitespace-squeezed: telemetry of what a harness
    said when it died — enough to debug, never a full log dump."""
    return " ".join(text[-limit:].split())

DELIVERED = "hive"
SUPERSEDED = "superseded"  # 409 already_retired: hive closed the node itself
REJECTED = "rejected"  # a permanent non-409 4xx: parked for the operator
PARKED = "parked"  # undelivered, the retry loop owns it


@dataclass(frozen=True)
class Death:
    cause: str
    text: str


@dataclass
class LifeState:
    """The runner's persisted state of the current life (`.runner/state.json`)."""

    serial: int
    born_at: str
    ttl_min: int
    traits: dict[str, Any]
    session_id: str | None = None


class SessionProcess:
    """A subprocess of the harness with a background stdout reader (no pipe deadlock)."""

    def __init__(self, popen: subprocess.Popen):
        self._popen = popen
        self._chunks: list[str] = []
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        stream = self._popen.stdout
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            self._chunks.append(line)
        stream.close()

    def poll(self) -> int | None:
        return self._popen.poll()

    def returncode(self) -> int | None:
        return self._popen.returncode

    def terminate(self) -> None:
        if self._popen.poll() is None:
            try:
                os.killpg(self._popen.pid, 15)
            except (AttributeError, PermissionError, ProcessLookupError, OSError):
                self._popen.terminate()

    def kill(self) -> None:
        if self._popen.poll() is None:
            try:
                os.killpg(self._popen.pid, 9)
            except (AttributeError, PermissionError, ProcessLookupError, OSError):
                self._popen.kill()

    def wait(self, timeout: float | None = None) -> None:
        try:
            self._popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def output(self) -> str:
        return "".join(self._chunks)


class BoardAlarms(BaseApi):
    """The runner's voice on the board (TASKBOARD.md section 7): environment
    alarms with the slot's runner key. One attempt per call — repeats fold into
    `count` on the hive, and an alarm must never stall the loop that raises it."""

    def alarm(self, alarm_type: str, text: str, *, slot: str, serial: int | None) -> None:
        payload: dict[str, Any] = {"type": alarm_type, "text": text, "slot": slot}
        if serial is not None:
            payload["serial"] = serial
        self._request("post", "/board/alarms", payload)


class NodeRunner:
    """All wiring, no policy: every state is hive's, the mind's or the workplace's."""

    def __init__(
        self,
        cfg,
        *,
        mind: MindApi,
        hive: HiveApi,
        adapters: Mapping[str, Any],
        naming: NamingPreset,
        quirks: list[str],
        persona: PersonaProvider | None = None,
        spawn: Callable[[tuple[str, ...], Path, dict[str, str]], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] | None = None,
        log: Callable[[str, dict[str, Any]], None] | None = None,
        seed_factory: Callable[[], int] | None = None,
        will_proxy_factory: Callable[[str, str, str], Any] | None = None,
        boardlink_factory: Callable[["LifeState"], Any] | None = None,
        sweep: Callable[[], None] | None = None,
        alarms: BoardAlarms | None = None,
        monotonic: Callable[[], float] | None = None,
    ):
        self.cfg = cfg
        self._mind = mind
        self._hive = hive
        self._adapters = dict(adapters)
        self._naming = naming
        self._quirks = quirks
        self._persona = persona or PersonaProvider(naming)
        self._spawn = spawn or self._spawn_child
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._log = log or (lambda type, payload=None: None)
        self._seed_factory = seed_factory or (lambda: secrets.randbelow(2**31))
        self._will_proxy_factory = will_proxy_factory or (
            lambda target, node_key, runner_key: WillProxy(
                target, node_key=node_key, runner_key=runner_key
            )
        )
        self._boardlink_factory = boardlink_factory or self._make_boardlink
        self._sweep = sweep or self._kill_sister_uid_processes
        self._alarms = alarms
        self._monotonic = monotonic or time.monotonic

    def _alarm(self, alarm_type: str, text: str, state: "LifeState | None" = None) -> None:
        """Raise a board alarm best-effort: a board that is down must not take
        the runner down with it — the log keeps the attempt either way."""
        if self._alarms is None:
            return
        try:
            self._alarms.alarm(
                alarm_type,
                text,
                slot=self.cfg.slot,
                serial=state.serial if state is not None else None,
            )
        except Exception as exc:
            self._log("alarm.post_failed", {"type": alarm_type, "error": f"{type(exc).__name__}: {exc}"})
            return
        self._log("alarm.posted", {"type": alarm_type})

    # --- plumbing -----------------------------------------------------------------------

    def _state_path(self) -> Path:
        return self.cfg.workplaces / workplace.RUNNER_DIR / "state.json"

    def _load_state(self) -> LifeState | None:
        try:
            doc = json.loads(self._state_path().read_text(encoding="utf-8"))
            return LifeState(
                serial=int(doc["serial"]),
                born_at=str(doc.get("born_at") or ""),
                ttl_min=int(doc.get("ttl_min") or 0),
                traits=doc.get("traits") or {},
                session_id=doc.get("session_id"),
            )
        except (OSError, ValueError, KeyError):
            return None

    def _save_state(self, state: LifeState) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(state), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _clear_state(self) -> None:
        try:
            self._state_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _spawn_child(self, argv: tuple[str, ...], cwd: Path, env: dict[str, str]) -> SessionProcess:
        preexec = None
        if self.cfg.sister_uid is not None and hasattr(os, "setuid"):
            uid, gid = self.cfg.sister_uid, self.cfg.sister_gid or self.cfg.sister_uid

            def _drop() -> None:  # the harness and its bash run as the sister (P1-1)
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

    def _child_env(self, extra: Mapping[str, str]) -> dict[str, str]:
        home = "/home/sister" if Path("/home/sister").exists() else str(Path.home())
        env = {
            "HOME": home,
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "NO_COLOR": "1",
            "ZCODE_DISABLE_UPDATE_CHECK": "1",
            "NO_UPDATE_NOTIFIER": "1",
        }
        env.update(extra)
        return env

    def _capped_pause(self, pause: float) -> float:
        return min(pause * 2, self.cfg.restart_pause_max_s)

    def _hive_pause(self, attempt: int) -> float:
        return min(self.cfg.hive_retry_pause_s * (2**attempt), self.cfg.hive_retry_max_s)

    def _kill_sister_uid_processes(self) -> None:
        """Kill EVERY process of the sister's uid (review R2-4): killpg only reaches the
        session group, so a child that detached (setsid/nohup/disown) would survive her
        death, the will session and the respawn — and write into the heir's memory under
        the same uid. The sweep runs as a setuid(sister) child doing os.kill(-1, SIGKILL):
        from root, kill(-1) would take the whole container down, from the sister's uid it
        reaches only her own processes (the sweeper kills itself too — expected). Called
        only after the life session is stopped for a death and after the will session,
        never between an agent's own turns: a harness may keep a server between runs."""
        uid = self.cfg.sister_uid
        if uid is None or not hasattr(os, "setuid"):
            return  # no privilege drop on this host: the process group was the boundary
        argv = (
            sys.executable, "-c",
            "import os, sys; os.setgroups([]); os.setgid(int(sys.argv[2]));"
            " os.setuid(int(sys.argv[1])); os.kill(-1, 9)",
            str(uid), str(self.cfg.sister_gid or uid),
        )
        try:
            subprocess.run(argv, check=False, timeout=30)
        except OSError as exc:
            self._log("runner.sweep_failed", {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._log("runner.sister_swept", {"uid": uid})

    # --- the loop -----------------------------------------------------------------------

    def run(self) -> None:
        while True:
            try:
                self._generation()
            except Exception as exc:  # service-grade: log and restart the generation
                self._log("runner.error", {"error": f"{type(exc).__name__}: {exc}"})
                self._sleep(self.cfg.restart_pause_s)

    def _generation(self) -> None:
        status = self._wait_for_mind()
        state = self._load_state()
        if status.get("dead"):
            serial = int(status.get("serial") or (state.serial if state else 0) or 0)
            if serial <= 0:
                self._log("death.unknown_serial", {"status": status})
                self._sleep(self.cfg.restart_pause_max_s)
                return
            if state is None or state.serial != serial:
                born = status.get("dead_since") or self._now().isoformat()
                state = LifeState(serial=serial, born_at=born, ttl_min=0, traits={})
                self._log("death.state_reconstructed", {"serial": serial})
            self._death_path(
                state,
                str(status.get("cause") or "game"),
                str(status.get("death_text") or ""),
                self.cfg.harness,
            )
            return
        armed = status.get("serial")
        if not armed:
            if self._keys_missing():
                # fail-closed (review R2-6): without both keys the node would run open —
                # an empty runner key silently passes no gate, an empty node key ships
                # harness configs with a blank identity. Wait for the operator instead;
                # NODE_DEV_OPEN=1 is the explicit dev exception.
                self._log("runner.no_keys", {"slot": self.cfg.slot})
                self._sleep(self.cfg.restart_pause_max_s)
                return
            state = self._birth()
        else:
            armed = int(armed)
            root = self.cfg.workplaces / "agent" / workplace.serial_dir(armed)
            if not root.exists():
                # the clock is armed but the workplace is gone (a wiped volume): never
                # fake a persona — wait the armed life out; the emergency will speaks
                self._log("runner.state_lost", {"serial": armed})
                self._sleep(self.cfg.restart_pause_max_s)
                return
            if state is None or state.serial != armed:
                state = LifeState(serial=armed, born_at=self._now().isoformat(), ttl_min=0, traits={})
                self._save_state(state)
                self._log("runner.state_reconstructed", {"serial": armed})
            else:
                self._log("life.reattached", {"serial": armed})
        death, adapter_name = self._life(state)
        self._death_path(state, death.cause, death.text, adapter_name)

    def _wait_for_mind(self) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return self._mind.status()
            except HiveUnavailable as exc:
                self._log("mind.unreachable", {"attempt": attempt + 1, "error": str(exc)})
                self._sleep(self._hive_pause(attempt))
                attempt += 1

    # --- birth --------------------------------------------------------------------------

    def _keys_missing(self) -> bool:
        return not self.cfg.runner_key and not self.cfg.node_key and not self.cfg.dev_open

    def _birth(self) -> LifeState:
        seed = self._seed_factory()
        traits = roll(seed, self._quirks)
        record = self._retry_forever(
            "birth", lambda: self._hive.birth(self.cfg.slot, traits)
        )
        serial, born_at, ttl_min = int(record["serial"]), str(record["born_at"]), int(record["ttl_min"])
        # Arm BEFORE any session starts (T-025 note): the clock belongs to this serial.
        # A lost reply may still have armed it: re-read the status before retrying, the
        # same idempotence the retire retry owes (section 9).
        attempt = 0
        while True:
            try:
                self._mind.arm(serial, born_at, ttl_min)
                break
            except HiveRejected:
                status = self._wait_for_mind()
                if status.get("serial") != serial:
                    raise
                self._log("birth.already_armed", {"serial": serial})
                break
            except HiveUnavailable as exc:
                status = self._quiet_status()
                if status is not None and status.get("serial") == serial:
                    self._log("birth.already_armed", {"serial": serial, "via": "lost_reply"})
                    break
                self._log("birth.arm_retry", {"serial": serial, "attempt": attempt + 1, "error": str(exc)})
                self._sleep(self._hive_pause(attempt))
                attempt += 1
        self._log("life.birth", {"serial": serial, "slot": self.cfg.slot, "seed": seed})
        workplace.create_workplace(
            workplaces=self.cfg.workplaces,
            template_dir=self.cfg.template_dir,
            shared_dir=self.cfg.shared_dir,
            serial=serial,
            traits=traits,
            persona=self._persona,
            node=Node(serial, traits),
            body_base=self.cfg.body_base,
            hive_url=self.cfg.hive_url,
            node_key=self.cfg.node_key,
            gateway_url=self.cfg.gateway_url,
            opencode_model=self.cfg.opencode_model,
            sister_uid=self.cfg.sister_uid,
            sister_gid=self.cfg.sister_gid,
        )
        if not workplace.link_current(self.cfg.workplaces, serial):
            self._log("current.link_failed", {"serial": serial})
        state = LifeState(
            serial=serial, born_at=born_at, ttl_min=ttl_min, traits=traits.as_dict()
        )
        self._save_state(state)
        return state

    def _retry_forever(self, what: str, fn: Callable[[], Any]) -> Any:
        attempt = 0
        while True:
            try:
                return fn()
            except HiveUnavailable as exc:
                self._log(f"{what}.retry", {"attempt": attempt + 1, "error": str(exc)})
                self._sleep(self._hive_pause(attempt))
                attempt += 1

    # --- life ---------------------------------------------------------------------------

    def _make_boardlink(self, state: LifeState):
        """The temporary board bridge of this life (T-050). None — and the bridge
        stays off — when the slot has no node key: without it the feed answers 401
        for as long as the operator does not fix the keys. Also off for the pi
        harness: the misaka-link extension of the session (T-048) delivers the
        feed itself, and the two would ack one tape — hive keeps one cursor per
        serial (the T-048 review note, 2026-11-05)."""
        if self.cfg.harness == "pi":
            self._log("boardlink.off", {"serial": state.serial, "why": "pi (misaka-link)"})
            return None
        if not self.cfg.node_key:
            self._log("boardlink.off", {"serial": state.serial, "why": "no node key"})
            return None
        return BoardLink(
            BoardFeedClient(self.cfg.hive_url, node_key=self.cfg.node_key),
            MindEventPoster(self.cfg.body_base, runner_key=self.cfg.runner_key),
            serial=state.serial,
            slot=self.cfg.slot,
            naming=self._naming,
            log=self._log,
        )

    def _life(self, state: LifeState) -> tuple[Death, str]:
        """The long session of one life; returns the death and the adapter it ended on."""
        adapter_name = self.cfg.harness
        adapter = self._adapters[adapter_name]
        root = self.cfg.workplaces / "agent" / workplace.serial_dir(state.serial)
        prompt = self._session_prompt(LIFE_PREFIX)
        pause = self.cfg.restart_pause_s
        boardlink = self._boardlink_factory(state)
        if boardlink is not None:
            boardlink.start()  # the bridge lives exactly as long as the life session
        try:
            quick_exits = 0  # consecutive instant deaths of a RESUMED long session
            while True:
                death = self._death_status()
                if death is not None:
                    return death, adapter_name
                if not self._adapter_ready(adapter_name):
                    self._log("harness.waits", {"harness": adapter_name, "why": "no key"})
                    self._sleep(self.cfg.restart_pause_max_s)
                    continue
                command = adapter.life_turn(prompt, state.session_id)
                started = time.monotonic()
                spawned_at = time.time()
                process = self._safe_spawn(command, root)
                if process is None:
                    self._sleep(pause)
                    pause = self._capped_pause(pause)
                    continue
                resumed = state.session_id is not None
                if getattr(adapter, "long_running", False) and not resumed:
                    # the console wrapper: the opener can only enter through the
                    # hub, so wait for the wrapper's boot report first
                    boot = self._await_boot(adapter, process, spawned_at)
                    if boot is None and process.poll() is None:
                        # alive but mute past the boot deadline: no session, no
                        # opener — kill and let the restart loop try again
                        self._log("hapi.boot_timeout", {"slot": self.cfg.slot})
                        process.terminate()
                        process.kill()
                        self._sleep(pause)
                        pause = self._capped_pause(pause)
                        continue
                    if boot is not None:
                        state.session_id = boot.pi_session_id
                        self._save_state(state)
                        if not self._deliver_opener(adapter, boot.hub_session_id, prompt, state):
                            process.terminate()
                            process.kill()
                            self._sleep(pause)
                            pause = self._capped_pause(pause)
                            continue
                    # a boot of None with the process already dead is a plain
                    # crash: the end-of-session path below owns it (session_missing)
                death = self._wait_session(state, process, adapter)
                if death is not None:
                    return death, adapter_name
                output_enrich = getattr(adapter, "output_of", None)
                output = (
                    output_enrich(process.output(), spawned_at)
                    if output_enrich is not None
                    else process.output()
                )
                session_id = adapter.session_id(output)
                if session_id:
                    state.session_id = session_id
                    self._save_state(state)
                ran_for = time.monotonic() - started
                self._log_session_end(adapter, adapter_name, state, process, output, ran_for)
                if getattr(adapter, "long_running", False):
                    # rpc mode never ends its turn: an exit with the body alive is
                    # the session vanishing, not a completed turn (section 7)
                    self._alarm("session_missing", "the hapi pi session died while the body lives", state)
                    if resumed and ran_for < self.cfg.restart_pause_s:
                        quick_exits += 1
                        if quick_exits >= 2:
                            # the hub no longer holds that session (verified: T-043,
                            # resume_lost) — a resume key that cannot resume would
                            # crash-loop until the TTL kills the body
                            self._log("hapi.resume_lost", {"session": state.session_id})
                            state.session_id = None
                            self._save_state(state)
                            quick_exits = 0
                    else:
                        quick_exits = 0
                if quota_exhausted(output):
                    self._alarm("quota", "the provider says the quota ran out", state)
                    switched = self._quota_switch(adapter_name, state)
                    if switched is not None:
                        adapter_name, adapter = switched
                        pause = self.cfg.restart_pause_s
                        continue
                    self._log("quota.waits", {"harness": adapter_name})
                    self._sleep(pause)
                    pause = self._capped_pause(pause)
                    continue
                self._sleep(pause)
                pause = (
                    self.cfg.restart_pause_s
                    if ran_for >= self.cfg.restart_reset_after_s
                    else self._capped_pause(pause)
                )
        finally:
            if boardlink is not None:
                boardlink.stop()  # before the death path: no bridge in the will session

    def _log_session_end(self, adapter, adapter_name: str, state: LifeState, process, output: str, ran_for: float) -> None:
        """session.ended plus, in hapi mode, the life's spend off the Pi session
        file — the report's quota and money numbers (telemetry: the file is the
        sister's own, so it can only misstate her own spend)."""
        payload: dict[str, Any] = {
            "harness": adapter_name,
            "code": process.returncode(),
            "ran_s": round(ran_for, 1),
            "tail": _tail(output, 300),
        }
        spend = getattr(adapter, "session_spend", None)
        if spend is not None and state.session_id:
            try:
                usage = spend(state.session_id)
            except OSError:
                usage = None
            if usage is not None:
                payload["session"] = state.session_id
                payload["tokens"] = usage["tokens"]
                payload["cost_usd"] = round(usage["cost_usd"], 4)
        self._log("session.ended", payload)

    def _await_boot(self, adapter, process, spawned_at: float):
        """Wait for the wrapper's boot report (both ids — the hub's session for
        the opener and pi's for the resume), None on timeout."""
        deadline = self._monotonic() + adapter._boot_timeout_s
        while process.poll() is None and self._monotonic() < deadline:
            boot = adapter.boot_report(spawned_at)
            if boot is not None:
                return boot
            self._sleep(0.5)
        return adapter.boot_report(spawned_at)

    def _deliver_opener(self, adapter, session_id: str, prompt: str, state: LifeState) -> bool:
        """Post the life opener into the hub session (the operator's channel,
        T-044): a few bounded retries, then the restart loop owns it. The prompt
        rides the body of a hub message — never argv (stdin is HAPI's)."""
        for attempt in range(3):
            try:
                adapter.deliver_prompt(session_id, prompt)
            except Exception as exc:
                self._log(
                    "hapi.prompt_retry",
                    {"session": session_id, "attempt": attempt + 1, "error": f"{type(exc).__name__}: {exc}"},
                )
                self._sleep(self._hive_pause(attempt))
                continue
            self._log("hapi.prompt_delivered", {"session": session_id, "serial": state.serial})
            return True
        self._log("hapi.prompt_failed", {"session": session_id, "slot": self.cfg.slot})
        return False

    def _quota_switch(self, adapter_name: str, state: LifeState | None = None) -> tuple[str, Any] | None:
        if self.cfg.quota_action != "switch" or not self.cfg.failover:
            return None
        if adapter_name == self.cfg.failover or not self._adapter_ready(self.cfg.failover):
            return None
        self._log("quota.switch", {"from": adapter_name, "to": self.cfg.failover})
        self._alarm(
            "harness_failover",
            f"Z.ai died on quota: the node moves from {adapter_name} to {self.cfg.failover}",
            state,
        )
        return self.cfg.failover, self._adapters[self.cfg.failover]

    def _adapter_ready(self, name: str) -> bool:
        if name in ("zcode", "pi"):
            return bool(self.cfg.zai_key)
        if name == "opencode":
            return bool(self.cfg.gateway_url and self.cfg.gateway_key)
        return True

    def _safe_spawn(self, command: Command, root: Path):
        try:
            return self._spawn(command.argv, root, self._child_env(command.env))
        except OSError as exc:  # a missing binary is a crash like any other
            self._log("session.spawn_failed", {"error": f"{type(exc).__name__}: {exc}"})
            return None

    def _wait_session(self, state: LifeState, process, adapter=None) -> Death | None:
        """Watch the mind's death status while the harness process runs. For a
        long-running session also the `stuck` watch (section 7): every model turn
        appends records to the Pi session file, so a size that does not move for
        `stuck_s` means the agent has gone quiet — an alarm, not a kill: the
        operator sees it and the TTL still bounds the life."""
        next_poll = 0.0
        progress = getattr(adapter, "session_progress", None) if adapter is not None else None
        watching = progress is not None and state.session_id is not None
        last_size = progress(state.session_id) if watching else 0
        last_move = self._monotonic()
        stuck_raised = False
        while True:
            code = process.poll()
            if code is not None:
                process.wait()
                return None
            now = self._monotonic()
            if watching and now - last_move >= self.cfg.stuck_s:
                size = progress(state.session_id)
                if size is not None and size != last_size:
                    last_size = size
                    last_move = now
                    stuck_raised = False
                elif not stuck_raised:
                    self._alarm("stuck", "no agent turns for a while: the session went quiet", state)
                    stuck_raised = True
                    last_move = now  # one alarm per quiet stretch, not per poll
            if now >= next_poll:
                death = self._death_status()
                if death is not None:
                    process.terminate()
                    if process.poll() is None:
                        process.wait(self.cfg.kill_grace_s)
                    process.kill()
                    self._log("session.stopped_for_death", {"serial": state.serial})
                    self._sweep()  # detached sisters die with their node (R2-4)
                    return death
                next_poll = now + self.cfg.poll_s
            time.sleep(min(0.2, self.cfg.poll_s))

    def _quiet_status(self) -> dict[str, Any] | None:
        try:
            return self._mind.status()
        except HiveUnavailable:
            return None

    def _death_status(self) -> Death | None:
        try:
            status = self._mind.status()
        except HiveUnavailable:
            return None  # the mind restarts; the session keeps running meanwhile
        if not status.get("dead"):
            return None
        return Death(str(status.get("cause") or "game"), str(status.get("death_text") or ""))

    def _session_prompt(self, prefix: str) -> str:
        template = (self.cfg.template_dir / "SESSION_PROMPT.md").read_text(encoding="utf-8")
        return f"{prefix}\n\n{template}"

    # --- death --------------------------------------------------------------------------

    def _death_path(self, state: LifeState, cause: str, text: str, adapter_name: str) -> None:
        serial = state.serial
        cause = cause if cause in CAUSES else "game"
        died_at = self._now().isoformat()
        self._mark_dead_bounded(serial)
        # the will session: the agent reads her memory and calls submit_will through the
        # proxy; a timeout (or a hung session) leaves the stage to the emergency will
        if self._adapter_ready(adapter_name):
            self.run_will_session(state, adapter_name)
        else:
            self._log("will.session_skipped", {"serial": serial, "harness": adapter_name})
        will_text, will_origin = self._will_of(state, cause, text, died_at, self._harness_model(adapter_name))
        self._log(
            "will.written" if will_origin == "agent" else "will.emergency",
            {"serial": serial, "origin": will_origin},
        )
        if will_origin != "agent":
            # section 7: the runner, not the agent, wrote the will — the operator
            # wants to know a node died without speaking for herself
            self._alarm("emergency_will", "the will is the runner's emergency template, not the agent's", state)
        self._retire(state, cause, will_text)
        root = self.cfg.workplaces / "agent" / workplace.serial_dir(serial)
        if root.exists():
            archive = workplace.archive_workplace(self.cfg.workplaces, serial)
            self._log("life.archived", {"serial": serial, "path": str(archive)})
        self._clear_state()
        self._respawn(serial)

    def _mark_dead_bounded(self, serial: int) -> None:
        for attempt in range(self.cfg.hive_retry_rounds):
            try:
                self._hive.mark_dead(serial)
                return
            except HiveRejected as exc:
                # 404/409: the node is already closed or unknown to hive — nothing to mark
                self._log("mark_dead.rejected", {"serial": serial, "error": str(exc)})
                return
            except HiveUnavailable as exc:
                self._log("mark_dead.retry", {"serial": serial, "attempt": attempt + 1, "error": str(exc)})
                self._sleep(self._hive_pause(attempt))
        # the will session still runs: submit_will would fail with hive down, and the
        # emergency will below covers exactly that (section 9)

    def _will_of(
        self, state: LifeState, cause: str, text: str, died_at: str, model: str
    ) -> tuple[str, str]:
        """The agent's staged will body with the service fields filled by the
        environment (T-040), else the emergency template — never nothing."""
        staged = self._staged_will(state.serial)
        if staged is not None:
            try:
                will = assemble_will(
                    serial=state.serial,
                    slot=self.cfg.slot,
                    cause=cause,
                    born_at=state.born_at or died_at,
                    died_at=died_at,
                    traits=self._traits_of(state),
                    body=staged,
                    model=model,
                    emergency=False,
                )
                return will.to_markdown(), "agent"
            except SchemaError:
                pass  # broken life state: the emergency template below still speaks
        traits = self._traits_of(state)
        will = emergency_will(
            serial=state.serial,
            slot=self.cfg.slot,
            cause=cause,
            born_at=state.born_at or died_at,
            died_at=died_at,
            traits=traits,
            last_action="",
            last_events=[line for line in (text or "").splitlines() if line.strip()][:5],
            node_name=node_name(self._naming, state.serial),
            heir_address=HEIR_ADDRESSES.get(self._naming.key, HEIR_NEUTRAL),
        )
        return will.to_markdown(), "emergency"

    def _harness_model(self, adapter_name: str) -> str:
        """The adapter's model for the will frontmatter — the same cfg fields
        `__main__` wires into the adapters (the adapters keep their model private)."""
        return str(getattr(self.cfg, f"{adapter_name}_model", "") or adapter_name)

    def _traits_of(self, state: LifeState) -> Traits:
        try:
            return Traits(**state.traits)
        except TypeError:
            return Traits(5, 5, 5, 5, 5, quirk="черты утеряны вместе с состоянием", seed=0)

    def _staged_will(self, serial: int) -> str | None:
        """The staged will body, or None. Since T-040 `submit_will` stages the text
        alone; a stage left by an older run (a whole will.md) is read through the
        parser. Unusable content — unparseable, a stranger's serial, a body that
        fails the checks — leaves the stage to the emergency will."""
        if self.cfg.pending_wills is None:
            return None
        path = self.cfg.pending_wills / f"{workplace.serial_dir(serial)}.md"
        try:
            body = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if body.startswith("---"):
            try:
                will = Will.from_markdown(body)
            except SchemaError:
                return None
            if will.meta.serial != serial:
                return None
            body = will.body
        try:
            check_will_body(body)
        except WillRejected:
            return None
        return body

    def _retire(self, state: LifeState, cause: str, will_text: str) -> str:
        delivery = PARKED
        attempt = 0
        while True:
            try:
                self._hive.retire(state.serial, cause, will_text)
                self._log("will.retired", {"serial": state.serial, "cause": cause})
                return DELIVERED
            except HiveRejected as exc:
                if exc.kind == "already_retired":
                    # hive closed this node itself (lost-close): the will stays staged
                    self._log("will.superseded", {"serial": state.serial, "error": str(exc)})
                    return SUPERSEDED
                if exc.status in (401, 403):
                    # An auth refusal is a configuration error the operator fixes
                    # (review R2-8, the 00033 case): parking here would drop the will the
                    # moment the key is repaired. Retry with the capped pause and a loud
                    # log, like unavailability.
                    self._log(
                        "will.auth_retry",
                        {"serial": state.serial, "status": exc.status, "error": str(exc)},
                    )
                    self._sleep(self._hive_pause(attempt))
                    attempt += 1
                    continue
                # permanent (400/422 and friends): park a copy for the operator, keep the
                # staged file
                parked = self.cfg.workplaces / workplace.RUNNER_DIR / "parked"
                parked.mkdir(parents=True, exist_ok=True)
                (parked / f"{workplace.serial_dir(state.serial)}.md").write_text(
                    will_text, encoding="utf-8"
                )
                self._log("will.parked", {"serial": state.serial, "status": exc.status, "kind": exc.kind})
                return REJECTED
            except HiveUnavailable as exc:
                self._log(
                    "will.delivery_retry",
                    {"serial": state.serial, "attempt": attempt + 1, "error": str(exc)},
                )
                self._sleep(self._hive_pause(attempt))
                attempt += 1

    def _respawn(self, serial: int) -> None:
        attempt = 0
        while True:
            try:
                self._mind.respawn()
                self._log("body.respawned", {"serial": serial})
                return
            except HiveRejected as exc:
                if "not dead" in exc.message:
                    # somebody stood the body up first (an idempotent retry): continue
                    self._log("body.respawn_already", {"serial": serial})
                    return
                self._log("body.respawn_failed", {"serial": serial, "error": str(exc)})
            except HiveUnavailable as exc:
                self._log("body.respawn_retry", {"serial": serial, "attempt": attempt + 1, "error": str(exc)})
            self._sleep(self._hive_pause(attempt))
            attempt += 1

    # --- the will session ---------------------------------------------------------------

    def run_will_session(self, state: LifeState, adapter_name: str) -> str:
        """The will session behind a timeout; returns the harness output. Used by the
        death path through `_will_session` and directly by tests. A long-running
        (console) session never ends its turn: the timeout stays the backstop, but
        the staged will file ends it early — the agent said her piece."""
        adapter = self._adapters[adapter_name]
        root = self.cfg.workplaces / "agent" / workplace.serial_dir(state.serial)
        proxy = self._will_proxy_factory(
            f"{self.cfg.hive_url.rstrip('/')}/node/mcp", self.cfg.node_key, self.cfg.runner_key
        )
        url = proxy.start()
        workplace.set_will_mode(root, adapter_name, url)
        self._log("will.session", {"serial": state.serial, "harness": adapter_name, "proxy": url})
        prompt = self._session_prompt(WILL_PREFIX)
        command = adapter.will_session(prompt)
        process = self._safe_spawn(command, root)
        output = ""
        if process is not None:
            long_running = getattr(adapter, "long_running", False)
            spawned_at = time.time()
            if long_running:
                # the opener enters through the hub, like the life session's
                boot = self._await_boot(adapter, process, spawned_at)
                if boot is not None:
                    self._deliver_opener(adapter, boot.hub_session_id, prompt, state)
            deadline = time.monotonic() + self.cfg.will_timeout_s
            while process.poll() is None and time.monotonic() < deadline:
                if long_running and self._staged_will(state.serial) is not None:
                    self._log("will.staged", {"serial": state.serial})
                    break
                time.sleep(0.05)
            if process.poll() is None:
                process.terminate()
                if process.poll() is None:
                    process.wait(self.cfg.kill_grace_s)
                process.kill()
                if long_running and self._staged_will(state.serial) is None:
                    self._log(
                        "will.session_timeout",
                        {"serial": state.serial, "timeout_s": self.cfg.will_timeout_s, "tail": _tail(process.output(), 300)},
                    )
            process.wait()
            output = process.output()
            output_enrich = getattr(adapter, "output_of", None)
            if output_enrich is not None:
                output = output_enrich(output, spawned_at)
            self._log("will.session_done", {"serial": state.serial, "tail": _tail(output, 300)})
        self._sweep()  # the will session's strays must not meet the heir (R2-4)
        proxy.stop()
        return output
