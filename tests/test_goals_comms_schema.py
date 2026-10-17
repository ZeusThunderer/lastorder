"""GOALS_COMMS.md: the schemas its examples promise, and the examples must parse.

Every ```json fence marked with a ``<!-- schema:... -->`` comment in the document is a
canonical payload and must build a valid object; the set of markers must cover every
JSON-crossing schema, so the document and the code cannot drift apart. Mutations of the
canonical payloads must raise, so a broken smart reply or chatter line can never pass as
valid. The council and the director themselves are T-016/T-017.
"""

import json
import re
from pathlib import Path

import pytest

from runtime.comms import schema as comms
from runtime.hive import goals_schema as goals

DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture" / "GOALS_COMMS.md"

MARKER = re.compile(r"<!-- schema:([\w-]+) -->\s*\n```json\n(.*?)```", re.S)
ANY_JSON_FENCE = re.compile(r"```json\n.*?```", re.S)

PARSERS = {
    "council-in": goals.CouncilInput.from_dict,
    "council-out": goals.CouncilOutput.from_dict,
    "living-status": goals.LivingStatus.from_dict,
    "goal-set": goals.goal_set_from_list,
    "msg": comms.OutboundMessage.from_dict,
    "chatter-reply": comms.ChatterReply.from_dict,
    "chatter-line": comms.ChatterLine.from_dict,
}

DOC_LINE_LIMIT = 300  # the task's own constraint: dry document, at most 300 lines


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def marked_examples() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for kind, payload in MARKER.findall(doc_text()):
        out.setdefault(kind, []).append(json.loads(payload))
    return out


# --- the document and its canonical examples --------------------------------------------


def test_document_exists_and_keeps_the_line_limit():
    lines = doc_text().splitlines()
    assert 100 <= len(lines) <= DOC_LINE_LIMIT


def test_every_json_fence_is_marked_and_every_parser_is_exercised():
    examples = marked_examples()
    assert set(examples) == set(PARSERS), "document markers and parsers must cover each other"
    total_marked = sum(len(v) for v in examples.values())
    assert total_marked == len(ANY_JSON_FENCE.findall(doc_text())), "an unmarked json fence"


@pytest.mark.parametrize("kind", sorted(PARSERS))
def test_marked_examples_parse(kind):
    for payload in marked_examples()[kind]:
        obj = PARSERS[kind](payload)
        assert obj is not None


