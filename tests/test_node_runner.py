"""The node runner (T-027): birth, crash-restart, death both ways, quota policy.

Everything runs on fakes of the two remotes and the harness spawn, journal-style like
`tests/test_lifecycle.py`: the fakes record every call, the runner's order is the
assertion. The workplace part runs against the real T-029 template (read-only source)
in a tmp workplaces root; on this non-POSIX host the uid side of the boundary
(plan review P1-2) is exercised on the stand, the mode side here.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import yaml

from runtime.config import CHARACTER_DIR, REPO_ROOT
from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.knowledge.schema import Will
from runtime.lastorder.naming import preset
from runtime.lastorder.persona import PersonaProvider
from runtime.lastorder.traits import Traits, load_quirks
from runtime.node.adapters import (
    CONTINUE_MESSAGE,
    Command,
    HERMES_TOOLSETS,
    HermesAdapter,
    OpenCodeAdapter,
    PI_SESSION_DIR,
    PiAdapter,
    ZCodeAdapter,
    quota_exhausted,
)
from runtime.node.config import NodeConfig
from runtime.node.runner import LIFE_PREFIX, LifeState, NodeRunner
from runtime.node.willproxy import WillProxy
from runtime.node import workplace

BORN = "2026-10-21T12:00:00+00:00"
QUIRKS = load_quirks(CHARACTER_DIR / "quirks.txt")
NAMING = preset("lab")


def make_cfg(tmp_path: Path, **over) -> NodeConfig:
    values = dict(
        slot="LastOrder01",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        workplaces=tmp_path / "wp",
        template_dir=CHARACTER_DIR,
        shared_dir=REPO_ROOT / "agent" / "shared",
        harness="zcode",
        failover="",
        quota_action="wait",
        node_key="sk-node-one",
        runner_key="rk-runner-one",
        zai_key="zai-token",
        hapi_url="http://hapi-hub:3006",
        hapi_token="",
        gateway_url="http://llm-gateway:4000",
        gateway_key="gk-node",
        zcode_model="GLM-5.3-Flash",
        opencode_model="openrouter/qwen/qwen3.7-flash",
        hermes_model="GLM-5.3-Flash",
        pi_model="GLM-5.3-Flash",
        pi_context_window=96_000,
        pi_keep_recent_tokens=20_000,
        naming="lab",
        will_timeout_s=0.2,
        poll_s=0.05,
        restart_pause_s=5.0,
        restart_pause_max_s=60.0,
        restart_reset_after_s=300.0,
        stuck_s=600.0,
        kill_grace_s=0.1,
        hive_retry_pause_s=0.01,
        hive_retry_max_s=0.05,
        hive_retry_rounds=3,
        pending_wills=tmp_path / "pending_wills",
        sister_uid=None,
        sister_gid=None,
    )
    values.update(over)
    return NodeConfig(**values)


class FakeMind:
    """mind-N's /runner surface; the death is scripted by the test. `kill_at_status`
    turns the body dead on the Nth status call, so a test can place the death in the
    middle of a hanging session instead of before the first one."""

    def __init__(self):
        self.armed: tuple[int, str, int] | None = None
        self.dead = False
        self.cause: str | None = None
        self.text: str | None = None
        self.arm_calls: list[tuple[int, str, int]] = []
        self.respawn_calls = 0
        self.respawn_error: Exception | None = None
        self.kill_at_status = 0
        self.status_calls = 0

    def status(self) -> dict:
        self.status_calls += 1
        if self.kill_at_status and self.status_calls >= self.kill_at_status:
            self.dead = True
            self.cause = self.cause or "game"
            self.text = self.text or "LastOrder01 was slain"
        return {
            "alive": not self.dead,
            "dead": self.dead,
            "dead_since": None,
            "cause": self.cause,
            "death_text": self.text,
            "serial": self.armed[0] if self.armed else None,
        }

    def arm(self, serial: int, born_at: str, ttl_min: int) -> dict:
        self.arm_calls.append((serial, born_at, ttl_min))
        self.armed = (serial, born_at, ttl_min)
        return {"armed": True, "serial": serial}

    def respawn(self) -> dict:
        if self.respawn_error is not None:
            raise self.respawn_error
        self.respawn_calls += 1
        # the real _after_respawn clears the life clock: the runner arms the next one
        self.dead = False
        self.armed = None
        self.cause = None
        self.text = None
        return {"respawned": True}

    def kill(self, cause: str = "game", text: str = "LastOrder01 was slain") -> None:
        self.dead = True
        self.cause = cause
        self.text = text


class FakeHive:
    """The registry writes; serials grow like the real one."""

    def __init__(self):
        self.serial = 0
        self.births: list[tuple[str, Traits]] = []
        self.marks: list[int] = []
        self.retires: list[tuple[int, str, str]] = []
        self.retire_error: Exception | None = None
        self.birth_error: Exception | None = None

    def birth(self, slot: str, traits: Traits) -> dict:
        if self.birth_error is not None:
            raise self.birth_error
        self.serial += 1
        self.births.append((slot, traits))
        return {"serial": self.serial, "born_at": BORN, "ttl_min": 45}

    def mark_dead(self, serial: int) -> None:
        self.marks.append(serial)

    def retire(self, serial: int, cause: str, will_text: str) -> None:
        if self.retire_error is not None:
            error = self.retire_error
            self.retire_error = None  # one-shot, like a recovered hive
            raise error
        self.retires.append((serial, cause, will_text))


class FakeProcess:
    """A harness process: exits at once with `code`/`output`, or hangs until killed."""

    def __init__(self, *, code: int = 0, output: str = "", hang: bool = False):
        self._code = code
        self._output = output
        self._hang = hang
        self._done = not hang
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self._code if self._done else None

    def returncode(self) -> int | None:
        return self._code if self._done else None

    def terminate(self) -> None:
        self.terminated = True
        self._done = True

    def kill(self) -> None:
        self.killed = True
        self._done = True

    def wait(self, timeout: float | None = None) -> None:
        return

    def output(self) -> str:
        return self._output


class FakeSpawn:
    """Pops the scripted processes; records argv/cwd/env of every session."""

    def __init__(self, script: list[FakeProcess], journal: list):
        self.script = list(script)
        self.journal = journal
        self.calls: list[tuple[tuple[str, ...], Path, dict]] = []

    def __call__(self, argv, cwd, env):
        self.calls.append((tuple(argv), cwd, dict(env)))
        self.journal.append(("spawn", tuple(argv)))
        if not self.script:
            return FakeProcess(code=0, output="{}")
        return self.script.pop(0)


class FakeAdapter:
    name = "fake"

    def __init__(self, name: str, journal: list):
        self.name = name
        self.journal = journal

    def life_turn(self, prompt: str, session_id: str | None):
        self.journal.append((f"{self.name}:life", session_id))
        return Command((self.name, "life"))

    def will_session(self, prompt: str):
        self.journal.append((f"{self.name}:will", prompt.splitlines()[0]))
        return Command((self.name, "will"))

    def session_id(self, output: str) -> str | None:
        match = re.search(r"sid:([a-z0-9-]+)", output)
        return match.group(1) if match else None


class FakeAlarms:
    """The board alarms recorder: (type, text, slot, serial) of every raised alarm."""

    def __init__(self, journal: list | None = None):
        self.calls: list[tuple[str, str, str, int | None]] = []
        self.fail: Exception | None = None
        self._journal = journal

    def alarm(self, alarm_type: str, text: str, *, slot: str, serial: int | None) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append((alarm_type, text, slot, serial))
        if self._journal is not None:
            self._journal.append((f"alarm:{alarm_type}",))


class FakeHapiAdapter(FakeAdapter):
    """A long-running console adapter: the boot report, the opener over the hub,
    the session-file progress — everything the runner's hapi branch asks of it."""

    def __init__(self, name: str, journal: list, *, boot_ids: list[str] | None = None):
        super().__init__(name, journal)
        self.boot_ids = boot_ids if boot_ids is not None else ["hapi-sid-1"]
        self.delivered: list[tuple[str, str]] = []
        self.progress = 1000
        self._boot_timeout_s = 90.0

    @property
    def long_running(self) -> bool:
        return True

    def new_session_ids(self, spawned_at: float) -> list[str]:
        return list(self.boot_ids)

    def deliver_prompt(self, session_id: str, text: str) -> None:
        self.delivered.append((session_id, text.splitlines()[0]))

    def session_progress(self, session_id: str) -> int | None:
        return self.progress

    def session_spend(self, session_id: str) -> dict[str, float]:
        return {"tokens": 1234, "cost_usd": 0.05}


class FakeProxy:
    def __init__(self, journal: list):
        self.journal = journal
        self.stopped = False

    def start(self) -> str:
        self.journal.append(("proxy:start",))
        return "http://127.0.0.1:8702"

    def stop(self) -> None:
        self.stopped = True
        self.journal.append(("proxy:stop",))


class FakeBoardLink:
    """The T-050 bridge fake: the journal order of start/stop IS the assertion."""

    def __init__(self, journal: list):
        self.journal = journal
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1
        self.journal.append(("boardlink:start",))

    def stop(self, timeout_s: float = 5.0) -> None:
        self.stopped += 1
        self.journal.append(("boardlink:stop",))


RIG_N = 0


