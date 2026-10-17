"""DeathDetector and the cause ruling (NETWORK.md section 7; T-013 acceptance 1-2).

The sequences are the ones the stand actually produced: a zombie kill (T-003, onDeath
silent, the vanilla line in chat), an RCON /kill (T-006, "LastOrder01 was killed") and a
fall; T-006 also showed the chat line itself arrives twice, which must not read as two
deaths. A new death needs a living state in between (the respawn).
"""

from datetime import datetime, timedelta, timezone

from runtime.lastorder.death import (
    CAUSE_GAME,
    CAUSE_STUCK,
    CAUSE_TTL,
    DeathDetector,
    classify_death,
    is_death_line,
)
from runtime.ports import BirthRecord, GameState

USER = "LastOrder01"


def state(hp: float, in_game: bool = True) -> GameState:
    return GameState(
        in_game=in_game, hp=hp, food=20.0, pos=(0.0, 0.0, 0.0), held="", looking_at=""
    )


def birth(born_at: str, ttl_min: int = 45) -> BirthRecord:
    return BirthRecord(serial=7, born_at=born_at, ttl_min=ttl_min)


# --- the vanilla line --------------------------------------------------------------


def test_death_line_matches_stand_forms_and_rejects_noise():
    assert is_death_line("LastOrder01 was slain by Zombie", USER)
    assert is_death_line("LastOrder01 was killed", USER)
    assert is_death_line("LastOrder01 hit the ground too hard", USER)
    assert is_death_line("LastOrder01 fell from a high place", USER)
    assert is_death_line("§2§lLastOrder01 drowned", USER)  # color codes stripped
    assert not is_death_line("LastOrder01 joined the game", USER)
    assert not is_death_line("LastOrder01 left the game", USER)
    assert not is_death_line("LastOrder01 has made the advancement [Stone Age]", USER)
    assert not is_death_line("<LastOrder01> I was slain by boredom", USER)  # player chat
    assert not is_death_line("§2§l§o[Alto Clef] Task started", USER)  # uc service line
    assert not is_death_line("LastOrder02 was slain by Zombie", USER)  # someone else


# --- the detector: one event per death ----------------------------------------------


def test_zombie_death_hp_and_line_collapse_into_one_event():
    detector = DeathDetector(USER)
    assert detector.feed(state(20.0), ()) is None
    event = detector.feed(state(0.0), ("LastOrder01 was slain by Zombie",))
    assert event is not None and event.text == "LastOrder01 was slain by Zombie"
    # the same line again, still dead: not a second death
    assert detector.feed(state(0.0), ("LastOrder01 was slain by Zombie",)) is None


def test_rcon_kill_line_arrives_twice_in_one_window():
    detector = DeathDetector(USER)
    event = detector.feed(
        state(0.0), ("LastOrder01 was killed", "LastOrder01 was killed")
    )
    assert event is not None and event.text == "LastOrder01 was killed"
    assert detector.feed(state(0.0), ("LastOrder01 was killed",)) is None


def test_fall_death_line_is_taken_verbatim():
    detector = DeathDetector(USER)
    event = detector.feed(state(0.0), ("LastOrder01 fell from a high place",))
    assert event is not None and event.text == "LastOrder01 fell from a high place"


def test_hp_zero_without_a_line_still_dies():
    detector = DeathDetector(USER)
    event = detector.feed(state(0.0), ())
    assert event is not None and event.text == ""


def test_new_death_needs_a_living_state_in_between():
    detector = DeathDetector(USER)
    assert detector.feed(state(0.0), ("LastOrder01 was killed",)) is not None
    assert detector.feed(state(0.0), ("LastOrder01 drowned",)) is None  # still latched
    assert detector.feed(state(20.0), ("LastOrder01 was killed",)) is None  # respawned
    second = detector.feed(state(0.0), ("LastOrder01 drowned",))
    assert second is not None and second.text == "LastOrder01 drowned"


def test_a_line_while_alive_is_not_a_death():
    # getRecentChat is a window, not a stream: a stale death line from an earlier death
    # must not fire while the body stands alive.
    detector = DeathDetector(USER)
    assert detector.feed(state(20.0), ("LastOrder01 was killed",)) is None
    assert not detector.dead


def test_out_of_game_state_is_not_a_corpse():
    detector = DeathDetector(USER)
    assert detector.feed(state(0.0, in_game=False), ("LastOrder01 was killed",)) is None


# --- the cause ruling -----------------------------------------------------------------


BORN = "2026-10-17T12:04:11+00:00"


def test_cause_ttl_when_the_minds_clock_passes_the_deadline():
    now = datetime(2026, 10, 17, 12, 49, 12, tzinfo=timezone.utc)
    ruling = classify_death(now, birth(BORN), self_eliminated=False, death_text="LastOrder01 was killed")
    assert (ruling.cause, ruling.text) == (CAUSE_TTL, "LastOrder01 was killed")


def test_cause_ttl_wins_over_the_stuck_flag():
    now = datetime(2026, 10, 17, 12, 49, 12, tzinfo=timezone.utc)
    ruling = classify_death(now, birth(BORN), self_eliminated=True)
    assert ruling.cause == CAUSE_TTL


def test_cause_stuck_when_the_node_chose_to_eliminate_itself():
    now = datetime(2026, 10, 17, 12, 10, 0, tzinfo=timezone.utc)
    ruling = classify_death(now, birth(BORN), self_eliminated=True, death_text="LastOrder01 was killed")
    assert (ruling.cause, ruling.text) == (CAUSE_STUCK, "LastOrder01 was killed")


def test_cause_game_otherwise_with_the_vanilla_line():
    now = datetime(2026, 10, 17, 12, 10, 0, tzinfo=timezone.utc)
    ruling = classify_death(now, birth(BORN), self_eliminated=False, death_text="LastOrder01 was slain by Zombie")
    assert (ruling.cause, ruling.text) == (CAUSE_GAME, "LastOrder01 was slain by Zombie")


def test_cause_ttl_boundary_is_inclusive_and_naive_clock_reads_utc():
    born = datetime.fromisoformat(BORN)
    deadline = born + timedelta(minutes=45)
    assert classify_death(deadline.replace(tzinfo=None), birth(BORN), False).cause == CAUSE_TTL
    just_before = deadline - timedelta(seconds=1)
    assert classify_death(just_before, birth(BORN), False).cause == CAUSE_GAME
