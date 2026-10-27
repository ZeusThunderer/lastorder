#!/usr/bin/env bash
# T-044 stand: the HAPI console spike — hapi-hub plus the slot-1 stack
# (mc-server, body-1, mind-1, hive, zai-egress) and TWO agent sandboxes with
# their runners asleep: node1-pi (the slot 1 sister's `hapi pi`, ADR-042) and
# lo-pi (a minimal test agent in the last-order sandbox). The agents are
# launched by hand through hapi_agent.py — the runner/admin integration is
# T-045/T-043, not this spike. Keys are lifted from the repo root .env exactly
# like docker/node/up.sh does (values never echo).
#
#   bash dev/console_spike/up_spike.sh          # raise (idempotent)
#   bash dev/console_spike/up_spike.sh down     # take the stand down
#
# Parallel with another stand: HAPI_PROJECT=t044 MC_HOST_PORT=25575
# HIVE_BOARD_PORT=18081 HAPI_PORT=3007 up_spike.sh (compose -p wins over the
# file's name:, ports move; volumes separate per project).
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(git -C "$HERE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
[ -n "${ROOT:-}" ] || ROOT="$HERE"
COMPOSE="docker compose -p ${HAPI_PROJECT:-lastorder} -f $HERE/docker/compose.yml"
# Project-scoped names: a parallel stand must not collide with another
# project's containers (a stopped node1-pi from the T-041 spike still exists).
PI_CONTAINER="${HAPI_PROJECT:-lastorder}-node1-pi"
LO_CONTAINER="${HAPI_PROJECT:-lastorder}-lo-pi"

if [ "${1:-}" = "down" ]; then
  docker rm -f "$PI_CONTAINER" "$LO_CONTAINER" 2>/dev/null || true
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
           GATEWAY_KEY_MIND_1 GATEWAY_KEY_HIVE RCON_PASSWORD HAPI_CLI_API_TOKEN \
           OPENROUTER_API_KEY GATEWAY_MASTER_KEY GATEWAY_DB_PASSWORD GATEWAY_KEY_LO; do
  lift "$key"
done
if [ -z "${HIVE_RUNNER_KEY_1:-}" ] && [ -z "${HIVE_NODE_KEY_1:-}" ]; then
  echo "up_spike.sh: slot 1 has no keys in env or $ROOT/.env (R2-6); aborting" >&2
  exit 1
fi
if [ -z "${HAPI_CLI_API_TOKEN:-}" ]; then
  echo "up_spike.sh: no HAPI_CLI_API_TOKEN (uv run python dev/stand_keys.py init); aborting" >&2
  exit 1
fi
export HIVE_MCP_NODE_KEYS="${HIVE_MCP_NODE_KEYS:-LastOrder01=${HIVE_NODE_KEY_1:-}}"
export HIVE_RUNNER_KEYS="${HIVE_RUNNER_KEYS:-LastOrder01=${HIVE_RUNNER_KEY_1:-}}"

# The hub first: raising it early also fails fast on a taken host port
# (HAPI_PORT moves it) before anything quota-bearing starts. llm-gateway feeds
# the LO test agent (the gateway tier, not Z.ai) and the hive.
$COMPOSE up -d --build hapi-hub
$COMPOSE up -d zai-egress llm-gateway
# The dependency chain covers the task's stand list: mind-1 -> body-1 ->
# mc-server, hive. Hive must run BEFORE the sandboxes: on a fresh project's
# volumes only the hive's start mkdir creates the pending_wills subpath the
# node sandboxes bind (R2-9); `compose run --no-deps` skips that ordering.
$COMPOSE up -d body-1 mind-1 hive
# MSYS_NO_PATHCONV: Git Bash would rewrite the container's absolute path into a
# host one and the probe would always fail (the T-041 recipe hit the same trap)
if ! MSYS_NO_PATHCONV=1 bash "$HERE/dev/wait_for.sh" 90 -- $COMPOSE exec -T hive test -d /hive-run/pending_wills; then
  echo "up_spike.sh: hive never created /hive-run/pending_wills" >&2
  exit 1
fi
# `compose run` replaces the runner command with a sleep and keeps both quota
# spenders (the node runner, the hive admin) off the meter.
if ! docker ps -a --format '{{.Names}}' | grep -q "^$PI_CONTAINER$"; then
  $COMPOSE run -d --no-deps --name "$PI_CONTAINER" node-1 sleep infinity
fi
if ! docker ps -a --format '{{.Names}}' | grep -q "^$LO_CONTAINER$"; then
  $COMPOSE run -d --no-deps --name "$LO_CONTAINER" last-order sleep infinity
fi
echo "stand up: hapi-hub (http://127.0.0.1:${HAPI_PORT:-3006}) + hive/body-1/mind-1/zai-egress"
echo "agent sandboxes: $PI_CONTAINER, $LO_CONTAINER (runners asleep)"
echo "agents:   docker exec -i -e HAPI_AGENT_MODE=slot  -e HAPI_ACTION=start $PI_CONTAINER /app/.venv/bin/python - < dev/console_spike/hapi_agent.py"
echo "          docker exec -i -e HAPI_AGENT_MODE=lo    -e HAPI_ACTION=start $LO_CONTAINER /app/.venv/bin/python - < dev/console_spike/hapi_agent.py"
