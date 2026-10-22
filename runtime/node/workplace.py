"""Birth and archive of one node's workplace (T-029 harness README, ADR-034).

Birth is the README's recipe, in order: copy the whole `agent/last_order/` template,
render `PERSONA.md` through `PersonaProvider.render_core` (name and speech manner of
the preset, dealt traits as words — the numbers stay in birth.json), drop the harness
configs at the workplace root (MCP `body` of this slot + MCP `hive` with the node key),
ship `agent/shared/` next to the workplace as the network instructions.

Ownership is the agent/runner boundary of plan review P1-2: the runner (root in the
container) owns the volume root, `archive/`, `shared/`, the core files and the harness
configs; the workplace directory itself is sticky and sister-writable — she writes her
memory, goals and skills, but cannot delete, replace or chmod the runner's files. On a
non-POSIX host (unit tests) only the mode bits apply; the stand probe exercises the
uid side (`docker compose exec -u sister`).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path

import yaml

from runtime.knowledge.slice import format_serial
from runtime.lastorder.persona import Node, PersonaProvider
from runtime.lastorder.traits import Traits

# Mode plan of the boundary (P1-2); the sticky bit is what keeps runner-owned files
# undeletable inside the sister-writable workplace directory.
WORKPLACE_DIR_MODE = stat.S_IFDIR | 0o1777
READ_ONLY_FILE = 0o444
READ_ONLY_DIR = 0o555
SISTER_FILE = 0o666
SISTER_DIR = 0o777
CORE_FILES = ("PERSONA.md", "AGENTS.md", "SESSION_PROMPT.md")
SISTER_WRITABLE_DIRS = ("memory", "skills", "policy")
RUNNER_DIR = ".runner"


def serial_dir(serial: int) -> str:
    return format_serial(serial)


def _chmod(path: Path, mode: int) -> None:
    os.chmod(path, mode)


def _iter_tree_no_links(root: Path) -> Iterator[Path]:
    """root and everything under it, never following symlinks (review R2-1): a symlink
    planted by the sister is yielded as a leaf, its target never entered — neither for
    recursion (os.walk(followlinks=False), unlike rglob on Python 3.13) nor for chmod,
    which follows the link and would re-mode a root file outside the workplace."""
    yield root
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            yield Path(dirpath) / name


def _chmod_tree(root: Path, dir_mode: int, file_mode: int) -> None:
    """Mode a whole runner-owned tree, symlinks untouched (see _iter_tree_no_links)."""
    for item in _iter_tree_no_links(root):
        if item.is_symlink():
            continue
        _chmod(item, dir_mode if item.is_dir() else file_mode)


def _own(path: Path, uid: int | None, gid: int | None) -> None:
    if uid is not None and hasattr(os, "chown"):
        try:
            os.chown(path, uid, gid if gid is not None else -1)
        except OSError:
            pass  # non-POSIX or unprivileged: the mode plan still applies


def _write_readonly(path: Path, text: str, *, uid: int | None = None, gid: int | None = None) -> None:
    tmp = path.with_name(path.name + ".runner-tmp")
    tmp.write_text(text, encoding="utf-8")
    _own(tmp, uid, gid)
    tmp.replace(path)
    _chmod(path, READ_ONLY_FILE)


def _copy_tree_readonly(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst)
    _chmod_tree(dst, READ_ONLY_DIR, READ_ONLY_FILE)


def _force_rmtree(path: Path) -> None:
    """Remove a read-only tree (the shared refresh of the next birth): give the modes
    back first — POSIX refuses rmdir on 0555 dirs, Windows unlink on 0444 files. A
    symlink is only unlinked, never chmod'd: chmod follows the link and would loosen
    whatever root-owned file it points at (review R2-1)."""

    def _loosen(func, target, _exc) -> None:
        try:
            if not os.path.islink(target):
                os.chmod(target, 0o700 if os.path.isdir(target) else 0o600)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onexc=_loosen)


def _make_sister_writable(root: Path, uid: int | None, gid: int | None) -> None:
    for name in SISTER_WRITABLE_DIRS:
        path = root / name
        if not path.exists():
            continue
        for item in _iter_tree_no_links(path):
            if item.is_symlink():
                continue  # the runner never re-modes or re-owns a symlink's target (R2-1)
            _own(item, uid, gid)
            _chmod(item, SISTER_DIR if item.is_dir() else SISTER_FILE)
    goals = root / "GOALS.md"
    if goals.exists():
        _own(goals, uid, gid)
        _chmod(goals, SISTER_FILE)


def hive_mcp_url(hive_url: str) -> str:
    return f"{hive_url.rstrip('/')}/node/mcp"


def render_zcode_config(body_base: str, hive_url: str, node_key: str) -> dict:
    """zcode.json of the workplace: the body MCP of this slot plus the network MCP."""
    return {
        "mcp": {
            "servers": {
                "body": {
                    "type": "http",
                    "url": f"{body_base.rstrip('/')}/mcp",
                    "enabled": True,
                },
                "hive": {
                    "type": "http",
                    "url": hive_mcp_url(hive_url),
                    "enabled": True,
                    "headers": {"X-Node-Key": node_key},
                },
            }
        }
    }


def render_opencode_config(
    model: str, gateway_url: str, body_base: str, hive_url: str, node_key: str
) -> dict:
    """opencode.json: the node tier through the gateway (ADR-033) plus both MCP servers."""
    return {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "model": f"llm-gateway/{model}",
        "provider": {
            "llm-gateway": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "LLM Gateway",
                "options": {"baseURL": f"{gateway_url.rstrip('/')}/v1"},
                "models": {model: {"name": "node tier"}},
            }
        },
        "mcp": {
            "body": {
                "type": "remote",
                "url": f"{body_base.rstrip('/')}/mcp",
                "enabled": True,
            },
            "hive": {
                "type": "remote",
                "url": hive_mcp_url(hive_url),
                "enabled": True,
                "headers": {"X-Node-Key": node_key},
            },
        },
    }


# Hermes Agent (ADR-032 third harness) keeps its home on the sister's tmpfs, NOT in
# the workplace: state.db, sessions and the .env with the Z.ai key are per-container
# runtime state, while the workplace is the persona that gets archived read-only.
# Rendered once per container start by the entrypoint (drop_hermes_home); the node
# MCP key inside config.yaml is the agent's own identity, same rule as zcode.json
# (ADR-033: keys are names inside the network).
HERMES_HOME = Path("/home/sister/.hermes")
# The coding endpoint of the GLM Coding Plan (harness-spike step 0): Hermes would
# otherwise probe endpoints itself and could land on the billed-by-balance one.
HERMES_GLM_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
# The identity slot SOUL.md has to be non-empty: an empty one makes Hermes inject
# its own DEFAULT_AGENT_IDENTITY ("You are Hermes Agent, built by Nous Research…")
# on top of the sister. This note occupies the slot and points identity back to
# the workplace files; what Hermes still adds on its own is in harness-spike.md.
HERMES_SOUL_MD = (
    "# Identity\n\n"
    "Your identity, duties and manner of speech are defined by this workspace's "
    "AGENTS.md and PERSONA.md. Follow them. Do not invent or add another persona.\n"
)
# T-024 parity (supervisor note): the built-in memory, the user profile and skill
# self-creation stay off — the sister's memory and skills live in the workplace.


def _glm_model(model: str) -> str:
    """Z.ai's OpenAI-compatible endpoint knows lowercase model ids (its own probe
    list is glm-5.3/glm-5.3-flash/…), while ZCODE_MODEL habitually travels cased."""
    return model.strip().lower()


def render_hermes_config(model: str, body_base: str, hive_url: str, node_key: str) -> dict:
    """Hermes config.yaml: the Z.ai coding provider, both MCP servers of the slot,
    files+terminal toolsets and every Hermes extra (memory, skills, self-review,
    messenger/cron/browser surfaces, update checks) off. No key material here:
    GLM_API_KEY/GLM_BASE_URL ride HERMES_HOME/.env (written by the entrypoint)."""
    return {
        "model": {"default": _glm_model(model), "provider": "zai"},
        "mcp_servers": {
            "body": {
                "url": f"{body_base.rstrip('/')}/mcp",
                # fail fast: body/hive restarts (the death path) must not stall a
                # session start for the upstream default connect timeout of 60 s
                "connect_timeout": 10,
                "sampling": {"enabled": False},
            },
            "hive": {
                "url": hive_mcp_url(hive_url),
                "headers": {"X-Node-Key": node_key},
                "connect_timeout": 10,
                "sampling": {"enabled": False},
            },
        },
        # The CLI surface without any --toolsets flag: files, terminal, MCP. The
        # messenger/browser/cron/delegation toolsets exist upstream but never load.
        "platform_toolsets": {"cli": ["file", "terminal"]},
        "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
        "skills": {"creation_nudge_interval": 0},
        "updates": {"check": False},
        "auxiliary": {
            # The post-turn "save memory / patch a skill" fork is a memory/skill
            # feature dressed as an aux call — off for the T-024-parity spike.
            "background_review": {"enabled": False},
            # Titles stay the instant derived kind: no model call per session.
            "title_generation": {"model_upgrade_enabled": False},
        },
    }


def drop_hermes_home(
    home: Path,
    *,
    model: str,
    body_base: str,
    hive_url: str,
    node_key: str,
    glm_key: str = "",
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    """Lay out HERMES_HOME: config.yaml, the neutral SOUL.md and (when the
    entrypoint passes the Z.ai key) the 0600 sister-owned .env. Everything else
    (state.db, sessions, memories) Hermes creates itself on first run."""
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.yaml"
    config.write_text(
        json.dumps(render_hermes_config(model, body_base, hive_url, node_key), indent=2),
        encoding="utf-8",
    )
    (home / "SOUL.md").write_text(HERMES_SOUL_MD, encoding="utf-8")
    if glm_key:
        env_file = home / ".env"
        env_file.write_text(
            f"GLM_API_KEY={glm_key}\nGLM_BASE_URL={HERMES_GLM_BASE_URL}\n",
            encoding="utf-8",
        )
        _chmod(env_file, 0o600)
    # The home is the sister's: she must read config/SOUL/.env and write her state.
    if uid is not None and hasattr(os, "chown"):
        for name in ("config.yaml", "SOUL.md", ".env"):
            path = home / name
            if path.exists():
                try:
                    os.chown(path, uid, gid if gid is not None else -1)
                except OSError:
                    pass  # non-POSIX or unprivileged: the mode plan still applies
    _chmod(home, 0o700)


def _drop_harness_configs(
    root: Path,
    template: Path,
    *,
    body_base: str,
    hive_url: str,
    node_key: str,
    gateway_url: str,
    opencode_model: str,
) -> None:
    """Both adapters' files land at the root at birth: the failover switch (section 9)
    then needs no workplace surgery, and the harness config stays the runner's."""
    _write_readonly(
        root / "zcode.json",
        json.dumps(render_zcode_config(body_base, hive_url, node_key), indent=2),
    )
    _write_readonly(
        root / "opencode.json",
        json.dumps(
            render_opencode_config(opencode_model, gateway_url, body_base, hive_url, node_key),
            indent=2,
        ),
    )
    zcode_skills = template / "harness" / "zcode" / "skills"
    if zcode_skills.is_dir():
        _copy_tree_readonly(zcode_skills, root / ".zcode" / "skills")
    opencode_command = template / "harness" / "opencode" / "command"
    if opencode_command.is_dir():
        _copy_tree_readonly(opencode_command, root / ".opencode" / "command")


