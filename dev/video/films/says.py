"""Unique chat lines of the film shoot (T-067 films) -> dev/video/out/logs2/says.txt (UTC, from, text)."""
import datetime
import json
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "out/logs2"
T0 = datetime.datetime(2026, 11, 8, 21, 0, tzinfo=datetime.timezone.utc).timestamp()
rows, seen = [], set()
for f in sorted(D.glob("mind-*.jsonl")):
    for line in f.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("ts", 0) < T0 or d.get("event") not in ("chat.line", "game.death", "birth.armed", "life.expiring"):
            continue
        t = datetime.datetime.fromtimestamp(d["ts"], datetime.timezone.utc).strftime("%H:%M:%S")
        if d.get("event") != "chat.line":
            rows.append((t, f.stem, d["event"], json.dumps({k: v for k, v in d.items() if k not in ("ts", "kind", "event")}, ensure_ascii=False)))
            continue
        for x in d.get("lines", []):
            k = (x.get("from"), x.get("text"))
            if k in seen or (not x.get("from") and x.get("text", "").startswith("/")):
                continue
            seen.add(k)
            rows.append((t, f.stem, x.get("from", "SYS"), x.get("text", "")))
rows.sort()
(D / "says.txt").write_text("\n".join(" | ".join(r) for r in rows), encoding="utf-8")
print(len(rows), "rows")
