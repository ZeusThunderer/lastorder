"""What a sister's request to the model is made of (T-056).

Reads Pi session files (JSONL, one `usage` per assistant message) and splits every
request into parts: the system prompt by section, the tool schemas, and the history
(wake messages, the sister's own text, thinking and tool calls, tool output by tool).
The real size of a request is `input + cacheRead + cacheWrite` from `usage`; the
parts are char counts scaled to it, so their tokens are an estimate [assumption:
one token per the same number of chars across parts].

    python dev/context_report.py <session.jsonl | dir> [...]   # markdown to stdout
    python dev/context_report.py --json <...>                  # the same as JSON

Wakes: user messages (board lines, sister messages, the runner) and `wait_events`
answers by type; a `timeout` answer is an empty wake.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

WAIT_TOOL = "mcp__body__wait_events"


def _chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _measured(usage: dict[str, Any] | None) -> int:
    if not usage:
        return 0
    return int(usage.get("input", 0)) + int(usage.get("cacheRead", 0)) + int(usage.get("cacheWrite", 0))


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict))


def _wake_kind(text: str) -> str:
    head = text.lstrip()[:40]
    if head.startswith("[board"):
        return "board"
    if head.startswith("[net"):
        return "sister message"
    if head.startswith("["):
        return head[1 : head.find("]") if "]" in head else 12].split(" ")[0] or "other"
    return "runner/other"


def _parts_of(message: dict[str, Any]) -> list[tuple[str, int]]:
    """History parts one message adds to every later request."""
    role = message.get("role")
    content = message.get("content")
    if role == "user":
        return [("wake: user message", _chars(content))]
    if role == "toolResult":
        return [(f"tool output: {message.get('toolName', '?')}", _chars(content))]
    if role != "assistant" or not isinstance(content, list):
        return []
    out: list[tuple[str, int]] = []
    for block in content:
        kind = block.get("type")
        if kind == "thinking":
            out.append(("sister: thinking", _chars(block.get("thinking", ""))))
        elif kind == "text":
            out.append(("sister: text", _chars(block.get("text", ""))))
        elif kind == "toolCall":
            out.append(("sister: tool calls", _chars(block.get("arguments", {})) + len(block.get("name", ""))))
    return out


def analyse(path: Path) -> dict[str, Any]:
    """One session: per-request parts (chars) and measured tokens, plus wakes."""
    static: dict[str, int] = {}
    history: dict[str, list[tuple[str, int]]] = {}  # entry id -> parts, in order
    requests: list[dict[str, Any]] = []
    wakes: Counter[str] = Counter()
    waits: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        kind = entry.get("type")
        if kind == "context_edit":  # Pi drops (or replaces) one message from the context
            target = entry.get("targetId")
            if target in history:
                repl = entry.get("replacement")
                history[target] = _parts_of(repl) if isinstance(repl, dict) else []
            continue
        if kind != "message":
            continue
        message = entry.get("message") or {}
        role = message.get("role")
        if role == "system":
            for name, text in (message.get("sections") or {}).items():
                static[f"system: {name}"] = _chars(text)
            if message.get("toolsAdded"):
                static["tool schemas"] = _chars(message["toolsAdded"])
            continue
        if role == "assistant":
            measured = _measured(message.get("usage"))
            if measured:
                parts: Counter[str] = Counter(static)
                for items in history.values():
                    for name, n in items:
                        parts[name] += n
                requests.append({"ts": entry.get("timestamp"), "tokens": measured, "parts": dict(parts)})
        if role == "user":
            wakes[_wake_kind(_text(message.get("content")))] += 1
        if role == "toolResult" and message.get("toolName") == WAIT_TOOL:
            text = _text(message.get("content"))
            try:
                waits[str(json.loads(text).get("type", "?"))] += 1
            except (ValueError, AttributeError):
                waits["error: " + text[:40]] += 1
        history[entry.get("id", str(len(history)))] = _parts_of(message)
    return {"file": str(path), "requests": requests, "wakes": dict(wakes), "waits": dict(waits)}


def scaled(request: dict[str, Any]) -> dict[str, int]:
    """Parts of one request in tokens: chars scaled to the measured size."""
    total = sum(request["parts"].values()) or 1
    return {k: round(v * request["tokens"] / total) for k, v in request["parts"].items()}


def _files(args: list[str]) -> list[Path]:
    out: list[Path] = []
    for a in args:
        p = Path(a)
        out.extend(sorted(p.rglob("*.jsonl")) if p.is_dir() else [p])
    return out


def report(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    reqs = [r for s in sessions for r in s["requests"]]
    if not reqs:
        return {"sessions": len(sessions), "requests": 0}
    by_size = sorted(reqs, key=lambda r: r["tokens"])
    median_req = by_size[len(by_size) // 2]
    max_req = by_size[-1]
    sum_parts: Counter[str] = Counter()  # tokens over all requests: what the network pays for
    for r in reqs:
        sum_parts.update(scaled(r))
    wakes: Counter[str] = Counter()
    waits: Counter[str] = Counter()
    for s in sessions:
        wakes.update(s["wakes"])
        waits.update(s["waits"])
    return {
        "sessions": len(sessions),
        "requests": len(reqs),
        "tokens_total": sum(r["tokens"] for r in reqs),
        "tokens_median": int(statistics.median(r["tokens"] for r in reqs)),
        "tokens_max": max_req["tokens"],
        "median_request": {"tokens": median_req["tokens"], "parts": scaled(median_req)},
        "max_request": {"tokens": max_req["tokens"], "parts": scaled(max_req)},
        "all_requests": dict(sum_parts),
        "wakes": dict(wakes),
        "wait_events": dict(waits),
        "per_session": [
            {
                "file": Path(s["file"]).name,
                "requests": len(s["requests"]),
                "first": s["requests"][0]["tokens"] if s["requests"] else 0,
                "max": max((r["tokens"] for r in s["requests"]), default=0),
            }
            for s in sessions
        ],
    }


def _table(parts: dict[str, int], total: int, top: int = 14) -> list[str]:
    rows = ["| часть | токены | доля |", "|---|---:|---:|"]
    items = sorted(parts.items(), key=lambda kv: -kv[1])
    for name, n in items[:top]:
        rows.append(f"| {name} | {n} | {100 * n / max(total, 1):.0f} % |")
    rest = sum(n for _, n in items[top:])
    if rest:
        rows.append(f"| остальное ({len(items) - top}) | {rest} | {100 * rest / max(total, 1):.0f} % |")
    return rows


def markdown(rep: dict[str, Any]) -> str:
    if not rep.get("requests"):
        return f"Сессий: {rep['sessions']}, запросов нет."
    out = [
        f"Сессий: {rep['sessions']}, запросов: {rep['requests']}, токенов входа всего: {rep['tokens_total']}.",
        f"Запрос: медиана {rep['tokens_median']}, максимум {rep['tokens_max']} токенов.",
        "",
        f"### Медианный запрос ({rep['median_request']['tokens']} токенов)",
        *_table(rep["median_request"]["parts"], rep["median_request"]["tokens"]),
        "",
        f"### Максимальный запрос ({rep['max_request']['tokens']} токенов)",
        *_table(rep["max_request"]["parts"], rep["max_request"]["tokens"]),
        "",
        f"### Все запросы вместе ({rep['tokens_total']} токенов)",
        *_table(rep["all_requests"], rep["tokens_total"]),
        "",
        "### Пробуждения",
        "Сообщения пользователя: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["wakes"].items())),
        "Ответы wait_events: " + ", ".join(f"{k} {v}" for k, v in sorted(rep["wait_events"].items())),
        "",
        "### Сессии",
        "| файл | запросов | первый | максимум |",
        "|---|---:|---:|---:|",
        *(f"| {s['file']} | {s['requests']} | {s['first']} | {s['max']} |" for s in rep["per_session"]),
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="Pi session .jsonl files or directories with them")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ns = ap.parse_args(argv)
    sessions = [analyse(p) for p in _files(ns.paths)]
    rep = report(sessions)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(json.dumps(rep, ensure_ascii=False, indent=1) if ns.json else markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
