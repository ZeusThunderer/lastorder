"""The final reflection: one smart call for the will, and the emergency template behind it.

CLAUDE.md: there is no death without a will. `write_will` makes exactly one `smart` call
(KNOWLEDGE.md v2 section 10, kind=death; the level of ADR-023 — no new call kinds), checks
the reply against section 5 (2-4 lines, no trait numbers, no raw game ids) and assembles
the full `Will` (frontmatter plus body). Any failure — a timeout, an exception, an empty,
broken or unacceptable reply — falls back to `emergency_will` of the schema, marked
`emergency: true`, `model: template`. The fallback is hardened: even a `SchemaError` from
broken caller fields still yields a minimal valid file.

The prompt is `agent/last_order/will_prompt.md` (English, reply in Russian, ADR-018 style
placeholders); the user part is the ReflectionInput JSON, trimmed to WILL_INPUT_BUDGET by
dropping the oldest events first (section 10: a death keeps the last ones).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from runtime.config import CHARACTER_DIR
from runtime.knowledge.budget import (
    WILL_BODY_MAX_TOKENS,
    WILL_BODY_SAFE_CHARS,
    WILL_INPUT_BUDGET,
    estimate_tokens,
)
from runtime.knowledge.schema import (
    CAUSES,
    CAUSE_LESSON,
    CAUSE_TEXT,
    ReflectionInput,
    ReflectionOutput,
    SchemaError,
    Will,
    WillMeta,
    emergency_will,
)
from runtime.lastorder.naming import NamingPreset, node_name
from runtime.lastorder.persona import LEADING_COMMENT, Node, render
from runtime.llm.client import CallStats
from runtime.perception.translate import NAMESPACED

WILL_PROMPT_PATH = CHARACTER_DIR / "will_prompt.md"

# The numberless heir address per naming preset (ADR-018): the heir's serial is assigned
# at birth, after retire, so the dying mind cannot know it. A future preset without a
# form here gets the preset-neutral address hive itself uses.
HEIR_ADDRESSES: dict[str, str] = {"lab": "следующий узел", "misaka": "следующая Мисака"}
HEIR_NEUTRAL = "наследник"

# A will body never carries trait numbers (KNOWLEDGE.md v2 section 5: numbers live in the
# frontmatter only). The stems cover the Russian renderings the prompt's examples invite.
_TRAIT_STEMS = (
    "bravery",
    "greed",
    "talkativeness",
    "trust_in_chat_and_nodes",
    "trust in chat and nodes",
    "superstition",
    "храброст",
    "смелост",
    "жадност",
    "разговорчивост",
    "болтливост",
    "довери",
    "суевери",
)
_TRAIT_NUMBER = [
    re.compile(
        rf"(?<![0-9a-zа-яё])(?:{stem})[^0-9\n]{{0,4}}\d|\d[^0-9\n]{{0,4}}(?:{stem})(?![0-9a-zа-яё])",
        re.IGNORECASE,
    )
    for stem in _TRAIT_STEMS
]


class WillRejected(ValueError):
    """The model replied, but the reply cannot be a will body; the emergency path speaks."""


def load_will_template(path: Path = WILL_PROMPT_PATH) -> str:
    return LEADING_COMMENT.sub("", path.read_text(encoding="utf-8"), count=1)


def heir_address(naming: NamingPreset) -> str:
    return HEIR_ADDRESSES.get(naming.key, HEIR_NEUTRAL)


def trait_numbers_in(body: str) -> bool:
    """True when a trait word carries a number nearby («жадность 7», «greed: 8»)."""
    return any(pattern.search(body) for pattern in _TRAIT_NUMBER)


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def assemble_will(
    *,
    serial: int,
    slot: str,
    cause: str,
    born_at: str,
    died_at: str,
    traits,
    body: str,
    model: str,
    emergency: bool,
) -> Will:
    """The full will.md from the service fields plus a validated body. Shared by the
    one-call scenario above and the runner's death path (T-040: `submit_will` stages
    the body alone, the environment re-adds the frontmatter). Hive re-stamps the
    service fields from its own birth.json at retire."""
    meta = WillMeta(
        serial=serial,
        slot=slot,
        cause=cause,
        born_at=born_at,
        died_at=died_at,
        lifetime_s=max(0, int((_utc(died_at) - _utc(born_at)).total_seconds())),
        emergency=emergency,
        traits=traits,
        model=model,
    )
    return Will.from_dict({**meta.to_dict(), "body": body})


def build_will_messages(
    node: Node,
    naming: NamingPreset,
    reflection: ReflectionInput,
    template: str | None = None,
) -> tuple[str, str]:
    """The (system, user) pair of the one death call; the input stays within
    WILL_INPUT_BUDGET by dropping the oldest events, then the oldest notes."""
    if reflection.kind != "death":
        raise ValueError(f"the will needs a death reflection, got kind={reflection.kind!r}")
    if reflection.serial != node.serial:
        raise ValueError(
            f"reflection serial {reflection.serial} does not match the dying node {node.serial}"
        )
    system = render(
        load_will_template() if template is None else template,
        {
            "node_name": node_name(naming, node.serial),
            "speech_style": naming.speech_style,
            "quirk": node.traits.quirk,
        },
    )
    data = reflection.to_dict()
    while estimate_tokens(json.dumps(data, ensure_ascii=False)) > WILL_INPUT_BUDGET:
        if data["events"]:
            del data["events"][0]  # oldest first: a death reflection keeps the last events
        elif data["notes"]:
            del data["notes"][0]
        else:
            raise WillRejected(
                f"will input over {WILL_INPUT_BUDGET} tokens with no events or notes left to drop"
            )
    return system, json.dumps(data, ensure_ascii=False)


def _event_lines(reflection: ReflectionInput) -> list[str]:
    """One short line per event, for the emergency template: post-perception notes first,
    then the trigger types (the template caps each line and the count anyway)."""
    lines = [note for note in reflection.notes if note.strip()]
    lines += [event.type for event in reflection.events]
    return lines


def _emergency(
    *,
    node: Node,
    naming: NamingPreset,
    slot: str,
    birth,
    cause: str,
    died_at: str,
    reflection: ReflectionInput,
    error: str | None,
) -> tuple[Will, str | None]:
    """The template will, then a minimal one: the last line before a death without a will."""
    try:
        return (
            emergency_will(
                serial=node.serial,
                slot=slot,
                cause=cause,
                born_at=birth.born_at,
                died_at=died_at,
                traits=node.traits,
                last_action=reflection.last_action,
                last_events=_event_lines(reflection),
                node_name=node_name(naming, node.serial),
                heir_address=heir_address(naming),
            ),
            error,
        )
    except SchemaError as exc:  # broken caller fields — still a file, never a silent gap
        safe_cause = cause if cause in CAUSES else "game"
        born = birth.born_at if _is_iso(birth.born_at) else died_at
        died = died_at if _is_iso(died_at) else datetime.now(timezone.utc).isoformat()
        minimal = Will.from_dict(
            {
                **WillMeta(
                    serial=node.serial if isinstance(node.serial, int) and node.serial > 0 else 1,
                    slot=slot or "?",
                    cause=safe_cause,
                    born_at=born,
                    died_at=died,
                    lifetime_s=0,
                    emergency=True,
                    traits=node.traits,
                    model="template",
                ).to_dict(),
                "body": "\n".join(
                    [
                        f"{heir_address(naming)}, это {node_name(naming, node.serial)}; "
                        "пишу без своей головы, голова уже молчит.",
                        f"Смерть: {CAUSE_TEXT[safe_cause]}; последнее действие: неизвестно.",
                        "Перед концом видел: ничего приметного.",
                        f"Урок: {CAUSE_LESSON[safe_cause]}. Проверь сам.",
                    ]
                ),
            }
        )
        return minimal, f"{type(exc).__name__}: {exc} (minimal will written)"


def _is_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class WillResult:
    """What one death's will attempt produced; `emergency` says which voice spoke."""

    will: Will
    emergency: bool
    model: str
    stats: CallStats | None = None
    error: str | None = None  # why the smart reply was rejected, when it was


