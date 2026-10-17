"""Chat hygiene: uc's own chat lines never reach a prompt (CLAUDE.md «Ловушки», ADR-020).

uc writes bookkeeping into the player's chat, color-coded and full of raw ids. The markers below
come from the mod sources shipped in this repo, not from guessing:

- `[Alto Clef]` — the default `chatLogPrefix` (unionclef/src/main/java/adris/altoclef/Settings.java:94);
   every altoclef Debug.logMessage/logWarning/logError chat line carries it after the §-codes
  (unionclef/src/main/java/adris/altoclef/Debug.java:48,73,94).
- `[Tungsten]` — the tungsten pathfinder's Debug.logMessage prefix
  (unionclef/tungsten/src/main/java/kaptainwutax/tungsten/Debug.java:16,48).
- `WARNING:` — tungsten's Debug.logWarning has no bracket marker at all, only the codes
  (same file, line 71).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

SERVICE_PREFIXES: tuple[str, ...] = ("[Alto Clef]", "[Tungsten]", "WARNING:")

_COLOR_CODE = re.compile("§.")


def strip_formatting(text: str) -> str:
    """Drop Minecraft `§x` color/format codes; the glyphs stay."""
    return _COLOR_CODE.sub("", text)


def is_service_line(text: str) -> bool:
    """True for uc's own bookkeeping lines. Runs after strip_formatting, before any prompt."""
    stripped = strip_formatting(text).lstrip()
    return stripped.startswith(SERVICE_PREFIXES)


def clean_chat(lines: Iterable[str]) -> list[str]:
    """Chat fit for a prompt: service lines dropped, §-codes gone, empty lines gone.

    Player speech (`<LastOrder01> …`) and vanilla system lines (`LastOrder01 was killed`) stay.
    """
    out: list[str] = []
    for line in lines:
        line = str(line)  # once: both checks below assume a str
        if is_service_line(line):
            continue
        clean = strip_formatting(line).strip()
        if clean:
            out.append(clean)
    return out
