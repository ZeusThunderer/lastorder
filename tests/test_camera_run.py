"""T-065: the camera run harness — measured sequence, baseline mode, shared-stand guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def dry_run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "dev/camera_run.sh", "--dry-run", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_dry_run_prints_the_measured_sequence():
    out = dry_run("--minutes", "5")
    assert out.returncode == 0, out.stderr
    text = out.stdout
    # cameras on for all three slots, stats over the window, footage and C1 logs pulled
    assert "BODY_CAMERA_1=1" in text and "BODY_CAMERA_3=1" in text
    assert "measure_resources.py" in text and "--samples 10" in text
    assert "t065_footage" in text and "mind-3-1:/mind-run/log.jsonl" in text
    assert "list?path=LastOrder03" in text


def test_no_camera_is_the_baseline_run():
    text = dry_run("--no-camera").stdout
    assert "BODY_CAMERA_1=1" not in text
    assert "BODY_RENDER_DISTANCE" in text  # the distance point is measured either way


def test_the_shared_stand_is_refused():
    out = subprocess.run(
        ["bash", "dev/camera_run.sh", "--project", "lastorder"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert out.returncode != 0
    assert "refusing project 'lastorder'" in out.stderr


def test_bodies_build_under_an_isolated_tag():
    # a measurement must never rebuild lastorder-body:dev under the live stand
    text = dry_run().stdout
    assert "t065-body:dev" in text
    assert "lastorder-body:dev" not in text
