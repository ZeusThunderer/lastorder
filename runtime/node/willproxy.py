"""The will-session MCP proxy: a localhost forwarder that carries the runner's mark.

`submit_will` (T-028) runs only in the will session: the request must carry
`X-Runner-Session: will`, and with runner keys configured (T-027 plan review P1-1) also
the slot's `X-Runner-Key`. Both belong to the runner, not to the sister: the death path
rewrites the workplace's hive MCP entry to this proxy, which adds the two headers on
every forwarded request. The runner key never enters the workplace, the child env or
the page of any file the sister can read.

Body streaming is verbatim (MCP streamable HTTP answers SSE); only the hop changes.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

WILL_SESSION_HEADER = "X-Runner-Session"
WILL_SESSION_VALUE = "will"
NODE_KEY_HEADER = "X-Node-Key"
RUNNER_KEY_HEADER = "X-Runner-Key"
_HOP_HEADERS = frozenset(
    {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
        "x-node-key", "x-runner-key", "x-runner-session",
    }
)


class WillProxy:
    """Starts on 127.0.0.1 (port 0 unless fixed) and forwards everything to one URL."""

    def __init__(
        self,
        target: str,
        *,
        node_key: str,
        runner_key: str,
        client: httpx.Client | None = None,
    ):
        self._target = target
        self._node_key = node_key
        self._runner_key = runner_key
        self._client = client
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else 0

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    def start(self) -> str:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:  # quiet: the runner logs
                return

            def _forward(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else None
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in _HOP_HEADERS
                }
                headers[NODE_KEY_HEADER] = proxy._node_key
                headers[RUNNER_KEY_HEADER] = proxy._runner_key
                headers[WILL_SESSION_HEADER] = WILL_SESSION_VALUE
                client = proxy._client or httpx.Client(timeout=60.0)
                owned = proxy._client is None
                try:
                    with client.stream(
                        self.command, proxy._target, headers=headers, content=body
                    ) as response:
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in _HOP_HEADERS:
                                self.send_header(key, value)
                        self.end_headers()
                        for chunk in response.iter_raw():
                            if chunk:
                                self.wfile.write(chunk)
                                self.wfile.flush()
                except (httpx.HTTPError, OSError):
                    try:
                        self.send_error(502, "hive unreachable through the will proxy")
                    except OSError:
                        pass
                finally:
                    if owned:
                        client.close()

            do_GET = do_POST = do_PUT = do_DELETE = do_OPTIONS = do_PATCH = _forward

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # ephemeral port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.url()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
