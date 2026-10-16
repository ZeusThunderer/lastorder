"""Ports of the Last Order network (docs/architecture/NETWORK.md, section 4).

Signatures only, no logic. Every port is a typing.Protocol, so any class with the right
methods fits, including test stubs; real implementations arrive in their own tasks:
Py4jBody in T-006, HiveClient in T-012, deciders in T-014/T-015. Payloads crossing a port
are frozen dataclasses defined here and mirrored in NETWORK.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from runtime.lastorder.persona import Inheritance, Node
from runtime.lastorder.traits import Traits


# --- payloads of one observation ---------------------------------------------------


@dataclass(frozen=True)
class GameState:
    """Raw body state for one tick; perception maps ids before anything reaches a prompt."""

    in_game: bool
    hp: float
    food: float
    pos: tuple[float, float, float]
    held: str
    looking_at: str


@dataclass(frozen=True)
class EntityInfo:
    """One entity from getEntitiesInfo: raw game names, perception maps them later."""

    kind: str
    name: str
    dist_m: float


@dataclass(frozen=True)
class BlockInfo:
    """One block from getBlocksAround: display name as the body reports it."""

    name: str


@dataclass(frozen=True)
class ItemStack:
    """One inventory slot from getInventoryFull."""

    name: str
    count: int
    slot: int = -1


@dataclass(frozen=True)
class Event:
    """Event envelope (NETWORK.md section 5), after the ARCHITECTURE 5.1 shape."""

    id: str
    ts_mono: float
    ts_wall: str  # ISO-8601
    source: str  # body | mind | hive
    type: str  # game.damage, game.death, chat.line, task.idle, ...
    priority: int = 0
    payload: Mapping[str, Any] = field(default_factory=dict)


# --- C1: options and the decider ---------------------------------------------------


@dataclass(frozen=True)
class Option:
    """One move a step or decider may pick: what the node reads, what C1 executes."""

    id: str
    title: str
    lever: str
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    option_id: str
    probs: Mapping[str, float] = field(default_factory=dict)
    confidence: float = 0.0


# --- knowledge and goals, served by hive (ADR-017, ADR-021) ------------------------


@dataclass(frozen=True)
class KnowledgeEntry:
    """A base record as a node sees it: the prompt fields of a KNOWLEDGE.md 2 record."""

    id: str
    kind: str  # danger | fact | recipe | skill | term
    claim: str
    status: str  # hypothesis | confirmed | disputed | retired
    about: tuple[str, ...] = ()


@dataclass(frozen=True)
class NetworkGoal:
    """Advice from the network council, never an order (ADR-021)."""

    text: str
    why: str = ""


@dataclass(frozen=True)
class KnowledgeSlice:
    """What the hive hands a mind around one step."""

    catalog: str
    network_goals: tuple[NetworkGoal, ...] = ()
    about: tuple[KnowledgeEntry, ...] = ()  # entries about the handles the node sees now
    fresh: tuple[KnowledgeEntry, ...] = ()  # recent tail for context
    pending_votes: tuple[KnowledgeEntry, ...] = ()  # up to two candidates to vote on


@dataclass(frozen=True)
class KnowledgeCandidate:
    """A record proposed by a node; the hive stamps author, ts and status (ADR-017)."""

    kind: str
    claim: str
    about: tuple[str, ...] = ()
    evidence_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Vote:
    entry_id: str
    accept: bool
    why: str = ""


# --- node lifecycle and comms (ADR-018, ADR-022) ------------------------------------


@dataclass(frozen=True)
class BirthRecord:
    serial: int
    born_at: str  # ISO-8601
    ttl_min: int  # forced death after this many minutes of life


@dataclass(frozen=True)
class NodeBrief:
    serial: int
    slot: str


@dataclass(frozen=True)
class NodeMessage:
    """Misaka-net message via hive: an observation to the reader, never a command."""

    from_serial: int
    to_serial: int
    text: str
    ts: str = ""


@dataclass(frozen=True)
class ChatterTurn:
    """The chatter director grants a turn: a topic and how many lines the node may say."""

    topic: str
    max_lines: int = 1


# --- the step reply (schema is preliminary; T-011 finalizes it) --------------------


@dataclass(frozen=True)
class NewTerm:
    handle: str
    term: str


@dataclass(frozen=True)
class MessageDraft:
    """The `msg` field of a step reply; the mind stamps the sender and sends it on."""

    to_serial: int
    text: str


@dataclass(frozen=True)
class StepReply:
    option_id: str
    why: str
    say: str | None = None
    new_term: NewTerm | None = None
    note: str | None = None
    msg: MessageDraft | None = None
    votes: tuple[Vote, ...] = ()


# --- ports ---------------------------------------------------------------------------


@runtime_checkable
class GameBody(Protocol):
    """The body of one slot: uc over py4j (T-006), or a stub in tests."""

    def state(self) -> GameState: ...
    def entities(self, radius: int = 16) -> Sequence[EntityInfo]: ...
    def blocks_around(self, radius: int = 4) -> Sequence[BlockInfo]: ...
    def inventory(self) -> Sequence[ItemStack]: ...
    def recent_chat(self, lines: int = 5) -> Sequence[str]: ...
    def act(self, lever: str, args: Mapping[str, Any] | None = None) -> None: ...
    def busy(self) -> bool: ...
    def stop(self) -> None: ...
    def respawn(self) -> None: ...


@runtime_checkable
class Decider(Protocol):
    """C1 tactic pick among sub-variants of the step's option; None intent means survival."""

    def choose(
        self, state: GameState, intent: Option | None, options: Sequence[Option]
    ) -> Decision: ...


@runtime_checkable
class PersonaProvider(Protocol):
    """A node's system prompt (ADR-018); implemented by runtime.lastorder.persona."""

    def name(self, node: Node) -> str: ...
    def system_prompt(self, node: Node, inheritance: Inheritance) -> str: ...


@runtime_checkable
class Memory(Protocol):
    """A node's view of the shared base, served by hive (ADR-017). The serial is explicit:
    the writer of records, evidence and votes is the node, and hive cannot know it from
    the connection."""

    def inheritance(self, serial: int) -> Inheritance: ...
    def knowledge(self, serial: int, handles: Sequence[str]) -> KnowledgeSlice: ...
    def core_memory(self, serial: int) -> str: ...
    def store_core_memory(self, serial: int, text: str) -> None: ...
    def submit_candidate(self, serial: int, candidate: KnowledgeCandidate) -> None: ...
    def submit_vote(self, serial: int, vote: Vote) -> None: ...


@runtime_checkable
class NodeRegistry(Protocol):
    """Lifecycle bookkeeping; serials are the hive's to give (ADR-018). A stuck node
    asks for its own death, and hive grants it without a threshold of its own (ADR-026)."""

    def birth(self, slot: str, traits: Traits) -> BirthRecord: ...
    def request_death(self, serial: int, cause: str) -> None: ...
    def retire(self, serial: int, cause: str, will_text: str) -> None: ...
    def alive(self) -> Sequence[NodeBrief]: ...


@runtime_checkable
class NodeComms(Protocol):
    """Misaka-net and chatter turns, directed by the chatter director in hive (ADR-022)."""

    def inbox(self, serial: int) -> Sequence[NodeMessage]: ...
    def send(self, message: NodeMessage) -> None: ...
    def chatter_turn(self, serial: int) -> ChatterTurn | None: ...
