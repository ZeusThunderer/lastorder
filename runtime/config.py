"""Environment helpers. Containers get variables from compose `env_file`; local tools read `.env`."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHARACTER_DIR = REPO_ROOT / "agent" / "last_order"

PERCEPTION_MODES = ("plain", "masked")


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines into os.environ without overriding what is already set.

    Values are never printed. Keys are stripped because hand-edited files carry stray spaces.
    """
    path = path or REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(*names: str, default: str | None = None) -> str | None:
    """First non-empty variable among `names` (new name first, legacy NetTyan name after)."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def perception_mode() -> str:
    """PERCEPTION_MODE (ADR-020): `plain` by default, only `plain|masked` allowed.

    An unknown value is a startup error, not a silent fallback: a mistyped mode must not
    quietly leak or mask more than intended. Case and outer spaces are tolerated (.env files
    are hand-edited).
    """
    raw = env("PERCEPTION_MODE", default="plain") or ""
    mode = raw.lower()
    if mode not in PERCEPTION_MODES:
        raise ValueError(f"PERCEPTION_MODE must be one of {', '.join(PERCEPTION_MODES)}, got {raw!r}")
    return mode
