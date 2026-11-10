"""The network's tool shelf (T-047, ADR-041 workshop): Pi extensions in the common base.

A tool is one directory of Pi extension source (`index.ts` or `index.js` plus helper
files). Last Order publishes from her `workshop/` (the hive reads her workplace
read-only), a sister proposes hers through the proposal queue (kind `tool`) and Last
Order decides. Either way hive is the only writer (ADR-017): the files land under
`base/tools/<name>/` in one git commit naming the author and the reason. The shelf
never runs a tool; the sandboxes load what they get through the workshop loader,
which checks every extension before Pi sees it.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from runtime.knowledge.store import KnowledgeStore

TOOLS_REL = PurePosixPath("base") / "tools"
TOOL_NAME = re.compile(r"\A[a-z][a-z0-9-]{1,39}\Z")
TOOL_EXTS = (".ts", ".js", ".mjs", ".json", ".md")
TOOL_ENTRIES = ("index.ts", "index.js")
TOOL_FILES_MAX = 20
TOOL_BYTES_MAX = 64_000
META_FILE = "TOOL.md"  # the shelf's own note per tool: author and reason


class ToolError(ValueError):
    pass


def check_name(name: Any) -> str:
    if not isinstance(name, str) or not TOOL_NAME.match(name.strip()):
        raise ToolError(
            "tool name: 2-40 chars, lowercase letters, digits and '-', starting with a letter"
        )
    return name.strip()


def check_files(files: Any) -> dict[str, str]:
    """{relative posix path: text}: no escapes, known extensions, an entry point, size caps."""
    if not isinstance(files, Mapping) or not files:
        raise ToolError("tool files: expected a non-empty object {path: text}")
    if len(files) > TOOL_FILES_MAX:
        raise ToolError(f"tool files: at most {TOOL_FILES_MAX} files")
    out: dict[str, str] = {}
    total = 0
    for raw, text in files.items():
        if not isinstance(raw, str) or not isinstance(text, str):
            raise ToolError("tool files: paths and contents must be strings")
        rel = PurePosixPath(raw.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0].startswith("."):
            raise ToolError(f"tool files: {raw!r} must be a plain relative path")
        if rel.suffix not in TOOL_EXTS:
            raise ToolError(f"tool files: {raw!r} — allowed extensions {list(TOOL_EXTS)}")
        if rel.name == META_FILE and len(rel.parts) == 1:
            continue  # the shelf writes its own note
        total += len(text.encode("utf-8"))
        out[str(rel)] = text
    if total > TOOL_BYTES_MAX:
        raise ToolError(f"tool files: {total} bytes, the cap is {TOOL_BYTES_MAX}")
    if not any(entry in out for entry in TOOL_ENTRIES):
        raise ToolError(f"tool files: an entry point {list(TOOL_ENTRIES)} is required")
    return out


def read_dir(root: Path) -> dict[str, str]:
    """A workshop directory as {relative path: text}; symlinks and dot-entries skipped."""
    if not root.is_dir() or root.is_symlink():
        raise ToolError(f"no tool directory {root.name!r} in the workshop")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.startswith(".") or part == "node_modules" for part in rel.parts):
            continue
        if path.is_symlink() or not path.is_file() or path.suffix not in TOOL_EXTS:
            continue
        if len(files) >= TOOL_FILES_MAX:
            raise ToolError(f"tool files: at most {TOOL_FILES_MAX} files")
        files[rel.as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return files


class ToolShelf:
    def __init__(self, store: KnowledgeStore):
        self._store = store

    def _dir(self, name: str) -> Path:
        return self._store.root / Path(*TOOLS_REL.parts) / name

    def publish(
        self,
        name: Any,
        files: Any,
        *,
        author: str,
        by: str,
        why: str,
        extra_files: Mapping[Path, str] | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        """Replace `base/tools/<name>/` with `files` in one commit."""
        name = check_name(name)
        clean = check_files(files)
        if not isinstance(why, str) or not why.strip():
            raise ToolError("publish: why is required")
        base = TOOLS_REL / name
        writes: dict[Path, str] = {Path(*(base / rel).parts): text for rel, text in clean.items()}
        writes[Path(*(base / META_FILE).parts)] = (
            f"# {name}\n\nauthor: {author}\npublished by: {by}\nwhy: {why.strip()}\n"
        )
        # files of the previous version that the new one drops become empty stubs:
        # the store has no delete; the loader skips an empty file, git keeps history
        old = self._dir(name)
        if old.is_dir():
            for path in old.rglob("*"):
                if path.is_file():
                    rel = Path(*TOOLS_REL.parts) / name / path.relative_to(old)
                    writes.setdefault(rel, "")
        writes.update(extra_files or {})
        self._store.commit_layer(
            message or f"tool {name} published by {by} (author {author}): {why.strip()}", writes
        )
        return {"name": name, "files": sorted(clean)}

    def bundle(self) -> dict[str, dict[str, str]]:
        """Every published tool: {name: {relative path: text}}, empty stubs left out."""
        root = self._store.root / Path(*TOOLS_REL.parts)
        out: dict[str, dict[str, str]] = {}
        if not root.is_dir():
            return out
        for tool in sorted(root.iterdir()):
            if not tool.is_dir() or not TOOL_NAME.match(tool.name):
                continue
            files = {
                path.relative_to(tool).as_posix(): path.read_text(encoding="utf-8", errors="replace")
                for path in sorted(tool.rglob("*"))
                if path.is_file()
            }
            files = {rel: text for rel, text in files.items() if text}
            if any(entry in files for entry in TOOL_ENTRIES):
                out[tool.name] = files
        return out

    def names(self) -> list[str]:
        return sorted(self.bundle())


__all__ = [
    "META_FILE", "TOOLS_REL", "TOOL_ENTRIES", "ToolError", "ToolShelf",
    "check_files", "check_name", "read_dir",
]
