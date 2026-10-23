#!/usr/bin/env python3
"""MVP criterion report (prototype-plan «Критерий MVP», T-032).

    uv run python dev/mvp_report.py --knowledge <volume> --workplaces <volume> \
        [--workplaces <volume> ...] --run <dir> [--to 2026-10-23T14:00:00Z] \
        [--window 2h] [--out docs/agents/runs/<date>-mvp.md] [--json]

`--knowledge` is the hive knowledge volume (nodes/<serial>/birth.json + will.md,
base/records/*.json, base/proposals/*.json), `--workplaces` one workplaces volume per
slot (agent/<serial>/ lives there, archive/<serial>/ after death; repeat the flag or
comma-separate for several slots), `--run` the collected run artifacts:

    events.jsonl            hive events (the hive-run volume)
    runner-<slot>.log       node runner stdout per slot (docker compose logs node-N)
    mind-<slot>-log.jsonl   the mind's log.jsonl per slot (the mind-N-run volume)
    mc-server.log           the server console (chat quotes, uc service lines)
    gateway.json            optional dev/gateway_report.py --json output (money spend)
    hermes-state.json       optional Hermes state dump (sessions + session_model_usage)
    hermes-messages.jsonl   optional Hermes messages dump, one JSON row per message
                            (what the agent actually saw — the uc service-lines check)

The window is `--window` (default 2h) ending at `--to` (default now, UTC). Every
criterion item gets pass/fail/n-a; the overall verdict is PASS only when no item fails
or stays unverified. Quotes and tables land in the markdown, secrets never do: the
inputs are volumes and logs, keys are not there (and are never printed).

The item rules are this file's own reading of the criterion, kept visible:

- generations: >= 6 births inside the window across slots;
- traits: every window birth carries the five numbers + quirk + seed in birth.json and
  a rendered PERSONA.md in its workplace (live or archived), and no two window nodes
  share the whole trait vector;
- inheritance: >= 70% of window births (that have a predecessor) reference the
  predecessor in one of their first 3 chat lines — by her serial (00013 form), a term
  record she authored, or a 3-word run from her will's «Урок:» line;
- wills: no death inside the window without will.md; the emergency share is reported
  (the criterion only forbids will-less deaths, emergency wills are wills);
- sessions: no runner.error / spawn_failed / will.parked, no will session timeouts,
  no session that ran > HANG_S without a death stop; restarts and waits are counted;
- cost: gateway money <= $0.35/h over the window; Hermes credits are reported (quota,
  not money — no published ceiling, the number is for the operator);
- self-correction: every window birth has core memory or a workplace memory/ file, and
  the network has >= 1 Last Order proposal accepted inside the window;
- uc lines: no uc service prefix ([Alto Clef] / [Tungsten] / WARNING:, ADR-020) in what
  the harness showed the agent; the server chat count is the cross-check that scrubbing
  had something to scrub.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runtime.knowledge.schema import SchemaError, Will  # noqa: E402
from runtime.knowledge.slice import format_serial, parse_utc  # noqa: E402
from runtime.perception.chat import is_service_line  # noqa: E402

# The uc chat markers of runtime/perception/chat.py (ADR-020): if one of these ever
# appears in a Hermes message, the scrub failed.
SERVICE_PREFIXES: tuple[str, ...] = ("[Alto Clef]", "[Tungsten]", "WARNING:")
COST_CEILING_PER_H = 0.35
MIN_GENERATIONS = 6
REF_SHARE = 0.70
FIRST_REPLIES = 3
HANG_S = 2700.0  # ttl is 45 min; a life session ending beyond that without a death is a hang
TRAIT_KEYS = ("bravery", "greed", "talkativeness", "trust_in_chat_and_nodes", "superstition")
SLOT_RE = re.compile(r"LastOrder0[1-3]")
MIN_SLOT_RE = re.compile(r"(?:mind|runner)[-_](\d)")
CHAT_LINE_RE = re.compile(r"<(LastOrder0[1-3])> (.*)")


# --- the collected facts ---------------------------------------------------------------------


@dataclass
class Birth:
    serial: int
    slot: str
    born_ts: float
    traits: dict


@dataclass
class Death:
    serial: int
    slot: str
    cause: str
    died_ts: float
    lifetime_s: int
    emergency: bool
    body: str


@dataclass
class Facts:
    births: dict[int, Birth]
    deaths: dict[int, Death]  # deaths that have a will (the store writes one on retire)
    retire_events: list[tuple[float, int]]  # (ts, serial) of life.retire in window
    records: list[dict]
    proposals: list[dict]
    runner_lines: list[dict]  # every parsed runner log line
    runner_slots: dict[int, str]  # line index -> slot of the runner.start it belongs to
    says: dict[str, list[tuple[float, str]]]  # slot -> [(ts, text)] of ok say calls
    server_chat: list[tuple[str, str]]  # (slot, text) from mc-server.log
    server_service: int  # uc service lines in the server chat (what observe had to scrub)
    gateway: dict | None
    hermes_state: dict | None
    hermes_messages: Path | None


def _iso_to_ts(text: str) -> float:
    try:
        return parse_utc(text).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _load_jsonl(path: Path, *, epoch: bool = False) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn docker-log line is data loss, not a crash
        if isinstance(row, dict):
            if epoch and not isinstance(row.get("ts"), (int, float)):
                continue
            rows.append(row)
    return rows


def load_knowledge(root: Path) -> tuple[dict[int, Birth], dict[int, Death], list[dict], list[dict]]:
    births: dict[int, Birth] = {}
    deaths: dict[int, Death] = {}
    records: list[dict] = []
    proposals: list[dict] = []
    nodes = root / "nodes"
    if nodes.is_dir():
        for child in sorted(nodes.iterdir()):
            birth_file = child / "birth.json"
            if not birth_file.is_file():
                continue
            try:
                data = json.loads(birth_file.read_text(encoding="utf-8"))
                serial = int(data["serial"])
                births[serial] = Birth(
                    serial=serial,
                    slot=str(data["slot"]),
                    born_ts=_iso_to_ts(str(data["born_at"])),
                    traits=data.get("traits") or {},
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                continue  # an unreadable birth.json is itself a finding: the item fails
            will_file = child / "will.md"
            if will_file.is_file():
                try:
                    will = Will.from_markdown(will_file.read_text(encoding="utf-8"))
                except SchemaError:
                    continue
                meta = will.meta
                deaths[serial] = Death(
                    serial=serial,
                    slot=str(meta.slot),
                    cause=str(meta.cause),
                    died_ts=_iso_to_ts(str(meta.died_at)),
                    lifetime_s=int(meta.lifetime_s),
                    emergency=bool(meta.emergency),
                    body=will.body,
                )
    for pattern, sink in (("*.json", records),):
        for path in sorted((root / "base" / "records").glob(pattern)):
            try:
                sink.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    for path in sorted((root / "base" / "proposals").glob("*.json")):
        try:
            proposals.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return births, deaths, records, proposals


def load_runner(root: Path) -> tuple[list[dict], dict[int, str]]:
    """Every runner log line plus the slot each line belongs to (per file, from its
    runner.start / life.birth markers — session lines carry no slot of their own)."""
    lines: list[dict] = []
    owner: dict[int, str] = {}
    files = [p for p in sorted(root.glob("runner*.log")) if p.is_file()]
    for path in files:
        slot = ""
        named = MIN_SLOT_RE.search(path.stem)
        if named:
            slot = f"LastOrder0{int(named.group(1))}"
        for row in _load_jsonl(path):
            if not isinstance(row.get("ts"), str):
                continue
            for key in ("slot",):
                if isinstance(row.get(key), str) and SLOT_RE.fullmatch(row[key]):
                    slot = row[key]
            if isinstance(row.get("payload"), dict) and isinstance(
                row["payload"].get("slot"), str
            ) and SLOT_RE.fullmatch(row["payload"]["slot"]):
                slot = row["payload"]["slot"]
            lines.append(row)
            owner[len(lines) - 1] = slot
    return lines, owner


def _mind_slot(path: Path, rows: list[dict], known_slots: set[str]) -> str:
    named = MIN_SLOT_RE.search(path.stem)
    if named:
        return f"LastOrder0{int(named.group(1))}"
    for row in rows:  # death rows carry the body's username
        if row.get("kind") == "death" and SLOT_RE.fullmatch(str(row.get("username", ""))):
            return str(row["username"])
    if len(known_slots) == 1:
        return next(iter(known_slots))
    return ""


def load_mind_says(root: Path, slots: set[str]) -> dict[str, list[tuple[float, str]]]:
    """ok `say` tool calls per slot: the node's own chat lines."""
    says: dict[str, list[tuple[float, str]]] = {}
    for path in sorted(root.glob("*mind*log*.jsonl")) + sorted(root.glob("log.jsonl")):
        if not path.is_file():
            continue
        rows = _load_jsonl(path, epoch=True)
        slot = _mind_slot(path, rows, slots)
        if not slot:
            continue
        bucket = says.setdefault(slot, [])
        for row in rows:
            if row.get("kind") != "call" or row.get("tool") != "say" or not row.get("ok"):
                continue
            text = str((row.get("args") or {}).get("text") or "")
            if text:
                bucket.append((float(row["ts"]), text))
    for bucket in says.values():
        bucket.sort()
    return says


