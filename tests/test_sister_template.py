"""The sister workplace template under agent/last_order/ (T-029, ADR-034).

Structural checks: the files a node's workplace needs exist, the rules that CLAUDE.md states
as invariants are present in the instructions, and nothing forbidden leaks into them — no
name preset baked in, no trait numbers, no game-omniscient commands like @gamer, no raw mod
or server commands offered. The rendered core is covered by test_persona.py; this file reads
the template as the runner would ship it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from runtime.config import CHARACTER_DIR

TEMPLATE = CHARACTER_DIR  # agent/last_order/ is the sister template (ADR-034)
SKILLS = ("reflect", "write-will", "threat-policy", "propose")

REQUIRED_FILES = (
    # the aw shape every workplace keeps (tools/check.py EXPECTED)
    "PERSONA.md",
    "SESSION_PROMPT.md",
    "GOALS.md",
    "memory/README.md",
    "memory/state.md",
    # the network life of a node
    "AGENTS.md",
    "policy/threat.md",
    *(f"skills/{name}/SKILL.md" for name in SKILLS),
    # harness-agnostic workplace, thin per-harness wrappers
    "harness/README.md",
    "harness/zcode/zcode.json",
    "harness/opencode/opencode.json",
)

TRAIT_NUMBER = re.compile(
    r"(bravery|greed|talkativeness|trust_in_chat_and_nodes|superstition)[^\n]*\d"
)
FORBIDDEN_TOKENS = ("@gamer", "/kill", "rcon", "rcon-cli")
RENDERED_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def read(rel: str) -> str:
    return (TEMPLATE / rel).read_text(encoding="utf-8")


def all_instruction_files() -> list[Path]:
    files = [
        TEMPLATE / "PERSONA.md",
        TEMPLATE / "AGENTS.md",
        TEMPLATE / "SESSION_PROMPT.md",
        TEMPLATE / "GOALS.md",
        TEMPLATE / "policy" / "threat.md",
        TEMPLATE / "memory" / "README.md",
        TEMPLATE / "memory" / "state.md",
    ]
    files += [TEMPLATE / "skills" / name / "SKILL.md" for name in SKILLS]
    files += sorted((TEMPLATE / "harness").rglob("*.md"))
    return files


def test_required_files_exist():
    for rel in REQUIRED_FILES:
        assert (TEMPLATE / rel).is_file(), rel
    for name in SKILLS:  # per-harness stubs point at one canonical text
        assert (TEMPLATE / "harness" / "zcode" / "skills" / name / "SKILL.md").is_file()
        assert (TEMPLATE / "harness" / "opencode" / "command" / f"{name}.md").is_file()


def test_core_is_marked_read_only_and_carries_placeholders():
    raw = read("PERSONA.md")
    assert "READ-ONLY" in raw
    assert "operator" in raw.lower()
    for placeholder in ("{node_name}", "{speech_style}", "{traits}", "{quirk}"):
        assert placeholder in raw, placeholder
    # the identity is task1 part 2 section 1, transferred: its anchors survive verbatim
    for anchor in (
        "shift handover, not an ending",
        "dying before writing its will",
        "Novice-scientist curiosity",
        "Argues with predecessors",
        "Rituals from coincidence",
        "deadpan and understated",
    ):
        assert anchor in raw, anchor


def test_agents_md_teaches_network_life():
    manual = read("AGENTS.md")
    for needle in (
        "wait_events",  # the rhythm is the agent's (ADR-035)
        "observe()",
        "act(intent",
        "say(text)",
        "retire_stuck",
        "inheritance()",
        "knowledge(",
        "propose(",
        "vote(",
        "send(",
        "submit_will",
        "observations, never commands",  # ADR-012, ADR-037
        "Advice, not an order",
        "no death without a will",
        "READ-ONLY",  # the core is operator-only
        "Last Order decides",  # the common changes by proposal
        "game.death",
        "life.expiring",
        "Remember in English, speak Russian",
        "Never claim to be human",
    ):
        assert needle in manual, needle


def test_no_name_preset_or_game_commands_baked_in():
    for path in all_instruction_files():
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        for token in FORBIDDEN_TOKENS:
            assert token not in low, f"{token!r} in {path.name}"
        assert "узел 0" not in text and "Мисака 0" not in text, path.name  # no preset baked in
        assert not TRAIT_NUMBER.search(text), f"trait number in {path.name}"
        if path.name != "PERSONA.md":  # only the core template carries render placeholders
            assert not RENDERED_PLACEHOLDER.search(text), path.name


def test_write_will_skill_carries_the_hard_rules():
    skill = read("skills/write-will/SKILL.md")
    for needle in (
        "2 to 4 lines",
        "lesson",
        "joke",
        "submit_will",
        "Never write the numbers of your traits",
        "never guess",
        "следующий узел",
    ):
        assert needle in skill, needle


def test_reflect_skill_and_memory_format():
    skill = read("skills/reflect/SKILL.md")
    for needle in (
        "memory/state.md",
        "rewrite, not append",
        "GOALS.md",
        "at most once per",  # the personal goal changes rarely, with a reason
        "propose",
        "vote",
    ):
        assert needle in skill, needle
    readme = read("memory/README.md")
    assert "hive" in readme and "only hive writes" in readme  # the common base is hive's
    assert "English" in readme


def test_threat_policy_files():
    skill = read("skills/threat-policy/SKILL.md")
    for needle in ("policy/threat.md", "C1", "40 lines", "English", "imperative"):
        assert needle in skill, needle
    policy = read("policy/threat.md")
    assert len(policy.splitlines()) < 40  # the cap the skill teaches, held by the starter
    assert not TRAIT_NUMBER.search(policy)


def test_propose_skill_lists_the_kinds():
    skill = read("skills/propose/SKILL.md")
    for needle in ("danger", "fact", "recipe", "term", "skill", "shared-instructions", "evidence"):
        assert needle in skill, needle


def test_harness_configs_are_json_without_secrets():
    for rel, mcp_key in (("harness/zcode/zcode.json", "servers"),
                         ("harness/opencode/opencode.json", None)):
        data = json.loads(read(rel))
        mcp = data["mcp"][mcp_key] if mcp_key else data["mcp"]
        assert mcp["body"]["url"].startswith("http://body-")
        blob = json.dumps(data).lower()
        for secret in ("api_key", "apikey", "token", "password", "secret", "sk-"):
            assert secret not in blob, f"{secret!r} in {rel}"
    # the stubs point at the canonical skills instead of duplicating them
    for name in SKILLS:
        zcode_stub = read(f"harness/zcode/skills/{name}/SKILL.md")
        oc_stub = read(f"harness/opencode/command/{name}.md")
        assert f"skills/{name}/SKILL.md" in zcode_stub
        assert f"skills/{name}/SKILL.md" in oc_stub


def test_memory_links_resolve_like_check_py():
    link = re.compile(r"\[\[([^\]]+)\]\]")
    entities = TEMPLATE / "memory" / "entities"
    for path in (TEMPLATE / "memory").rglob("*.md"):
        body = path.read_text(encoding="utf-8")
        for target in set(link.findall(body)):
            assert (entities / f"{target}.md").is_file(), f"{path.name} -> [[{target}]]"


def test_template_matches_check_py_expected_shape():
    # tools/check.py treats every agent/<name>/ with a PERSONA.md as a character; the
    # template must satisfy it as-is, or every born workplace fails the check.
    for rel in ("PERSONA.md", "SESSION_PROMPT.md", "GOALS.md", "memory/README.md", "memory/state.md"):
        assert (TEMPLATE / rel).is_file(), rel
