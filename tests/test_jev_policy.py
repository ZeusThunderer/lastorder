"""T-031: Jev in C1 by the sister's text policy (ADR-027, ADR-035; NETWORK.md section 2).

Unit level: the policy source (criterion 2), the backend replies (cloud JSON, local System
One), the env wiring and the statics (compose JEV_* env, the gateway tier `jev` deployments
with their own timeout and zero retries, the up.sh consumer-key lift).

Contour level: an Executor with a real JevGate over a scripted backend — timeout/error/
invalid reply fall back to the rules with a `jev.fallback` line in log.jsonl (criterion 3),
the floor caps the call rate on a fake clock (criterion 4), the Jev call runs OUTSIDE the
body lock (plan review P1-1: a death or a concurrent submit during a slow call), and the
picked mode (flee/fight/keep) leads the episode between calls (P1-3).
"""

from __future__ import annotations

import json
import threading
import time as _time
from pathlib import Path

import pytest
import yaml

from runtime.llm.client import LLMError
from runtime.mind.executor import Executor, JsonlLog
from runtime.mind.jev import (
    BACKEND_DEFAULT,
    MODEL_DEFAULT,
    POLICY_MAX_CHARS_DEFAULT,
    POLICY_PATH_DEFAULT,
    TIMEOUT_S_DEFAULT,
    CloudJevBackend,
    JevError,
    JevGate,
    LocalJevBackend,
    PolicySource,
    build_messages,
    parse_decision,
)
from runtime.ports import Decision, EntityInfo, Option
from test_executor import FakeBody, FakeClock, FakeWall, Rig

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))
GATEWAY_CONFIG = yaml.safe_load((ROOT / "docker" / "gateway" / "config.yaml").read_text(encoding="utf-8"))
UP_SH = (ROOT / "docker" / "gateway" / "up.sh").read_text(encoding="utf-8")

FLEE = Option("flee", "run away", "goto", {})
FIGHT = Option("fight", "strike the hostile", "strike", {})
KEEP = Option("keep", "hold the intent", "keep", {})
ALL_OPTIONS = [FLEE, FIGHT, KEEP]

SAMPLE_POLICY_TEXT = "Below 8 hp: run. A creeper within 6 blocks: run immediately."


# --- scripted backend + a rigged executor ---------------------------------------------------


class ScriptedBackend:
    """A backend with a scripted outcome and an on_call hook that runs INSIDE decide —
    the hook mutates the world mid-call, exactly what a slow network call lets happen
    while C1 holds no lock (plan review P1-1)."""

    name = "cloud"

    def __init__(self, outcome=None):
        self.outcome = outcome
        self.calls: list[tuple[dict, str | None, list[Option]]] = []
        self.on_call = None

    def decide(self, snapshot, policy, options):
        self.calls.append((dict(snapshot), policy, list(options)))
        if self.on_call is not None:
            self.on_call()
        outcome = self.outcome() if callable(self.outcome) else self.outcome
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def read_log(path: str) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def records_of(path: str, kind: str) -> list[dict]:
    return [r for r in read_log(path) if r.get("kind") == kind]


class JevRig(Rig):
    """A Rig whose executor carries a real JevGate over a scripted backend."""

    def __init__(self, backend: ScriptedBackend, *, tmp_path, floor_s: float = 3.0, **kwargs):
        self.log_path = str(tmp_path / "log.jsonl")
        super().__init__(log_file=self.log_path, **kwargs)
        self.backend = backend
        self.gate = JevGate(
            backend,
            floor_s=floor_s,
            policy=PolicySource(str(tmp_path / "policy" / "threat.md")),  # no file: no policy
            log_=self.log,
            clock=self.clock,
        )
        self.x = Executor(
            self.body,
            username="LastOrder01",
            log_=self.log,
            clock=self.clock,
            wall_clock=self.wall,
            sleep=lambda _s: None,
            idle_s=5.0,
            jev=self.gate,
        )


