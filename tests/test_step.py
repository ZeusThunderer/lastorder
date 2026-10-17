"""build_step_prompt and parse_step_reply (KNOWLEDGE.md v2 section 9, NETWORK.md section 5).

Every acceptance criterion of T-011 has a test: block order and budgets, the prefix-cache
invariant, the ceiling (ADR-029), leaks and service lines in both perception modes, and the
reply parsing rules (valid, partial, broken payloads, foreign option_id, garbage).
"""

import pytest

from runtime.comms.schema import OutboundMessage
from runtime.knowledge.budget import (
    B_ABOUT,
    B_COMMS,
    B_CORE,
    B_PERSONA,
    B_PRIMER,
    B_VOTES,
    PRIMER_MAX_ENTRIES,
    STEP_INPUT_BUDGET,
    estimate_tokens,
)
from runtime.lastorder.naming import PRESETS
from runtime.lastorder.persona import NOTHING_YET, PRIMER_HEAD, Inheritance, Node, PersonaProvider
from runtime.lastorder.step import StepError, build_step_prompt, parse_step_reply
from runtime.lastorder.traits import Traits
from runtime.perception.chat import SERVICE_PREFIXES
from runtime.perception.translate import Perception
from runtime.ports import (
    KnowledgeEntry,
    KnowledgeSlice,
    NetworkGoal,
    NewTerm,
    NodeMessage,
    Option,
    StepReply,
    Vote,
)

DESCRIPTIONS = {"zombie": "стонущее прямоходящее", "birch_log": "светлый столб с тёмными штрихами"}

OPTIONS = [
    Option(id="fell_birch", title="рубить берёзу рядом", lever="chop"),
    Option(id="eat_bread", title="съесть хлеб", lever="eat"),
    Option(id="back_off_zombie", title="отойти от зомби", lever="move"),
]

OBSERVATION = (
    '{"self": {"hp": 8, "food": 14, "pos": [120, 64, -30], "held": "itm:wooden_axe"},\n'
    ' "entities": [{"ref": "ent:zombie", "dist_m": 9}], "blocks": [{"ref": "blk:birch_log"}],\n'
    ' "inventory": [{"ref": "itm:bread", "count": 2}], "events": []}'
)


def node() -> Node:
    return Node(serial=7, traits=Traits(5, 6, 4, 5, 3, quirk="любит светлые деревья", seed=11))


def inheritance() -> Inheritance:
    return Inheritance(
        predecessors_summary="00001 game:zombie 8 мин",
        active_nodes_summary="00008 LastOrder02",
        inherited_notes="Узлу 00007: стонущие подходят вплотную.",
        collective_terms="«пятнистая колонна» — blk:birch_log",
        knowledge_catalog="k-0001 [fact] еда восстанавливает здоровье",
    )


def slice_(**over) -> KnowledgeSlice:
    base = dict(
        catalog="k-0001 [fact] еда восстанавливает здоровье",
        network_goals=(NetworkGoal(text="найти источник еды", why="ступень food не взята"),),
        about=(KnowledgeEntry(id="k-0002", kind="danger", claim="стонущий бьёт на сближении", status="confirmed", about=("ent:zombie",)),),
        fresh=(KnowledgeEntry(id="k-0003", kind="fact", claim="берёза рубится топором", status="hypothesis", about=("blk:birch_log",)),),
        pending_votes=(KnowledgeEntry(id="k-0004", kind="danger", claim="ночь без света опасна", status="hypothesis"),),
    )
    base.update(over)
    return KnowledgeSlice(**base)


def build(mode="plain", perception=None, **over):
    kwargs = dict(
        node=node(),
        inheritance=inheritance(),
        slice=slice_(),
        core_memory="goal: дерево на верстак",
        observation=OBSERVATION,
        options=OPTIONS,
        inbox=(NodeMessage(from_serial=8, to_serial=7, text="я рублю западнее"),),
        chat=("<LastOrder02> у меня хлеб есть",),
        mode=mode,
        primer=("Ночь опасна: без света у двери собираются враждебные.", "Кровать задаёт точку возрождения."),
    )
    kwargs.update(over)
    if mode == "masked" and perception is None:
        perception = Perception(DESCRIPTIONS, salt="t", mode="masked")
    if perception is not None:
        kwargs["perception"] = perception
    return build_step_prompt(**kwargs)


