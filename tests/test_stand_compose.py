"""T-037: docker/compose.yml — node memory, zai-egress dependency, restart policies.

Pins the stand against the three defects behind the task: a node sandbox pinned at its
memory cap (T-032 incident 2: 1005/999 MiB of the old 1g), a node that can start without
its only route to api.z.ai (T-034 П-1), and quota spenders that resurrect unattended when
Docker Desktop comes back up (T-034 П-4). The daemon-restart matrix itself was measured
live in T-037 (docker inspect + docker desktop restart + wsl --shutdown); these tests only
keep the chosen configuration from drifting.
"""

from __future__ import annotations

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