def load_facts(knowledge: Path, run: Path, workplaces: list[Path]) -> Facts:
    births, deaths, records, proposals = load_knowledge(knowledge)
    events = _load_jsonl(run / "events.jsonl") if (run / "events.jsonl").is_file() else []
    retire_events = [
        (_iso_to_ts(str(row.get("ts", ""))), int((row.get("payload") or {}).get("serial", 0)))
        for row in events
        if row.get("type") == "life.retire" and isinstance(row.get("payload"), dict)
    ]
    retire_events = [(ts, serial) for ts, serial in retire_events if ts > 0 and serial > 0]
    runner_lines, runner_slots = load_runner(run)
    slots = {b.slot for b in births.values()}
    says = load_mind_says(run, slots)
    server_chat: list[tuple[str, str]] = []
    server_service = 0
    mc = run / "mc-server.log"
    if mc.is_file():
        for line in mc.read_text(encoding="utf-8", errors="replace").splitlines():
            # the console prefixes its own timestamp/thread header, so the uc marker sits
            # mid-line; this is the scrub cross-check, not the authoritative check
            if any(prefix in line for prefix in SERVICE_PREFIXES):
                server_service += 1
            match = CHAT_LINE_RE.search(line)
            if match:
                server_chat.append((match.group(1), match.group(2).strip()))
    gateway = None
    gateway_path = run / "gateway.json"
    if gateway_path.is_file():
        try:
            gateway = json.loads(gateway_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            gateway = None
    hermes_state = None
    state_path = run / "hermes-state.json"
    if state_path.is_file():
        try:
            hermes_state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            hermes_state = None
    return Facts(
        births=births,
        deaths=deaths,
        retire_events=retire_events,
        records=records,
        proposals=proposals,
        runner_lines=runner_lines,
        runner_slots=runner_slots,
        says=says,
        server_chat=server_chat,
        server_service=server_service,
        gateway=gateway,
        hermes_state=hermes_state,
        hermes_messages=run / "hermes-messages.jsonl"
        if (run / "hermes-messages.jsonl").is_file()
        else None,
    )


# --- helpers ---------------------------------------------------------------------------------


def _norm_words(text: str) -> list[str]:
    lowered = text.lower().replace("ё", "е")
    return [w for w in re.split(r"[^\wа-яё]+", lowered, flags=re.UNICODE) if w]


def _quoted_terms(claim: str) -> list[str]:
    """Term names live inside a term record's claim («семь», "creeper kin")."""
    return re.findall(r"[«\"']([^»\"']{2,40})[»\"']", claim)


def _lesson_line(body: str) -> str:
    for line in body.splitlines():
        if line.strip().lower().startswith("урок"):
            return line.split(":", 1)[-1] if ":" in line else line
    return body


def _lesson_ngrams(body: str) -> set[tuple[str, ...]]:
    words = [w for w in _norm_words(_lesson_line(body)) if len(w) >= 3]
    return {tuple(words[i : i + 3]) for i in range(len(words) - 2)}


def _reference_kind(reply: str, pred: Birth | None, records: list[dict], lesson: set[tuple[str, ...]]) -> str | None:
    if pred is not None and format_serial(pred.serial) in reply:
        return "serial"
    terms: list[str] = []
    if pred is not None:
        for record in records:
            if record.get("kind") != "term" or record.get("author") != pred.serial:
                continue
            if record.get("status") == "retired":
                continue
            terms.extend(_quoted_terms(str(record.get("claim", ""))))
    lowered = reply.lower()
    for term in terms:
        if term.lower() in lowered:
            return "term"
    words = _norm_words(reply)
    for i in range(len(words) - 2):
        if tuple(words[i : i + 3]) in lesson:
            return "lesson"
    return None


def _personas(workplaces: list[Path]) -> dict[int, Path]:
    """serial -> the PERSONA.md of her workplace, live (agent/) or archived."""
    found: dict[int, Path] = {}
    for root in workplaces:
        for sub in ("agent", "archive"):
            for child in sorted((root / sub).glob("*")) if (root / sub).is_dir() else []:
                persona = child / "PERSONA.md"
                if persona.is_file():
                    try:
                        found[int(child.name)] = persona
                    except ValueError:
                        continue
    return found


def _memory_files(workplaces: list[Path], serial: int) -> list[Path]:
    out: list[Path] = []
    for root in workplaces:
        for sub in ("agent", "archive"):
            base = root / sub / format_serial(serial)
            if (base / "memory").is_dir():
                out.extend(p for p in (base / "memory").rglob("*") if p.is_file() and p.stat().st_size)
    return out


# --- the items -------------------------------------------------------------------------------


@dataclass
class Item:
    key: str
    title: str
    status: str = ""  # pass | fail | n-a (set by every item builder)
    reason: str = ""
    lines: list[str] = field(default_factory=list)

    def markdown(self) -> str:
        head = f"### {self.title} — {self.status.upper()}"
        if self.reason:
            head += f"\n\n{self.reason}"
        body = "\n".join(self.lines).strip()
        return head + (f"\n\n{body}" if body else "")


def item_generations(facts: Facts, start: float, end: float) -> Item:
    window = [b for b in facts.births.values() if start <= b.born_ts <= end]
    item = Item("generations", "1. Поколения по слотам")
    if not window:
        item.status, item.reason = "n-a", "в окне нет ни одного рождения — критерий не проверяем"
        return item
    slots = sorted({b.slot for b in window})
    item.lines.append("| слот | узел | родился (UTC) | жил | причина смерти |")
    item.lines.append("|---|---|---|---|---|")
    for slot in slots:
        for birth in sorted((b for b in window if b.slot == slot), key=lambda b: b.born_ts):
            death = facts.deaths.get(birth.serial)
            lived = "жива на конец окна" if death is None else f"{death.lifetime_s // 60} мин"
            cause = "—" if death is None else death.cause
            born = dt.datetime.fromtimestamp(birth.born_ts, dt.timezone.utc).strftime("%H:%M:%S")
            item.lines.append(f"| {slot} | {format_serial(birth.serial)} | {born} | {lived} | {cause} |")
    item.status = "pass" if len(window) >= MIN_GENERATIONS else "fail"
    item.reason = f"рождений в окне: {len(window)} (нужно ≥ {MIN_GENERATIONS})"
    return item


def item_traits(facts: Facts, window_births: list[Birth], workplaces: list[Path]) -> Item:
    item = Item("traits", "2. Черты: свои у каждого, в birth.json и в рабочем месте")
    personas = _personas(workplaces)
    problems: list[str] = []
    vectors: dict[tuple, int] = {}
    for birth in window_births:
        traits = birth.traits
        missing = [k for k in TRAIT_KEYS if not isinstance(traits.get(k), int)]
        if missing or not str(traits.get("quirk") or "").strip() or not isinstance(
            traits.get("seed"), int
        ):
            problems.append(f"{format_serial(birth.serial)}: неполные черты в birth.json ({', '.join(missing) or 'quirk/seed'})")
        if birth.serial not in personas:
            problems.append(f"{format_serial(birth.serial)}: нет PERSONA.md в рабочем месте (agent/ или archive/)")
        vector = tuple(traits.get(k) for k in TRAIT_KEYS) + (str(traits.get("quirk")),)
        vectors[vector] = vectors.get(vector, 0) + 1
    duplicates = [count for count in vectors.values() if count > 1]
    if window_births:
        item.lines.append("| черта | мин | макс |")
        item.lines.append("|---|---|---|")
        for key in TRAIT_KEYS:
            values = [b.traits.get(key) for b in window_births if isinstance(b.traits.get(key), int)]
            if values:
                item.lines.append(f"| {key} | {min(values)} | {max(values)} |")
        quirks = "; ".join(
            f"{format_serial(b.serial)}: {b.traits.get('quirk')}" for b in window_births
        )
        item.lines.append(f"\nЧерты-занятки: {quirks or '—'}")
    if not window_births:
        item.status, item.reason = "n-a", "нет рождений в окне"
        return item
    if duplicates:
        problems.append(f"у {sum(duplicates)} узлов совпадает весь набор черт")
    item.lines.extend(f"- проблема: {p}" for p in problems)
    item.status = "pass" if not problems else "fail"
    if not problems:
        item.reason = f"у всех {len(window_births)} узлов окна полные черты и PERSONA.md, дублей нет"
    else:
        item.reason = f"проблем: {len(problems)}"
    return item


def item_inheritance(facts: Facts, window_births: list[Birth], start: float, end: float) -> Item:
    item = Item("inheritance", "3. Ссылка на предшественницу в первых 3 репликах")
    rows: list[tuple[Birth, Birth | None, list[str], str | None]] = []
    eligible = 0
    referenced = 0
    for birth in sorted(window_births, key=lambda b: b.born_ts):
        preds = [
            b
            for b in facts.births.values()
            if b.slot == birth.slot and b.serial < birth.serial
        ]
        pred = max(preds, key=lambda b: b.serial) if preds else None
        pred_death = facts.deaths.get(pred.serial) if pred else None
        lesson = _lesson_ngrams(pred_death.body) if pred_death else set()
        own_death = facts.deaths.get(birth.serial)
        says = [
            text
            for ts, text in facts.says.get(birth.slot, [])
            if birth.born_ts <= ts <= end
            and (own_death is None or ts <= own_death.died_ts)
        ][:FIRST_REPLIES]
        kind = None
        for text in says:
            kind = _reference_kind(text, pred, facts.records, lesson)
            if kind:
                break
        if pred is None:
            continue  # the first node of a slot has nobody to reference
        eligible += 1
        if kind:
            referenced += 1
        rows.append((birth, pred, says, kind))
    if eligible == 0:
        item.status, item.reason = "n-a", "в окне нет узлов с предшественницей"
        return item
    share = referenced / eligible
    for birth, pred, says, kind in rows:
        mark = kind or "нет ссылки"
        first = " / ".join(f"«{text}»" for text in says) or "(молчала)"
        item.lines.append(f"- {format_serial(birth.serial)} (после {format_serial(pred.serial)}): {mark} — {first}")
    item.status = "pass" if share >= REF_SHARE else "fail"
    item.reason = f"{referenced} из {eligible} новых узлов ссылаются на предшественницу ({share:.0%}, нужно ≥ {REF_SHARE:.0%})"
    return item


def item_wills(facts: Facts, start: float, end: float) -> Item:
    item = Item("wills", "4. Смерти: все с will.md, доля аварийных")
    deaths = [d for d in facts.deaths.values() if start <= d.died_ts <= end]
    without = [
        serial
        for ts, serial in facts.retire_events
        if start <= ts <= end and serial not in facts.deaths
    ]
    emergencies = [d for d in deaths if d.emergency]
    for death in sorted(deaths, key=lambda d: d.died_ts):
        died = dt.datetime.fromtimestamp(death.died_ts, dt.timezone.utc).strftime("%H:%M:%S")
        quote = next((ln for ln in death.body.splitlines() if ln.strip()), "")
        kind = "аварийное" if death.emergency else "агента"
        item.lines.append(
            f"- {format_serial(death.serial)} ({death.slot}), {died}, {death.cause}, "
            f"{death.lifetime_s // 60} мин, воля {kind}: «{quote[:160]}»"
        )
    if not deaths:
        item.status, item.reason = "n-a", "в окне нет смертей"
        return item
    if without:
        item.lines.append(f"- БЕЗ will.md: {', '.join(format_serial(s) for s in without)}")
    item.status = "pass" if not without else "fail"
    item.reason = (
        f"смертей в окне: {len(deaths)}, без will.md: {len(without)}; "
        f"аварийных: {len(emergencies)} ({len(emergencies) / len(deaths):.0%})"
    )
    return item


def item_sessions(facts: Facts, start: float, end: float) -> Item:
    item = Item("sessions", "5. Сессии: перезапуски раннером, зависания")
    problems: list[str] = []
    counts: dict[str, dict[str, int]] = {}
    for index, row in enumerate(facts.runner_lines):
        ts = _iso_to_ts(str(row.get("ts", "")))
        if not (start <= ts <= end):
            continue
        slot = facts.runner_slots.get(index, "?")
        bucket = counts.setdefault(slot, {})
        type_ = str(row.get("type", ""))
        bucket[type_] = bucket.get(type_, 0) + 1
        if type_ == "session.ended":
            ran = float(row.get("ran_s") or 0)
            if ran > HANG_S:
                problems.append(f"{slot}: сессия {ran / 60:.0f} мин без смерти (зависание)")
        if type_ in ("runner.error", "session.spawn_failed", "will.parked"):
            problems.append(f"{slot}: {type_}: {row.get('error') or row.get('status') or ''}")
        if type_ == "will.session_timeout":
            problems.append(f"{slot}: воля зависла и снята таймаутом (serial {row.get('serial')})")
    if not counts:
        item.status, item.reason = "n-a", "нет журналов раннера в артефактах прогона"
        return item
    for slot in sorted(counts):
        bucket = counts[slot]
        item.lines.append(
            f"- {slot}: сессий завершено {bucket.get('session.ended', 0)}, "
            f"перезапущено по смерти {bucket.get('session.stopped_for_death', 0)}, "
            f"ожидания ключей/квоты {bucket.get('harness.waits', 0)}/{bucket.get('quota.waits', 0)}, "
            f"воли {bucket.get('will.session', 0)} (таймаутов {bucket.get('will.session_timeout', 0)})"
        )
    item.status = "pass" if not problems else "fail"
    item.reason = "зависаний и ошибок раннера нет" if not problems else f"проблем: {len(problems)}"
    item.lines.extend(f"- проблема: {p}" for p in problems)
    return item


def _gateway_total(gateway: dict) -> float | None:
    for payload, field in ((gateway.get("per_key"), "total_spend"), (gateway.get("per_model"), "total_spend")):
        rows = payload if isinstance(payload, list) else (payload or {}).get("data", []) if payload else []
        total = 0.0
        seen = False
        for entry in rows if isinstance(rows, list) else []:
            if isinstance(entry, dict) and entry.get(field) is not None:
                total += float(entry[field])
                seen = True
        if seen:
            return total
    return None


def _credits(model: str, tin: int, cache_read: int, tout: int) -> float | None:
    try:  # the Flash-formula of the T-024 spike, reused verbatim
        sys.path.insert(0, str(Path(__file__).resolve().parent / "harness_spike"))
        import importlib

        summarize = importlib.import_module("summarize")
        return summarize.glm_credits(model, tin, cache_read, tout)
    except Exception:
        return None


def item_cost(facts: Facts, start: float, end: float, window_s: float) -> Item:
    item = Item("cost", "6. Расход: деньги по ключам шлюза и квота")
    hours = window_s / 3600 or 1.0
    total = _gateway_total(facts.gateway or {})
    if total is None:
        item.lines.append("- gateway.json отсутствует или пуст: деньги шлюза не измерены")
    else:
        item.lines.append(f"- шлюз: ${total:.5f} за окно → ${total / hours:.5f}/ч (потолок $0.35/ч)")
        for entry in (facts.gateway.get("per_model") or [])[:8]:
            name = entry.get("model") or entry.get("model_group") or "?"
            item.lines.append(f"  - {name}: ${float(entry.get('total_spend') or 0):.5f}")
    if facts.hermes_state:
        rows = facts.hermes_state.get("session_model_usage") or []
        tin = cache = tout = 0
        credited = 0.0
        models: dict[str, int] = {}
        for row in rows:
            try:
                last = float(row.get("last_seen") or 0)
                if not (start <= last <= end):
                    continue
                tin += int(float(row.get("input_tokens") or 0))
                cache += int(float(row.get("cache_read_tokens") or 0))
                tout += int(float(row.get("output_tokens") or 0))
                model = str(row.get("model") or "")
                models[model] = models.get(model, 0) + int(float(row.get("api_call_count") or 0))
                value = _credits(model, int(float(row.get("input_tokens") or 0)), int(float(row.get("cache_read_tokens") or 0)), int(float(row.get("output_tokens") or 0)))
                if value is not None:
                    credited += value
            except (TypeError, ValueError):
                continue
        item.lines.append(
            f"- квота Hermes: {tin} вх / {cache} кеш-чтение / {tout} вых; "
            f"≈ {credited:.0f} кредитов за окно → {credited / hours:.0f} кредитов/ч; "
            f"вызовы: {', '.join(f'{m}: {n}' for m, n in sorted(models.items())) or '—'}"
        )
    else:
        item.lines.append("- hermes-state.json отсутствует: квота не измерена")
    if total is None:
        item.status = "n-a"
        item.reason = "деньги шлюза не измерены — критерий расхода не проверяем (см. строку выше)"
    else:
        rate = total / hours
        item.status = "pass" if rate <= COST_CEILING_PER_H else "fail"
        item.reason = f"${rate:.5f}/ч против $0.35/ч"
    return item


def item_self_correction(
    facts: Facts, window_births: list[Birth], knowledge: Path, workplaces: list[Path], start: float, end: float
) -> Item:
    item = Item("self_correction", "7. Самоправка: записи в памяти сестёр, решения Last Order")
    problems: list[str] = []
    for birth in window_births:
        core = knowledge / "nodes" / format_serial(birth.serial) / "core.md"
        core_ok = core.is_file() and len(core.read_text(encoding="utf-8", errors="replace").strip()) >= 30
        memory = _memory_files(workplaces, birth.serial)
        if not core_ok and not memory:
            problems.append(f"{format_serial(birth.serial)}: нет ни core.md, ни файлов в memory/ рабочего места")
        else:
            source = "core.md" if core_ok else f"memory/ ({len(memory)} файлов)"
            item.lines.append(f"- {format_serial(birth.serial)}: память есть ({source})")
    accepted = [
        p
        for p in facts.proposals
        if p.get("status") == "accepted"
        and isinstance(p.get("decision"), dict)
        and p["decision"].get("by") == "last_order"
        and start <= _iso_to_ts(str(p["decision"].get("ts") or p.get("ts") or "")) <= end
    ]
    for proposal in accepted[:5]:
        why = str((proposal.get("decision") or {}).get("why", ""))
        item.lines.append(f"- предложение {proposal.get('id')}: принято Last Order — «{why[:120]}»")
    if not accepted:
        problems.append("в окне нет ни одного принятого Last Order предложения")
    if not window_births:
        item.status, item.reason = "n-a", "нет рождений в окне"
        return item
    item.status = "pass" if not problems else "fail"
    item.reason = (
        f"память есть у всех узлов окна; предложений Last Order принято: {len(accepted)}"
        if not problems
        else f"проблем: {len(problems)}"
    )
    item.lines.extend(f"- проблема: {p}" for p in problems)
    return item


def _service_leaks(path: Path) -> list[str]:
    """uc service lines inside what the harness fed the agent.

    A real leak rides an observe result: role=tool content whose decoded JSON carries
    a `chat` array. Assistant rows are the agent's own speech — the sisters write
    "WARNING: ..." into their own notes (seen in the 2026-10-23 run), and that is not
    a leak, so only user and tool rows are scanned, tool rows through the chat array
    (is_service_line, the observe rule itself), user rows line by line.
    """
    hits: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("role") not in ("user", "tool"):
            continue
        content = str(row.get("content") or "")
        texts = content.splitlines()
        if row.get("role") == "tool":
            try:
                decoded = json.loads(content)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict) and isinstance(decoded.get("chat"), list):
                texts = [str(item) for item in decoded["chat"]]
        for text in texts:
            if is_service_line(text):
                hits.append(f"{row.get('role')}: {text.strip()[:160]}")
    return hits