# --- assembly: order, cache, ceiling ----------------------------------------------------


def test_blocks_follow_section_9_order():
    system, user = build()
    heads_sys = [
        "# Knowledge rule",
        PRIMER_HEAD,
        "# Node variables",
        "# Collective context",
        "# Network knowledge catalog",
        "# Core memory",
        "# Network council",
    ]
    at = [system.find(h) for h in heads_sys]
    assert all(i >= 0 for i in at), [h for h, i in zip(heads_sys, at) if i < 0]
    assert at == sorted(at)
    heads_user = [
        "# Known about what you see",
        "# The network asks you to evaluate",
        "# Node messages and chat quotes",
        "# Observation now",
        "# Options",
    ]
    at = [user.find(h) for h in heads_user]
    assert all(i >= 0 for i in at)
    assert at == sorted(at)
    assert OBSERVATION not in system  # per-step content stays out of the cached prefix


def test_masked_omits_primer_but_keeps_the_rule():
    system, _ = build(mode="masked", primer=("Ночь опасна",))
    assert PRIMER_HEAD not in system
    assert "do NOT know this world" in system
    plain_system, _ = build()
    assert PRIMER_HEAD in plain_system and "Ночь опасна" in plain_system
    assert "do NOT know this world" not in plain_system


def test_system_is_stable_across_steps():
    first = build()[0]
    second = build(
        slice=slice_(
            about=(),
            fresh=(KnowledgeEntry(id="k-0009", kind="fact", claim="другой хвост", status="hypothesis"),),
            pending_votes=(),
        ),
        observation='{"self": {"hp": 20}}',
        options=[Option(id="wait_look", title="стоять", lever="move")],
        inbox=(),
        chat=("<LastOrder03> другое",),
    )[0]
    assert first == second  # cache prefix condition: only core and goals may move it


def test_template_fits_persona_budget_in_both_modes():
    for naming in PRESETS.values():
        for mode in ("plain", "masked"):
            prompt = PersonaProvider(naming).system_prompt(node(), Inheritance(), mode=mode)
            assert estimate_tokens(prompt) <= B_PERSONA, (naming.key, mode)


def test_full_prompt_under_ceiling():
    system, user = build(mode="masked")
    assert estimate_tokens(system + user) <= STEP_INPUT_BUDGET


# --- block budgets and the trim order ---------------------------------------------------


def test_primer_block_fits_budget():
    entries = [f"Запись праймера номер {i}: длинное правило про выживание в этом мире." for i in range(30)]
    system, _ = build(primer=entries)
    block = system.split(PRIMER_HEAD, 1)[1].split("#", 1)[0]
    lines = [l for l in block.splitlines() if l.strip()]
    assert len(lines) <= PRIMER_MAX_ENTRIES
    assert estimate_tokens(PRIMER_HEAD + "\n" + "\n".join(lines)) <= B_PRIMER


def test_core_block_fits_budget():
    system, _ = build(core_memory="факт про мир. " * 300)
    block = system.split("# Core memory (yours)\n", 1)[1].split("\n#", 1)[0]
    assert estimate_tokens(block) <= B_CORE
    empty_system, _ = build(core_memory="   ")
    assert NOTHING_YET in empty_system


def test_about_and_fresh_fit_budget_and_fresh_trims_first():
    heavy = slice_(
        about=tuple(
            KnowledgeEntry(id=f"k-a{i:03d}", kind="fact", claim=f"длинное наблюдение о стонущем номер {i}" * 3, status="confirmed", about=("ent:zombie",))
            for i in range(12)
        ),
        fresh=tuple(
            KnowledgeEntry(id=f"k-f{i:03d}", kind="fact", claim=f"свежая запись базы номер {i}" * 3, status="hypothesis")
            for i in range(10)
        ),
    )
    _, user = build(slice=heavy)
    block = user.split("# Known about what you see\n", 1)[1].split("\n\n#", 1)[0]
    assert estimate_tokens(block) <= B_ABOUT
    lines = [l for l in block.splitlines() if l.strip()]
    assert any("длинное наблюдение" in l for l in lines)  # about survives longest