def write_will(
    llm,
    *,
    node: Node,
    naming: NamingPreset,
    slot: str,
    birth,
    cause: str,
    died_at: str,
    reflection: ReflectionInput,
    template: str | None = None,
) -> WillResult:
    """One `smart` call for the will; any refusal degrades to the emergency template.

    `llm` is an LLMClient (or a stub with `complete_json(system, user) -> dict` and
    `config.model`); `birth` is the dying node's BirthRecord (born_at for the frontmatter).
    Never raises for a model-side failure — only for a caller bug (unknown cause, a
    reflection of another kind or serial), which has no business reaching the death path.
    """
    if cause not in CAUSES:
        raise ValueError(f"unknown death cause {cause!r}, want one of {CAUSES}")
    if reflection.kind != "death":
        raise ValueError(f"the will needs a death reflection, got kind={reflection.kind!r}")
    if reflection.serial != node.serial:
        raise ValueError(
            f"reflection serial {reflection.serial} does not match the dying node {node.serial}"
        )
    try:
        system, user = build_will_messages(node, naming, reflection, template)
        reply = llm.complete_json(system, user)
        out = ReflectionOutput.from_dict(reply)
        if out.will is None:
            raise WillRejected("the reply carries no will")
        if trait_numbers_in(out.will):
            raise WillRejected("trait numbers in the will body")
        if NAMESPACED.findall(out.will):
            raise WillRejected(f"raw game ids {sorted(set(NAMESPACED.findall(out.will)))} in the will body")
        will = assemble_will(
            serial=node.serial,
            slot=slot,
            cause=cause,
            born_at=birth.born_at,
            died_at=died_at,
            traits=node.traits,
            body=out.will,
            model=str(llm.config.model),
            emergency=False,
        )
    except Exception as exc:  # a timeout, a broken or unacceptable reply — the template speaks
        will, error = _emergency(
            node=node,
            naming=naming,
            slot=slot,
            birth=birth,
            cause=cause,
            died_at=died_at,
            reflection=reflection,
            error=f"{type(exc).__name__}: {exc}",
        )
        return WillResult(will=will, emergency=True, model="template", error=error)
    return WillResult(
        will=will,
        emergency=False,
        model=str(llm.config.model),
        stats=getattr(llm, "last_call", None),
    )


def check_will_body(body: str) -> None:
    """The section-5 checks a candidate body must pass (used by tests and the scenario)."""
    lines = body.split("\n")
    if any(not line.strip() for line in lines):
        raise WillRejected("blank lines are not allowed")
    if not 2 <= len(lines) <= 4:
        raise WillRejected(f"expected 2-4 lines, got {len(lines)}")
    if trait_numbers_in(body):
        raise WillRejected("trait numbers in the will body")
    if NAMESPACED.findall(body):
        raise WillRejected("raw game ids in the will body")
    size = estimate_tokens(body)
    if size > WILL_BODY_MAX_TOKENS:
        # the numbers the writer needs to fix it in one retry (T-071: three sisters
        # missed by 2-90 tokens and gave up — the bare refusal said by how much)
        cut = math.ceil((size - WILL_BODY_MAX_TOKENS) / 0.6) + 10
        raise WillRejected(
            f"the body is {len(body)} characters, ~{size} tokens by the hive estimate; "
            f"the cap is {WILL_BODY_MAX_TOKENS}: cut about {cut} characters and resubmit "
            f"(a will of {WILL_BODY_SAFE_CHARS} characters or less always fits)"
        )
