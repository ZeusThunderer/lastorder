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
    # and the operator hears about it exactly once
    assert [kind for kind, _ in alarms] == ["harness_failover"]
    assert "opencode" in alarms[0][1]
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
