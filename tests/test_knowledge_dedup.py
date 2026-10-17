"""Dedup rules of KNOWLEDGE.md section 4 at the record level (T-012)."""

from typing import Any

from runtime.knowledge.dedup import find_duplicate, shares_about
from runtime.knowledge.schema import Record


def _record(**over: Any) -> Record:
    data = {
        "id": "k-0001",
        "kind": "danger",
        "claim": "Стонущий прямоходящий бьёт на сближении; держать дистанцию.",
        "about": ["ent:zombie"],
        "source": "node",
        "author": 14,
        "ts": "2026-10-17T12:04:11+00:00",
        "status": "hypothesis",
        "evidence": [],
        "votes": [],
    }
    data.update(over)
    return Record.from_dict(data)


def test_shares_about_rules():
    assert shares_about(["ent:zombie"], ["ent:zombie", "itm:bread"])
    assert not shares_about(["ent:zombie"], ["ent:creeper"])
    assert shares_about([], [])  # two empty abouts match: knowledge about the world at large
    assert not shares_about([], ["ent:zombie"])


def test_duplicate_same_about_reworded_claim():
    record = _record()
    found = find_duplicate(
        [record], "Стонущие прямоходящие бьют на сближении; держать дистанцию.", ["ent:zombie"]
    )
    assert found is record  # 5-char truncation merges the case ending


def test_not_a_duplicate_below_threshold():
    # The document's own pair (KNOWLEDGE.md section 4): J = 4/7 < 0.6 — a competing record
    assert (
        find_duplicate([_record()], "стонущий бьёт на сближении", ["ent:zombie"]) is None
    )


def test_no_shared_about_is_never_a_duplicate():
    assert find_duplicate([_record()], _record().claim, ["ent:creeper"]) is None
    assert find_duplicate([_record()], _record().claim, []) is None


def test_empty_about_matches_empty_about():
    record = _record(about=[], claim="Ночью холодно и опасно.")
    assert find_duplicate([record], "ночью холодно, опасно", []) is record


def test_retired_records_are_not_duplicates():
    record = _record(status="retired")
    assert find_duplicate([record], record.claim, ["ent:zombie"]) is None


def test_primer_records_are_not_duplicates():
    # Review P1-2: disagreement with the primer is a node's own hypothesis (section 2)
    record = _record(source="primer", author="primer", status="confirmed")
    assert find_duplicate([record], record.claim, ["ent:zombie"]) is None


def test_highest_similarity_wins():
    near = _record(id="k-0001", claim="Зомби бьёт вблизи, держи дистанцию всегда.")
    exact = _record(id="k-0002", ts="2026-10-17T13:00:00+00:00", claim="Зомби бьёт вблизи, держи дистанцию.")
    claim = "Зомби бьёт вблизи, держи дистанцию."
    assert find_duplicate([near, exact], claim, ["ent:zombie"]) is exact


def test_threshold_is_jaccard_06():
    # J = 4/7 for the first pair (below 0.6: a competing record), exactly 0.6 for the second
    base = _record(claim="раз два три четыре")
    assert find_duplicate([base], "раз два три четыре пять шесть семь", ["ent:zombie"]) is None
    short = _record(claim="раз два три")
    assert find_duplicate([short], "раз два три четыре пять", ["ent:zombie"]) is short
