"""Run inside mind-1. Speak through Minecraft."""
import asyncio
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(text):
    async with streamable_http_client("http://localhost:8700/mcp") as streams:
        async with ClientSession(*streams[:2]) as session:
            await session.initialize()
            result = await session.call_tool("say", {"text": text})
            print(time.time(), result.content, flush=True)
            if result.is_error:
                raise RuntimeError("body refused the line")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
