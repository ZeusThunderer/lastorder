"""Record-level dedup: is this candidate a new record or more evidence for an old one.

KNOWLEDGE.md v2 section 4: a duplicate is the same observation reworded — a shared `about`
key (two empty `about` lists count as shared) plus claim similarity J >= CLAIM_DUP_JACCARD
(schema.py owns the text half). A duplicate adds evidence, it does not create a record;
a near miss below the threshold is a competing record that votes and evidence settle.

Primer records are never duplicates (review P1-2): disagreement with the primer is a node's
own hypothesis, and primer records cannot carry evidence or votes (section 2). Retired
records are gone from the living base — the fix for a retired record is a new record.
"""

from __future__ import annotations

from typing import Sequence

from runtime.knowledge.schema import CLAIM_DUP_JACCARD, Record, jaccard


def shares_about(first: Sequence[str], second: Sequence[str]) -> bool:
    """True when two `about` lists point at the same thing; two empty lists match (section 4)."""
    if not first and not second:
        return True
    return bool(set(first) & set(second))


def find_duplicate(
    records: Sequence[Record], claim: str, about: Sequence[str]
) -> Record | None:
    """The live record this (claim, about) duplicates, highest similarity first.

    Ties keep the earliest record in the given order (records arrive id-sorted); None
    means a competing record must be created.
    """

    best: Record | None = None
    best_score = 0.0
    for record in records:
        if record.status == "retired" or record.source == "primer":
            continue
        if not shares_about(record.about, about):
            continue
        score = jaccard(record.claim, claim)
        if score >= CLAIM_DUP_JACCARD and score > best_score:
            best, best_score = record, score
    return best


__all__ = ["find_duplicate", "shares_about"]
