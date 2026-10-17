"""Minimal Source RCON client for the hive's forced deaths (NETWORK.md section 7).

The server is ours (compose `mc-server`), so there is exactly one consumer of this
client: `runtime/hive/lifecycle.py`, which turns a 45-minute deadline or a stuck
node's own request (ADR-026) into `/kill LastOrder0N`. Host and password come from
env only; the password never appears in `repr`, exceptions or events (review P1-9).
The wire format and the response slicing follow `tools/body_probe.py`, the E1 spike
that proved this exact exchange against the real server.

`kill()` returns the server's answer text and lets the caller judge it: an offline
player gets a successful transport exchange with "No entity was found", which is
not a confirmed kill (review P1-6).
"""

from __future__ import annotations

import socket
import struct

from runtime.config import env

SERVERDATA_AUTH = 3
SERVERDATA_EXECCOMMAND = 2
_AUTH_REJECTED = -1


class RconError(RuntimeError):
    """The RCON exchange failed; the forced death did not happen."""


class RconAuthError(RconError):
    """The server rejected the password (the password itself stays out of the message)."""


class RconClient:
    """One command per connection: kills are rare, and a stale socket serves nobody."""

    def __init__(
        self, host: str, port: int = 25575, password: str = "", timeout_s: float = 10.0
    ):
        self.host = host
        self.port = port
        self.password = password
        self.timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> "RconClient":
        # No password fallback (review R1-5): the only default lives in compose;
        # without RCON_PASSWORD the kill refuses instead of guessing.
        return cls(
            host=env("RCON_HOST") or "mc-server",
            port=int(env("RCON_PORT") or 25575),
            password=env("RCON_PASSWORD") or "",
            timeout_s=float(env("RCON_TIMEOUT_S") or 10.0),
        )

    def __repr__(self) -> str:  # the password is a secret: never in logs or tracebacks
        return f"RconClient(host={self.host!r}, port={self.port}, timeout_s={self.timeout_s})"

    @staticmethod
    def _packet(request_id: int, kind: int, body: str) -> bytes:
        data = struct.pack("<ii", request_id, kind) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(data)) + data

    def command(self, command: str) -> str:
        if not self.password:
            raise RconError("RCON_PASSWORD is not set; refusing to guess")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as sock:
                sock.settimeout(self.timeout_s)
                sock.sendall(self._packet(1, SERVERDATA_AUTH, self.password))
                auth = sock.recv(4096)
                if len(auth) < 14 or struct.unpack("<i", auth[4:8])[0] == _AUTH_REJECTED:
                    raise RconAuthError("rcon auth rejected")
                sock.sendall(self._packet(2, SERVERDATA_EXECCOMMAND, command))
                raw = sock.recv(4096)
        except RconError:
            raise
        except OSError as exc:
            raise RconError(f"rcon {self.host}:{self.port}: {exc}") from exc
        if not raw:
            raise RconError("rcon: empty response")
        return raw[12:-2].decode(errors="replace")

    def kill(self, player: str) -> str:
        """`/kill <player>`; the answer text is the caller's problem (see P1-6)."""
        return self.command(f"kill {player}")


def kill_confirmed(response: str) -> bool:
    """Did the server actually kill anyone? A positive match only (review R1-4):
    the vanilla answer to `/kill` starts with "Killed". Anything else — the empty
    answer, "No entity was found" (an offline player), "Unknown or incomplete
    command" — means the kill did not happen, whatever the transport says."""

    return response.strip().lower().startswith("killed")
