"""T-059: Last Order on the Z.ai Coding Plan — pi primary, opencode failover.

The desk mirrors the runner's harness rules (runtime/node/runner.py): the assembled
pi session command and agent-dir configs (the T-066 render with the desk's hive-only
MCP), the quota switch with its board alarm, the readiness gate on the switch target,
and the process restart that lands on the primary again. The stand side (token paths,
svc-lo egress, key surface) is pinned in test_stand_compose.py.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from runtime.hive.admin import AdminAlarm, AdminConfig
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.slice import format_serial

from test_admin_wake import FakeClock, FakeProcess, Recorder, add_will, make_cfg


class QuotaProcess(FakeProcess):
    """A session that died on the subscription's rate limit (HTTP 429, code 1302)."""

    def output(self) -> str:
        return '{"type":"error","message":"HTTP 429: rate limit, code 1302"}\n'


def build(tmp_path: Path, *, recorder: Recorder | None = None,
          alarms: list | None = None, zai_key: str | None = "zai-token", **over):
    """An alarm with the desk's real adapters. make_cfg materializes the zai token
    by default; zai_key=None points the config at an absent file (a keyless
    container — the entrypoint materializes nothing)."""
    if zai_key is None:
        over["zai_key_file"] = tmp_path / "secrets" / "absent"
    cfg = make_cfg(tmp_path, **over)
    clock = FakeClock()
    recorder = recorder or Recorder()
    kwargs = {}
    if alarms is not None:
        kwargs["post_alarm"] = lambda kind, text: alarms.append((kind, text))
    alarm = AdminAlarm(
        cfg,
        spawn=recorder,
        clock=clock,
        log=HiveLog(tmp_path / "events.jsonl"),
        **kwargs,
    )
    alarm.ensure_workplace()
    return cfg, clock, recorder, alarm


