#!/usr/bin/env bash
# One spike session in OpenCode (T-024). Runs inside the harness container at /workplace.
#
# The model works as one long turn: the prompt tells the node to keep an observe/act/wait_events
# loop until the session deadline. If OpenCode still ends its turn early, we continue the same
# session (--continue) until the outer time is up; the outer orchestrator stops the container
# at the hard deadline either way.
set -uo pipefail
: "${SPIKE_MINUTES:=15}"
: "${SPIKE_MODEL:=openrouter/qwen/qwen3.7-flash}"
: "${SPIKE_LOGDIR:=/workplace/.logs}"
mkdir -p "$SPIKE_LOGDIR"
cp /etc/harness/opencode.json /workplace/opencode.json
cd /workplace

DEADLINE=$(( $(date +%s) + SPIKE_MINUTES * 60 ))
PROMPT="$(cat FIRST_MESSAGE.md)"
FIRST=1
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if [ "$FIRST" = 1 ]; then
    opencode run --model "$SPIKE_MODEL" --format json --title "harness-spike" "$PROMPT" \
      2>>"$SPIKE_LOGDIR/opencode.stderr.log" || echo "opencode-exit=$?" >>"$SPIKE_LOGDIR/opencode.stderr.log"
    FIRST=0
  else
    opencode run --model "$SPIKE_MODEL" --format json --continue \
      "Continue the spike session: observe, act, speak, wait_events. The run ends at the deadline." \
      2>>"$SPIKE_LOGDIR/opencode.stderr.log" || echo "opencode-exit=$?" >>"$SPIKE_LOGDIR/opencode.stderr.log"
  fi
done
echo "session window closed" >>"$SPIKE_LOGDIR/opencode.stderr.log"
