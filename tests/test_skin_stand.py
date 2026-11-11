"""T-061: the sister skin on the stand.

Pins the skin surface against drift: the skinrestorer mod pinned next to fabric-api
(T-062's pin intact), the operator's PNG mounted read-only into mc-server for the
mod alone, the per-slot MC_SKIN_COMMAND riding mind-N exactly like the T-053 login
password, and dev/apply_skin.sh printing all three rcon commands on a dry run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))

SLOTS = ("LastOrder01", "LastOrder02", "LastOrder03")


def test_skinrestorer_mod_is_pinned_next_to_fabric_api():
    projects = COMPOSE["services"]["mc-server"]["environment"]["MODRINTH_PROJECTS"]
    # the T-062 pin survives, the skin mod rides the same comma list, both pinned
    assert "fabric-api:${FABRIC_API:-0.140.0+1.21.11}" in projects
    assert "skinrestorer:${SKINRESTORER:-2.11.1+1.21.11-fabric}" in projects
    assert ", " not in projects  # a space would become part of a project name


def test_mc_server_mounts_the_operators_skin_and_mod_config_read_only():
    vols = COMPOSE["services"]["mc-server"]["volumes"]
    assert "../agent/last_order/skin/sister.png:/skins/sister.png:ro" in vols
    assert "./mc-server/skinrestorer/config.json:/data/config/skinrestorer/config.json:ro" in vols


def test_no_other_service_sees_the_skin_or_the_mod_config():
    # the operator's file and the mod config live in mc-server alone: no sandbox,
    # body or mind ever gets a mount touching /skins or skinrestorer
    for name, svc in COMPOSE["services"].items():
        if name == "mc-server":
            continue
        for vol in svc.get("volumes") or []:
            assert "/skins" not in vol and "skinrestorer" not in vol, f"{name}: {vol}"


def test_skin_command_rides_the_mind_of_each_slot_alone():
    # the T-061 external-server skin command: the same per-slot lift as
    # MC_LOGIN_PASSWORD_N — mind-N alone, never a body or a sandbox
    for n in (1, 2, 3):
        env = COMPOSE["services"][f"mind-{n}"]["environment"]
        assert env["MC_SKIN_COMMAND"] == f"${{MC_SKIN_COMMAND_{n}:-}}", f"mind-{n}"
    for name, svc in COMPOSE["services"].items():
        if name.startswith("mind-"):
            continue
        env = svc.get("environment") or {}
        values = list(env.values()) if isinstance(env, dict) else env
        assert not any(
            isinstance(v, str) and "MC_SKIN_COMMAND" in v for v in values
        ), f"MC_SKIN_COMMAND leaked into {name}"


def test_apply_skin_dry_run_prints_all_three_rcon_commands():
    # the acceptance command itself: no docker daemon needed, one line per slot
    out = subprocess.run(
        ["bash", "dev/apply_skin.sh", "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout
    for slot in SLOTS:
        assert f"exec -T mc-server rcon-cli skin set collection sister {slot}" in out
