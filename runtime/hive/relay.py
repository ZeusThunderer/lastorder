"""The grant relay (ADR-047 follow-up of T-046): Last Order's one way out.

Last Order (and the tools she builds in her workshop) call an HTTPS API through
hive: `{{secret:NAME}}` placeholders in the url, header values or body are filled
from the grant vault here, the request leaves through the egress proxy, and only
to a domain with an active egress grant. The secret value never reaches the
sandbox: it is not in the answer (redacted back to its placeholder), not in the
log, not in any error text.

What the agent calls is mechanics, not behaviour: which API, what to post and
when is hers (ADR-032, ADR-041).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import httpx

from runtime.hive.grants import GrantError, Grants
from runtime.hive.lifecycle import HiveLog

SECRET_RE = re.compile(r"\{\{secret:([a-z][a-z0-9_]{0,63})\}\}")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
BODY_MAX = 32_000  # chars of the answer handed back to the agent
TIMEOUT_S = 20.0

# The file mode (ADR-052): responses saved to and uploads read from one shared
# volume both the hive and the Last Order container mount. Files are the agent's
# data, never secrets — the vault value still only ever lives inside `fill()`.
ARTIFACTS_ROOT = Path(os.environ.get("RELAY_ARTIFACTS_DIR") or "/artifacts")
SAVE_MAX_BYTES = 25_000_000
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


def _placeholders(*texts: str) -> list[str]:
    seen: list[str] = []
    for text in texts:
        for name in SECRET_RE.findall(text or ""):
            if name not in seen:
                seen.append(name)
    return seen


class Relay:
    """Fill secrets, check the egress grant, send through the proxy, redact."""

    def __init__(
        self,
        grants: Grants,
        *,
        log: HiveLog,
        proxy: str | None = None,
        transport: Callable[[], httpx.Client] | None = None,
        timeout_s: float = TIMEOUT_S,
        artifacts_root: Path | None = None,
    ):
        self._grants = grants
        self._log = log
        self._proxy = proxy or None
        self._transport = transport
        self._timeout = timeout_s
        self._artifacts = artifacts_root or ARTIFACTS_ROOT

    def _client(self) -> httpx.Client:
        if self._transport is not None:
            return self._transport()
        return httpx.Client(proxy=self._proxy, timeout=self._timeout, follow_redirects=False)

    def _artifact_path(self, name: Any) -> Path:
        """Validate an artifacts filename: a bare name under the shared root, no
        directories, no traversal — the agent names files, the relay owns the tree."""
        if not isinstance(name, str) or not FILE_NAME_RE.fullmatch(name):
            raise GrantError(
                422, "validation",
                "file name must be 1..100 of A-Za-z0-9._- (a bare name in /artifacts)",
            )
        path = (self._artifacts / name).resolve()
        if not path.is_relative_to(Path(self._artifacts).resolve()):
            raise GrantError(422, "validation", "path escapes the artifacts volume")
        return path

    def fetch(
        self,
        actor: str,
        method: Any,
        url: Any,
        headers: Any = None,
        body: Any = None,
        *,
        save_to: Any = None,
        upload_file: Any = None,
        upload_part: Any = None,
        fields: Any = None,
    ) -> dict[str, Any]:
        if not isinstance(method, str) or method.upper() not in METHODS:
            raise GrantError(422, "validation", f"method must be one of {list(METHODS)}")
        method = method.upper()
        if not isinstance(url, str) or not url.strip():
            raise GrantError(422, "validation", "url must be an https url")
        headers = headers or {}
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise GrantError(422, "validation", "headers must be an object of strings")
        content: str | None
        if body is None or body == "":
            content = None
        elif isinstance(body, str):
            content = body
        else:  # an object or a list rides as JSON
            content = json.dumps(body, ensure_ascii=False)
            if not any(k.lower() == "content-type" for k in headers):
                headers = {**headers, "Content-Type": "application/json"}

        # the file mode (ADR-052): exactly one of save/upload/plain per call
        save_path = self._artifact_path(save_to) if save_to is not None else None
        if upload_file is not None:
            if content is not None:
                raise GrantError(422, "validation", "body and upload_file are mutually exclusive")
            upload_path = self._artifact_path(upload_file)
            if not upload_path.is_file():
                raise GrantError(422, "validation", f"no such artifacts file: {upload_file}")
            if not isinstance(method, str) or method.upper() != "POST":
                raise GrantError(422, "validation", "upload_file needs method POST")
            if fields is not None and (
                not isinstance(fields, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items())
            ):
                raise GrantError(422, "validation", "fields must be an object of strings")
            part = upload_part if isinstance(upload_part, str) and upload_part else "photo"
        else:
            upload_path = None

        names = _placeholders(url, content or "", *headers.values())
        values = {name: self._grants.secret_of(name) for name in names}

        def fill(text: str) -> str:
            return SECRET_RE.sub(lambda m: values[m.group(1)], text)

        def redact(text: str) -> str:
            for name, value in values.items():
                if value:
                    text = text.replace(value, "{{secret:%s}}" % name)
            return text

        real_url = fill(url.strip())
        try:
            parsed = httpx.URL(real_url)
        except Exception as exc:  # noqa: BLE001 - any parse failure is a bad url
            raise GrantError(422, "validation", redact(f"bad url: {exc}")) from None
        host = (parsed.host or "").lower()
        if parsed.scheme != "https" or not host:
            raise GrantError(422, "validation", "url must be an https url")
        if host not in self._grants.egress_domains():
            raise GrantError(
                403, "no_egress_grant",
                f"{host} has no active egress grant (request_grant kind=egress)",
            )
        real_headers = {k: fill(v) for k, v in headers.items()}
        real_fields = {k: fill(v) for k, v in (fields or {}).items()}
        real_body = fill(content) if content is not None else None
        try:
            with self._client() as client:
                if upload_path is not None:
                    response = client.request(
                        method, real_url, headers=real_headers,
                        files={part: (upload_path.name, upload_path.read_bytes(), None)},
                        data=real_fields,
                    )
                else:
                    response = client.request(
                        method, real_url, headers=real_headers,
                        content=real_body.encode("utf-8") if real_body is not None else None,
                    )
        except httpx.HTTPError as exc:
            self._log.event("relay.fetch", {
                "actor": actor, "method": method, "host": host,
                "error": type(exc).__name__, "secrets": names,
            })
            raise GrantError(502, "relay_failed", redact(f"{type(exc).__name__}: {exc}")) from None
        if save_path is not None:
            payload = response.content
            if len(payload) > SAVE_MAX_BYTES:
                raise GrantError(
                    507, "too_large",
                    f"response is {len(payload)} bytes, save_to caps at {SAVE_MAX_BYTES}",
                )
            try:
                self._artifacts.mkdir(parents=True, exist_ok=True)
                save_path.write_bytes(payload)
            except OSError as exc:
                raise GrantError(
                    502, "relay_failed", redact(f"save failed: {type(exc).__name__}: {exc}")
                ) from None
            self._log.event("relay.fetch", {
                "actor": actor, "method": method, "host": host,
                "status": response.status_code, "secrets": names, "saved": len(payload),
            })
            return {
                "status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "path": str(save_path),
                "size": len(payload),
            }
        text = redact(response.text)
        truncated = len(text) > BODY_MAX
        self._log.event("relay.fetch", {
            "actor": actor, "method": method, "host": host,
            "status": response.status_code, "secrets": names,
            **({"uploaded": part} if upload_path is not None else {}),
        })
        return {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "body": text[:BODY_MAX],
            **({"truncated": True} if truncated else {}),
        }


__all__ = ["BODY_MAX", "METHODS", "Relay", "SECRET_RE"]
