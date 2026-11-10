"""The workshop (T-047): the checked active dir, the tool shelf, tool proposals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.hive.proposals import ProposalBook, ProposalError
from runtime.hive.tools import ToolError, ToolShelf, check_files, read_dir
from runtime.knowledge.store import KnowledgeStore
from runtime.node import workshop
from runtime.node.workplace import render_pi_configs

GOOD = "export default function (pi: any) { pi.registerTool({ name: 'x' }); }\n"


def _tool(root: Path, name: str, text: str = GOOD) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "index.ts").write_text(text, encoding="utf-8")
    return d


def fake_check(entry: Path) -> str | None:
    text = entry.read_text(encoding="utf-8")
    return "Failed to load extension: ParseError" if "(((" in text else None


# --- the active dir ------------------------------------------------------------------------


def test_render_keeps_only_loading_extensions(tmp_path):
    agent, own, net = tmp_path / "agent", tmp_path / "own", tmp_path / "net"
    agent.mkdir()
    _tool(own, "counter")
    _tool(own, "broken", "export default (((")
    _tool(net, "counter")  # shadowed by her own
    _tool(net, "telegram")
    (own / "notes").mkdir()  # no entry point
    rows = workshop.render_workshop(agent, own=own, net=net, check=fake_check)
    active = agent / workshop.ACTIVE_DIR
    assert sorted(p.name for p in active.iterdir()) == ["net-telegram", "own-counter"]
    by = {(r["from"], r["tool"]): r for r in rows}
    assert by[("own", "broken")]["ok"] is False and "ParseError" in by[("own", "broken")]["error"]
    assert by[("net", "counter")]["ok"] is False and "shadowed" in by[("net", "counter")]["error"]
    assert by[("own", "notes")]["ok"] is False
    config = json.loads((agent / workshop.CONFIG_FILE).read_text(encoding="utf-8"))
    assert config["active"] == str(active)
    assert [s["prefix"] for s in config["sources"]] == ["own-", "net-"]
    # a second render replaces the dir (a fixed tool comes in, nothing stale stays)
    (own / "broken" / "index.ts").write_text(GOOD, encoding="utf-8")
    workshop.render_workshop(agent, own=own, net=net, check=fake_check)
    assert "own-broken" in {p.name for p in active.iterdir()}


def test_check_is_cached_by_content(tmp_path):
    calls: list[Path] = []

    def counting(entry: Path) -> str | None:
        calls.append(entry)
        return None

    check = workshop.cached(counting)
    d = _tool(tmp_path, "t")
    check(d / "index.ts")
    check(d / "index.ts")
    assert len(calls) == 1
    (d / "index.ts").write_text(GOOD + "// edit\n", encoding="utf-8")
    check(d / "index.ts")
    assert len(calls) == 2


def test_net_tools_cannot_escape(tmp_path):
    agent = tmp_path / "agent"
    agent.mkdir()
    workshop.write_net_tools(agent, {
        "ok": {"index.ts": GOOD},
        "bad": {"../../evil.ts": "x"},
        "../up": {"index.ts": "x"},
    })
    assert (agent / workshop.NET_DIR / "ok" / "index.ts").exists()
    assert not (tmp_path / "evil.ts").exists()
    assert sorted(p.name for p in (agent / workshop.NET_DIR).iterdir()) == ["ok"]


def test_settings_list_only_the_active_dir():
    configs = render_pi_configs("GLM-5.3", "http://body", "http://hive:8000", "k",
                                workshop_active="/home/sister/.pi/agent/workshop-active")
    assert configs["settings.json"]["extensions"] == ["/home/sister/.pi/agent/workshop-active"]
    assert "extensions" not in render_pi_configs("GLM-5.3", "http://b", "http://h", "k")["settings.json"]


# --- the shelf -----------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(tmp_path / "kb", git=False)


def test_publish_and_bundle(store, tmp_path):
    shelf = ToolShelf(store)
    src = tmp_path / "workshop" / "death-counter"
    src.mkdir(parents=True)
    (src / "index.ts").write_text(GOOD, encoding="utf-8")
    (src / "lib.ts").write_text("export const x = 1;\n", encoding="utf-8")
    (src / ".secret").write_text("no", encoding="utf-8")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "dep.js").write_text("no", encoding="utf-8")
    shelf.publish("death-counter", read_dir(src), author="last-order", by="last-order", why="count deaths")
    bundle = shelf.bundle()
    assert sorted(bundle["death-counter"]) == ["TOOL.md", "index.ts", "lib.ts"]
    assert "count deaths" in bundle["death-counter"]["TOOL.md"]
    # republish without lib.ts: the old file is emptied, the bundle drops it
    (src / "lib.ts").unlink()
    shelf.publish("death-counter", read_dir(src), author="last-order", by="last-order", why="v2")
    assert sorted(shelf.bundle()["death-counter"]) == ["TOOL.md", "index.ts"]


@pytest.mark.parametrize("files", [
    {},
    {"lib.ts": "x"},  # no entry point
    {"../index.ts": GOOD},
    {"/abs/index.ts": GOOD},
    {"index.ts": GOOD, "run.sh": "rm -rf /"},
    {"index.ts": "x" * 70_000},
])
def test_check_files_refuses(files):
    with pytest.raises(ToolError):
        check_files(files)


def test_publish_refuses_bad_name(store):
    with pytest.raises(ToolError):
        ToolShelf(store).publish("Bad Name", {"index.ts": GOOD}, author="a", by="b", why="c")


# --- a sister's tool through the proposal queue ---------------------------------------------


def test_tool_proposal_accept_publishes(store):
    book = ProposalBook(store)
    p = book.submit(7, "tool", {"name": "torch-map", "files": {"index.ts": GOOD}, "description": "where torches are"})
    assert book.shelf.bundle() == {}
    book.decide(p.id, accept=True, why="useful for the night", by="last_order")
    tool = book.shelf.bundle()["torch-map"]
    assert tool["index.ts"] == GOOD
    assert "00007" in tool["TOOL.md"]


def test_tool_proposal_reject_and_validation(store):
    book = ProposalBook(store)
    p = book.submit(7, "tool", {"name": "x-tool", "files": {"index.ts": GOOD}})
    book.decide(p.id, accept=False, why="duplicate", by="last_order")
    assert book.shelf.bundle() == {}
    with pytest.raises(ProposalError):
        book.submit(7, "tool", {"name": "x-tool", "files": {"evil.sh": "x"}})
