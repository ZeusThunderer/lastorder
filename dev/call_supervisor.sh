#!/usr/bin/env bash
# Headless supervisor call from ZCode: Claude Code CLI, Opus, medium effort.
# Protocol: docs/agents/coop-protocol.md, "Вызов надзора из ZCode". Run from the task worktree
# .worktrees/T-NNN-<slug> (Git Bash on Windows), never from the repo root.
#   dev/call_supervisor.sh plan   T-NNN   # review the plan in report.md (status in_progress)
#   dev/call_supervisor.sh result T-NNN   # review the submitted work (status review)
set -uo pipefail

die() { echo "call_supervisor: $*" >&2; exit 2; }

mode="${1:-}"; id="${2:-}"
[[ "$mode" =~ ^(plan|result)$ ]] || die "usage: dev/call_supervisor.sh plan|result T-NNN"
[[ "$id" =~ ^T-[0-9]{3}$ ]] || die "bad task id '$id'"
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

# Critical work always goes to the operator.
[ "$crit" = "critical" ] && die "$id is critical: headless review is not allowed, hand it to the operator"

case "$mode" in
  plan)   [ "$status" = "in_progress" ] || die "plan review needs status in_progress, got '$status'" ;;
  result) [ "$status" = "review" ]      || die "result review needs status review, got '$status'" ;;
esac

[ "$(git branch --show-current)" = "$branch" ] || die "switch to $branch first"
git diff --quiet && git diff --cached --quiet || die "uncommitted changes: commit them first"

# Critical zones (coop-protocol.md, "Критичные зоны"): in the allowed scope of the task or in the diff.
critical_re='^(runtime/perception/|runtime/hive/|runtime/llm/|runtime/lastorder/persona\.py|agent/last_order/prompt_template\.md|tests/test_perception\.py|docker/|\.env)|(^|/)(death|will)[^/]*\.py$'
scope="$(awk '/^Можно трогать:/{f=1;next} /^Нельзя трогать:/{f=0} f' "$task" | tr -d '\r' | grep -o '`[^`]*`' | tr -d '`')"
hits="$( { printf '%s\n' "$scope"; git diff --name-only main...HEAD; } | grep -E "$critical_re" | sort -u)"
[ -z "$hits" ] || die "touches critical zones, hand it to the operator:
$hits"

# Round limits: count headless rounds already in review.md.
review="$dir/review.md"
if [ "$mode" = plan ]; then limit=2; pat='^## План .*headless'; else limit=3; pat='^## Раунд .*headless'; fi
done_n=0; [ -f "$review" ] && done_n="$(grep -cE "$pat" "$review" || true)"
[ "$done_n" -lt "$limit" ] || die "limit of $limit headless $mode reviews for $id reached, hand it to the operator"

prompt="Ты — supervisor (Claude Code). Тебя вызвал исполнитель ZCode в headless-режиме, оператора рядом нет.
Задача $id, режим $mode. Выполни скил lo-review в режиме $mode, соблюдая раздел «Headless» этого скила.
Последней строкой ответа выведи ровно одно: VERDICT: accept | changes | ok | revise | blocked"

sha="$(git rev-parse --short HEAD)"
echo "call_supervisor: $id $mode @ $branch $sha (opus, effort medium, budget \$${SUPERVISOR_BUDGET_USD:-3})" >&2

out="$(claude -p "$prompt" \
  --model opus --effort medium \
  --permission-mode dontAsk \
  --allowedTools Read Grep Glob Edit Write Skill \
    "Bash(git *)" "Bash(uv run *)" "Bash(python tools/*)" "Bash(bash dev/*)" \
  --disallowedTools "Bash(git push*)" "Bash(git reset*)" "Bash(git clean*)" "Bash(git branch -D*)" \
    "Bash(git checkout -- *)" "Bash(git restore*)" "Bash(git rebase*)" \
  --max-budget-usd "${SUPERVISOR_BUDGET_USD:-3}" \
  --no-session-persistence --output-format json < /dev/null)"
rc=$?

printf '%s' "$out" | python -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("call_supervisor: unparsable CLI output"); sys.exit(0)
print(d.get("result", ""))
print("cost_usd: %s" % d.get("total_cost_usd", "?"))
'
exit "$rc"