def create_workplace(
    *,
    workplaces: Path,
    template_dir: Path,
    shared_dir: Path,
    serial: int,
    traits: Traits,
    persona: PersonaProvider,
    node: Node | None = None,
    body_base: str,
    hive_url: str,
    node_key: str,
    gateway_url: str,
    opencode_model: str,
    sister_uid: int | None = None,
    sister_gid: int | None = None,
) -> Path:
    """The birth recipe of the T-029 harness README; returns the workplace root."""
    root = workplaces / "agent" / serial_dir(serial)
    if root.exists():
        raise FileExistsError(f"workplace of {serial_dir(serial)} already exists")
    root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, root)

    # PERSONA.md: the template's placeholder file becomes this node's rendered core.
    _write_readonly(
        root / "PERSONA.md", persona.render_core(node or Node(serial, traits))
    )
    for name in CORE_FILES:
        path = root / name
        if path.exists():
            _chmod(path, READ_ONLY_FILE)
    _drop_harness_configs(
        root,
        template_dir,
        body_base=body_base,
        hive_url=hive_url,
        node_key=node_key,
        gateway_url=gateway_url,
        opencode_model=opencode_model,
    )

    # The shared rules travel with every workplace: network instructions, read-only.
    shared_dst = workplaces / "shared"
    if shared_dst.exists():
        _force_rmtree(shared_dst)
    if shared_dir.is_dir():
        _copy_tree_readonly(shared_dir, shared_dst)

    (root / RUNNER_DIR).mkdir()
    _chmod(root / RUNNER_DIR, 0o755)

    # The workplace directory last: sticky + sister-writable (P1-2).
    _make_sister_writable(root, sister_uid, sister_gid)
    _chmod(root, WORKPLACE_DIR_MODE)
    return root


