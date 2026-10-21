#!/usr/bin/env bash
# Bring the node sandboxes up with their secrets lifted from the repo root .env.
#
# The runner refuses env-borne secrets (plan review P1-1: /proc/environ), so its keys are
# written as 0600 files under docker/node/secrets/ (gitignored) and mounted as compose
# secrets. The hive-side key maps (HIVE_MCP_NODE_KEYS, HIVE_RUNNER_KEYS) ride the same
# lift as plain env, like docker/gateway/up.sh does for its consumers.
#
#   bash docker/node/up.sh                 # the whole stack (mc-server..node-3)
#   bash docker/node/up.sh node-1          # args pass through to compose
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(git -C "$HERE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
[ -n "${ROOT:-}" ] || ROOT="$HERE"

# values are never echoed; quotes are stripped like gateway/up.sh does
lift() { # lift <var>
  local key="$1" val=""
  [ -n "${!key:-}" ] && return 0
  [ -f "$ROOT/.env" ] || return 0
  val="$(sed -n "s/^${key}=//p" "$ROOT/.env" | head -1 | tr -d '\r')"
  val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
  [ -n "$val" ] && export "$key=$val"
}
for key in ZCODE_API_KEY GATEWAY_KEY_NODE_1 GATEWAY_KEY_NODE_2 GATEWAY_KEY_NODE_3 \
           HIVE_NODE_KEY_1 HIVE_NODE_KEY_2 HIVE_NODE_KEY_3 \
           HIVE_RUNNER_KEY_1 HIVE_RUNNER_KEY_2 HIVE_RUNNER_KEY_3 \
           GATEWAY_MASTER_KEY OPENROUTER_API_KEY GATEWAY_DB_PASSWORD \
           GATEWAY_KEY_MIND_1 GATEWAY_KEY_MIND_2 GATEWAY_KEY_MIND_3 GATEWAY_KEY_HIVE \
           RCON_PASSWORD; do
  lift "$key"
done

SECRETS="$HERE/docker/node/secrets"
mkdir -p "$SECRETS" && chmod 700 "$SECRETS"
secret() { # secret <file> <value>
  umask 177
  printf '%s' "${2:-}" > "$SECRETS/$1"
}
secret zai_api_key "${ZCODE_API_KEY:-}"
for n in 1 2 3; do
  eval "secret runner_key_$n \"\${HIVE_RUNNER_KEY_$n:-}\""
  eval "secret node_mcp_key_$n \"\${HIVE_NODE_KEY_$n:-}\""
  eval "secret gateway_node_key_$n \"\${GATEWAY_KEY_NODE_$n:-}\""
done

# the hive and the minds read their identity maps from env (compose interpolates from
# this process); each mind-N gets its own runner key as MIND_RUNNER_KEY
parts() { # parts <prefix> -> "LastOrder01=k1;LastOrder02=k2;..." non-empty entries only
  local out="" n key
  for n in 1 2 3; do
    eval "key=\"\${${1}_${n}:-}\""
    [ -n "$key" ] && out="${out:+$out;}LastOrder0${n}=$key"
  done
  printf '%s' "$out"
}
export HIVE_MCP_NODE_KEYS="${HIVE_MCP_NODE_KEYS:-$(parts HIVE_NODE_KEY)}"
export HIVE_RUNNER_KEYS="${HIVE_RUNNER_KEYS:-$(parts HIVE_RUNNER_KEY)}"

if [ -z "${ZCODE_API_KEY:-}" ]; then
  echo "up.sh: no ZCODE_API_KEY in env or $ROOT/.env: node-N starts, the zcode harness waits" >&2
fi

exec docker compose -f "$HERE/docker/compose.yml" up -d "$@"
