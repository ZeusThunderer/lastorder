"""Perception: game names reach a node's prompt through one of two modes (ADR-020).

`plain` (default): normalized game names. The key is `<kind>:<normalized id>` (e.g. `blk:birch_log`),
stable across epochs and fit for the knowledge base; the label is the human-readable name plus the
node's term in «», if one exists. Only the `minecraft:` namespace form is forbidden.
`masked`: every raw id becomes an opaque, salted handle like `blk:7f3a`, so raw ids never reach
a prompt. A node sees the handle with its collective term if one exists, else a plain description
from descriptions.yml (written by a human), else a generic label.

Commands go the other way: key/handle -> raw id. uc's service chat lines are dropped before
perception (chat.py).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

import yaml

from runtime.config import PERCEPTION_MODES, perception_mode

Kind = Literal["block", "entity", "item"]
Mode = Literal["plain", "masked"]
PREFIX: dict[str, str] = {"block": "blk", "entity": "ent", "item": "itm"}
GENERIC: dict[str, str] = {
    "block": "незнакомый блок",
    "entity": "незнакомое существо",
    "item": "незнакомая вещь",
}
NAMESPACED = re.compile(r"\bminecraft:[a-z0-9_./]+")


def normalize(raw_id: str) -> str:
    """Registry id: lowercase, no `minecraft:` namespace, underscores instead of spaces.

    uc feeds display names ("Birch Leaves", "birch leaves") alongside registry ids
    ("minecraft:birch_leaves", "birch_leaves"); all four are one thing (E1 samples).
    Known gap (review R1-3, for T-002/T-011): not every display name is the id with spaces
    swapped — "Block of Iron" is `iron_block`, "Jack o'Lantern" is `jack_o_lantern`.
    """
    raw_id = raw_id.strip().lower().replace(" ", "_")
    return raw_id.split(":", 1)[1] if raw_id.startswith("minecraft:") else raw_id


def readable(raw_id: str) -> str:
    """Human-readable form of a registry id for `plain` labels: `birch log`."""
    return normalize(raw_id).replace("_", " ")


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
    def __init__(
        self,
        descriptions: Mapping[str, str],
        salt: str,
        terms: Mapping[str, str] | None = None,
        mode: str | None = None,
    ):
        if mode is None:  # the environment decides (ADR-020); an explicit mode is for tests
            mode = perception_mode()
        if mode not in PERCEPTION_MODES:
            raise ValueError(
                f"unknown perception mode {mode!r}: expected one of {PERCEPTION_MODES} (ADR-020)"
            )
        self.mode: Mode = mode  # type: ignore[assignment]
        self.descriptions = dict(descriptions)
        self.salt = salt
        self.terms: dict[str, str] = dict(terms or {})  # key/handle -> node's term
        self._raw_by_handle: dict[str, str] = {}

    def handle(self, raw_id: str, kind: Kind) -> str:
        """Key for one thing: stable in its mode, usable as `about` in the knowledge base."""
        raw = normalize(raw_id)
        if self.mode == "plain":
            key = f"{PREFIX[kind]}:{raw}"  # self-describing and epoch-independent, unlike a hash
            self._raw_by_handle[key] = raw
            return key
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
        if self.mode == "plain":
            name = readable(raw_id)
            label = f"{name} «{self.terms[handle]}»" if handle in self.terms else name
            return Seen(handle, kind, label)
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
        """Make free text, e.g. chat lines, fit for a prompt.

        `masked`: raw ids seen so far (and their display forms) become handles; namespaced ids
        nobody has seen yet become a neutral mark (no kind to hash them by).
        `plain`: names are allowed, only the `minecraft:` form is not — it degrades to the
        normalized id.
        """
        if self.mode == "plain":
            return NAMESPACED.sub(lambda m: normalize(m.group(0)), text)
        for handle, raw in sorted(self._raw_by_handle.items(), key=lambda kv: -len(kv[1])):
            for form in self._forms(raw):
                text = re.sub(self._word(form), handle, text)
        return NAMESPACED.sub("[неопознанное]", text)

    def leaks(self, text: str) -> list[str]:
        """Raw-id forms that must not appear in `text`. Empty for any prompt.

        The `minecraft:` namespace is a leak in both modes (ADR-020); in `masked` the bare
        ids and display names seen by this perception are leaks too.
        """
        found = set(NAMESPACED.findall(text))
        if self.mode == "masked":
            for raw in set(self._raw_by_handle.values()):
                if any(re.search(self._word(form), text) for form in self._forms(raw)):
                    found.add(raw)
        return sorted(found)
