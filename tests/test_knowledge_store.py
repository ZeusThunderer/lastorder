"""KnowledgeStore: layout, serials, node files, records, statuses, git (T-012).

Every test works in a temporary knowledge repo; `git=True` cases check the commit trail
straight from `git log`, because the history IS the product (ADR-017).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.knowledge.schema import (
    BirthFile,
    Evidence,
    Record,
    RecordVote,
    Will,
    emergency_will,
)
from runtime.knowledge.slice import format_serial
from runtime.knowledge.store import (
    AlreadyRetiredError,
    InvalidCandidateError,
    InvalidVoteError,
    InvalidWillError,
    KnowledgeStore,
    StoreError,
    UnknownNodeError,
    fit_core_memory,
    status_of,
)
from runtime.lastorder.traits import Traits
from runtime.ports import KnowledgeCandidate, Vote

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-10-17T10:00:00+00:00"


def traits(seed: int = 42) -> Traits:
    return Traits(
        bravery=3,
        greed=7,
        talkativeness=5,
        trust_in_chat_and_nodes=4,
        superstition=9,
        quirk="считает крипера родственником",
        seed=seed,
    )


def candidate(**over) -> KnowledgeCandidate:
    fields = dict(
        kind="danger",
        claim="Стонущий прямоходящий бьёт на сближении; держать дистанцию.",
        about=("ent:zombie",),
        evidence_event_ids=("e-918",),
    )
    fields.update(over)
    return KnowledgeCandidate(**fields)


def birth(store: KnowledgeStore, slot: str = "LastOrder01", seed: int = 42) -> int:
    return store.birth(slot, traits(seed)).serial


def will_text(
    serial: int,
    *,
    cause: str = "game",
    born_at: str = T0,
    died_at: str = "2026-10-17T10:37:42+00:00",
    body: str = "Урок: не ходить в горы ночью.\nШутка: мне досталась жадность, я не просил.",
) -> str:
    from runtime.knowledge.schema import WillMeta

    meta = WillMeta(
        serial=serial,
        slot="LastOrder01",
        cause=cause,
        born_at=born_at,
        died_at=died_at,
        lifetime_s=2262,
        emergency=False,
        traits=traits(),
        model="deepseek/deepseek-v4.1-flash",
    )
    return Will(meta=meta, body=body).to_markdown()


def git_log(store: KnowledgeStore, fmt: str = "%s") -> list[str]:
    out = store._git("log", "--reverse", f"--format={fmt}")
    return [line for line in out.stdout.splitlines() if line]


def write_primer(directory: Path, entries: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "basics.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    return directory


@pytest.fixture()
def base(tmp_path: Path) -> Path:
    return tmp_path / "knowledge"


@pytest.fixture()
def store(base: Path) -> KnowledgeStore:
    return KnowledgeStore(base, git=False)


# --- init and primer ----------------------------------------------------------------------


def test_init_creates_layout_and_git(base: Path):
    opened = KnowledgeStore(base, git=True)
    assert (base / "base" / "records").is_dir()
    assert (base / "nodes").is_dir()
    assert (base / "base" / "catalog.md").exists()
    assert (base / "base" / "goals.md").exists()
    assert (base / ".git").exists()
    assert git_log(opened) == ["init: layout"]
    assert (base / ".gitignore").read_text(encoding="utf-8").splitlines() == [".lock", ".serial"]


PRIMER_FIXTURE = [
    {
        "kind": "danger",
        "claim": "Зелёная бесшумная тварь взрывается у самого бока: держать три шага.",
        "about": ["ent:creeper"],
    },
    {
        "kind": "fact",
        "claim": "Дерево рубится топором быстрее, чем кулаком.",
        "about": ["itm:axe"],
        "extended": True,
    },
]


def test_init_plain_loads_primer_in_one_commit(base: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERCEPTION_MODE", "plain")
    monkeypatch.setenv("PRIMER_DIR", str(write_primer(tmp_path / "primer", PRIMER_FIXTURE)))
    opened = KnowledgeStore(base, git=True)
    records = opened.records()
    assert [r.id for r in records] == ["k-0001", "k-0002"]
    assert all(r.source == "primer" and r.status == "confirmed" for r in records)
    assert git_log(opened)[-1] == "primer: load 2 records"
    # the extended entry is in the base, but no primer line ever reaches the catalog (P1-2)
    assert (base / "base" / "catalog.md").read_text(encoding="utf-8") == ""


def test_init_masked_skips_primer(base: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERCEPTION_MODE", "masked")
    monkeypatch.setenv("PRIMER_DIR", str(write_primer(tmp_path / "primer", PRIMER_FIXTURE[:1])))
    opened = KnowledgeStore(base, git=True)
    assert opened.records() == []
    assert git_log(opened) == ["init: layout"]


def test_primer_not_reloaded_on_existing_base(base: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERCEPTION_MODE", "plain")
    monkeypatch.setenv("PRIMER_DIR", str(write_primer(tmp_path / "primer", PRIMER_FIXTURE[:1])))
    KnowledgeStore(base, git=True)
    reopened = KnowledgeStore(base, git=True)
    assert len(reopened.records()) == 1  # the second open did not load the primer twice


# --- serials --------------------------------------------------------------------------------


BIRTH_WORKER = """
import sys
from pathlib import Path
sys.path.insert(0, {root!r})
from runtime.knowledge.store import KnowledgeStore
from runtime.lastorder.traits import Traits

