#!/usr/bin/env bash
# One spike session in the ZCode headless CLI (T-024). Runs inside the harness container at
# /workplace. Auth: the GLM Coding Plan key arrives as ANTHROPIC_API_KEY (the built-in
# zai-api provider is anthropic-messages at api.z.ai/api/anthropic; step 0 finding); the MCP
# body comes from .mcp.json (array form, the CLI's own schema).
set -uo pipefail
: "${SPIKE_MINUTES:=15}"
: "${SPIKE_LOGDIR:=/workplace/.logs}"
mkdir -p "$SPIKE_LOGDIR"
cp /etc/harness/zcode.mcp.json /workplace/.mcp.json
cd /workplace

zcode() { node /opt/zcode-cli/glm/zcode.cjs "$@"; }

DEADLINE=$(( $(date +%s) + SPIKE_MINUTES * 60 ))
PROMPT="$(cat FIRST_MESSAGE.md)"
SESSION=""
FIRST=1
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if [ "$FIRST" = 1 ]; then
    OUT="$(zcode -p "$PROMPT" --mode yolo --json --no-color 2>>"$SPIKE_LOGDIR/zcode.stderr.log")"
    FIRST=0
  else
    OUT="$(zcode -p "Continue the spike session: observe, act, speak, wait_events. The run ends at the deadline." \
      --mode yolo --json --no-color ${SESSION:+--resume "$SESSION"} 2>>"$SPIKE_LOGDIR/zcode.stderr.log")"
  fi
  printf '%s\n' "$OUT" >>"$SPIKE_LOGDIR/zcode.jsonl"
  NEXT="$(printf '%s\n' "$OUT" | jq -r 'select(.sessionId != null) | .sessionId' 2>/dev/null | tail -1)"
  [ -n "$NEXT" ] && SESSION="$NEXT"
done
echo "session window closed" >>"$SPIKE_LOGDIR/zcode.stderr.log"
