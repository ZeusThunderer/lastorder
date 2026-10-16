"""Tier configs (ADR-023) and per-call stats of the LLM client. No network: litellm is faked."""

from types import SimpleNamespace

import pytest

from runtime.llm.client import LLMClient, LLMConfig, LLMError, parse_json_object

ALL_ENV_VARS = (
    "LLM_MODEL",
    "LLM_API_BASE",
    "LLM_API_KEY",
    "LLM_REASONING_OFF",
    "LLM_TIMEOUT_S",
    "LLM_MAX_TOKENS",
    "LLM_TEMPERATURE",
    "CORE_LLM_MODEL_NAME",
    "CORE_LLM_API_BASE",
    "CORE_LLM_REASONNING",
    "LLM_NODE_MODEL",
    "LLM_NODE_API_BASE",
    "LLM_NODE_REASONING_OFF",
    "LLM_NODE_MAX_TOKENS",
    "LLM_NODE_TEMPERATURE",
    "LLM_NODE_TIMEOUT_S",
    "LLM_SMART_MODEL",
    "LLM_SMART_API_BASE",
    "LLM_SMART_REASONING_OFF",
    "LLM_SMART_MAX_TOKENS",
    "LLM_SMART_TEMPERATURE",
    "LLM_SMART_TIMEOUT_S",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def fake_reply(content='{"option_id": "a"}', prompt=120, completion=30, cost=0.0004):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
        _hidden_params={"response_cost": cost},
    )


class TestForTier:
    def test_node_tier_reads_tier_variables(self, monkeypatch):
        monkeypatch.setenv("LLM_NODE_MODEL", "openrouter/qwen/qwen3.7-flash")
        monkeypatch.setenv("LLM_NODE_MAX_TOKENS", "300")
        monkeypatch.setenv("LLM_NODE_TEMPERATURE", "0.5")
        monkeypatch.setenv("LLM_NODE_TIMEOUT_S", "12")
        monkeypatch.setenv("LLM_NODE_REASONING_OFF", "1")
        config = LLMConfig.for_tier("node")
        assert config.model == "openrouter/qwen/qwen3.7-flash"
        assert config.max_tokens == 300
        assert config.temperature == 0.5
        assert config.timeout_s == 12.0
        assert config.reasoning_off is True

    def test_smart_tier_reads_tier_variables(self, monkeypatch):
        monkeypatch.setenv("LLM_SMART_MODEL", "openrouter/z-ai/glm-5.3-flash")
        config = LLMConfig.for_tier("smart")
        assert config.model == "openrouter/z-ai/glm-5.3-flash"

    def test_tier_falls_back_to_base_llm_names(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "openrouter/xiaomi/mimo-v2.6-flash")
        monkeypatch.setenv("LLM_MAX_TOKENS", "777")
        config = LLMConfig.for_tier("node")
        assert config.model == "openrouter/xiaomi/mimo-v2.6-flash"
        assert config.max_tokens == 777

    def test_tier_variable_overrides_the_base_one(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "openrouter/xiaomi/mimo-v2.6-flash")
        monkeypatch.setenv("LLM_MAX_TOKENS", "777")
        monkeypatch.setenv("LLM_NODE_MODEL", "openrouter/qwen/qwen3.7-flash")
        monkeypatch.setenv("LLM_NODE_MAX_TOKENS", "111")
        config = LLMConfig.for_tier("node")
        assert config.model == "openrouter/qwen/qwen3.7-flash"
        assert config.max_tokens == 111
        assert LLMConfig.from_env().model == "openrouter/xiaomi/mimo-v2.6-flash"

    def test_tier_api_base_needs_a_real_url(self, monkeypatch):
        monkeypatch.setenv("LLM_NODE_MODEL", "m")
        monkeypatch.setenv("LLM_NODE_API_BASE", "NONE")
        assert LLMConfig.for_tier("node").api_base is None
        monkeypatch.setenv("LLM_NODE_API_BASE", "http://127.0.0.1:8000/v1")
        assert LLMConfig.for_tier("node").api_base == "http://127.0.0.1:8000/v1"

    def test_reasoning_off_via_tier_and_legacy_names(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "m")
        monkeypatch.setenv("LLM_NODE_REASONING_OFF", "1")
        assert LLMConfig.for_tier("node").reasoning_off is True
        monkeypatch.delenv("LLM_NODE_REASONING_OFF")
        monkeypatch.setenv("LLM_REASONING_OFF", "1")
        assert LLMConfig.for_tier("smart").reasoning_off is True
        monkeypatch.delenv("LLM_REASONING_OFF")
        monkeypatch.setenv("CORE_LLM_REASONNING", "NONE")
        assert LLMConfig.for_tier("node").reasoning_off is True
        monkeypatch.delenv("CORE_LLM_REASONNING")
        assert LLMConfig.for_tier("node").reasoning_off is False

    def test_unknown_tier_is_rejected(self):
        with pytest.raises(LLMError, match="unknown tier"):
            LLMConfig.for_tier("jev")

    def test_missing_model_names_the_tier_variable(self):
        with pytest.raises(LLMError, match="LLM_SMART_MODEL"):
            LLMConfig.for_tier("smart")

    def test_from_env_keeps_the_legacy_nettyan_fallback(self, monkeypatch):
        monkeypatch.setenv("CORE_LLM_MODEL_NAME", "hosted_vllm/Qwen/Qwen3-8B-AWQ")
        assert LLMConfig.from_env().model == "hosted_vllm/Qwen/Qwen3-8B-AWQ"


class TestCallStats:
    def test_complete_records_tokens_latency_and_cost(self, monkeypatch):
        import litellm

        monkeypatch.setattr(litellm, "completion", lambda **kwargs: fake_reply(cost=0.0004))
        client = LLMClient(LLMConfig(model="openrouter/xiaomi/mimo-v2.6-flash"))
        reply = client.complete("system", "user")
        assert reply == '{"option_id": "a"}'
        stats = client.last_call
        assert stats.model == "openrouter/xiaomi/mimo-v2.6-flash"
        assert stats.prompt_tokens == 120
        assert stats.completion_tokens == 30
        assert stats.cost_usd == pytest.approx(0.0004)
        assert 0.0 <= stats.latency_s < 5.0

    def test_complete_json_updates_stats_too(self, monkeypatch):
        import litellm

        monkeypatch.setattr(litellm, "completion", lambda **kwargs: fake_reply())
        client = LLMClient(LLMConfig(model="m"))
        assert client.complete_json("s", "u") == {"option_id": "a"}
        assert client.last_call.prompt_tokens == 120

    def test_cost_stays_none_without_provider_price(self, monkeypatch):
        import litellm

        reply = fake_reply()
        reply._hidden_params = {}
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: reply)
        client = LLMClient(LLMConfig(model="m"))
        client.complete("s", "u")
        assert client.last_call.cost_usd is None
        assert client.last_call.prompt_tokens == 120

    def test_openrouter_reasoning_off_goes_into_extra_body(self, monkeypatch):
        import litellm

        seen = {}

        def capture(**kwargs):
            seen.update(kwargs)
            return fake_reply()

        monkeypatch.setattr(litellm, "completion", capture)
        client = LLMClient(LLMConfig(model="openrouter/xiaomi/mimo-v2.6-flash", reasoning_off=True))
        client.complete("s", "u")
        assert seen["extra_body"] == {"reasoning": {"enabled": False}}


def test_parse_json_object_tolerates_fences_and_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Вот ответ: {"a": {"b": 2}} — держи.') == {"a": {"b": 2}}
    with pytest.raises(LLMError):
        parse_json_object("нет здесь объекта")
