#!/usr/bin/env bash
# Headless supervisor call from ZCode: Claude Code CLI, Opus only (ADR-028, ADR-040).
# Protocol: docs/agents/coop-protocol.md, "Вызов надзора". Run from the task worktree
# .worktrees/T-NNN-<slug> (Git Bash on Windows), never from the repo root. Call the root copy of the
# script: the branch copy may be older than main.
#   bash <root>/dev/call_supervisor.sh result T-NNN [high]   # review the submitted work (status review)
#   bash <root>/dev/call_supervisor.sh answer T-NNN [high]   # answer the worker's question (status blocked)
# Effort is medium ($4). high ($6) only when the operator asked for it.
set -uo pipefail

die() { echo "call_supervisor: $*" >&2; exit 2; }

mode="${1:-}"; id="${2:-}"; effort="${3:-medium}"
[[ "$mode" =~ ^(result|answer)$ ]] || die "usage: call_supervisor.sh result|answer T-NNN [high]"
[[ "$id" =~ ^T-[0-9]{3}$ ]] || die "bad task id '$id'"
[[ "$effort" =~ ^(medium|high)$ ]] || die "bad effort '$effort': medium (default) or high"
[ -f CLAUDE.md ] && [ -d docs/agents/work ] || die "run from the top of the task worktree"
root="$(git worktree list --porcelain | sed -n '1s/^worktree //p')"
[ "$(git rev-parse --show-toplevel)" != "$root" ] || die "run from the task worktree .worktrees/$id-*, not from the repo root"
command -v claude >/dev/null || die "claude CLI not found in PATH"

shopt -s nullglob
dirs=(docs/agents/work/"$id"-*/)
[ "${#dirs[@]}" -eq 1 ] || die "expected exactly one folder docs/agents/work/$id-*"
dir="${dirs[0]%/}"
task="$dir/task.md"
[ -f "$task" ] || die "no $task"

field() { sed -n "s/^$1: *//p" "$task" | head -1 | tr -d '\r'; }
status="$(field status)"; crit="$(field criticality)"; branch="$(field branch)"

case "$mode" in
  result) [ "$status" = "review" ]  || die "result review needs status review, got '$status'" ;;
  answer) [ "$status" = "blocked" ] || die "answer needs status blocked, got '$status'"
          ! grep -q '^Blocked: нужен оператор' "$task" || die "$id waits for the operator, not for the supervisor" ;;
esac

[ "$(git branch --show-current)" = "$branch" ] || die "switch to $branch first"
git diff --quiet && git diff --cached --quiet || die "uncommitted changes: commit them first"

# Round limits: count headless rounds already in review.md.
review="$dir/review.md"
case "$mode" in
  result) limit=3; pat='^## Раунд .*headless' ;;
  answer) limit=2; pat='^## Ответ .*headless' ;;
esac
done_n=0; [ -f "$review" ] && done_n="$(grep -cE "$pat" "$review" || true)"
# The operator may grant extra rounds for a task after the limit is exhausted.
limit="${SUPERVISOR_MAX_ROUNDS:-$limit}"
[ "$done_n" -lt "$limit" ] || die "limit of $limit headless $mode calls for $id reached, hand it to the operator"

case "$effort" in medium) budget=4 ;; high) budget=6 ;; esac
budget="${SUPERVISOR_BUDGET_USD:-$budget}"

prompt="Ты — supervisor (Claude Code). Тебя вызвал исполнитель ZCode в headless-режиме, оператора рядом нет.
Задача $id, режим $mode, criticality: ${crit:-normal}. Выполни скил lo-review в режиме $mode, соблюдая раздел «Headless» этого скила.
Актуальные правила лежат в корне репозитория $root (ветка main): $root/.claude/skills/lo-review/SKILL.md,
$root/docs/agents/coop-protocol.md, $root/CLAUDE.md. Копии в worktree могут быть старше — читай корневые.
Скрипты dev/ запускай из корня: bash $root/dev/<скрипт>.
Последней строкой ответа выведи ровно одно: VERDICT: accept | changes | reject | answered | blocked"

sha="$(git rev-parse --short HEAD)"
echo "call_supervisor: $id $mode @ $branch $sha (opus, effort $effort, budget \$$budget)" >&2

# The model is fixed: Opus only, no override (ADR-028). The stand may be used, but not torn down.
out="$(claude -p "$prompt" \
  --model opus --effort "$effort" \
  --permission-mode dontAsk \
  --add-dir "$root" \
  --allowedTools Read Grep Glob Edit Write Skill \
    "Bash(git *)" "Bash(uv run *)" "Bash(python tools/*)" "Bash(bash dev/*)" "Bash(bash $root/dev/*)" \
    "Bash(docker compose *)" "Bash(docker ps*)" "Bash(docker logs *)" \
  --disallowedTools "Bash(git push*)" "Bash(git reset*)" "Bash(git clean*)" "Bash(git branch -D*)" \
    "Bash(git checkout -- *)" "Bash(git restore*)" "Bash(git rebase*)" \
    "Bash(docker compose down*)" "Bash(docker compose * down*)" "Bash(docker volume *)" "Bash(docker system *)" \
  --max-budget-usd "$budget" \
  --no-session-persistence --output-format json < /dev/null)"
rc=$?

printf '%s' "$out" | EFFORT="$effort" PYTHONIOENCODING=utf-8 uv run --no-project python -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("call_supervisor: unparsable CLI output"); sys.exit(0)
print(d.get("result", ""))
print("cost_usd: %s · effort: %s" % (d.get("total_cost_usd", "?"), os.environ["EFFORT"]))
'
exit "$rc"