def test_document_names_every_limit_constant():
    text = doc_text()
    for module in (goals, comms):
        for name, value in vars(module).items():
            if (
                not name.startswith("_")
                and name.isupper()
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                assert name in text, f"{name} is missing from GOALS_COMMS.md"


def test_document_routes_node_free_text_through_perception():
    """GOALS_COMMS.md must pin the masked path for text another node or the council wrote."""
    text = doc_text()
    assert text.count("runtime/perception") == 2  # 1.4 network goals, 5 chatter and net text
    assert text.count("утечк") == 2  # the leak test is named for T-016 and for T-017


# --- limit values the document promises (NETWORK.md section 6, user answer 2026-10-16) ---


def test_registry_limits_match_network_section_6():
    assert goals.COUNCIL_SMART_PER_HOUR == 4
    assert goals.MAX_NETWORK_GOALS == 3
    assert comms.CHATTER_CALLS_PER_HOUR == 30
    assert comms.CHATTER_CALLS_PER_NODE_PER_HOUR < comms.CHATTER_CALLS_PER_HOUR
    assert comms.SAY_MIN_INTERVAL_S == 20


def test_ladder_is_ordered_and_unique():
    assert goals.LADDER[0] == "survive"
    assert goals.LADDER[-1] == "shared"
    assert len(set(goals.LADDER)) == len(goals.LADDER)


# --- the pass-merge example of GOALS_COMMS.md 1.2 (findings R2-1) -------------------------


def _record(record_id, text, expires_at, source="rule", signal_refs=()):
    return {
        "id": record_id,
        "text": text,
        "why": f"why of {record_id}",
        "source": source,
        "created_at": "2026-10-17T11:15:00Z",
        "expires_at": expires_at,
        "signal_refs": list(signal_refs),
    }


def test_goal_set_example_pins_the_merge_rule():
    """Document order: the set before the pass, then the set after it (GOALS_COMMS.md 1.2)."""
    before_raw, after_raw = marked_examples()["goal-set"]
    before = goals.goal_set_from_list(before_raw)
    after = goals.goal_set_from_list(after_raw)
    # new goals of the pass come first (renewed g-0001, new g-0004), inherited last (g-0002)
    assert [r.id for r in after] == ["g-0001", "g-0004", "g-0002"]
    assert len(after) == goals.MAX_NETWORK_GOALS
    old = next(r for r in before if r.id == "g-0001")
    renewed = next(r for r in after if r.id == "g-0001")
    assert renewed.created_at == old.created_at  # text match renews, it is not a new record
    assert renewed.expires_at == "2026-10-17T13:15:00Z"  # now + GOAL_TTL_MIN
    assert all(r.id != "g-0003" for r in after)  # displaced by the cut
    inherited = next(r for r in after if r.id == "g-0002")
    untouched = next(r for r in before if r.id == "g-0002")
    assert inherited == untouched  # inherited records are carried over as they are


@pytest.mark.parametrize(
    "mutation",
    [
        lambda items: items.extend(
            [
                _record("g-0004", "четвёртая цель", "2026-10-17T13:15:00Z"),
                _record("g-0005", "пятая цель", "2026-10-17T13:15:00Z"),
            ]
        ),
        lambda items: items.append("not an object"),
        lambda items: items[0].update(source="llm"),
    ],
)
def test_goal_set_rejects_broken_stores(mutation):
    items = [
        _record("g-0001", "первая цель", "2026-10-17T13:15:00Z"),
        _record("g-0002", "вторая цель", "2026-10-17T12:30:00Z"),
    ]
    mutation(items)
    with pytest.raises(goals.GoalSchemaError):
        goals.goal_set_from_list(items)


def test_goal_set_rejects_non_lists():
    with pytest.raises(goals.GoalSchemaError):
        goals.goal_set_from_list({"id": "g-0001"})


def test_goal_set_accepts_an_empty_active_set():
    assert goals.goal_set_from_list([]) == ()


# --- invalid payloads are rejected, one mutation at a time --------------------------------


def _council_out():
    return {
        "goals": [
            {"text": "обезопасить сеть: ночной стон", "why": "3 смертей за 120 мин"},
            {"text": "найти и записать стабильный источник еды", "why": "ступень food не взята"},
        ]
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["goals"].extend([{"text": "третья", "why": "x"}, {"text": "четвёртая", "why": "y"}]),
        lambda d: d.update(goals=[]),
        lambda d: d["goals"][0].update(text="x" * 121),
        lambda d: d["goals"][0].update(why=""),
        lambda d: d.update(goals={"text": "не список"}),
    ],
)
def test_council_output_rejects_broken_replies(mutation):
    data = _council_out()
    mutation(data)
    with pytest.raises(goals.GoalSchemaError):
        goals.CouncilOutput.from_dict(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(current_step="boss"),
        lambda d: d.update(gap_step="netherite"),
        lambda d: d.update(deaths={"cause": "game:лава"}),
        lambda d: d["deaths"].append({"cause": "game:лава", "count": 0}),
        lambda d: d["gaps"].append({"kind": "lore", "about": "x", "status": ""}),
        lambda d: d["gaps"].append({"kind": "danger", "about": "x", "status": "confirmed"}),
        lambda d: d["gaps"].append({"kind": "danger", "about": "x", "status": 7}),
    ],
)
def test_council_input_rejects_broken_signals(mutation):
    data = {
        "current_step": "food",
        "gap_step": "safety",
        "deaths": [{"cause": "game:ночной стон", "count": 3}],
        "gaps": [{"kind": "danger", "about": "ent:3c1d", "status": "hypothesis"}],
        "current_goals": [{"text": "найти еду", "why": "ступень food не взята"}],
    }
    mutation(data)
    with pytest.raises(goals.GoalSchemaError):
        goals.CouncilInput.from_dict(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(tool_tier="diamond"),
        lambda d: d.update(food_count=-1),
        lambda d: d.update(serial=0),
        lambda d: d.update(pos=[120, 64]),
        lambda d: d.update(weapon="yes"),
    ],
)
def test_living_status_rejects_broken_reports(mutation):
    data = {
        "serial": 14,
        "slot": "LastOrder02",
        "food_count": 5,
        "tool_tier": "stone",
        "weapon": True,
        "armor": False,
        "pos": [120, 64, -30],
        "updated_at": "2026-10-17T12:00:00Z",
    }
    mutation(data)
    with pytest.raises(goals.GoalSchemaError):
        goals.LivingStatus.from_dict(data)


