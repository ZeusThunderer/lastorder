"""Primer validation: schema, plain keys, budgets (T-010).

Checks agent/last_order/primer/*.json against KNOWLEDGE.md v2 section 8 and the T-010
acceptance list:

- every file is a topic from TOPICS and holds 1-4 entries;
- every entry parses as PrimerEntry (runtime/knowledge/schema.py, T-007);
- every about key is exactly what plain-mode T-009 perception produces: a normalize()
  round trip plus Perception().handle() of the key's own id part;
- every entry converts into a valid Record the store would load (source=primer,
  status=confirmed, no evidence, no votes);
- the main primer (extended=false) fits PRIMER_MAX_ENTRIES records and B_PRIMER tokens
  on the canonical entry_line rendering (runtime/knowledge/slice.py);
- claims are Russian, at most two sentences, and carry no bot commands, service markers
  or external links.

Prints the topic table and, on any problem, lists them and exits 1.

    uv run python dev/primer_check.py
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from runtime.knowledge.budget import B_PRIMER, PRIMER_MAX_ENTRIES, estimate_tokens
from runtime.knowledge.schema import PrimerEntry, Record, SchemaError
from runtime.knowledge.slice import entry_line
from runtime.perception.translate import PREFIX, Perception, normalize

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIMER_DIR = REPO_ROOT / "agent" / "last_order" / "primer"

# The record ts is hive's load moment; validation only needs a valid ISO-8601 stamp.
LOAD_TS = "2026-10-17T00:00:00+00:00"

# The T-010 acceptance list: one file per topic, Russian labels for the table.
TOPICS: dict[str, str] = {
    "shelter": "первая ночь, укрытие",
    "tools": "инструменты, прогрессия",
    "food": "еда, голод, готовка",
    "mobs": "ночные враги",
    "falling": "падение с высоты",
    "lava": "лава",
    "water": "вода, утопление",
    "bed": "кровать и сон",
    "death": "вещи после смерти",
    "light": "свет и факелы",
}
MIN_PER_TOPIC = 1
MAX_PER_TOPIC = 4

CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
SENTENCE_END = re.compile(r"[.!?…]")
# Bot tooling, service markers and external links never enter a claim (T-010).
FORBIDDEN_IN_CLAIM = (
    "altoclef",
    "alto clef",
    "tungsten",
    "@",
    "http://",
    "https://",
    "www.",
    ".com",
    ".ru",
    "minecraft:",
)

KIND_BY_PREFIX = {prefix: kind for kind, prefix in PREFIX.items()}


@dataclass
class Entry:
    """One primer entry with the record id a fresh base assigns it in load order."""

    topic: str
    index: int  # position inside its topic file, 1-based
    entry: PrimerEntry
    record_id: str  # store._load_primer_locked numbers every entry, main and extended alike

    @property
    def where(self) -> str:
        return f"{self.topic}.json#{self.index}"


@dataclass
class Topic:
    name: str
    label: str
    entries: list[Entry] = field(default_factory=list)


def key_problems(key: str) -> list[str]:
    """The key must be the plain-mode handle T-009 builds from its own id part."""
    prefix, _, id_part = key.partition(":")
    kind = KIND_BY_PREFIX.get(prefix)
    if kind is None:
        return [f"{key!r}: prefix is not one of {sorted(PREFIX.values())}"]
    if not id_part:
        return [f"{key!r}: empty id part"]
    if normalize(id_part) != id_part:
        return [f"{key!r}: id part is not the normalized form T-009 produces"]
    produced = Perception({}, salt="primer-check", mode="plain").handle(id_part, kind)
    if produced != key:
        return [f"{key!r}: plain perception produces {produced!r} for it"]
    return []


def claim_problems(claim: str) -> list[str]:
    """Russian, at most two sentences, nothing the base must never carry."""
    if not CYRILLIC.search(claim):
        return ["claim: not a Russian sentence"]
    problems = []
    if len(SENTENCE_END.findall(claim)) > 2:
        problems.append("claim: more than two sentences")
    lower = claim.lower()
    for fragment in FORBIDDEN_IN_CLAIM:
        if fragment in lower:
            problems.append(
                f"claim: forbidden fragment {fragment!r} "
                "(bot commands, service markers and links stay out of the primer)"
            )
    return problems


def load_primer(primer_dir: Path) -> tuple[list[Topic], list[str]]:
    """Parse every topic file; record ids follow the fresh-base load order, as store.py."""
    problems: list[str] = []
    topics: dict[str, Topic] = {}
    sequence = 0
    for path in sorted(primer_dir.glob("*.json")):
        stem = path.stem
        if stem not in TOPICS:
            problems.append(f"{path.name}: not a known primer topic of {sorted(TOPICS)}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{path.name}: broken JSON: {exc}")
            continue
        if not isinstance(data, list):
            problems.append(f"{path.name}: expected a list of entries")
            continue
        topic = Topic(stem, TOPICS[stem])
        for index, raw in enumerate(data, start=1):
            try:
                entry = PrimerEntry.from_dict(raw)
            except SchemaError as exc:
                problems.append(f"{path.name}#{index}: rejected by the T-007 schema: {exc}")
                continue
            sequence += 1
            topic.entries.append(Entry(stem, index, entry, f"k-{sequence:04d}"))
        topics[stem] = topic
    for stem, label in TOPICS.items():
        if stem not in topics:
            problems.append(f"{stem}.json: missing topic ({label})")
    return [topics[name] for name in sorted(TOPICS) if name in topics], problems


def main_budget(topics: list[Topic]) -> tuple[int, int]:
    """(record count, token count) of the main primer on the canonical rendering."""
    lines = [
        entry_line(item.entry.to_record(item.record_id, LOAD_TS).to_entry()) + "\n"
        for topic in topics
        for item in topic.entries
        if not item.entry.extended
    ]
    return len(lines), sum(estimate_tokens(line) for line in lines)


def check_primer(primer_dir: Path | None = None) -> tuple[list[Topic], list[str]]:
    """Full validation; returns the parsed topics and every problem found."""
    primer_dir = Path(primer_dir) if primer_dir is not None else PRIMER_DIR
    topics, problems = load_primer(primer_dir)
    for topic in topics:
        if not MIN_PER_TOPIC <= len(topic.entries) <= MAX_PER_TOPIC:
            problems.append(
                f"{topic.name}.json: {len(topic.entries)} entries, "
                f"expected {MIN_PER_TOPIC}-{MAX_PER_TOPIC} per topic"
            )
        for item in topic.entries:
            problems += [f"{item.where}: {p}" for p in claim_problems(item.entry.claim)]
            for key in item.entry.about:
                problems += [f"{item.where}: {p}" for p in key_problems(key)]
            try:
                Record.from_dict(item.entry.to_record(item.record_id, LOAD_TS).to_dict())
            except SchemaError as exc:
                problems.append(f"{item.where}: does not load as a primer record: {exc}")
    count, tokens = main_budget(topics)
    if count > PRIMER_MAX_ENTRIES:
        problems.append(
            f"main primer: {count} records exceed PRIMER_MAX_ENTRIES={PRIMER_MAX_ENTRIES}"
        )
    if tokens > B_PRIMER:
        problems.append(f"main primer: {tokens} tokens exceed B_PRIMER={B_PRIMER}")
    if not any(item.entry.extended for topic in topics for item in topic.entries):
        problems.append(
            "extended primer is empty: what does not fit the budget must be marked extended=true"
        )
    return topics, problems


def print_table(topics: list[Topic]) -> None:
    print(f"{'topic':<10} {'label':<26} {'entries':>7} {'main':>5} {'ext':>4} {'tokens':>7}")
    for topic in topics:
        count, tokens = main_budget([topic])
        print(
            f"{topic.name:<10} {topic.label:<26} {len(topic.entries):>7} "
            f"{count:>5} {len(topic.entries) - count:>4} {tokens:>7}"
        )
    count, tokens = main_budget(topics)
    print(f"main primer: {count}/{PRIMER_MAX_ENTRIES} records, {tokens}/{B_PRIMER} tokens")


def main() -> int:
    topics, problems = check_primer()
    print_table(topics)
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("primer: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
