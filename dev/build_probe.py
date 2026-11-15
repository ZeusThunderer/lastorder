"""Live probe of the body's build tool (defence-plan W3) — what a sister would do, minus the sister.

Runs INSIDE the mind container (the MCP port is never published):
    docker exec -i lastorder-mind-1-1 python - "<schematic json>" [dx dz [limit_s [x y z]]] < dev/build_probe.py

observe -> build next to the body (dx, dz blocks away, on its feet level) -> build_status
every 5 s -> the intent.done / intent.failed event. Prints one line per step.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = "http://127.0.0.1:8700/mcp"


def text(result) -> dict:
    body = result.content[0].text if result.content else ""
    if result.is_error:
        return {"error": body}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"text": body}


async def main(schematic: str, dx: int, dz: int, limit_s: float, at: list[int] | None = None) -> None:
    async with Client(streamable_http_client(URL)) as c:
        seen = text(await c.call_tool("observe", {}))
        me = seen.get("self", {})
        print("observe:", json.dumps({k: me.get(k) for k in ("pos", "hp", "in_game")}),
              "hotbar:", [(i["name"], i["count"], i["slot"]) for i in seen.get("inventory", []) if i["slot"] < 9])
        x, y, z = (math.floor(c) for c in me["pos"])
        ox, oy, oz = at if at else (x + dx, y, z + dz)
        answer = text(await c.call_tool("build", {"schematic": json.loads(schematic),
                                                  "x": ox, "y": oy, "z": oz}))
        print("build:", json.dumps(answer, ensure_ascii=False))
        if "error" in answer:
            return
        t0 = time.monotonic()
        while time.monotonic() - t0 < limit_s:
            event = text(await c.call_tool("wait_events", {"timeout_s": 5}))
            status = text(await c.call_tool("build_status", {}))
            print(f"t={time.monotonic() - t0:5.0f}s status: placed={status.get('placed')} "
                  f"already={status.get('already')} queued={status.get('queued')} "
                  f"deferred={status.get('deferred')} reasons={status.get('reasons')}")
            if event.get("type", "").startswith("intent."):
                print("event:", json.dumps(event, ensure_ascii=False))
                if event["type"] in ("intent.done", "intent.failed"):
                    return
        print("probe: time limit reached")


if __name__ == "__main__":
    dx = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    dz = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    at = [int(v) for v in sys.argv[5:8]] if len(sys.argv) > 7 else None  # absolute origin
    asyncio.run(main(sys.argv[1], dx, dz, float(sys.argv[4]) if len(sys.argv) > 4 else 300, at))
