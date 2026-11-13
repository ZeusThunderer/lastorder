"""Chat line → text a TTS model reads well (ADR-055).

The line already passed `check_say` (no control characters, no `§`, ≤ 256 chars). Here it
loses what must not be spoken — role-play asterisks, emoji, links — and numbers become
Russian words, because a cloned voice reads "00007" digit by digit or not at all. The
misaka preset's serial "Мисака 00007" becomes "Мисака семь", the way the sisters say it.
An empty result means "nothing to voice": the caller sends the line to chat silently.
"""

from __future__ import annotations

import re

try:  # the tts image has it; without it numbers stay digits (the mind never imports this)
    from num2words import num2words
except ImportError:  # pragma: no cover - exercised only outside the tts image
    num2words = None

_ROLEPLAY = re.compile(r"\*[^*\n]{1,80}\*")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
# emoji, pictographs, dingbats, variation selectors, zero-width joiners
_EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2190, 0x21FF), (0x2B00, 0x2BFF), (0xFE0F, 0xFE0F), (0x200D, 0x200D))
_EMOJI = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _EMOJI_RANGES) + "]")
_NUMBER = re.compile(r"(?<![\w.])(\d{1,12})(?:[.,](\d{1,3}))?(?![\w])")
_SPACES = re.compile(r"\s+")
_ONLY_PUNCT = re.compile(r"^[\W_]*$")


def _number_words(match: re.Match[str]) -> str:
    whole, frac = match.group(1), match.group(2)
    if num2words is None:
        return match.group(0)
    text = num2words(int(whole), lang="ru")
    if frac:
        text += " целых " + num2words(int(frac), lang="ru")
    return text


def normalize(text: str) -> str:
    """The speakable form of one chat line; "" when nothing in it should be voiced."""
    s = _ROLEPLAY.sub(" ", text)
    s = _URL.sub(" ссылка ", s)
    s = _EMOJI.sub(" ", s)
    s = _NUMBER.sub(_number_words, s)
    s = _SPACES.sub(" ", s).strip()
    if _ONLY_PUNCT.match(s):
        return ""
    return s
