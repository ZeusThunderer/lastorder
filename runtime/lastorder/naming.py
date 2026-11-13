"""Node name and speech manner are a preset, not code (ADR-018). The choice is not final.

The presets themselves are content (ADR-054): agent/last_order/naming.yml. This module
only reads them; a missing or broken file is a startup error, never a silent default.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from runtime.config import CHARACTER_DIR

NAMING_FILE = CHARACTER_DIR / "naming.yml"


@dataclass(frozen=True)
class NamingPreset:
    key: str
    name_format: str  # str.format with `serial`
    speech_style: str  # one line for the Style section of the prompt
    heir_address: str = ""  # the numberless heir address of a will; "" -> heir_neutral


def load_presets(path: Path = NAMING_FILE) -> tuple[dict[str, NamingPreset], str]:
    """(presets, heir_neutral) from the naming file."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("presets"), dict) or not raw["presets"]:
        raise ValueError(f"{path}: needs a non-empty `presets` mapping")
    presets: dict[str, NamingPreset] = {}
    for key, item in raw["presets"].items():
        if not isinstance(item, dict):
            raise ValueError(f"{path}: preset {key!r} must be a mapping")
        try:
            name_format, speech_style = str(item["name_format"]), str(item["speech_style"])
        except KeyError as exc:
            raise ValueError(f"{path}: preset {key!r} lacks {exc.args[0]!r}") from None
        name_format.format(serial=7)  # a broken format is a startup error too
        presets[str(key)] = NamingPreset(
            key=str(key),
            name_format=name_format,
            speech_style=" ".join(speech_style.split()),
            heir_address=str(item.get("heir_address") or ""),
        )
    neutral = str(raw.get("heir_neutral") or "").strip()
    if not neutral:
        raise ValueError(f"{path}: needs `heir_neutral`")
    return presets, neutral


PRESETS, HEIR_NEUTRAL = load_presets()


def preset(key: str) -> NamingPreset:
    try:
        return PRESETS[key]
    except KeyError:
        raise ValueError(f"unknown naming preset {key!r}; known: {', '.join(PRESETS)}") from None


def node_name(naming: NamingPreset, serial: int) -> str:
    return naming.name_format.format(serial=serial)
