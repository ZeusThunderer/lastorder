"""Operator-owned slot profiles; no character policy in the runner (ADR-058)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from runtime.config import CHARACTER_DIR
from runtime.lastorder.traits import TRAIT_NAMES, Traits

PROFILES_PATH = CHARACTER_DIR / "lineages.yml"


@dataclass(frozen=True)
class Lineage:
    key: str
    memory_key: str
    version: int
    traits: Traits
    character: str
    sources: str

    def core(self) -> str:
        return (
            f"# Your permanent lineage: {self.key} (baseline v{self.version})\n"
            f"{self.character}\n"
            f"Lineage development in the shared base: {self.memory_key}.\n"
            f"Sources: {self.sources}.\n"
        )


def load_lineages(path: Path = PROFILES_PATH) -> dict[str, Lineage]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] < 1:
        raise ValueError(f"{path}: positive profile version required")
    profiles = raw.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError(f"{path}: non-empty profiles required")
    result = {}
    for slot, item in profiles.items():
        if not isinstance(slot, str) or not slot or not isinstance(item, dict):
            raise ValueError(f"{path}: invalid slot profile")
        for key in ("key", "memory_key", "quirk", "character", "sources"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"{path}: {slot} needs {key}")
        numbers = item.get("traits")
        if not isinstance(numbers, dict) or set(numbers) != set(TRAIT_NAMES):
            raise ValueError(f"{path}: {slot} needs the five traits")
        if any(type(v) is not int or not 0 <= v <= 10 for v in numbers.values()):
            raise ValueError(f"{path}: {slot} traits must be integers in 0..10")
        if type(item.get("seed")) is not int:
            raise ValueError(f"{path}: {slot} needs integer provenance seed")
        result[slot] = Lineage(
            key=item["key"], memory_key=item["memory_key"], version=raw["version"],
            traits=Traits(**numbers, quirk=item["quirk"], seed=item["seed"]),
            character=" ".join(item["character"].split()), sources=item["sources"],
        )
    return result


def lineage(slot: str) -> Lineage:
    try:
        return load_lineages()[slot]
    except KeyError:
        raise ValueError(f"unknown lineage slot {slot!r}") from None
