#!/usr/bin/env bash
# Entrypoint of node-N (T-027). Runs as root, prepares the sister's home and the secrets,
# then execs the runner (which stays root as the supervisor and drops every harness
# process to uid `sister`; plan review P1-1).
#
# Secrets arrive as docker secrets under /run/secrets (root-owned 0444 by default). The
# entrypoint tightens them to 0400 BEFORE the runner starts, so the only uid that can
# read the runner key is root itself; the Z.ai token and the gateway key are rendered
# into the sister's home (0600, hers) exactly as ADR-033 allows.
set -euo pipefail

SISTER_UID="${SISTER_UID:-1001}"
SISTER_GID="${SISTER_GID:-1001}"
SISTER_HOME=/home/sister

log() { printf '[node-entrypoint] %s\n' "$*" >&2; }

read_secret() { # read_secret <default file> <env name>
  local file="$1" env_name="$2" value=""
  if [ -r "$file" ]; then value="$(tr -d '\r\n' < "$file")"; fi
  if [ -z "$value" ] && [ -n "${!env_name:-}" ]; then value="${!env_name}"; fi
  printf '%s' "$value"
}

# 1. The runner's own key: root-only, before anything else runs.
for f in /run/secrets/*; do
  [ -e "$f" ] || continue
  chown root:root "$f" && chmod 0400 "$f"
done

# 2. The sister's home (tmpfs): zcode provider + opencode auth, rendered from secrets.
mkdir -p "$SISTER_HOME/.zcode/v2" "$SISTER_HOME/.local/share/opencode"

zai_key="$(read_secret /run/secrets/zai_api_key ZAI_API_KEY)"
if [ -n "$zai_key" ]; then
  ZAI_API_KEY="$zai_key" ZCODE_MODEL="${ZCODE_MODEL:-GLM-5.3-Flash}" \
    node /usr/local/lib/zcode-provider.cjs "$SISTER_HOME/.zcode/v2/provider_config.json" \
    || log "zcode provider render failed: the zcode harness will not run"
else
  log "no Z.ai key: HARNESS=zcode would only wait (QUOTA_ACTION governs the rest)"
fi

gateway_key="$(read_secret /run/secrets/gateway_node_key GATEWAY_NODE_KEY)"
if [ -n "$gateway_key" ]; then
  printf '{"llm-gateway":{"type":"api","key":"%s"}}' "$gateway_key" \
    > "$SISTER_HOME/.local/share/opencode/auth.json"
  chmod 600 "$SISTER_HOME/.local/share/opencode/auth.json"
else
  log "no gateway node key: the opencode harness (and failover) will only wait"
fi

chown -R "${SISTER_UID}:${SISTER_GID}" "$SISTER_HOME"

exec "$@"
