"""T-064: the Last Order video desk — media reach, read-only journals, sisters locked out.

The desk is the one place with the footage: it builds the `lo` image variant (ffmpeg),
joins media-net and gets the network's journals read-only. The sisters' sandboxes keep
the lean `base` image, no media-net and no journal volumes — only their pending_wills
subpath, as since T-028.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text(encoding="utf-8"))

LO = COMPOSE["services"]["last-order"]


def vol_targets(volumes: list) -> set[str]:
    out = set()
    for v in volumes:
        if isinstance(v, str):
            out.add(v.split(":")[1] if v.count(":") == 2 else v.rsplit(":", 1)[-1])
        else:
            out.add(v["target"])
    return out


def test_desk_builds_the_lo_variant_with_ffmpeg():
    assert LO["build"]["target"] == "lo"
    assert LO["image"] == "lastorder-node-lo:dev"
    # the sisters' sandboxes stay on the lean base image (no ffmpeg, no video tools)
    for n in (1, 2, 3):
        assert COMPOSE["services"][f"node-{n}"]["build"]["target"] == "base", f"node-{n}"
        assert COMPOSE["services"][f"node-{n}"]["image"] == "lastorder-node:dev", f"node-{n}"


def test_desk_reaches_media_over_media_net():
    assert "media-net" in LO["networks"] and "svc-lo" in LO["networks"]


def test_journals_are_mounted_read_only_on_the_desk_alone():
    vols = LO["volumes"]
    assert "hive-run:/net-logs/hive:ro" in vols
    for n in (1, 2, 3):
        assert f"mind-{n}-run:/net-logs/mind-{n}:ro" in vols
    # the sisters see neither the journals nor media: node-N carries only the
    # pending_wills subpath plus its own workplaces, and never joins media-net
    for n in (1, 2, 3):
        node = COMPOSE["services"][f"node-{n}"]
        targets = vol_targets(node["volumes"])
        assert "/hive-run/pending_wills" in targets, f"node-{n}"
        assert not any(t.startswith("/net-logs") for t in targets), f"node-{n}"
        assert "media-net" not in node["networks"], f"node-{n}"


def test_the_desk_reference_exists_and_stays_knowledge_not_orders():
    ref = (ROOT / "agent" / "network" / "reference" / "video-tools.md").read_text(encoding="utf-8")
    assert "media:9996" in ref and "/net-logs" in ref
    for order in ("must film", "should film", "always cut", "you have to"):
        assert order not in ref.lower(), order
    agents = (ROOT / "agent" / "network" / "AGENTS.md").read_text(encoding="utf-8")
    assert "What is around you" in agents and "reference/video-tools.md" in agents
