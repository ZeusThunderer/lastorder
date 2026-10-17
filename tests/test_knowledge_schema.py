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
from runtime.lastorder.persona import Inheritance, Node, PersonaProvider
from runtime.lastorder.naming import PRESETS
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


def example_body(name: str) -> str:
    """The valid fenced example `name` from KNOWLEDGE.md (tests read the doc, not copies)."""
    for m in BLOCK.finditer(KNOWLEDGE_MD.read_text(encoding="utf-8")):
        if m.group(2) == name and not m.group(3):
            return m.group(4)
    raise AssertionError(f"no valid example {name!r} in KNOWLEDGE.md")


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
        last_events=["простой 90 с", "видел стонущего у воды"],  # post-perception text
        node_name="узел 00014",
        heir_address="следующий узел",  # the heir's serial is unknown at death
    )
    assert will.meta.emergency and will.meta.model == "template"
    assert will.meta.lifetime_s == 2262  # computed from the dates, not passed in
    lines = will.body.split("\n")
    assert 2 <= len(lines) <= 4
    assert "шёл к болоту" in will.body and "следующий узел" in will.body
    assert "4213" not in will.body  # the seed and trait numbers never reach the text
    for trait in ("bravery", "greed", "talkativeness", "superstition"):
        assert trait not in will.body
    assert schema.Will.from_dict(will.to_dict()) == will  # it is a valid will as-is


def test_emergency_will_survives_broken_inputs():
    """The last line before "a death without a will" never throws on realistic corruption."""
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
        cause="lava",  # unknown cause degrades to game
        born_at="2026-10-17T12:04:11",  # naive: read as UTC, no aware-naive TypeError
        died_at="2026-10-17T12:03:11+00:00",  # a second earlier: clock skew clamps to 0
        traits=traits,
        last_action="шёл к болоту\nи ждал у воды",  # newlines collapse to one line
        last_events=["первое", "второе\nсобытие", "", None, "третье", "четвёртое"],
        node_name="узел\n00014 с лишним уточнением откуда-то",  # every body field is one line
        heir_address="следующий узел",
    )
    assert will.meta.cause == "game" and will.meta.lifetime_s == 0
    assert will.meta.born_at == "2026-10-17T12:04:11+00:00"  # normalized, not crashed
    lines = will.body.split("\n")
    assert len(lines) == 4  # exactly the template: fields never add lines
    assert "третье" in will.body and "четвёртое" not in will.body  # at most 3 events
    assert "шёл к болоту и ждал у воды" in will.body  # one line, no embedded newline
    assert "это узел 00014 с лишним уточ;" in will.body  # one line, capped
    assert budget.estimate_tokens(will.body) <= budget.WILL_BODY_MAX_TOKENS
    # the file stays readable for the heir and T-012
    assert schema.Will.from_markdown(will.to_markdown()) == will


def test_emergency_will_worst_case_fits_will_cap():
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
        # every field over its cap, all Cyrillic (the token-heaviest case)
        last_action="а" * 200,
        last_events=["б" * 200] * 5,
        node_name="ж" * 200,
        heir_address="щ" * 200,
    )
    assert budget.estimate_tokens(will.body) <= budget.WILL_BODY_MAX_TOKENS


def test_will_missing_field_and_broken_yaml_are_schema_errors():
    data = _will_data()
    del data["model"]  # a missing top-level field must not escape as KeyError
    with pytest.raises(schema.SchemaError):
        schema.Will.from_dict(data)
    with pytest.raises(schema.SchemaError):  # broken YAML is wrapped, not yaml.YAMLError
        schema.Will.from_markdown("---\nserial: [oops\n---\nстрока\nстрока")
    with pytest.raises(schema.SchemaError):  # int and str unknown keys must not TypeError
        schema.Will.from_markdown("---\n1: a\nfoo: b\n---\nстрока\nстрока")


def test_will_body_token_cap():
    data = _will_data(body="\n".join(["уходить от воды и не возвращаться сюда обратно"] * 4))
    schema.Will.from_dict(data)  # ~110 tokens: fine
    huge = _will_data(body="\n".join(["у" * 95] * 4))  # > WILL_BODY_MAX_TOKENS
    with pytest.raises(schema.SchemaError):
        schema.Will.from_dict(huge)


