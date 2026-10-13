"""One thin LLM client over LiteLLM. Local vLLM or a cloud provider is a matter of env, not code.

Env (new name first, NetTyan name as fallback):
  LLM_MODEL / CORE_LLM_MODEL_NAME       litellm model string, e.g. hosted_vllm/Qwen/Qwen3-8B-AWQ
  LLM_API_BASE / CORE_LLM_API_BASE      for self-hosted endpoints
  LLM_API_KEY                           optional; providers' own *_API_KEY variables also work
  LLM_REASONING_OFF=1 / CORE_LLM_REASONNING=NONE   ask the model to skip its thinking pass
  LLM_TIMEOUT_S, LLM_MAX_TOKENS, LLM_TEMPERATURE
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from runtime.config import env


class LLMError(RuntimeError):
    pass


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
        model = env("LLM_MODEL", "CORE_LLM_MODEL_NAME")
        if not model:
            raise LLMError("set LLM_MODEL (or CORE_LLM_MODEL_NAME)")
        off = env("LLM_REASONING_OFF") == "1" or env("CORE_LLM_REASONNING") == "NONE"
        return cls(
            model=model,
            api_base=_url(env("LLM_API_BASE", "CORE_LLM_API_BASE")),
            api_key=env("LLM_API_KEY"),
            reasoning_off=off,
            timeout_s=float(env("LLM_TIMEOUT_S", default="30")),
            max_tokens=int(env("LLM_MAX_TOKENS", default="600")),
            temperature=float(env("LLM_TEMPERATURE", default="0.8")),
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

    def _extra(self) -> dict:
        if not self.config.reasoning_off:
            return {}
        if self.config.model.startswith("openrouter/"):
            return {"extra_body": {"reasoning": {"enabled": False}}}
        if self.config.api_base:  # self-hosted vLLM with a thinking chat template (Qwen3)
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

        delay, last = 1.0, None
        for attempt in range(self.config.retries + 1):
            try:
                reply = litellm.completion(**kwargs)
                return (reply.choices[0].message.content or "").strip()
            except Exception as exc:  # provider errors vary; every one is retried with a pause
                last = exc
                if attempt < self.config.retries:
                    time.sleep(delay)
                    delay *= 2
        raise LLMError(f"LLM call failed after {self.config.retries + 1} attempts: {last}") from last

    def complete_json(self, system: str, user: str) -> dict:
        return parse_json_object(self.complete(system, user, json_mode=True))
