"""ZCode token usage for the Last Order pipeline, read-only over ZCode's local session database.

    python dev/zcode_usage.py [--since YYYY-MM-DD] [--top N] [--db PATH]

Prints: per day (main chats vs subagents), top sessions, per task (sessions titled "Задача T-NNN"),
and the share of ceremony tool calls in subagents. "Credits" are relative units:
the Coding Plan weights from docs.z.ai (GLM-5.3: input 6.9, cache 1.7, output 24; Flash: 2.3, 0.56, 8)
per 1M tokens, with the weekend 50% discount; weekday off-peak hours are not known locally.
The absolute unit of the plan quota is not known: compare runs, not absolutes.
"""

import argparse
import collections
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

WEIGHTS = {"flash": (2.3, 0.56, 8.0), "default": (6.9, 1.7, 24.0)}
CEREMONY = {
    "git status/log/diff": r"\bgit (status|log|diff|show)\b",
    "poll (sleep, docker logs/ps)": r"\bsleep\b|docker (logs|ps)\b",
    "cat/sed/head/tail (reads)": r"(^|&&|;)\s*(cat|sed -n|head|tail)\s",
    "pytest": r"uv run pytest",
    "check.py": r"tools/check\.py",
}
TASK_RE = re.compile(r"Задача (T-\d{3})")


def credits(model: str, inp: int, cache: int, out: int, ts_ms: int) -> float:
    w_in, w_cache, w_out = WEIGHTS["flash" if "flash" in (model or "").lower() else "default"]
    uncached = max(0, inp - cache)
    c = (uncached * w_in + cache * w_cache + out * w_out) / 1e6
    return c * 0.5 if dt.datetime.fromtimestamp(ts_ms / 1000).weekday() >= 5 else c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=None, help="YYYY-MM-DD, local time")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--db", default=str(Path.home() / ".zcode/cli/db/db.sqlite"))
    a = ap.parse_args()
    since_ms = 0
    if a.since:
        since_ms = int(dt.datetime.strptime(a.since, "%Y-%m-%d").timestamp() * 1000)
    if not Path(a.db).exists():
        print(f"zcode_usage: no database at {a.db}", file=sys.stderr)
        return 2
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    day = lambda ms: dt.datetime.fromtimestamp(ms / 1000).strftime("%m-%d %a")
    sessions = {sid: (title or "", parent) for sid, title, parent in con.execute("select id, title, parent_id from session")}

    rows = con.execute(
        "select session_id, query_source, model_id, started_at, coalesce(input_tokens,0),"
        " coalesce(output_tokens,0), coalesce(cache_read_input_tokens,0) from model_usage where started_at >= ?",
        (since_ms,),
    ).fetchall()
    if not rows:
        print("zcode_usage: no model calls in range")
        return 0

    per_day = collections.defaultdict(lambda: [0, 0, 0, 0, 0.0, 0])  # calls, input, cache, out, credits, max ctx
    per_sess = collections.defaultdict(lambda: [0, 0, 0.0, 0, ""])  # calls, input, credits, max ctx, day
    for sid, src, model, ts, inp, out, cache in rows:
        c = credits(model, inp, cache, out, ts)
        kind = "sub" if src == "subagent" else "main"
        for key in ((day(ts), kind), (day(ts), "all")):
            d = per_day[key]
            d[0] += 1; d[1] += inp; d[2] += cache; d[3] += out; d[4] += c; d[5] = max(d[5], inp)
        s = per_sess[sid]
        s[0] += 1; s[1] += inp; s[2] += c; s[3] = max(s[3], inp); s[4] = s[4] or day(ts)

    print("day        kind  calls  inputM  cache%  outK  credits  avgK  maxK")
    for (d, kind), v in sorted(per_day.items()):
        calls, inp, cache, out, c, mx = v
        print(f"{d}  {kind:<4} {calls:>6} {inp/1e6:7.1f} {100*cache/max(inp,1):6.0f}% {out//1000:5}"
              f" {c:8.1f} {inp//max(calls,1)//1000:5} {mx//1000:5}")

    print(f"\ntop {a.top} sessions by credits:")
    for sid, (calls, inp, c, mx, d) in sorted(per_sess.items(), key=lambda x: -x[1][2])[: a.top]:
        title, parent = sessions.get(sid, ("?", None))
        print(f"  {d}  {c:6.1f} cr  {inp/1e6:6.1f}M  calls={calls:<4} avg={inp//calls//1000}K"
              f" max={mx//1000}K  {'sub ' if parent else 'main'} {title[:50]}")

    per_task = collections.defaultdict(lambda: [0, 0, 0, 0.0, 0])  # sessions, calls, input, credits, max calls
    for sid, (calls, inp, c, mx, d) in per_sess.items():
        m = TASK_RE.search(sessions.get(sid, ("", None))[0])
        if m:
            t = per_task[m.group(1)]
            t[0] += 1; t[1] += calls; t[2] += inp; t[3] += c; t[4] = max(t[4], calls)
    if per_task:
        print("\nper task (worker sessions):")
        for tid, (n, calls, inp, c, mx) in sorted(per_task.items(), key=lambda x: -x[1][3]):
            print(f"  {tid}  {c:6.1f} cr  {inp/1e6:6.1f}M  sessions={n} calls={calls} max_calls/session={mx}")

    subs = {sid for sid, (_, parent) in sessions.items() if parent}
    cer = collections.Counter(); bash_calls = 0; tool_calls = 0; todo = 0
    for sid, data, ts in con.execute("select session_id, data, time_created from part where time_created >= ?", (since_ms,)):
        if sid not in subs:
            continue
        try:
            d = json.loads(data)
        except ValueError:
            continue
        if d.get("type") != "tool":
            continue
        tool_calls += 1
        tool = d.get("tool")
        if tool == "TodoWrite":
            todo += 1
        if tool != "Bash":
            continue
        bash_calls += 1
        cmd = str((d.get("state") or {}).get("input", {}).get("command", ""))
        for name, pat in CEREMONY.items():
            if re.search(pat, cmd):
                cer[name] += 1
    if tool_calls:
        print(f"\nsubagent tool calls: {tool_calls} (Bash {bash_calls}, TodoWrite {todo})")
        for name in CEREMONY:
            print(f"  {name:<30} {cer[name]:>5}  {100*cer[name]/tool_calls:4.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
