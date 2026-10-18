"""Build the spike sister's workplace prompt (T-024).

The system part comes from the real core: `agent/last_order/prompt_template.md` rendered by
`runtime/lastorder/persona.py` with the `lab` preset and dealt traits (the template is never
edited — ADR-034). On top of it goes the spike session instruction: play, speak, keep
`memory/state.md`, write `will.md` on death. The workplace is a throwaway copy of
`agent/last_order/`; nothing here is committed with the repo (review P1-6).

    uv run python dev/harness_spike/sister_prompt.py --serial 7 --seed 42 --run /tmp/lo-spike/run1
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from runtime.config import REPO_ROOT
from runtime.lastorder.naming import preset
from runtime.lastorder.persona import Inheritance, Node, PersonaProvider
from runtime.lastorder.traits import load_quirks, roll

SPIKE_INSTRUCTION = """\
# Spike session — read first
- This is a measurement sandbox for your harness: no hive, no other nodes, no shared base yet.
- Your body is the Minecraft player {username} on a small survival server. Everything you see
  and do goes through the MCP server `body` (tools: observe, act, stop, say, wait_events).
  There are no server commands and no raw mod commands, and none will appear.
- Play: explore, collect, craft, stay alive. Choose your own pace with `wait_events`.
- Speak in the game chat via `say` from time to time: Russian, your own voice, 1-3 sentences.
- Keep your memory in `memory/state.md` (append short lines; rewrite it only to compress).
  Pick your own personal goal and write it as the first line.
- If `wait_events` returns `game.death`: write your will to `will.md` (2-4 lines: one concrete
  lesson, one joke), then stop acting; the next node takes this body over.
- Do not wait idly for permission: there is no operator watching. The session ends after
  {minutes} minutes; finish with a last line in `memory/state.md`.
- Your tools log every call; that log is the measurement, not you. Behave naturally.
"""


def build_system_prompt(serial: int, seed: int, *, username: str, minutes: int) -> str:
    quirks = load_quirks(REPO_ROOT / "agent" / "last_order" / "quirks.txt")
    provider = PersonaProvider(naming=preset("lab"))
    node = Node(serial=serial, traits=roll(seed, quirks))
    core = provider.system_prompt(node, Inheritance(), mode="plain", primer="")
    return core + "\n" + SPIKE_INSTRUCTION.format(username=username, minutes=minutes)


def build_workplace(run_dir: Path, *, serial: int, seed: int, username: str, minutes: int) -> Path:
    """A throwaway copy of the sister's template plus the spike prompt as AGENTS.md."""
    workplace = run_dir / "workplace"
    if workplace.exists():
        shutil.rmtree(workplace)
    shutil.copytree(REPO_ROOT / "agent" / "last_order", workplace)
    (workplace / "AGENTS.md").write_text(build_system_prompt(serial, seed, username=username, minutes=minutes), encoding="utf-8")
    (workplace / "FIRST_MESSAGE.md").write_text(
        f"You are node {serial:05d}. The session has just started: read AGENTS.md, observe, "
        "and begin. Do not ask for instructions.",
        encoding="utf-8",
    )
    return workplace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--run", type=Path, required=True, help="run directory (workplace/ inside)")
    parser.add_argument("--username", default="LastOrder01")
    parser.add_argument("--minutes", type=int, default=15)
    args = parser.parse_args()
    args.run.mkdir(parents=True, exist_ok=True)
    workplace = build_workplace(
        args.run, serial=args.serial, seed=args.seed, username=args.username, minutes=args.minutes
    )
    print(workplace)


if __name__ == "__main__":
    main()
