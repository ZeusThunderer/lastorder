#!/usr/bin/env bash
# T-041 stand: the spike profile of the task — mc-server, body-1, mind-1, hive,
# zai-egress and node-1 WITHOUT its runner (the quota spender sleeps; the Pi
# sessions of the spike are launched by hand through pi_rpc.sh). Keys are lifted
# from the repo root .env exactly like docker/node/up.sh does (values never echo).
#
#   bash dev/console_spike/up_spike.sh          # raise (idempotent)
#   bash dev/console_spike/up_spike.sh down     # take the stand down
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(git -C "$HERE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
[ -n "${ROOT:-}" ] || ROOT="$HERE"
COMPOSE="docker compose -f $HERE/docker/compose.yml"
SPIKE_CONTAINER=node1-pi

if [ "${1:-}" = "down" ]; then
  docker rm -f "$SPIKE_CONTAINER" 2>/dev/null || true
  exec $COMPOSE down
fi

lift() { # lift <var> [file key]: env wins, then the root .env; never echoes the value
  local key="$1" file_key="${2:-$1}" val=""
  if [ -z "${!key:-}" ] && [ -f "$ROOT/.env" ]; then
    val="$(sed -n "s/^${file_key}=//p" "$ROOT/.env" | head -1 | tr -d '\r')"
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ -n "$val" ] && export "$key=$val"
  fi
  return 0
}
lift ZCODE_API_KEY ZAI_API_KEY
for key in GATEWAY_KEY_NODE_1 HIVE_NODE_KEY_1 HIVE_RUNNER_KEY_1 \
           GATEWAY_KEY_MIND_1 GATEWAY_KEY_HIVE RCON_PASSWORD; do
  lift "$key"
done
if [ -z "${HIVE_RUNNER_KEY_1:-}" ] && [ -z "${HIVE_NODE_KEY_1:-}" ]; then
  echo "up_spike.sh: slot 1 has no keys in env or $ROOT/.env (R2-6); aborting" >&2
  exit 1
fi
export HIVE_MCP_NODE_KEYS="${HIVE_MCP_NODE_KEYS:-LastOrder01=${HIVE_NODE_KEY_1:-}}"
export HIVE_RUNNER_KEYS="${HIVE_RUNNER_KEYS:-LastOrder01=${HIVE_RUNNER_KEY_1:-}}"

# The dependency chain of node-1 covers everything the task names: zai-egress,
# mind-1 -> body-1 -> mc-server, hive. `compose run` honors depends_on, replaces
# the runner command with a sleep, and keeps the spike off the quota.
$COMPOSE up -d zai-egress
if ! docker ps --format '{{.Names}}' | grep -q "^$SPIKE_CONTAINER$"; then
  $COMPOSE run -d --no-deps --name "$SPIKE_CONTAINER" node-1 sleep infinity
fi
# dependencies after the run container exists (mc-server healthy gates the rest)
$COMPOSE up -d body-1 mind-1 hive
echo "stand up: hive/body-1/mind-1/zai-egress + $SPIKE_CONTAINER (runner asleep)"
