#!/usr/bin/env bash
# Merge an accepted task into main (Claude, last step of lo-review).
#   bash dev/merge_task.sh T-NNN
# Needs: status accepted committed in the task branch, a clean task worktree.
# Brings main into the branch, re-runs the checks there, then merges --no-ff into main under the main lock,
# sets status done and updates BOARD in one commit. Main never gets a merge result that was not tested.
set -uo pipefail
. "$(dirname "$0")/_common.sh"

id="${1:-}"; check_id "$id"
dir="$(task_dir "$id")"
branch="$(field main "$dir" branch)"
title="$(field main "$dir" title)"
wt="$ROOT/.worktrees/$dir"
branch_exists "$branch" || die "no branch $branch"
[ "$(field "$branch" "$dir" status)" = accepted ] || die "$id is not accepted in $branch"
[ -d "$wt" ] || wt="$(bash "$(dirname "$0")/task_worktree.sh" "$id" --attach)" || exit 2
require_root_on_main

for attempt in 1 2 3; do
  # 1. main -> branch, tested in the task worktree.
  if ! git merge-base --is-ancestor main "$branch"; then
    git -C "$wt" diff --quiet && git -C "$wt" diff --cached --quiet || die "uncommitted changes in $wt"
    if ! git -C "$wt" merge -q --no-edit -m "$id: merge main" main; then
      git -C "$wt" merge --abort
      die "conflict between $branch and main: verdict changes, the worker resolves it"
    fi
  fi
  if ! out="$(cd "$wt" && uv run pytest -q 2>&1 && python tools/check.py 2>&1)"; then
    printf '%s\n' "$out" | tail -15 >&2
    die "checks fail in $wt after bringing main in: verdict changes"
  fi

  # 2. branch -> main under the lock; retry if main moved while we tested.
  lock_main
  if git merge-base --is-ancestor main "$branch"; then break; fi
  unlock_main; trap - EXIT
  [ "$attempt" -lt 3 ] || die "main keeps moving, try again later"
done

cd "$ROOT" || die "no $ROOT"
board="$WORK/BOARD.md"; task="$WORK/$dir/task.md"
board_dirty=0; git diff --quiet -- "$board" || board_dirty=1
git merge -q --no-ff "$branch" -m "Merge $id: $title" || { git merge --abort 2>/dev/null; die "merge into main failed: local changes in $ROOT overlap"; }
set_status_file "$task" done
paths=("$task")
promoted="$(promote_queued)"
for d in $promoted; do paths+=("$WORK/$d/task.md"); done
if [ "$board_dirty" = 0 ]; then
  board_set "$board" "$id" done
  for d in $promoted; do board_set "$board" "${d:0:5}" ready; done
  paths+=("$board")
else echo "merge_task: $board has local edits, rows not updated (dev/tasks.sh --sync later)" >&2; fi
msg=(-m "$id: done")
[ -z "$promoted" ] || msg+=(-m "ready: $(printf '%s\n' $promoted | cut -c1-5 | tr '\n' ' ' | sed 's/ $//')")
git commit -q "${msg[@]}" -m "Agent: claude" -- "${paths[@]}" || die "could not commit status done"
unlock_main

echo "merge_task: $id merged into main as $(git rev-parse --short HEAD)"
[ -z "$promoted" ] || echo "merge_task: now ready: $(printf '%s\n' $promoted | cut -c1-5 | tr '\n' ' ')"
echo "merge_task: worktree $wt is left in place; dev/tasks.sh --prune removes it"
