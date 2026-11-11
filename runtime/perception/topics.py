"""Topic filter: lines on the operator's banned topics never leave mind-N.

Other players write whatever they like in the game chat. A line on a topic the
operator banned must not reach ANY model — the provider can ban the account for
what it is sent, even if a sister never repeats a word of it. So the filter sits at
the one door other players' text comes through (`Executor.chat_entry`, entity
names, death and damage text): a matching line is dropped before an event, a log
record or `observe()` sees it, and the sisters, Last Order (who reads the minds'
journals) and the will writer never learn it existed.

The topics are the operator's data, not code: a plain-text file (CHAT_TOPICS_PATH)
mounted into mind-N alone — the list itself must never enter a prompt. Format:

    # comment
    [topic]          a section; its name is what the journal records, never the text
    stem             a token starting with it ("порн" → порно, порнуха)
    =word            exactly this token
    *part            a token containing it
    two words        consecutive tokens, each word by the same rules

Matching folds both the line and the patterns the same way: NFKC, case, ё→е,
accents and zero-width characters dropped, Cyrillic/Latin look-alikes and the usual
digit substitutions (s3x, п0рн) merged, repeated letters collapsed, and s.e.x-style
spelled-out letters joined. The file is re-read when it changes.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

RELOAD_CHECK_S = 5.0

_ZERO_WIDTH = re.compile(
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]"
)
_TOKEN = re.compile(r"[^\W_]+|[@$]+", re.UNICODE)
_REPEAT = re.compile(r"(.)\1+")
_CAMEL = re.compile(r"(?<=[^\W\d_])(?=[A-ZА-ЯЁ])(?<![A-ZА-ЯЁ])")  # BigWordBoy → Big Word Boy

# Cyrillic look-alikes onto Latin, so a word mixing the alphabets (сeкс) folds to
# one skeleton. Lower case only: the text is casefolded first.
_LOOKALIKE = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "ԛ": "q", "ԝ": "w",
})
# Digit and symbol substitutions differ between the alphabets (3 is `e` in s3x and
# `з` in 3аебал), so a token folds into two skeletons and either may match.
_DIGITS_LATIN = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_DIGITS_CYR = str.maketrans({"0": "o", "3": "з", "4": "ч", "6": "б", "@": "a", "$": "s"})


def _base(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("ё", "е")
    text = _ZERO_WIDTH.sub("", text)
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _skeletons(token: str) -> tuple[str, str]:
    out = []
    for digits in (_DIGITS_LATIN, _DIGITS_CYR):
        out.append(_REPEAT.sub(r"\1", token.translate(digits).translate(_LOOKALIKE)))
    return out[0], out[1]


def tokens(text: str) -> list[tuple[str, str]]:
    """The folded tokens of a line, each as its two skeletons; runs of three or more
    single letters (s e x, с.е.к.с) also yield their joined form."""
    raw = _TOKEN.findall(_base(text))
    out = [_skeletons(t) for t in raw]
    run: list[str] = []
    for t in raw + [""]:
        if len(t) == 1:
            run.append(t)
            continue
        if len(run) >= 3:
            out.append(_skeletons("".join(run)))
        run = []
    return out


@dataclass(frozen=True)
class _Word:
    mode: str  # "prefix" | "exact" | "contains"
    forms: tuple[str, str]

    def hits(self, token: tuple[str, str], *, loose: bool = False) -> bool:
        for form, have in zip(self.forms, token):
            if not form:
                continue
            # loose (names): a word of 4+ letters anywhere inside; shorter ones keep
            # their own rule — "tit" inside "entity" is not a hit
            if (loose and len(form) >= 4) or self.mode == "contains":
                if form in have:
                    return True
            elif self.mode == "exact":
                if have == form:
                    return True
            elif have.startswith(form):
                return True
        return False


@dataclass(frozen=True)
class _Pattern:
    words: tuple[_Word, ...]

    def hits(self, toks: list[tuple[str, str]], *, loose: bool = False) -> bool:
        n = len(self.words)
        for i in range(len(toks) - n + 1):
            if all(w.hits(toks[i + j], loose=loose) for j, w in enumerate(self.words)):
                return True
        return False


def _word(spec: str) -> _Word | None:
    mode = "prefix"
    if spec.startswith("="):
        mode, spec = "exact", spec[1:]
    elif spec.startswith("*"):
        mode, spec = "contains", spec[1:].rstrip("*")
    folded = _TOKEN.findall(_base(spec))
    if len(folded) != 1:
        return None
    return _Word(mode, _skeletons(folded[0]))


def parse_topics(text: str) -> dict[str, tuple[_Pattern, ...]]:
    """The topic file into {topic: patterns}. A line before any [section] belongs to
    `banned`; an unparsable line is an error naming its number — a silently skipped
    pattern is a hole in the filter."""
    topics: dict[str, list[_Pattern]] = {}
    current = "banned"
    for number, line in enumerate(str(text).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip() or "banned"
            topics.setdefault(current, [])
            continue
        words = []
        for spec in line.split():
            word = _word(spec)
            if word is None:
                raise ValueError(f"topic file line {number}: {spec!r} is not one word")
            words.append(word)
        topics.setdefault(current, []).append(_Pattern(tuple(words)))
    return {name: tuple(patterns) for name, patterns in topics.items() if patterns}


class TopicFilter:
    """`topic(text)` → the banned topic a line is on, or None. `name_topic(name)` —
    the same for a player or entity name, where words run together (BigWordBoy):
    camel case and `_` split, and a word of 4+ letters matches anywhere inside.
    Empty topics — the filter is off."""

    def __init__(
        self,
        topics: dict[str, tuple[_Pattern, ...]] | None = None,
        *,
        path: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_reload_error: Callable[[str], None] | None = None,
    ):
        self._topics = dict(topics or {})
        self._path = path
        self._clock = clock
        self._on_reload_error = on_reload_error
        self._mtime: float | None = None
        self._checked_at = float("-inf")
        self._lock = threading.Lock()
        if path:
            self._load()  # a broken file at start is a startup error, not an open filter

    @classmethod
    def from_text(cls, text: str) -> "TopicFilter":
        return cls(parse_topics(text))

    @property
    def topics(self) -> tuple[str, ...]:
        return tuple(self._topics)

    @property
    def patterns(self) -> int:
        return sum(len(p) for p in self._topics.values())

    def _load(self) -> None:
        assert self._path is not None
        with open(self._path, encoding="utf-8") as fh:
            topics = parse_topics(fh.read())
        self._topics = topics
        self._mtime = os.stat(self._path).st_mtime

    def _maybe_reload(self) -> None:
        if not self._path:
            return
        now = self._clock()
        with self._lock:
            if now - self._checked_at < RELOAD_CHECK_S:
                return
            self._checked_at = now
            try:
                if os.stat(self._path).st_mtime != self._mtime:
                    self._load()
            except (OSError, ValueError) as exc:  # keep the last good list: never fail open
                if self._on_reload_error is not None:
                    self._on_reload_error(f"{type(exc).__name__}: {exc}")

    def _find(self, text: str, *, loose: bool) -> str | None:
        self._maybe_reload()
        if not self._topics or not text:
            return None
        toks = tokens(text)
        if not toks:
            return None
        for name, patterns in self._topics.items():
            if any(p.hits(toks, loose=loose) for p in patterns):
                return name
        return None

    def topic(self, text: str) -> str | None:
        return self._find(text, loose=False)

    def name_topic(self, name: str) -> str | None:
        spaced = _CAMEL.sub(" ", str(name or "")).replace("_", " ")
        return self._find(spaced, loose=True)
