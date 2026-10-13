"""PersonaProvider: the node's system prompt from agent/last_order/prompt_template.md.

The prompt is static for a node's life (identity, traits, inheritance at birth), so it sits first
in every request and the provider's prefix cache can keep it. Per-step content goes elsewhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from runtime.config import CHARACTER_DIR
from runtime.lastorder.naming import NamingPreset, node_name
from runtime.lastorder.traits import TRAIT_NAMES, Traits

TEMPLATE_PATH = CHARACTER_DIR / "prompt_template.md"
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
LEADING_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.S)
NOTHING_YET = "none yet"


class PersonaError(ValueError):
    pass


def load_template(path: Path = TEMPLATE_PATH) -> str:
    return LEADING_COMMENT.sub("", path.read_text(encoding="utf-8"), count=1)


def render(template: str, values: dict[str, str]) -> str:
    """Fill every {placeholder}. Missing or unused values are errors, not silent blanks."""
    wanted = set(PLACEHOLDER.findall(template))
    missing = wanted - values.keys()
    unused = values.keys() - wanted
    if missing or unused:
        raise PersonaError(f"placeholders missing={sorted(missing)} unused={sorted(unused)}")
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


@dataclass(frozen=True)
class Node:
    serial: int
    traits: Traits
    nickname: str | None = None


@dataclass
class Inheritance:
    """What the hive hands a node at birth. Empty strings become 'none yet'."""

    predecessors_summary: str = ""
    active_nodes_summary: str = ""
    inherited_notes: str = ""
    collective_terms: str = ""
    knowledge_catalog: str = ""

    def values(self) -> dict[str, str]:
        return {k: (v.strip() or NOTHING_YET) for k, v in self.__dict__.items()}


@dataclass
class PersonaProvider:
    naming: NamingPreset
    template: str = field(default_factory=load_template)

    def name(self, node: Node) -> str:
        return node_name(self.naming, node.serial)

    def system_prompt(self, node: Node, inheritance: Inheritance) -> str:
        values = {
            "node_name": self.name(node),
            "nickname": node.nickname or "not earned yet",
            "speech_style": self.naming.speech_style,
            "quirk": node.traits.quirk,
            **{name: str(getattr(node.traits, name)) for name in TRAIT_NAMES},
            **inheritance.values(),
        }
        return render(self.template, values)
