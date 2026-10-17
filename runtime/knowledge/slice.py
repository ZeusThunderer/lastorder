"""Slices of the base for one step and one birth (KNOWLEDGE.md v2 sections 7 and 9).

Pure assembly from records: no I/O, no LLM, nothing mutated. Budgets come from budget.py
and are enforced on a canonical one-line-per-entry rendering — a claim is the bulk of any
rendering T-011 may build, so a block that fits here fits its budget in the prompt too.

Primer records never enter a slice or the catalog (review P1-2, section 8): the primer
reaches the prompt only as block 2, assembled by T-011. Retired records are absent
everywhere (section 4). Vote candidates are hypotheses first, then disputes — a dispute
is settled by votes (section 4), without them it would hang forever (review P1-6).
"""

from __future__ import annotations

from typing import Sequence

from runtime.knowledge.budget import (
    ABOUT_ENTRIES,
    B_ABOUT,
    B_VOTES,
    FRESH_ENTRIES,
    INHERITANCE_SUMMARY_TOKENS,
    INHERITANCE_TERMS,
    INHERITANCE_TERMS_TOKENS,
    INHERITANCE_WILLS,
    PREDECESSOR_LINES,
    VOTE_CANDIDATES,
    estimate_tokens,
)
from runtime.knowledge.schema import Record, Will
from runtime.lastorder.persona import Inheritance
from runtime.ports import KnowledgeEntry, KnowledgeSlice, NetworkGoal, NodeBrief

SERIAL_DIGITS = 5  # ADR-018: the network-wide node number is 5 digits when written out

STATUS_RANK = {"confirmed": 3, "disputed": 2, "hypothesis": 1}


def format_serial(serial: int) -> str:
    """The serial as the network writes it: 00007, not 7 (ADR-018)."""

    return f"{serial:0{SERIAL_DIGITS}d}"


def entry_line(entry: KnowledgeEntry) -> str:
    """Canonical one-line rendering every block budget here is measured against."""

    line = f"{entry.id} [{entry.kind}] {entry.claim}"
    if entry.about:
        line += f" ({' '.join(entry.about)})"
    return line


def _fits(lines: Sequence[str], budget: int) -> bool:
    return sum(estimate_tokens(line + "\n") for line in lines) <= budget


def _minutes(seconds: int) -> str:
    return f"{seconds // 60} мин"


def build_slice(
    *,
    serial: int,
    keys: Sequence[str],
    records: Sequence[Record],
    catalog: str,
    goals: Sequence[NetworkGoal],
) -> KnowledgeSlice:
    """What one node sees around one step (section 9 blocks 4, 6-8).

    `about` — live records about the keys the node sees now, up to ABOUT_ENTRIES per key,
    ranked (status, then recency) inside each key in `keys` order. `fresh` — the recent
    tail. Both share B_ABOUT: the fresh tail is cut first, then the least relevant about
    entries (section 9). `pending_votes` — up to VOTE_CANDIDATES hypotheses (then
    disputes) the node has not authored or voted on, within B_VOTES.
    """

    visible = [
        r for r in records if r.status != "retired" and r.source != "primer"
    ]

    picked: list[Record] = []
    seen: set[str] = set()
    for key in keys:
        matches = [r for r in visible if key in r.about and r.id not in seen]
        matches.sort(key=lambda r: (STATUS_RANK.get(r.status, 0), r.ts, r.id), reverse=True)
        for record in matches[:ABOUT_ENTRIES]:
            picked.append(record)
            seen.add(record.id)

    fresh_pool = [r for r in visible if r.id not in seen]
    fresh_pool.sort(key=lambda r: (r.ts, r.id), reverse=True)
    fresh = fresh_pool[:FRESH_ENTRIES]

    about_lines = [entry_line(r.to_entry()) for r in picked]
    fresh_lines = [entry_line(r.to_entry()) for r in fresh]
    while not _fits(about_lines + fresh_lines, B_ABOUT):
        if fresh_lines:
            fresh_lines.pop()
            fresh.pop()
        elif about_lines:
            about_lines.pop()
            picked.pop()
        else:
            break

    queue = [
        r
        for r in visible
        if r.status in ("hypothesis", "disputed")
        and r.author != serial
        and serial not in {v.node for v in r.votes}
    ]
    queue.sort(key=lambda r: (r.ts, r.id), reverse=True)
    queue.sort(key=lambda r: r.status == "disputed")  # stable: hypotheses first
    votes = queue[:VOTE_CANDIDATES]
    vote_lines = [entry_line(r.to_entry()) for r in votes]
    while not _fits(vote_lines, B_VOTES):
        vote_lines.pop()
        votes.pop()
        if not votes:
            break

    return KnowledgeSlice(
        catalog=catalog,
        network_goals=tuple(goals),
        about=tuple(r.to_entry() for r in picked),
        fresh=tuple(r.to_entry() for r in fresh),
        pending_votes=tuple(r.to_entry() for r in votes),
    )


def build_inheritance(
    *,
    dead: Sequence[tuple[int, Will]],
    alive: Sequence[NodeBrief],
    terms: Sequence[tuple[str, str]],
    catalog: str,
) -> Inheritance:
    """What a newborn node receives (section 7): summaries, K wills, terms, catalog.

    `dead` carries (serial, will) pairs of retired nodes, `terms` — (claim, key) pairs of
    live term records. Predecessor lines are capped at PREDECESSOR_LINES with the older
    ones collapsed into a counter, then the shared INHERITANCE_SUMMARY_TOKENS budget drops
    the oldest lines first; will bodies are capped at write time (schema), terms fit
    INHERITANCE_TERMS_TOKENS by dropping whole lines from the end.
    """

    dead_sorted = sorted(
        dead, key=lambda item: (item[1].meta.died_at, item[0]), reverse=True
    )
    lines = [
        f"{format_serial(serial)} {will.meta.cause} {_minutes(will.meta.lifetime_s)}"
        for serial, will in dead_sorted
    ]
    shown = lines[:PREDECESSOR_LINES]
    older = len(lines) - len(shown)
    active = [
        f"{format_serial(brief.serial)} {brief.slot}"
        for brief in sorted(alive, key=lambda b: b.serial)
    ]

    def predecessor_block(extra_older: int) -> list[str]:
        block = list(shown)
        if extra_older:
            block.append(f"… ещё {extra_older} раньше")
        return block

    while not _fits(predecessor_block(older) + active, INHERITANCE_SUMMARY_TOKENS):
        if shown:
            shown.pop()
            older += 1
        else:
            active.pop()
            if not active:
                break
    predecessors = "\n".join(predecessor_block(older))

    # K latest wills; each body is capped at WILL_BODY_MAX_TOKENS at write time (schema)
    notes = "\n\n".join(will.body for _, will in dead_sorted[:INHERITANCE_WILLS])

    term_lines = [f"{claim} — {key}" for claim, key in terms[:INHERITANCE_TERMS]]
    while term_lines and not _fits(term_lines, INHERITANCE_TERMS_TOKENS):
        term_lines.pop()
    terms_text = "\n".join(term_lines)

    return Inheritance(
        predecessors_summary=predecessors,
        active_nodes_summary="\n".join(active),
        inherited_notes=notes,
        collective_terms=terms_text,
        knowledge_catalog=catalog,
    )


__all__ = ["build_inheritance", "build_slice", "entry_line", "format_serial"]
