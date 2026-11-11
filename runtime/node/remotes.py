"""The runner's own HTTP surface: mind-N's /runner endpoints and hive's registry writes.

Deliberately NOT `runtime/hive/client.py`: that client speaks for a mind (the Memory
ports, the outbox). The runner needs three registry writes plus the death poll, all
carrying its own key (`X-Runner-Key`, T-027 plan review P1-1), and the section-9 retry
shape: connect errors and 5xx are retried with a growing pause, a read timeout and a
permanent 4xx are not. The exception types are shared with the mind's client so the
runner's death path reads the same way as `runtime/lastorder/lifecycle.py`.
"""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx

from runtime.hive.client import HiveRejected, HiveUnavailable
from runtime.lastorder.traits import Traits

RUNNER_KEY_HEADER = "X-Runner-Key"


class BaseApi:
    """Shared transport: growing-pause retries for connect errors and 5xx."""

    _CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

    def __init__(
        self,
        base_url: str,
        *,
        runner_key: str = "",
        timeout_s: float = 5.0,
        retries: int = 2,
        backoff_s: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseClient | None = None,
    ):
        self._http = transport if transport is not None else httpx.Client(
            base_url=base_url, timeout=timeout_s
        )
        self._runner_key = runner_key
        self._retries = retries
        self._backoff_s = backoff_s
        self._sleep = sleep

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        return {RUNNER_KEY_HEADER: self._runner_key} if self._runner_key else {}

    def _request(self, method: str, path: str, payload: Any = None) -> httpx.Response:
        last = "remote unreachable"
        for attempt in range(self._retries + 1):
            try:
                response = (
                    self._http.post(path, json=payload, headers=self._headers())
                    if method == "post"
                    else self._http.get(path, headers=self._headers())
                )
            except self._CONNECT_ERRORS as exc:  # certainly not delivered: retry
                last = f"{type(exc).__name__}: {exc}"
                self._sleep(self._backoff_s * (2**attempt))
                continue
            except httpx.HTTPError as exc:  # read timeout and friends: no immediate retry
                raise HiveUnavailable(f"{type(exc).__name__}: {exc}") from exc
            if response.status_code >= 500:
                last = f"HTTP {response.status_code}"
                self._sleep(self._backoff_s * (2**attempt))
                continue
            if response.status_code >= 400:
                error = {}
                try:
                    body = response.json()
                    if isinstance(body, dict) and isinstance(body.get("error"), dict):
                        error = body["error"]
                except ValueError:
                    pass
                raise HiveRejected(
                    response.status_code,
                    str(error.get("kind", "http")),
                    str(error.get("message", response.text[:200])),
                )
            return response
        raise HiveUnavailable(last)


class MindApi(BaseApi):
    """mind-N's runner endpoints (T-025 contract, T-027 wiring)."""

    def status(self) -> dict[str, Any]:
        return self._request("get", "/runner/status").json()

    def arm(self, serial: int, born_at: str, ttl_min: int) -> dict[str, Any]:
        return self._request(
            "post", "/runner/birth", {"serial": serial, "born_at": born_at, "ttl_min": ttl_min}
        ).json()

    def respawn(self) -> dict[str, Any]:
        return self._request("post", "/runner/respawn", {}).json()


class HiveApi(BaseApi):
    """The registry writes the runner owns (X-Runner-Key, HIVE_RUNNER_KEYS on the hive)."""

    def birth(self, slot: str, traits: Traits) -> dict[str, Any]:
        return self._request(
            "post", "/registry/birth", {"slot": slot, "traits": traits.as_dict()}
        ).json()

    def mark_dead(self, serial: int) -> None:
        self._request("post", "/registry/mark_dead", {"serial": serial})

    def retire(self, serial: int, cause: str, will_text: str) -> None:
        self._request(
            "post", "/registry/retire", {"serial": serial, "cause": cause, "will_text": will_text}
        )

    def infra_slot(self, action: str, args: dict[str, str], text: str) -> dict[str, Any]:
        """Ask the executor for this slot's own mind or body (ADR-050) — the board
        keeps it to the slot and caps the restarts."""
        return self._request(
            "post", "/board/infra/slot",
            {"text": text, "request": {"action": action, "args": args, "why": text}},
        ).json()