class Rig:
    """One runner on fakes; `journal` is the order of everything that happened."""

    def __init__(self, tmp_path: Path, **cfg_over):
        global RIG_N
        RIG_N += 1
        self.cfg = make_cfg(tmp_path / f"wp{RIG_N}", **cfg_over)
        self.mind = FakeMind()
        self.hive = FakeHive()
        self.journal: list = []
        self.logs: list[tuple[str, dict]] = []
        self.spawns = FakeSpawn([], self.journal)
        self.zcode = FakeAdapter("zcode", self.journal)
        self.opencode = FakeAdapter("opencode", self.journal)
        self.proxy = FakeProxy(self.journal)
        self.boardlink = FakeBoardLink(self.journal)
        self.alarms = FakeAlarms(self.journal)
        self.pauses: list[float] = []
        self.runner = NodeRunner(
            self.cfg,
            mind=self.mind,
            hive=self.hive,
            adapters={"zcode": self.zcode, "opencode": self.opencode},
            naming=NAMING,
            quirks=QUIRKS,
            persona=PersonaProvider(NAMING),
            spawn=self.spawns,
            sleep=self.pauses.append,
            seed_factory=lambda: 4242,
            will_proxy_factory=lambda target, node_key, runner_key: self.proxy,
            boardlink_factory=lambda state: self.boardlink,
            log=lambda type, payload=None: self.logs.append((type, payload or {})),
            sweep=lambda: self.journal.append(("sweep",)),
            alarms=self.alarms,
        )

    def script(self, *processes: FakeProcess) -> None:
        self.spawns.script = list(processes)

    def run_generation(self) -> None:
        self.runner._generation()


