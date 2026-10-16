"""KNOWLEDGE.md v2 examples parse (and bad ones are rejected) — straight from the document.

Every fenced block tagged `json <Type>` / `markdown <Type>` (optionally `invalid`) is pulled
out of docs/architecture/KNOWLEDGE.md, so the document cannot drift from the schema: a new
example is tested the moment it is written, and an unknown tag fails the test loudly.
"""

import json
import math
import re
from pathlib import Path

import pytest

from runtime.knowledge import budget, schema
from runtime.lastorder.traits import Traits
from runtime.ports import KnowledgeEntry

KNOWLEDGE_MD = Path(__file__).resolve().parents[1] / "docs" / "architecture" / "KNOWLEDGE.md"
BLOCK = re.compile(r"```(json|markdown)\s+([A-Za-z]+)(\s+invalid)?\s*\n(.*?)\n```", re.S)

JSON_TYPES = {
    "Record": schema.Record,
    "PrimerEntry": schema.PrimerEntry,
    "BirthFile": schema.BirthFile,
    "TriggerEvent": schema.TriggerEvent,
    "ReflectionInput": schema.ReflectionInput,
    "ReflectionOutput": schema.ReflectionOutput,
}


def examples():
    text = KNOWLEDGE_MD.read_text(encoding="utf-8")
    found = list(BLOCK.finditer(text))
    assert found, "no tagged examples in KNOWLEDGE.md"
    return [
        pytest.param(
            m.group(1),
            m.group(2),
            bool(m.group(3)),
            m.group(4),
            id=f"{i}-{m.group(2)}{'-invalid' if m.group(3) else ''}",
        )
        for i, m in enumerate(found)
    ]


def check_core(body: str) -> None:
    """`markdown core` example: fits the 1.5 KB limit and has the three sections."""
    assert len(body.encode("utf-8")) <= budget.CORE_MEMORY_LIMIT_BYTES
    for section in ("## Цель", "## Факты", "## Сеть"):
        assert section in body, f"core example misses {section}"


@pytest.mark.parametrize("lang, name, invalid, body", examples())
def test_document_example(lang, name, invalid, body):
    if lang == "json":
        assert name in JSON_TYPES, f"unknown tagged example type {name}"
        if invalid:
            with pytest.raises((schema.SchemaError, json.JSONDecodeError)):
                JSON_TYPES[name].from_dict(json.loads(body))
        else:
            parsed = JSON_TYPES[name].from_dict(json.loads(body))
            assert JSON_TYPES[name].from_dict(parsed.to_dict()) == parsed  # roundtrip
    else:
        if name == "Will":
            if invalid:
                with pytest.raises(schema.SchemaError):
                    schema.Will.from_markdown(body)
            else:
                will = schema.Will.from_markdown(body)
                assert schema.Will.from_markdown(will.to_markdown()) == will
        elif name == "core":
            assert not invalid, "core example must be valid"
            check_core(body)
        else:
            pytest.fail(f"unknown markdown example type {name}")


# --- record ---------------------------------------------------------------------------


def _record_data(**over):
    data = {
        "id": "k-0001",
        "kind": "danger",
        "claim": "Крипер взрывается вблизи.",
        "about": ["ent:creeper"],
        "source": "node",
        "author": 7,
        "ts": "2026-10-17T10:00:00+00:00",
        "status": "hypothesis",
        "evidence": [],
        "votes": [],
    }
    data.update(over)
    return data


def test_record_to_entry():
    record = schema.Record.from_dict(_record_data())
    entry = record.to_entry()
    assert isinstance(entry, KnowledgeEntry)
    assert (entry.id, entry.kind, entry.claim, entry.status, entry.about) == (
        record.id,
        record.kind,
        record.claim,
        record.status,
        record.about,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(kind="warning"),
        lambda d: d.update(unknown=1),
        lambda d: d.update(about=["zombie"]),
        lambda d: d.update(about=["ent:zombie", "ent:zombie"]),
        lambda d: d.update(claim="   "),
        lambda d: d.update(author="primer"),  # source is node here
        lambda d: d.update(status="accepted"),
        lambda d: d.update(id="42"),
        lambda d: d.update(ts="yesterday"),
        lambda d: d.pop("evidence"),
        lambda d: d.update(kind="term", about=[]),  # a term needs exactly one about key
    ],
)
def test_record_rejects(mutate):
    data = _record_data()
    mutate(data)
    with pytest.raises(schema.SchemaError):
        schema.Record.from_dict(data)


def test_primer_entry_becomes_confirmed_record():
    entry = schema.PrimerEntry.from_dict(
        {"kind": "danger", "claim": "Крипер взрывается вблизи.", "about": ["ent:creeper"]}
    )
    record = entry.to_record("k-0001", "2026-10-17T00:00:00+00:00")
    assert (record.source, record.author, record.status) == ("primer", "primer", "confirmed")
    assert schema.Record.from_dict(record.to_dict()) == record  # a valid record right away
    with pytest.raises(schema.SchemaError):
        schema.PrimerEntry.from_dict(
            {"kind": "term", "claim": "имя", "about": ["ent:creeper"]}
        )  # terms are folklore, not primer


