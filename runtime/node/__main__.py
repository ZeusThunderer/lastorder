"""Entrypoint of node-N: `python -m runtime.node` (docker/node, compose service node-N)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from runtime.lastorder.naming import preset
from runtime.lastorder.persona import PersonaProvider, load_core_template
from runtime.lastorder.traits import load_quirks
from runtime.node.adapters import HermesAdapter, OpenCodeAdapter, ZCodeAdapter
from runtime.node.config import NodeConfig
from runtime.node.remotes import HiveApi, MindApi
from runtime.node.runner import NodeRunner


def _log(type: str, payload: dict | None = None) -> None:
    print(
        json.dumps(
            {"ts": datetime.now(timezone.utc).isoformat(), "type": type, **(payload or {})},
            ensure_ascii=False,
        ),
        flush=True,
    )


def main() -> int:
    cfg = NodeConfig.from_env()
    _log("runner.start", {"slot": cfg.slot, "harness": cfg.harness, "failover": cfg.failover or None})
    naming = preset(cfg.naming)
    quirks = load_quirks(cfg.template_dir / "quirks.txt")
    runner = NodeRunner(
        cfg,
        mind=MindApi(cfg.body_base, runner_key=cfg.runner_key),
        hive=HiveApi(cfg.hive_url, runner_key=cfg.runner_key),
        adapters={
            "opencode": OpenCodeAdapter(cfg.opencode_model),
            "zcode": ZCodeAdapter(cfg.zcode_model),
            "hermes": HermesAdapter(cfg.hermes_model),
        },
        naming=naming,
        quirks=quirks,
        persona=PersonaProvider(
            naming, core_template=load_core_template(cfg.template_dir / "PERSONA.md")
        ),
        log=_log,
    )
    try:
        runner.run()
    except KeyboardInterrupt:
        _log("runner.stop", {})
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
