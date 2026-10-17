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
mkdir -p "$SPIKE_LOGDIR" /root/.local/share/opencode
cp /etc/harness/opencode.json /workplace/opencode.json
# opencode 1.19.3 does not pick the provider key from OPENROUTER_API_KEY alone: it reads its
# own auth store. Write it from the environment (the key never lands in the workplace or repo).
if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  printf '{"openrouter":{"type":"api","key":"%s"}}' "$OPENROUTER_API_KEY" \
    > /root/.local/share/opencode/auth.json
  chmod 600 /root/.local/share/opencode/auth.json
fi
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
