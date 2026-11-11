"""T-062: mc-server on Fabric — versions pinned in one place, server and bodies in step.

The loader and launcher are pinned to the stable fabricmc.net meta picks for 1.21.11
(checked 2026-11-11), never "latest" (the itzg image floats otherwise); fabric-api is
the single server mod (ADR-045: no mods "for the future"), pinned by Modrinth version
number. The same FABRIC_API compose variable builds the body image and the server mod
list — these tests keep the two sides and the Dockerfile fallback from drifting.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))


def compose_default(raw: str) -> str:
    # "${VAR:-value}" -> "value": a version pin must carry its explicit default
    m = re.fullmatch(r"\$\{[A-Z_]+:-(.+)}", raw)
    assert m, f"not a ${{VAR:-…}} pin with a default: {raw!r}"
    return m.group(1)


def test_mc_server_is_fabric_with_pinned_loader_and_launcher():
    env = COMPOSE["services"]["mc-server"]["environment"]
    assert env["TYPE"] == "FABRIC"
    assert compose_default(env["FABRIC_LOADER_VERSION"]) == "0.20.1"
    assert compose_default(env["FABRIC_LAUNCHER_VERSION"]) == "1.1.2"


def test_the_single_server_mod_is_fabric_api():
    projects = COMPOSE["services"]["mc-server"]["environment"]["MODRINTH_PROJECTS"]
    m = re.fullmatch(r"fabric-api:(.+)", projects)
    assert m, "the only server mod entry must be fabric-api, pinned"
    assert compose_default(m.group(1)) == "0.140.0+1.21.11"


def test_bodies_build_with_the_same_fabric_api():
    for n in (1, 2, 3):
        arg = COMPOSE["services"][f"body-{n}"]["build"]["args"]["FABRIC_API"]
        assert compose_default(arg) == "0.140.0+1.21.11", f"body-{n}"
    server = COMPOSE["services"]["mc-server"]["environment"]["MODRINTH_PROJECTS"]
    pin = compose_default(server.split(":", 1)[1])
    dockerfile = (ROOT / "docker" / "body" / "Dockerfile").read_text(encoding="utf-8")
    m = re.search(r"^ARG FABRIC_API=(\S+)", dockerfile, re.MULTILINE)
    assert m and m.group(1) == pin, "the body Dockerfile fallback ARG drifted from compose"


def test_backup_world_dry_run_prints_the_stream_commands():
    out = subprocess.run(
        ["bash", "dev/backup_world.sh", "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "lastorder_world" in out and "tar" in out
    # the live world volume is never restored without the explicit --force
    denied = subprocess.run(
        ["bash", "dev/backup_world.sh", "restore", "x.tar.gz", "--dry-run"],
        capture_output=True,
        text=True,
    )
    assert denied.returncode != 0 and "without --force" in denied.stderr