def test_votes_fit_budget():
    many = slice_(
        pending_votes=tuple(
            KnowledgeEntry(id=f"k-v{i:03d}", kind="danger", claim=f"кандидат на голосование номер {i}" * 4, status="hypothesis")
            for i in range(6)
        )
    )
    _, user = build(slice=many)
    block = user.split("# The network asks you to evaluate\n", 1)[1].split("\n\n#", 1)[0]
    assert estimate_tokens(block) <= B_VOTES


def test_comms_block_fits_budget_and_oldest_chat_trims_first():
    chat = [f"<LastOrder0{i % 3 + 1}> реплика номер {i} про то и это" for i in range(60)]
    inbox = tuple(NodeMessage(from_serial=8 + i, to_serial=7, text=f"сообщение узла номер {i}") for i in range(10))
    _, user = build(chat=chat, inbox=inbox)
    block = user.split("# Node messages and chat quotes", 1)[1].split("\n\n#", 1)[0]
    assert estimate_tokens(block) <= B_COMMS
    assert "реплика номер 0" not in block  # the oldest chat goes first
    assert "сообщение узла" in block      # messages outlive chat in the trim order


def test_unfittable_observation_raises():
    with pytest.raises(ValueError, match="STEP_INPUT_BUDGET"):
        build(slice=slice_(about=(), fresh=(), pending_votes=(), network_goals=()), core_memory="", inbox=(), chat=(), observation='{"self": ' + '"x"' * 20000 + "}")


def test_big_goals_are_kept_whole_until_the_ceiling_forces_an_error():
    """Block 6 is never trimmed (section 9): big goals stay whole in the system part while
    the refillable blocks give way; a goal set too big to fit at all is an explicit error,
    not a silently truncated council."""
    big = tuple(
        NetworkGoal(
            text=f"цель {i}: обезопасить сеть от ночных стонущих у периметра и записать итог в общую базу",
            why="много смертей за окно, совет требует реакции",
        )
        for i in range(10)
    )
    system, user = build(slice=slice_(network_goals=big))
    assert "цель 9:" in system  # every goal arrived whole
    assert "стонущий бьёт" in user  # and the refillable blocks are still there
    assert estimate_tokens(system + "\n" + user) <= STEP_INPUT_BUDGET
    with pytest.raises(ValueError, match="STEP_INPUT_BUDGET"):
        build(slice=slice_(network_goals=(NetworkGoal(text="жить и наблюдать " * 3000, why=""),)))


def test_ceiling_trim_keeps_the_first_about_and_the_first_candidate():
    """Section-9 trim order under the ceiling: the fresh tail goes first, about shrinks to
    the most relevant entry, vote candidates to the first one, and only then the oldest
    chat; messages outlive chat; the last about entry and the last candidate go only after
    everything else (beyond section 9). The goal size is picked so the trim walks exactly
    into that middle stage."""
    heavy_goals = tuple(
        NetworkGoal(text=f"цель {i}: " + "обезопасить периметр сети и записать итог в базу знаний " * 2,
                    why="совет требует реакции и проверки")
        for i in range(33)
    )
    heavy_slice = slice_(
        network_goals=heavy_goals,
        about=tuple(
            KnowledgeEntry(id=f"k-a{i:03d}", kind="fact", claim=f"наблюдение о стонущем номер {i}" * 2, status="confirmed", about=("ent:zombie",))
            for i in range(4)
        ),
        fresh=(KnowledgeEntry(id="k-fresh", kind="fact", claim="свежий хвост базы", status="hypothesis"),),
        pending_votes=(
            KnowledgeEntry(id="k-v1", kind="danger", claim="первый кандидат: ночь без света", status="hypothesis"),
            KnowledgeEntry(id="k-v2", kind="danger", claim="второй кандидат: лава под тонким слоем", status="hypothesis"),
        ),
    )
    system, user = build(
        slice=heavy_slice,
        chat=(f"<LastOrder0{i}> старая реплика номер {i} про разное" for i in range(5)),
        inbox=(NodeMessage(from_serial=8, to_serial=7, text="сообщение узла"),),
    )
    assert estimate_tokens(system + "\n" + user) <= STEP_INPUT_BUDGET
    assert "k-fresh" not in user      # the fresh tail was trimmed first
    assert "k-a000" in user and "k-a001" not in user  # about shrank to the most relevant
    assert "k-v1" in user and "k-v2" not in user      # candidates shrank to the first
    assert "старая реплика номер 4" in user           # ...while chat was still there:
    assert "старая реплика номер 0" not in user       # its oldest lines went after votes
    assert "сообщение узла" in user   # messages outlive chat in the trim order