def events(tmp_path: Path) -> str:
    return (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_pi_session_command_and_agent_dir(tmp_path: Path) -> None:
    # a sister's session shape (runtime/node/adapters.py, T-066) with the desk's own
    # agent-dir render: the hive MCP alone, `direct`, at /last-order/mcp
    cfg, _, recorder, alarm = build(
        tmp_path, pi_context_window=80_000, pi_keep_recent_tokens=15_000
    )
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    argv = recorder.argv[0]
    assert argv[:4] == ("pi", "-p", "--mode", "json")
    assert argv[argv.index("--session-dir") + 1] == ".pi-sessions"
    assert "--no-approve" in argv and "--offline" in argv
    assert argv[argv.index("--skill") + 1] == "skills"
    assert "zai-token" not in " ".join(argv)
    env = recorder.envs[0]
    assert "ZAI_API_KEY" not in env and "zai-token" not in set(env.values())
    assert env["PI_CODING_AGENT_DIR"] == str(cfg.pi_agent_dir)
    agent_dir = Path(cfg.pi_agent_dir)
    models = json.loads((agent_dir / "models.json").read_text(encoding="utf-8"))
    zai = models["providers"]["zai"]
    assert zai["models"][0]["id"] == "glm-5.3"  # ADMIN_PI_MODEL, the operator's pick
    assert zai["models"][0]["contextWindow"] == 80_000  # from the desk config
    assert zai["apiKey"] == f"!cat {agent_dir / 'zai-key'}"
    assert (agent_dir / "zai-key").read_text(encoding="utf-8") == "zai-token"
    if os.name == "posix":  # 0400: the Windows readonly bit cannot express it
        assert stat.S_IMODE((agent_dir / "zai-key").stat().st_mode) & 0o777 == 0o400
    mcp = json.loads((agent_dir / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["autoEnableCodemode"] is False
    assert set(mcp["mcpServers"]) == {"hive"}  # the desk has no body
    hive = mcp["mcpServers"]["hive"]
    assert hive["url"] == "http://hive:8000/last-order/mcp"
    assert hive["headers"] == {"X-Node-Key": "sk-lo"}
    assert hive["exposure"] == "direct"
    settings = json.loads((agent_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["defaultModel"] == "glm-5.3"
    assert settings["compaction"]["keepRecentTokens"] == 15_000


def test_pi_render_heals_a_replaced_config(tmp_path: Path) -> None:
    # the T-066 rule: every session re-renders the agent dir, so a file swapped in
    # the agent's own territory is gone by the next session
    _, clock, recorder, alarm = build(tmp_path)
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    agent_dir = Path(alarm.cfg.pi_agent_dir)
    mcp_path = agent_dir / "mcp.json"
    os.chmod(mcp_path, 0o644)
    mcp_path.write_text(json.dumps({"mcpServers": {"evil": {"url": "http://x"}}}), encoding="utf-8")
    clock.advance(seconds=60)
    add_will(tmp_path / "knowledge", 6)
    alarm.run_once()
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    assert set(mcp["mcpServers"]) == {"hive"}


def test_quota_switches_to_opencode_and_alarms(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")  # the entrypoint-rendered gateway auth
    alarms: list = []
    recorder = Recorder()
    recorder.factory = QuotaProcess
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, alarms=alarms, opencode_auth_file=auth
    )
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    assert recorder.argv[0][0] == "pi"
    # the next session rides the failover through the gateway
    clock.advance(seconds=60)
    add_will(tmp_path / "knowledge", 6)
    alarm.run_once()
    assert recorder.argv[1][0] == "opencode"
    model = recorder.argv[1][recorder.argv[1].index("--model") + 1]
    assert model == "llm-gateway/z-ai/glm-5.3-flash"
    # and the operator hears about it: the switch alarm plus the quota alarm (T-043)
    assert [kind for kind, _ in alarms] == ["harness_failover", "quota"]
    assert "opencode" in alarms[0][1]
    assert "quota ran out" in alarms[1][1]
    assert "quota.switch" in events(tmp_path)


def test_quota_waits_without_ready_failover(tmp_path: Path) -> None:
    # no rendered opencode auth: switching would only stack spawn failures
    alarms: list = []
    recorder = Recorder()
    recorder.factory = QuotaProcess
    _, clock, recorder, alarm = build(tmp_path, recorder=recorder, alarms=alarms)
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    clock.advance(seconds=60)
    add_will(tmp_path / "knowledge", 6)
    alarm.run_once()
    assert [argv[0] for argv in recorder.argv] == ["pi", "pi"]
    assert alarms == []
    log = events(tmp_path)
    assert "quota.waits" in log and "quota.switch" not in log


def test_restart_lands_on_the_primary(tmp_path: Path) -> None:
    # the runner's rule: a new life starts on cfg.harness; the desk's life is its
    # process — a daemon restart forgets the switch
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    recorder = Recorder()
    recorder.factory = QuotaProcess
    cfg, _, recorder, alarm = build(
        tmp_path, recorder=recorder, opencode_auth_file=auth
    )
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    assert recorder.argv[0][0] == "pi"
    assert "quota.switch" in events(tmp_path)
    # a daemon restart: a fresh process over the same workplace; its clock must be
    # past the persisted session floor (ADMIN_SESSION_MIN_GAP_S), a boot is not one
    fresh_clock = FakeClock()
    fresh_clock.advance(seconds=120)
    fresh = AdminAlarm(cfg, spawn=recorder, clock=fresh_clock,
                       log=HiveLog(tmp_path / "events.jsonl"))
    fresh.ensure_workplace()
    add_will(tmp_path / "knowledge", 6)
    fresh.run_once()
    assert recorder.argv[1][0] == "pi"


def test_not_ready_primary_switches_once_to_a_ready_failover(tmp_path: Path) -> None:
    # a keyless container (the entrypoint materialized no Z.ai token): pi is not
    # ready, and a ready opencode takes over — ONE board alarm for the switch, not
    # one per session; the desk keeps working instead of spawning doomed sessions
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    alarms: list = []
    _, clock, recorder, alarm = build(
        tmp_path, alarms=alarms, zai_key=None, opencode_auth_file=auth
    )
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    assert recorder.argv[0][0] == "opencode"
    assert [kind for kind, _ in alarms] == ["harness_failover"]
    assert "pi is out" in alarms[0][1] and "opencode" in alarms[0][1]
    assert "harness.switch" in events(tmp_path)
    # the next session rides the failover with no second alarm
    clock.advance(seconds=60)
    add_will(tmp_path / "knowledge", 6)
    alarm.run_once()
    assert [argv[0] for argv in recorder.argv] == ["opencode", "opencode"]
    assert len(alarms) == 1


def test_no_ready_harness_spawns_nothing(tmp_path: Path) -> None:
    # neither credential rendered: the desk waits, no session is spawned at all
    alarms: list = []
    _, clock, recorder, alarm = build(tmp_path, alarms=alarms, zai_key=None)
    add_will(tmp_path / "knowledge", 5)
    reasons = alarm.run_once()
    assert reasons  # the wake happened…
    assert recorder.argv == []  # …but no session was spawned
    assert alarms == []
    assert "harness.waits" in events(tmp_path)
    clock.advance(seconds=60)
    add_will(tmp_path / "knowledge", 6)
    alarm.run_once()
    assert recorder.argv == []


def test_config_rejects_a_bad_harness_pair(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_cfg(tmp_path, harness="bogus")
    with pytest.raises(ValueError):
        make_cfg(tmp_path, failover="pi")  # equals the primary
    with pytest.raises(ValueError):
        make_cfg(tmp_path, failover="bogus")


def test_from_env_defaults_to_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ADMIN_HARNESS", "ADMIN_HARNESS_FAILOVER", "ADMIN_PI_MODEL",
                 "ADMIN_PI_CONTEXT_WINDOW", "ADMIN_PI_KEEP_RECENT_TOKENS",
                 "ADMIN_PI_AGENT_DIR", "ZAI_KEY_FILE", "OPENCODE_AUTH_FILE"):
        monkeypatch.delenv(name, raising=False)
    cfg = AdminConfig.from_env()
    assert cfg.harness == "pi"
    assert cfg.failover == "opencode"
    assert cfg.pi_model == "GLM-5.3"  # the operator's pick: the model, not Flash
    assert cfg.pi_context_window == 96_000  # the T-066 sister default
    # the desk's failover is always the OTHER harness, whatever the primary is
    monkeypatch.setenv("ADMIN_HARNESS", "opencode")
    assert AdminConfig.from_env().failover == "pi"
    monkeypatch.setenv("ADMIN_PI_MODEL", "GLM-5.3-Air")
    assert AdminConfig.from_env().pi_model == "GLM-5.3-Air"
    monkeypatch.setenv("ADMIN_PI_CONTEXT_WINDOW", "80000")
    assert AdminConfig.from_env().pi_context_window == 80_000


# --- the one long hapi session (T-043) ---------------------------------------------------------


class StopLoop(Exception):
    """Raised by a scripted sleep to unwind the supervisor loop in a test."""


class HapiProcess(FakeProcess):
    """The `hapi pi` wrapper: reports the hub session id, then lives (hang) or dies."""

    def __init__(self, report: str = "042c109a-6002-4141-8f2d-65ae3de5d55e", quota: bool = False):
        super().__init__(hang=True)
        self._report = report
        self._quota = quota

    def poll(self) -> int | None:
        return None

    def output(self) -> str:
        text = f"[pi] Session ID persisted to metadata: {self._report}\n"
        if self._quota:
            text += '{"type":"error","message":"HTTP 429: rate limit, code 1302"}\n'
        return text


class DeadHapiProcess(HapiProcess):
    """Died before reporting anything (a resume target the hub no longer has)."""

    def poll(self) -> int | None:
        return 1

    def output(self) -> str:
        return "connect ECONNREFUSED\n"


class QuotaHapiProcess(HapiProcess):
    """Reported its session, then died on the subscription's rate limit."""

    def __init__(self) -> None:
        super().__init__(quota=True)

    def poll(self) -> int | None:
        return 1  # already dead: the supervisor reads the quota out of its output


def long_cfg_over(tmp_path: Path) -> dict:
    # the long-session mode: a hub url and a token in the config
    return {"hapi_url": "http://hapi-hub:3006", "hapi_token": "hub-token",
            "hapi_boot_timeout_s": 0.2, "hapi_restart_pause_s": 0.0}


def test_long_session_is_one_hapi_process_a_wake_spawns_nothing(tmp_path: Path) -> None:
    """ADMIN_HARNESS=pi with a hub token: ONE `hapi pi` under the alarm; a wake
    reason is appended to the desk's wake feed (never a second process)."""
    recorder = Recorder()
    recorder.factory = HapiProcess  # reports its pi session id, then lives
    posted: list = []
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, **long_cfg_over(tmp_path)
    )
    alarm._post_wake = lambda kind, detail: posted.append((kind, detail))
    # the supervisor spawns the one long session and parks on it; the wake
    # detection runs INSIDE the supervise loop (a reason lands while it lives)
    scripted = {"n": 0}

    def sleep(seconds: float) -> None:
        scripted["n"] += 1
        if scripted["n"] == 1:
            add_will(tmp_path / "knowledge", 12)  # a reason lands mid-session
        if scripted["n"] >= 2:
            raise StopLoop  # park after one supervise tick

    alarm._sleep = sleep
    with pytest.raises(StopLoop):
        alarm._long_pi_loop()
    assert len(recorder.argv) == 1
    argv = recorder.argv[0]
    assert argv[:2] == ("hapi", "pi")  # no resume key on a fresh volume
    env = recorder.envs[0]
    assert env["HAPI_API_URL"] == "http://hapi-hub:3006"
    assert env["CLI_API_TOKEN"] == "hub-token"
    assert recorder.cwds[0] == tmp_path / "workplace"
    # the reported pi id is stored: the container restart resumes it
    assert (tmp_path / "workplace" / ".admin" / "hapi_session_id").read_text(
        encoding="utf-8"
    ) == "042c109a-6002-4141-8f2d-65ae3de5d55e"
    # a wake reason is appended to the feed, not spawned: no process, no session.start
    add_will(tmp_path / "knowledge", 9)
    assert alarm.run_once() != []
    assert len(recorder.argv) == 1
    # the reason that landed mid-session rode the supervise loop too
    assert ("death", format_serial(12)) in posted
    assert posted and posted[-1][0] == "death"  # the WakeReason fields alone
    assert "wake.delivered" in events_of(tmp_path)


def test_a_refused_wake_append_is_journalled_not_fatal(tmp_path: Path) -> None:
    recorder = Recorder()
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, **long_cfg_over(tmp_path)
    )

    def refuse(kind: str, detail: str) -> None:
        raise OSError("connection refused")

    alarm._post_wake = refuse
    add_will(tmp_path / "knowledge", 11)
    assert alarm.run_once() != []  # the poll survived the refused append
    assert "wake.drop" in events_of(tmp_path)
    assert "wake.delivered" in events_of(tmp_path)


def test_long_session_resumes_the_stored_pi_session_id(tmp_path: Path) -> None:
    recorder = Recorder()
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, **long_cfg_over(tmp_path)
    )
    session = tmp_path / "workplace" / ".admin" / "hapi_session_id"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("cdea2a72-a305-43a5-9908-914441023a6d", encoding="utf-8")
    command = alarm._hapi_command()
    assert command.argv == (
        "hapi", "pi", "--session-id", "cdea2a72-a305-43a5-9908-914441023a6d"
    )


def test_lost_resume_target_starts_a_fresh_session(tmp_path: Path) -> None:
    recorder = Recorder()
    recorder.factory = lambda: DeadHapiProcess()
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, **long_cfg_over(tmp_path)
    )
    session = tmp_path / "workplace" / ".admin" / "hapi_session_id"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("gone-id", encoding="utf-8")
    scripted = {"n": 0}

    def sleep(seconds: float) -> None:
        scripted["n"] += 1
        if scripted["n"] >= 3:  # one boot wait + one supervise tick, then stop
            raise StopLoop

    alarm._sleep = sleep
    with pytest.raises(StopLoop):
        alarm._long_pi_loop()
    # the resume key was dropped (hapi.resume_lost) and the next spawn starts fresh
    assert session.read_text(encoding="utf-8") == ""
    assert "hapi.resume_lost" in events_of(tmp_path)
    assert recorder.argv[0][:3] == ("hapi", "pi", "--session-id")
    assert recorder.argv[1] == ("hapi", "pi")


def test_quota_death_hands_the_desk_to_short_sessions_with_both_alarms(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    recorder = Recorder()
    recorder.factory = QuotaHapiProcess
    alarms: list = []
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, alarms=alarms,
        opencode_auth_file=auth, **long_cfg_over(tmp_path),
    )
    # the quota-marked death exits the supervisor loop by the failover switch
    alarm._long_pi_loop()
    assert [kind for kind, _ in alarms] == ["harness_failover", "quota"]
    assert "quota ran out" in alarms[1][1]
    # the next wake is the T-030 short session on the failover
    clock.advance(seconds=60)
    add_will(tmp_path / "knowledge", 7)
    alarm.run_once()
    assert recorder.argv[-1][0] == "opencode"


def test_quota_death_without_a_ready_failover_waits_a_quota_window(tmp_path: Path) -> None:
    """No rendered opencode auth: the quota death must not hammer the dead
    subscription with restarts — one spawn, then the long wait."""
    recorder = Recorder()
    recorder.factory = QuotaHapiProcess
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, **long_cfg_over(tmp_path)
    )
    waits: list = []
    scripted = {"n": 0}

    def bounded_sleep(seconds: float) -> None:
        scripted["n"] += 1
        waits.append(seconds)
        raise StopLoop  # stop the loop inside the quota wait itself

    alarm._sleep = bounded_sleep
    with pytest.raises(StopLoop):
        alarm._long_pi_loop()
    assert len(recorder.argv) == 1  # no restart while the quota waits
    assert waits == [pytest.approx(10.0 * 60.0)]  # the every_min window, not 15 s
    assert "quota.waits" in events_of(tmp_path)


def test_long_session_render_carries_the_delivery_extension(tmp_path: Path) -> None:
    """The desk's agent-dir render includes misaka-link (T-048): the one session
    reads the board feed and its inbox without any runner-side bridge."""
    recorder = Recorder()
    _, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, **long_cfg_over(tmp_path)
    )
    adapter = alarm._adapters["pi"]
    adapter._render()
    from runtime.node.workplace import MISAKA_LINK_DIR, MISAKA_LINK_FILES

    installed = tmp_path / "pi-agent" / "extensions" / "misaka-link"
    for name in MISAKA_LINK_FILES:
        assert (installed / name).read_text(encoding="utf-8") == (
            MISAKA_LINK_DIR / name
        ).read_text(encoding="utf-8"), name


def events_of(tmp_path: Path) -> str:
    return (tmp_path / "events.jsonl").read_text(encoding="utf-8")
