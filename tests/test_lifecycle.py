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
    """NodeRegistry on scripts: retire/birth raise or pass by preloaded queues.

    With `hive_semantics` the review-R1-1 behavior of the real hive is modeled too:
    `birth` closes every living node of the slot with hive's OWN emergency will, and a
    `retire` of a closed (or never-born) serial then answers 409 already_retired —
    the trap the old after-birth delivery fell into.
    """

    def __init__(self, journal: list[str], *, hive_semantics: bool = False, born=()):
        self.journal = journal
        self.hive_semantics = hive_semantics
        self.living: set[int] = set(born)
        self.closed_by_hive: dict[int, str] = {}  # serial -> hive's emergency will text
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
        if self.hive_semantics:
            if serial not in self.living:
                raise HiveRejected(409, "already_retired", f"node {serial:05d} is retired")
            self.living.discard(serial)

    def birth(self, slot: str, traits: Traits) -> BirthRecord:
        self.birth_calls.append(slot)
        self.journal.append("birth")
        if self.birth_failures:
            failure = self.birth_failures.pop(0)
            if failure is not None:
                raise failure
        if self.hive_semantics:
            # the real hive (runtime/hive/lifecycle.py): a birth into an occupied slot
            # closes the old node as lost — with hive's emergency will, not the node's
            for serial in sorted(self.living):
                self.closed_by_hive[serial] = f"---\nserial: {serial}\nemergency: true\n---\n(шаблон hive)"
                self.living.discard(serial)
        record = BirthRecord(
            serial=self.next_serial,
            born_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ttl_min=45,
        )
        if self.hive_semantics:
            self.living.add(record.serial)
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
    # retire: 1 (park) + 1 (failed round, no birth that round) + 1 (success) = 3
    assert len(registry.retire_calls) == 3
    serial, cause, text = registry.retire_calls[-1]
    assert (serial, cause) == (14, "game")
    assert text.startswith("---") and "cause: game" in text  # the full will.md
    # the birth never runs ahead of the delivery (review R1-1)
    first_birth = journal.index("birth")
    assert all(i < first_birth for i, e in enumerate(journal) if e == "retire")
    # the body was respawned exactly once, hive or no hive
    assert body.respawn_calls == 1
    # the pauses grew between the rounds: retire-down, then two birth failures
    sleeps = [e for e in journal if e.startswith("sleep:")]
    assert sleeps == ["sleep:2", "sleep:4", "sleep:8"]  # default backoff 2s, doubling
    assert rebirth.will_delivered is True
    assert rebirth.record.serial == 15


def test_parked_will_is_written_once_and_retries_never_rewrite_it(tmp_path):
    journal: list[str] = []
    registry = FakeRegistry(journal)
    registry.retire_failures = [HiveUnavailable("down")] * 5
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
    # the park attempt plus four rounds of retire-down (each: pause, NO birth), the
    # fifth round delivers and the birth runs once; the will was parked exactly once
    # and its content is the will that finally landed
    assert len(registry.retire_calls) == 6
    assert parked_events and all(c == parked_events[0] for c in parked_events)
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
    # settled on the first call: a permanent 4xx is not retried in the rounds
    assert len(registry.retire_calls) == 1


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
    with pytest.raises(HiveUnavailable, match="did not land"):
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
    assert rebirth.will_error is not None and "RuntimeError" in rebirth.will_error
    serial, cause, text = registry.retire_calls[0]
    assert (serial, cause) == (14, "stuck")
    assert "голова уже молчит" in text
    assert rebirth.will_delivered is True


# --- review R1-1: the birth never runs ahead of the will --------------------------------


def test_birth_waits_for_the_delivery_with_real_hive_semantics(tmp_path):
    # The trap of R1-1: hive closes a LIVING node of the slot at birth with its own
    # emergency will, so a birth before the delivery would answer the next retire 409
    # and drop the node's only copy. With retire down once and birth up, the path must
    # deliver first and only then birth — hive never writes its template here.
    journal: list[str] = []
    registry = FakeRegistry(journal, hive_semantics=True, born=(14,))
    registry.retire_failures = [HiveUnavailable("read timeout")]  # the park attempt
    life, _, _ = make_life(tmp_path, journal=journal, registry=registry)
    rebirth = life.on_death(node(), birth(), DeathCause("game"), reflection())

    first_birth = journal.index("birth")
    assert all(i < first_birth for i, e in enumerate(journal) if e == "retire")
    assert len(registry.retire_calls) == 2  # the failed park attempt, then the delivery
    serial, cause, text = registry.retire_calls[-1]
    assert (serial, cause) == (14, "game")
    assert rebirth.will.body.split("\n")[0] in text  # the node's will, not a template
    assert registry.closed_by_hive == {}  # hive closed nobody: the delivery came first
    assert rebirth.will_delivered is True
    assert not pending_path(tmp_path).exists()
    assert rebirth.record.serial == 15


def test_delivery_and_birth_both_flaky_birth_still_waits(tmp_path):
    # Rounds where retire stays down hold the birth back too, however available
    # birth itself is: no birth call happens while the will is parked.
    journal: list[str] = []
    registry = FakeRegistry(journal, hive_semantics=True, born=(14,))
    registry.retire_failures = [HiveUnavailable("down")] * 2
    registry.birth_failures = [HiveUnavailable("down")]  # birth would fail once anyway
    life, _, _ = make_life(tmp_path, journal=journal, registry=registry)
    rebirth = life.on_death(node(), birth(), DeathCause("game"), reflection())

    core = [e for e in journal if e in ("retire", "birth", "sleep:2", "sleep:4")]
    # park -> (retire down, pause, no birth) -> delivery -> (birth down, pause) -> birth
    assert core == ["retire", "retire", "sleep:2", "retire", "birth", "sleep:4", "birth"]
    assert rebirth.will_delivered is True
    assert rebirth.record.serial == 15


# --- review R1-2: 409 on the very first retire -------------------------------------------


def test_first_retire_409_supersession_parks_the_real_will(tmp_path):
    # Hive closed the node itself (lost-close, reconcile) before our first retire:
    # the 409 is not a delivery, the node's will never landed — it is parked as
    # superseded, never dropped silently, and the generation goes on.
    journal: list[str] = []
    registry = FakeRegistry(journal, hive_semantics=True)  # serial 14 was never born
    life, _, _ = make_life(tmp_path, journal=journal, registry=registry)
    rebirth = life.on_death(node(), birth(), DeathCause("game"), reflection())

    assert "log:will.superseded" in journal
    assert len(registry.retire_calls) == 1  # settled on the first call, no retries
    assert len(registry.birth_calls) == 1  # the heir is born regardless
    assert rebirth.will_delivered is False
    parked = pending_path(tmp_path)
    assert parked.exists()
    assert parked.read_text(encoding="utf-8") == rebirth.will.to_markdown()  # preserved
    assert rebirth.record.serial == 15
