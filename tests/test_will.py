"""write_will and the will prompt (KNOWLEDGE.md v2 sections 5 and 10; T-013 acceptance 3).

One smart call, a checked reply, and the emergency template behind every refusal: a
timeout, an exception, an empty or a broken reply each produce a valid will with
`emergency: true` and `model: template`. The prompt tests load the real
agent/last_order/will_prompt.md, so the template and the code cannot drift silently.
"""

from types import SimpleNamespace

import pytest

from runtime.knowledge.budget import WILL_INPUT_BUDGET, estimate_tokens
from runtime.knowledge.schema import ReflectionInput, TriggerEvent, Will
from runtime.lastorder.naming import preset
from runtime.lastorder.persona import PersonaError, Node
from runtime.lastorder.traits import Traits
from runtime.lastorder.will import (
    WillRejected,
    build_will_messages,
    check_will_body,
    heir_address,
    trait_numbers_in,
    write_will,
)
from runtime.llm.client import LLMError
from runtime.ports import BirthRecord

NAMING = preset("lab")

GOOD_BODY = (
    "Не ищите ночью гриб в каменном мешке: свет дороже гриба.\n"
    "Пятнистая колонна у воды взрывается; зовите её «семь» и обходите.\n"
    "Урок: факелы заготовить днём, до заката — в укрытие. Шутка: мне досталась жадность, я не просил."
)

BORN = "2026-10-17T12:04:11+00:00"
DIED = "2026-10-17T12:41:53+00:00"


def node() -> Node:
    return Node(serial=14, traits=Traits(3, 7, 5, 4, 9, quirk="считает крипера родственником", seed=4213))


def birth() -> BirthRecord:
    return BirthRecord(serial=14, born_at=BORN, ttl_min=45)


def reflection(cause: str = "game") -> ReflectionInput:
    return ReflectionInput(
        serial=14,
        kind="death",
        cause=cause,
        last_action="рубил берёзу у воды",
        notes=("стонующий бьёт на сближении",),
        core_memory="## Цель\nНайти глину.",
        personal_goal="найти глину у болота",
        network_goals=("сеть просит проверить болото",),
    )


class StubLLM:
    """complete_json with a scriptable reply or failure; config.model like LLMClient."""

    def __init__(self, reply=None, error: Exception | None = None, model: str = "stub/deepseek-test"):
        self.config = SimpleNamespace(model=model)
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.last_call = None

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        if isinstance(self.reply, dict):
            return self.reply
        raise AssertionError("stub has no reply scripted")


def write(llm, cause: str = "game", **over):
    kwargs = dict(
        node=node(),
        naming=NAMING,
        slot="LastOrder02",
        birth=birth(),
        cause=cause,
        died_at=DIED,
        reflection=reflection(cause),
    )
    kwargs.update(over)
    return write_will(llm, **kwargs)


# --- the one call and the happy path ---------------------------------------------------


def test_good_reply_becomes_a_will_with_full_frontmatter():
    result = write(StubLLM(reply={"will": GOOD_BODY}))
    assert result.emergency is False
    assert result.model == "stub/deepseek-test"
    assert result.error is None
    will = result.will
    assert will.meta.serial == 14
    assert will.meta.slot == "LastOrder02"
    assert will.meta.cause == "game"
    assert will.meta.emergency is False
    assert will.meta.model == "stub/deepseek-test"
    assert will.meta.traits.quirk == "считает крипера родственником"
    assert will.meta.born_at == BORN
    assert will.meta.died_at == DIED
    assert will.meta.lifetime_s == 2262
    assert will.body == GOOD_BODY
    # the file the hive store parses at retire
    assert Will.from_markdown(will.to_markdown()).body == GOOD_BODY


def test_exactly_one_smart_call():
    llm = StubLLM(reply={"will": GOOD_BODY})
    write(llm)
    assert len(llm.calls) == 1


# --- the four refusals: timeout, exception, empty, broken ------------------------------


def test_timeout_falls_back_to_the_emergency_will():
    result = write(StubLLM(error=LLMError("LLM call failed after 3 attempts: read timeout")))
    assert result.emergency is True
    assert result.model == "template"
    assert "LLMError" in result.error
    assert result.will.meta.emergency is True
    assert result.will.meta.model == "template"
    assert result.will.meta.cause == "game"


def test_unexpected_exception_falls_back_to_the_emergency_will():
    result = write(StubLLM(error=RuntimeError("connection reset by peer")))
    assert result.emergency is True
    assert "RuntimeError" in result.error
    Will.from_markdown(result.will.to_markdown())  # still a valid will.md


def test_empty_reply_falls_back_to_the_emergency_will():
    result = write(StubLLM(reply={}))
    assert result.emergency is True
    assert result.error is not None


def test_broken_reply_falls_back_to_the_emergency_will():
    result = write(StubLLM(reply={"will": "Всё, что я успел: ничего."}))  # one line only
    assert result.emergency is True
    assert result.will.meta.emergency is True


def test_reply_without_a_will_field_is_a_refusal():
    result = write(StubLLM(reply={"personal_goal": "дожить до заката", "core_memory": "..."}))
    assert result.emergency is True


