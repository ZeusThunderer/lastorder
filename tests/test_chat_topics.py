"""ADR-048: other players' lines on the operator's banned topics never leave mind-N.

The filter is data (docker/mind/chat-topics/topics.txt), the door is code: a chat
line, a speaker or entity name, a death text on a banned topic is dropped before an
event, a journal record or observe() — so no prompt and no model provider sees it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from runtime.mind.mcp_body import build_mind
from runtime.perception.topics import TopicFilter, parse_topics
from runtime.ports import EntityInfo
from test_executor import FakeBody, Rig

ROOT = Path(__file__).resolve().parent.parent
TOPICS_FILE = ROOT / "docker" / "mind" / "chat-topics" / "topics.txt"
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))

# neutral stand-ins: the matching rules are tested on harmless words
SAMPLE = """
# comment
[fruit]
banan
=kiwi
*grap
red apple
груш
"""


def flt() -> TopicFilter:
    return TopicFilter.from_text(SAMPLE)


# --- matching -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "I love bananas",            # prefix
        "BANANA time",               # case
        "a kiwi here",               # exact
        "grapefruit juice",          # contains
        "a red apple pie",           # two words
        "свежие груши",              # cyrillic prefix
        "b4n4n4",                    # digits for letters
        "bаnаnа",                    # cyrillic а inside a latin word
        "baaaaanana",                # repeated letters
        "b a n a n a",               # spelled out
        "b.a.n.a.n.a!",              # spelled out with dots
        "гр\u200bуша",          # a zero-width space inside
        "ГРУШИ",
    ],
)
def test_a_line_on_a_topic_is_found(line: str) -> None:
    assert flt().topic(line) == "fruit"


@pytest.mark.parametrize(
    "line",
    ["kiwis are birds", "an apple", "red car", "грузовик", "ban the player", "", "   "],
)
def test_other_lines_pass(line: str) -> None:
    assert flt().topic(line) is None


def test_names_run_words_together() -> None:
    f = flt()
    assert f.name_topic("BigBananaBoy") == "fruit"     # camel case splits
    assert f.name_topic("xx_banana_xx") == "fruit"
    assert f.name_topic("superbananaking") == "fruit"   # 4+ letters: anywhere inside
    assert f.name_topic("Steve") is None


def test_unparsable_line_is_an_error_with_its_number() -> None:
    with pytest.raises(ValueError, match="line 3"):
        parse_topics("[t]\nok\n=,,\n")


def test_lines_before_a_section_belong_to_banned() -> None:
    assert TopicFilter.from_text("banan").topic("banana") == "banned"


def test_empty_list_is_off() -> None:
    assert TopicFilter.from_text("# nothing\n").topic("banana") is None


def test_the_file_reloads_and_a_broken_edit_keeps_the_last_good_list(tmp_path) -> None:
    path = tmp_path / "topics.txt"
    path.write_text("[fruit]\nbanan\n", encoding="utf-8")
    now = [0.0]
    errors: list[str] = []
    f = TopicFilter(path=str(path), clock=lambda: now[0], on_reload_error=errors.append)
    assert f.topic("banana") == "fruit"
    path.write_text("[veg]\ncarrot\n", encoding="utf-8")
    os.utime(path, (1_000_000, 1_000_000))
    now[0] = 10.0
    assert f.topic("carrots") == "veg" and f.topic("banana") is None
    path.write_text("[veg]\n=,,\n", encoding="utf-8")
    os.utime(path, (2_000_000, 2_000_000))
    now[0] = 20.0
    assert f.topic("carrots") == "veg"   # never fails open
    assert errors and "line 2" in errors[0]


def test_a_missing_file_at_start_is_an_error(tmp_path) -> None:
    with pytest.raises(OSError):
        TopicFilter(path=str(tmp_path / "nope.txt"))


# --- the shipped list ---------------------------------------------------------------------


def test_the_shipped_list_parses_and_has_topics() -> None:
    f = TopicFilter(path=str(TOPICS_FILE))
    assert f.topics and f.patterns >= 50


@pytest.mark.parametrize(
    "line",
    [
        "привет, пойдём копать алмазы",
        "анализ руды показал железо",
        "член клана зовёт на базу",
        "гол! наша команда победила",
        "the entity despawned",
        "cocktail party at spawn",
        "titanium is not in minecraft",
    ],
)
def test_the_shipped_list_leaves_ordinary_game_chat_alone(line: str) -> None:
    assert TopicFilter(path=str(TOPICS_FILE)).topic(line) is None


@pytest.mark.parametrize("line", ["s.e.x anyone", "п0рн0 сайт", "сeкс", "PORNHUB link", "nsfw pics"])
def test_the_shipped_list_catches_the_usual_evasions(line: str) -> None:
    assert TopicFilter(path=str(TOPICS_FILE)).topic(line) == "sexual"


# --- the door in mind-N -------------------------------------------------------------------


def rig_with(tmp_path, text: str = SAMPLE) -> tuple[Rig, Path]:
    log_file = tmp_path / "log.jsonl"
    rig = Rig(log_file=str(log_file), topic_filter=TopicFilter.from_text(text))
    return rig, log_file


def journal(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_a_banned_chat_line_never_becomes_an_event_or_a_journal_line(tmp_path) -> None:
    rig, log_file = rig_with(tmp_path)
    try:
        rig.body.push_event("chat.line", "<Bob> who wants a banana")
        rig.body.push_event("chat.line", "<Bob> привет")
        rig.tick()
        lines = [e["lines"] for e in rig.events_of("chat.line")]
        assert lines == [[{"from": "Bob", "text": "привет"}]]
    finally:
        rig.close()
    text = journal(log_file)
    assert "banana" not in text
    filtered = [json.loads(l) for l in text.splitlines() if "chat.filtered" in l]
    assert filtered and filtered[0]["topic"] == "fruit" and filtered[0]["scope"] == "chat"


def test_a_speaker_with_a_banned_name_is_ignored_entirely(tmp_path) -> None:
    rig, log_file = rig_with(tmp_path)
    try:
        assert rig.x.chat_entry("<BananaKing> hi all") is None
        assert rig.x.chat_entry("<Steve> hi all") == {"from": "Steve", "text": "hi all"}
        assert rig.x.chat_entry("BananaKing joined the game") is None   # system line
    finally:
        rig.close()
    assert "BananaKing" not in journal(log_file)


def test_death_text_on_a_banned_topic_is_dropped(tmp_path) -> None:
    rig, _ = rig_with(tmp_path)
    try:
        assert rig.x._clean_text("LastOrder01 was slain by BananaKing") is None
        assert rig.x._clean_text("LastOrder01 fell from a high place") == "LastOrder01 fell from a high place"
    finally:
        rig.close()


def test_observe_shows_a_banned_entity_name_as_its_kind(tmp_path) -> None:
    body = FakeBody()
    body.hostiles = [EntityInfo(kind="player", name="BigBananaBoy", dist_m=3.0, pos=(1, 64, 1))]
    body.chat = ["<BigBananaBoy> hey", "<Steve> grapes for sale", "<Steve> hello"]
    _server, impl, x = build_mind(
        body,
        username="LastOrder01",
        start_c1=False,
        topic_filter=TopicFilter.from_text(SAMPLE),
        idle_s=10_000.0,
    )
    obs = impl.observe()
    dumped = json.dumps(obs, ensure_ascii=False)
    assert "Banana" not in dumped and "grape" not in dumped
    assert obs["chat"] == [{"from": "Steve", "text": "hello"}]
    player = [e for e in obs["entities"] if e["kind"] == "player"]
    assert player and "player" in player[0]["name"]   # the kind stands in for the name


def test_without_a_filter_nothing_changes(tmp_path) -> None:
    rig = Rig()
    try:
        assert rig.x.chat_entry("<Bob> banana") == {"from": "Bob", "text": "banana"}
    finally:
        rig.close()


# --- compose: the list lives in the minds alone ------------------------------------------


def test_the_list_is_mounted_read_only_into_the_minds_alone() -> None:
    mount = "${STAND_DAEMON_ROOT:-..}/docker/mind/chat-topics:/etc/lastorder/chat-topics:ro"
    services = COMPOSE["services"]
    for n in (1, 2, 3):
        assert mount in services[f"mind-{n}"]["volumes"], f"mind-{n}"
    for name, svc in services.items():
        if name.startswith("mind-"):
            continue
        vols = [v if isinstance(v, str) else v.get("source", "") for v in svc.get("volumes", [])]
        assert not any("chat-topics" in v for v in vols), name
    env = services["mind-1"]["environment"]
    assert env["CHAT_TOPICS_PATH"] == "${CHAT_TOPICS_PATH:-/etc/lastorder/chat-topics/topics.txt}"


# --- T-072: attack hunts named players only (k-0120, k-0149: sisters killed by a bare attack) ----


def test_attack_needs_named_players_and_never_hits_its_own_body() -> None:
    from runtime.mind.mcp_body import ToolError, validate_args

    with pytest.raises(ToolError, match="missing required"):
        validate_args("attack", {})
    with pytest.raises(ToolError, match="strike"):
        validate_args("attack", {"allow": []})
    body = FakeBody()
    _server, impl, x = build_mind(body, username="LastOrder01", start_c1=False, idle_s=10_000.0)
    with pytest.raises(ToolError, match="itself"):
        impl.act("attack", {"allow": ["lastorder01"]})
    impl.act("attack", {"allow": ["Stranger"]})
    queued = x._queue[-1] if hasattr(x, "_queue") and x._queue else None
    intent = queued or x._pending_act or x._current
    args = getattr(intent, "args", None) or {}
    assert args.get("allow") == ["Stranger"] and "LastOrder01" in args.get("avoid", [])
