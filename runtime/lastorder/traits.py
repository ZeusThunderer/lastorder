"""Node traits: five numbers 0-10 and a quirk, dealt once at birth and fixed for the node's life."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from pathlib import Path

TRAIT_NAMES = ("bravery", "greed", "talkativeness", "trust_in_chat_and_nodes", "superstition")


@dataclass(frozen=True)
class Traits:
    bravery: int
    greed: int
    talkativeness: int
    trust_in_chat_and_nodes: int
    superstition: int
    quirk: str
    seed: int

    def as_dict(self) -> dict:
        return asdict(self)

    def numbers(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in TRAIT_NAMES}


def load_quirks(path: Path) -> list[str]:
    """One quirk per line; blank lines and # comments skipped."""
    quirks = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not quirks:
        raise ValueError(f"no quirks in {path}")
    return quirks


def roll(seed: int, quirks: list[str]) -> Traits:
    """Deal traits from a seed, so a node's character can be reproduced from its birth record."""
    rng = random.Random(seed)
    numbers = {name: rng.randint(0, 10) for name in TRAIT_NAMES}
    return Traits(**numbers, quirk=rng.choice(quirks), seed=seed)
