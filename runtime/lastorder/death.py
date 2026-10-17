"""The death detector and the cause ruling of one mind (NETWORK.md section 7, T-013).

CLAUDE.md «Ловушки»: uc's onDeath stays silent for mob, fall and lava deaths, so the only
honest indicators are the body state (hp = 0 while in game) and the vanilla death line in
getRecentChat. Both arrive for one death, and T-006 showed the chat line itself can arrive
twice — all of that must collapse into exactly one event.

`DeathDetector` is a poll-fed latch: one `game.death` per death, armed again only after a
living state (hp > 0) has been seen — the respawn of the same body is the only way back.
A death line alone never fires the latch: while alive it is a stale line from the chat
history (the mind polls `getRecentChat`, a window, not a stream), and a real death always
brings hp = 0 in the same or a later poll. The line's job is the text: the vanilla message
names the killer, and it rides into the cause as `game` context and the will prompt.

`classify_death` rules on the cause (NETWORK.md section 7.3): the mind decides alone —
`ttl` by its own clock against the birth record, `stuck` by its own choice to eliminate
itself (the flag of ADR-026, set by the option picker of T-021 and passed by the loop of
T-014), everything else `game` with the vanilla line attached. Hive corrects the cause at
`retire` when it knows better (an RCON kill of its own); the ruling here is the mind's
honest version, not a prophecy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from runtime.perception.chat import is_service_line, strip_formatting
from runtime.ports import BirthRecord, GameState

CAUSE_GAME = "game"
CAUSE_TTL = "ttl"
CAUSE_STUCK = "stuck"

# Vanilla death messages that follow the player's name (en_us, 1.21.x — the server's locale
# on the stand). A whitelist, not a blacklist: "LastOrder01 joined the game" must never read
# as a death. The exact killer text after the phrase is free ("was slain by Zombie").
DEATH_PHRASES: tuple[str, ...] = (
    "was slain by",
    "was shot by",
    "was shot off",
    "was pricked to death",
    "was skewered by",
    "was impaled by",
    "was stabbed to death",
    "was stung to death",
    "was poked to death",
    "was pummeled by",
    "was fireballed by",
    "was blown up by",
    "was obliterated by",
    "was squashed by",
    "was squished by",
    "was doomed to fall",
    "was struck by lightning",
    "was killed",
    "was frozen to death",
    "hit the ground too hard",
    "fell",
    "died",
    "drowned",
    "starved to death",
    "suffocated in a wall",
    "withered away",
    "froze to death",
    "tried to swim in lava",
    "burned to death",
    "burnt to death",
    "blew up",
    "experienced kinetic energy",
)


@dataclass(frozen=True)
class DeathEvent:
    """One death of this body, `game.death` in the event envelope (NETWORK.md section 5).

    `text` is the vanilla chat line verbatim (formatting codes stripped) or "" when the
    poll saw only hp = 0; it feeds the cause ruling and the will prompt.
    """

    text: str


@dataclass(frozen=True)
class DeathCause:
    """The mind's ruling on why it died: `game` / `ttl` / `stuck` plus context."""

    cause: str
    text: str = ""  # the vanilla line, `game` context only


def is_death_line(line: str, username: str, phrases: Sequence[str] = DEATH_PHRASES) -> bool:
    """True for a vanilla death line of `username`: uc service lines and player chat
    (`<LastOrder01> …`) never qualify."""

    cleaned = strip_formatting(str(line)).strip()
    if not cleaned or is_service_line(cleaned) or cleaned.startswith("<"):
        return False
    if not cleaned.lower().startswith(f"{username.lower()} "):
        return False
    rest = cleaned[len(username) + 1 :].lower()
    return any(rest.startswith(phrase) for phrase in phrases)


class DeathDetector:
    """One death per death: hp = 0 and the chat line collapse, a doubled line is one
    (T-006), and a new death needs a living state (hp > 0) in between.

    Feed it each poll's state and chat window; the return value is the death event or
    None. Out-of-game states are ignored: an absent body is not a corpse (T-006).
    """

    def __init__(self, username: str, phrases: Sequence[str] = DEATH_PHRASES):
        self._username = username
        self._phrases = tuple(phrases)
        self._latched = False

    @property
    def dead(self) -> bool:
        """True between the death event and the first living state after it."""
        return self._latched

    def _death_text(self, chat: Sequence[str]) -> str:
        """The NEWEST matching line of the window: a stale line of an earlier death
        must not speak for this one (review R1-3)."""
        for line in reversed(list(chat)):
            if is_death_line(line, self._username, self._phrases):
                return strip_formatting(str(line)).strip()
        return ""

    def feed(self, state: GameState, chat: Sequence[str] = ()) -> DeathEvent | None:
        """One poll: the state plus the chat window of the same poll."""
        if not state.in_game:
            return None
        if state.hp > 0.0:
            self._latched = False  # a living body re-arms the detector (respawn)
            return None
        if self._latched:
            # A second indicator of the same death — the doubled line included — is
            # not a second death (T-006).
            return None
        self._latched = True
        return DeathEvent(text=self._death_text(chat))


def _utc(value: str) -> datetime:
    """An ISO timestamp as UTC: hive and mind clocks live in different containers, a
    naive mark is read as UTC rather than crashing the comparison (schema's rule)."""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def classify_death(
    now: datetime,
    birth: BirthRecord,
    self_eliminated: bool,
    death_text: str = "",
) -> DeathCause:
    """The cause ruling, in the order of NETWORK.md section 7.3: `ttl` by the mind's
    own clock first, then the node's own `stuck` choice, else `game` with the line."""

    deadline = _utc(birth.born_at) + timedelta(minutes=birth.ttl_min)
    if (now if now.tzinfo else now.replace(tzinfo=timezone.utc)) >= deadline:
        return DeathCause(cause=CAUSE_TTL, text=death_text)
    if self_eliminated:
        return DeathCause(cause=CAUSE_STUCK, text=death_text)
    return DeathCause(cause=CAUSE_GAME, text=death_text)