def test_trait_numbers_in_the_body_are_a_refusal():
    body = "Мне досталась жадность 9, я не просил.\nВторая строка урока.\nШутка: цифры не врут."
    result = write(StubLLM(reply={"will": body}))
    assert result.emergency is True
    assert "trait numbers" in result.error


def test_raw_game_ids_in_the_body_are_a_refusal():
    body = (
        "Не рубите minecraft:birch_log ночью.\n"
        "Свет дороже гриба.\n"
        "Урок: факелы днём. Шутка: голова молчала."
    )
    result = write(StubLLM(reply={"will": body}))
    assert result.emergency is True
    assert "game ids" in result.error


def test_emergency_will_never_carries_trait_numbers_or_ids():
    result = write(StubLLM(error=LLMError("timeout")))
    assert not trait_numbers_in(result.will.body)  # the serial in the node name is fine
    assert "minecraft:" not in result.will.body
    lines = result.will.body.split("\n")
    assert 2 <= len(lines) <= 4
    assert "голова уже молчит" in result.will.body


# --- caller bugs raise, they do not become emergency wills ------------------------------


def test_unknown_cause_is_a_caller_bug():
    with pytest.raises(ValueError, match="unknown death cause"):
        write(StubLLM(reply={"will": GOOD_BODY}), cause="boredom")


def test_a_non_death_reflection_is_a_caller_bug():
    regular = ReflectionInput(serial=14, kind="regular", personal_goal="дожить")
    with pytest.raises(ValueError, match="death reflection"):
        write(StubLLM(reply={"will": GOOD_BODY}), reflection=regular)


def test_a_foreign_serial_in_the_reflection_is_a_caller_bug():
    foreign = ReflectionInput(serial=15, kind="death", cause="game")
    with pytest.raises(ValueError, match="does not match the dying node"):
        write(StubLLM(reply={"will": GOOD_BODY}), reflection=foreign)


# --- the prompt -------------------------------------------------------------------------


def test_prompt_is_rendered_from_the_real_template():
    llm = StubLLM(reply={"will": GOOD_BODY})
    write(llm)
    system, user = llm.calls[0]
    assert "узел 00014" in system  # the naming preset (ADR-018), not a hardcoded name
    assert "считает крипера родственником" in system  # the quirk
    assert "Reply in Russian" in system
    assert '"kind": "death"' in user
    assert '"cause": "game"' in user
    assert '"last_action": "рубил берёзу у воды"' in user


def test_prompt_has_no_leftover_placeholder():
    llm = StubLLM(reply={"will": GOOD_BODY})
    write(llm)
    system, _ = llm.calls[0]
    assert "{" not in system.split('{"will"')[0]


def test_input_is_trimmed_to_the_budget_by_dropping_oldest_events():
    events = tuple(
        TriggerEvent(
            type="damage.taken",
            importance=4,
            event_id=f"e-{i}",
            ts="2026-10-17T12:05:12+00:00",
            payload={"from": "ent:zombie", "amount": 3},
        )
        for i in range(300)
    )
    reflection = ReflectionInput(serial=14, kind="death", cause="game", events=events)
    _, user = build_will_messages(node(), NAMING, reflection)
    assert estimate_tokens(user) <= WILL_INPUT_BUDGET
    assert '"e-299"' in user  # the last events stay
    assert '"e-0"' not in user  # the oldest go first


def test_input_over_budget_with_nothing_to_drop_is_a_refusal():
    huge = ReflectionInput(
        serial=14,
        kind="death",
        cause="game",
        core_memory="факт " * 2000,
    )
    with pytest.raises(WillRejected, match="no events or notes left"):
        build_will_messages(node(), NAMING, huge)


# --- small pieces ------------------------------------------------------------------------


def test_trait_number_detection():
    assert trait_numbers_in("мне досталась жадность 9, я не просил")
    assert trait_numbers_in("greed: 8 was my condition")
    assert trait_numbers_in("храбрость — 10 из 10")
    assert not trait_numbers_in("мне досталась жадность, я не просил")
    assert not trait_numbers_in("держи три шага и не жми к стене")
    assert not trait_numbers_in("факелы заготовить днём, до заката — в укрытие")


def test_heir_address_follows_the_naming_preset():
    assert heir_address(preset("lab")) == "следующий узел"
    assert heir_address(preset("misaka")) == "следующая Мисака"
    assert heir_address(SimpleNamespace(key="future")) == "наследник"


def test_check_will_body_accepts_the_documented_example():
    check_will_body(GOOD_BODY)
    check_will_body(GOOD_BODY + "\nчетвёртая строка тоже уместна")  # 4 lines is the cap
    for bad in ("одна строка", "a\n\nb", GOOD_BODY + "\nчетвёртая\nпятая"):
        with pytest.raises(WillRejected):
            check_will_body(bad)


def test_template_placeholder_mismatch_is_an_error():
    # render() of persona.py: a placeholder drift between will_prompt.md and will.py
    # must be loud, not a silent prompt with braces in it.
    with pytest.raises(PersonaError, match="missing"):
        build_will_messages(node(), NAMING, reflection(), template="Stray {unknown} placeholder")
    with pytest.raises(PersonaError, match="unused"):
        build_will_messages(node(), NAMING, reflection(), template="Only {node_name} here")
