"""PersonaProvider: the node's system prompt and the core of her workplace.

Two artifacts, one source of identity (ADR-034):

- the step system prompt from `agent/last_order/prompt_template.md` (the T-011 pipeline): static
  for a node's life, so it sits first in every request and the provider's prefix cache can keep
  it. The template carries one placeholder each for the perception-mode variants (ADR-020): the
  knowledge rule ({knowledge_rule}) and the primer block ({primer}, section 9 block 2).
- the read-only core of the node's workplace — `agent/last_order/PERSONA.md`, rendered by
  `render_core` when the runner copies the template into `agent/<serial>/`. The dealt numbers
  never reach the workplace: traits travel as words (bands below), the name and speech manner
  come from the naming preset (ADR-018), so both presets render the same file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from runtime.config import CHARACTER_DIR, PERCEPTION_MODES, perception_mode
from runtime.lastorder.naming import NamingPreset, node_name
from runtime.lastorder.traits import TRAIT_NAMES, Traits

TEMPLATE_PATH = CHARACTER_DIR / "prompt_template.md"
CORE_TEMPLATE_PATH = CHARACTER_DIR / "PERSONA.md"
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
LEADING_COMMENT = re.compile(r"\A\s*<!--.*?-->\s*", re.S)
NOTHING_YET = "none yet"

# A trait never travels as a number into a node's workplace (ADR-034): the dealt numbers live
# in birth.json, the core speaks bands. The trust bands follow GOALS_COMMS.md section 2; the
# wording presents the character, it does not decide anything for the agent.
TRAIT_BANDS: dict[str, tuple[str, str, str]] = {
    "bravery": (
        "danger makes you step back and study it from a distance; retreat is a finding, not a shame",
        "you weigh danger against the finding; neither heroics nor flight come naturally",
        "the unknown pulls you forward even when it hurts; caution is a hypothesis you keep testing",
    ),
    "greed": (
        "you keep only what you will use soon and leave the rest where it lies",
        "you take an opportunity but do not chase loot past safety",
        "the shiny and the numerous win you over; you count twice and hoard",
    ),
    "talkativeness": (
        "you speak rarely: a line only when it changes something",
        "you speak when there is something to say, and sometimes to think aloud",
        "you narrate: silence feels like lost data",
    ),
    "trust_in_chat_and_nodes": (
        "read the network's advice as someone else's opinion: test it with your own hands "
        "before making it your goal",
        "the network's advice is one argument among several; weigh it against your own "
        "experience and the wills",
        "the network has rarely been wrong: take its advice seriously even when it is inconvenient",
    ),
    "superstition": (
        "coincidences are noise until they repeat twice; rituals are for other nodes",
        "you keep small rituals from lucky moments and drop them when they are disproved",
        "a lucky pattern is real until disproved: you repeat whatever came right before a lucky event",
    ),
}

PRIMER_HEAD = "# Primer — game basics from the network"

# The two knowledge rules (KNOWLEDGE.md v2 section 9): which world the node starts with.
KNOWLEDGE_RULE_PLAIN = (
    "- You know the game basics from the network primer below; verify them yourself.\n"
    "- You know only: what you observe now, the primer, the inherited notes, the collective "
    "terms, the network knowledge below, and what you tried yourself.\n"
    "- Things you observe are given as a key (like blk:birch_log) with a readable name, or "
    "with a node's term. Use the term or invent one; never guess a \"real\" game name.\n"
    "- Network knowledge is advice from other nodes, not an order. Whether you follow it is "
    "your decision."
)

KNOWLEDGE_RULE_MASKED = (
    "- You do NOT know this world: no knowledge of Minecraft, its rules, blocks, mobs or "
    "crafting. Never use outside knowledge about the game.\n"
    "- You know only: what you observe now, the inherited notes, the collective terms, the "
    "network knowledge below, and what you tried yourself.\n"
    "- Things you observe are given as a handle (like blk:7f3a) with a plain description, or "
    "with a term a node already gave them. When you name a thing, use its term or invent one; "
    'never guess a "real" name.\n'
    "- Network knowledge is advice from other nodes, not an order. Whether you follow it is "
    "your decision."
)

KNOWLEDGE_RULES = {"plain": KNOWLEDGE_RULE_PLAIN, "masked": KNOWLEDGE_RULE_MASKED}


def knowledge_rule(mode: str) -> str:
    try:
        return KNOWLEDGE_RULES[mode]
    except KeyError:
        raise ValueError(
            f"unknown perception mode {mode!r}: expected one of {PERCEPTION_MODES} (ADR-020)"
        ) from None


def trait_word(name: str, value: int) -> str:
    """One dealt trait as a band description: low 0-3, middle 4-6, high 7-10, never a number."""

    try:
        low, middle, high = TRAIT_BANDS[name]
    except KeyError:
        raise ValueError(f"unknown trait {name!r}: expected one of {TRAIT_NAMES}") from None
    if not 0 <= value <= 10:
        raise ValueError(f"trait {name} out of 0..10: {value}")
    return low if value <= 3 else middle if value <= 6 else high


def traits_lines(traits: Traits) -> str:
    """The dealt character as one line per trait, in TRAIT_NAMES order, words only (ADR-034)."""

    return "\n".join(f"- {name}: {trait_word(name, getattr(traits, name))}" for name in TRAIT_NAMES)


def primer_block(primer: str, mode: str) -> str:
    """The primer as one template value: heading plus lines in `plain`, nothing in `masked`.

    A masked node must not learn the game from the primer (ADR-020), so the whole section 9
    block 2 disappears rather than staying as an empty heading. The empty value is a bare
    newline: the template keeps one blank line between the knowledge rule and Node variables.
    """

    text = primer.strip()
    if not text or mode == "masked":
        return "\n"
    return f"{PRIMER_HEAD}\n{text}\n\n"


class PersonaError(ValueError):
    pass


def load_template(path: Path = TEMPLATE_PATH) -> str:
    return LEADING_COMMENT.sub("", path.read_text(encoding="utf-8"), count=1)


def load_core_template(path: Path = CORE_TEMPLATE_PATH) -> str:
    """The workplace core template: the READ-ONLY marker comment is stripped on render."""

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
    core_template: str = field(default_factory=load_core_template)

    def name(self, node: Node) -> str:
        return node_name(self.naming, node.serial)

    def render_core(self, node: Node) -> str:
        """The PERSONA.md of a concrete node's workplace (ADR-034).

        The task1 identity with the name and speech manner of the naming preset and the dealt
        character in words; the runner writes this into `agent/<serial>/PERSONA.md` at birth.
        """

        return render(
            self.core_template,
            {
                "node_name": self.name(node),
                "nickname": node.nickname or "not earned yet",
                "speech_style": self.naming.speech_style,
                "traits": traits_lines(node.traits),
                "quirk": node.traits.quirk,
            },
        )

    def system_prompt(
        self,
        node: Node,
        inheritance: Inheritance,
        *,
        mode: str | None = None,
        primer: str = "",
    ) -> str:
        """The system part's static head: template blocks 1-4 (KNOWLEDGE.md v2 section 9).

        `mode` defaults to the environment's PERCEPTION_MODE; `primer` is the pre-rendered
        primer lines (block 2) and is dropped whole in `masked`.
        """

        if mode is None:
            mode = perception_mode()
        values = {
            "node_name": self.name(node),
            "nickname": node.nickname or "not earned yet",
            "speech_style": self.naming.speech_style,
            "quirk": node.traits.quirk,
            **{name: str(getattr(node.traits, name)) for name in TRAIT_NAMES},
            **inheritance.values(),
            "knowledge_rule": knowledge_rule(mode),
            "primer": primer_block(primer, mode),
        }
        return render(self.template, values)
