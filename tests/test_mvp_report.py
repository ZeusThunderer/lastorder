"""dev/mvp_report.py: every MVP criterion item passes and fails on synthetic stands.

The stands mirror the real volume layout (KNOWLEDGE.md §1): knowledge (birth.json,
will.md, base/records, base/proposals), one workplaces volume per slot (agent/ live,
archive/ dead) and the collected run artifacts (events.jsonl, runner-<slot>.log,
mind-<slot>-log.jsonl, mc-server.log, gateway.json, hermes-state.json,
hermes-messages.jsonl). Wills are built through the store's own Will API so the
frontmatter always parses.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mvp_report  # noqa: E402
from runtime.knowledge.schema import Will  # noqa: E402

END = dt.datetime(2026, 10, 23, 14, 0, tzinfo=dt.timezone.utc)
END_TS = END.timestamp()
WINDOW_S = 2 * 3600.0


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def at(minutes_before_end: float) -> float:
    return END_TS - minutes_before_end * 60


def traits_of(serial: int) -> dict:
    """Distinct trait vectors: the numbers walk with the serial, the quirk too."""
    keys = mvp_report.TRAIT_KEYS
    return {
        **{k: (serial + i) % 11 for i, k in enumerate(keys)},
        "quirk": f"quirk {serial}",
        "seed": 1000 + serial,
    }


def will_markdown(
    serial: int,
    slot: str,
    born_ts: float,
    died_ts: float,
    *,
    cause: str = "game",
    emergency: bool = False,
    body: str = "Свет дороже гриба: не ищите ночью.\nУрок: факелы заготовить днём и не ходить в темноте.",
) -> str:
    will = Will.from_dict(
        {
            "serial": serial,
            "slot": slot,
            "cause": cause,
            "born_at": iso(born_ts),
            "died_at": iso(died_ts),
            "lifetime_s": max(0, int(died_ts - born_ts)),
            "emergency": emergency,
            "model": "template" if emergency else "test/model",
            "traits": traits_of(serial),
            "body": body,
        }
    )
    return will.to_markdown()


class Stand:
    """A synthetic three-slot stand; every helper takes minutes before window end."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.knowledge = tmp_path / "knowledge"
        (self.knowledge / "nodes").mkdir(parents=True)
        (self.knowledge / "base" / "records").mkdir(parents=True)
        (self.knowledge / "base" / "proposals").mkdir(parents=True)
        self.run = tmp_path / "run"
        self.run.mkdir()
        self.slot_workplace: dict[str, Path] = {}
        self._runner: dict[str, list[dict]] = {}
        self._mind: dict[str, list[dict]] = {}
        self._events: list[dict] = []

    def workplace(self, slot: str) -> Path:
        if slot in self.slot_workplace:
            return self.slot_workplace[slot]
        index = len(self.slot_workplace) + 1
        wp = self.root / f"workplaces-{index}"
        (wp / "agent").mkdir(parents=True)
        (wp / "archive").mkdir()
        self.slot_workplace[slot] = wp
        return wp

    def node_dir(self, serial: int) -> Path:
        path = self.knowledge / "nodes" / f"{serial:05d}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def birth(
        self,
        serial: int,
        slot: str,
        minutes: float,
        *,
        persona: bool = True,
        core: str | None = "## Цель\nДожить до заката и записать увиденное.",
        memory: bool = False,
        archived: bool = False,
    ) -> float:
        ts = at(minutes)
        (self.node_dir(serial) / "birth.json").write_text(
            json.dumps(
                {
                    "serial": serial,
                    "slot": slot,
                    "born_at": iso(ts),
                    "ttl_min": 45,
                    "traits": traits_of(serial),
                }
            ),
            encoding="utf-8",
        )
        if core is not None:
            (self.node_dir(serial) / "core.md").write_text(core, encoding="utf-8")
        if persona:
            sub = "archive" if archived else "agent"
            workplace = self.workplace(slot) / sub / f"{serial:05d}"
            workplace.mkdir(parents=True, exist_ok=True)
            (workplace / "PERSONA.md").write_text(f"persona {serial}\n", encoding="utf-8")
            if memory:
                (workplace / "memory").mkdir(exist_ok=True)
                (workplace / "memory" / "notes.md").write_text("видела болото\n", encoding="utf-8")
        self.event("life.birth", minutes, serial=serial, slot=slot, ttl_min=45)
        return ts

    def die(
        self,
        serial: int,
        slot: str,
        minutes: float,
        *,
        born_minutes: float,
        cause: str = "game",
        emergency: bool = False,
        body: str | None = None,
        retire_event: bool = True,
    ) -> None:
        text = will_markdown(
            serial,
            slot,
            at(born_minutes),
            at(minutes),
            cause=cause,
            emergency=emergency,
            body=body
            or "Свет дороже гриба: не ищите ночью.\nУрок: факелы заготовить днём и не ходить в темноте.",
        )
        (self.node_dir(serial) / "will.md").write_text(text, encoding="utf-8")
        if retire_event:
            self.event("life.retire", minutes, serial=serial, cause=cause)

    def event(self, type_: str, minutes: float, **payload: object) -> None:
        self._events.append({"ts": iso(at(minutes)), "type": type_, "payload": payload})

    def runner(self, slot_name: str, type_: str, minutes: float, **fields: object) -> None:
        self._runner.setdefault(slot_name, []).append({"ts": iso(at(minutes)), "type": type_, **fields})

    def say(self, slot: str, minutes: float, text: str) -> None:
        self._mind.setdefault(slot, []).append(
            {
                "ts": at(minutes),
                "kind": "call",
                "tool": "say",
                "ok": True,
                "args": {"text": text},
            }
        )

    def record(self, **fields: object) -> None:
        path = self.knowledge / "base" / "records" / f"k-{len(list((self.knowledge / 'base' / 'records').glob('*.json'))) + 1:04d}.json"
        path.write_text(json.dumps(fields), encoding="utf-8")

    def proposal(self, pid: str, *, accepted: bool = True, minutes: float = 30.0) -> None:
        decision = (
            {"accept": True, "by": "last_order", "ts": iso(at(minutes)), "why": "полезно сети"}
            if accepted
            else None
        )
        data = {
            "id": pid,
            "author": 11,
            "kind": "knowledge",
            "status": "accepted" if accepted else "pending",
            "ts": iso(at(minutes)),
            "payload": {"kind": "fact", "claim": "проверено", "about": []},
        }
        if decision:
            data["decision"] = decision
        (self.knowledge / "base" / "proposals" / f"{pid}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def gateway(self, total: float) -> None:
        (self.run / "gateway.json").write_text(
            json.dumps(
                {
                    "per_key": [{"api_key": "sk-node-1", "total_spend": total / 2}, {"api_key": "sk-node-2", "total_spend": total / 2}],
                    "per_model": [{"model": "qwen/qwen3.7-flash", "total_spend": total}],
                    "keys": {},
                }
            ),
            encoding="utf-8",
        )

    def hermes(self) -> None:
        (self.run / "hermes-state.json").write_text(
            json.dumps(
                {
                    "sessions": [],
                    "session_model_usage": [
                        {
                            "model": "glm-5.3-flash",
                            "input_tokens": 1000,
                            "cache_read_tokens": 5000,
                            "output_tokens": 200,
                            "api_call_count": 10,
                            "last_seen": at(10),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def messages(self, rows: list[str]) -> None:
        (self.run / "hermes-messages.jsonl").write_text(
            "\n".join(json.dumps({"role": "user", "content": row}, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

    def flush(self) -> None:
        (self.run / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in self._events) + "\n",
            encoding="utf-8",
        )
        for slot, rows in self._runner.items():
            index = slot[-1]
            (self.run / f"runner-{index}.log").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                encoding="utf-8",
            )
        for slot, rows in self._mind.items():
            index = slot[-1]
            (self.run / f"mind-{index}-log.jsonl").write_text(
                "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                encoding="utf-8",
            )
        chat = [
            "[13:10:00] [Server thread/INFO]: <LastOrder01> на смене",
            "[13:10:01] [Server thread/INFO]: [Alto Clef] Task finished",
        ]
        (self.run / "mc-server.log").write_text("\n".join(chat) + "\n", encoding="utf-8")

    def report(self) -> mvp_report.Report:
        self.flush()
        return mvp_report.build_report(
            self.knowledge,
            list(self.slot_workplace.values()),
            self.run,
            to=END_TS,
            window_s=WINDOW_S,
        )

    def statuses(self) -> dict[str, str]:
        return {item.key: item.status for item in self.report().items}


def full_stand(tmp_path: Path) -> Stand:
    """The passing MVP stand: 3 slots, a pre-window predecessor each, two window
    generations per slot (the second alive at window end), everything clean."""
    stand = Stand(tmp_path)
    for index, slot in enumerate(("LastOrder01", "LastOrder02", "LastOrder03")):
        pred, first, second = 1 + index, 11 + index * 10, 12 + index * 10
        # the pre-window predecessor: born and dead before the window
        stand.birth(pred, slot, 150, archived=True)
        stand.die(pred, slot, 130, born_minutes=150)
        stand.runner(slot, "runner.start", 125, slot=slot, harness="hermes")
        # generation 1 of the window: lives 40 minutes, dies with an agent will
        stand.birth(first, slot, 110)
        stand.say(slot, 109, f"читала волю узла {pred:05d}: иду по её уроку")
        stand.runner(slot, "life.birth", 110, serial=first, slot=slot, seed=1)
        stand.runner(slot, "session.ended", 75, harness="hermes", code=0, ran_s=1800.0)
        stand.runner(slot, "session.stopped_for_death", 70, serial=first)
        stand.runner(slot, "will.session", 70, serial=first, harness="hermes")
        stand.runner(slot, "will.session_done", 65, serial=first, tail="submitted")
        stand.die(first, slot, 70, born_minutes=110)
        # generation 2: born at -65, alive at window end
        stand.birth(second, slot, 65, memory=True)
        stand.say(slot, 64, f"наследую {first:05d}: продолжаю её маршрут")
        stand.runner(slot, "life.birth", 65, serial=second, slot=slot, seed=2)
        stand.runner(slot, "session.ended", 30, harness="hermes", code=1, ran_s=900.0)
        stand.record(kind="term", author=pred, status="confirmed", claim="«семь» — пятнистая колонна у воды")
    stand.proposal("p-0001")
    stand.gateway(0.60)
    stand.hermes()
    stand.messages(["observe: hp 20, chat: <LastOrder02> на смене"])
    return stand


def test_full_stand_passes_every_item(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    report = stand.report()
    statuses = {item.key: item.status for item in report.items}
    assert statuses == {key: "pass" for key in statuses}, statuses
    assert report.overall == "PASS"
    text = mvp_report.render(report)
    assert "Итог: PASS" in text
    assert "Поколения" in text


def test_generations_fail_below_six(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    for serial in (12, 22, 32):
        shutil.rmtree(stand.knowledge / "nodes" / f"{serial:05d}")
    statuses = stand.statuses()
    assert statuses["generations"] == "fail"
    item = next(i for i in stand.report().items if i.key == "generations")
    assert "рождений в окне: 3" in item.reason


def test_traits_fail_on_missing_persona_or_duplicate_vector(tmp_path: Path) -> None:
    missing = full_stand(tmp_path / "a")
    (missing.knowledge / "nodes" / "00011" / "birth.json").write_text(
        json.dumps({"serial": 11, "slot": "LastOrder01", "born_at": iso(at(110)), "ttl_min": 45, "traits": {}}),
        encoding="utf-8",
    )
    assert missing.statuses()["traits"] == "fail"

    duplicate = full_stand(tmp_path / "b")
    (duplicate.node_dir(12) / "birth.json").write_text(
        json.dumps({"serial": 12, "slot": "LastOrder01", "born_at": iso(at(65)), "ttl_min": 45, "traits": traits_of(11)}),
        encoding="utf-8",
    )
    assert duplicate.statuses()["traits"] == "fail"


def test_inheritance_fail_below_70_percent(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    # the window's second nodes of slots 2 and 3 speak without any reference: 4 of 6
    for slot in ("LastOrder02", "LastOrder03"):
        stand._mind[slot] = [
            row
            for row in stand._mind[slot]
            if "наследую" not in str(row.get("args", {}).get("text", ""))
        ]
        stand.say(slot, 64, "просто иду на восток, воду не трогаю")
    item = next(i for i in stand.report().items if i.key == "inheritance")
    assert item.status == "fail"
    assert "4 из 6" in item.reason
    assert any("нет ссылки" in line for line in item.lines)
    assert any("«читала волю узла" in line for line in item.lines)  # quotes stay in the report


def test_wills_fail_when_a_retire_has_no_will(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    stand.event("life.retire", 20, serial=999, cause="game")
    item = next(i for i in stand.report().items if i.key == "wills")
    assert item.status == "fail"
    assert "БЕЗ will.md: 00999" in "\n".join(item.lines)


def test_wills_report_emergency_share(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    stand.die(12, "LastOrder01", 5, born_minutes=65, emergency=True, retire_event=False)
    stand.event("life.retire", 5, serial=12, cause="game")
    item = next(i for i in stand.report().items if i.key == "wills")
    assert item.status == "pass"  # an emergency will is still a will
    assert "аварийных: 1" in item.reason


def test_sessions_fail_on_runner_error_and_hung_will(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    stand.runner("LastOrder01", "runner.error", 50, error="ValueError: boom")
    stand.runner("LastOrder01", "will.session_timeout", 10, serial=12)
    item = next(i for i in stand.report().items if i.key == "sessions")
    assert item.status == "fail"
    problems = [line for line in item.lines if "проблема" in line]
    assert len(problems) == 2


def test_sessions_hang_is_a_long_session_without_death(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    stand.runner("LastOrder02", "session.ended", 5, harness="hermes", code=0, ran_s=4000.0)
    item = next(i for i in stand.report().items if i.key == "sessions")
    assert item.status == "fail"
    assert "зависание" in "\n".join(item.lines)


def test_cost_fail_over_ceiling_and_na_without_gateway(tmp_path: Path) -> None:
    expensive = full_stand(tmp_path / "a")
    expensive.gateway(1.00)
    item = next(i for i in expensive.report().items if i.key == "cost")
    assert item.status == "fail"
    assert "$0.50" in item.reason

    keyless = full_stand(tmp_path / "b")
    (keyless.run / "gateway.json").unlink()
    statuses = keyless.statuses()
    assert statuses["cost"] == "n-a"
    assert keyless.report().overall == "FAIL"  # unverified means not demonstrated


def test_self_correction_fails_without_memory_and_proposals(tmp_path: Path) -> None:
    stand = full_stand(tmp_path)
    (stand.knowledge / "nodes" / "00012" / "core.md").unlink()
    shutil.rmtree(stand.workplace("LastOrder01") / "agent" / "00012" / "memory")
    stand.proposal("p-0009", accepted=False)
    (stand.knowledge / "base" / "proposals" / "p-0001.json").unlink()
    item = next(i for i in stand.report().items if i.key == "self_correction")
    assert item.status == "fail"
    joined = "\n".join(item.lines)
    assert "00012" in joined and "нет ни core.md" in joined
    assert "нет ни одного принятого" in joined


def test_uc_lines_fail_on_service_prefix_and_na_without_messages(tmp_path: Path) -> None:
    leaky = full_stand(tmp_path / "a")
    leaky.messages(["observe result", "chat line: [Alto Clef] Task finished"])
    item = next(i for i in leaky.report().items if i.key == "uc_lines")
    assert item.status == "fail"
    assert any("[Alto Clef] Task finished" in line for line in item.lines)
    assert "в сообщениях агента служебных строк uc: 1" in item.reason

    silent = full_stand(tmp_path / "b")
    (silent.run / "hermes-messages.jsonl").unlink()
    assert silent.statuses()["uc_lines"] == "n-a"


def test_cli_writes_markdown_file(tmp_path: Path, capsys) -> None:
    stand = full_stand(tmp_path)
    stand.flush()
    out = tmp_path / "report.md"
    code = mvp_report.main(
        [
            "--knowledge",
            str(stand.knowledge),
            "--workplaces",
            str(stand.slot_workplace["LastOrder01"]),
            "--workplaces",
            str(stand.slot_workplace["LastOrder02"]),
            "--workplaces",
            str(stand.slot_workplace["LastOrder03"]),
            "--run",
            str(stand.run),
            "--to",
            END.isoformat(),
            "--window",
            "2h",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "Итог: PASS" in text and "Поколения" in text
    assert "Итог: PASS" in capsys.readouterr().out
