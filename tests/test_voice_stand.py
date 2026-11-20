"""ADR-055: the voice on the stand — one switch per slot for body and mind, the tts
service behind its own profile, the reference voice and the cloud key in tts alone."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]


def test_one_switch_per_slot_drives_the_body_and_its_mind():
    for n in (1, 2, 3):
        assert SERVICES[f"body-{n}"]["environment"]["BODY_VOICE"] == f"${{BODY_VOICE_{n}:-}}"
        assert SERVICES[f"mind-{n}"]["environment"]["VOICE_ENABLED"] == f"${{BODY_VOICE_{n}:-}}"
    up = (ROOT / "docker" / "node" / "up.sh").read_text(encoding="utf-8")
    assert 'lift "BODY_VOICE_$n"' in up


def test_tts_is_raised_by_the_operator_alone():
    tts = SERVICES["tts"]
    assert tts["profiles"] == ["voice"] and tts["restart"] == "no"
    assert "${STAND_DAEMON_ROOT:-..}/.stand/voice:/voice:ro" in tts["volumes"]
    assert all(p.startswith("127.0.0.1:") for p in tts["ports"])


def test_reference_mounts_are_read_only_in_voice_services_and_the_key_stays_in_tts():
    for name, svc in SERVICES.items():
        for vol in svc.get("volumes") or []:
            if ".stand/voice" in vol:
                assert name in {"tts", "tts-cloud"} and vol.endswith(":ro"), f"{name}: {vol}"
        if name == "tts":
            continue
        env = svc.get("environment") or {}
        values = list(env.values()) if isinstance(env, dict) else env
        assert not any(isinstance(v, str) and "TTS_OPENROUTER_KEY" in v for v in values), name


def test_the_body_writes_svc_config_and_opens_the_inlet_only_for_voice():
    entry = (ROOT / "docker" / "body" / "entrypoint.sh").read_text(encoding="utf-8")
    assert 'if [ "${BODY_VOICE:-}" = "1" ]; then' in entry
    assert "microphone_activation_type=VOICE" in entry
    assert "source_name=lo_mic" in entry
    assert "max-children=1" in entry and "--device=lo_voice" in entry
    dockerfile = (ROOT / "docker" / "body" / "Dockerfile").read_text(encoding="utf-8")
    # SVC lives apart from the always-loaded mods: a silent body never loads it
    assert "mods-voice/voicechat-fabric-1.21.11-" in dockerfile
    assert "mods/voicechat" not in dockerfile