def damage_episode(rig: JevRig, *, hp: float = 3.0, dist_m: float = 2.0, pos=(2, 64, 0)) -> None:
    rig.body.set_state(hp=hp)
    rig.body.hostiles = [EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=dist_m, pos=pos)]
    rig.body.push_event("game.damage", "zombie hit")


# --- criterion 2: the policy file ------------------------------------------------------------


class TestPolicySource:
    def test_read_from_configured_path(self, tmp_path):
        path = tmp_path / "workplace" / "policy" / "threat.md"
        path.parent.mkdir(parents=True)
        path.write_text(SAMPLE_POLICY_TEXT, encoding="utf-8")
        assert PolicySource(str(path)).text() == SAMPLE_POLICY_TEXT

    def test_truncated_to_the_limit(self, tmp_path):
        path = tmp_path / "p.md"
        path.write_text("x" * 5000, encoding="utf-8")
        max_chars = 1500
        assert len(PolicySource(str(path), max_chars).text()) == max_chars

    def test_missing_file_or_empty_means_no_policy(self, tmp_path):
        assert PolicySource(str(tmp_path / "none.md")).text() is None
        empty = tmp_path / "empty.md"
        empty.write_text("   \n", encoding="utf-8")
        assert PolicySource(str(empty)).text() is None
        unreadable = tmp_path / "dir.md"
        unreadable.mkdir()
        assert PolicySource(str(unreadable)).text() is None  # OSError, not a crash

    def test_policy_reaches_the_prompt(self):
        system, user = build_messages({"hp": 6.0}, SAMPLE_POLICY_TEXT, ALL_OPTIONS)
        assert "NODE THREAT POLICY" in user and SAMPLE_POLICY_TEXT in user
        assert all(o.id in user for o in ALL_OPTIONS)
        assert "option_id" in system

    def test_no_policy_no_block(self):
        _system, user = build_messages({"hp": 6.0}, None, ALL_OPTIONS)
        assert "POLICY" not in user


# --- backend replies -------------------------------------------------------------------------


class TestBackends:
    def test_cloud_json_reply_parsed_with_fences_and_prose(self):
        backend = CloudJevBackend(
            complete=lambda _s, _u: '```json\nSure thing:\n{"option_id": "flee", '
            '"confidence": 0.9, "probs": {"flee": 0.9, "keep": 0.1, "bogus": 0.4}}\n```'
        )
        decision = backend.decide({"hp": 3.0}, None, ALL_OPTIONS)
        assert decision.option_id == "flee"
        assert decision.confidence == pytest.approx(0.9)
        assert decision.probs == {"flee": 0.9, "keep": 0.1}  # foreign ids dropped

    def test_cloud_foreign_option_id_is_an_error(self):
        backend = CloudJevBackend(complete=lambda _s, _u: '{"option_id": "nuke", "confidence": 1}')
        with pytest.raises(JevError, match="foreign option"):
            backend.decide({"hp": 3.0}, None, ALL_OPTIONS)

    def test_cloud_garbage_is_an_error(self):
        backend = CloudJevBackend(complete=lambda _s, _u: "run away!")
        with pytest.raises(JevError):
            backend.decide({"hp": 3.0}, None, ALL_OPTIONS)

    def test_local_system_one_reply_parsed(self):
        def post(_url, payload, _timeout):
            assert payload["questions"]["action"]["criteria"] == {o.id: o.title for o in ALL_OPTIONS}
            assert "Below 8 hp" in payload["questions"]["action"]["instructions"]
            return {
                "answers": {
                    "action": {
                        "choice": "fight",
                        "probabilities": {"fight": 0.7, "flee": 0.3},
                        "confidence": 0.7,
                    }
                }
            }

        backend = LocalJevBackend(post=post)
        decision = backend.decide({"hp": 12.0}, "Below 8 hp", ALL_OPTIONS)
        assert decision.option_id == "fight"
        assert decision.probs == {"fight": 0.7, "flee": 0.3}

    def test_local_foreign_choice_is_an_error(self):
        backend = LocalJevBackend(post=lambda _u, _p, _t: {"answers": {"action": {"choice": "@gamer"}}})
        with pytest.raises(JevError, match="foreign option"):
            backend.decide({"hp": 12.0}, None, ALL_OPTIONS)

    def test_parse_decision_is_strict_about_the_shape(self):
        with pytest.raises(JevError):
            parse_decision("[1, 2]", ALL_OPTIONS)  # not an object
        with pytest.raises(JevError):
            parse_decision("{}", ALL_OPTIONS)  # no option_id


