#!/usr/bin/env bash
# One spike session in the ZCode harness (T-024). Runs inside the harness container; the
# workplace is bind-mounted at /workspace. The operator's zcode-headless entrypoint
# (/usr/local/bin/zcode-harness) renders the personal Coding Plan provider and wraps
# `zcode -p ... --model zai-cp/<model> --json --mode yolo --cwd /workspace`.
#
# The session is a run/resume loop: FIRST_MESSAGE.md first, then a continuation every time
# the agent finishes its turn, until SPIKE_MINUTES is up (the host orchestrator stops the
# container at the hard deadline either way). Sequential runs only: the Coding Plan rate
# limit is per key (README "Грабли" #1), so no parallel turns from this container.
set -uo pipefail
: "${SPIKE_MINUTES:=15}"
: "${SPIKE_LOGDIR:=/workspace/.logs}"
mkdir -p "$SPIKE_LOGDIR"
# Project config with the body MCP server: zcode merges zcode.json from the working directory
# (the .mcp.json array form is not read by the -p path — verified live).
cp /etc/harness/zcode.json /workspace/zcode.json

HARNESS=/usr/local/bin/zcode-harness
DEADLINE=$(( $(date +%s) + SPIKE_MINUTES * 60 ))
SESSION=""
FIRST=1
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if [ "$FIRST" = 1 ]; then
    OUT="$("$HARNESS" run -f /workspace/FIRST_MESSAGE.md 2>>"$SPIKE_LOGDIR/zcode.stderr.log")" \
      || echo "zcode-exit=$?" >>"$SPIKE_LOGDIR/zcode.stderr.log"
    FIRST=0
  else
    OUT="$("$HARNESS" resume "$SESSION" "Continue the spike session: observe, act, speak, wait_events. The run ends at the deadline." 2>>"$SPIKE_LOGDIR/zcode.stderr.log")" \
      || echo "zcode-exit=$?" >>"$SPIKE_LOGDIR/zcode.stderr.log"
  fi
  printf '%s\n' "$OUT" >>"$SPIKE_LOGDIR/zcode.jsonl"
  NEXT="$(printf '%s\n' "$OUT" | jq -r 'select(.sessionId != null) | .sessionId' 2>/dev/null | tail -1)"
  [ -n "$NEXT" ] && SESSION="$NEXT"
  # Without a session id there is nothing to resume: retry a fresh run instead of spinning.
  if [ -z "$SESSION" ] && [ "$FIRST" = 0 ]; then
    FIRST=1
    sleep 5
  fi
  sleep 2
done
echo "session window closed" >>"$SPIKE_LOGDIR/zcode.stderr.log"
