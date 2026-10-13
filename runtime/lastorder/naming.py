"""Node name and speech manner are a preset, not code (ADR-018). The choice is not final."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NamingPreset:
    key: str
    name_format: str  # str.format with `serial`
    speech_style: str  # one line for the Style section of the prompt


PRESETS: dict[str, NamingPreset] = {
    "lab": NamingPreset(
        key="lab",
        name_format="узел {serial:05d}",
        speech_style=(
            "Speak in first person, like a lab report. Prefer phrasing without gender-marked "
            "past-tense verbs about yourself."
        ),
    ),
    "misaka": NamingPreset(
        key="misaka",
        name_format="Мисака {serial:05d}",
        speech_style=(
            "Speak as if reading your own line from a script or a book: say the line, then "
            "attribute it in third person with your name and feminine grammar, for example "
            "«Приступаю к работе. Ознакамливаюсь с завещанием прошлых узлов, сказала Мисака 00007»."
        ),
    ),
}


def preset(key: str) -> NamingPreset:
    try:
        return PRESETS[key]
    except KeyError:
        raise ValueError(f"unknown naming preset {key!r}; known: {', '.join(PRESETS)}") from None


def node_name(naming: NamingPreset, serial: int) -> str:
    return naming.name_format.format(serial=serial)