# --- env wiring ------------------------------------------------------------------------------


class TestFromEnv:
    def test_default_backend_is_rules_and_due_is_false(self, monkeypatch):
        for name in ("JEV_BACKEND", "JEV_MODEL", "JEV_API_BASE", "LLM_API_BASE", "JEV_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        gate = JevGate.from_env(log_=None, clock=lambda: 0.0)
        assert not gate.enabled
        assert gate.backend_name == "rules"
        assert not gate.due()  # rules never ask

    def test_cloud_and_local_selection(self, monkeypatch):
        monkeypatch.setenv("JEV_BACKEND", "cloud")
        monkeypatch.setenv("JEV_MODEL", "openrouter/typesafe/jev-router")
        monkeypatch.setenv("LLM_API_BASE", "http://llm-gateway:4000")
        monkeypatch.setenv("LLM_API_KEY", "sk-mind-1")
        gate = JevGate.from_env(log_=None, clock=lambda: 0.0)
        assert isinstance(gate._backend, CloudJevBackend)

        monkeypatch.setenv("JEV_BACKEND", "local")
        gate = JevGate.from_env(log_=None, clock=lambda: 0.0)
        assert isinstance(gate._backend, LocalJevBackend)

    def test_unknown_backend_is_a_startup_error(self, monkeypatch):
        monkeypatch.setenv("JEV_BACKEND", "cloudy")
        with pytest.raises(ValueError, match="JEV_BACKEND"):
            JevGate.from_env(log_=None)

    def test_bad_number_is_a_startup_error(self, monkeypatch):
        monkeypatch.setenv("JEV_BACKEND", "cloud")
        monkeypatch.setenv("JEV_TIMEOUT_S", "fast")
        with pytest.raises(ValueError, match="JEV_TIMEOUT_S"):
            JevGate.from_env(log_=None)


# --- criterion 3: fallback to the rules ------------------------------------------------------


class TestFallback:
    def test_timeout_or_error_runs_the_rules_and_logs_fallback(self, tmp_path):
        backend = ScriptedBackend(outcome=LLMError("LLM call failed after 1 attempts: timeout"))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            damage_episode(rig)  # hp 3 <= flee_hp: the rules flee
            rig.tick()
            gotos = [a for a in rig.body.acts if a[0] == "goto"]
            assert gotos, "the rules ran after the Jev failure"
            fallbacks = records_of(rig.log_path, "jev.fallback")
            assert len(fallbacks) == 1
            assert "timeout" in fallbacks[0]["reason"]
            assert fallbacks[0]["backend"] == "cloud"
            assert not records_of(rig.log_path, "jev.choice")
        finally:
            rig.close()

    def test_invalid_reply_runs_the_rules(self, tmp_path):
        backend = ScriptedBackend(outcome=JevError("reply picked a foreign option 'nuke'"))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            damage_episode(rig)
            rig.tick()
            assert [a for a in rig.body.acts if a[0] == "goto"]  # rules fled
            assert any("foreign option" in r["reason"] for r in records_of(rig.log_path, "jev.fallback"))
        finally:
            rig.close()

    def test_valid_choice_is_logged_and_applied(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("flee", {"flee": 0.9}, 0.9))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            damage_episode(rig)
            rig.tick()
            choices = records_of(rig.log_path, "jev.choice")
            assert choices and choices[0]["option_id"] == "flee"
            assert choices[0]["backend"] == "cloud"
            assert not records_of(rig.log_path, "jev.fallback")
            assert any(a[0] == "goto" for a in rig.body.acts)  # the flee maneuver ran
        finally:
            rig.close()


# --- criterion 4: the floor ------------------------------------------------------------------


class TestFloor:
    def test_floor_caps_calls_per_minute(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("keep", {}, 0.5))
        rig = JevRig(backend, tmp_path=tmp_path, floor_s=3.0)
        try:
            damage_episode(rig)
            for step in range(120):  # 60 s of 0.5 s ticks, damage refreshed every 5 s
                if step % 10 == 0:
                    rig.body.push_event("game.damage", "zombie hit")
                rig.tick(0.5)
            assert len(backend.calls) <= 20, f"floor breached: {len(backend.calls)} calls in a minute"
            assert len(backend.calls) >= 19  # and it did keep asking at the floor rate
        finally:
            rig.close()

    def test_floor_failure_still_spends_the_slot(self, tmp_path):
        backend = ScriptedBackend(outcome=LLMError("down"))
        rig = JevRig(backend, tmp_path=tmp_path, floor_s=3.0)
        try:
            damage_episode(rig)
            rig.tick()
            rig.tick(1.0)  # 1 s later: still floored, no second call
            assert len(backend.calls) == 1
            rig.tick(2.0)  # 3 s after the failed call: the slot is spent, ask again
            assert len(backend.calls) == 2
        finally:
            rig.close()


# --- plan review P1-1: the call runs outside the body lock ------------------------------------


class TestUnlockedCall:
    def test_death_during_slow_jev_runs_the_death_path_unharmed(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("flee", {"flee": 1.0}, 1.0))

        def kill_mid_call():
            rig.body.set_state(hp=0.0)
            rig.body.chat = ["LastOrder01 was slain by Zombie"]

        rig = JevRig(backend, tmp_path=tmp_path)
        backend.on_call = kill_mid_call
        try:
            intent = rig.x.submit("goto", {"x": 20, "y": 64, "z": 20})
            rig.tick()
            damage_episode(rig)
            rig.tick()  # the Jev reply arrives after the body died mid-call

            assert any("stale" in r["reason"] for r in records_of(rig.log_path, "jev.fallback"))
            assert not [a for a in rig.body.acts if a[0] == "goto" and a[1] != {"x": 20, "y": 64, "z": 20}], (
                "no maneuver for a dying body"
            )
            rig.tick()  # the next tick processes the death as always
            assert rig.x.dead()
            assert rig.events_of("game.death")
            assert intent.state == "cleared"
            rig.x.runner_respawn()  # the runner-only respawn still works
            assert not rig.x.dead()
        finally:
            rig.close()

    def test_submit_during_slow_jev_does_not_wait(self, tmp_path):
        release = threading.Event()
        backend = ScriptedBackend(outcome=Decision("keep", {}, 0.5))
        rig = JevRig(backend, tmp_path=tmp_path)

        def slow_decide(*_a):
            backend.calls.append(({}, None, []))
            assert release.wait(10.0)
            return Decision("keep", {}, 0.5)

        backend.decide = slow_decide
        try:
            damage_episode(rig)
            tick_thread = threading.Thread(target=rig.x._tick, daemon=True)  # no outer lock
            tick_thread.start()
            _time.sleep(0.2)  # the tick is now inside the Jev call

            started = _time.perf_counter()
            submitted = rig.x.submit("look_at", {"x": 1.0, "y": 64.0, "z": 1.0})
            took_s = _time.perf_counter() - started
            assert submitted.id > 0  # accepted while Jev was still thinking
            assert took_s < 1.0, f"submit waited {took_s:.2f}s on the Jev call"

            release.set()
            tick_thread.join(timeout=10.0)
        finally:
            release.set()
            rig.close()


# --- plan review P1-3: the picked mode leads the episode ---------------------------------------


class TestModes:
    def test_keep_holds_the_intent_and_blocks_the_rules(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("keep", {"keep": 0.8}, 0.8))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            intent = rig.x.submit("goto", {"x": 20, "y": 64, "z": 20})
            rig.tick()
            acts_before = len(rig.body.acts)

            damage_episode(rig)  # hp 3 <= flee_hp: the rules WOULD flee — Jev said keep
            rig.tick()
            assert intent.state != "suspended"  # keep suspends nothing
            assert len(rig.body.acts) == acts_before  # nothing was issued

            rig.tick(1.0)  # floor holds: the carried keep still leads, not the rules
            assert intent.state != "suspended"
            assert len(rig.body.acts) == acts_before
            # the intent keeps the body, so it settles by its own verdict: the test body
            # never moves, so goto settles failed — that is the keep semantics (rules
            # else-branch, T-025 R1-1), not a maneuver
            assert intent.state in ("active", "failed")
            assert rig.events_of("intent.failed") or intent.state == "active"

            rig.tick(10.0)  # calm: the episode is over, everything resets
            assert rig.x._jev_mode is None
        finally:
            rig.close()

    def test_flee_choice_runs_the_flee_maneuver(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("flee", {"flee": 0.9}, 0.9))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            rig.body.set_state(hp=18.0)  # high hp: the rules would NOT flee — Jev did
            rig.body.hostiles = [EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.0, pos=(2, 64, 0))]
            rig.body.busy_flag = False
            rig.body.push_event("game.damage", "zombie hit")
            rig.tick()
            gotos = [a for a in rig.body.acts if a[0] == "goto"]
            assert gotos, "the Jev flee ran although the rules would have kept the intent"
        finally:
            rig.close()

    def test_fight_choice_strikes(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("fight", {"fight": 0.9}, 0.9))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            rig.body.set_state(hp=18.0)
            rig.body.hostiles = [EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.0, pos=(2, 64, 0))]
            rig.body.push_event("game.damage", "zombie hit")
            rig.tick()
            strikes = [a for a in rig.body.acts if a[0] == "strike"]
            assert strikes and strikes[0][1]["pos"] == [2.0, 64.0, 0.0]
        finally:
            rig.close()

    def test_stale_fight_out_of_reach_falls_back_to_rules(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("fight", {"fight": 0.9}, 0.9))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            damage_episode(rig, hp=18.0, dist_m=2.0)  # fight offered in reach

            def hostile_backs_off():
                rig.body.hostiles = [
                    EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=8.0, pos=(6, 64, 6))
                ]

            backend.on_call = hostile_backs_off
            rig.tick()
            assert not [a for a in rig.body.acts if a[0] == "strike"], "stale fight must not run"
            stale = [r for r in records_of(rig.log_path, "jev.fallback") if "stale" in r["reason"]]
            assert stale, "the stale pick is a recorded fallback"
            # the rules then run for the FRESH picture: hp 18, hostile at 8 m -> keep intent
        finally:
            rig.close()

    def test_stale_when_the_threat_disappears(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("flee", {"flee": 1.0}, 1.0))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            damage_episode(rig, hp=3.0)
            backend.on_call = lambda: rig.body.hostiles.clear()
            rig.tick()
            assert not [a for a in rig.body.acts if a[0] == "goto"], "no flee without a threat"
            assert any("stale" in r["reason"] for r in records_of(rig.log_path, "jev.fallback"))
        finally:
            rig.close()


