"""T-036: dev/stand_keys.py — the .env of a keyed stand.

Unit level on temp env files (criteria 1-4): init fills only the missing/empty local tokens
and preserves the rest byte-for-byte; issue asks gateway_keys for the missing GATEWAY_KEY_*
with the issuance stubbed; no key value ever reaches the output; provider keys are named as
missing, never created. The lists themselves are pinned against docker/compose.yml and both
up.sh scripts, so a key renamed in the stand fails here first.

Contour level (criterion 5): docker/node/up.sh itself runs against a prepared temp .env with
a recording docker stub on PATH — no NODE_DEV_OPEN, no "refusing", exit 0.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev"))

import gateway_keys  # noqa: E402
import stand_keys  # noqa: E402

COMPOSE_TEXT = (ROOT / "docker" / "compose.yml").read_text(encoding="utf-8")
NODE_UP = (ROOT / "docker" / "node" / "up.sh").read_text(encoding="utf-8")
GW_UP = (ROOT / "docker" / "gateway" / "up.sh").read_text(encoding="utf-8")

# Absolute path on purpose: on Windows a bare "bash" can resolve to the WSL stub in System32
# (same reason as tests/test_aw_scripts.py).
BASH = shutil.which("bash")

URLSAFE = re.compile(r"^[A-Za-z0-9_-]+$")


def parse_env(path: Path) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in
            (re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
             for line in path.read_text(encoding="utf-8").splitlines()) if m}


def verb_line(out: str, verb: str, name: str) -> bool:
    return re.search(rf"^{verb}\s+{name}$", out, re.M) is not None


# --- the lists cannot drift from the stand ---------------------------------------------------


def test_key_lists_cover_the_stand():
    """Every key the stand reads (compose ${} lifts + up.sh lift lists) is classified, and
    everything the script writes is actually read by the stand."""
    names = set(re.findall(r"\$\{([A-Z0-9_]+)", COMPOSE_TEXT))
    for src in (NODE_UP, GW_UP):
        names.update(t for tup in re.findall(r"^\s*lift (\w+)(?: (\w+))?", src, re.M)
                     for t in tup if t)
        for block in re.findall(r"for key in (.*?); do", src, re.S):
            names.update(block.replace("\\\n", " ").split())
    keyish = {n for n in names if re.search(r"KEY|PASSWORD", n)}
    known = (set(stand_keys.LOCAL_TOKENS) | set(stand_keys.VIRTUAL_KEYS)
             | set(stand_keys.PROVIDER_KEYS) | set(stand_keys.OPERATOR_KEYS))
    # HIVE_*_KEYS maps are assembled by node/up.sh from the per-node keys; ZCODE_API_KEY is the
    # env-only override, the .env name is ZAI_API_KEY.
    derived = {"HIVE_MCP_NODE_KEYS", "HIVE_RUNNER_KEYS", "ZCODE_API_KEY"}
    assert not keyish - derived - known, "stand reads a key stand_keys does not know"
    assert known <= keyish, "stand_keys writes a key the stand never reads"


# --- init: local tokens ----------------------------------------------------------------------


def test_init_fills_only_missing_and_preserves_the_rest(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(
        "# operator comment\n"
        "\n"
        "GATEWAY_MASTER_KEY=sk-existing-master\n"
        "HIVE_RUNNER_KEY_1=\n"
        'GATEWAY_DB_PASSWORD=""\n'
        "GATEWAY_KEY_HIVE=sk-issued-earlier\n"
        "OPENROUTER_API_KEY=sk-or-provider\n"
        "ZAI_API_KEY=\n"
        "LLM_MODEL=openrouter/qwen/qwen3.7-flash\n",
        encoding="utf-8", newline="\n")

    assert stand_keys.main(["init", "--env", str(env)]) == 0

    lines = env.read_text(encoding="utf-8").splitlines()
    by_key = parse_env(env)
    # every local token is present and non-empty after init
    for name in stand_keys.LOCAL_TOKENS:
        assert by_key.get(name, ""), f"{name} still missing/empty after init"
    # existing values are never touched; virtual/provider/foreign keys are not init's business
    assert by_key["GATEWAY_MASTER_KEY"] == "sk-existing-master"
    assert by_key["GATEWAY_KEY_HIVE"] == "sk-issued-earlier"
    assert by_key["OPENROUTER_API_KEY"] == "sk-or-provider"
    assert by_key["ZAI_API_KEY"] == ""  # provider keys are the operator's: no value invented
    assert by_key["LLM_MODEL"] == "openrouter/qwen/qwen3.7-flash"
    # comments and line order survive; present-but-empty keys are filled in place, only
    # the absent ones are appended at the end, in LOCAL_TOKENS order
    assert lines[0] == "# operator comment" and lines[1] == ""
    in_place = {"GATEWAY_MASTER_KEY", "HIVE_RUNNER_KEY_1", "GATEWAY_DB_PASSWORD"}
    appended = [n for n in stand_keys.LOCAL_TOKENS if n not in in_place]
    assert lines[-len(appended):] == [f"{n}={by_key[n]}" for n in appended]

    out = capsys.readouterr().out
    assert verb_line(out, "kept", "GATEWAY_MASTER_KEY")
    assert verb_line(out, "added", "HIVE_RUNNER_KEY_1")
    assert verb_line(out, "added", "GATEWAY_DB_PASSWORD")
    # provider keys: the missing one is named, the present one is not
    assert "missing provider key (set it by hand): ZAI_API_KEY" in out
    assert "missing provider key" not in out.replace(
        "missing provider key (set it by hand): ZAI_API_KEY", "")
    # the issue hint names the missing virtual keys (HIVE is present) and how to issue them
    hint = out.split("virtual keys not set:")[1].splitlines()[0]
    assert "GATEWAY_KEY_LO" in hint and "GATEWAY_KEY_HIVE" not in hint
    assert "stand_keys.py issue" in out


def test_init_token_shapes(tmp_path):
    env = tmp_path / ".env"
    assert stand_keys.main(["init", "--env", str(env)]) == 0
    values = parse_env(env)
    for name in stand_keys.LOCAL_TOKENS:
        value = values[name]
        if name == "GATEWAY_MASTER_KEY":
            assert value.startswith("sk-") and URLSAFE.match(value[3:]), name
        else:
            assert URLSAFE.match(value), name
        assert len(value) >= 40, name
    # the DB password rides a postgres URL: none of the @:/#? that break it (.env.example)
    assert not set(values["GATEWAY_DB_PASSWORD"]) & set("@:/#?'")


def test_init_is_idempotent(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("RCON_PASSWORD=keep-me-rcon\n", encoding="utf-8", newline="\n")
    assert stand_keys.main(["init", "--env", str(env)]) == 0
    once = env.read_text(encoding="utf-8")
    capsys.readouterr()
    assert stand_keys.main(["init", "--env", str(env)]) == 0
    assert env.read_text(encoding="utf-8") == once
    out = capsys.readouterr().out
    assert "added" not in out and out.count("kept") == len(stand_keys.LOCAL_TOKENS)


# --- issue: virtual keys through gateway_keys ------------------------------------------------


def test_issue_uses_gateway_keys_and_writes_tokens(tmp_path, capsys, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GATEWAY_MASTER_KEY=sk-master\nGATEWAY_KEY_HIVE=sk-issued-earlier\n",
                   encoding="utf-8", newline="\n")
    aliases: list[str] = []

    def fake_issue(alias, rpm=None, hourly_usd=None, models=None):
        aliases.append(alias)
        return {"key": f"sk-fake-{alias}", "key_alias": alias}

    monkeypatch.setattr(gateway_keys, "issue_key", fake_issue)

    assert stand_keys.main(["issue", "--env", str(env)]) == 0

    by_key = parse_env(env)
    assert by_key["GATEWAY_KEY_HIVE"] == "sk-issued-earlier"  # present keys are not re-issued
    assert "hive" not in aliases
    for name, alias in stand_keys.VIRTUAL_KEYS.items():
        if name == "GATEWAY_KEY_HIVE":
            continue
        assert by_key[name] == f"sk-fake-{alias}", name  # token_of validated the sk- prefix
    assert sorted(aliases) == sorted(a for n, a in stand_keys.VIRTUAL_KEYS.items()
                                     if n != "GATEWAY_KEY_HIVE")
    out = capsys.readouterr().out
    assert verb_line(out, "issued", "GATEWAY_KEY_LO") and verb_line(out, "kept", "GATEWAY_KEY_HIVE")
    assert "sk-fake" not in out  # values are never printed


def test_issue_gateway_down_is_a_clear_error(tmp_path, capsys, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GATEWAY_MASTER_KEY=sk-master\n", encoding="utf-8", newline="\n")
    before = env.read_text(encoding="utf-8")

    def down(alias, rpm=None, hourly_usd=None, models=None):
        raise SystemExit('gateway_keys: service "llm-gateway" is not running\n'
                         "is the gateway up? start it with: bash docker/gateway/up.sh")

    monkeypatch.setattr(gateway_keys, "issue_key", down)
    assert stand_keys.main(["issue", "--env", str(env)]) == 1
    err = capsys.readouterr().err
    assert "is the gateway up?" in err and "docker/gateway/up.sh" in err
    assert env.read_text(encoding="utf-8") == before  # nothing written on a failed run


def test_no_secret_value_ever_printed(tmp_path, capsys, monkeypatch):
    """Criteria 1+3 together: after init and a stubbed issue, no value from the env file —
    generated, pre-existing or stubbed — appears in stdout or stderr."""
    env = tmp_path / ".env"
    env.write_text("GATEWAY_MASTER_KEY=sk-existing-master\nGATEWAY_KEY_HIVE=sk-issued-earlier\n",
                   encoding="utf-8", newline="\n")
    monkeypatch.setattr(gateway_keys, "issue_key",
                        lambda alias, rpm=None, hourly_usd=None, models=None:
                        {"key": f"sk-fake-{alias}", "key_alias": alias})
    assert stand_keys.main(["init", "--env", str(env)]) == 0
    assert stand_keys.main(["issue", "--env", str(env)]) == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for value in parse_env(env).values():
        if value:
            assert value not in output, "a key value leaked into the output"


# --- criterion 5: node up.sh passes on a prepared env ----------------------------------------


@pytest.mark.skipif(BASH is None, reason="bash is not in PATH")
def test_node_up_sh_passes_after_init(tmp_path):
    """The real docker/node/up.sh (fail-closed R2-6) against a temp .env after init: no
    NODE_DEV_OPEN, docker replaced by a recording stub — the compose command it would have
    run is captured instead of executed."""
    stand = tmp_path / "stand"
    (stand / "docker" / "node").mkdir(parents=True)
    shutil.copy(ROOT / "docker" / "node" / "up.sh", stand / "docker" / "node" / "up.sh")
    env_file = stand / ".env"
    env_file.write_text("# stand env\n", encoding="utf-8", newline="\n")
    assert stand_keys.main(["init", "--env", str(env_file)]) == 0

    # up.sh resolves the repo root with `git worktree list`: a fresh repo makes the stand dir
    # itself the main worktree, so the lift reads our temp .env
    subprocess.run(["git", "init", "-q"], cwd=stand, check=True, capture_output=True)
    stub_dir = stand / "bin"
    stub_dir.mkdir()
    docker_stub = stub_dir / "docker"
    args_file = stand / "docker.args"
    docker_stub.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$DOCKER_ARGS_FILE\"\n",
                           encoding="utf-8", newline="\n")
    docker_stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(stub_dir) + os.pathsep + env.get("PATH", "")
    env["DOCKER_ARGS_FILE"] = str(args_file)
    for name in (*stand_keys.LOCAL_TOKENS, *stand_keys.VIRTUAL_KEYS,
                 *stand_keys.PROVIDER_KEYS, "ZCODE_API_KEY", "NODE_DEV_OPEN"):
        env.pop(name, None)  # hermetic: the env file is the only key source

    proc = subprocess.run([BASH, str(stand / "docker" / "node" / "up.sh")],
                          env=env, capture_output=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    combined = proc.stdout + proc.stderr
    assert "refusing" not in combined
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "up" in args and "-d" in args, args  # it got as far as `docker compose up -d`
    assert not any("=" in a for a in args), args  # no key=value ever rides the argv


def test_init_never_generates_the_slot_login_passwords(tmp_path, capsys) -> None:
    """R1-1: a generated password would log every slot into an external server
    under a credential nobody knows — the operator enters these by hand."""
    env = tmp_path / "env"
    assert stand_keys.main(["init", "--env", str(env)]) == 0
    values = stand_keys.read_env(env) if hasattr(stand_keys, "read_env") else {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in env.read_text().splitlines()
        if "=" in line
    }
    for name in stand_keys.OPERATOR_KEYS:
        assert values.get(name, "") == "", name
    out = capsys.readouterr().out
    assert "MC_LOGIN_PASSWORD_1" in out  # init names them, it never fills them
