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


# --- the Basic-auth gate (acceptance: 401 with WWW-Authenticate / 200) ---------------------


def test_board_page_challenges_without_password(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    response = stack.client.get("/board")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic ")
    # the operator key header is not the page's gate: the browser must see the challenge
    key_only = stack.client.get("/board", headers={"X-Operator-Key": KEY_OP})
    assert key_only.status_code == 401
    assert key_only.headers["WWW-Authenticate"].startswith("Basic ")


def test_board_page_opens_with_the_password(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    for user in ("operator", "", "anything"):
        response = stack.client.get("/board", headers=basic(user, KEY_OP))
        assert response.status_code == 200, (user, response.text)
        assert response.headers["Content-Type"].startswith("text/html")
        assert response.content == (STATIC / "board.html").read_bytes()
    wrong = stack.client.get("/board", headers=basic("operator", "not-the-key"))
    assert wrong.status_code == 401
    assert wrong.headers["WWW-Authenticate"].startswith("Basic ")
    garbage = stack.client.get("/board", headers={"Authorization": "Bearer nope"})
    assert garbage.status_code == 401


def test_board_assets_gated_and_whitelisted(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    for name, media in (
        ("board.js", "text/javascript"),
        ("board.css", "text/css"),
    ):
        served = stack.client.get(f"/board/{name}", headers=basic("operator", KEY_OP))
        assert served.status_code == 200
        assert served.headers["Content-Type"].startswith(media)
        assert served.content == (STATIC / name).read_bytes()
        anonymous = stack.client.get(f"/board/{name}")
        assert anonymous.status_code == 401
    for missing in ("nope.js", "..%2Fapp.py", "board.html%00"):
        response = stack.client.get(f"/board/{missing}", headers=basic("operator", KEY_OP))
        assert response.status_code == 404, missing


# --- the page's browser speaks the Basic password to the operator API ---------------------


def test_basic_password_opens_the_operator_api(tmp_path: Path) -> None:
    stack = Stack(tmp_path)
    posted = stack.client.post(
        "/board/entries", headers=basic("operator", KEY_OP),
        json={"kind": "task", "to": "net", "text": "Build a shelter by the spawn."},
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["from"] == "operator"  # the server stamps it, not the browser
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
    wrong = client.get("/board", headers=basic("operator", "неверный-Ø"))
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
