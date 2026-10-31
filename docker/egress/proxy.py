"""The grants egress proxy (T-046, ADR-041, TASKBOARD.md 5.3): an HTTP CONNECT
tunnel that opens ONLY the domains of active egress grants.

The proxy asks hive (`GET /grants/egress`, the tool key in X-Tool-Key) which
domains are open right now, with a short cache: an approval opens a domain and a
revoke closes it without any rebuild or restart. It terminates no TLS — the
client's CONNECT tunnel carries the real certificates end-to-end, exactly like
the zai-egress passthrough of ADR-033. Plain (non-CONNECT) requests are refused:
this is a tunnel for approved tools, not a forward proxy.

Stdlib alone: the image is python:3.12-slim with this one file.

    HIVE_URL   default http://hive:8000 — where the allowlist comes from
    HIVE_TOOL_KEY      the vault's tool key (X-Tool-Key)
    EGRESS_LISTEN      default 0.0.0.0:3128
    EGRESS_CACHE_S     allowlist cache seconds (default 3; 0 = ask every time)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
import urllib.request

HIVE_URL = os.environ.get("HIVE_URL", "http://hive:8000").rstrip("/")
TOOL_KEY = os.environ.get("HIVE_TOOL_KEY", "")
CACHE_S = float(os.environ.get("EGRESS_CACHE_S", "3"))
HEAD_LIMIT = 16384
TUNNEL_PORT = 443  # TLS end-to-end is the point; a grant names a domain, not a port


def _listen() -> tuple[str, int]:
    raw = os.environ.get("EGRESS_LISTEN", "0.0.0.0:3128")
    host, _, port = raw.rpartition(":")
    return host or "0.0.0.0", int(port)


def fetch_domains() -> frozenset[str]:
    """The active egress grants, straight from the vault."""
    request = urllib.request.Request(
        f"{HIVE_URL}/grants/egress", headers={"X-Tool-Key": TOOL_KEY}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = json.loads(response.read().decode("utf-8"))
    return frozenset(d.lower() for d in body.get("domains", []))


class Allowlist:
    """The cached view of the vault; a revoke lands within one cache tick."""

    def __init__(self) -> None:
        self._domains: frozenset[str] = frozenset()
        self._fetched_at = 0.0

    async def allows(self, host: str) -> bool:
        now = time.monotonic()
        if CACHE_S <= 0 or now - self._fetched_at > CACHE_S:
            self._domains = await asyncio.to_thread(fetch_domains)
            self._fetched_at = now
        return host.lower() in self._domains


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        with contextlib.suppress(Exception):  # a closed far end is not an error here
            await writer.drain()
        writer.close()


def _refuse(writer: asyncio.StreamWriter, status: str, note: bytes) -> None:
    head = (
        f"HTTP/1.1 {status}\r\nContent-Length: {len(note)}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    writer.write(head + note)


async def handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    allowlist: Allowlist,
) -> None:
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
        writer.close()
        return
    first = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    parts = first.split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT" or not parts[2].startswith("HTTP/"):
        _refuse(writer, "405 Method Not Allowed", b"CONNECT alone is spoken here\n")
        await writer.drain()
        writer.close()
        return
    host, _, port_text = parts[1].rpartition(":")
    try:
        port = int(port_text)
    except ValueError:
        port = TUNNEL_PORT
    if port != TUNNEL_PORT:
        _refuse(writer, "403 Forbidden", b"only the TLS port is granted\n")
        await writer.drain()
        writer.close()
        return
    if not await allowlist.allows(host):
        # the honest answer to an ungranted domain, before any DNS or dial
        _refuse(writer, "403 Forbidden", b"the domain has no active egress grant\n")
        await writer.drain()
        writer.close()
        return
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=10
        )
    except (OSError, asyncio.TimeoutError):
        _refuse(writer, "502 Bad Gateway", b"the granted host did not answer\n")
        await writer.drain()
        writer.close()
        return
    writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))


async def main() -> None:
    if not TOOL_KEY:
        raise SystemExit("egress: HIVE_TOOL_KEY is empty — the vault would refuse anyway")
    host, port = _listen()
    allowlist = Allowlist()
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, allowlist), host=host, port=port
    )
    print(f"egress: tunnel on {host}:{port}, allowlist from {HIVE_URL}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