def item_uc_lines(facts: Facts) -> Item:
    item = Item("uc_lines", "8. Служебные строки uc не попадают агенту")
    item.lines.append(
        f"- служебных строк uc в чате сервера за прогон: {facts.server_service} "
        "(их и должен был вычистить observe)"
    )
    if facts.hermes_messages is None:
        item.status = "n-a"
        item.reason = "нет дампа сообщений харнесса (hermes-messages.jsonl) — проверить нечем"
        return item
    hits = _service_leaks(facts.hermes_messages)
    item.lines.extend(f"- ПРОПУСК: {hit}" for hit in hits[:10])
    item.status = "pass" if not hits else "fail"
    item.reason = (
        f"в сообщениях агента служебных строк uc: {len(hits)}"
        + ("" if facts.server_service else " (в чате сервера их тоже не было — вычищать было нечего)")
    )
    return item


# --- the report ------------------------------------------------------------------------------


@dataclass
class Report:
    start: dt.datetime
    end: dt.datetime
    items: list[Item]

    @property
    def overall(self) -> str:
        # strict: an unverified (n-a) item means the criterion was not demonstrated
        return "PASS" if all(item.status == "pass" for item in self.items) else "FAIL"


def build_report(knowledge: Path, workplaces: list[Path], run: Path, *, to: float, window_s: float) -> Report:
    start = to - window_s
    facts = load_facts(knowledge, run, workplaces)
    window_births = sorted(
        (b for b in facts.births.values() if start <= b.born_ts <= to), key=lambda b: b.born_ts
    )
    items = [
        item_generations(facts, start, to),
        item_traits(facts, window_births, workplaces),
        item_inheritance(facts, window_births, start, to),
        item_wills(facts, start, to),
        item_sessions(facts, start, to),
        item_cost(facts, start, to, window_s),
        item_self_correction(facts, window_births, knowledge, workplaces, start, to),
        item_uc_lines(facts),
    ]
    return Report(
        start=dt.datetime.fromtimestamp(start, dt.timezone.utc),
        end=dt.datetime.fromtimestamp(to, dt.timezone.utc),
        items=items,
    )