def test_goal_signal_and_record_validate():
    ok_signal = goals.GoalSignal.from_dict(
        {"kind": "deaths", "detail": "deaths:game:ночной стон", "weight": 3, "refs": ["14"]}
    )
    assert ok_signal.weight == 3.0
    with pytest.raises(goals.GoalSchemaError):
        goals.GoalSignal.from_dict({"kind": "vibe", "detail": "x", "weight": 1})
    with pytest.raises(goals.GoalSchemaError):
        goals.GoalSignal.from_dict({"kind": "deaths", "detail": "x", "weight": -1})
    record = goals.NetworkGoalRecord.from_dict(
        {
            "id": "g-0001",
            "text": "найти и записать стабильный источник еды",
            "why": "3 смерти game:голода за 120 мин",
            "source": "rule",
            "created_at": "2026-10-17T12:00:00Z",
            "expires_at": "2026-10-17T13:00:00Z",
            "signal_refs": ["deaths:game:голода"],
        }
    )
    assert record.source == "rule"
    with pytest.raises(goals.GoalSchemaError):
        goals.NetworkGoalRecord.from_dict(
            {**record.__dict__, "source": "llm", "signal_refs": ["deaths:game:голода"]}
        )
    aliased = goals.NetworkGoalRecord.from_dict(
        {
            "id": "g-0002",
            "text": "обезопасить сеть: лава",
            "why": "2 смертей за 120 мин",
            "source": "rule",
            "created_at": "2026-10-17T11:30:00Z",
            "expires_at": "2026-10-17T12:30:00Z",
            "refs": ["deaths:game:лава"],
        }
    )
    assert aliased.signal_refs == ()  # "refs" is not a synonym for "signal_refs"


def _msg():
    return {"to": 8, "channel": "net", "type": "warning", "text": "у спавна стонущий"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(channel="shout"),
        lambda d: d.update(type="order"),  # messages are observations, never commands
        lambda d: d.update(to="everyone"),
        lambda d: d.update(to=0),
        lambda d: d.update(text=""),
        lambda d: d.update(text="д" * 161),
        lambda d: (d.pop("type"), d.update(msg_type="warning")),  # "msg_type" is not a key
    ],
)
def test_outbound_message_rejects_broken_fields(mutation):
    data = _msg()
    mutation(data)
    with pytest.raises(comms.CommsSchemaError):
        comms.OutboundMessage.from_dict(data)


def test_chat_channel_is_public_all_only():
    ok = comms.OutboundMessage.from_dict(
        {"to": "all", "channel": "chat", "type": "claim", "text": "ферму веду я"}
    )
    assert ok.to == comms.ALL_ADDRESSEE
    with pytest.raises(comms.CommsSchemaError):
        comms.OutboundMessage.from_dict(
            {"to": 8, "channel": "chat", "type": "claim", "text": "ферму веду я"}
        )


def _reply():
    return {"text": "Запись без наблюдения. Проверю сама.", "stance": "disagree"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(stance="yes"),
        lambda d: d.update(text=""),
        lambda d: d.update(text="с" * 201),
    ],
)
def test_chatter_reply_rejects_broken_lines(mutation):
    data = _reply()
    mutation(data)
    with pytest.raises(comms.CommsSchemaError):
        comms.ChatterReply.from_dict(data)


def _line():
    return {
        "thread_id": "th-0007",
        "serial": 14,
        "text": "Запись без наблюдения. Проверю сама.",
        "stance": "disagree",
        "ts": "2026-10-17T12:04:11Z",
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(stance="maybe"),
        lambda d: d.update(serial=0),
        lambda d: d.update(thread_id=""),
    ],
)
def test_chatter_line_rejects_broken_lines(mutation):
    data = _line()
    mutation(data)
    with pytest.raises(comms.CommsSchemaError):
        comms.ChatterLine.from_dict(data)


def _thread(lines):
    return {
        "thread_id": "th-0007",
        "channel": "net",
        "topic": "Спор о записи k-0042",
        "quote": "Стонущий прямоходящий бьёт на сближении",
        "quote_entry_id": "k-0042",
        "event": "knowledge",
        "opened_at": "2026-10-17T12:03:50Z",
        "lines": lines,
        "closed_at": "",
    }


def test_chatter_thread_accepts_a_quoted_knowledge_thread():
    obj = comms.ChatterThread.from_dict(_thread([_line()]))
    assert obj.quote_entry_id == "k-0042"
    assert obj.lines[0].thread_id == obj.thread_id


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(event="gossip"),
        lambda d: d.update(channel="irc"),
        lambda d: d.update(quote=""),
        lambda d: d.update(quote_entry_id=""),
        lambda d: d["lines"].append({**_line(), "thread_id": "th-9999"}),
        lambda d: d.update(lines=[_line() for _ in range(comms.CHATTER_THREAD_MAX_LINES + 1)]),
    ],
)
def test_chatter_thread_rejects_broken_state(mutation):
    data = _thread([_line()])
    mutation(data)
    with pytest.raises(comms.CommsSchemaError):
        comms.ChatterThread.from_dict(data)


def test_silence_thread_does_not_need_a_quote():
    obj = comms.ChatterThread.from_dict(
        {**_thread([]), "event": "silence", "channel": "chat", "quote": "", "quote_entry_id": ""}
    )
    assert obj.event == "silence"
