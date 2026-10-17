"""Lifecycle.on_death: the strict order, the hive-down path, the respawn retries
(NETWORK.md section 7 and section 9; T-013 acceptance 4-6).

Everything runs on stubs of the ports: a registry whose retire/birth can be switched
off and on (HiveUnavailable/HiveRejected from runtime/hive/client.py, exactly what
HiveClient raises), a body, a memory, an LLM that always answers a good will. The
journal test pins the call order of the path; the offline tests pin the section 9
contract: the will parked in run/<slot>/pending/ exactly once, the respawn anyway,
both the delivery and the birth retried with a growing pause, and the will reaching
hive exactly once.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.game.py4j_body import BodyUnavailable
from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.knowledge.schema import ReflectionInput
from runtime.lastorder.death import DeathCause
from runtime.lastorder.lifecycle import Lifecycle, Rebirth
from runtime.lastorder.naming import preset
from runtime.lastorder.persona import Inheritance, Node
from runtime.lastorder.traits import Traits
from runtime.ports import BirthRecord
from test_will import GOOD_BODY, StubLLM

BORN = "2026-10-17T12:04:11+00:00"
DIED = "2026-10-17T12:41:53+00:00"


def node(serial: int = 14) -> Node:
    return Node(
        serial=serial,
        traits=Traits(3, 7, 5, 4, 9, quirk="считает крипера родственником", seed=4213),
    )


def birth(serial: int = 14) -> BirthRecord:
    return BirthRecord(serial=serial, born_at=BORN, ttl_min=45)


def reflection(serial: int = 14, cause: str = "game") -> ReflectionInput:
    return ReflectionInput(
        serial=serial, kind="death", cause=cause, last_action="рубил берёзу у воды"
    )


class JournalLLM(StubLLM):
    """The will call, journaled like every other step of the path."""

    def __init__(self, journal: list[str]):
        super().__init__(reply={"will": GOOD_BODY})
        self.journal = journal

    def complete_json(self, system: str, user: str) -> dict:
        out = super().complete_json(system, user)
        self.journal.append("will")
        return out


class FakeRegistry:
    """NodeRegistry on scripts: retire/birth raise or pass by preloaded queues."""

    def __init__(self, journal: list[str]):
        self.journal = journal
        self.retire_failures: list[Exception | None] = []
        self.birth_failures: list[Exception | None] = []
        self.retire_always: Exception | None = None  # a permanent 4xx, retrying is futile
        self.retire_calls: list[tuple[int, str, str]] = []
        self.birth_calls: list[str] = []
        self.next_serial = 15

    def retire(self, serial: int, cause: str, will_text: str) -> None:
        self.retire_calls.append((serial, cause, will_text))
        self.journal.append("retire")
        if self.retire_always is not None:
            raise self.retire_always
        if self.retire_failures:
            failure = self.retire_failures.pop(0)
            if failure is not None:
                raise failure

    def birth(self, slot: str, traits: Traits) -> BirthRecord:
        self.birth_calls.append(slot)
        self.journal.append("birth")
        if self.birth_failures:
            failure = self.birth_failures.pop(0)
            if failure is not None:
                raise failure
        record = BirthRecord(
            serial=self.next_serial,
            born_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ttl_min=45,
        )
        self.next_serial += 1
        return record


class FakeBody:
    def __init__(self, journal: list[str], failures: int = 0):
        self.journal = journal
        self.failures = failures
        self.respawn_calls = 0

    def respawn(self) -> None:
        self.respawn_calls += 1
        self.journal.append("respawn")
        if self.respawn_calls <= self.failures:
            raise BodyUnavailable("no living body within 60s after respawn")


class FakeMemory:
    def __init__(self, journal: list[str]):
        self.journal = journal
        self.calls: list[int] = []

    def inheritance(self, serial: int) -> Inheritance:
        self.calls.append(serial)
        self.journal.append("inheritance")
        return Inheritance(inherited_notes="завещание предшественника")


def write_quirks(tmp_path: Path) -> Path:
    path = tmp_path / "quirks.txt"
    path.write_text("# one\nсчитает крипера родственником\nне ест хлеб у воды\n", encoding="utf-8")
    return path


def make_life(tmp_path: Path, *, journal=None, registry=None, body=None, llm=None):
    journal = journal if journal is not None else []
    registry = registry if registry is not None else FakeRegistry(journal)
    body = body if body is not None else FakeBody(journal)
    llm = llm if llm is not None else StubLLM(reply={"will": GOOD_BODY})
    seeds = iter([101, 102, 103, 104])
    life = Lifecycle(
        slot="LastOrder01",
        body=body,
        registry=registry,
        memory=FakeMemory(journal),
        llm=llm,
        naming=preset("lab"),
        quirks_path=write_quirks(tmp_path),
        run_dir=tmp_path / "run",
        now=lambda: datetime.fromisoformat(DIED),
        sleep=lambda s: journal.append(f"sleep:{s:g}"),
        seed_factory=lambda: next(seeds),
        log=lambda type, payload=None: journal.append(f"log:{type}"),
    )
    return life, registry, body


def pending_path(tmp_path: Path, serial: int = 14) -> Path:
    return tmp_path / "run" / "LastOrder01" / "pending" / f"will-{serial:05d}.md"


# --- acceptance 4: the strict order -----------------------------------------------------


def test_on_death_calls_the_steps_in_the_documented_order(tmp_path):
    journal: list[str] = []
    llm = JournalLLM(journal)
    life, _, _ = make_life(tmp_path, journal=journal, llm=llm)
    rebirth = life.on_death(
        node(),
        birth(),
        DeathCause(cause="game", text="LastOrder01 was slain by Zombie"),
        reflection(),
        pause=lambda: journal.append("pause"),
        resume=lambda: journal.append("resume"),
    )
    order = [
        "pause",
        "will",  # the LLM call
        "retire",  # the will lands in hive before the body moves
        "respawn",
        "birth",
        "inheritance",
        "resume",
    ]
    core = [e for e in journal if e in order]
    assert core == order
    assert isinstance(rebirth, Rebirth)
    assert rebirth.will_delivered is True
    assert rebirth.emergency is False


def test_rebirth_carries_the_next_node_of_the_same_slot(tmp_path):
    journal: list[str] = []
    life, registry, _ = make_life(tmp_path, journal=journal)
    rebirth = life.on_death(node(), birth(), DeathCause("game"), reflection(cause="game"))
    assert rebirth.record.serial == 15
    assert registry.birth_calls == ["LastOrder01"]
    assert rebirth.traits.seed == 101  # a fresh deal, not the dead node's
    assert rebirth.traits.quirk in ("считает крипера родственником", "не ест хлеб у воды")
    assert rebirth.inheritance.inherited_notes == "завещание предшественника"


# --- acceptance 5: hive down -------------------------------------------------------------


def test_hive_down_parks_the_will_respawns_and_delivers_exactly_once(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    registry.retire_failures = [HiveUnavailable("hive unreachable")] * 2
    registry.birth_failures = [HiveUnavailable("hive unreachable")] * 2
    body = FakeBody(journal)
    life, _, _ = make_life(tmp_path, journal=journal, registry=registry, body=body)
    rebirth = life.on_death(node(), birth(), DeathCause("game"), reflection())

    assert not pending_path(tmp_path).exists()  # delivered, then removed
    # retire: 1 (park) + 1 (failed round) + 1 (success) = 3 attempts, 1 success
    assert len(registry.retire_calls) == 3
    serial, cause, text = registry.retire_calls[-1]
    assert (serial, cause) == (14, "game")
    assert text.startswith("---") and "cause: game" in text  # the full will.md
    # the body was respawned exactly once, hive or no hive
    assert body.respawn_calls == 1
    # the pauses grew between the rounds
    sleeps = [e for e in journal if e.startswith("sleep:")]
    assert sleeps == ["sleep:2", "sleep:4"]  # default backoff 2s, doubling
    assert rebirth.will_delivered is True
    assert rebirth.record.serial == 15


def test_parked_will_is_written_once_and_retries_never_rewrite_it(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    registry.retire_failures = [HiveUnavailable("down")] * 5
    registry.birth_failures = [HiveUnavailable("down")] * 5
    parked_events: list[str] = []

    def spy(type_, payload=None):
        if type_ == "will.parked":
            parked_events.append(pending_path(tmp_path).read_text(encoding="utf-8"))

    seeds = iter([101, 102])
    life = Lifecycle(
        slot="LastOrder01",
        body=FakeBody(journal),
        registry=registry,
        memory=FakeMemory(journal),
        llm=StubLLM(reply={"will": GOOD_BODY}),
        naming=preset("lab"),
        quirks_path=write_quirks(tmp_path),
        run_dir=tmp_path / "run",
        now=lambda: datetime.fromisoformat(DIED),
        sleep=lambda s: None,
        seed_factory=lambda: next(seeds),
        hive_retries=5,
        log=spy,
    )
    rebirth = life.on_death(node(), birth(), DeathCause("game"), reflection())
    # five failed delivery rounds, and the will was parked exactly once — the file
    # was never rewritten, and its content is the will that finally landed
    assert len(registry.retire_calls) == 6
    assert len(parked_events) == 1
    assert parked_events[0] == rebirth.will.to_markdown()
    assert "cause: game" in parked_events[0]
    assert not pending_path(tmp_path).exists()


def test_already_retired_counts_as_delivered_and_unparks(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    registry.retire_failures = [
        HiveUnavailable("read timeout"),  # landed, the reply was lost
        HiveRejected(409, "already_retired", "node 00014 is retired"),
    ]
    life, _, _ = make_life(tmp_path, journal=journal, registry=registry)
    rebirth = life.on_death(node(), birth(), DeathCause("game"), reflection())
    assert rebirth.will_delivered is True
    assert not pending_path(tmp_path).exists()
    assert len(registry.birth_calls) == 1


def test_permanent_rejection_parks_the_will_and_still_births_the_heir(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    registry.retire_always = HiveRejected(422, "invalid_will", "broken will text")
    life, _, _ = make_life(tmp_path, journal=journal, registry=registry)
    rebirth = life.on_death(node(), birth(), DeathCause("ttl"), reflection(cause="ttl"))
    assert rebirth.will_delivered is False  # stays parked for the operator
    parked = pending_path(tmp_path)
    assert parked.exists() and "cause: ttl" in parked.read_text(encoding="utf-8")
    assert len(registry.birth_calls) == 1  # the generation is not blocked
    assert rebirth.record.serial == 15
    # the delivery was tried exactly twice: the park attempt and the first round
    assert len(registry.retire_calls) == 2


def test_birth_never_lands_and_the_path_gives_up_after_the_budget(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    registry.retire_failures = [HiveUnavailable("down")] * 6
    registry.birth_failures = [HiveUnavailable("down")] * 6
    seeds = iter([101, 102])
    life = Lifecycle(
        slot="LastOrder01",
        body=FakeBody(journal),
        registry=registry,
        memory=FakeMemory(journal),
        llm=StubLLM(reply={"will": GOOD_BODY}),
        naming=preset("lab"),
        quirks_path=write_quirks(tmp_path),
        run_dir=tmp_path / "run",
        now=lambda: datetime.fromisoformat(DIED),
        sleep=lambda s: None,
        seed_factory=lambda: next(seeds),
        hive_retries=3,
    )
    with pytest.raises(HiveUnavailable, match="birth did not land"):
        life.on_death(node(), birth(), DeathCause("game"), reflection())
    assert pending_path(tmp_path).exists()  # the will survives the give-up


# --- acceptance 6: the body does not stand up -------------------------------------------


def test_respawn_failure_retries_once_then_raises_with_the_will_saved(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    body = FakeBody(journal, failures=2)  # both attempts fail
    life, _, body = make_life(tmp_path, journal=journal, registry=registry, body=body)
    with pytest.raises(BodyUnavailable, match="did not stand up"):
        life.on_death(node(), birth(), DeathCause("game"), reflection())
    assert body.respawn_calls == 2
    # the will was already saved in hive before the respawn was attempted
    assert len(registry.retire_calls) == 1
    assert registry.birth_calls == []  # no heir without a body
    assert "resume" not in journal  # the loop stays paused: no resume on failure


def test_respawn_failure_with_hive_down_leaves_the_will_parked(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    registry.retire_failures = [HiveUnavailable("down")]
    body = FakeBody(journal, failures=2)
    life, _, _ = make_life(tmp_path, journal=journal, registry=registry, body=body)
    with pytest.raises(BodyUnavailable):
        life.on_death(node(), birth(), DeathCause("game"), reflection())
    assert pending_path(tmp_path).exists()  # смерти без завещания не бывает


# --- the emergency path rides the same rails ---------------------------------------------


def test_an_emergency_will_still_reaches_retire(tmp_path):
    journal: list[str] = []
    llm = StubLLM(error=RuntimeError("smart tier is down"))
    life, registry, _ = make_life(tmp_path, journal=journal, llm=llm)
    rebirth = life.on_death(node(), birth(), DeathCause("stuck"), reflection(cause="stuck"))
    assert rebirth.emergency is True
    assert rebirth.model == "template"
    serial, cause, text = registry.retire_calls[0]
    assert (serial, cause) == (14, "stuck")
    assert "голова уже молчит" in text
    assert rebirth.will_delivered is True
