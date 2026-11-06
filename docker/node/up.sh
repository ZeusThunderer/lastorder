#!/usr/bin/env bash
# Bring the node sandboxes up with their secrets lifted from the repo root .env.
#
# Secrets reach the containers as single env variables (never an env_file over the whole
# .env, like docker/gateway/up.sh); the node entrypoint materializes them as 0400
# root-only files on tmpfs and scrubs its environment before exec'ing the runner (plan
# review P1-1; compose file-secrets bind as 0777 on Docker Desktop). The hive-side key
# maps (HIVE_MCP_NODE_KEYS, HIVE_RUNNER_KEYS) are assembled here as plain env too.
#
#   bash docker/node/up.sh                 # the whole stack (mc-server..node-3)
#   bash docker/node/up.sh node-1          # args pass through to compose
set -euo pipefail

HERE="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="$(git -C "$HERE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
[ -n "${ROOT:-}" ] || ROOT="$HERE"

# values are never echoed; quotes are stripped like gateway/up.sh does. lift ends 0 even
# when the value is absent: a missing key is a wait case, not a script abort.
lift() { # lift <var> [file key]: env wins, then the root .env
  local key="$1" file_key="${2:-$1}" val=""
  if [ -z "${!key:-}" ] && [ -f "$ROOT/.env" ]; then
    val="$(sed -n "s/^${file_key}=//p" "$ROOT/.env" | head -1 | tr -d '\r')"
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ -n "$val" ] && export "$key=$val"
  fi
  return 0
}
# the Z.ai token sits under ZAI_API_KEY in the root .env (T-024 naming); ZCODE_API_KEY
# stays the canonical env override, like the spike overlay used it
lift ZCODE_API_KEY ZAI_API_KEY
# the operator's game name (T-051) rides the same lift: a stand config value, not a key
lift OPERATOR_MC_NAME
lift MC_SERVER_1  # slot 1 on another server: an address, not a secret
# player commands (ADR-043, T-053): the common deny list, per-slot extras and the
# per-slot server login passwords — a password reaches that slot's mind ONLY
# (compose wires MC_LOGIN_PASSWORD_N into mind-N alone; node-N never sees it)
lift MC_COMMANDS_DENY
for n in 1 2 3; do
  lift "MC_COMMANDS_DENY_$n"
  lift "MC_LOGIN_PASSWORD_$n"
done
for key in GATEWAY_KEY_NODE_1 GATEWAY_KEY_NODE_2 GATEWAY_KEY_NODE_3 \
           HIVE_NODE_KEY_1 HIVE_NODE_KEY_2 HIVE_NODE_KEY_3 \
           HIVE_RUNNER_KEY_1 HIVE_RUNNER_KEY_2 HIVE_RUNNER_KEY_3 \
           GATEWAY_MASTER_KEY OPENROUTER_API_KEY GATEWAY_DB_PASSWORD \
           GATEWAY_KEY_MIND_1 GATEWAY_KEY_MIND_2 GATEWAY_KEY_MIND_3 GATEWAY_KEY_HIVE \
           HIVE_OPERATOR_KEY RCON_PASSWORD \
           HIVE_MCP_LO_KEY GATEWAY_KEY_LO HAPI_CLI_KEY; do  # the LO sandbox (T-043, T-039)
  lift "$key"
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

# The console tokens of the slots (T-044 R1-1/R1-3, T-045): the hub's namespace form
# `<base>:node-N` — the base token alone reads every session of the network and must
# never enter a sandbox. Derived from the one HAPI_CLI_KEY the hub itself uses; the
# operator's own page login is `<base>:node-N` per slot (one page = one namespace).
if [ -n "${HAPI_CLI_KEY:-}" ]; then
  for n in 1 2 3; do
    eval "current=\"\${HAPI_TOKEN_${n}:-}\""  # indirect default: plain ${x_${n}:-} is not bash
    [ -n "$current" ] || export "HAPI_TOKEN_${n}=${HAPI_CLI_KEY}:node-${n}"
  done
fi

# Fail-closed (review R2-6): a stand raised without keys would come up open (empty
# HIVE_RUNNER_KEYS ungates the registry/memory/comms routes) and nobody would notice.
# Refuse instead; NODE_DEV_OPEN=1 is the explicit dev exception (exported for compose,
# so the hive and the minds run open too).
if [ "${NODE_DEV_OPEN:-}" = "1" ]; then
  export NODE_DEV_OPEN=1
else
  # A node slot that is actually being raised must have its own pair of keys: no args
  # means the whole stack (every node). Both keys missing is the silent-open case.
  targets="${*:-}"
  for n in 1 2 3; do
    eval "rk=\${HIVE_RUNNER_KEY_$n:-}"
    eval "nk=\${HIVE_NODE_KEY_$n:-}"
    if [ -z "$rk" ] && [ -z "$nk" ]; then
      if [ -z "$targets" ] || printf '%s\n' $targets | grep -q "node-$n"; then
        echo "up.sh: node-$n has neither HIVE_RUNNER_KEY_$n nor HIVE_NODE_KEY_$n:" \
             "refusing — an unkeyed node is an open one (R2-6); keys come from $ROOT/.env" >&2
        exit 1
      fi
    fi
  done
fi

if [ -z "${ZCODE_API_KEY:-}" ]; then
  echo "up.sh: no ZCODE_API_KEY in env or $ROOT/.env: node-N starts, the zcode harness waits" >&2
fi

exec docker compose -f "$HERE/docker/compose.yml" up -d "$@"
