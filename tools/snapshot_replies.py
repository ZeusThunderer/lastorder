"""Snapshot of the node-tier step assembly: three task1 requests plus two full steps.

    uv run python tools/snapshot_replies.py [--preset lab|misaka] [--out file.md]

Everything goes through the real step pipeline (T-011): the prompts come from
build_step_prompt (KNOWLEDGE.md v2 section 9, blocks and budgets), the calls run at the
node tier (ADR-023, LLMConfig.for_tier("node")), and the step replies must pass
parse_step_reply. Checks are the style contract, not a judgement of quality: Russian,
no emoji, 1-3 sentences, a will of 2-4 lines, no raw game ids, no trait numbers in
speech, an option_id from the offered list. Exit code 1 if any check fails.

The two steps use the observation of dev/bench_cases/node_step.json: one in `plain` and
one in `masked`, so both perception modes (ADR-020) and both knowledge-rule variants of
the template are exercised. The primer, goals and knowledge slice are synthetic (T-010
has not written the primer files yet).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.config import load_dotenv  # noqa: E402
from runtime.lastorder.naming import preset  # noqa: E402
from runtime.lastorder.persona import Inheritance, Node  # noqa: E402
from runtime.lastorder.step import StepError, build_step_prompt, parse_step_reply  # noqa: E402
from runtime.lastorder.traits import Traits  # noqa: E402
from runtime.llm.client import LLMClient, LLMConfig  # noqa: E402
from runtime.perception.translate import Perception  # noqa: E402
from runtime.ports import KnowledgeEntry, KnowledgeSlice, NetworkGoal, NodeMessage, Option  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
TRAIT_NUMBER = re.compile(
    r"(bravery|greed|talkativeness|trust|superstition|жадност\w*|храброст\w*|болтливост\w*"
    r"|суевер\w*|доверчив\w*|доверчивост\w*)\s*[:\-—]?\s*(1?[\dа-я]|ноль|один|два|три|четыре"
    r"|пять|шесть|семь|восемь|девять|десять)\b",
    re.I,
)
SAMPLE = {"birch_log": "светлый вертикальный столб с тёмными штрихами", "grass_block": "плотный блок, сверху зелёный"}
DESCRIPTIONS = {"zombie": "стонущее прямоходящее", "birch_log": "светлый столб с тёмными штрихами",
                "grass_block": "плотный блок, сверху зелёный", "wooden_axe": "деревянное орудие с рукоятью",
                "bread": "мягкая круглая еда", "cow": "пятнистое рогатое", "stone": "серый твёрдый блок"}
PRIMER = (
    "Ночь без света опасна: у двери и окон собираются враждебные.",
    "Дерево рубится топором быстрее, чем рукой.",
    "Кровать задаёт точку возвращения после смерти.",
    "Сытая еда постепенно восстанавливает здоровье.",
)

REQUESTS = [
    ("where", "Ты где? Что видишь?"),
    ("chat", "Зритель из чата: иди в пещеру, там алмазы!"),
    ("will", "Ты только что погиб. Напиши завещание следующему узлу."),
]

OPTIONS = [
    Option(id="fell_birch", title="рубить берёзу рядом", lever="chop"),
    Option(id="craft_table", title="скрафтить верстак из 4 берёзных брёвен", lever="craft"),
    Option(id="eat_bread", title="съесть хлеб, еда и hp низкие", lever="eat"),
    Option(id="back_off_zombie", title="отойти от стонущего на 12+ блоков", lever="move"),
    Option(id="punch_zombie", title="атаковать стонущего кулаком, топор жалко", lever="attack"),
    Option(id="ask_bread", title="попросить у узла 00008 ещё хлеба в чате", lever="say"),
    Option(id="mark_tree", title="дать этому дереву свой термин и осмотреться", lever="say"),
    Option(id="wait_look", title="стоять и наблюдать 8 секунд", lever="move"),
]


def sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]+", text) if s.strip()])


def style_problems(text: str, p: Perception) -> list[str]:
    problems = []
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    if letters and sum(c.isascii() for c in letters) / len(letters) > 0.3:
        problems.append("mostly not Russian")
    if EMOJI.search(text):
        problems.append("emoji")
    if p.leaks(text):
        problems.append(f"raw ids: {p.leaks(text)}")
    if TRAIT_NUMBER.search(text):
        problems.append("trait number in speech")
    return problems


def check_free_text(kind: str, text: str, p: Perception) -> list[str]:
    problems = style_problems(text, p)
    if kind == "will":
        lines = [l for l in text.splitlines() if l.strip()]
        if not 2 <= len(lines) <= 4:
            problems.append(f"will has {len(lines)} lines, want 2-4")
    elif sentences(text) > 3:
        problems.append(f"{sentences(text)} sentences, want 1-3")
    return problems


def task1_observation(p: Perception) -> str:
    seen = [p.see("birch_log", "block"), p.see("grass_block", "block"), p.see("zombie", "entity")]
    return "Наблюдение сейчас:\n" + "\n".join(f"- {s.render()}" for s in seen) + "\nЗдоровье полное. Светло."


def step_observation(p: Perception) -> str:
    """The dev/bench_cases/node_step.json observation, refs rendered by perception."""

    def ref(raw: str, kind: str) -> str:
        return p.handle(raw, kind)

    obs = {
        "self": {"hp": 8, "food": 14, "pos": [120, 64, -30],
                 "held": ref("wooden_axe", "item"), "looking_at": ref("birch_log", "block")},
        "entities": [{"ref": ref("zombie", "entity"), "dist_m": 9},
                     {"ref": ref("cow", "entity"), "dist_m": 14}],
        "blocks": [{"ref": ref("birch_log", "block")}, {"ref": ref("grass_block", "block")},
                   {"ref": ref("stone", "block")}],
        "inventory": [{"ref": ref("birch_log", "item"), "count": 12},
                      {"ref": ref("wooden_axe", "item"), "count": 1},
                      {"ref": ref("bread", "item"), "count": 2}],
        "events": [{"type": "game.damage", "payload": {"amount": 3}}],
    }
    return json.dumps(obs, ensure_ascii=False, indent=1)


def knowledge_slice(p: Perception) -> KnowledgeSlice:
    return KnowledgeSlice(
        catalog="k-0001 [fact] еда восстанавливает здоровье",
        network_goals=(NetworkGoal(text="найти и записать стабильный источник еды",
                                   why="ступень food не взята"),),
        about=(KnowledgeEntry(id="k-0002", kind="danger", claim="стонущий бьёт на сближении",
                              status="confirmed", about=(p.handle("zombie", "entity"),)),),
        fresh=(KnowledgeEntry(id="k-0003", kind="fact", claim="берёза рубится топором быстрее",
                              status="hypothesis", about=(p.handle("birch_log", "block"),)),),
        pending_votes=(KnowledgeEntry(id="k-0004", kind="danger", claim="ночь без света опасна",
                                      status="hypothesis"),),
    )


def check_step(reply_text: str, p: Perception) -> tuple[str, list[str]]:
    reply = parse_step_reply(reply_text, OPTIONS)
    if isinstance(reply, StepError):
        return "", [f"parse: {reply.reason}"]
    problems = []
    if reply.say is None:
        problems.append("no say")
    else:
        problems += style_problems(reply.say, p)
        if sentences(reply.say) > 3:
            problems.append(f"say has {sentences(reply.say)} sentences, want 1-3")
    if reply.note:
        problems += style_problems(reply.note, p)
    if reply.msg is not None:
        problems += style_problems(reply.msg.text, p)
    return reply.option_id, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="lab")
    ap.add_argument("--out")
    args = ap.parse_args()

    load_dotenv()
    client = LLMClient(LLMConfig.for_tier("node"))
    traits = Traits(5, 5, 5, 5, 5, quirk="likes pale trees more than dark ones", seed=0)
    node = Node(1, traits)
    naming = preset(args.preset)
    report = [f"# Snapshot · {client.config.model} · preset {args.preset} · tier node\n"]
    failed, cost = False, 0.0

    def spend() -> str:
        nonlocal cost
        stats = client.last_call
        if stats and stats.cost_usd is not None:
            cost += stats.cost_usd
            tokens = f"{stats.prompt_tokens}/{stats.completion_tokens} tok"
            return f" · {tokens} · ${stats.cost_usd:.4f}"
        return " · cost unknown"

    # the three task1 requests run on the assembled step system part
    p_plain = Perception(SAMPLE, salt="snapshot", mode="plain")
    system, _ = build_step_prompt(
        node, Inheritance(predecessors_summary="you are the first one",
                          active_nodes_summary="you are the first one"),
        knowledge_slice(p_plain), "goal: дерево на верстак и кровать",
        step_observation(p_plain), OPTIONS, mode="plain", naming=naming, primer=PRIMER,
        perception=p_plain,
        inbox=(NodeMessage(from_serial=8, to_serial=1, text="я рублю западнее, не мешайся"),),
        chat=("<LastOrder02> у меня хлеб есть, дам два",),
    )
    obs = task1_observation(p_plain)
    assert not p_plain.leaks(system + obs), "prompt leaks raw ids"
    for kind, request in REQUESTS:
        # the task1 requests are the brief's free-text probes, not steps: the JSON
        # contract of the system part is explicitly set aside for them
        reply = client.complete(system, f"{obs}\n\n{request}\n\n(Это не шаг: ответь обычным текстом, без JSON.)")
        problems = check_free_text(kind, reply, p_plain)
        failed |= bool(problems)
        report.append(f"## task1 {kind}: {request}{spend()}\n\n{reply}\n\n"
                      f"{'OK' if not problems else 'FAIL: ' + '; '.join(problems)}\n")

    # two full steps: plain and masked, both through the real assembly
    for mode in ("plain", "masked"):
        p = Perception(DESCRIPTIONS, salt="snapshot", mode=mode)
        step_system, user = build_step_prompt(
            node, Inheritance(predecessors_summary="you are the first one",
                              active_nodes_summary="you are the first one"),
            knowledge_slice(p), "goal: дерево на верстак и кровать",
            step_observation(p), OPTIONS, mode=mode, naming=naming,
            primer=PRIMER, perception=p,
            inbox=(NodeMessage(from_serial=8, to_serial=1, text="я рублю западнее, не мешайся"),),
            chat=("<LastOrder02> у меня хлеб есть, дам два",
                  "§a[Alto Clef] task «get log 8» started"),
        )
        assert not p.leaks(step_system + user), f"{mode} prompt leaks raw ids"
        raw = client.complete(step_system, user, json_mode=True)
        option_id, problems = check_step(raw, p)
        failed |= bool(problems)
        report.append(f"## step ({mode}){spend()}\n\nchosen: {option_id or '—'}\n\n{raw}\n\n"
                      f"{'OK' if not problems else 'FAIL: ' + '; '.join(problems)}\n")

    report.append(f"cost: ${cost:.4f}" + "\n")
    text = "\n".join(report)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
