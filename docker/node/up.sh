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
# The repo as the Docker daemon sees it (ADR-050): compose's bind sources ride it, so
# the host CLI and compose inside the infra container hand the daemon the same paths
# (and never recreate each other's containers). Git Bash on Docker Desktop: /c/x ->
# the VM's host mount /run/desktop/mnt/host/c/x; Linux: the path itself.
if [ -z "${STAND_DAEMON_ROOT:-}" ]; then
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      p="$(cd "$ROOT" && pwd)"
      STAND_DAEMON_ROOT="/run/desktop/mnt/host/$(printf '%s' "${p:1:1}" | tr 'A-Z' 'a-z')${p:2}" ;;
    *) STAND_DAEMON_ROOT="$ROOT" ;;
  esac
fi
export STAND_DAEMON_ROOT
# MSYS rewrites any POSIX-looking env value it hands a Windows program (/run/... ->
# C:/Program Files/Git/run/...): keep this one verbatim for docker.exe
export MSYS2_ENV_CONV_EXCL="${MSYS2_ENV_CONV_EXCL:+$MSYS2_ENV_CONV_EXCL;}STAND_DAEMON_ROOT"
mkdir -p "$ROOT/.stand" 2>/dev/null || true   # the infra container's writable corner

# A slot on another server: an address, not a secret (all slots, T-057). Last Order's
# own moves (infra `slot_server`, T-072) live in $ROOT/.stand/servers.env, written by
# the executor; they win over .env so a later restart keeps her choice. Env wins
# over both. A slot sent home (mc-server:25565) leaves the file.
if [ -f "$ROOT/.stand/servers.env" ]; then
  for n in 1 2 3; do
    eval "cur=\"\${MC_SERVER_${n}:-}\""
    if [ -z "$cur" ]; then
      val="$(sed -n "s/^MC_SERVER_${n}=//p" "$ROOT/.stand/servers.env" | head -1 | tr -d '\r')"
      [ -n "$val" ] && export "MC_SERVER_${n}=$val"
    fi
  done
fi
lift MC_SERVER_1
lift MC_SERVER_2
lift MC_SERVER_3
# per-slot stand switches compose reads only from this process (it never sees the root
# .env): the camera (T-063) and the post-login skin command (T-061); the chat topics
# path (ADR-048) — empty in .env turns the filter off
for n in 1 2 3; do
  lift "BODY_CAMERA_$n"
  lift "MC_SKIN_COMMAND_$n"
done
lift CHAT_TOPICS_PATH
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
           HIVE_MCP_LO_KEY GATEWAY_KEY_LO HAPI_CLI_KEY HIVE_INFRA_KEY HIVE_TOOL_KEY; do  # the LO sandbox (T-043, T-039); HIVE_INFRA_KEY -> hive alone (T-057)
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

COMPOSE=(docker compose -f "$HERE/docker/compose.yml")
if [ $# -gt 0 ]; then
  exec "${COMPOSE[@]}" up -d "$@"   # services as asked (the executor, slot_restart.sh)
fi
# The stand's bring-up (ADR-050): the core up — the only tier that autostarts — and
# every slot, body and the home world CREATED but stopped, keys baked in; the executor
# starts them when Last Order, the operator or a mind asks.
"${COMPOSE[@]}" up -d
exec "${COMPOSE[@]}" --profile slots --profile bodies --profile world create