# --- the prompt carries plain names only (ADR-037, review checklist) --------------------------


class TestPlainNames:
    def test_snapshot_has_no_raw_minecraft_ids(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("keep", {}, 0.5))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            rig.body.set_state(hp=12.0, held="minecraft:wooden_axe", looking_at="minecraft:zombie")
            rig.body.hostiles = [EntityInfo(kind="zombie", name="minecraft:zombie", dist_m=2.0, pos=(2, 64, 0))]
            rig.body.push_event("game.damage", "hit")
            rig.tick()
            assert backend.calls, "Jev was asked"
            snapshot = backend.calls[0][0]
            dumped = json.dumps(snapshot)
            assert "minecraft:" not in dumped, dumped
            assert snapshot["threat"]["dist_m"] == 2.0
            assert snapshot["intent"] is None  # no intent in flight
        finally:
            rig.close()

    def test_fight_is_offered_only_in_reach(self, tmp_path):
        backend = ScriptedBackend(outcome=Decision("keep", {}, 0.5))
        rig = JevRig(backend, tmp_path=tmp_path)
        try:
            damage_episode(rig, dist_m=8.0)  # beyond fight_radius_m (3)
            rig.tick()
            ids = [o.id for o in backend.calls[0][2]]
            assert ids == ["flee", "keep"], ids

            rig.tick(10.0)  # calm, then a close threat offers fight
            damage_episode(rig, dist_m=2.0)
            rig.tick()
            ids = [o.id for o in backend.calls[1][2]]
            assert ids == ["flee", "fight", "keep"], ids
        finally:
            rig.close()


