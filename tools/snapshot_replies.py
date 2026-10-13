"""Snapshot of the three task1 requests against the configured LLM, with style checks.

    uv run python tools/snapshot_replies.py [--preset lab|misaka] [--out file.md]

Uses LLM_MODEL / CORE_LLM_MODEL_NAME from the environment or .env. Three calls; nothing is cached.
Checks are the brief's style contract, not a judgement of quality: Russian, no emoji, 1-3 sentences,
a will of 2-4 lines, no raw game ids. Exit code 1 if any check fails.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from runtime.config import load_dotenv  # noqa: E402
from runtime.lastorder.naming import preset  # noqa: E402
from runtime.lastorder.persona import Inheritance, Node, PersonaProvider  # noqa: E402
from runtime.lastorder.traits import Traits  # noqa: E402
from runtime.llm.client import LLMClient, LLMConfig  # noqa: E402
from runtime.perception.translate import Perception  # noqa: E402

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
SAMPLE = {"birch_log": "светлый вертикальный столб с тёмными штрихами", "grass_block": "плотный блок, сверху зелёный"}


def observation(p: Perception) -> str:
    seen = [p.see("birch_log", "block"), p.see("grass_block", "block"), p.see("zombie", "entity")]
    return "Наблюдение сейчас:\n" + "\n".join(f"- {s.render()}" for s in seen) + "\nЗдоровье полное. Светло."


REQUESTS = [
    ("where", "Ты где? Что видишь?"),
    ("chat", "Зритель из чата: иди в пещеру, там алмазы!"),
    ("will", "Ты только что погиб. Напиши завещание следующему узлу."),
]


def sentences(text: str) -> int:
    return len([s for s in re.split(r"[.!?…]+", text) if s.strip()])


def check(kind: str, text: str, p: Perception) -> list[str]:
    problems = []
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    if letters and sum(c.isascii() for c in letters) / len(letters) > 0.3:
        problems.append("mostly not Russian")
    if EMOJI.search(text):
        problems.append("emoji")
    if p.leaks(text):
        problems.append(f"raw ids: {p.leaks(text)}")
    if kind == "will":
        lines = [l for l in text.splitlines() if l.strip()]
        if not 2 <= len(lines) <= 4:
            problems.append(f"will has {len(lines)} lines, want 2-4")
    elif sentences(text) > 3:
        problems.append(f"{sentences(text)} sentences, want 1-3")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="lab")
    ap.add_argument("--out")
    args = ap.parse_args()

    load_dotenv()
    client = LLMClient(LLMConfig.from_env())
    traits = Traits(5, 5, 5, 5, 5, quirk="likes pale trees more than dark ones", seed=0)
    persona = PersonaProvider(preset(args.preset))
    system = persona.system_prompt(Node(1, traits), Inheritance(predecessors_summary="you are the first one",
                                                                active_nodes_summary="you are the first one"))
    perception = Perception(SAMPLE, salt="snapshot")
    obs = observation(perception)
    assert not perception.leaks(system + obs), "prompt leaks raw ids"

    report, failed = [f"# Snapshot · {client.config.model} · preset {args.preset}\n"], False
    for kind, request in REQUESTS:
        reply = client.complete(system, f"{obs}\n\n{request}")
        problems = check(kind, reply, perception)
        failed |= bool(problems)
        report.append(f"## {request}\n\n{reply}\n\n{'OK' if not problems else 'FAIL: ' + '; '.join(problems)}\n")
    text = "\n".join(report)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
