#!/usr/bin/env bash
# Run a command in the main worktree on main under the main lock (Claude: committing new tasks, BOARD, DIRECT_EDITS).
#   bash dev/on_main.sh git commit -m "docs: T-020 task — ..." -- docs/agents/work/T-020-x docs/agents/work/BOARD.md
set -uo pipefail
. "$(dirname "$0")/_common.sh"

[ "$#" -gt 0 ] || die "usage: dev/on_main.sh <command...>"
require_root_on_main
lock_main
cd "$ROOT" || die "no $ROOT"
"$@"
