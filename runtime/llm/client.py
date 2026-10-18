"""One thin LLM client over LiteLLM. Local vLLM or a cloud provider is a matter of env, not code.

Env (new name first, NetTyan name as fallback):
  LLM_MODEL / CORE_LLM_MODEL_NAME       litellm model string, e.g. hosted_vllm/Qwen/Qwen3-8B-AWQ
  LLM_API_BASE / CORE_LLM_API_BASE      for self-hosted endpoints
  LLM_API_KEY                           optional; providers' own *_API_KEY variables also work
  LLM_REASONING_OFF=1 / CORE_LLM_REASONNING=NONE   ask the model to skip its thinking pass
  LLM_TIMEOUT_S, LLM_MAX_TOKENS, LLM_TEMPERATURE

Tiers (ADR-023): for_tier("node"|"smart") reads the same variables with an LLM_NODE_ / LLM_SMART_
prefix first and falls back to the base LLM_* names: LLM_<TIER>_MODEL, _API_BASE, _API_KEY,
_REASONING_OFF, _MAX_TOKENS, _TEMPERATURE, _TIMEOUT_S.

Gateway (ADR-033): containers do not hold provider keys. Point LLM_<TIER>_API_BASE (or the base
LLM_API_BASE) at the llm-gateway proxy and put the consumer's virtual key into LLM_API_KEY — or
into LLM_<TIER>_API_KEY when one process needs different keys per tier. The model string stays
the full litellm name (the gateway routes by it), so only the base URL and the key differ from
a direct provider setup.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from runtime.config import env


class LLMError(RuntimeError):
    pass


TIERS = ("node", "smart")


@dataclass(frozen=True)
class LLMConfig:
    model: str
    api_base: str | None = None
    api_key: str | None = None
    reasoning_off: bool = True
    timeout_s: float = 30.0
    max_tokens: int = 600
    temperature: float = 0.8
    retries: int = 2

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls.for_tier(None)

    @classmethod
    def for_tier(cls, tier: str | None) -> "LLMConfig":
        """Config of an ADR-023 tier; tier variables override the base LLM_* ones."""
        prefix = f"LLM_{tier.upper()}_" if tier else "LLM_"
        if tier and tier not in TIERS:
            raise LLMError(f"unknown tier {tier!r}, want one of {TIERS}")
        model = env(f"{prefix}MODEL", "LLM_MODEL", "CORE_LLM_MODEL_NAME")
        if not model:
            want = f"LLM_{tier.upper()}_MODEL" if tier else "LLM_MODEL"
            raise LLMError(f"set {want} (or LLM_MODEL / CORE_LLM_MODEL_NAME)")
        off = env(f"{prefix}REASONING_OFF", "LLM_REASONING_OFF") == "1" or env("CORE_LLM_REASONNING") == "NONE"
        return cls(
            model=model,
            api_base=_url(env(f"{prefix}API_BASE", "LLM_API_BASE", "CORE_LLM_API_BASE")),
            api_key=env(f"{prefix}API_KEY", "LLM_API_KEY"),
            reasoning_off=off,
            timeout_s=float(env(f"{prefix}TIMEOUT_S", "LLM_TIMEOUT_S", default="30")),
            max_tokens=int(env(f"{prefix}MAX_TOKENS", "LLM_MAX_TOKENS", default="600")),
            temperature=float(env(f"{prefix}TEMPERATURE", "LLM_TEMPERATURE", default="0.8")),
        )


@dataclass(frozen=True)
class CallStats:
    """What one call cost: model, tokens, wall latency and the provider-reported price, if any."""

    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_s: float
    cost_usd: float | None


def _call_stats(model: str, reply, latency_s: float) -> CallStats:
    usage = getattr(reply, "usage", None)
    hidden = getattr(reply, "_hidden_params", None)
    cost = hidden.get("response_cost") if isinstance(hidden, dict) else None
    return CallStats(
        model=model,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        latency_s=latency_s,
        cost_usd=cost if isinstance(cost, (int, float)) else None,
    )


def _url(value: str | None) -> str | None:
    """Legacy .env files carry placeholders like NONE in URL slots; only a real URL counts."""
    return value if value and value.startswith(("http://", "https://")) else None


FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def parse_json_object(text: str) -> dict:
    """Parse a JSON object, tolerating code fences and prose around the braces."""
    text = FENCE.sub("", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"no JSON object in reply: {text[:200]!r}")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"bad JSON in reply: {exc}") from exc
    if not isinstance(value, dict):
        raise LLMError("reply JSON is not an object")
    return value


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.last_call: CallStats | None = None

    def _extra(self) -> dict:
        if not self.config.reasoning_off:
            return {}
        if self.config.model.startswith("openrouter/"):
            return {"extra_body": {"reasoning": {"enabled": False}}}
        if self.config.api_base:  # self-hosted vLLM with a thinking chat template (Qwen)
            return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
        return {}

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        import litellm  # heavy import, kept out of module import time

        kwargs = dict(
            model=self.config.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            timeout=self.config.timeout_s,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            **self._extra(),
        )
        if self.config.api_base:
            kwargs["api_base"] = self.config.api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        delay, last = 1.0, None
        for attempt in range(self.config.retries + 1):
            try:
                reply = litellm.completion(**kwargs)
                self.last_call = _call_stats(self.config.model, reply, time.perf_counter() - started)
                return (reply.choices[0].message.content or "").strip()
            except Exception as exc:  # provider errors vary; every one is retried with a pause
                last = exc
                if "reasoning is mandatory" in str(exc).lower() and "extra_body" in kwargs:
                    kwargs.pop("extra_body")  # e.g. openai/gpt-5-nano: the thinking pass is forced
                    continue
                if attempt < self.config.retries:
                    time.sleep(delay)
                    delay *= 2
        raise LLMError(f"LLM call failed after {self.config.retries + 1} attempts: {last}") from last

    def complete_json(self, system: str, user: str) -> dict:
        return parse_json_object(self.complete(system, user, json_mode=True))
