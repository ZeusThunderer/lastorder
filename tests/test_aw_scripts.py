"""Bash-level tests for the upstream aw scripts (agent/scripts/).

A character session runs these from the repo root with AGENT_NAME set; each test builds that
minimal root in tmp_path — a copy of agent/scripts/ and one character home — so nothing touches
the real memories under agent/.
"""
import datetime
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "agent" / "scripts"

# Absolute path on purpose: on Windows a bare "bash" can resolve to the WSL stub in System32,
# which silently strips env vars the script needs.
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not in PATH")


def make_repo(tmp_path, agent="vesper"):
    shutil.copytree(SCRIPTS, tmp_path / "agent" / "scripts")
    home = tmp_path / "agent" / agent
    home.mkdir(parents=True)
    (home / "PERSONA.md").write_text(f"# {agent}\n", encoding="utf-8", newline="\n")
    return home


def remember(repo, entity, text):
    return subprocess.run(
        [BASH, "agent/scripts/remember.sh", entity, text],
        cwd=repo,
        env=dict(os.environ, AGENT_NAME="vesper"),
        capture_output=True,
        text=True,
    )


def line_right_after_events(body):
    lines = body.split("\n")
    assert "## Events" in lines, "no ## Events header in:\n" + body
    return lines[lines.index("## Events") + 1]


def event_line(text):
    return f"- {datetime.date.today().isoformat()} — {text}"


def test_remember_creates_note_for_new_entity(tmp_path):
    home = make_repo(tmp_path)

    done = remember(tmp_path, "lantern", "met at the well")

    assert done.returncode == 0, done.stderr
    body = (home / "memory" / "entities" / "lantern.md").read_text(encoding="utf-8")
    assert line_right_after_events(body) == event_line("met at the well")


def test_remember_adds_events_header_to_note_without_one(tmp_path):
    home = make_repo(tmp_path)
    note = home / "memory" / "entities" / "lantern.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "# lantern\ntype: stranger | met: 2026-01-01\n\n## Links\n",
        encoding="utf-8",
        newline="\n",
    )

    done = remember(tmp_path, "lantern", "traded a map")

    assert done.returncode == 0, done.stderr
    body = note.read_text(encoding="utf-8")
    assert line_right_after_events(body) == event_line("traded a map")
