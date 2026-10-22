#!/usr/bin/env bash
# T-030 stand: the whole T-027 stack plus the last-order sandbox, in one compose project.
#
# The T-027 stand on this host is already running, but its keys lived in that session's
# shell. They are lifted here from the running containers' environments (values are
# never printed, never written to a file); the LO-only secrets are generated/issued
# fresh: HIVE_MCP_LO_KEY (the MCP identity of the administrator) and GATEWAY_KEY_LO
# (a dedicated gateway key, review P1-4, so the session cost is separable).
#
#   bash docs/agents/work/T-030-last-order-agent/stand_up.sh
set -euo pipefail
cd "$(dirname "$0")/../../../.."

lift_env() { # lift_env <container> <var>: export var from a running container
  local val
  val="$(docker exec "$1" printenv "$2" 2>/dev/null || true)"
  [ -n "$val" ] && export "$2=$val"
  return 0
}

lift_env lastorder-llm-gateway-1 LITELLM_MASTER_KEY
lift_env lastorder-gateway-db-1 POSTGRES_PASSWORD
# the provider key never rode the old stand (its sisters used the Z.ai subscription):
# lift it from the repo root .env, the up.sh convention
if [ -z "${OPENROUTER_API_KEY:-}" ] && [ -f /c/ai-char/.env ]; then
  val="$(sed -n 's/^OPENROUTER_API_KEY=//p' /c/ai-char/.env | head -1 | tr -d '\r"')"
  [ -n "$val" ] && export OPENROUTER_API_KEY="$val"
fi
lift_env lastorder-hive-1 HIVE_MCP_NODE_KEYS
lift_env lastorder-hive-1 HIVE_RUNNER_KEYS
lift_env lastorder-mc-server-1 RCON_PASSWORD
for n in 1 2 3; do
  lift_env "lastorder-mind-$n-1" LLM_API_KEY
  if [ -n "${LLM_API_KEY:-}" ]; then export "GATEWAY_KEY_MIND_$n=$LLM_API_KEY"; unset LLM_API_KEY; fi
done
export GATEWAY_MASTER_KEY="${LITELLM_MASTER_KEY:-}"
export GATEWAY_DB_PASSWORD="${POSTGRES_PASSWORD:-lastorder}"
unset LITELLM_MASTER_KEY POSTGRES_PASSWORD

part() { printf '%s' "${1:-}" | tr ';' '\n' | sed -n "s/^$2=//p" | head -1; }
for n in 1 2 3; do
  k="$(part "${HIVE_MCP_NODE_KEYS:-}" "LastOrder0$n")"; [ -n "$k" ] && export "HIVE_NODE_KEY_$n=$k"
  k="$(part "${HIVE_RUNNER_KEYS:-}" "LastOrder0$n")"; [ -n "$k" ] && export "HIVE_RUNNER_KEY_$n=$k"
done

# the sisters' zcode harness runs on the Z.ai subscription (up.sh convention)
zai="$(sed -n 's/^ZAI_API_KEY=//p' /c/ai-char/.env | head -1 | tr -d '\r"')"
[ -n "$zai" ] && export ZCODE_API_KEY="$zai"

# Last Order's MCP key: reuse the running hive's, else a fresh one
lo="$(docker exec lastorder-hive-1 printenv HIVE_MCP_LO_KEY 2>/dev/null || true)"
[ -n "$lo" ] || lo="sk-lo-$(openssl rand -hex 12)"
export HIVE_MCP_LO_KEY="$lo"

# a dedicated gateway key for the LO sessions (review P1-4); printed once by the tool,
# captured straight into the environment. The alias is run-stamped: a re-run issues a
# fresh key instead of colliding (the orphan of an aborted run is revoked by hand).
if [ -n "${GATEWAY_MASTER_KEY:-}" ]; then
  token="$(uv run python dev/gateway_keys.py issue \
    --alias "last-order-$(date +%m%d%H%M)" --rpm 30 \
    --hourly-usd 0.15 --models z-ai/glm-5.3-flash,openrouter/z-ai/glm-5.3-flash \
    | sed -n 's/^token *: //p')"
  [ -n "$token" ] && export GATEWAY_KEY_LO="$token"
fi

docker compose -f docker/compose.yml up -d --build
