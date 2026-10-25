#!/usr/bin/env bash
# Take a task into its own worktree (ZCode) or attach to it (Claude, for review).
#   bash dev/task_worktree.sh T-NNN            # ready|changes -> in_progress, committed at once; in_progress -> resume
#   bash dev/task_worktree.sh T-NNN --attach   # open the worktree without touching the status
# Prints the worktree path. Run from anywhere inside the repo (Git Bash on Windows).
set -uo pipefail
. "$(dirname "$0")/_common.sh"

id="${1:-}"; mode="${2:-take}"
check_id "$id"
[[ "$mode" =~ ^(take|--attach)$ ]] || die "usage: dev/task_worktree.sh T-NNN [--attach]"

dir="$(task_dir "$id")"
branch="$(field main "$dir" branch)"
[ -n "$branch" ] || die "$id has no branch in task.md"
status="$(task_status "$dir")"
wt="$ROOT/.worktrees/$dir"

if [ "$mode" = take ]; then
  case "$status" in
    ready|changes|in_progress) ;;
    *) die "$id has status '$status', nothing to take" ;;
  esac
  open="$(deps_open "$dir")"
  [ "$status" != ready ] || [ -z "$open" ] || die "$id waits for ${open% } (after:)"
fi

if [ ! -d "$wt" ]; then
  if branch_exists "$branch"; then
    git -C "$ROOT" worktree add -q "$wt" "$branch" \
      || die "$branch is checked out elsewhere (git worktree list): someone already works on $id"
  else
    [ "$mode" = take ] && [ "$status" = ready ] || die "$id has no branch yet and status '$status'"
    git -C "$ROOT" worktree add -q -b "$branch" "$wt" main \
      || die "could not create $branch: someone may have taken $id a moment ago"
  fi
fi

if [ "$mode" = take ] && [ "$status" != in_progress ]; then
  cd "$wt" || die "no $wt"
  git diff --quiet && git diff --cached --quiet || die "uncommitted changes in $wt"
  set_status_file "$WORK/$dir/task.md" in_progress
  git commit -q -m "$id: take ($status -> in_progress)" -m "Agent: zcode" -- "$WORK/$dir/task.md" \
    || die "could not commit the status"
fi

printf '%s\n' "$wt"