# --- perception hygiene (ADR-020, ADR-025) -----------------------------------------------


def test_masked_prompt_has_no_leaks():
    p = Perception(DESCRIPTIONS, salt="t", mode="masked")
    p.see("zombie", "entity")
    p.see("birch_log", "block")
    dirty_slice = slice_(
        network_goals=(NetworkGoal(text="обезопасить сеть: zombie у спавна", why="minecraft:zombie в логах"),),
        about=(KnowledgeEntry(id="k-0002", kind="danger", claim="zombie бьёт на сближении, birch log не спасает", status="confirmed", about=(p.handle("zombie", "entity"),)),),
        fresh=(KnowledgeEntry(id="k-0003", kind="fact", claim="minecraft:birch_log рубится топором", status="hypothesis"),),
    )
    dirty_inheritance = Inheritance(
        predecessors_summary="00001 game:zombie 8 мин, около birch log",
        active_nodes_summary="00008 LastOrder02 у zombie",
        inherited_notes="Узлу 00007: zombie приходит ночью, birch log не стена.",
        collective_terms="«пятнистая колонна» — minecraft:birch_log",
        knowledge_catalog="k-0001 [fact] zombie носит birch log",
    )
    system, user = build(
        mode="masked",
        perception=p,
        slice=dirty_slice,
        inheritance=dirty_inheritance,
        core_memory="goal: обойти zombie и minecraft:birch_log",
        chat=("<LastOrder02> zombie was slain near minecraft:birch_log",),
        inbox=(NodeMessage(from_serial=8, to_serial=7, text="birch log рядом с водой, zombie тоже"),),
    )
    assert p.leaks(system + user) == []


def test_plain_namespaced_forms_are_normalized():
    """`minecraft:` is a leak in plain too (ADR-020): free texts go through perception.scrub
    when one is passed, and through NAMESPACED+normalize when the plain call has none."""
    dirty_slice = slice_(
        network_goals=(NetworkGoal(text="обезопасить сеть: ночной стон", why="minecraft:zombie в логах"),),
    )
    dirty_inheritance = inheritance().__class__(
        predecessors_summary="00001 game:стон 8 мин",
        active_nodes_summary="00008 LastOrder02",
        inherited_notes="Узлу 00007: minecraft:birch_log не стена.",
        collective_terms="«пятнистая колонна» — blk:birch_log",
        knowledge_catalog="k-0001 [fact] еда лечит",
    )
    kwargs = dict(
        slice=dirty_slice,
        inheritance=dirty_inheritance,
        core_memory="goal: minecraft:birch_log на верстак",
        chat=("<LastOrder02> дай minecraft:bread",),
        inbox=(NodeMessage(from_serial=8, to_serial=7, text="я рублю minecraft:birch_log"),),
    )
    with_perception = Perception(DESCRIPTIONS, salt="t", mode="plain")
    system, user = build(mode="plain", perception=with_perception, **kwargs)
    assert with_perception.leaks(system + user) == []
    assert "minecraft:" not in system + user
    system, user = build(mode="plain", **kwargs)  # no perception: at least normalized
    assert "minecraft:" not in system + user
    assert "birch_log" in system  # the normalized form stays readable


def test_service_lines_never_reach_the_prompt():
    dirty = [
        "§a[Alto Clef] task «get birch_log 8» started",
        "[Tungsten] path computed",
        "WARNING: cached chunk gone",
        "<LastOrder02> у меня хлеб есть, дам два",
    ]
    for mode in ("plain", "masked"):
        _, user = build(mode=mode, chat=dirty)
        assert all(prefix not in user for prefix in SERVICE_PREFIXES)
        assert "у меня хлеб есть" in user