store = KnowledgeStore(Path({base!r}), git={git!r})
traits = Traits(bravery=1, greed=2, talkativeness=3, trust_in_chat_and_nodes=4,
                superstition=5, quirk="q", seed=7)
for _ in range(int({count})):
    store.birth("SLOT", traits)
"""


def run_workers(base: Path, *, procs: int, count: int, git: bool) -> None:
    workers = []
    for slot in [f"LastOrder0{i}" for i in range(1, procs + 1)]:
        script = BIRTH_WORKER.format(
            root=str(REPO_ROOT), base=str(base), git=git, count=count
        ).replace("SLOT", slot)
        workers.append(subprocess.Popen([sys.executable, "-c", script], cwd=REPO_ROOT))
    for worker in workers:
        assert worker.wait() == 0


def serials_on_disk(base: Path) -> list[int]:
    return sorted(int(p.name) for p in (base / "nodes").iterdir() if p.is_dir())


def test_next_serial_atomic_across_processes(base: Path):
    KnowledgeStore(base, git=False)
    run_workers(base, procs=3, count=20, git=False)
    assert serials_on_disk(base) == list(range(1, 61))  # 60 unique numbers, no gaps


def test_next_serial_atomic_across_processes_with_git(base: Path):
    # Review P1-5: the write lock must cover git itself (index.lock contention)
    KnowledgeStore(base, git=True)
    run_workers(base, procs=2, count=5, git=True)
    assert serials_on_disk(base) == list(range(1, 11))
    opened = KnowledgeStore(base, git=True)
    assert len(git_log(opened)) == 1 + 10  # init plus ten births
    assert set(git_log(opened, "%an")) == {"hive"}


def test_serial_survives_restart_and_recovers(store: KnowledgeStore):
    birth(store, seed=1)
    birth(store, seed=2)
    assert KnowledgeStore(store.root, git=False).next_serial() == 3
    (store.root / ".serial").unlink()  # counter lost: recovered from existing nodes
    assert KnowledgeStore(store.root, git=False).next_serial() == 3


def test_format_serial_is_five_digits():
    assert format_serial(7) == "00007"
    assert format_serial(123456) == "123456"


# --- birth and retire ------------------------------------------------------------------------


def test_birth_writes_birth_json(store: KnowledgeStore):
    record = store.birth("LastOrder02", traits(4213))
    assert (record.serial, record.ttl_min) == (1, 45)
    data = json.loads(
        (store.root / "nodes" / "00001" / "birth.json").read_text(encoding="utf-8")
    )
    assert BirthFile.from_dict(data) == BirthFile(
        serial=1,
        slot="LastOrder02",
        born_at=record.born_at,
        ttl_min=45,
        traits=traits(4213),
    )


def test_birth_seed_mismatch_rejected(store: KnowledgeStore):
    with pytest.raises(StoreError):
        store.birth("LastOrder01", traits(42), seed=7)
    assert not (store.root / "nodes" / "00001").exists()


def test_retire_writes_will_and_marks_dead(base: Path):
    store = KnowledgeStore(base, git=False, now=lambda: T0)
    serial = birth(store)
    assert [b.serial for b in store.alive()] == [serial]
    store.retire(serial, "game", will_text(serial, died_at="2026-10-17T10:37:42+00:00"))
    will = Will.from_markdown(
        (store.root / "nodes" / "00001" / "will.md").read_text(encoding="utf-8")
    )
    # service fields come from the store's birth.json, the timestamps from the will (P1-1)
    assert will.meta.lifetime_s == 2262  # 10:00:00 -> 10:37:42
    assert will.meta.slot == "LastOrder01" and will.meta.cause == "game"
    assert store.alive() == []


def test_retire_accepts_emergency_will(store: KnowledgeStore):
    serial = birth(store)
    will = emergency_will(
        serial=serial,
        slot="LastOrder01",
        cause="ttl",
        born_at=T0,
        died_at="2026-10-17T10:45:00+00:00",
        traits=traits(),
        last_action="шёл к болоту",
        last_events=["видел стонущего"],
        node_name="узел 00001",
        heir_address="следующий узел",
    )
    store.retire(serial, "ttl", will.to_markdown())
    stored = Will.from_markdown(
        (store.root / "nodes" / "00001" / "will.md").read_text(encoding="utf-8")
    )
    assert stored.meta.emergency and stored.meta.model == "template"


def test_retire_twice_is_an_error_without_rewrite(store: KnowledgeStore):
    serial = birth(store)
    store.retire(serial, "game", will_text(serial))
    path = store.root / "nodes" / "00001" / "will.md"
    first = path.read_bytes()
    with pytest.raises(AlreadyRetiredError):
        store.retire(serial, "ttl", will_text(serial, cause="ttl"))
    assert path.read_bytes() == first


def test_retire_rejects_unknown_mismatched_and_broken(store: KnowledgeStore):
    with pytest.raises(UnknownNodeError):
        store.retire(99, "game", will_text(99))
    serial = birth(store)
    with pytest.raises(InvalidWillError):
        store.retire(serial, "game", will_text(serial + 1))  # frontmatter serial mismatch
    with pytest.raises(InvalidWillError):
        store.retire(serial, "game", "no frontmatter at all\nsecond line")
    with pytest.raises(InvalidWillError):
        store.retire(serial, "boredom", will_text(serial))  # cause must be a known one
    assert not (store.root / "nodes" / "00001" / "will.md").exists()


def test_retire_clamps_clock_skew(store: KnowledgeStore):
    serial = birth(store)
    # clock skew between the mind's and hive's containers: died_at before born_at, clamped to 0
    store.retire(serial, "game", will_text(serial, died_at="2026-10-17T09:59:00"))
    will = Will.from_markdown(
        (store.root / "nodes" / "00001" / "will.md").read_text(encoding="utf-8")
    )
    assert will.meta.lifetime_s == 0


def test_retired_node_gets_no_slice_or_inheritance(store: KnowledgeStore):
    # Review C-2: a retired node gets neither a step slice nor an inheritance
    serial = birth(store)
    store.retire(serial, "game", will_text(serial))
    with pytest.raises(AlreadyRetiredError):
        store.knowledge_slice(serial, ["ent:zombie"])
    with pytest.raises(AlreadyRetiredError):
        store.inheritance(serial)


# --- core memory -----------------------------------------------------------------------------


def test_core_memory_roundtrip_and_cut(store: KnowledgeStore):
    serial = birth(store)
    assert store.core_memory(serial) == ""  # born without one
    store.store_core_memory(serial, "## Цель\nДойти до болота.")
    assert store.core_memory(serial) == "## Цель\nДойти до болота.\n"

    long_text = "\n".join("строка номер %02d про глину и болото" % i for i in range(60))
    store.store_core_memory(serial, long_text)
    written = (store.root / "nodes" / "00001" / "core.md").read_text(encoding="utf-8")
    assert len(written.encode("utf-8")) <= 1536
    assert written.endswith("\n")
    assert "".join(written.splitlines()) in long_text.replace("\n", "")  # whole lines only
    with pytest.raises(UnknownNodeError):
        store.core_memory(99)


def test_fit_core_memory_single_long_line():
    cut = fit_core_memory("я" * 5000)  # no newline at all: hard byte cut on a char boundary
    assert 0 < len(cut.encode("utf-8")) <= 1536


# --- candidates and statuses -------------------------------------------------------------------


def _evidence(*nodes: int) -> list[dict]:
    return [{"node": n, "event": None, "ts": T0} for n in nodes]


def _votes(*entries: tuple[int, bool, str]) -> list[dict]:
    return [{"node": n, "accept": a, "why": w, "ts": T0} for n, a, w in entries]


@pytest.mark.parametrize(
    "evidence, votes, expected",
    [
        (_evidence(7), [], "hypothesis"),  # the author's own testimony confirms nothing
        (_evidence(7), _votes((7, True, "")), "hypothesis"),  # his vote neither
        (_evidence(7), _votes((8, True, "")), "hypothesis"),  # one voice is not enough
        (_evidence(7, 8), [], "confirmed"),  # independent evidence
        (_evidence(7), _votes((8, True, ""), (9, True, "")), "confirmed"),  # two voices, none against
        (_evidence(7), _votes((8, True, ""), (9, True, ""), (10, False, "")), "hypothesis"),  # a mute against blocks confirm
        (_evidence(7, 8), _votes((10, False, "видел иначе")), "disputed"),  # a refutation outranks evidence
        (_evidence(7), _votes((10, False, "не так"), (11, False, "тоже не так")), "retired"),
        (_evidence(7), _votes((8, True, ""), (10, False, "нет")), "disputed"),
    ],
)
def test_status_table(evidence, votes, expected):
    objects = [Evidence.from_dict(e) for e in evidence]
    votes_objects = [RecordVote.from_dict(v) for v in votes]
    assert status_of("node", 7, objects, votes_objects) == expected


def test_status_of_primer_is_always_confirmed():
    assert status_of("primer", "primer", [], []) == "confirmed"


def test_statuses_through_real_records(store: KnowledgeStore):
    # the same table on real records: hypothesis -> confirmed -> disputed -> retired
    serial_a = birth(store)
    serial_b = birth(store, slot="LastOrder02")
    record = store.submit_candidate(serial_a, candidate())
    assert record.status == "hypothesis" and record.author == serial_a
    assert record.id == "k-0001" and record.evidence[0].event == "e-918"

    twin = store.submit_candidate(serial_b, candidate())  # a duplicate: evidence, not a record
    assert twin.id == record.id and len(twin.evidence) == 2 and twin.status == "confirmed"
    assert len(store.records()) == 1

    store.submit_vote(serial_b, Vote(entry_id="k-0001", accept=False, why="видел иначе"))
    assert store.record("k-0001").status == "disputed"

    serial_c = birth(store, slot="LastOrder03")
    store.submit_vote(serial_c, Vote(entry_id="k-0001", accept=False, why="я мерил"))
    assert store.record("k-0001").status == "retired"


def test_candidate_below_threshold_is_competing_record(store: KnowledgeStore):
    serial_a = birth(store)
    serial_b = birth(store, slot="LastOrder02")
    store.submit_candidate(serial_a, candidate())
    other = store.submit_candidate(
        serial_b, candidate(claim="стонущий бьёт на сближении")  # J = 4/7 (section 4)
    )
    assert other.id == "k-0002" and other.status == "hypothesis"
    assert len(store.records()) == 2


def test_candidate_near_primer_is_a_new_hypothesis(base: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERCEPTION_MODE", "plain")
    monkeypatch.setenv("PRIMER_DIR", str(write_primer(tmp_path / "primer", PRIMER_FIXTURE[:1])))
    opened = KnowledgeStore(base, git=False)
    serial = birth(opened)
    near = opened.submit_candidate(serial, candidate(claim=PRIMER_FIXTURE[0]["claim"]))
    # disagreement with the primer is the node's own hypothesis (P1-2)
    assert near.id == "k-0002" and near.status == "hypothesis" and near.source == "node"


def test_duplicate_resubmission_is_idempotent(store: KnowledgeStore):
    serial_a = birth(store)
    serial_b = birth(store, slot="LastOrder02")
    store.submit_candidate(serial_a, candidate())
    store.submit_candidate(serial_b, candidate())
    third = store.submit_candidate(serial_b, candidate())  # the same node again
    assert len(third.evidence) == 2 and len(store.records()) == 1


def test_vote_replacement_changes_status(store: KnowledgeStore):
    serial_a = birth(store)
    serial_b = birth(store, slot="LastOrder02")
    serial_c = birth(store, slot="LastOrder03")
    store.submit_candidate(serial_a, candidate())
    store.submit_vote(serial_b, Vote(entry_id="k-0001", accept=False, why="не согласен"))
    assert store.record("k-0001").status == "disputed"
    store.submit_vote(serial_b, Vote(entry_id="k-0001", accept=True, why=""))  # replaced
    store.submit_vote(serial_c, Vote(entry_id="k-0001", accept=True, why=""))
    stored = store.record("k-0001")
    assert stored.status == "confirmed"
    assert [v.node for v in stored.votes] == [serial_b, serial_c]  # one vote per node


def test_retired_is_terminal(store: KnowledgeStore):
    # Review P1-3: nothing changes a retired record; the fix for it is a new record
    serial_a = birth(store)
    serial_b = birth(store, slot="LastOrder02")
    serial_c = birth(store, slot="LastOrder03")
    store.submit_candidate(serial_a, candidate())
    store.submit_vote(serial_b, Vote(entry_id="k-0001", accept=False, why="нет"))
    store.submit_vote(serial_c, Vote(entry_id="k-0001", accept=False, why="тоже нет"))
    assert store.record("k-0001").status == "retired"
    path = store.root / "base" / "records" / "k-0001.json"
    before = path.read_bytes()
    with pytest.raises(InvalidVoteError):  # even the refuter's replacement cannot revive it
        store.submit_vote(serial_b, Vote(entry_id="k-0001", accept=True, why=""))
    assert path.read_bytes() == before
    serial_d = birth(store, slot="LastOrder01")  # a later node re-observes the same thing
    heir_record = store.submit_candidate(serial_d, candidate())
    assert heir_record.id == "k-0002" and path.read_bytes() == before  # a new record, not a rewrite


def test_vote_on_primer_rejected(base: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERCEPTION_MODE", "plain")
    monkeypatch.setenv("PRIMER_DIR", str(write_primer(tmp_path / "primer", PRIMER_FIXTURE[:1])))
    opened = KnowledgeStore(base, git=False)
    serial = birth(opened)
    with pytest.raises(InvalidVoteError):
        opened.submit_vote(serial, Vote(entry_id="k-0001", accept=True, why=""))
    assert opened.record("k-0001").votes == ()


def test_author_vote_does_not_confirm(store: KnowledgeStore):
    serial_a = birth(store)
    serial_b = birth(store, slot="LastOrder02")
    serial_c = birth(store, slot="LastOrder03")
    store.submit_candidate(serial_a, candidate())
    store.submit_vote(serial_a, Vote(entry_id="k-0001", accept=True, why=""))
    store.submit_vote(serial_b, Vote(entry_id="k-0001", accept=True, why=""))
    assert store.record("k-0001").status == "hypothesis"  # the author's vote never counts
    store.submit_vote(serial_c, Vote(entry_id="k-0001", accept=True, why=""))
    assert store.record("k-0001").status == "confirmed"  # two voices of others


# --- raw ids (review P1-4) ---------------------------------------------------------------------


def test_candidate_refuses_namespaced_ids(store: KnowledgeStore):
    serial = birth(store)
    with pytest.raises(InvalidCandidateError):
        store.submit_candidate(
            serial, candidate(claim="minecraft:zombie бьёт на сближении.")
        )
    assert store.records() == []  # nothing was written


def test_leak_check_callable_refuses_seen_ids(store: KnowledgeStore):
    serial_a = birth(store)
    store.submit_candidate(serial_a, candidate())  # k-0001 exists
    seen = {"зомби"}
    opened = KnowledgeStore(
        store.root, git=False, leak_check=lambda text: [w for w in seen if w in text]
    )
    serial_b = birth(opened, slot="LastOrder02")
    with pytest.raises(InvalidCandidateError):
        opened.submit_candidate(
            serial_b, candidate(claim="зомби бьёт на сближении; держать дистанцию.")
        )
    with pytest.raises(InvalidVoteError):
        opened.submit_vote(
            serial_b, Vote(entry_id="k-0001", accept=False, why="зомби иной")
        )
    ok = opened.submit_candidate(serial_b, candidate(claim="Стонущий бьёт на сближении."))
    assert ok.id == "k-0002"


# --- input errors --------------------------------------------------------------------------------


def test_unknown_node_and_broken_candidate(store: KnowledgeStore):
    with pytest.raises(UnknownNodeError):
        store.submit_candidate(99, candidate())
    with pytest.raises(UnknownNodeError):
        store.submit_vote(99, Vote(entry_id="k-0001", accept=True, why=""))
    with pytest.raises(UnknownNodeError):
        store.knowledge_slice(99, ["ent:zombie"])
    with pytest.raises(UnknownNodeError):
        store.inheritance(99)
    serial = birth(store)
    with pytest.raises(InvalidCandidateError):
        store.submit_candidate(serial, candidate(claim="   "))  # empty claim
    with pytest.raises(InvalidCandidateError):
        store.submit_candidate(serial, candidate(about=("zombie",)))  # key without a prefix
    with pytest.raises(InvalidVoteError):
        store.submit_vote(serial, Vote(entry_id="k-9999", accept=True, why=""))  # unknown record
    with pytest.raises(StoreError):
        store.knowledge_slice(serial, ["zombie"])  # bad visible key
    assert store.records() == []


# --- git trail ------------------------------------------------------------------------------------


def test_git_log_shows_every_op_as_hive(base: Path, monkeypatch):
    monkeypatch.delenv("PERCEPTION_MODE", raising=False)
    monkeypatch.delenv("PRIMER_DIR", raising=False)
    opened = KnowledgeStore(base, git=True)  # plain mode, no primer dir: nothing to load
    serial = birth(opened)
    opened.submit_candidate(serial, candidate())
    peer = birth(opened, slot="LastOrder02")
    opened.submit_vote(peer, Vote(entry_id="k-0001", accept=True, why=""))
    opened.store_core_memory(serial, "## Цель\nЖить.")
    opened.retire(serial, "ttl", will_text(serial, cause="ttl"))
    assert git_log(opened) == [
        "init: layout",
        "birth: 00001 LastOrder01",
        "record: k-0001 hypothesis by 1",
        "birth: 00002 LastOrder02",
        "vote: k-0001 by 2",
        "core: 00001",
        "retire: 00001 ttl",
    ]
    assert set(git_log(opened, "%an")) == {"hive"}
    opened.submit_candidate(peer, candidate())  # the peer saw the same thing
    assert git_log(opened)[-1] == "evidence: k-0001 by 2"


def test_commit_failure_rolls_files_back(base: Path, monkeypatch):
    store = KnowledgeStore(base, git=True)
    serial = birth(store)
    records_dir = store.root / "base" / "records"

    def broken_git(*args: str, check: bool = True):
        if args and args[0] == "commit":
            raise subprocess.CalledProcessError(1, "git", stderr="boom")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(store, "_git", broken_git)
    with pytest.raises(StoreError):
        store.submit_candidate(serial, candidate())
    assert list(records_dir.glob("k-*.json")) == []  # the half-op left no files behind
    assert (store.root / "base" / "catalog.md").read_text(encoding="utf-8") == ""
    monkeypatch.undo()
    store.submit_candidate(serial, candidate())  # the store still works after the failure
    assert [r.id for r in store.records()] == ["k-0001"]


def test_record_files_parse_back(store: KnowledgeStore):
    serial = birth(store)
    record = store.submit_candidate(serial, candidate())
    path = store.root / "base" / "records" / f"{record.id}.json"
    assert Record.from_dict(json.loads(path.read_text(encoding="utf-8"))) == record
