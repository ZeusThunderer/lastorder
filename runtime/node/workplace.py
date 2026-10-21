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
from pathlib import Path

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
    for item in [dst, *dst.rglob("*")]:
        _chmod(item, READ_ONLY_DIR if item.is_dir() else READ_ONLY_FILE)


def _force_rmtree(path: Path) -> None:
    """Remove a read-only tree (the shared refresh of the next birth): give the modes
    back first — POSIX refuses rmdir on 0555 dirs, Windows unlink on 0444 files."""

    def _loosen(func, target, _exc) -> None:
        try:
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
        for item in [path, *path.rglob("*")]:
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
    """agent/<serial> → archive/<serial>, everything read-only (NETWORK.md section 6.5)."""
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
    for item in [dst, *dst.rglob("*")]:
        _chmod(item, READ_ONLY_DIR if item.is_dir() else READ_ONLY_FILE)
    return dst