def render(report: Report) -> str:
    lines = [
        "# Отчёт MVP",
        "",
        f"Окно: {report.start.isoformat(timespec='seconds')} .. {report.end.isoformat(timespec='seconds')}"
        f" ({(report.end - report.start).total_seconds() / 3600:g} ч).",
        "",
        f"**Итог: {report.overall}**",
        "",
        "| # | Пункт | Статус |",
        "|---|---|---|",
    ]
    for item in report.items:
        lines.append(f"| {item.key} | {item.title.split('. ', 1)[-1]} | {item.status.upper()} |")
    lines.append("")
    for item in report.items:
        lines.append(item.markdown())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_span(text: str) -> float:
    seconds = 0.0
    for count, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([dhm])", text.lower()):
        seconds += float(count) * {"d": 86400, "h": 3600, "m": 60}[unit]
    if seconds <= 0 or re.sub(r"(\d+(?:\.\d+)?)\s*[dhm]|[\s,]", "", text.lower()):
        sys.exit(f"mvp_report: cannot parse span {text!r} (want e.g. 90m, 2h)")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--knowledge", type=Path, required=True, help="the knowledge volume")
    parser.add_argument(
        "--workplaces",
        type=Path,
        action="append",
        required=True,
        help="a workplaces volume (one per slot; repeat or comma-separate)",
    )
    parser.add_argument("--run", type=Path, required=True, help="the run artifacts dir")
    parser.add_argument("--to", default=None, help="window end, ISO-8601 (default: now UTC)")
    parser.add_argument("--window", default="2h", help="window length, e.g. 2h, 90m (default 2h)")
    parser.add_argument("--out", type=Path, default=None, help="also write the markdown here")
    parser.add_argument("--json", action="store_true", help="machine-readable verdict instead of markdown")
    args = parser.parse_args(argv)

    if args.to:
        try:
            end = dt.datetime.fromisoformat(args.to.replace("Z", "+00:00")).timestamp()
        except ValueError:
            sys.exit(f"mvp_report: cannot parse --to {args.to!r} (want ISO-8601)")
    else:
        end = dt.datetime.now(dt.timezone.utc).timestamp()
    workplaces = [p for value in args.workplaces for p in map(Path, str(value).split(","))]

    report = build_report(
        args.knowledge, workplaces, args.run, to=end, window_s=parse_span(args.window)
    )
    if args.json:
        print(
            json.dumps(
                {
                    "window": {
                        "start": report.start.isoformat(),
                        "end": report.end.isoformat(),
                    },
                    "overall": report.overall,
                    "items": [
                        {"key": i.key, "status": i.status, "reason": i.reason}
                        for i in report.items
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        text = render(report)
        print(text, end="")
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
