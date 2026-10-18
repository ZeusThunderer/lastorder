"""T-026 llm-gateway: proxy config, compose wiring and the client-side gateway env.

Files only — no docker, no network. The checks mirror the acceptance criteria: tier models are
gateway settings, provider keys exist only as environment references, the gateway and its DB
publish no ports, and consumers get the gateway address plus their own virtual key.
"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev"))

from gateway_keys import build_issue_payload, build_revoke_payload  # noqa: E402
from gateway_report import parse_since  # noqa: E402
from runtime.llm.client import LLMConfig  # noqa: E402

COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))
GATEWAY_CONFIG = yaml.safe_load((ROOT / "docker" / "gateway" / "config.yaml").read_text(encoding="utf-8"))
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
SERVICES = COMPOSE["services"]


def env_example_value(name: str) -> str:
    for line in ENV_EXAMPLE.splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{name} missing from .env.example")


class TestGatewayConfig:
    def test_model_list_covers_the_adr_023_tiers(self):
        names = {m["model_name"] for m in GATEWAY_CONFIG["model_list"]}
        assert env_example_value("LLM_NODE_MODEL") in names
        assert env_example_value("LLM_SMART_MODEL") in names

    def test_provider_key_is_an_environment_reference(self):
        for model in GATEWAY_CONFIG["model_list"]:
            assert model["litellm_params"]["api_key"] == "os.environ/OPENROUTER_API_KEY"
            # every name routes to the full litellm string of its tier
            assert model["litellm_params"]["model"].startswith("openrouter/")

    def test_each_tier_answers_to_full_and_bare_names(self):
        """The SDK strips the provider prefix under a custom api_base; both forms must route."""
        names = {m["model_name"] for m in GATEWAY_CONFIG["model_list"]}
        for tier_model in ("LLM_NODE_MODEL", "LLM_SMART_MODEL"):
            full = env_example_value(tier_model)
            bare = full.split("/", 1)[1]
            assert full in names and bare in names

    def test_master_key_and_database_are_environment_references(self):
        general = GATEWAY_CONFIG["general_settings"]
        assert general["master_key"] == "os.environ/LITELLM_MASTER_KEY"
        assert general["database_url"] == "os.environ/DATABASE_URL"

    def test_unknown_params_are_dropped_not_fatal(self):
        assert GATEWAY_CONFIG["litellm_settings"]["drop_params"] is True


class TestComposeWiring:
    def test_gateway_and_db_publish_no_ports(self):
        for name in ("llm-gateway", "gateway-db"):
            assert "ports" not in SERVICES[name], f"{name} must stay inside the docker network"

    def test_gateway_mounts_the_config_and_gets_the_provider_key(self):
        service = SERVICES["llm-gateway"]
        assert service["environment"]["OPENROUTER_API_KEY"] == "${OPENROUTER_API_KEY:-}"
        assert "gateway-db:5432" in service["environment"]["DATABASE_URL"]
        assert any("gateway/config.yaml" in str(v) for v in service["volumes"])

    def test_db_is_private_to_the_gateway(self):
        assert SERVICES["gateway-db"]["networks"] == ["gateway-internal"]
        assert set(SERVICES["llm-gateway"]["networks"]) == {"default", "gateway-internal"}
        for name, service in SERVICES.items():
            if name not in ("gateway-db", "llm-gateway"):
                assert "gateway-internal" not in (service.get("networks") or [])

    def test_provider_key_reaches_only_the_gateway(self):
        for name, service in SERVICES.items():
            text = str(service.get("environment", "")) + str(service.get("command", ""))
            assert "OPENROUTER_API_KEY" not in text or name == "llm-gateway"

    def test_consumers_point_at_the_gateway_with_their_own_keys(self):
        for name, var in (("mind-1", "GATEWAY_KEY_MIND_1"), ("hive", "GATEWAY_KEY_HIVE")):
            env = SERVICES[name]["environment"]
            assert env["LLM_API_BASE"] == "http://llm-gateway:4000"
            assert env["LLM_API_KEY"] == "${" + var + ":-}"


class TestNoSecretsInFiles:
    def test_no_literal_tokens_or_keys(self):
        files = [
            ROOT / "docker" / "compose.yml",
            ROOT / "docker" / "gateway" / "config.yaml",
            ROOT / ".env.example",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"sk-[A-Za-z0-9]{16,}", text), path
            assert not re.search(r"(?i)api_?key\w*\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{24,}", text), path

    def test_env_example_names_the_gateway_variables(self):
        for name in ("GATEWAY_MASTER_KEY", "GATEWAY_DB_PASSWORD", "GATEWAY_KEY_NODE_1",
                     "GATEWAY_KEY_HIVE", "GATEWAY_KEY_MIND_1"):
            assert re.search(rf"^{name}=$", ENV_EXAMPLE, re.M)


class TestClientVirtualKey:
    def test_tier_key_overrides_the_shared_one(self, monkeypatch):
        monkeypatch.setenv("LLM_NODE_MODEL", "openrouter/qwen/qwen3.7-flash")
        monkeypatch.setenv("LLM_API_KEY", "sk-shared")
        monkeypatch.setenv("LLM_NODE_API_KEY", "sk-node")
        assert LLMConfig.for_tier("node").api_key == "sk-node"

    def test_tier_falls_back_to_the_shared_key(self, monkeypatch):
        monkeypatch.setenv("LLM_SMART_MODEL", "openrouter/z-ai/glm-5.3-flash")
        monkeypatch.setenv("LLM_API_KEY", "sk-shared")
        assert LLMConfig.for_tier("smart").api_key == "sk-shared"

    def test_gateway_address_and_key_travel_together(self, monkeypatch):
        monkeypatch.setenv("LLM_NODE_MODEL", "openrouter/qwen/qwen3.7-flash")
        monkeypatch.setenv("LLM_API_BASE", "http://llm-gateway:4000")
        monkeypatch.setenv("LLM_NODE_API_KEY", "sk-node")
        config = LLMConfig.for_tier("node")
        assert config.api_base == "http://llm-gateway:4000"
        assert config.api_key == "sk-node"
        # the model string stays the full litellm name: the gateway routes by it unchanged
        assert config.model == "openrouter/qwen/qwen3.7-flash"


class TestKeyScriptPayloads:
    def test_issue_payload_carries_the_adr_035_ceilings(self):
        payload = build_issue_payload("node-1", rpm=12, hourly_usd=0.12,
                                      models=["openrouter/qwen/qwen3.7-flash"])
        assert payload["key_alias"] == "node-1"
        assert payload["rpm_limit"] == 12
        assert payload["max_budget"] == 0.12
        assert payload["budget_duration"] == "1h"  # the budget recurs hourly
        assert payload["models"] == ["openrouter/qwen/qwen3.7-flash"]

    def test_issue_without_models_allows_the_whole_config(self):
        payload = build_issue_payload("hive", rpm=30, hourly_usd=0.1, models=[])
        assert "models" not in payload

    def test_revoke_payload_targets_the_token(self):
        assert build_revoke_payload("sk-x") == {"keys": ["sk-x"]}


class TestSinceParsing:
    def test_units_combine(self):
        assert parse_since("90m") == 5400
        assert parse_since("2h30m") == 9000
        assert parse_since("1d") == 86400

    def test_garbage_is_rejected(self):
        for bad in ("soon", "5x"):
            try:
                parse_since(bad)
            except SystemExit:
                continue
            raise AssertionError(f"{bad!r} must not parse")
