#!/usr/bin/env bash
# Append a dated line to an entity note, creating the note if it does not exist.
#
#   remember.sh <entity> <what happened>
#
# The date is added for you, because a memory without one cannot be ordered later and an
# unordered memory is a pile.
set -euo pipefail
[ $# -ge 2 ] || { echo "usage: remember.sh <entity> <what happened>" >&2; exit 2; }
who="$1"; shift
[[ "$who" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "entity must be a plain name (letters, digits, _ -): $who" >&2; exit 2; }
me="${AGENT_NAME:?AGENT_NAME is not set; refusing to guess whose memory to write}"
[[ "$me" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "bad AGENT_NAME: $me" >&2; exit 2; }
[ -f "agent/$me/PERSONA.md" ] || { echo "no character agent/$me (run from the repo root)" >&2; exit 2; }
# A Windows install can put a python3 stub in PATH that exists but cannot run code, so probe
# for a name that actually works instead of trusting the first hit.
py=""
for cand in python3 python; do
    if "$cand" -c "" >/dev/null 2>&1; then py="$cand"; break; fi
done
[ -n "$py" ] || { echo "no working python3 or python in PATH" >&2; exit 3; }
"$py" - "agent/$me/memory/entities/$who.md" "$who" "$*" <<'PY'
import sys, io, os, datetime
path, who, text = sys.argv[1], sys.argv[2], sys.argv[3]
today = datetime.date.today().isoformat()
if not os.path.exists(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="\n").write(
        "# %s\ntype: unknown | met: %s\n\n## Events\n\n## Links\n" % (who, today))
body = io.open(path, encoding="utf-8").read()
mark = "## Events"
if mark not in body:
    body = body.rstrip("\n") + "\n\n## Events\n"
i = body.index(mark) + len(mark)
body = body[:i] + "\n- %s — %s" % (today, text) + body[i:]
io.open(path, "w", encoding="utf-8", newline="\n").write(body)
print("remembered about %s" % who)
PY