# --- statics: compose env, gateway tier, up.sh lift --------------------------------------------


class TestStatics:
    def test_compose_jev_env_for_mind_1(self):
        env_block = COMPOSE["services"]["mind-1"]["environment"]
        assert env_block["JEV_BACKEND"] == "${JEV_BACKEND:-rules}"
        assert env_block["JEV_MODEL"] == "${JEV_MODEL:-openrouter/typesafe/jev-router}"
        assert env_block["JEV_TIMEOUT_S"] == "${JEV_TIMEOUT_S:-1.5}"
        assert env_block["JEV_MIN_INTERVAL_S"] == "${JEV_MIN_INTERVAL_S:-3}"
        assert env_block["JEV_POLICY_PATH"] == "${JEV_POLICY_PATH:-" + POLICY_PATH_DEFAULT + "}"
        assert env_block["JEV_POLICY_MAX_CHARS"] == "${JEV_POLICY_MAX_CHARS:-1500}"

    def test_runtime_defaults_match_compose(self):
        assert BACKEND_DEFAULT == "rules"
        assert TIMEOUT_S_DEFAULT == 1.5
        assert POLICY_MAX_CHARS_DEFAULT == 1500
        assert MODEL_DEFAULT.endswith("typesafe/jev-router")

    def test_gateway_tier_jev_has_own_timeout_and_zero_retries(self):
        deployments = {
            m["model_name"]: m["litellm_params"]
            for m in GATEWAY_CONFIG["model_list"]
            if "jev" in m["model_name"]
        }
        assert set(deployments) == {"openrouter/typesafe/jev-router", "typesafe/jev-router"}
        for name, params in deployments.items():
            assert params["model"] == "openrouter/typesafe/jev-router", name
            assert params["api_key"] == "os.environ/OPENROUTER_API_KEY", name
            assert params["timeout"] == 2, name  # just above JEV_TIMEOUT_S, never retried
            assert params["num_retries"] == 0, name  # overrides the global num_retries: 2
            assert params["max_retries"] == 0, name  # the SDK client default is 2

    def test_up_sh_lifts_the_consumer_keys(self):
        """T-026 review R1-1: plain `docker compose up` interpolates the consumer keys
        empty (compose reads .env from docker/, not the repo root)."""
        for key in ("GATEWAY_KEY_MIND_1", "GATEWAY_KEY_HIVE", "GATEWAY_KEY_NODE_1"):
            assert key in UP_SH
        assert "${GATEWAY_KEY_MIND_1" not in UP_SH.replace("${!key}", "")  # lifted, not interpolated
