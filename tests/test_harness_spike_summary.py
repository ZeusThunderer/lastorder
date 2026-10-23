"""The T-034 hermes additions to the spike summary: the stream-json shapes of
`hermes chat` (hermes_cli/stream_json.py v0.22.4), the state.db dump the stand
collection takes (the node runner pipes the CLI stdout away) and the node runner's
own JSON log."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "harness_spike"))

import summarize  # noqa: E402


def test_hermes_stream_summary_counts_runs_tokens_and_tool_errors() -> None:
    events = [
        {"type": "system", "subtype": "init", "session_id": "", "timestamp": 1},
        {"type": "text", "text": "Blue.", "timestamp": 2},
        {"type": "tool_use", "name": "body", "input": {"tool": "observe"}, "timestamp": 3},
        {"type": "tool_result", "name": "body", "output": "ok", "duration_ms": 40, "timestamp": 4},
        {"type": "tool_result", "name": "body", "output": "no", "is_error": True, "timestamp": 5},
        {"type": "result", "session_id": "local-aaa", "exit_code": 0,
         "tokens": {"input": 6770, "output": 77, "total": 6847, "cache_read": 6000, "cache_write": 0},
         "duration_ms": 7400, "timestamp": 6},
        {"type": "result", "session_id": "local-bbb", "exit_code": 1, "error": "HTTP 429",
         "tokens": {"input": 0, "output": 0, "total": 0, "cache_read": 0, "cache_write": 0},
         "duration_ms": 900, "timestamp": 7},
    ]
    out = summarize.hermes_stream_summary(events)
    assert out["runs"] == 2
    assert out["turns"] == 2  # one result envelope per oneshot run
    assert out["tokens"]["totals"] == {"input": 6770, "output": 77, "cache_read": 6000, "cache_write": 0}
    assert out["tokens"]["cache_share"] == round(6000 / 6770, 3)
    assert out["tool_uses"] == {"body": 1}
    assert out["tool_errors"] == {"body": 1}
    assert out["failed_runs"] == 1
    assert out["run_duration_ms"]["p50"] == 7400  # pct() takes the upper mid of two


def test_hermes_state_summary_splits_will_sessions_and_bills_credits() -> None:
    state = {
        "sessions": [
            {"id": "local-life", "started_at": 1792665366.5, "api_call_count": 3,
             "input_tokens": 21000, "output_tokens": 300, "reasoning_tokens": 100,
             "cache_read_tokens": 20000, "cache_write_tokens": 0,
             "message_count": 9, "tool_call_count": 6, "model": "glm-5.3-flash",
             "title": "Life session.", "compression_fallback_streak": 0},
            {"id": "local-will", "started_at": 1792666300.0, "api_call_count": 1,
             "input_tokens": 7000, "output_tokens": 400, "reasoning_tokens": 50,
             "cache_read_tokens": 6500, "cache_write_tokens": 0,
             "message_count": 3, "tool_call_count": 1, "model": "glm-5.3-flash",
             "title": "Will session.", "compression_fallback_streak": 0},
        ]
    }
    runner_events = [
        {"ts": "2026-10-22T15:13:28.209128+00:00", "type": "will.session", "serial": 73},
    ]
    # the will row (1792666300) must fall within 120 s of the event ts:
    import datetime as dt
    will_ts = dt.datetime.fromisoformat("2026-10-22T15:13:28.209128+00:00").timestamp()
    state["sessions"][1]["started_at"] = will_ts + 30
    out = summarize.hermes_state_summary(state, runner_events)
    assert out["turns"] == 4  # api_call_count sum, the zcode-comparable metric
    assert out["life_sessions"] == 1 and out["will_sessions"] == 1
    kinds = {s["session_id"]: s["kind"] for s in out["sessions"]}
    assert kinds == {"local-life": "life", "local-will": "will"}
    assert out["reasoning_total"] == 150
    assert out["tokens"]["totals"]["output"] == 850  # 300+400 output + 150 reasoning
    # (28000*2.3 + 26500*0.56 + 850*8)/10^4 — both sessions' input, cache and output
    assert abs(out["quota_credits"]["credits"] - (28000 * 2.3 + 26500 * 0.56 + 850 * 8) / 10_000) < 1e-6


def test_runner_summary_counts_sessions_codes_and_timeline() -> None:
    events = [
        {"ts": "2026-10-22T15:00:00+00:00", "type": "runner.start", "slot": "LastOrder01", "harness": "hermes"},
        {"ts": "2026-10-22T15:00:01+00:00", "type": "life.birth", "serial": 73},
        {"ts": "2026-10-22T15:03:00+00:00", "type": "session.ended", "harness": "hermes",
         "code": 0, "ran_s": 170.2, "tail": "…"},
        {"ts": "2026-10-22T15:06:00+00:00", "type": "session.ended", "harness": "hermes",
         "code": 1, "ran_s": 12.0, "tail": "Gateway starting: deadline"},
        {"ts": "2026-10-22T15:10:00+00:00", "type": "session.stopped_for_death", "serial": 73},
        {"ts": "2026-10-22T15:10:01+00:00", "type": "will.session", "serial": 73},
        {"ts": "2026-10-22T15:12:00+00:00", "type": "will.staged", "serial": 73},
        {"ts": "2026-10-22T15:12:30+00:00", "type": "life.birth", "serial": 74},
        {"ts": "2026-10-22T15:13:00+00:00", "type": "quota.waits", "harness": "hermes"},
    ]
    out = summarize.runner_summary(events)
    assert out["sessions_ended"] == 2
    assert out["session_exit_codes"] == {"0": 1, "1": 1}
    assert out["session_ran_s"]["max"] == 170.2
    kinds = [e["type"] for e in out["timeline"]]
    assert "life.birth" in kinds and "will.session" in kinds and "quota.waits" in kinds
    assert out["event_counts"]["session.ended"] == 2
    # the failed session lands in errors, the happy one does not
    assert [e["code"] for e in out["errors"]] == [1]


def test_hermes_main_prefers_stream_and_falls_back_to_state(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "logs").mkdir(parents=True)
    (run / "logs" / "mcp-calls.jsonl").write_text(
        json.dumps({"ts": 1792665400.0, "t_end": 1792665400.5, "tool": "observe", "ok": True, "args": {}}) + "\n"
        + json.dumps({"ts": 1792665402.0, "t_end": 1792665402.5, "tool": "wait_events", "ok": True,
                      "args": {"timeout_s": 10}, "woke_by": "timeout"}) + "\n",
        encoding="utf-8",
    )
    (run / "logs" / "runner.log").write_text(
        json.dumps({"ts": "2026-10-22T15:00:00+00:00", "type": "life.birth", "serial": 73}) + "\n",
        encoding="utf-8",
    )
    (run / "logs" / "hermes-state.json").write_text(json.dumps({
        "sessions": [{"id": "local-life", "started_at": 1792665366.5, "api_call_count": 4,
                      "input_tokens": 28000, "output_tokens": 500, "reasoning_tokens": 200,
                      "cache_read_tokens": 26000, "cache_write_tokens": 0,
                      "message_count": 12, "tool_call_count": 8, "model": "glm-5.3-flash",
                      "title": "Life session.", "compression_fallback_streak": 0}]
    }), encoding="utf-8")
    (run / "workplace" / "memory").mkdir(parents=True)
    (run / "workplace" / "memory" / "state.md").write_text("цель: дерево", encoding="utf-8")
    (run / "logs" / "mc-server.log").write_text("[12:00:01] <LastOrder01> Узел 73 на связи.\n", encoding="utf-8")

    import pytest
    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as mp:
        mp.setattr("sys.argv", ["summarize.py", "--harness", "hermes", "--minutes", "15", "--run", str(run)])
        summarize.main()
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["harness_events"]["source"] == "state.db dump"
    assert summary["harness_events"]["turns"] == 4
    assert summary["runner"]["event_counts"]["life.birth"] == 1
    assert summary["mcp"]["calls_by_tool"] == {"observe": 1, "wait_events": 1}
    assert summary["turns_per_minute"] == round(4 / 15, 2)
    assert summary["mc_chat_quotes"] == ["Узел 73 на связи."]
    assert "дерево" in summary["state_md"]

    # a captured stream-json wins over the state dump
    (run / "logs" / "harness.log").write_text(
        json.dumps({"type": "result", "session_id": "local-x", "exit_code": 0,
                    "tokens": {"input": 100, "output": 10, "cache_read": 50, "cache_write": 0},
                    "duration_ms": 1000}) + "\n",
        encoding="utf-8",
    )
    with monkeypatch.context() as mp:
        mp.setattr("sys.argv", ["summarize.py", "--harness", "hermes", "--minutes", "15", "--run", str(run)])
        summarize.main()
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["harness_events"]["source"] == "stream-json"
    assert summary["harness_events"]["runs"] == 1


def test_mind_log_entries_filters_calls_and_joins_act_effects() -> None:
    events = [
        {"ts": 100.0, "kind": "event", "event": "game.damage"},
        {"ts": 101.0, "kind": "call", "tool": "act", "args": {"lever": "goto"},
         "ok": True, "t_end": 101.5, "intent_id": "i1"},
        {"ts": 102.0, "kind": "intent", "phase": "applied", "intent_id": "i1"},
        {"ts": 105.2, "kind": "intent", "phase": "done", "intent_id": "i1"},
        {"ts": 106.0, "kind": "call", "tool": "wait_events", "args": {"timeout_s": 2.0},
         "ok": True, "t_end": 108.0, "woke_by": "timeout"},
        {"ts": 109.0, "kind": "call", "tool": "say", "args": {"text": "привет"},
         "ok": False, "t_end": 109.1, "error": "chat filter"},
    ]
    out = summarize.mind_log_entries(events)
    assert [e["tool"] for e in out] == ["act", "wait_events", "say"]
    assert "kind" not in out[0]
    assert out[0]["effect_ms"] == 3700  # first settle (105.2) after t_end (101.5)
    assert "effect_ms" not in out[1]
    summary = summarize.mcp_summary(out)
    assert summary["act_to_effect_ms"]["n"] == 1
    assert summary["act_to_effect_ms"]["p50"] == 3700.0
    assert summary["say_quotes"] == []  # the rejected say is not a quote
    assert summary["rejections"]["say: chat filter"] == 1


def test_hermes_main_reads_the_mind_log_when_present(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    run = tmp_path / "run"
    (run / "logs").mkdir(parents=True)
    (run / "logs" / "runner.log").write_text(
        json.dumps({"ts": "2026-10-23T11:00:00+00:00", "type": "runner.start"}) + "\n",
        encoding="utf-8",
    )
    (run / "logs" / "mind-log.jsonl").write_text(
        json.dumps({"ts": 1.0, "kind": "call", "tool": "observe", "args": {}, "ok": True,
                    "t_end": 1.2}) + "\n",
        encoding="utf-8",
    )
    with monkeypatch.context() as mp:
        mp.setattr("sys.argv", ["summarize.py", "--harness", "hermes", "--minutes", "15", "--run", str(run)])
        summarize.main()
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["mcp"]["calls_by_tool"] == {"observe": 1}


def test_hermes_state_summary_joins_usage_rows_by_session_id() -> None:
    state = {
        "sessions": [
            {"id": "local-a", "started_at": 1792754040.0, "model": "glm-5.3-flash",
             "message_count": 10, "tool_call_count": 4},
            {"id": "local-b", "started_at": 1792754904.0, "model": "glm-5.3-flash",
             "message_count": 5, "tool_call_count": 2},
        ],
        "session_model_usage": [
            {"session_id": "local-a", "model": "glm-5.3-flash", "api_call_count": 3,
             "input_tokens": 1000, "output_tokens": 100, "reasoning_tokens": 10,
             "cache_read_tokens": 5000, "cache_write_tokens": 0},
            {"session_id": "local-b", "model": "glm-5.3-flash", "api_call_count": 2,
             "input_tokens": 2000, "output_tokens": 200, "reasoning_tokens": 20,
             "cache_read_tokens": 1000, "cache_write_tokens": 0},
        ],
    }
    out = summarize.hermes_state_summary(state, [])
    assert out["turns"] == 5
    by_id = {s["session_id"]: s for s in out["sessions"]}
    assert by_id["local-a"]["api_calls"] == 3
    assert by_id["local-a"]["input"] == 1000
    assert out["quota_credits"]["model"] == "glm-5.3-flash"
    # (1000+2000)*2.3 + (5000+1000)*0.56 + (100+10+200+20)*8 all /10^4
    expected = (3000 * 2.3 + 6000 * 0.56 + 330 * 8) / 10_000
    assert abs(out["quota_credits"]["credits"] - expected) < 0.01
