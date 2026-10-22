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
    HermesAdapter,
    OpenCodeAdapter,
    ZCodeAdapter,
    quota_exhausted,
)
from runtime.node.config import NodeConfig
from runtime.node.runner import NodeRunner
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
        gateway_url="http://llm-gateway:4000",
        gateway_key="gk-node",
        zcode_model="GLM-5.3-Flash",
        opencode_model="openrouter/qwen/qwen3.7-flash",
        hermes_model="GLM-5.3-Flash",
        naming="lab",
        will_timeout_s=0.2,
        poll_s=0.05,
        restart_pause_s=5.0,
        restart_pause_max_s=60.0,
        restart_reset_after_s=300.0,
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
            log=lambda type, payload=None: self.logs.append((type, payload or {})),
            sweep=lambda: self.journal.append(("sweep",)),
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
            will = Will.from_markdown(staged_markdown(state.serial))
            staged.write_text(will.to_markdown(), encoding="utf-8")
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
    assert "serial: 1" in text and "не ходить" in text  # the agent's will, verbatim
    # the workplace left for the archive, read-only; the heir's clock not yet armed
    archive = rig.cfg.workplaces / "archive" / f"{state.serial:05d}"
    assert archive.is_dir() and not (rig.cfg.workplaces / "agent" / f"{state.serial:05d}").exists()
    with pytest.raises(PermissionError):
        (archive / "GOALS.md").write_text("x", encoding="utf-8")
    assert rig.mind.respawn_calls == 1


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


def test_hermes_adapter_commands_and_session_id(tmp_path: Path) -> None:
    adapter = HermesAdapter("GLM-5.3-Flash", home=str(tmp_path / ".hermes"))
    first = adapter.life_turn("Life session.\n\nlong prompt", None)
    assert first.argv[:4] == ("hermes", "chat", "--query-file", first.argv[3])
    query = Path(first.argv[3])
    assert query.read_text(encoding="utf-8") == "Life session.\n\nlong prompt"
    tail = first.argv[4:]
    assert tail == ("--oneshot", "--format", "stream-json", "--yolo", "--toolsets", "file,terminal")
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
    assert config["platform_toolsets"] == {"cli": ["file", "terminal"]}
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
    # a config hermes itself rewrote (real YAML, not the rendered JSON) survives the surgery
    (home / "config.yaml").write_text(
        "model:\n  default: glm-5.3-flash\nmcp_servers:\n  hive:\n    url: http://hive:8000/node/mcp\n",
        encoding="utf-8",
    )
    workplace.set_will_mode(tmp_path, "hermes", "http://127.0.0.1:8702")
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["mcp_servers"]["hive"]["url"] == "http://127.0.0.1:8702/mcp"


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
    body = "Урок: не ходить в горы ночью.\nШутка: жадность — наследство, я не просил."
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)} for {k}, {v}" for k, v in []] + [
        f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()
    ] + ["---", "", body]
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
        client=httpx.Client(transport=httpx.MockTransport(handler)),
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