# --- triggers -------------------------------------------------------------------------


def test_trigger_importance_must_match_table():
    data = {
        "type": "damage.taken",
        "importance": 5,  # table says 4
        "event_id": "e-1",
        "ts": "2026-10-17T10:00:00+00:00",
    }
    with pytest.raises(schema.SchemaError):
        schema.TriggerEvent.from_dict(data)
    data["importance"] = 4
    assert schema.TRIGGER_IMPORTANCE["damage.taken"] == 4
    assert schema.REFLECT_IMPORTANCE_THRESHOLD == 12  # documented in section 3


# --- wills ----------------------------------------------------------------------------


def _will_data(**over):
    data = {
        "serial": 14,
        "slot": "LastOrder02",
        "cause": "game",
        "born_at": "2026-10-17T12:04:11+00:00",
        "died_at": "2026-10-17T12:41:53+00:00",
        "lifetime_s": 2262,
        "emergency": False,
        "traits": {
            "bravery": 3,
            "greed": 7,
            "talkativeness": 5,
            "trust_in_chat_and_nodes": 4,
            "superstition": 9,
            "quirk": "считает крипера родственником",
            "seed": 4213,
        },
        "model": "deepseek/deepseek-v4.1-flash",
        "body": "Урок: не ходить в горы ночью.\nШутка: мне досталась жадность, я не просил.",
    }
    data.update(over)
    return data


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(cause="boredom"),
        lambda d: d.update(body="одна строка"),
        lambda d: d.update(body="a\nb\nc\nd\ne"),
        lambda d: d.update(emergency="no"),
        lambda d: d["traits"].update(bravery=11),
        lambda d: d["traits"].pop("seed"),
        lambda d: d.update(unknown=True),
    ],
)
def test_will_rejects(mutate):
    data = _will_data()
    mutate(data)
    with pytest.raises(schema.SchemaError):
        schema.Will.from_dict(data)


def test_emergency_will_assembles_without_llm():
    traits = Traits(
        bravery=7,
        greed=2,
        talkativeness=4,
        trust_in_chat_and_nodes=5,
        superstition=8,
        quirk="боится воды",
        seed=4213,
    )
    will = schema.emergency_will(
        serial=14,
        slot="LastOrder02",
        cause="stuck",
        born_at="2026-10-17T12:04:11+00:00",
        died_at="2026-10-17T12:41:53+00:00",
        traits=traits,
        last_action="шёл к болоту",
        last_events=["простой 90 с", "pathStatus: FAILED"],
        node_name="узел 00014",
        heir_name="узел 00015",
    )
    assert will.meta.emergency and will.meta.model == "template"
    assert will.meta.lifetime_s == 2262  # computed from the dates, not passed in
    lines = will.body.split("\n")
    assert 2 <= len(lines) <= 4
    assert "шёл к болоту" in will.body and "узел 00015" in will.body
    assert "4213" not in will.body  # the seed and trait numbers never reach the text
    for trait in ("bravery", "greed", "talkativeness", "superstition"):
        assert trait not in will.body
    assert schema.Will.from_dict(will.to_dict()) == will  # it is a valid will as-is


# --- reflection -----------------------------------------------------------------------


def test_reflection_death_requires_cause():
    with pytest.raises(schema.SchemaError):
        schema.ReflectionInput.from_dict({"serial": 14, "kind": "death"})
    out = schema.ReflectionInput.from_dict({"serial": 14, "kind": "death", "cause": "ttl"})
    assert out.cause == "ttl"


def test_reflection_output_caps_candidates_and_empty():
    candidates = [
        {"kind": "fact", "claim": f"факт {i}", "about": ["itm:bread"], "evidence_event_ids": ["e-1"]}
        for i in range(4)
    ]
    with pytest.raises(schema.SchemaError):
        schema.ReflectionOutput.from_dict({"core_memory": "## Цель\nЖить.", "candidates": candidates})
    with pytest.raises(schema.SchemaError):
        schema.ReflectionOutput.from_dict({})
    out = schema.ReflectionOutput.from_dict(
        {"personal_goal": "жить", "candidates": candidates[:3]}
    )
    assert len(out.candidates) == 3 and out.candidates[0].kind == "fact"


# --- budgets --------------------------------------------------------------------------


def test_block_budgets_fit_the_step_ceiling():
    blocks = {
        name: value
        for name, value in vars(budget).items()
        if name.startswith("B_") and isinstance(value, int)
    }
    assert len(blocks) == 10  # the ten blocks of section 9
    assert sum(blocks.values()) == 2390  # documented: 2390 + 110 spare
    assert sum(blocks.values()) <= budget.STEP_INPUT_BUDGET - 100


def test_estimate_tokens_is_conservative():
    assert budget.estimate_tokens("") == 0
    cyr = budget.estimate_tokens("привет" * 20)  # 120 cyrillic chars
    lat = budget.estimate_tokens("hello" * 20)  # 100 latin chars
    assert cyr == math.ceil(0.6 * 120) == 72
    assert lat == math.ceil(0.3 * 100) == 30
    assert cyr > lat
