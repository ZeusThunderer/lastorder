"""Perception: raw Minecraft ids never reach a node's prompt (brief §6, ADR-017).

Every raw id becomes a handle: an opaque, stable key like `blk:7f3a`, salted per epoch so handles
do not carry meaning across worlds. A node sees the handle with its collective term if one exists,
else a plain description from descriptions.yml (written by a human), else a generic label.
Commands go the other way: handle -> raw id.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import yaml

Kind = Literal["block", "entity", "item"]
PREFIX: dict[str, str] = {"block": "blk", "entity": "ent", "item": "itm"}
GENERIC: dict[str, str] = {
    "block": "незнакомый блок",
    "entity": "незнакомое существо",
    "item": "незнакомая вещь",
}
NAMESPACED = re.compile(r"\bminecraft:[a-z0-9_./]+")


def normalize(raw_id: str) -> str:
    raw_id = raw_id.strip().lower()
    return raw_id.split(":", 1)[1] if raw_id.startswith("minecraft:") else raw_id


def load_descriptions(path: Path) -> dict[str, str]:
    """`raw_id: description` pairs; ids may be written with or without the namespace."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {normalize(k): str(v).strip() for k, v in data.items() if v}


@dataclass(frozen=True)
class Seen:
    handle: str
    kind: Kind
    label: str  # what the node sees after the handle

    def render(self) -> str:
        return f"{self.handle} {self.label}"


class Perception:
    def __init__(self, descriptions: Mapping[str, str], salt: str, terms: Mapping[str, str] | None = None):
        self.descriptions = dict(descriptions)
        self.salt = salt
        self.terms: dict[str, str] = dict(terms or {})  # handle -> collective term
        self._raw_by_handle: dict[str, str] = {}

    def handle(self, raw_id: str, kind: Kind) -> str:
        raw = normalize(raw_id)
        digest = hashlib.sha1(f"{self.salt}|{kind}|{raw}".encode()).hexdigest()[:4]
        handle = f"{PREFIX[kind]}:{digest}"
        known = self._raw_by_handle.setdefault(handle, raw)
        if known != raw:  # 16 bits per kind: rare, but a collision must not swap two things
            digest = hashlib.sha1(f"{self.salt}|{kind}|{raw}".encode()).hexdigest()[:8]
            handle = f"{PREFIX[kind]}:{digest}"
            self._raw_by_handle[handle] = raw
        return handle

    def see(self, raw_id: str, kind: Kind) -> Seen:
        handle = self.handle(raw_id, kind)
        if handle in self.terms:
            label = f"«{self.terms[handle]}»"
        else:
            label = "— " + self.descriptions.get(normalize(raw_id), GENERIC[kind])
        return Seen(handle, kind, label)

    def raw(self, handle: str) -> str:
        """Raw id for a command. Only handles this perception produced are accepted."""
        try:
            return self._raw_by_handle[handle]
        except KeyError:
            raise KeyError(f"unknown handle {handle!r}") from None

    def name_term(self, handle: str, term: str) -> None:
        self.raw(handle)  # must be a real handle
        self.terms.setdefault(handle, term.strip())  # the first author keeps the term

    def _forms(self, raw: str) -> list[str]:
        return [f"minecraft:{raw}", raw, raw.replace("_", " ")]

    @staticmethod
    def _word(form: str) -> str:
        return rf"(?i)(?<![\w:]){re.escape(form)}(?!\w)"

    def scrub(self, text: str) -> str:
        """Replace raw ids seen so far (and their display forms) in free text, e.g. chat lines.

        Namespaced ids nobody has seen yet become a neutral mark: there is no kind to hash them by.
        """
        for handle, raw in sorted(self._raw_by_handle.items(), key=lambda kv: -len(kv[1])):
            for form in self._forms(raw):
                text = re.sub(self._word(form), handle, text)
        return NAMESPACED.sub("[неопознанное]", text)

    def leaks(self, text: str) -> list[str]:
        """Raw ids seen by this perception that appear in `text`. Must be empty for any prompt."""
        found = set(NAMESPACED.findall(text))
        for raw in set(self._raw_by_handle.values()):
            if any(re.search(self._word(form), text) for form in self._forms(raw)):
                found.add(raw)
        return sorted(found)
