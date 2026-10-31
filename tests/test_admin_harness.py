"""T-059: Last Order on the Z.ai Coding Plan — zcode primary, opencode failover.

The desk mirrors the runner's harness rules (runtime/node/runner.py): the assembled
zcode session command, the quota switch with its board alarm, the readiness gate on
the switch target, the process restart that lands on the primary again, and the
workplace's zcode.json. The stand side (token paths, svc-lo egress, key surface) is
pinned in test_stand_compose.py.
"""

from __future__ import annotations

import json
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
          alarms: list | None = None, **over):
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


def test_zcode_session_command(tmp_path: Path) -> None:
    # a sister's will-session shape (runtime/node/adapters.py), the desk's prompt
    _, _, recorder, alarm = build(tmp_path, harness="zcode", zcode_model="GLM-5.3-Flash")
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    argv = recorder.argv[0]
    assert argv[0] == "zcode" and argv[1] == "-p"
    assert argv[argv.index("--mode") + 1] == "yolo"
    assert argv[argv.index("--model") + 1] == "zai-cp/GLM-5.3-Flash"
    assert "--json" in argv and "--no-color" in argv


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
    assert recorder.argv[0][0] == "zcode"
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
    assert [argv[0] for argv in recorder.argv] == ["zcode", "zcode"]
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
    cfg, clock, recorder, alarm = build(
        tmp_path, recorder=recorder, opencode_auth_file=auth
    )
    add_will(tmp_path / "knowledge", 5)
    alarm.run_once()
    assert recorder.argv[0][0] == "zcode"
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
    assert recorder.argv[1][0] == "zcode"


def test_workplace_writes_zcode_json(tmp_path: Path) -> None:
    cfg, _, _, alarm = build(tmp_path)
    doc = json.loads((cfg.workplace / "zcode.json").read_text(encoding="utf-8"))
    hive = doc["mcp"]["servers"]["hive"]
    assert hive["type"] == "http"
    assert hive["url"] == "http://hive:8000/last-order/mcp"
    assert hive["headers"] == {"X-Node-Key": "sk-lo"}
    # the failover's config is written next to it — a switch needs no re-render
    assert (cfg.workplace / "opencode.json").exists()


def test_config_rejects_a_bad_harness_pair(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_cfg(tmp_path, harness="bogus")
    with pytest.raises(ValueError):
        make_cfg(tmp_path, failover="zcode")  # equals the primary
    with pytest.raises(ValueError):
        make_cfg(tmp_path, failover="bogus")


def test_from_env_defaults_to_zcode(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ADMIN_HARNESS", "ADMIN_HARNESS_FAILOVER", "ADMIN_ZCODE_MODEL",
                 "ZCODE_MODEL", "ZAI_KEY_FILE", "OPENCODE_AUTH_FILE"):
        monkeypatch.delenv(name, raising=False)
    cfg = AdminConfig.from_env()
    assert cfg.harness == "zcode"
    assert cfg.failover == "opencode"
    assert cfg.zcode_model == "GLM-5.3-Flash"
    # the desk's failover is always the OTHER harness, whatever the primary is
    monkeypatch.setenv("ADMIN_HARNESS", "opencode")
    assert AdminConfig.from_env().failover == "zcode"
    monkeypatch.setenv("ZCODE_MODEL", "GLM-5.3")
    assert AdminConfig.from_env().zcode_model == "GLM-5.3"
    monkeypatch.setenv("ADMIN_ZCODE_MODEL", "GLM-5.3-Air")
    assert AdminConfig.from_env().zcode_model == "GLM-5.3-Air"  # ADMIN_ZCODE_MODEL wins
