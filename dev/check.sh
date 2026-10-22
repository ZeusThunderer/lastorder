#!/usr/bin/env bash
# Project checks in one agent call with short output (every agent call re-sends its whole context).
# Run from the repo root or a task worktree; the root copy works for any worktree.
#   bash <root>/dev/check.sh                    # uv run pytest -q + uv run python tools/check.py
#   bash <root>/dev/check.sh tests/test_x.py …  # only these tests, without check.py
# One line per check; on failure, the last 40 lines of its output. Exit 1 if anything failed.
set -uo pipefail

[ -f CLAUDE.md ] && [ -d tools ] || { echo "check: run from the repo root or a task worktree" >&2; exit 2; }

# Local test servers (hive MCP) must not go through a desktop HTTP proxy, or httpx hangs on them.
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost" no_proxy="${no_proxy:+$no_proxy,}127.0.0.1,localhost"

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
rc=0

run() {  # run <name> <command…>
  local name="$1"; shift
  if "$@" >"$tmp" 2>&1; then
    printf '%s: ok · %s\n' "$name" "$(grep -v '^[[:space:]]*$' "$tmp" | tail -n 1)"
  else
    rc=1
    printf '%s: FAIL\n' "$name"
    tail -n 40 "$tmp"
  fi
}

if [ "$#" -gt 0 ]; then
  run pytest uv run pytest -q "$@"
else
  run pytest uv run pytest -q
  run check.py uv run python tools/check.py   # bare `python` is the Windows Store alias here
fi
exit "$rc"
