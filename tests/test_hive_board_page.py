"""The operator's board page (T-049, TASKBOARD.md section 12): the Basic-auth gate,
the static whitelist and the page's own safety rules — no external URLs, agent text
through textContent alone (the grep level; the visible proof is the stand screenshot).
The API under the page is tests/test_hive_board.py.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from runtime.hive.app import create_app
from runtime.hive.lifecycle import HiveLog
from runtime.knowledge.store import KnowledgeStore

KEY_OP = "op-stand-key"
STATIC = Path(__file__).resolve().parents[1] / "runtime" / "hive" / "static"


def basic(user: str, password: str) -> dict:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


class FakeRcon:
    def kill(self, player: str) -> str:
        return "Killed LastOrder01"


class Stack:
    def __init__(self, tmp_path: Path):
        clock = lambda: datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731
        self.app = create_app(
            store=KnowledgeStore(tmp_path / "kb", git=False, now=lambda: clock().isoformat()),
            rcon=FakeRcon(),
            log=HiveLog(tmp_path / "run" / "events.jsonl"),
            run_dir=tmp_path / "run",
            clock=clock,
            node_keys={"sk-node-one": "LastOrder01"},
            lo_key="sk-last-order",
            operator_key=KEY_OP,
            runner_keys={},
        )
        self.client = TestClient(self.app)


# --- the page and its assets are open; the data behind them is not (T-052) ----------------


def test_board_page_opens_without_a_dialog(tmp_path: Path) -> None:
    """The shell carries no data — every number comes from the authed routes — so it
    loads anonymously and shows the login form instead of the browser's Basic box."""
    stack = Stack(tmp_path)
    response = stack.client.get("/board")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert response.content == (STATIC / "board.html").read_bytes()
    # the data stays closed: the API answers 401 without the key/cookie
    assert stack.client.get("/board/entries").status_code == 401


def test_board_assets_open_and_whitelisted(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    for name, media in (
        ("board.js", "text/javascript"),
        ("board.css", "text/css"),
    ):
        served = stack.client.get(f"/board/{name}")
        assert served.status_code == 200
        assert served.headers["Content-Type"].startswith(media)
        assert served.content == (STATIC / name).read_bytes()
    for missing in ("nope.js", "..%2Fapp.py", "board.html%00"):
        response = stack.client.get(f"/board/{missing}")
        assert response.status_code == 404, missing


def test_basic_password_still_opens_the_page(tmp_path: Path) -> None:
    """Basic-auth keeps working for browsers that cached the pair and for scripts."""
    stack = Stack(tmp_path)
    for user in ("operator", "", "anything"):
        response = stack.client.get("/board", headers=basic(user, KEY_OP))
        assert response.status_code == 200, (user, response.text)
    wrong = stack.client.get("/board/entries", headers=basic("operator", "not-the-key"))
    assert wrong.status_code == 401


# --- the page's login form (T-052) ---------------------------------------------------------


def test_login_sets_the_cookie_that_opens_the_api(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    wrong = stack.client.post("/board/login", json={"password": "not-the-key"})
    assert wrong.status_code == 401
    ok = stack.client.post("/board/login", json={"password": KEY_OP})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    cookie = ok.headers["Set-Cookie"]
    assert "lo_board=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert KEY_OP not in cookie  # the cookie carries the hash, never the password
    # the cookie alone (no headers) opens the operator API and the SSE source
    assert stack.client.get("/board/entries", cookies={"lo_board": cookie.split("lo_board=")[1].split(";")[0]}).status_code == 200
    # a wrong cookie value is still a calm 401
    assert stack.client.get("/board/entries", cookies={"lo_board": "f" * 64}).status_code == 401


def test_login_overlay_hides_after_a_successful_login(tmp_path: Path) -> None:
    """The overlay is display:flex, which outranks the UA's [hidden] rule; the
    class must carry its own [hidden] guard or the form stays over the board
    after login (operator report 2026-10-31). No JS runner here — the guard is
    asserted as a static regression on the stylesheet."""
    css = (STATIC / "board.css").read_text(encoding="utf-8")
    assert ".login[hidden]" in css and "display: none" in css


def test_logout_clears_the_cookie(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    stack.client.post("/board/login", json={"password": KEY_OP})
    out = stack.client.post("/board/logout")
    assert out.status_code == 200
    assert 'lo_board=""' in out.headers["Set-Cookie"]


# --- the page's browser speaks the Basic password to the operator API ---------------------


def test_basic_password_opens_the_operator_api(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    posted = stack.client.post(
        "/board/entries", headers=basic("operator", KEY_OP),
        json={"kind": "task", "to": "net", "text": "Build a shelter by the spawn."},
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["from"] == "operator"  # the server stamps it, not the browser
    # the page's thread loads: the one-entry route under the same Basic pair (R2-1)
    one = stack.client.get(f"/board/entries/{posted.json()['id']}",
                           headers=basic("operator", KEY_OP))
    assert one.status_code == 200, one.text
    assert one.json()["thread"] == []
    listed = stack.client.get("/board/entries", headers=basic("operator", KEY_OP))
    assert listed.status_code == 200
    # an explicit but wrong X-Operator-Key is a refusal even over a valid Basic pair:
    # a header sent is a header checked
    refused = stack.client.get(
        "/board/entries",
        headers={**basic("operator", KEY_OP), "X-Operator-Key": "wrong"},
    )
    assert refused.status_code == 401
    # the original header path keeps working (scripts, curl)
    legacy = stack.client.get("/board/entries", headers={"X-Operator-Key": KEY_OP})
    assert legacy.status_code == 200


# --- the page's own safety rules (acceptance: grep level) ---------------------------------


def test_non_ascii_key_is_a_calm_401(tmp_path: Path) -> None:
    """compare_digest refuses non-ASCII str (review R1-3): a stray symbol in a stand
    password must still answer 401, never a 500."""
    unicode_key = "пароль-стенда-Ø"
    clock = lambda: datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731
    home = tmp_path / "unicode"
    app = create_app(
        store=KnowledgeStore(home / "kb", git=False, now=lambda: clock().isoformat()),
        rcon=FakeRcon(),
        log=HiveLog(home / "run" / "events.jsonl"),
        run_dir=home / "run",
        clock=clock,
        node_keys={"sk-node-one": "LastOrder01"},
        lo_key="sk-last-order",
        operator_key=unicode_key,
        runner_keys={},
    )
    client = TestClient(app)
    wrong = client.get("/board/entries", headers=basic("operator", "неверный-Ø"))
    assert wrong.status_code == 401  # not 500: the compare is over bytes
    right = client.get("/board", headers=basic("operator", unicode_key))
    assert right.status_code == 200
    # raw header values arrive as bytes on the wire; the server side still compares
    # over utf-8 bytes, so a non-ASCII attempt is a 401, not a TypeError/500
    wrong_header = client.get(
        "/board/entries", headers={"X-Operator-Key": "ключ-Ø-не-тот".encode("utf-8")}
    )
    assert wrong_header.status_code == 401


def test_static_references_nothing_external() -> None:
    for path in sorted(STATIC.iterdir()):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"https?://", text) is None, path
        assert "http" not in text.lower(), f"{path} mentions the network protocol"
        assert re.search(r"""(src|href)\s*=\s*["']//""", text) is None, path  # protocol-relative


def test_no_markup_sinks_in_the_script() -> None:
    script = (STATIC / "board.js").read_text(encoding="utf-8")
    for sink in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write"):
        assert sink not in script, sink  # agent text flows through textContent alone
    assert "textContent" in script
