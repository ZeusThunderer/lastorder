"""T-037: docker/compose.yml — node memory, zai-egress dependency, restart policies.

Pins the stand against the three defects behind the task: a node sandbox pinned at its
memory cap (T-032 incident 2: 1005/999 MiB of the old 1g), a node that can start without
its only route to api.z.ai (T-034 П-1), and quota spenders that resurrect unattended when
Docker Desktop comes back up (T-034 П-4). The daemon-restart matrix itself was measured
live in T-037 (docker inspect + docker desktop restart + wsl --shutdown); these tests only
keep the chosen configuration from drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))

# node-N and last-order run agent harness sessions: the services that spend quota
QUOTA_SPENDERS = ("node-1", "node-2", "node-3", "last-order")


def node_services() -> dict[str, dict]:
    return {n: s for n, s in COMPOSE["services"].items() if n.startswith("node-")}


def test_three_slots_exist():
    assert set(node_services()) == {"node-1", "node-2", "node-3"}


def test_node_limits_match_network_doc():
    # 1.5g over the measured 1005 MiB peak = the body's 1.5x margin (NETWORK.md section 9)
    for name, svc in node_services().items():
        assert svc["mem_limit"] == "1.5g", name
        assert svc["cpus"] == "0.5", name


def test_nodes_depend_on_zai_egress():
    # T-034 П-1: without the passthrough the zcode harness has no route to api.z.ai
    for name, svc in node_services().items():
        assert "zai-egress" in svc["depends_on"], name


def test_quota_spenders_never_auto_restart():
    # Measured in T-037: after a hard engine kill, containers that were running come back
    # under on-failure/unless-stopped, only restart:"no" stays down.
    for name in QUOTA_SPENDERS:
        assert COMPOSE["services"][name]["restart"] == "no", name


def test_infrastructure_restarts_on_failure():
    # Everything else keeps daemon crash-restart (the body-N row of the section 9 table)
    for name in set(COMPOSE["services"]) - set(QUOTA_SPENDERS):
        assert COMPOSE["services"][name]["restart"] == "on-failure", name


# --- T-059: Last Order on the Z.ai Coding Plan -------------------------------------------------


def test_last_order_rides_pi_with_gateway_failover():
    env = COMPOSE["services"]["last-order"]["environment"]
    assert env["ADMIN_HARNESS"] == "${ADMIN_HARNESS:-pi}"
    assert env["ADMIN_HARNESS_FAILOVER"] == "${ADMIN_HARNESS_FAILOVER:-opencode}"
    # the operator's pick: the model, not Flash; the compaction budget is T-066's
    assert env["ADMIN_PI_MODEL"] == "${ADMIN_PI_MODEL:-GLM-5.3}"
    assert env["ADMIN_PI_CONTEXT_WINDOW"] == "${ADMIN_PI_CONTEXT_WINDOW:-96000}"
    # the token rides the same lift as node-N (up.sh lifts .env's ZAI_API_KEY as
    # ZCODE_API_KEY), and the entrypoint materializes it the same way
    assert env["ZAI_API_KEY"] == "${ZCODE_API_KEY:-}"
    assert env["ZAI_KEY_FILE"] == "/run/node/secrets/zai_api_key"


def test_last_order_key_surface_is_zai_alone():
    # ADR-033: the subscription token is the conscious exception; the gateway virtual
    # key and the hive key are the stand's own, no provider key rides anything else.
    # T-043 adds HAPI_CLI_KEY — the hub's chat token of the one long session, the
    # DESK sandbox's own conscious exception (it opens chat sessions, not providers);
    # the sisters' sandboxes still get no hub token at all (T-044/T-045)
    env = COMPOSE["services"]["last-order"]["environment"]
    keyish = {k for k in env if ("KEY" in k or "TOKEN" in k) and not k.endswith("_KEY_FILE")}
    assert keyish == {"HIVE_MCP_LO_KEY", "GATEWAY_NODE_KEY", "ZAI_API_KEY", "HAPI_CLI_KEY"}


def test_zai_egress_carries_api_zai_into_svc_lo():
    # T-059: the desk's pi harness talks to api.z.ai like a sister's — the SNI
    # passthrough is svc-lo's only way out, the network itself stays internal
    nets = COMPOSE["services"]["zai-egress"]["networks"]
    assert nets["svc-lo"]["aliases"] == ["api.z.ai"]
    assert COMPOSE["networks"]["svc-lo"]["internal"] is True
    assert COMPOSE["services"]["last-order"]["networks"] == ["svc-lo"]


# --- T-054: wait_events under the harness tool timeout ------------------------------------------


def test_mind_wait_events_cap_stays_under_the_harness_kill():
    # zcode kills a tool call after 30 s (7 killed waits on node-2, T-052); the
    # compose default keeps every ask under it with a margin, and an operator
    # override rides the same variable.
    for name in ("mind-1", "mind-2", "mind-3"):
        raw = COMPOSE["services"][name]["environment"]["WAIT_EVENTS_MAX_S"]
        assert raw == "${WAIT_EVENTS_MAX_S:-25}", name
        assert float(re.search(r":-(\d+(?:\.\d+)?)}", raw).group(1)) < 30.0, name