def test_evidence_and_vote_to_dict_roundtrip():
    evidence = schema.Evidence.from_dict(
        {"node": 14, "event": None, "ts": "2026-10-17T12:03:40+00:00"}
    )
    assert schema.Evidence.from_dict(evidence.to_dict()) == evidence
    vote = schema.RecordVote.from_dict(
        {"node": 15, "accept": False, "why": "видел иначе", "ts": "2026-10-17T12:21:00+00:00"}
    )
    assert schema.RecordVote.from_dict(vote.to_dict()) == vote


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


def _blocks() -> dict[str, int]:
    return {
        name: value
        for name, value in vars(budget).items()
        if name.startswith("B_") and isinstance(value, int)
    }


def test_block_budgets_match_the_documented_total():
    blocks = _blocks()
    assert len(blocks) == 10  # the ten blocks of section 9
    assert sum(blocks.values()) == budget.STEP_BLOCKS_TOTAL
    # Honest sum, deliberately over the ceiling: the overflow (1360) is documented in
    # KNOWLEDGE.md section 9 and goes to "Предлагаемые ADR" — the ceiling is not raised
    # silently, and no constant here may paper over the conflict.
    assert budget.STEP_BLOCKS_TOTAL == 3860
    assert budget.STEP_BLOCKS_TOTAL > budget.STEP_INPUT_BUDGET


def test_persona_template_fits_persona_budget():
    """B_PERSONA is bound to its source: the rendered template with an empty inheritance,
    both naming presets (misaka has the longer speech style)."""
    node = Node(
        serial=14,
        traits=Traits(
            bravery=3,
            greed=7,
            talkativeness=5,
            trust_in_chat_and_nodes=4,
            superstition=9,
            quirk="считает крипера родственником",
            seed=4213,
        ),
    )
    for naming in PRESETS.values():
        prompt = PersonaProvider(naming=naming).system_prompt(node, Inheritance())
        assert budget.estimate_tokens(prompt) <= budget.B_PERSONA, naming.key


def test_core_limit_fits_core_budget():
    """B_CORE covers the byte-heaviest core.md: 1536 bytes of pure Cyrillic."""
    worst = "я" * (budget.CORE_MEMORY_LIMIT_BYTES // 2)  # 2 bytes per Cyrillic char
    assert len(worst.encode("utf-8")) <= budget.CORE_MEMORY_LIMIT_BYTES
    assert budget.estimate_tokens(worst) == 461 <= budget.B_CORE


def test_inheritance_budget_comes_from_quotas():
    """B_INHERITANCE is exactly the section-7 quotas: K wills at the cap + summaries + terms."""
    assert budget.B_INHERITANCE == (
        budget.WILL_BODY_MAX_TOKENS * budget.INHERITANCE_WILLS
        + budget.INHERITANCE_SUMMARY_TOKENS
        + budget.INHERITANCE_TERMS_TOKENS
    )
    assert budget.WILL_BODY_MAX_TOKENS * budget.INHERITANCE_WILLS <= budget.B_INHERITANCE


def test_primer_entries_fit_primer_budget():
    """B_PRIMER covers PRIMER_MAX_ENTRIES records the size of the document's own example."""
    claim = json.loads(example_body("PrimerEntry"))["claim"]
    assert (
        budget.PRIMER_MAX_ENTRIES * budget.estimate_tokens(claim) <= budget.B_PRIMER
    ), "a full primer block would not fit B_PRIMER"


# --- dedup (section 4) ----------------------------------------------------------------


def test_claim_dedup_rules():
    # 5-char truncation merges case endings: the same observation, reworded
    assert schema.claims_duplicate("стонущие бьют на сближении", "стонущий бьёт на сближении")
    # the document's own pair (record k-0042 vs the NETWORK.md section 5 note) is NOT a
    # duplicate: added words outweigh the shared stem, J = 4/7 < 0.6 — competing records
    claim_k42 = json.loads(example_body("Record"))["claim"]
    assert not schema.claims_duplicate(claim_k42, "стонущий бьёт на сближении")
    assert schema.jaccard(claim_k42, "стонущий бьёт на сближении") == pytest.approx(4 / 7)
    # negation stays a token: «не» is not dropped as a stop word
    assert "не" in schema.claim_tokens("не ходи к воде")


def test_estimate_tokens_is_conservative():
    assert budget.estimate_tokens("") == 0
    cyr = budget.estimate_tokens("привет" * 20)  # 120 cyrillic chars
    lat = budget.estimate_tokens("hello" * 20)  # 100 latin chars
    assert cyr == math.ceil(0.6 * 120) == 72
    assert lat == math.ceil(0.3 * 100) == 30
    assert cyr > lat