def set_will_mode(root: Path, harness: str, proxy_url: str) -> None:
    """Point the hive MCP of the given adapter's config at the runner's will proxy for
    the will session: the proxy adds X-Runner-Session: will and the runner key, so the
    mark cannot be forged from the workplace (plan review P1-1)."""
    if harness == "hermes":
        _set_hermes_will_mode(proxy_url)
        return
    name = "zcode.json" if harness == "zcode" else "opencode.json"
    path = root / name
    if not path.exists():
        return
    config = json.loads(path.read_text(encoding="utf-8"))
    mcp = config.get("mcp", {})
    entry = mcp["servers"].get("hive") if isinstance(mcp.get("servers"), dict) else mcp.get("hive")
    if entry is None:
        return
    entry["url"] = f"{proxy_url.rstrip('/')}/mcp"
    # the file is read-only and runner-owned: rewrite through the same atomic path
    _chmod(path, 0o644)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    _chmod(path, READ_ONLY_FILE)


def _set_hermes_will_mode(proxy_url: str) -> None:
    """The hermes twin of set_will_mode: the config lives in HERMES_HOME (YAML —
    Hermes may have rewritten it with its own migrations after the first run, so
    no JSON round-trip). A missing or unparsable config leaves the will session
    on the real hive: submit_will without the runner mark is refused there and
    the emergency will covers it — degraded, never unsafe."""
    path = HERMES_HOME / "config.yaml"
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return
    if not isinstance(config, dict):
        return
    entry = config.get("mcp_servers", {}).get("hive")
    if not isinstance(entry, dict):
        return
    entry["url"] = f"{proxy_url.rstrip('/')}/mcp"
    _chmod(HERMES_HOME, 0o700)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def link_current(workplaces: Path, serial: int) -> bool:
    """`current` → this serial's workplace: mind-N reads the Jev policy through it
    (JEV_POLICY_PATH of compose), re-read on every call, so the link moves at birth.
    False when the host refuses symlinks (a Windows dev run): the policy path just
    stays empty and C1 runs without a policy, same as before this link existed."""
    link = workplaces / "current"
    target = Path("agent") / serial_dir(serial)
    tmp = workplaces / "current.runner-tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    try:
        tmp.symlink_to(target)
        tmp.replace(link)
    except OSError:
        return False
    return True


def archive_workplace(workplaces: Path, serial: int) -> Path:
    """agent/<serial> → archive/<serial>, everything read-only (NETWORK.md section 6.5).
    Symlinks the sister left behind are moved as links and never chmod'd: the runner
    works as root, and a chmod through `memory/k -> /run/node/secrets/runner_key` would
    re-mode the secret itself (review R2-1)."""
    root = workplaces / "agent" / serial_dir(serial)
    if not root.exists():
        raise FileNotFoundError(f"no workplace of {serial_dir(serial)} to archive")
    archive = workplaces / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    _chmod(root, 0o755)  # undo the sticky+writable mode to move it
    dst = archive / serial_dir(serial)
    if dst.exists():
        _force_rmtree(dst)
    root.replace(dst)
    _chmod_tree(dst, READ_ONLY_DIR, READ_ONLY_FILE)
    return dst