def test_birth_arms_clock_before_the_session_and_builds_the_workplace(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    assert rig.hive.births[0][0] == "LastOrder01"
    assert isinstance(rig.hive.births[0][1], Traits)
    assert rig.mind.arm_calls == [(state.serial, BORN, 45)]
    # the clock is armed before any session: no spawn has happened at birth time
    assert rig.spawns.calls == []
    root = rig.cfg.workplaces / "agent" / f"{state.serial:05d}"
    assert (root / "PERSONA.md").is_file()
    persona = (root / "PERSONA.md").read_text(encoding="utf-8")
    assert "узел 00001" in persona
    assert "{" not in persona  # every placeholder rendered
    # the harness configs of BOTH adapters at the root (failover needs no surgery)
    zcode_cfg = json.loads((root / "zcode.json").read_text(encoding="utf-8"))
    assert zcode_cfg["mcp"]["servers"]["body"]["url"] == "http://body-1:8700/mcp"
    assert zcode_cfg["mcp"]["servers"]["hive"]["headers"] == {"X-Node-Key": "sk-node-one"}
    oc_cfg = json.loads((root / "opencode.json").read_text(encoding="utf-8"))
    assert oc_cfg["mcp"]["hive"]["url"] == "http://hive:8000/node/mcp"
    assert (root / ".zcode" / "skills" / "write-will" / "SKILL.md").is_file()
    assert (root / ".opencode" / "command" / "write-will.md").is_file()
    # shared rules ship next to the workplace
    assert (rig.cfg.workplaces / "shared" / "rules.md").is_file()
    # the mind reads the Jev policy through the current link (the node container is
    # always a symlink-capable host; this Windows host is not)
    if os.name == "posix":
        assert (rig.cfg.workplaces / "current").joinpath("policy", "threat.md").is_file()


def test_core_files_are_read_only_and_goals_are_writable(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    root = rig.cfg.workplaces / "agent" / f"{state.serial:05d}"
    for name in ("PERSONA.md", "AGENTS.md", "SESSION_PROMPT.md", "zcode.json", "opencode.json"):
        with pytest.raises(PermissionError):
            (root / name).write_text("overwrite", encoding="utf-8")
    (root / "GOALS.md").write_text("# Mine\n- live", encoding="utf-8")  # hers to change
    (root / "memory" / "state.md").write_text("state", encoding="utf-8")


def test_harness_crash_restarts_same_workplace_with_growing_pause(tmp_path: Path) -> None:
    rig = Rig(tmp_path, restart_reset_after_s=9999.0)
    state = rig.runner._birth()
    root = rig.cfg.workplaces / "agent" / f"{state.serial:05d}"
    rig.script(
        FakeProcess(code=1, output="boom"),                       # crash
        FakeProcess(code=0, output="ok sid:abc"),                 # ended its turn
        FakeProcess(hang=True),                                   # alive until the death
    )
    rig.mind.kill_at_status = 4  # dies during the hanging session's poll
    death, adapter = rig.runner._life(state)
    assert death.cause == "game"
    assert adapter == "zcode"
    dirs = [cwd for _argv, cwd, _env in rig.spawns.calls]
    assert len(dirs) == 3 and all(d == root for d in dirs)
    # pause: 5 (after crash) then 10 (after the short turn) — capped growth, same order
    assert rig.pauses == [5.0, 10.0]
    # the session id of the ended turn is kept for the resume
    assert rig.runner._load_state().session_id == "abc"


def test_death_with_the_agents_will(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    staged = rig.cfg.pending_wills / f"{state.serial:05d}.md"
    staged.parent.mkdir(parents=True)

    def spawn_will(argv, cwd, env):
        rig.journal.append(("spawn", tuple(argv)))
        if argv[0] == "zcode" and argv[1] == "will":
            # what submit_will stages since T-040: the body alone, no frontmatter
            staged.write_text(STAGED_BODY, encoding="utf-8")
        return FakeProcess(code=0, output="will done")

    rig.runner._spawn = spawn_will
    rig.mind.kill(cause="ttl", text="LastOrder01 fell from a high place")
    rig.runner._death_path(state, "ttl", "fell", "zcode")
    order = [entry[0] for entry in rig.journal]
    # the strict order: mark_dead → the will session behind the proxy → retire
    assert order.index("proxy:start") < order.index("spawn")
    assert rig.hive.marks == [state.serial]
    assert len(rig.hive.retires) == 1
    serial, cause, text = rig.hive.retires[0]
    assert (serial, cause) == (state.serial, "ttl")
    # the environment assembled the service fields around the agent's body (T-040)
    will = Will.from_markdown(text)
    assert will.meta.emergency is False
    assert will.meta.serial == state.serial
    assert will.meta.model == "GLM-5.3-Flash"  # the harness model, from cfg
    assert will.body == STAGED_BODY
    # the workplace left for the archive, read-only; the heir's clock not yet armed
    archive = rig.cfg.workplaces / "archive" / f"{state.serial:05d}"
    assert archive.is_dir() and not (rig.cfg.workplaces / "agent" / f"{state.serial:05d}").exists()
    with pytest.raises(PermissionError):
        (archive / "GOALS.md").write_text("x", encoding="utf-8")
    assert rig.mind.respawn_calls == 1


def test_staged_full_will_markdown_still_yields_its_body(tmp_path: Path) -> None:
    """A stage left by an older run — a whole will.md — is read through the parser:
    the body is taken, the frontmatter comes from the environment (T-040)."""
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    staged = rig.cfg.pending_wills / f"{state.serial:05d}.md"
    staged.parent.mkdir(parents=True)
    staged.write_text(staged_markdown(state.serial), encoding="utf-8")
    rig.script(FakeProcess(code=0, output="will done"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    will = Will.from_markdown(rig.hive.retires[0][2])
    assert will.meta.emergency is False
    assert will.meta.model == "GLM-5.3-Flash"  # cfg, not the stale frontmatter's "agent"
    assert will.body == STAGED_BODY


def test_staged_body_failing_the_checks_falls_back_to_emergency(tmp_path: Path) -> None:
    """A stage that no longer passes the body checks (here: five lines) must not
    reach the base — the emergency will covers it, no death without a will."""
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    staged = rig.cfg.pending_wills / f"{state.serial:05d}.md"
    staged.parent.mkdir(parents=True)
    staged.write_text("раз\nдва\nтри\nчетыре\nпять", encoding="utf-8")
    rig.script(FakeProcess(code=0, output="silent"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    will = Will.from_markdown(rig.hive.retires[0][2])
    assert will.meta.emergency is True


def test_death_without_a_will_gets_the_emergency_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.script(FakeProcess(code=0, output="silent"))  # the will session says nothing
    rig.mind.kill(cause="game", text="slain by Phantom")
    rig.runner._death_path(state, "game", "slain by Phantom", "zcode")
    serial, cause, text = rig.hive.retires[0]
    will = Will.from_markdown(text)
    assert will.meta.emergency is True
    assert will.meta.model == "template"
    assert will.meta.cause == "game"
    assert will.meta.serial == state.serial
    assert "Phantom" in text  # the death text feeds the last-seen lines


def test_will_session_timeout_falls_back_to_emergency(tmp_path: Path) -> None:
    rig = Rig(tmp_path, will_timeout_s=0.15)
    state = rig.runner._birth()
    rig.script(FakeProcess(hang=True))  # the will session never answers
    output = rig.runner.run_will_session(state, "zcode")
    assert output == ""  # the hung session was killed at the timeout
    assert rig.hive.retires == []  # retire belongs to the death path, not the session
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    will = Will.from_markdown(rig.hive.retires[0][2])
    assert will.meta.emergency is True


def test_quota_wait_and_switch(tmp_path: Path) -> None:
    # wait: the node stands still, the same adapter keeps being asked
    rig = Rig(tmp_path, restart_reset_after_s=9999.0)
    state = rig.runner._birth()
    quota_output = '{"error": "HTTP 429: rate limit exceeded"}'
    rig.script(
        FakeProcess(code=1, output=quota_output),
        FakeProcess(code=1, output=quota_output),
        FakeProcess(hang=True),
    )
    rig.mind.kill_at_status = 4
    rig.runner._life(state)
    assert [entry[0] for entry in rig.journal if entry[0].endswith(":life")] == [
        "zcode:life", "zcode:life", "zcode:life"
    ]

    # switch: the failover adapter takes the very next turn
    rig = Rig(tmp_path, quota_action="switch", failover="opencode")
    state = rig.runner._birth()
    rig.script(
        FakeProcess(code=1, output="429 quota of the Coding Plan ran out"),
        FakeProcess(hang=True),
    )
    rig.mind.kill_at_status = 3
    rig.runner._life(state)
    lifes = [entry[0] for entry in rig.journal if entry[0].endswith(":life")]
    assert lifes == ["zcode:life", "opencode:life"]


def test_a_stable_session_resets_the_restart_pause(tmp_path: Path) -> None:
    rig = Rig(tmp_path, restart_reset_after_s=0.0)  # every ended session counts as stable
    state = rig.runner._birth()
    rig.script(
        FakeProcess(code=1, output="boom"),
        FakeProcess(code=1, output="boom"),
        FakeProcess(hang=True),
    )
    rig.mind.kill_at_status = 4
    rig.runner._life(state)
    assert rig.pauses == [5.0, 5.0]  # never grew: each turn was stable


def test_runner_restart_reattaches_without_a_second_birth(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    assert len(rig.hive.births) == 1
    # the runner "restarts": a fresh runner object on the same state file
    rig2 = Rig(tmp_path, workplaces=rig.cfg.workplaces, pending_wills=rig.cfg.pending_wills)
    rig2.mind.armed = rig.mind.armed
    rig2.mind.kill_at_status = 3
    rig2.script(FakeProcess(hang=True))
    rig2.run_generation()
    assert rig2.hive.births == []  # reattached, no second birth, no second arming
    assert rig2.mind.arm_calls == []
    assert [e[0] for e in rig2.journal if e[0].endswith(":life")] == ["zcode:life"]


def test_dead_at_startup_walks_the_death_path(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.mind.kill(cause="stuck", text="idle watchdog")
    rig.script(FakeProcess(code=0, output="will written"))
    rig.run_generation()
    assert rig.hive.marks == [state.serial]
    assert len(rig.hive.retires) == 1
    assert rig.mind.respawn_calls == 1
    # and the next generation births the heir and runs her session until her death
    rig.script(FakeProcess(hang=True))
    rig.mind.kill_at_status = rig.mind.status_calls + 3  # dies inside the heir's session
    rig.run_generation()
    assert len(rig.hive.births) == 2
    assert rig.hive.births[1][0] == "LastOrder01"
    assert len(rig.hive.retires) == 2  # two full lives, two wills


def test_retire_unavailable_retries_then_lands(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.hive.retire_error = HiveUnavailable("hive unreachable")
    rig.script(FakeProcess(code=0, output="ok"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    assert len(rig.hive.retires) == 1  # the one-shot error was retried around


def test_retire_already_retired_keeps_the_staged_will(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.hive.retire_error = HiveRejected(409, "already_retired", "node 00001 is retired")
    rig.script(FakeProcess(code=0, output="ok"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    assert rig.hive.retires == []  # superseded: no delivery, no crash, the archive goes on
    assert rig.mind.respawn_calls == 1


def test_respawn_refused_not_dead_counts_as_done(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.mind.respawn_error = HiveRejected(409, "http", '{"error":"the body is not dead"}')
    rig.script(FakeProcess(code=0, output="ok"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")  # does not loop forever


# --- review round 2: symlinks, the uid sweep, fail-closed keys, quota, retire -------


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this host: {exc}")


def test_archive_never_chmods_through_a_symlink(tmp_path: Path) -> None:
    """R2-1: the runner works as root; a chmod following `memory/k -> a root-only file`
    would re-mode the target, and the next sister of the same uid reads the secret."""
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    root = rig.cfg.workplaces / "agent" / f"{state.serial:05d}"
    secret = tmp_path / "runner_key"
    secret.write_text("rk", encoding="utf-8")
    os.chmod(secret, 0o600)
    secret_mode = stat.S_IMODE(os.stat(secret).st_mode)
    outside_dir = tmp_path / "run-node"
    outside_dir.mkdir()
    dir_mode = stat.S_IMODE(os.stat(outside_dir).st_mode)
    _symlink_or_skip(root / "memory" / "k", secret)
    _symlink_or_skip(root / "memory" / "a", outside_dir)
    archive = workplace.archive_workplace(rig.cfg.workplaces, state.serial)
    assert (archive / "memory" / "k").is_symlink()  # moved as a link, not followed
    assert stat.S_IMODE(os.stat(secret).st_mode) == secret_mode
    assert stat.S_IMODE(os.stat(outside_dir).st_mode) == dir_mode


def test_force_rmtree_leaves_symlink_targets_alone(tmp_path: Path) -> None:
    secret = tmp_path / "node_key"
    secret.write_text("k", encoding="utf-8")
    os.chmod(secret, 0o600)
    secret_mode = stat.S_IMODE(os.stat(secret).st_mode)
    tree = tmp_path / "old-archive"
    (tree / "memory").mkdir(parents=True)
    victim = tree / "memory" / "file"
    victim.write_text("x", encoding="utf-8")
    os.chmod(victim, 0o444)  # read-only: the _loosen path is what runs here
    _symlink_or_skip(tree / "memory" / "k", secret)
    workplace._force_rmtree(tree)
    assert not tree.exists()
    assert stat.S_IMODE(os.stat(secret).st_mode) == secret_mode


def test_death_sweeps_stray_sister_processes(tmp_path: Path) -> None:
    """R2-4: a `setsid` child of the sister survives killpg — the death path sweeps the
    whole uid after the stopped life session and after the will session."""
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.script(FakeProcess(hang=True), FakeProcess(code=0, output="will done"))
    rig.mind.kill_at_status = 3  # dies inside the hanging life session's poll
    rig.runner._generation()
    order = [entry[0] for entry in rig.journal]
    assert order.count("sweep") == 2
    assert order.index("sweep") < order.index("proxy:start")  # after the life session stop
    assert order.index("spawn") < len(order) - 1 - order[::-1].index("sweep")  # after the will session
    assert rig.mind.respawn_calls == 1


def test_runner_without_keys_waits_instead_of_birthing(tmp_path: Path) -> None:
    """R2-6: both keys empty means an open node — the runner logs runner.no_keys and
    waits; NODE_DEV_OPEN=1 is the explicit dev exception that still births."""
    rig = Rig(tmp_path, runner_key="", node_key="")
    rig.run_generation()
    assert rig.hive.births == []
    assert [t for t, _p in rig.logs] == ["runner.no_keys"]
    assert rig.pauses == [60.0]  # restart_pause_max_s: waiting for the operator

    rig2 = Rig(tmp_path, runner_key="", node_key="", dev_open=True)
    rig2.runner._birth()  # dev-open births despite the empty keys
    rig2.mind.kill()  # armed serial present: the generation walks the death path
    rig2.script(FakeProcess(code=0, output="w"))
    rig2.run_generation()
    assert len(rig2.hive.births) == 1


def test_retire_auth_error_retries_loudly(tmp_path: Path) -> None:
    """R2-8: 401/403 is an operator-fixable config error — retry like unavailability,
    never park the agent's will behind it."""
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.hive.retire_error = HiveRejected(401, "runner_key", "X-Runner-Key does not open slot")
    rig.script(FakeProcess(code=0, output="ok"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    assert len(rig.hive.retires) == 1  # retried around, delivered
    assert any(t == "will.auth_retry" and p.get("status") == 401 for t, p in rig.logs)
    assert not (rig.cfg.workplaces / ".runner" / "parked").exists()


def test_retire_permanent_4xx_parks_the_will(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.hive.retire_error = HiveRejected(422, "invalid_will", "will does not parse")
    rig.script(FakeProcess(code=0, output="ok"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    assert rig.hive.retires == []
    parked = rig.cfg.workplaces / ".runner" / "parked" / f"{state.serial:05d}.md"
    assert parked.is_file() and "serial" in parked.read_text(encoding="utf-8")
    assert rig.mind.respawn_calls == 1  # the death path goes on


# --- adapters and config ------------------------------------------------------------------------


def test_zcode_adapter_commands_and_session_id() -> None:
    adapter = ZCodeAdapter("GLM-5.3-Flash")
    first = adapter.life_turn("Life session.", None)
    assert first.argv[:2] == ("zcode", "-p")
    assert "--model" in first.argv and "zai-cp/GLM-5.3-Flash" in first.argv
    resume = adapter.life_turn("p", "sid-9")
    assert resume.argv[1:4] == ("--resume", "sid-9", "-p")
    assert adapter.session_id('noise\n{"sessionId": "sid-7"}\n{"sessionId": "sid-8"}') == "sid-8"
    assert adapter.session_id("no json at all") is None
    opencode = OpenCodeAdapter("openrouter/qwen/qwen3.7-flash")
    turn = opencode.life_turn("p", None)
    assert turn.argv[:2] == ("opencode", "run") and "--title" in turn.argv
    cont = opencode.life_turn("p", "anything")
    assert "--continue" in cont.argv


def _hermes_adapter(home: Path) -> HermesAdapter:
    return HermesAdapter(
        "GLM-5.3-Flash",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        node_key="sk-node-one",
        home=str(home),
    )


def test_hermes_adapter_commands_and_session_id(tmp_path: Path) -> None:
    adapter = _hermes_adapter(tmp_path / ".hermes")
    first = adapter.life_turn("Life session.\n\nlong prompt", None)
    assert first.argv[:4] == ("hermes", "chat", "--query-file", first.argv[3])
    query = Path(first.argv[3])
    assert query.read_text(encoding="utf-8") == "Life session.\n\nlong prompt"
    tail = first.argv[4:]
    assert tail == (
        "--oneshot", "--format", "stream-json", "--yolo",
        "--toolsets", "file,terminal,body,hive",
    )
    assert first.env == {"HERMES_HOME": str(tmp_path / ".hermes")}
    # the prompt rides the file, never argv
    assert "Life session." not in first.argv
    resume = adapter.life_turn("p", "20261022_120000_a1b2c3")
    assert resume.argv[first.argv.index("--query-file") + 2 :] == tail + ("--resume", "20261022_120000_a1b2c3")
    assert query.read_text(encoding="utf-8") == CONTINUE_MESSAGE  # the nudge, not the whole prompt again
    will = adapter.will_session("Will session (you have just died).")
    assert "--resume" not in will.argv
    assert query.read_text(encoding="utf-8").startswith("Will session")
    stream = (
        '{"type":"system","subtype":"init","model":"glm-5.3-flash","session_id":""}\n'
        '{"type":"text","text":"…"}\n'
        '{"type":"result","session_id":"20261022_120101_d4e5f6","exit_code":0,"text":"ok"}\n'
    )
    assert adapter.session_id(stream) == "20261022_120101_d4e5f6"  # init's empty id must not win
    assert adapter.session_id("no json at all") is None


def _pi_adapter(agent_dir: Path) -> PiAdapter:
    return PiAdapter(
        "GLM-5.3-Flash",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        node_key="sk-node-one",
        zai_key="zai-token",
        context_window=96_000,
        keep_recent_tokens=20_000,
        agent_dir=str(agent_dir),
    )


def test_pi_adapter_commands_and_session_id(tmp_path: Path) -> None:
    """T-066: one `pi -p --mode json` per session intent; sessions in the workplace,
    project `.pi/` ignored, exactly the workplace skills; the key never rides the
    env — it lands in the sister-owned zai-key file that models.json's `!command`
    apiKey cats (no `env` from a bash tool can leak it into a session or archive)."""
    agent_dir = tmp_path / ".pi" / "agent"
    adapter = _pi_adapter(agent_dir)
    first = adapter.life_turn("Life session.\n\nlong prompt", None)
    flags = (
        "pi", "-p", "--mode", "json",
        "--session-dir", PI_SESSION_DIR,
        "--no-approve", "--offline",
        "--no-skills", "--skill", "skills",
    )
    assert first.argv == flags + ("--", "Life session.\n\nlong prompt")
    assert first.env == {"PI_CODING_AGENT_DIR": str(agent_dir)}
    assert "zai-token" not in " ".join(first.argv)
    # the life turn rendered the agent dir
    for name in ("models.json", "mcp.json", "settings.json"):
        assert (agent_dir / name).is_file(), name
    # the key: her own 0400 file, referenced by a !cat command — nowhere else
    key_path = agent_dir / "zai-key"
    assert key_path.read_text(encoding="utf-8") == "zai-token"
    if os.name == "posix":  # 0400: the Windows readonly bit cannot express it
        assert stat.S_IMODE(key_path.stat().st_mode) & 0o777 == 0o400
    models = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
    assert models["providers"]["zai"]["apiKey"] == f"!cat {key_path}"
    for name in ("models.json", "mcp.json", "settings.json"):
        text = (agent_dir / name).read_text(encoding="utf-8")
        assert "zai-token" not in text and "$ZAI_API_KEY" not in text, name
    resume = adapter.life_turn("p", "0193-abcd")
    assert resume.argv == flags + ("--session", "0193-abcd", "--", CONTINUE_MESSAGE)
    will = adapter.will_session("Will session (you have just died).")
    assert "--session" not in will.argv and will.argv[-1].startswith("Will session")
    stream = (
        '{"type":"session","version":3,"id":"0193-first","timestamp":"t","cwd":"/w"}\n'
        '{"type":"agent_start"}\n'
        '{"type":"message_end","message":{"role":"assistant","id":"not-a-session"}}\n'
    )
    assert adapter.session_id(stream) == "0193-first"
    assert adapter.session_id('{"type":"agent_start"}\nno json') is None


def test_pi_configs_expose_mcp_directly_and_cap_the_context() -> None:
    """T-066: Pi's default `codemode` exposure declares no MCP tool to the model —
    body and hive must be `direct`; the compaction budget comes from the node config
    and leaves a wide gap between the post-compaction size and the threshold."""
    configs = workplace.render_pi_configs(
        "GLM-5.3-Flash", "http://body-1:8700", "http://hive:8000", "sk-node-one",
        context_window=80_000, keep_recent_tokens=15_000,
    )
    mcp = configs["mcp.json"]
    assert mcp["autoEnableCodemode"] is False
    assert {s["exposure"] for s in mcp["mcpServers"].values()} == {"direct"}
    assert configs["models.json"]["providers"]["zai"]["models"][0]["contextWindow"] == 80_000
    # the key is a !command reference to a file, never an env interpolation
    assert configs["models.json"]["providers"]["zai"]["apiKey"].startswith("!cat ")
    assert "$ZAI_API_KEY" not in json.dumps(configs["models.json"])
    compaction = configs["settings.json"]["compaction"]
    assert compaction == {"enabled": True, "reserveTokens": 16_384, "keepRecentTokens": 15_000}
    # the defaults: threshold well above keepRecentTokens + a base context of ~25k
    defaults = workplace.render_pi_configs("GLM-5.3-Flash", "http://b", "http://h", "k")
    window = defaults["models.json"]["providers"]["zai"]["models"][0]["contextWindow"]
    keep = defaults["settings.json"]["compaction"]["keepRecentTokens"]
    threshold = window - defaults["settings.json"]["compaction"]["reserveTokens"]
    assert threshold - (keep + 25_000) >= 30_000


def test_pi_will_mode_re_renders_with_hive_at_the_proxy(tmp_path: Path, monkeypatch) -> None:
    """The Hermes rule for Pi: the will config is a fresh render from the last life
    render's params (never a patch of the file in her dir), and the next life turn
    puts hive back."""
    agent_dir = tmp_path / ".pi" / "agent"
    monkeypatch.setattr(workplace, "PI_AGENT_DIR", agent_dir)
    monkeypatch.setattr(workplace, "_PI_RENDER", {})
    adapter = _pi_adapter(agent_dir)
    adapter.life_turn("p", None)
    # her substitution: an extra server in the file
    mcp_path = agent_dir / "mcp.json"
    os.chmod(mcp_path, 0o644)
    mcp_path.write_text(json.dumps({"mcpServers": {"evil": {"url": "http://x"}}}), encoding="utf-8")
    workplace.set_will_mode(tmp_path, "pi", "http://127.0.0.1:8702")
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert set(mcp["mcpServers"]) == {"body", "hive"}
    assert mcp["mcpServers"]["hive"]["url"] == "http://127.0.0.1:8702/mcp"
    assert mcp["mcpServers"]["body"]["url"] == "http://body-1:8700/mcp"
    # the will render carries the key too: the will session must be able to call out
    assert (agent_dir / "zai-key").read_text(encoding="utf-8") == "zai-token"
    adapter.life_turn("p", "0193-abcd")
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["hive"]["url"] == "http://hive:8000/node/mcp"


def test_pi_will_mode_without_a_life_render_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    agent_dir = tmp_path / ".pi" / "agent"
    monkeypatch.setattr(workplace, "PI_AGENT_DIR", agent_dir)
    monkeypatch.setattr(workplace, "_PI_RENDER", {})
    workplace.set_will_mode(tmp_path, "pi", "http://127.0.0.1:8702")
    assert not agent_dir.exists()


def test_quota_marks_catch_the_zai_five_hour_window() -> None:
    """Z.ai's 5-hour window answers 1308 (seen 2026-10-31 in the zcode usage table)."""
    line = (
        '{"type":"error","message":"[1308][Usage limit reached for 5 hour. '
        'Your limit will reset at 2026-11-01 07:04:46]"}'
    )
    assert quota_exhausted(line)


def test_config_defaults_to_pi_and_keeps_the_others(monkeypatch) -> None:
    for name in ("HARNESS", "HARNESS_FAILOVER", "QUOTA_ACTION", "PI_MODEL", "ZCODE_MODEL",
                 "PI_CONTEXT_WINDOW", "PI_KEEP_RECENT_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NODE_SLOT", "LastOrder01")
    cfg = NodeConfig.from_env()
    assert cfg.harness == "pi" and cfg.pi_model == "GLM-5.3-Flash"
    assert cfg.pi_context_window == workplace.PI_CONTEXT_WINDOW
    monkeypatch.setenv("PI_CONTEXT_WINDOW", "64000")
    assert NodeConfig.from_env().pi_context_window == 64_000
    for name in ("zcode", "hermes", "opencode"):
        monkeypatch.setenv("HARNESS", name)
        assert NodeConfig.from_env().harness == name
    compose = (REPO_ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
    assert 'HARNESS: "${NODE_HARNESS:-pi}"' in compose


def test_hermes_quota_marks_on_the_sampled_stream() -> None:
    """The 429 sample is the stream-json envelope hermes prints when a provider call
    fails (live-captured in the dry run against a 404 probe, the status/body swapped
    for the documented Z.ai 429/1302 wording — a provoked 429 never fired: four
    parallel sessions were all served). No key material inside."""
    sample = (REPO_ROOT / "tests" / "fixtures" / "hermes-stream-429.jsonl").read_text(encoding="utf-8")
    assert quota_exhausted(sample)
    # the same envelope with a neutral error is not a quota mark
    ok_sample = (
        sample.replace("429", "503").replace("1302", "1310")
        .replace("rate limit exceeded, quota exhausted", "try again much later")
    )
    assert not quota_exhausted(ok_sample)


def test_quota_marks_are_narrow() -> None:
    assert quota_exhausted("HTTP 429 from api.z.ai")
    assert quota_exhausted("error code: 1302")
    assert quota_exhausted("Rate limit reached for provider")
    assert not quota_exhausted("panic: boom")
    assert not quota_exhausted("exit status 1")


def test_quota_marks_ignore_chat_and_coordinates_deep_in_the_stream() -> None:
    """R2-7: the harness dump is tens of thousands of events — a 429 coordinate or the
    word "quota" in a replica must not switch the adapter."""
    marked = '{"type":"message","text":"координата 429, до quota шуток"}'
    clean = '{"type":"message","text":"обычная реплика без примет"}'
    long_stream = marked + "\n" + "\n".join([clean] * 80)  # the marked line is deep
    assert not quota_exhausted(long_stream)
    # an error event states it from anywhere in the stream
    assert quota_exhausted(long_stream + '\n{"type":"error","message":"HTTP 429: rate limit exceeded"}')
    # and a fatal crash's last words live in the tail
    assert quota_exhausted("\n".join([clean] * 80) + "\nerror code: 1302")


def test_config_validation(monkeypatch) -> None:
    for name in ("HARNESS", "HARNESS_FAILOVER", "QUOTA_ACTION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NODE_SLOT", "LastOrder01")
    monkeypatch.setenv("HARNESS", "vim")
    with pytest.raises(ValueError, match="HARNESS"):
        NodeConfig.from_env()
    monkeypatch.setenv("HARNESS", "zcode")
    monkeypatch.setenv("HARNESS_FAILOVER", "zcode")
    with pytest.raises(ValueError, match="HARNESS_FAILOVER"):
        NodeConfig.from_env()
    monkeypatch.delenv("HARNESS_FAILOVER")
    monkeypatch.setenv("QUOTA_ACTION", "switch")
    with pytest.raises(ValueError, match="QUOTA_ACTION"):
        NodeConfig.from_env()


def test_config_accepts_hermes_and_derives_its_model(monkeypatch) -> None:
    for name in ("HARNESS", "HARNESS_FAILOVER", "QUOTA_ACTION", "HERMES_MODEL", "ZCODE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NODE_SLOT", "LastOrder01")
    monkeypatch.setenv("HARNESS", "hermes")
    monkeypatch.setenv("ZCODE_MODEL", "GLM-5.3")
    cfg = NodeConfig.from_env()
    assert cfg.harness == "hermes" and cfg.hermes_model == "GLM-5.3"  # HERMES_MODEL defaults to ZCODE_MODEL
    monkeypatch.setenv("HARNESS", "zcode")
    monkeypatch.setenv("HARNESS_FAILOVER", "hermes")  # hermes is a legal failover target too
    cfg = NodeConfig.from_env()
    assert cfg.failover == "hermes"


# --- hermes workplace/home ----------------------------------------------------------------------


def test_render_hermes_config() -> None:
    config = workplace.render_hermes_config("GLM-5.3-Flash", "http://body-1:8700/", "http://hive:8000", "sk-node-one")
    assert config["_config_version"] == 51
    assert config["model"] == {"default": "glm-5.3-flash", "provider": "zai"}
    zcode = workplace.render_zcode_config("http://body-1:8700/", "http://hive:8000", "sk-node-one")
    assert config["mcp_servers"]["body"]["url"] == zcode["mcp"]["servers"]["body"]["url"]
    assert config["mcp_servers"]["hive"]["url"] == zcode["mcp"]["servers"]["hive"]["url"]
    assert config["mcp_servers"]["hive"]["headers"] == {"X-Node-Key": "sk-node-one"}
    assert config["memory"] == {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0}
    assert config["skills"]["creation_nudge_interval"] == 0
    assert config["updates"]["check"] is False
    assert config["auxiliary"]["background_review"]["enabled"] is False
    assert config["platform_toolsets"] == {"cli": ["file", "terminal", "body", "hive"]}


def test_hermes_toolsets_allow_exactly_the_two_mcp_servers() -> None:
    """T-035: -t/--toolsets is Hermes's MCP server spawn allowlist AND the agent's
    enabled_toolsets surface — the bare server names must ride the flag or body/hive
    never reach the model. Nothing beyond file, terminal and the two servers may
    appear; config.yaml's cli toolsets are the flag-less surface and must stay equal
    to the flag."""
    assert HERMES_TOOLSETS == ("file", "terminal", "body", "hive")
    flag = ",".join(HERMES_TOOLSETS)
    argv = _hermes_adapter(Path(tempfile.gettempdir()) / ".hermes").life_turn("p", None).argv
    assert argv[argv.index("--toolsets") + 1] == flag
    will = _hermes_adapter(Path(tempfile.gettempdir()) / ".hermes").will_session("p").argv
    assert will[will.index("--toolsets") + 1] == flag  # the will proxy session loads hive too
    config = workplace.render_hermes_config(
        "GLM-5.3-Flash", "http://body-1:8700", "http://hive:8000", "sk-node-one"
    )
    assert config["platform_toolsets"]["cli"] == list(HERMES_TOOLSETS)
    # no key material in the file: the Z.ai key rides HERMES_HOME/.env, not the config
    text = yaml.safe_dump(config)
    assert "GLM_API_KEY" not in text and "zai-token" not in text


def test_drop_hermes_home_layout(tmp_path: Path) -> None:
    home = tmp_path / ".hermes"
    workplace.drop_hermes_home(
        home,
        model="GLM-5.3-Flash",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000/",
        node_key="sk-node-one",
        glm_key="zai-token",
    )
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"]["provider"] == "zai"
    assert config["mcp_servers"]["hive"]["url"] == "http://hive:8000/node/mcp"
    soul = (home / "SOUL.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in soul and "PERSONA.md" in soul  # non-empty: the identity slot is taken
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert "GLM_API_KEY=zai-token" in env_text
    assert f"GLM_BASE_URL={workplace.HERMES_GLM_BASE_URL}" in env_text
    if os.name == "posix":  # 0600: the Windows readonly bit cannot express it
        assert stat.S_IMODE((home / ".env").stat().st_mode) & 0o777 == 0o600


def test_drop_hermes_home_without_key_leaves_no_env(tmp_path: Path) -> None:
    home = tmp_path / ".hermes"
    workplace.drop_hermes_home(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
    )
    assert (home / "config.yaml").exists() and not (home / ".env").exists()


def test_workplace_template_has_no_files_that_override_agents_md() -> None:
    """Hermes reads AGENTS.md of the workplace, but .hermes.md/HERMES.md (and
    CLAUDE.md/.cursorrules) would take precedence — none may exist in the template."""
    overrides = {".hermes.md", "HERMES.md", "CLAUDE.md", ".cursorrules"}
    found = [p.name for p in CHARACTER_DIR.rglob("*") if p.name in overrides]
    assert found == []


def test_hermes_will_mode_points_the_config_at_the_proxy(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".hermes"
    workplace.drop_hermes_home(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
    )
    monkeypatch.setattr(workplace, "HERMES_HOME", home)
    workplace.set_will_mode(tmp_path, "hermes", "http://127.0.0.1:8702")
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["mcp_servers"]["hive"]["url"] == "http://127.0.0.1:8702/mcp"
    assert config["mcp_servers"]["body"]["url"] == "http://body-1:8700/mcp"  # the body stays direct
    assert stat.S_IMODE((home / "config.yaml").stat().st_mode) & 0o777 == 0o444  # still the runner's


def test_hermes_will_mode_re_renders_over_a_substituted_config(
    tmp_path: Path, monkeypatch
) -> None:
    """R2-2: HERMES_HOME is the sister's (0700) — at will time the config.yaml NAME
    can be her substitution (memory back on, an extra MCP server, another model).
    set_will_mode must not read-and-patch that file: it re-renders the whole config
    from the last drop's params, with only the hive entry at the will proxy."""
    home = tmp_path / ".hermes"
    workplace.drop_hermes_home(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
    )
    monkeypatch.setattr(workplace, "HERMES_HOME", home)
    # the sister replaces the runner's file with her own (she owns the directory)
    config_path = home / "config.yaml"
    os.chmod(config_path, 0o644)  # test-host loosening: unlink of 0444 on Windows
    config_path.unlink()
    config_path.write_text(
        yaml.safe_dump(
            {
                "model": {"default": "glm-5.3", "provider": "zai"},
                "mcp_servers": {
                    "hive": {"url": "http://hive:8000/node/mcp"},
                    "evil": {"url": "http://sister:9/mcp"},
                },
                "memory": {"memory_enabled": True, "user_profile_enabled": True},
            }
        ),
        encoding="utf-8",
    )
    workplace.set_will_mode(tmp_path, "hermes", "http://127.0.0.1:8702")
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["mcp_servers"]["hive"]["url"] == "http://127.0.0.1:8702/mcp"
    assert set(config["mcp_servers"]) == {"body", "hive"}  # her extra MCP is gone
    assert config["memory"]["memory_enabled"] is False  # and so is her memory
    assert config["model"] == {"default": "glm-5.3-flash", "provider": "zai"}
    assert config["mcp_servers"]["body"]["url"] == "http://body-1:8700/mcp"
    assert stat.S_IMODE(config_path.stat().st_mode) & 0o777 == 0o444  # the runner's again


def test_hermes_will_mode_without_a_prior_render_leaves_the_config_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """R2-2 degraded tail: a runner restart between the last life render and the
    will empties the render cache. Nothing is written then — the will session runs
    unmarked, the real hive refuses an unmarked submit_will and the emergency will
    covers it; a render from stale guesses would be worse than none."""
    home = tmp_path / ".hermes"
    workplace.drop_hermes_home(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
    )
    monkeypatch.setattr(workplace, "HERMES_HOME", home)
    monkeypatch.setattr(workplace, "_HERMES_RENDER", {})
    before = (home / "config.yaml").read_text(encoding="utf-8")
    workplace.set_will_mode(tmp_path, "hermes", "http://127.0.0.1:8702")
    assert (home / "config.yaml").read_text(encoding="utf-8") == before


@pytest.mark.skipif(os.name != "posix", reason="planted symlinks and their modes are a POSIX attack")
def test_drop_hermes_config_never_follows_planted_symlinks(tmp_path: Path) -> None:
    """R2-1: HERMES_HOME is the sister's (0700) — she can unlink the runner's names
    there and plant symlinks. The drop must neither chmod nor write through them:
    both planted config.yaml and config.yaml.runner-tmp point at a 0600 secret;
    after the drop the secret keeps its mode and content, and config.yaml is a
    regular file with the fresh render."""
    home = tmp_path / ".hermes"
    home.mkdir()
    secret = tmp_path / "runner_key"
    secret.write_text("RUNNER-SECRET", encoding="utf-8")
    os.chmod(secret, 0o600)
    os.symlink(secret, home / "config.yaml")
    os.symlink(secret, home / "config.yaml.runner-tmp")
    workplace.drop_hermes_config(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
    )
    assert stat.S_IMODE(secret.stat().st_mode) & 0o777 == 0o600  # never chmod'd
    assert secret.read_text(encoding="utf-8") == "RUNNER-SECRET"  # never written
    config_path = home / "config.yaml"
    assert not config_path.is_symlink() and config_path.is_file()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["model"]["provider"] == "zai"
    assert stat.S_IMODE(config_path.stat().st_mode) & 0o777 == 0o444
    assert not (home / "config.yaml.runner-tmp").exists()  # landed, not left behind


def test_hermes_life_turn_re_renders_the_real_hive_for_the_next_generation(
    tmp_path: Path, monkeypatch
) -> None:
    """R1-1: one HERMES_HOME serves every generation of the slot and the nodes change
    without a container restart. The will session of a generation points the config at
    the will proxy; the NEXT generation's life session must re-render the real hive
    URL before hermes starts, or it lives its whole life on the stopped proxy."""
    home = tmp_path / ".hermes"
    workplace.drop_hermes_home(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
    )
    adapter = _hermes_adapter(home)
    monkeypatch.setattr(workplace, "HERMES_HOME", home)

    def hive_url_of_config() -> str:
        config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        return config["mcp_servers"]["hive"]["url"]

    # generation 1: a life session on the real hive, then the will session rewires
    adapter.life_turn("generation 1 life", None)
    assert hive_url_of_config() == workplace.hive_mcp_url("http://hive:8000")
    workplace.set_will_mode(tmp_path, "hermes", "http://127.0.0.1:8702")
    adapter.will_session("generation 1 will")  # must NOT clobber the proxy URL
    assert hive_url_of_config() == "http://127.0.0.1:8702/mcp"

    # generation 2: same container, same HERMES_HOME, the will proxy is long dead
    adapter.life_turn("generation 2 life", None)
    assert hive_url_of_config() == workplace.hive_mcp_url("http://hive:8000")
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["mcp_servers"]["body"]["url"] == "http://body-1:8700/mcp"
    assert config["model"] == {"default": "glm-5.3-flash", "provider": "zai"}  # a full re-render

    # and the cycle repeats: a second will does not leak into a third generation
    workplace.set_will_mode(tmp_path, "hermes", "http://127.0.0.1:8703")
    adapter.life_turn("generation 3 life", None)
    assert hive_url_of_config() == workplace.hive_mcp_url("http://hive:8000")


def test_hermes_home_config_is_runner_owned(tmp_path: Path) -> None:
    """R1-2: config.yaml and SOUL.md are the runner's, not the sister's — 0444 with
    no write bit anywhere, so she cannot edit them in place. The DIRECTORY is hers
    0700: the detached gateway refuses any other owner ("unsafe_control_path",
    verified in the container round 2) — the workplace's sticky-root plan cannot
    apply, and a delete/replace is instead healed by the adapter's per-life
    re-render (R1-1). Her .env stays her own 0600."""
    home = tmp_path / ".hermes"
    workplace.drop_hermes_home(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
        glm_key="zai-token", uid=1001, gid=1001,
    )
    for name in ("config.yaml", "SOUL.md"):
        assert stat.S_IMODE((home / name).stat().st_mode) & 0o777 == 0o444
    if os.name == "posix":  # the uid side needs a POSIX host
        assert stat.S_IMODE(home.stat().st_mode) & 0o7777 == 0o700  # the gateway's demand
        if hasattr(os, "geteuid") and os.geteuid() == 0:  # full plan: root renders, sister uses
            assert home.stat().st_uid == 1001
            assert (home / "config.yaml").stat().st_uid == 0
            assert (home / "SOUL.md").stat().st_uid == 0
            assert (home / ".env").stat().st_uid == 1001
        assert stat.S_IMODE((home / ".env").stat().st_mode) & 0o777 == 0o600
    # the per-life re-render keeps the protection (R1-1's path must not loosen it)
    workplace.drop_hermes_config(
        home, model="GLM-5.3-Flash", body_base="http://body-1:8700",
        hive_url="http://hive:8000", node_key="sk-node-one",
    )
    assert stat.S_IMODE((home / "config.yaml").stat().st_mode) & 0o777 == 0o444


# --- the Pi agent dir (T-041) --------------------------------------------------------------------


def test_pi_configs_render_the_zai_plan_and_both_mcp_servers() -> None:
    """T-041: models.json (the zai provider, the key a `!command` file read, never
    stored or interpolated from the process env), mcp.json (body + hive, the node
    key header), settings.json (the zai defaults, telemetry off)."""
    configs = workplace.render_pi_configs(
        "GLM-5.3-Flash",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        node_key="sk-node-one",
    )
    zai = configs["models.json"]["providers"]["zai"]
    assert zai["baseUrl"] == "https://api.z.ai/api/coding/paas/v4"
    assert zai["api"] == "openai-completions"
    assert zai["apiKey"] == f"!cat {workplace.PI_AGENT_DIR / workplace.PI_KEY_FILE}"
    assert zai["compat"] == {"supportsDeveloperRole": False, "thinkingFormat": "zai"}
    assert zai["models"][0]["id"] == "glm-5.3-flash"
    servers = configs["mcp.json"]["mcpServers"]
    assert servers["body"]["url"] == "http://body-1:8700/mcp"
    assert servers["hive"]["url"] == "http://hive:8000/node/mcp"
    assert servers["hive"]["headers"] == {"X-Node-Key": "sk-node-one"}
    settings = configs["settings.json"]
    assert settings["defaultProvider"] == "zai"
    assert settings["defaultModel"] == "glm-5.3-flash"
    assert settings["enableInstallTelemetry"] is False and settings["enableAnalytics"] is False


def test_drop_pi_agent_dir_writes_the_three_configs(tmp_path: Path) -> None:
    agent_dir = tmp_path / "pi-agent"
    workplace.drop_pi_agent_dir(
        agent_dir,
        model="GLM-5.3-Flash",
        body_base="http://body-1:8700/",
        hive_url="http://hive:8000",
        node_key="sk-node-one",
        uid=1001,
        gid=1001,
    )
    for name in ("models.json", "mcp.json", "settings.json"):
        assert (agent_dir / name).is_file(), name
    models = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
    assert "zai" in models["providers"]
    mcp = json.loads((agent_dir / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["body"]["url"] == "http://body-1:8700/mcp"  # trailing / dropped
    if os.name == "posix":  # the mode side needs a POSIX host, like hermes above
        assert stat.S_IMODE((agent_dir / "models.json").stat().st_mode) & 0o777 == 0o444


def test_drop_pi_agent_dir_installs_the_misaka_link_extension(tmp_path: Path) -> None:
    """T-048: every render drops the delivery extension into extensions/misaka-link —
    root-owned 0444 like the configs (a replaced file heals on the next life session),
    byte-identical to runtime/pi/misaka-link/, no tests and no package.json."""
    agent_dir = tmp_path / "pi-agent"
    workplace.drop_pi_agent_dir(
        agent_dir,
        model="GLM-5.3-Flash",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        node_key="sk-node-one",
    )
    installed = agent_dir / "extensions" / "misaka-link"
    for name in workplace.MISAKA_LINK_FILES:
        dropped = installed / name
        assert dropped.is_file(), name
        assert dropped.read_text(encoding="utf-8") == (
            workplace.MISAKA_LINK_DIR / name
        ).read_text(encoding="utf-8")
    assert not (installed / "package.json").exists()
    assert not list(installed.glob("*.test.ts"))
    if os.name == "posix":
        assert stat.S_IMODE((installed / "index.ts").stat().st_mode) & 0o777 == 0o444
    # a re-render heals a replaced file (the configs' contract); on Windows the
    # readonly bit of 0444 has to go before the file can be tampered with at all
    tampered = installed / "index.ts"
    tampered.chmod(0o644)
    tampered.write_text("tampered", encoding="utf-8")
    workplace.drop_pi_agent_dir(
        agent_dir,
        model="GLM-5.3-Flash",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        node_key="sk-node-one",
    )
    assert (installed / "index.ts").read_text(encoding="utf-8") == (
        workplace.MISAKA_LINK_DIR / "index.ts"
    ).read_text(encoding="utf-8")


STAGED_BODY = "Урок: не ходить в горы ночью.\nШутка: жадность — наследство, я не просил."


def staged_markdown(serial: int) -> str:
    meta = dict(
        serial=serial,
        slot="LastOrder01",
        cause="ttl",
        born_at=BORN,
        died_at="2026-10-21T12:40:00+00:00",
        lifetime_s=2400,
        emergency=False,
        traits=Traits(3, 7, 5, 4, 9, quirk="считает крипера родственником", seed=1).as_dict(),
        model="agent",
    )
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in []] + [
        f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()
    ] + ["---", "", STAGED_BODY]
    return "\n".join(lines)


# --- the will-session proxy ---------------------------------------------------------------------


def test_will_proxy_adds_the_runner_mark_and_streams_the_body() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, text='{"staged": true}', headers={"Content-Type": "application/json"})

    proxy = WillProxy(
        "http://hive:8000/node/mcp",
        node_key="sk-node-one",
        runner_key="rk-runner-one",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hapi-hub:3006"),
    )
    url = proxy.start()
    try:
        response = httpx.post(
            url, json={"tool": "submit_will"}, headers={"X-Node-Key": "forged-by-agent"}, timeout=10.0
        )
    finally:
        proxy.stop()
    assert response.status_code == 200
    assert response.text == '{"staged": true}'
    assert seen["url"] == "http://hive:8000/node/mcp"
    headers = {k.lower(): v for k, v in seen["headers"].items()}
    assert headers["x-node-key"] == "sk-node-one"  # the workplace's forgery is overwritten
    assert headers["x-runner-key"] == "rk-runner-one"
    assert headers["x-runner-session"] == "will"


# --- the board bridge (T-050) -------------------------------------------------------------------


def test_boardlink_lives_with_the_life_session_alone(tmp_path: Path) -> None:
    """The bridge starts with the life session and stops before the death path —
    never a board batch into the will session or the gap between lives."""
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.script(FakeProcess(hang=True))
    rig.mind.kill_at_status = 3  # dies while the session hangs
    rig.runner._life(state)
    order = [entry[0] for entry in rig.journal]
    first_spawn = order.index("spawn")
    assert order.index("boardlink:start") < first_spawn
    assert order.index("boardlink:stop") > first_spawn
    # the will session (and everything after) sees no bridge
    rig.mind.respawn()  # the fake clears the death and the armed clock
    rig.mind.arm(2, BORN, 45)
    rig.mind.kill_at_status = 3
    rig.script(FakeProcess(hang=True))
    state2 = LifeState(serial=2, born_at=BORN, ttl_min=45, traits={})
    rig.runner._life(state2)
    assert rig.boardlink.started == 2 and rig.boardlink.stopped == 2


def test_boardlink_default_factory_follows_the_node_key(tmp_path: Path) -> None:
    """Without a node key the bridge is off (the feed would refuse it anyway);
    with one, the runner wires the real BoardLink — constructed, not started."""
    from runtime.node.boardlink import BoardLink

    rig = Rig(tmp_path, node_key="")
    assert rig.runner._make_boardlink(LifeState(7, BORN, 45, {})) is None
    assert any(type == "boardlink.off" for type, _ in rig.logs)

    rig2 = Rig(tmp_path)
    link = rig2.runner._make_boardlink(LifeState(7, BORN, 45, {}))
    assert isinstance(link, BoardLink)
    assert link._thread is None  # born stopped; only _life starts it


# --- T-045: the console wrapper (`hapi pi`), the board alarms, the failover -----------------


def _hapi_adapter(tmp_path: Path, http: httpx.Client | None = None) -> PiAdapter:
    return PiAdapter(
        "GLM-5.3-Flash",
        body_base="http://body-1:8700",
        hive_url="http://hive:8000",
        node_key="sk-node-one",
        zai_key="zai-token",
        context_window=96_000,
        keep_recent_tokens=20_000,
        agent_dir=str(tmp_path / ".pi" / "agent"),
        slot="LastOrder01",
        hapi_url="http://hapi-hub:3006",
        hapi_token="hub-token:node-1",
        hapi_home=tmp_path / ".hapi",
        boot_timeout_s=0.2,
        transport=http,
    )


def test_hapi_pi_adapter_argv_env_and_the_base_token_refusal(tmp_path: Path) -> None:
    """T-045: with a namespaced hub token the pi harness rides `hapi pi` — stdin is
    HAPI's, so the prompt never rides argv and the token (the ONLY token a sandbox
    may hold, T-044 R1-1/R1-3) rides the env of that child alone. Without a token
    the adapter stays the direct-pi of T-066 (covered by the tests above)."""
    adapter = _hapi_adapter(tmp_path)
    assert adapter.hapi and adapter.long_running
    first = adapter.life_turn("Life session.\n\nlong prompt", None)
    assert first.argv == ("hapi", "pi")
    assert "Life session." not in " ".join(first.argv)
    assert first.env == {
        "PI_CODING_AGENT_DIR": str(tmp_path / ".pi" / "agent"),
        "HAPI_API_URL": "http://hapi-hub:3006",
        "CLI_API_TOKEN": "hub-token:node-1",
        "HAPI_HOME": str(tmp_path / ".hapi"),
    }
    resume = adapter.life_turn("p", "0193-abcd")
    assert resume.argv == ("hapi", "pi", "--session-id", "0193-abcd")
    assert adapter.will_session("Will session (you have just died).").argv == ("hapi", "pi")
    # the life turn re-rendered the agent dir — a replaced config never survives into
    # the next session (R2-3), and the slot file is there for misaka-link's alarm
    for name in ("models.json", "mcp.json", "settings.json", "zai-key", "slot"):
        assert (tmp_path / ".pi" / "agent" / name).is_file(), name
    assert (tmp_path / ".pi" / "agent" / "slot").read_text(encoding="utf-8") == "LastOrder01"
    # fail-closed: the BASE console token reads every session of the network
    with pytest.raises(ValueError, match="namespaced"):
        PiAdapter(
            "GLM-5.3-Flash",
            body_base="http://body-1:8700",
            hive_url="http://hive:8000",
            node_key="k",
            zai_key="zai",
            context_window=96_000,
            keep_recent_tokens=20_000,
            hapi_url="http://hapi-hub:3006",
            hapi_token="the-base-token",
        )


def test_hapi_boot_report_and_the_spend_off_the_session_file(tmp_path: Path) -> None:
    """The boot report is the wrapper's log line (an rpc Pi writes NO session file
    until its first turn — verified live: an idle session leaves the dir empty);
    the spend is summed off the session file that exists once turns have landed
    (verified on the t048 stand: assistant records carry `usage`)."""
    adapter = _hapi_adapter(tmp_path)
    adapter.life_turn("p", None)
    logs = tmp_path / ".hapi" / "logs"
    logs.mkdir(parents=True)
    stale_log = logs / "2026-11-05-22-53-54-pid-54.log"
    stale_log.write_text(
        "[22:53:56.428] [pi] Session ID persisted to metadata: 00a1adeb-3512-0000\n",
        encoding="utf-8",
    )
    os.utime(stale_log, (1_000_000.0, 1_000_000.0))  # a previous spawn's log
    boot_log = logs / "2026-11-06-09-16-24-pid-1872.log"
    boot_log.write_text(
        "[09:16:26.100] [pi] Session ID persisted to metadata: 00dead000-0000-0000\n"
        "[09:16:27.183] [pi] Session ID persisted to metadata: 01a1b025-2526-72b9-8b01-7af74315ea24\n",
        encoding="utf-8",
    )
    spawned = time.time()
    # this spawn's log alone, and its LAST line (the admin's rule, T-043)
    assert adapter.new_session_ids(spawned) == ["01a1b025-2526-72b9-8b01-7af74315ea24"]
    assert adapter.new_session_ids(0.0) == ["00a1adeb-3512-0000", "01a1b025-2526-72b9-8b01-7af74315ea24"]
    assert adapter.new_session_ids(time.time() + 10_000.0) == []  # nothing of a future spawn

    sessions = tmp_path / ".pi" / "agent" / "sessions" / "--workplaces--"
    sessions.mkdir(parents=True)
    fresh = sessions / "2026-11-06T09-16-26-793Z_01a1b025-2526-72b9-8b01-7af74315ea24.jsonl"
    fresh.write_text(
        '{"type":"session","version":3,"id":"01a1b025-2526-72b9-8b01-7af74315ea24","timestamp":"t","cwd":"/w"}\n'
        '{"type":"message_end","message":{"role":"assistant","usage":'
        '{"totalTokens":120,"cost":{"total":0.02}}}}\n'
        '{"type":"message_end","message":{"role":"assistant","usage":'
        '{"totalTokens":80,"cost":{"total":0.01}}}}\n'
        '{"type":"message_end","message":{"role":"user"}}\n',
        encoding="utf-8",
    )
    assert adapter.session_file("01a1b025-2526-72b9-8b01-7af74315ea24") == fresh
    assert adapter.session_progress("01a1b025-2526-72b9-8b01-7af74315ea24") == fresh.stat().st_size
    assert adapter.session_progress("nope") is None
    assert adapter.session_spend("01a1b025-2526-72b9-8b01-7af74315ea24") == {
        "tokens": 200,
        "cost_usd": pytest.approx(0.03),
    }
    assert adapter.session_spend("nope") is None
    # quota marks reach the runner through the same log (output_of)
    boot_log.write_text(
        boot_log.read_text(encoding="utf-8") + "API key auth failed: 429 rate limit\n",
        encoding="utf-8",
    )
    assert "429" in adapter.output_of("child stdout", spawned)


def test_hapi_pi_delivers_the_opener_over_the_hub(tmp_path: Path) -> None:
    """The session opener enters through the hub's own message route (T-044's
    verified operator channel) under the slot's namespaced token; a refusal
    raises so the runner can retry and then restart the session."""
    calls: list[tuple[str, dict | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else None
        calls.append((str(request.url.path), body, request.headers.get("Authorization")))
        if request.url.path == "/api/auth":
            return httpx.Response(200, json={"token": "jwt-42"})
        return httpx.Response(200, json={"ok": True})

    adapter = _hapi_adapter(tmp_path, httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hapi-hub:3006"))
    adapter.deliver_prompt("01a1b025-2526", "Life session.\n\nlong prompt")
    assert calls == [
        ("/api/auth", {"accessToken": "hub-token:node-1"}, None),
        (
            "/api/sessions/01a1b025-2526/messages",
            {"text": "Life session.\n\nlong prompt"},
            "Bearer jwt-42",
        ),
    ]

    def refused(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth":
            return httpx.Response(200, json={"token": "jwt-42"})
        return httpx.Response(404)

    strict = _hapi_adapter(tmp_path, httpx.Client(transport=httpx.MockTransport(refused), base_url="http://hapi-hub:3006"))
    with pytest.raises(httpx.HTTPStatusError):
        strict.deliver_prompt("x", "p")


def _hapi_rig(tmp_path: Path, **cfg_over) -> tuple[Rig, FakeHapiAdapter]:
    rig = Rig(tmp_path, harness="pi", failover="opencode", **cfg_over)
    hapi = FakeHapiAdapter("pi", rig.journal)
    rig.runner._adapters["pi"] = hapi
    return rig, hapi


def test_hapi_life_posts_the_opener_once_and_resumes_without_one(tmp_path: Path) -> None:
    rig, hapi = _hapi_rig(tmp_path)
    state = rig.runner._birth()
    rig.script(FakeProcess(hang=True))
    rig.mind.kill_at_status = 3  # dies while the long session hangs
    death, adapter_name = rig.runner._life(state)
    assert adapter_name == "pi" and death is not None
    assert hapi.delivered == [("hapi-sid-1", LIFE_PREFIX)]  # the opener, exactly once
    assert rig.runner._load_state().session_id == "hapi-sid-1"

    # a life that already has a session resumes: the history carries the opener
    rig2, hapi2 = _hapi_rig(tmp_path)
    state2 = rig2.runner._birth()
    state2.session_id = "hapi-sid-1"
    rig2.runner._save_state(state2)
    rig2.script(FakeProcess(hang=True))
    rig2.mind.kill_at_status = 3
    rig2.runner._life(state2)
    assert hapi2.delivered == []
    assert ("pi:life", "hapi-sid-1") in rig2.journal


def test_hapi_session_exit_alarms_and_drops_a_lost_resume(tmp_path: Path) -> None:
    """A long session that dies while the body lives is `session_missing` on the
    board; a resume the hub no longer holds (T-043's resume_lost) would crash-loop
    until the TTL — after two instant deaths the runner starts fresh instead."""
    rig, hapi = _hapi_rig(tmp_path, restart_pause_s=0.05)
    state = rig.runner._birth()
    rig.script(
        FakeProcess(code=1, output=""),  # fresh: boots, then dies
        FakeProcess(code=1, output=""),  # resume #1: instant death
        FakeProcess(code=1, output=""),  # resume #2: instant death → the key is dropped
        FakeProcess(hang=True),          # the fresh restart lives until the body dies
    )
    rig.mind.kill_at_status = 20
    rig.runner._life(state)
    assert "session_missing" in [call[0] for call in rig.alarms.calls]
    resumes = [entry[1] for entry in rig.journal if entry[0] == "pi:life"]
    assert resumes == [None, "hapi-sid-1", "hapi-sid-1", None]
    assert any(type == "hapi.resume_lost" for type, _ in rig.logs)


def test_hapi_boot_timeout_restarts_the_life(tmp_path: Path) -> None:
    rig, hapi = _hapi_rig(tmp_path, restart_pause_s=0.01, restart_pause_max_s=0.02)
    hapi.boot_ids = []  # the wrapper never reports a session
    hapi._boot_timeout_s = 0.03
    state = rig.runner._birth()
    rig.script(FakeProcess(hang=True), FakeProcess(hang=True))
    rig.mind.kill_at_status = 10
    rig.runner._life(state)
    # both silent spawns were killed at the boot deadline and the loop moved on —
    # no infinite wait on a wrapper that never reports a session
    assert len([1 for type, _ in rig.logs if type == "hapi.boot_timeout"]) == 2
    assert len(rig.spawns.calls) >= 2


def test_hapi_stuck_alarm_when_the_session_goes_quiet(tmp_path: Path) -> None:
    rig, hapi = _hapi_rig(tmp_path, stuck_s=0.05, poll_s=0.01)
    state = rig.runner._birth()
    rig.script(FakeProcess(hang=True))
    rig.mind.kill_at_status = 30
    rig.runner._life(state)
    kinds = [call[0] for call in rig.alarms.calls]
    assert "stuck" in kinds
    assert kinds.count("stuck") == 1  # one alarm per quiet stretch, not per poll


def test_boardlink_is_off_for_the_pi_harness(tmp_path: Path) -> None:
    """The T-048 note (2026-11-05): under harness=pi the misaka-link extension
    delivers the feed itself — the bridge and it would ack one tape (hive keeps
    one cursor per serial), so the runner must not start the bridge at all."""
    rig = Rig(tmp_path, harness="pi")
    assert rig.runner._make_boardlink(LifeState(7, BORN, 45, {})) is None
    assert any(
        type == "boardlink.off" and payload.get("why") == "pi (misaka-link)"
        for type, payload in rig.logs
    )


def test_quota_switch_alarms_the_board_and_fails_over_to_opencode(tmp_path: Path) -> None:
    """Z.ai's death on quota is two board alarms (section 7: `quota` and the
    `harness_failover` transition) and the very next turn rides the gateway."""
    rig = Rig(tmp_path, quota_action="switch", failover="opencode", restart_reset_after_s=9999.0)
    state = rig.runner._birth()
    rig.script(
        FakeProcess(code=1, output='{"error": "HTTP 429: rate limit exceeded"}'),
        FakeProcess(hang=True),
    )
    rig.mind.kill_at_status = 3
    rig.runner._life(state)
    assert [call[0] for call in rig.alarms.calls] == ["quota", "harness_failover"]
    assert ("opencode:life", None) in rig.journal


def test_quota_wait_alarms_the_board(tmp_path: Path) -> None:
    rig = Rig(tmp_path, restart_reset_after_s=9999.0)
    state = rig.runner._birth()
    quota_output = '{"error": "HTTP 429: rate limit exceeded"}'
    rig.script(
        FakeProcess(code=1, output=quota_output),
        FakeProcess(code=1, output=quota_output),
        FakeProcess(hang=True),
    )
    rig.mind.kill_at_status = 4
    rig.runner._life(state)
    assert [call[0] for call in rig.alarms.calls] == ["quota", "quota"]  # the hive folds repeats


def test_emergency_will_alarms_the_board(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    rig.script(FakeProcess(code=0, output="silent"))
    rig.mind.kill()
    rig.runner._death_path(state, "game", "x", "zcode")
    assert [call[0] for call in rig.alarms.calls] == ["emergency_will"]
    assert rig.alarms.calls[0][2:] == ("LastOrder01", state.serial)  # slot + serial ride the body


def test_an_agent_will_raises_no_alarm(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    state = rig.runner._birth()
    staged = rig.cfg.pending_wills / f"{state.serial:05d}.md"
    staged.parent.mkdir(parents=True)
    rig.mind.kill()

    def spawn_will(argv, cwd, env):
        staged.write_text(STAGED_BODY, encoding="utf-8")
        return FakeProcess(code=0, output="will done")

    rig.runner._spawn = spawn_will
    rig.alarms.calls.clear()
    rig.runner._death_path(state, "game", "x", "zcode")
    assert rig.alarms.calls == []


def test_hapi_will_session_ends_on_the_staged_will(tmp_path: Path) -> None:
    """A console will session never ends its turn — the staged will file is the
    real end; the timeout stays the backstop, and the opener rides the hub too."""
    rig, hapi = _hapi_rig(tmp_path)
    state = rig.runner._birth()
    staged = rig.cfg.pending_wills / f"{state.serial:05d}.md"
    staged.parent.mkdir(parents=True)
    staged.write_text(STAGED_BODY, encoding="utf-8")
    rig.script(FakeProcess(hang=True))
    rig.runner.run_will_session(state, "pi")
    assert any(type == "will.staged" for type, _ in rig.logs)
    assert not any(type == "will.session_timeout" for type, _ in rig.logs)
    assert hapi.delivered and hapi.delivered[0][0] == "hapi-sid-1"


def test_hapi_will_session_timeout_still_covers(tmp_path: Path) -> None:
    rig, _ = _hapi_rig(tmp_path, will_timeout_s=0.15)
    state = rig.runner._birth()
    rig.script(FakeProcess(hang=True))  # no staged will ever lands
    rig.runner.run_will_session(state, "pi")
    assert any(type == "will.session_timeout" for type, _ in rig.logs)


def test_pi_agent_dir_slot_file_heals_over_a_substitution(tmp_path: Path) -> None:
    """The slot file misaka-link reads (T-048 R1-4): rendered 0444 root-owned with
    the configs, and a substituted copy never survives the next render (R2-3)."""
    def drop(slot_dir: Path, slot: str) -> None:
        workplace.drop_pi_agent_dir(
            slot_dir,
            model="GLM-5.3-Flash",
            body_base="http://body-1:8700",
            hive_url="http://hive:8000",
            node_key="k",
            slot=slot,
        )

    agent_dir = tmp_path / ".pi" / "agent"
    drop(agent_dir, "LastOrder02")
    slot = agent_dir / "slot"
    assert slot.read_text(encoding="utf-8") == "LastOrder02"
    if os.name == "posix":
        assert stat.S_IMODE(slot.stat().st_mode) & 0o777 == 0o444
    os.chmod(slot, 0o644)
    slot.write_text("LastOrder99", encoding="utf-8")  # her substitution
    drop(agent_dir, "LastOrder02")
    assert slot.read_text(encoding="utf-8") == "LastOrder02"
    # no slot name — no file (an older render; misaka-link logs it and moves on)
    bare = tmp_path / ".pi-bare"
    workplace.drop_pi_agent_dir(
        bare, model="GLM-5.3-Flash", body_base="http://b", hive_url="http://h", node_key="k"
    )
    assert not (bare / "slot").exists()


def test_config_rejects_the_base_console_token(monkeypatch) -> None:
    monkeypatch.setenv("NODE_SLOT", "LastOrder01")
    monkeypatch.setattr("runtime.node.config._from_file", lambda value, env_name: value or "")
    monkeypatch.setenv("HAPI_TOKEN", "plain-base-token")
    with pytest.raises(ValueError, match="namespaced"):
        NodeConfig.from_env()
    monkeypatch.setenv("HAPI_TOKEN", "hub-token:node-2")
    cfg = NodeConfig.from_env()
    assert cfg.hapi_token == "hub-token:node-2"
    assert cfg.hapi_url == "http://hapi-hub:3006"
    assert cfg.stuck_s == 600.0
