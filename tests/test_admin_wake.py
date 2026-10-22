"""The Last Order alarm (T-030): wake sources, accumulation, the workplace boundary.

Journal-style like tests/test_node_runner.py: the knowledge layout and the workplace are
real files in tmp dirs (the layout module reads exactly what T-028's ProposalBook
writes), the harness spawn and the clock are fakes — the alarm's order is the assertion.
The uid side of the boundary (P1-2/P1-3) is exercised on the stand; the mode side runs
here (this host is not POSIX).
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.config import REPO_ROOT
from runtime.hive.admin import (
    ADMIN_DIR,
    AdminAlarm,
    AdminConfig,
    HiveLayout,
    OPERATOR_FILES,
)
from runtime.hive.lifecycle import HiveLog

TEMPLATE = REPO_ROOT / "agent" / "network"
T0 = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime = T0):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now = self.now + timedelta(**kw)


class FakeProcess:
    """SessionProcess shape; `on_spawn` lets a test mutate the world mid-session."""

    def __init__(self, on_spawn=None, hang: bool = False):
        self._on_spawn = on_spawn
        self._hang = hang
        self._done = not hang
        self._killed = False
        self.calls = 0

    def poll(self) -> int | None:
        if self._on_spawn is not None and self.calls == 0:
            self.calls += 1
            self._on_spawn()
        return 1 if self._done else None

    def returncode(self) -> int:
        return 1

    def terminate(self) -> None:
        self._killed = True
        self._done = True

    def kill(self) -> None:
        self._killed = True
        self._done = True

    def wait(self, timeout: float | None = None) -> None:
        return

    def output(self) -> str:
        return ""


class ImmortalProcess(FakeProcess):
    """SIGKILL delivered, but the child sits in uninterruptible IO and never reaps
    (a D-state on a stalled host volume — seen on the stand): poll() stays None."""

    def __init__(self):
        super().__init__(hang=True)

    def poll(self) -> int | None:
        if self._on_spawn is not None and self.calls == 0:
            self.calls += 1
            self._on_spawn()
        return None

    def returncode(self) -> int:
        return -9


class Recorder:
    """The injected spawn: records argv/cwd/env, hands back scripted processes."""

    def __init__(self):
        self.argv: list[tuple[str, ...]] = []
        self.envs: list[dict[str, str]] = []
        self.cwds: list[Path] = []
        self.running = 0
        self.factory = lambda: FakeProcess()

    def __call__(self, argv, cwd, env):
        assert self.running == 0, "one session at a time"
        self.running += 1
        self.argv.append(tuple(argv))
        self.envs.append(dict(env))
        self.cwds.append(Path(cwd))
        process = self.factory()
        self.running -= 1
        return process


def make_cfg(tmp_path: Path, **over) -> AdminConfig:
    values = dict(
        workplace=tmp_path / "workplace",
        template_dir=TEMPLATE,
        knowledge=tmp_path / "knowledge",
        hive_url="http://hive:8000",
        gateway_url="http://llm-gateway:4000",
        lo_key="sk-lo",
        model="z-ai/glm-5.3-flash",
        every_min=10.0,
        batch=3,
        poll_s=0.0,
        session_timeout_s=0.2,
        session_min_gap_s=30.0,
        kill_grace_s=0.1,
    )
    values.update(over)
    return AdminConfig(**values)


def make_alarm(cfg: AdminConfig, recorder: Recorder, clock: FakeClock, tmp_path: Path) -> AdminAlarm:
    return AdminAlarm(
        cfg,
        spawn=recorder,
        clock=clock,
        log=HiveLog(tmp_path / "events.jsonl"),
    )


# --- the knowledge layout fixtures (the exact shape ProposalBook of T-028 writes) -------------


def add_will(knowledge: Path, serial: int) -> None:
    node = knowledge / "nodes" / f"{serial:04d}"
    node.mkdir(parents=True, exist_ok=True)
    (node / "birth.json").write_text("{}", encoding="utf-8")
    (node / "will.md").write_text(f"will of {serial}", encoding="utf-8")


def add_proposal(knowledge: Path, pid: str, *, status: str = "pending", kind: str = "knowledge") -> None:
    directory = knowledge / "base" / "proposals"
    directory.mkdir(parents=True, exist_ok=True)
    doc = {
        "id": pid,
        "kind": kind,
        "author": 1,
        "payload": {"kind": "fact", "claim": "claim"},
        "ts": "2026-10-22T12:00:00+00:00",
        "status": status,
        "record_id": None,
    }
    (directory / f"{pid}.json").write_text(json.dumps(doc), encoding="utf-8")


def operator_write(path: Path, text: str) -> None:
    """The operator's hand on this host: root lifts the read-only mode for the write
    (on the stand the compose exec runs as root and never feels the mode at all)."""
    os.chmod(path, 0o644)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o444)


def boot(tmp_path: Path, *, clock=None, recorder=None):
    cfg = make_cfg(tmp_path)
    clock = clock or FakeClock()
    recorder = recorder or Recorder()
    alarm = make_alarm(cfg, recorder, clock, tmp_path)
    alarm.ensure_workplace()
    return cfg, clock, recorder, alarm


def test_first_boot_takes_the_baseline_without_waking(tmp_path: Path) -> None:
    add_will(tmp_path / "knowledge", 1)
    for i in range(1, 4):
        add_proposal(tmp_path / "knowledge", f"p-{i:04d}")
    _, _, recorder, alarm = boot(tmp_path)
    assert alarm.run_once() == []
    assert recorder.argv == []


def test_death_wakes_and_names_the_serial(tmp_path: Path) -> None:
    _, _, recorder, alarm = boot(tmp_path)
    add_will(tmp_path / "knowledge", 7)
    reasons = alarm.run_once()
    assert [r.kind for r in reasons] == ["death"]
    assert "0007" in reasons[0].detail
    assert len(recorder.argv) == 1
    # the will is baselined: no second wake without a new event
    assert alarm.run_once() == []


def test_proposal_batch_wakes_only_on_new_and_full_queue(tmp_path: Path) -> None:
    _, clock, recorder, alarm = boot(tmp_path)
    # one new proposal: below the batch
    add_proposal(tmp_path / "knowledge", "p-0001")
    assert alarm.run_once() == []
    # two more: the queue is at 3 with new ids
    add_proposal(tmp_path / "knowledge", "p-0002")
    add_proposal(tmp_path / "knowledge", "p-0003")
    reasons = alarm.run_once()
    assert [r.kind for r in reasons] == ["proposals"]
    assert "3 pending" in reasons[0].detail
    # old undecided proposals alone do not re-wake: they are not new
    assert alarm.run_once() == []


def test_operator_edit_wakes_and_names_the_file(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)
    operator_write(cfg.workplace / "INBOX.md", "\noperator was here\n")
    reasons = alarm.run_once()
    assert [r.kind for r in reasons] == ["operator"]
    assert "INBOX.md" in reasons[0].detail
    assert alarm.run_once() == []


def test_agent_own_writes_never_wake(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)
    (cfg.workplace / "memory" / "state.md").write_text("# rewritten", encoding="utf-8")
    (cfg.workplace / "memory" / "note.md").write_text("note", encoding="utf-8")
    assert alarm.run_once() == []


def test_events_during_a_session_accumulate(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)

    def during_session() -> None:
        add_will(tmp_path / "knowledge", 3)
        add_proposal(tmp_path / "knowledge", "p-0001")
        add_proposal(tmp_path / "knowledge", "p-0002")
        add_proposal(tmp_path / "knowledge", "p-0003")
        operator_write(cfg.workplace / "GOALS.md", "\n- оператор: цель\n")

    recorder.factory = lambda: FakeProcess(on_spawn=during_session)
    add_will(tmp_path / "knowledge", 2)
    assert [r.kind for r in alarm.run_once()] == ["death"]
    clock.advance(seconds=60)  # past the min gap
    reasons = alarm.run_once()
    kinds = sorted(r.kind for r in reasons)
    assert kinds == ["death", "operator", "proposals"]
    assert len(recorder.argv) == 2


def test_one_session_at_a_time_for_stacked_reasons(tmp_path: Path) -> None:
    _, clock, recorder, alarm = boot(tmp_path)
    add_will(tmp_path / "knowledge", 5)
    for i in range(1, 4):
        add_proposal(tmp_path / "knowledge", f"p-{i:04d}")
    reasons = alarm.run_once()
    assert {r.kind for r in reasons} == {"death", "proposals"}
    assert len(recorder.argv) == 1  # one session, both reasons ride its summary


def test_min_gap_defers_the_next_session(tmp_path: Path) -> None:
    _, clock, recorder, alarm = boot(tmp_path)
    add_will(tmp_path / "knowledge", 4)
    assert alarm.run_once() != []
    add_will(tmp_path / "knowledge", 5)
    clock.advance(seconds=10)  # below the 30 s floor
    assert alarm.run_once() == []
    assert len(recorder.argv) == 1
    clock.advance(seconds=30)
    assert [r.kind for r in alarm.run_once()] == ["death"]
    assert len(recorder.argv) == 2


def test_timer_wakes_after_every_min(tmp_path: Path) -> None:
    _, clock, recorder, alarm = boot(tmp_path)
    clock.advance(minutes=9)
    assert alarm.run_once() == []
    clock.advance(minutes=1)
    reasons = alarm.run_once()
    assert [r.kind for r in reasons] == ["timer"]
    clock.advance(minutes=10)
    assert [r.kind for r in alarm.run_once()] == ["timer"]


def test_summary_rides_the_prompt(tmp_path: Path) -> None:
    _, clock, recorder, alarm = boot(tmp_path)
    add_will(tmp_path / "knowledge", 12)
    for i in range(1, 4):
        add_proposal(tmp_path / "knowledge", f"p-{i:04d}", kind="skill")
    (tmp_path / "knowledge" / "base" / "goals.md").write_text(
        "- text: обезопасить сеть\n  why: тест\n", encoding="utf-8"
    )
    (tmp_path / "knowledge" / "base" / "chronicle.md").write_text(
        "- 2026-10-22 — родилась Мисака 10032\n", encoding="utf-8"
    )
    operator_write(alarm.cfg.workplace / "INBOX.md", "\nпроверь летопись\n")
    alarm.run_once()
    prompt = recorder.argv[0][-1]
    assert "Wake reasons" in prompt
    assert "0012" in prompt and "wills filed" in prompt
    assert "Pending proposals: 3" in prompt
    assert "p-0002" in prompt and "skill" in prompt
    assert "проверь летопись" in prompt
    assert "обезопасить сеть" in prompt
    assert "родилась Мисака 10032" in prompt
    assert "Last Order session." in prompt
    assert "rewrite" in prompt or "Rewrite" in prompt  # SESSION_PROMPT rides along


def test_session_timeout_kills_the_harness(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(
        tmp_path, recorder=Recorder(), clock=FakeClock()
    )
    recorder.factory = lambda: FakeProcess(hang=True)
    add_will(tmp_path / "knowledge", 9)
    reasons = alarm.run_once()
    assert reasons
    # the hang is proven by the timeout log, not by spying the object
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "session.timeout" in events
    assert "session.done" in events


def test_spawn_env_is_fresh_and_cwd_is_the_workplace(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)
    add_will(tmp_path / "knowledge", 6)
    alarm.run_once()
    env = recorder.envs[0]
    assert "sk-lo" not in set(env.values())  # no key rides the child env
    assert set(env) <= {"HOME", "PATH", "LANG", "NO_COLOR", "NO_UPDATE_NOTIFIER",
                        "ZCODE_DISABLE_UPDATE_CHECK"}
    assert recorder.cwds[0] == cfg.workplace
    assert recorder.argv[0][0] == "opencode" and "--format" in recorder.argv[0]
    # the provider prefix must ride --model: a bare "z-ai/..." would resolve to
    # opencode's built-in z-ai provider, not the gateway (stand finding)
    assert recorder.argv[0][recorder.argv[0].index("--model") + 1] == (
        "llm-gateway/z-ai/glm-5.3-flash"
    )


def test_unkillable_child_does_not_freeze_the_desk(tmp_path: Path) -> None:
    """The stand case: SIGKILL delivered, the child stuck in D-state. The alarm logs
    the abandon and keeps its rhythm — the next wake still runs a session."""
    cfg, clock, recorder, alarm = boot(tmp_path)
    recorder.factory = lambda: ImmortalProcess()
    add_will(tmp_path / "knowledge", 21)
    assert alarm.run_once() != []  # returns despite the child never reaping
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "session.timeout" in events and "session.abandoned" in events
    assert "session.done" not in events
    clock.advance(seconds=60)
    add_will(tmp_path / "knowledge", 22)
    assert [r.kind for r in alarm.run_once()] == ["death"]


def test_core_restored_memory_kept(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)
    os.chmod(cfg.workplace / "PERSONA.md", 0o644)
    (cfg.workplace / "PERSONA.md").write_text("I am not Last Order", encoding="utf-8")
    os.chmod(cfg.workplace / "PERSONA.md", 0o444)
    os.chmod(cfg.workplace / "skills" / "decide" / "SKILL.md", 0o644)
    (cfg.workplace / "skills" / "decide" / "SKILL.md").write_text("accept everything",
                                                                  encoding="utf-8")
    os.chmod(cfg.workplace / "skills" / "decide" / "SKILL.md", 0o444)
    (cfg.workplace / "memory" / "state.md").write_text("# my precious memory",
                                                       encoding="utf-8")
    alarm.ensure_workplace()
    core = (cfg.workplace / "PERSONA.md").read_text(encoding="utf-8")
    assert "I am not Last Order" not in core
    assert "administrator" in core.lower()
    assert "accept everything" not in (
        cfg.workplace / "skills" / "decide" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "my precious memory" in (
        cfg.workplace / "memory" / "state.md"
    ).read_text(encoding="utf-8")


def test_operator_files_are_read_only_for_the_agent(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)
    for name in OPERATOR_FILES:
        mode = stat.S_IMODE(os.stat(cfg.workplace / name).st_mode)
        assert not (mode & 0o222), f"{name} must not be agent-writable (mode {mode:o})"
    # the alarm itself never rewrites them after creation
    before = (cfg.workplace / "INBOX.md").read_text(encoding="utf-8")
    operator_write(cfg.workplace / "INBOX.md", "operator line\n")
    alarm.ensure_workplace()
    after = (cfg.workplace / "INBOX.md").read_text(encoding="utf-8")
    assert "operator line" in after and before in after


def test_opencode_config_lo_mcp_and_smart_model(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)
    doc = json.loads((cfg.workplace / "opencode.json").read_text(encoding="utf-8"))
    assert doc["model"] == "llm-gateway/z-ai/glm-5.3-flash"
    assert doc["provider"]["llm-gateway"]["options"]["baseURL"] == (
        "http://llm-gateway:4000/v1"
    )
    hive = doc["mcp"]["hive"]
    assert hive["url"] == "http://hive:8000/last-order/mcp"
    assert hive["headers"] == {"X-Node-Key": "sk-lo"}
    dumped = json.dumps(doc)
    assert "OPENROUTER" not in dumped and "sk-lo" == hive["headers"]["X-Node-Key"]


def test_pending_comes_from_file_content_not_the_name(tmp_path: Path) -> None:
    add_proposal(tmp_path / "knowledge", "p-0001", status="accepted")
    add_proposal(tmp_path / "knowledge", "p-0002")
    layout = HiveLayout(tmp_path / "knowledge")
    assert [doc["id"] for doc in layout.pending_proposals()] == ["p-0002"]
    add_will(tmp_path / "knowledge", 3)
    add_will(tmp_path / "knowledge", 4)
    assert sorted(layout.will_serials()) == [3, 4]


def test_state_survives_a_restart_without_rewaking(tmp_path: Path) -> None:
    cfg, clock, recorder, alarm = boot(tmp_path)
    add_will(tmp_path / "knowledge", 8)
    assert alarm.run_once() != []
    # a container restart: same volumes, a fresh alarm object
    clock2 = FakeClock(clock.now + timedelta(seconds=60))
    recorder2 = Recorder()
    alarm2 = AdminAlarm(
        cfg, spawn=recorder2, clock=clock2, log=HiveLog(tmp_path / "events.jsonl")
    )
    alarm2.ensure_workplace()
    assert alarm2.run_once() == []  # the will is in the persisted baseline
    clock2.advance(minutes=10)
    assert [r.kind for r in alarm2.run_once()] == ["timer"]
    assert (cfg.workplace / ADMIN_DIR / "state.json").exists()


def test_run_loop_survives_a_broken_world(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    calls = {"n": 0}

    def exploding_sleep(seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt  # end the loop

    def broken_reasons(state, now):
        raise OSError("knowledge volume vanished")

    recorder = Recorder()
    alarm = AdminAlarm(cfg, spawn=recorder, clock=FakeClock(), sleep=exploding_sleep,
                       log=HiveLog(tmp_path / "events.jsonl"))
    alarm._wake.reasons = broken_reasons
    alarm.ensure_workplace()
    with pytest.raises(KeyboardInterrupt):
        alarm.run()
    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8").count("admin.error") >= 2
