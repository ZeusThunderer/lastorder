#!/usr/bin/env bash
# Real task statuses across branches and worktrees (the branch copy of task.md wins while the branch exists).
#   bash dev/tasks.sh                  # table: id, status, BOARD status, branch, worktree, open `after:` tasks
#   bash dev/tasks.sh --next [T-NNN …] # ZCode: next task to work on, "T-NNN <status>", skipping the given ids;
#                                      # order: in_progress, review, blocked with a question, changes, ready
#                                      # tasks with `owner: supervisor` are skipped
#   bash dev/tasks.sh --sync           # Claude: write real statuses into BOARD in main (under the main lock)
#   bash dev/tasks.sh --prune          # Claude: remove worktrees and branches of done tasks merged into main
set -uo pipefail
. "$(dirname "$0")/_common.sh"

mode="${1:-list}"
[[ "$mode" =~ ^(list|--next|--sync|--prune)$ ]] || die "usage: dev/tasks.sh [--next [T-NNN …]|--sync|--prune]"

board_status() { git show "main:$WORK/BOARD.md" | tr -d '\r' | awk -F'|' -v id="$1" '{gsub(/ /,"",$2)} $2==id {gsub(/^ +| +$/,"",$5); print $5}'; }

dirs="$(git ls-tree -d --name-only main "$WORK/" | sed 's#.*/##' | grep '^T-[0-9]\{3\}-' || true)"

if [ "$mode" = list ]; then
  printf '%-6s %-12s %-12s %-40s %-40s %s\n' id status board branch worktree after
  for d in $dirs; do
    id="${d:0:5}"; b="$(field main "$d" branch)"; wt="-"; st="$(task_status "$d")"; after=""
    [ -d "$ROOT/.worktrees/$d" ] && wt=".worktrees/$d"
    branch_exists "$b" || b="($b)"
    [ "$st" = ready ] && after="$(deps_open "$d")"
    printf '%-6s %-12s %-12s %-40s %-40s %s\n' "$id" "$st" "$(board_status "$id")" "$b" "$wt" "$after"
  done
  exit 0
fi

if [ "$mode" = --next ]; then
  shift; skip=" $* "
  for want in in_progress review blocked changes ready; do
    for d in $dirs; do
      id="${d:0:5}"
      case "$skip" in *" $id "*) continue ;; esac
      # Tasks the supervisor does itself (owner: supervisor) are not ZCode's.
      [ "$(field main "$d" owner)" = supervisor ] && continue
      [ "$(task_status "$d")" = "$want" ] || continue
      if [ "$want" = blocked ]; then
        # Only a worker's question goes to the supervisor; "нужен оператор" waits for the operator.
        b="$(field main "$d" branch)"; rev=main; branch_exists "$b" && rev="$b"
        git show "$rev:$WORK/$d/task.md" | grep -q '^Blocked: нужен оператор' && continue
      fi
      [ "$want" != ready ] || [ -z "$(deps_open "$d")" ] || continue
      echo "$id $want"; exit 0
    done
  done
  exit 0
fi

require_root_on_main

if [ "$mode" = --sync ]; then
  lock_main
  cd "$ROOT" || die "no $ROOT"
  git diff --quiet -- "$WORK/BOARD.md" || die "BOARD.md has uncommitted edits in $ROOT"
  for d in $dirs; do board_set "$WORK/BOARD.md" "${d:0:5}" "$(task_status "$d")"; done
  if git diff --quiet -- "$WORK/BOARD.md"; then echo "tasks: BOARD is in sync"
  else git commit -q -m "docs: BOARD sync" -m "Agent: claude" -- "$WORK/BOARD.md" && echo "tasks: BOARD synced"
  fi
  exit 0
fi

# --prune
for d in $dirs; do
  b="$(field main "$d" branch)"
  [ "$(field main "$d" status)" = done ] || continue
  wt="$ROOT/.worktrees/$d"
  if [ -d "$wt" ]; then
    git -C "$ROOT" worktree remove "$wt" && echo "tasks: removed $wt" || echo "tasks: $wt is busy or dirty, kept" >&2
  fi
  if branch_exists "$b" && git merge-base --is-ancestor "$b" main && [ ! -d "$wt" ]; then
    git -C "$ROOT" branch -q -d "$b" && echo "tasks: deleted $b"
  fi
done
