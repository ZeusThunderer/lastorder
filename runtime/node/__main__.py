"""Entrypoint of node-N: `python -m runtime.node` (docker/node, compose service node-N)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from runtime.lastorder.naming import preset
from runtime.lastorder.persona import PersonaProvider, load_core_template
from runtime.lastorder.traits import load_quirks
from runtime.node.adapters import HermesAdapter, OpenCodeAdapter, PiAdapter, ZCodeAdapter
from runtime.node.config import NodeConfig
from runtime.node.remotes import HiveApi, MindApi
from runtime.node.runner import BoardAlarms, NodeRunner


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
    _log(
        "runner.start",
        {
            "slot": cfg.slot,
            "harness": cfg.harness,
            "failover": cfg.failover or None,
            "console": bool(cfg.hapi_token) or None,
        },
    )
    naming = preset(cfg.naming)
    quirks = load_quirks(cfg.template_dir / "quirks.txt")
    runner = NodeRunner(
        cfg,
        mind=MindApi(cfg.body_base, runner_key=cfg.runner_key),
        hive=HiveApi(cfg.hive_url, runner_key=cfg.runner_key),
        adapters={
            # a namespaced hub token arms the `hapi pi` wrapper (T-045); without
            # one the adapter stays the direct-pi of T-066 — the harness is pi either way
            "pi": PiAdapter(
                cfg.pi_model,
                body_base=cfg.body_base,
                hive_url=cfg.hive_url,
                node_key=cfg.node_key,
                zai_key=cfg.zai_key,
                context_window=cfg.pi_context_window,
                keep_recent_tokens=cfg.pi_keep_recent_tokens,
                uid=cfg.sister_uid,
                gid=cfg.sister_gid,
                slot=cfg.slot,
                hapi_url=cfg.hapi_url,
                hapi_token=cfg.hapi_token,
            ),
            "opencode": OpenCodeAdapter(cfg.opencode_model),
            "zcode": ZCodeAdapter(cfg.zcode_model),
            "hermes": HermesAdapter(
                cfg.hermes_model,
                body_base=cfg.body_base,
                hive_url=cfg.hive_url,
                node_key=cfg.node_key,
            ),
        },
        naming=naming,
        quirks=quirks,
        persona=PersonaProvider(
            naming, core_template=load_core_template(cfg.template_dir / "PERSONA.md")
        ),
        alarms=BoardAlarms(cfg.hive_url, runner_key=cfg.runner_key),
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
