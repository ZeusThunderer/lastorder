"""Jev, the threat-tactics decider of C1 (T-031; ADR-027, ADR-035; NETWORK.md section 2).

C1 owns the body between the agent's turns and reacts to damage with its own rules. When a
gate is wired in (`Executor(jev=...)`), at threat onset — and then no more often than
`JEV_MIN_INTERVAL_S` while the threat holds (plan review P1-3) — the contour asks the
configured backend to pick one of the CODE-BUILT sub-variants (flee / fight / keep). Raw uc
commands and `@gamer` are never options (ADR-037). The input is a compact plain-name state
(ADR-037: no raw ids reach the prompt), the agent's own policy file from its workplace
(`policy/threat.md`, truncated to `JEV_POLICY_MAX_CHARS`; a missing file means no policy)
and the option list. The code never interprets the policy text — it is the node's voice
inside the prompt and nothing else.

A timeout, an error, an invalid reply or a stale snapshot (the call runs OUTSIDE the body
lock, plan review P1-1) falls back to the rules and leaves a `jev.fallback` line in
log.jsonl; a valid pick is logged as `jev.choice`.

Backends (ADR-027): `cloud` — the TypeSafe Jev model through the llm-gateway, the only path
to cloud models (ADR-033); the gateway deployments of tier `jev` carry their own timeout and
zero retries (plan review P1-2), and the client side is single-shot (`retries=0`) with the
call bounded by `JEV_TIMEOUT_S`. `local` — the FastJev System One service of the `jev`
compose profile (T-005, protocol in docs/agents/jev.md). `rules` — no call at all; C1 stays
on its own rules (the default, ADR-027 "MVP on rules").
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from runtime.config import env
from runtime.ports import Decision, Option

# --- env knobs (compose interpolates the same defaults for mind-N) -------------------------

BACKENDS = ("cloud", "local", "rules")
BACKEND_DEFAULT = "rules"  # ADR-027: MVP C1 runs on rules until a live run shows a win
MODEL_DEFAULT = "openrouter/typesafe/jev-router"  # the current OpenRouter id of Jev (T-031)
LOCAL_URL_DEFAULT = "http://jev:8000"  # the FastJev profile service, docker-internal
TIMEOUT_S_DEFAULT = 1.5  # the C1 ceiling (plan review answer 2); the T-031 probe p95 of
# typesafe/jev-router is 3.5-4.6 s — above it, so cloud calls mostly fall back to the
# rules today; see docs/agents/jev.md before raising anything here
MIN_INTERVAL_S_DEFAULT = 3.0  # the per-node call floor (ADR-027; <= 20 calls/min)
POLICY_PATH_DEFAULT = "/mind-run/policy/threat.md"  # workplace mount lands in T-027 (P1-5)
POLICY_MAX_CHARS_DEFAULT = 1500  # ~400 tokens: the NETWORK.md section 6 input estimate
MAX_TOKENS_DEFAULT = 600  # measured: the router reasons first and starves at 120
# (40% empty/truncated replies); the answer JSON is ~60 tokens, output is free
TEMPERATURE_DEFAULT = 0.0  # a reflex, not a poet

QUESTION = "Which maneuver should the body take right now?"

SYSTEM_PROMPT = (
    "You are Jev, the fast tactical decider inside the executor contour of a Minecraft "
    "node. You see one compact body state, the node's own threat policy (if any) and a "
    "fixed list of maneuver options built by the contour's code. Pick exactly one option. "
    "Never invent option ids, never suggest commands: options outside the list do not "
    "exist. The policy is the node's voice — respect it unless following it would get the "
    "body killed. Answer with one compact JSON object and nothing else: "
    '{"option_id": "<id>", "confidence": <float 0..1>, "probs": {"<id>": <float>, ...}} '
    "where probs covers the offered ids and sums to about 1."
)


class JevError(RuntimeError):
    """An unusable Jev reply: bad JSON, a foreign option id, a broken shape."""


# --- the agent's policy file ----------------------------------------------------------------


class PolicySource:
    """`policy/threat.md` of the node's workplace, re-read on every call (the agent edits
    it live), truncated to `max_chars`. A missing or unreadable file means no policy: the
    node has not written one yet and Jev decides on the state alone (criterion 2)."""

    def __init__(self, path: str, max_chars: int = POLICY_MAX_CHARS_DEFAULT):
        self._path = Path(path)
        self._max_chars = max(0, int(max_chars))

    @property
    def path(self) -> str:
        return str(self._path)

    def text(self) -> str | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        text = raw.strip()
        return text[: self._max_chars] if text else None


# --- the prompt (pure; the probe measures exactly this shape) -------------------------------


def build_messages(
    snapshot: Mapping[str, Any], policy: str | None, options: Sequence[Option]
) -> tuple[str, str]:
    """(system, user) for the cloud backend. The snapshot must already carry plain names."""
    option_lines = "\n".join(f"- {o.id}: {o.title}" for o in options)
    parts = ["STATE:\n" + json.dumps(dict(snapshot), ensure_ascii=False, default=str)]
    if policy:
        parts.append("NODE THREAT POLICY (written by the node itself):\n" + policy)
    parts.append("OPTIONS (the only moves the body can make right now):\n" + option_lines)
    parts.append("QUESTION: " + QUESTION)
    return SYSTEM_PROMPT, "\n\n".join(parts)


def parse_decision(text: str, options: Sequence[Option]) -> Decision:
    """One Jev reply to a Decision: a JSON object naming an offered option. Anything else
    raises JevError — the caller (gate) turns that into a rules fallback. Also used by the
    probe to score the in-format share of the live model."""

    from runtime.llm.client import parse_json_object  # heavy import stays out of module load

    try:
        data = parse_json_object(text)
        option_id = data["option_id"]
    except Exception as exc:  # parse_json_object raises LLMError; a missing key is the same
        raise JevError(f"unusable reply: {exc}") from exc
    if option_id not in {o.id for o in options}:
        raise JevError(f"reply picked a foreign option {option_id!r}")
    try:
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    known = {o.id for o in options}
    probs: dict[str, float] = {}
    raw_probs = data.get("probs")
    if isinstance(raw_probs, Mapping):
        for key, value in raw_probs.items():
            if key in known:
                try:
                    probs[key] = min(1.0, max(0.0, float(value)))
                except (TypeError, ValueError):
                    continue
    return Decision(option_id=option_id, probs=probs, confidence=confidence)


# --- backends -------------------------------------------------------------------------------


class CloudJevBackend:
    """The TypeSafe Jev model through the llm-gateway (ADR-027/033): an LLMClient call,
    single-shot (retries=0) with the JEV_TIMEOUT_S bound — a slow or failed call raises and
    the gate falls back to the rules. `complete(system, user) -> str` is injectable."""

    name = "cloud"

    def __init__(
        self,
        *,
        model: str = MODEL_DEFAULT,
        api_base: str | None = None,
        api_key: str | None = None,
        timeout_s: float = TIMEOUT_S_DEFAULT,
        max_tokens: int = MAX_TOKENS_DEFAULT,
        temperature: float = TEMPERATURE_DEFAULT,
        complete: Callable[[str, str], str] | None = None,
    ):
        if complete is None:
            # Import litellm HERE, on the caller's construction thread, not lazily inside
            # the first C1 tick: the stand run (T-031 criterion 5, 2026-10-18) caught a
            # `_DeadlockError: deadlock detected by _ModuleLock('litellm...')` when the
            # tick thread imported it under load — the fallback ate it, but the call was
            # lost. A startup import is one-time and off the tick path.
            import litellm  # noqa: F401  (the heavy import, out of the C1 tick)

            from runtime.llm.client import LLMClient, LLMConfig

            client = LLMClient(
                LLMConfig(
                    model=model,
                    api_base=api_base,
                    api_key=api_key,
                    timeout_s=timeout_s,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    retries=0,  # one attempt: C1 falls back to the rules, a retry would be late
                )
            )
            complete = client.complete
        self._complete = complete

    def decide(
        self, snapshot: Mapping[str, Any], policy: str | None, options: Sequence[Option]
    ) -> Decision:
        system, user = build_messages(snapshot, policy, options)
        return parse_decision(self._complete(system, user), options)


class LocalJevBackend:
    """The FastJev System One service of the `jev` compose profile (T-005): one choice
    question, `POST {base_url}/v1/systemone`. The reply names the pick `choice` (jev.md).
    `post(url, payload, timeout_s) -> dict` is injectable."""

    name = "local"

    def __init__(
        self,
        *,
        base_url: str = LOCAL_URL_DEFAULT,
        timeout_s: float = TIMEOUT_S_DEFAULT,
        post: Callable[[str, dict, float], dict] | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._post = post or self._httpx_post

    @staticmethod
    def _httpx_post(url: str, payload: dict, timeout_s: float) -> dict:
        import httpx  # heavy import stays out of module load

        with httpx.Client(timeout=timeout_s) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    def decide(
        self, snapshot: Mapping[str, Any], policy: str | None, options: Sequence[Option]
    ) -> Decision:
        instructions = QUESTION
        if policy:
            instructions += "\n\nNode threat policy (written by the node itself):\n" + policy
        payload = {
            "model": "fastjev",
            "state": dict(snapshot),
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": {o.id: o.title for o in options},
                }
            },
        }
        reply = self._post(f"{self._base_url}/v1/systemone", payload, self._timeout_s)
        try:
            answer = reply["answers"]["action"]
            picked = answer.get("choice", answer.get("value"))
        except (KeyError, TypeError, AttributeError) as exc:
            raise JevError(f"unusable System One reply: {exc}") from exc
        known = {o.id for o in options}
        if picked not in known:
            raise JevError(f"reply picked a foreign option {picked!r}")
        probs: dict[str, float] = {}
        raw_probs = answer.get("probabilities") or {}
        if isinstance(raw_probs, Mapping):
            for key, value in raw_probs.items():
                if key in known:
                    try:
                        probs[key] = min(1.0, max(0.0, float(value)))
                    except (TypeError, ValueError):
                        continue
        try:
            confidence = min(1.0, max(0.0, float(answer.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0
        return Decision(option_id=picked, probs=probs, confidence=confidence)


# --- the gate: floor, call, fallback logging ------------------------------------------------


class JevGate:
    """What C1 talks to. `due(now)` is the floor check (the executor asks under the body
    lock before snapshotting); `advise()` does the unlocked call and owns the fallback
    records. The floor stamps even on failure: a timed-out call still spent the slot."""

    def __init__(
        self,
        backend: CloudJevBackend | LocalJevBackend | None,
        *,
        floor_s: float = MIN_INTERVAL_S_DEFAULT,
        policy: PolicySource | None = None,
        log_: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._backend = backend
        self._floor_s = float(floor_s)
        self._policy = policy
        self._log = log_
        self._clock = clock
        self._last_call_at = -1e9

    @classmethod
    def from_env(cls, *, log_: Any = None, clock: Callable[[], float] = time.monotonic) -> "JevGate":
        """Wire the gate the mind's env describes. An unknown JEV_BACKEND is a startup
        error, not a silent `rules` (the same honesty as PERCEPTION_MODE)."""

        def _float(name: str, default: float) -> float:
            raw = env(name, default=str(default))
            try:
                return float(raw)
            except ValueError:
                raise ValueError(f"{name} must be a number, got {raw!r}") from None

        backend_name = (env("JEV_BACKEND", default=BACKEND_DEFAULT) or BACKEND_DEFAULT).strip().lower()
        if backend_name not in BACKENDS:
            raise ValueError(f"JEV_BACKEND must be one of {', '.join(BACKENDS)}, got {backend_name!r}")
        policy = PolicySource(
            env("JEV_POLICY_PATH", default=POLICY_PATH_DEFAULT) or POLICY_PATH_DEFAULT,
            int(_float("JEV_POLICY_MAX_CHARS", POLICY_MAX_CHARS_DEFAULT)),
        )
        floor_s = _float("JEV_MIN_INTERVAL_S", MIN_INTERVAL_S_DEFAULT)
        timeout_s = _float("JEV_TIMEOUT_S", TIMEOUT_S_DEFAULT)
        if backend_name == "rules":
            return cls(None, floor_s=floor_s, policy=policy, log_=log_, clock=clock)
        if backend_name == "cloud":
            backend: CloudJevBackend | LocalJevBackend = CloudJevBackend(
                model=env("JEV_MODEL", default=MODEL_DEFAULT) or MODEL_DEFAULT,
                api_base=env("JEV_API_BASE", "LLM_API_BASE") or None,
                api_key=env("JEV_API_KEY", "LLM_API_KEY") or None,
                timeout_s=timeout_s,
                max_tokens=int(_float("JEV_MAX_TOKENS", MAX_TOKENS_DEFAULT)),
                temperature=_float("JEV_TEMPERATURE", TEMPERATURE_DEFAULT),
            )
        else:
            backend = LocalJevBackend(
                base_url=env("JEV_LOCAL_URL", default=LOCAL_URL_DEFAULT) or LOCAL_URL_DEFAULT,
                timeout_s=timeout_s,
            )
        return cls(backend, floor_s=floor_s, policy=policy, log_=log_, clock=clock)

    @property
    def enabled(self) -> bool:
        return self._backend is not None

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend is not None else "rules"

    def due(self, now: float | None = None) -> bool:
        """True when a call is allowed right now (threat onset counts: the floor caps the
        global rate, criterion 4's <= 20 calls/min)."""
        if self._backend is None:
            return False
        now = self._clock() if now is None else now
        return now - self._last_call_at >= self._floor_s

    def advise(
        self, snapshot: Mapping[str, Any], options: Sequence[Option]
    ) -> Decision | None:
        """One unlocked Jev call. None means the rules decide (the reason is already in
        log.jsonl as `jev.fallback`)."""
        if self._backend is None:
            return None
        self._last_call_at = self._clock()
        policy_text = self._policy.text() if self._policy else None
        started = time.perf_counter()
        try:
            decision = self._backend.decide(snapshot, policy_text, options)
        except Exception as exc:  # timeout, HTTP error, bad reply — every one means rules
            self._record("jev.fallback", backend=self._backend.name, reason=f"{type(exc).__name__}: {exc}"[:300])
            return None
        self._record(
            "jev.choice",
            backend=self._backend.name,
            option_id=decision.option_id,
            confidence=round(decision.confidence, 3),
            latency_ms=round((time.perf_counter() - started) * 1000),
            policy=bool(policy_text),
        )
        return decision

    def _record(self, kind: str, **fields: Any) -> None:
        if self._log is not None:
            self._log.record(kind, **fields)
