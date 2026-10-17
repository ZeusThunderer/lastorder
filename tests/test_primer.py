"""Primer acceptance (T-010): topics, plain keys, loaded records, budget, content rules.

The checks live in dev/primer_check.py so an edit is validated outside pytest too
(`uv run python dev/primer_check.py`); these tests call that checker — the task's
acceptance criterion — and pin its guardrails on synthetic broken primers.
"""

import importlib.util
import json
import sys
from pathlib import Path

from runtime.knowledge.budget import B_PRIMER, PRIMER_MAX_ENTRIES
from runtime.knowledge.schema import Record

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "primer_check", REPO_ROOT / "dev" / "primer_check.py"
)
primer_check = importlib.util.module_from_spec(_SPEC)
sys.modules["primer_check"] = primer_check  # dataclasses resolve types via sys.modules
_SPEC.loader.exec_module(primer_check)

TS = "2026-10-17T00:00:00+00:00"


def write_topic(directory: Path, name: str, entries: list[dict]) -> None:
    (directory / f"{name}.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )


def test_primer_passes_the_check():
    topics, problems = primer_check.check_primer()
    assert problems == []
    assert topics


def test_every_topic_file_holds_one_to_four_entries():
    topics, _ = primer_check.check_primer()
    assert {topic.name for topic in topics} == set(primer_check.TOPICS)
    for topic in topics:
        assert 1 <= len(topic.entries) <= 4, topic.name


def test_main_primer_fits_the_prompt_budget():
    topics, problems = primer_check.check_primer()
    assert problems == []  # with problems found the numbers below would lie
    count, tokens = primer_check.main_budget(topics)
    assert count <= PRIMER_MAX_ENTRIES
    assert tokens <= B_PRIMER


def test_extended_primer_exists_and_stays_out_of_the_main_budget():
    topics, _ = primer_check.check_primer()
    entries = [item for topic in topics for item in topic.entries]
    assert any(item.entry.extended for item in entries)
    count, _ = primer_check.main_budget(topics)
    assert count < len(entries)


def test_entries_load_as_confirmed_primer_records():
    topics, _ = primer_check.check_primer()
    assert topics
    for topic in topics:
        for item in topic.entries:
            record = Record.from_dict(item.entry.to_record(item.record_id, TS).to_dict())
            assert record.source == "primer"
            assert record.status == "confirmed"
            assert record.author == "primer"
            assert record.evidence == ()
            assert record.votes == ()


def test_about_keys_are_what_plain_perception_produces():
    topics, _ = primer_check.check_primer()
    for topic in topics:
        for item in topic.entries:
            for key in item.entry.about:
                assert primer_check.key_problems(key) == [], f"{item.where}: {key}"


def test_claims_are_russian_short_and_free_of_bot_and_links():
    topics, _ = primer_check.check_primer()
    for topic in topics:
        for item in topic.entries:
            assert primer_check.claim_problems(item.entry.claim) == [], item.where


def test_key_problems_catches_keys_plain_mode_never_produces():
    assert primer_check.key_problems("blk:oak_log") == []
    assert primer_check.key_problems("itm:rotten_flesh") == []
    for bad in ("blk:Oak Log", "blk:minecraft:oak_log", "zmb:zombie", "ent:"):
        assert primer_check.key_problems(bad), bad


def test_checker_rejects_a_broken_topic_file(tmp_path):
    write_topic(
        tmp_path,
        "shelter",
        [{"kind": "warning", "claim": "Опасно!", "about": ["zombie"]}],
    )
    _, problems = primer_check.check_primer(tmp_path)
    assert any("T-007 schema" in problem for problem in problems)
    assert any("missing topic" in problem for problem in problems)  # the other topics are absent


def test_checker_rejects_a_budget_overflow(tmp_path):
    claim = "А" * 270 + "."  # a valid claim (<= 280 chars) worth ~170 tokens
    write_topic(tmp_path, "shelter", [{"kind": "fact", "claim": claim, "about": []}] * 4)
    _, problems = primer_check.check_primer(tmp_path)
    assert any("B_PRIMER" in problem for problem in problems)


def test_checker_rejects_too_many_main_entries(tmp_path):
    entry = {"kind": "fact", "claim": "Еда поддерживает силы.", "about": []}
    for name in ("shelter", "tools", "food"):
        write_topic(tmp_path, name, [entry] * 4)  # 12 main records, well under B_PRIMER
    _, problems = primer_check.check_primer(tmp_path)
    assert any("PRIMER_MAX_ENTRIES" in problem for problem in problems)
    assert not any("B_PRIMER" in problem for problem in problems)


def test_checker_rejects_foreign_files_and_topic_overflow(tmp_path):
    write_topic(tmp_path, "shelter", [{"kind": "fact", "claim": "Нора.", "about": []}] * 5)
    write_topic(tmp_path, "wiki", [{"kind": "fact", "claim": "Лишнее.", "about": []}])
    _, problems = primer_check.check_primer(tmp_path)
    assert any("not a known primer topic" in problem for problem in problems)
    assert any("expected 1-4 per topic" in problem for problem in problems)
