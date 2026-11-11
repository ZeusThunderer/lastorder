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
import subprocess
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
    # T-064: media-net joined for the footage; svc-lo is still the only way out
    assert COMPOSE["services"]["last-order"]["networks"] == ["svc-lo", "media-net"]


# --- T-054: wait_events under the harness tool timeout ------------------------------------------


def test_mind_wait_events_cap_stays_under_the_harness_kill():
    # zcode kills a tool call after 30 s (7 killed waits on node-2, T-052); the
    # compose default keeps every ask under it with a margin, and an operator
    # override rides the same variable.
    for name in ("mind-1", "mind-2", "mind-3"):
        raw = COMPOSE["services"][name]["environment"]["WAIT_EVENTS_MAX_S"]
        assert raw == "${WAIT_EVENTS_MAX_S:-25}", name
        assert float(re.search(r":-(\d+(?:\.\d+)?)}", raw).group(1)) < 30.0, name


# --- T-055: the bridge-friendly slot lifecycle --------------------------------------------------


def test_body_healthcheck_probes_the_py4j_gateway_port():
    # Ready = uc's gateway is listening (the mod starts it at the title screen);
    # a mind connecting during body load breaks the bridge (T-052 b).
    for name in ("body-1", "body-2", "body-3"):
        hc = COMPOSE["services"][name]["healthcheck"]
        assert "25333" in hc["test"][-1], name
        assert hc["start_period"] == "300s", name  # a cold volume downloads assets first


def test_mind_waits_for_a_healthy_body_and_stops_gracefully():
    for name, body in (("mind-1", "body-1"), ("mind-2", "body-2"), ("mind-3", "body-3")):
        svc = COMPOSE["services"][name]
        assert svc["depends_on"][body]["condition"] == "service_healthy", name
        # the SIGTERM teardown (~11 s) must fit inside the grace period
        assert svc["stop_grace_period"] == "20s", name


def test_slot_restart_dry_run_prints_the_sequence():
    # The acceptance command itself: the dry run prints every step without
    # touching docker (mind stop → body recreate → health wait → mind start).
    out = subprocess.run(
        ["bash", "dev/slot_restart.sh", "1", "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "stop mind-1" in out
    # `up` goes through docker/node/up.sh, which lifts the slot keys from .env (T-058)
    assert "docker/node/up.sh --force-recreate --no-deps body-1" in out
    assert "--healthy" in out and "ps -q body-1" in out
    assert "docker/node/up.sh --no-deps mind-1" in out
    other = subprocess.run(
        ["bash", "dev/slot_restart.sh", "1", "--dry-run", "-p", "t999"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "COMPOSE_PROJECT_NAME=t999 bash docker/node/up.sh --no-deps mind-1" in other


# --- the infra bridge surface (T-057) --------------------------------------------------------


def test_infra_key_reaches_the_hive_alone():
    # ADR-044: the executor's key opens the board's infra routes and nothing else,
    # so it must not leak into any sandbox or the LO desk. The page reads the
    # switch with the operator key; the bridge polls from the host.
    for name, svc in COMPOSE["services"].items():
        env = svc.get("environment") or {}
        values = list(env.values()) if isinstance(env, dict) else env
        has = any("HIVE_INFRA_KEY" in v for v in values if isinstance(v, str))
        assert (name == "hive") == has, f"HIVE_INFRA_KEY leaked into {name}"


def test_every_slot_points_at_a_configurable_server():
    # T-052 ran the slot-1 override by hand; T-057 makes the same shape uniform —
    # the bridge's slot_server action rides MC_SERVER_N the same way .env does.
    for n in (1, 2, 3):
        env = COMPOSE["services"][f"body-{n}"]["environment"]
        assert env["MC_SERVER"] == f"${{MC_SERVER_{n}:-mc-server:25565}}", f"body-{n}"


def test_env_example_names_the_bridge_key():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "HIVE_INFRA_KEY=" in text
    assert "MC_SERVER_1=" in text and "MC_SERVER_2=" in text and "MC_SERVER_3=" in text
