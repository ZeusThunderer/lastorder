"""Drive three sister proposals through the real node MCP (T-030 stand run).

Runs INSIDE a node-N container (the sandbox that owns the sister's identity):

    docker compose -f docker/compose.yml exec -T node-1 \
      python /stand_proposals.py   # mounted read-only from the task dir, see below

The node key is read from the runner's 0400 file (root in the container may); the value
never leaves the process. Three `propose` calls — enough for the ADMIN_PROPOSALS_BATCH=3
wake — plus one `send` so the summary shows a live sister talking.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

KEY_FILE = Path("/run/node/secrets/node_mcp_key")
HIVE_MCP = "http://hive:8000/node/mcp"

CLAIMS = [
    (
        "knowledge",
        {
            "kind": "danger",
            "claim": "ночной стон бьёт насмерть с трёх ударов, если стоять вплотную",
            "about": ["ночной стон"],
            "evidence_event_ids": [],
        },
    ),
    (
        "knowledge",
        {
            "kind": "recipe",
            "claim": "жареную свинину можно готовить из сырой свинины и угля",
            "about": ["свинина"],
            "evidence_event_ids": [],
        },
    ),
    (
        "skill",
        {
            "description": "проверить глубину водой перед входом в воду",
            "script": "# placeholder body: look down, probe with a block, step in only on solid floor",
        },
    ),
]


async def main() -> int:
    key = KEY_FILE.read_text(encoding="utf-8").strip()
    headers = {"X-Node-Key": key}
    async with Client(streamable_http_client(HIVE_MCP, headers=headers)) as client:
        for kind, payload in CLAIMS:
            result = await client.call_tool("propose", {"kind": kind, "payload": payload})
            text = result.content[0].text if result.content else "{}"
            print(f"{kind}: {text}")
        sent = await client.call_tool(
            "send", {"to": "all", "text": "отправила три предложения, ждём решения"}
        )
        print(f"send: {sent.content[0].text if sent.content else '{}'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
