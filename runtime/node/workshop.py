"""The workshop loader, Python half (T-047, ADR-041): what Pi loads, checked first.

Pi refuses to START when any extension fails to load [проверено: pi 1.4.0 in
lastorder-node:dev, a ParseError in one extension aborts `pi --mode rpc`]. A sister
or Last Order writing her own extensions would lock herself out of her own session
with one typo. So Pi never loads the agent's sources directly: settings.json lists
one ACTIVE directory, and only extensions that loaded cleanly in a throwaway
`pi -ne -e <entry>` process are copied there.

Two halves keep the active dir:
- this module, at every session render (root, before Pi starts): the network's
  shelf (`net-<name>`) and the agent's own `workshop/<name>` (`own-<name>`), checked;
- the `workshop` Pi extension (runtime/pi/workshop/), in the live session: the agent's
  `workshop_reload` tool runs the same check and reloads the runtime.

An own tool shadows a shelf tool of the same name. A broken extension is left out
and reported — to the event log here, to the agent by the extension.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Mapping

WORKSHOP_DIR = "workshop"  # the agent's own sources, in her workplace
ACTIVE_DIR = "workshop-active"  # in the Pi agent dir: the one dir settings.json lists
NET_DIR = "net-tools"  # in the Pi agent dir: the shelf bundle, root-owned
CONFIG_FILE = "workshop.json"  # in the Pi agent dir: what the extension half reads
REPORT_FILE = "workshop-report.json"  # in the Pi agent dir: the last render's check
ENTRIES = ("index.ts", "index.js")
CHECK_TIMEOUT_S = 30.0
OWN, NET = "own-", "net-"

Checker = Callable[[Path], "str | None"]
_CHECKED: dict[str, str | None] = {}  # content hash -> error (None = loads)


def entry_of(tool_dir: Path) -> Path | None:
    for name in ENTRIES:
        path = tool_dir / name
        if path.is_file() and not path.is_symlink() and path.stat().st_size > 0:
            return path
    return None


def _digest(tool_dir: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(tool_dir.rglob("*")):
        if path.is_file() and not path.is_symlink():
            h.update(path.relative_to(tool_dir).as_posix().encode())
            h.update(b"\0")
            h.update(path.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


def pi_checker(uid: int | None, gid: int | None) -> Checker:
    """Load one extension in a throwaway Pi (no other extensions, no session, no
    model call) as the agent's uid; the error line Pi prints, or None."""

    def check(entry: Path) -> str | None:
        with tempfile.TemporaryDirectory(prefix="pi-check-") as home:
            if uid is not None:
                os.chown(home, uid, gid if gid is not None else -1)
            env = {
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "HOME": home,
                "PI_CODING_AGENT_DIR": home,
            }
            try:
                done = subprocess.run(
                    ["pi", "--mode", "rpc", "--offline", "--no-session", "-ne", "-e", str(entry)],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    timeout=CHECK_TIMEOUT_S, env=env, cwd=home,
                    user=uid, group=gid,
                )
            except subprocess.TimeoutExpired:
                return f"the extension did not finish loading in {CHECK_TIMEOUT_S:g} s"
            except OSError as exc:
                return f"the check could not run: {exc}"
        out = (done.stderr or "") + (done.stdout or "")
        for line in out.splitlines():
            if "Failed to load extension" in line or line.startswith("Error:"):
                return line.strip()[:500]
        if done.returncode != 0:
            return f"pi exited {done.returncode}: {out.strip()[-300:]}"
        return None

    return check


def cached(check: Checker) -> Checker:
    def run(entry: Path) -> str | None:
        key = _digest(entry.parent)
        if key not in _CHECKED:
            _CHECKED[key] = check(entry)
        return _CHECKED[key]

    return run


def _own_tree(path: Path, uid: int | None, gid: int | None) -> None:
    if uid is None:
        return
    for item in [path, *path.rglob("*")]:
        if not item.is_symlink():
            os.chown(item, uid, gid if gid is not None else -1)


def _copy_tool(src: Path, dst: Path) -> None:
    shutil.copytree(
        src, dst, symlinks=False,
        ignore=lambda d, names: [n for n in names if n.startswith(".") or n == "node_modules"],
    )


def write_net_tools(agent_dir: Path, bundle: Mapping[str, Mapping[str, str]]) -> None:
    """The shelf bundle from hive (`GET /tools/bundle`) into `<agent_dir>/net-tools/`."""
    root = agent_dir / NET_DIR
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for name, files in bundle.items():
        if not isinstance(files, Mapping) or "/" in name or name.startswith("."):
            continue
        for rel, text in files.items():
            target = (root / name / rel).resolve()
            if not target.is_relative_to(root.resolve()) or not isinstance(text, str):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")


def render_workshop(
    agent_dir: Path,
    *,
    own: Path | None,
    net: Path | None,
    check: Checker,
    uid: int | None = None,
    gid: int | None = None,
) -> list[dict]:
    """Rebuild `<agent_dir>/workshop-active` from the checked sources; write the
    extension half's config and the report. Returns the report rows."""
    active = agent_dir / ACTIVE_DIR
    if active.exists():
        shutil.rmtree(active)
    active.mkdir(parents=True)
    report: list[dict] = []
    own_names: set[str] = set()
    for source, prefix in ((own, OWN), (net, NET)):
        if source is None or not source.is_dir():
            continue
        for tool in sorted(source.iterdir()):
            if not tool.is_dir() or tool.is_symlink() or tool.name.startswith("."):
                continue
            if prefix == NET and tool.name in own_names:
                report.append({"tool": tool.name, "from": "net", "ok": False,
                               "error": "shadowed by your own workshop tool of the same name"})
                continue
            entry = entry_of(tool)
            if entry is None:
                report.append({"tool": tool.name, "from": prefix[:-1], "ok": False,
                               "error": f"no entry point {list(ENTRIES)}"})
                continue
            error = check(entry)
            report.append({"tool": tool.name, "from": prefix[:-1], "ok": error is None,
                           **({"error": error} if error else {})})
            if error is None:
                _copy_tool(tool, active / f"{prefix}{tool.name}")
                if prefix == OWN:
                    own_names.add(tool.name)
    _own_tree(active, uid, gid)
    os.chmod(active, 0o700)
    config = {
        "active": str(active),
        "sources": [
            {"dir": str(own), "prefix": OWN} if own is not None else None,
            {"dir": str(net), "prefix": NET} if net is not None else None,
        ],
        "report": REPORT_FILE,
    }
    config["sources"] = [s for s in config["sources"] if s]
    for name, text in (
        (CONFIG_FILE, json.dumps(config, indent=2)),
        (REPORT_FILE, json.dumps(report, ensure_ascii=False, indent=2)),
    ):
        path = agent_dir / name
        if path.exists() or path.is_symlink():
            if not path.is_symlink():
                os.chmod(path, 0o644)
            path.unlink()  # never write through a link the agent planted
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o444)
    return report


__all__ = [
    "ACTIVE_DIR", "CONFIG_FILE", "NET_DIR", "REPORT_FILE", "WORKSHOP_DIR",
    "cached", "entry_of", "pi_checker", "render_workshop", "write_net_tools",
]