def test_masked_without_perception_is_an_error():
    with pytest.raises(ValueError, match="Perception"):
        build_step_prompt(
            node(), inheritance(), slice_(), "goal: жить", OBSERVATION, OPTIONS, (), (), "masked"
        )
    mismatched = Perception(DESCRIPTIONS, salt="t", mode="plain")
    with pytest.raises(ValueError, match="does not match"):
        build(mode="masked", perception=mismatched)


# --- parse_step_reply --------------------------------------------------------------------


def test_parse_valid_full_reply():
    reply = parse_step_reply(
        '```json\n{"option_id": "eat_bread", "why": "голоден", '
        '"say": "Хлеб мой. Ем, сказала реплика.", '
        '"note": "еда восстанавливает здоровье", '
        '"new_term": {"handle": "ent:3c1d", "term": "ночной стон"}, '
        '"msg": {"to": 8, "channel": "net", "type": "warning", "text": "у спавна стонущий"}, '
        '"votes": [{"entry_id": "k-0004", "accept": true, "why": "видел то же"}]}\n```',
        OPTIONS,
    )
    assert reply == StepReply(
        option_id="eat_bread",
        why="голоден",
        say="Хлеб мой. Ем, сказала реплика.",
        note="еда восстанавливает здоровье",
        new_term=NewTerm(handle="ent:3c1d", term="ночной стон"),
        msg=OutboundMessage(to=8, channel="net", msg_type="warning", text="у спавна стонущий"),
        votes=(Vote(entry_id="k-0004", accept=True, why="видел то же"),),
    )


def test_parse_partial_reply():
    reply = parse_step_reply('{"option_id": "fell_birch", "why": "нужен верстак"}', OPTIONS)
    assert isinstance(reply, StepReply)
    assert reply.option_id == "fell_birch" and reply.why == "нужен верстак"
    assert reply.say is None and reply.msg is None and reply.votes == ()


def test_parse_ignores_unknown_fields():
    reply = parse_step_reply('{"option_id": "eat_bread", "why": "", "junk": [1, 2], "extra": "x"}', OPTIONS)
    assert isinstance(reply, StepReply) and reply.option_id == "eat_bread"


def test_parse_clips_long_say():
    say = "Один. Два. Три. Четыре. Пять."
    reply = parse_step_reply('{"option_id": "eat_bread", "say": "%s"}' % say, OPTIONS)
    assert reply.say == "Один. Два. Три."


def test_parse_drops_broken_optionals_one_by_one():
    reply = parse_step_reply(
        '{"option_id": "back_off_zombie", "why": "близко", "say": "Отхожу.", '
        '"new_term": "ночной стон", '
        '"msg": {"to": 8, "channel": "pigeon", "type": "warning", "text": "нет"}, '
        '"votes": [{"entry_id": "k-0004", "accept": "yes"}, {"entry_id": "", "accept": true}, 7]}',
        OPTIONS,
    )
    assert isinstance(reply, StepReply)
    assert reply.option_id == "back_off_zombie" and reply.say == "Отхожу."
    assert reply.new_term is None and reply.msg is None and reply.votes == ()


def test_parse_keeps_valid_votes_next_to_broken_ones():
    reply = parse_step_reply(
        '{"option_id": "fell_birch", "votes": [7, {"entry_id": "k-0004", "accept": true}, '
        '{"entry_id": "k-0005", "accept": false, "why": "не сходится"}, '
        '{"entry_id": "k-0006", "accept": true, "why": null}]}',
        OPTIONS,
    )
    assert reply.votes == (
        Vote(entry_id="k-0004", accept=True, why=""),
        Vote(entry_id="k-0005", accept=False, why="не сходится"),
        Vote(entry_id="k-0006", accept=True, why=""),  # null why is an empty string, not "None"
    )


def test_parse_foreign_option_id_is_an_error():
    for option_id in ("run_away", "", None):
        raw = '{"option_id": %s, "why": "?"}' % ('"%s"' % option_id if option_id is not None else "null")
        reply = parse_step_reply(raw, OPTIONS)
        assert isinstance(reply, StepError), option_id
        assert "option_id" in reply.reason


def test_parse_garbage_is_an_error():
    for raw in ("просто текст", "{не json", "[]"):
        assert isinstance(parse_step_reply(raw, OPTIONS), StepError), raw
