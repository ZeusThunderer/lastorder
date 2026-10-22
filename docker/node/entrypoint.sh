#!/usr/bin/env bash
# Entrypoint of node-N (T-027). Runs as root, materializes the secrets as root-only
# files on the tmpfs, renders the harness credentials into the sister's home, scrubs
# every secret from its own environment and execs the runner (which stays root as the
# supervisor and drops every harness process to uid `sister`; plan review P1-1).
#
# Why env at all: compose file-secrets land as 0777 bind mounts on Docker Desktop —
# verified on the stand, a `su sister` could read the runner key through /run/secrets.
# Here the env exists only for the microseconds before this script execs: the runner
# and the agent meet the secrets only as 0400 root-owned files (and the Z.ai token and
# the gateway key as the sister's own rendered configs, which ADR-033 allows).
set -euo pipefail

SISTER_UID="${SISTER_UID:-1001}"
SISTER_GID="${SISTER_GID:-1001}"
SISTER_HOME=/home/sister
SECRET_DIR=/run/node/secrets

log() { printf '[node-entrypoint] %s\n' "$*" >&2; }

# 1. Secrets -> root-only files on the tmpfs (/run/node is mode 700, root-owned).
mkdir -p "$SECRET_DIR" && chmod 700 "$SECRET_DIR" && chown root:root "$SECRET_DIR"
materialize() { # materialize <file> <value>: written 0400 root; no umask games — a
  # leaked 0177 umask once cost every later directory its execute bit
  [ -n "$2" ] || return 0
  printf '%s' "$2" > "$SECRET_DIR/$1"
  chown root:root "$SECRET_DIR/$1"
  chmod 0400 "$SECRET_DIR/$1"
}
materialize runner_key "${HIVE_RUNNER_KEY:-}"
materialize node_mcp_key "${HIVE_NODE_KEY:-}"
materialize gateway_node_key "${GATEWAY_NODE_KEY:-}"
materialize zai_api_key "${ZAI_API_KEY:-}"

# 2. The sister's home (tmpfs): zcode provider + opencode auth, rendered from secrets.
mkdir -p "$SISTER_HOME/.zcode/v2" "$SISTER_HOME/.local/share/opencode"
if [ -n "${ZAI_API_KEY:-}" ]; then
  ZAI_API_KEY="${ZAI_API_KEY}" ZCODE_MODEL="${ZCODE_MODEL:-GLM-5.3-Flash}" \
    node /usr/local/lib/zcode-provider.cjs "$SISTER_HOME/.zcode/v2/provider_config.json" \
    || log "zcode provider render failed: the zcode harness will not run"
else
  log "no Z.ai key: HARNESS=zcode would only wait (QUOTA_ACTION governs the rest)"
fi
if [ -n "${GATEWAY_NODE_KEY:-}" ]; then
  printf '{"llm-gateway":{"type":"api","key":"%s"}}' "$GATEWAY_NODE_KEY" \
    > "$SISTER_HOME/.local/share/opencode/auth.json"
  chmod 600 "$SISTER_HOME/.local/share/opencode/auth.json"
else
  log "no gateway node key: the opencode harness (and failover) will only wait"
fi

# The sister's whole home is hers — except what 2b renders next: HERMES_HOME's
# config.yaml and SOUL.md stay root's 0444 (review R1-2), so this blanket chown
# must run BEFORE the hermes render, not after it.
chown -R "${SISTER_UID}:${SISTER_GID}" "$SISTER_HOME"

# 2b. HERMES_HOME (tmpfs): config.yaml + the neutral SOUL.md via
#     workplace.drop_hermes_home, plus .env with the Z.ai coding key + base URL —
#     exactly the two env names Hermes's zai provider reads. drop_hermes_home
#     stamps the final ownership itself: the dir is the sister's 0700 (the gateway
#     refuses a root-owned or writable-by-others home — verified in the container),
#     while config.yaml/SOUL.md stay root 0444 (the harness config is the runner's,
#     R1-2; a swap is healed by the adapter's per-life re-render), only .env is
#     hers at 0600. The key rides the helper's environment, never argv:
#     /proc/<pid>/cmdline is world-readable, /proc/<pid>/environ is not. Rendered
#     even without the key (config-only), so the harness still fails cleanly
#     instead of scaffold-failing on a dead home.
[ -n "${ZAI_API_KEY:-}" ] || log "no Z.ai key: HARNESS=hermes (like zcode) would only wait"
HERMES_KEY="${ZAI_API_KEY:-}" \
HERMES_MODEL="${HERMES_MODEL:-${ZCODE_MODEL:-GLM-5.3-Flash}}" \
HERMES_NODE_MCP_KEY="$(cat "$SECRET_DIR/node_mcp_key" 2>/dev/null || printf '%s' "${HIVE_NODE_KEY:-}")" \
BODY_BASE="${BODY_BASE:-http://body-1:8700}" HIVE_URL="${HIVE_URL:-http://hive:8000}" \
SISTER_UID="$SISTER_UID" SISTER_GID="$SISTER_GID" \
  /app/.venv/bin/python -c '
import os
from pathlib import Path
from runtime.node.workplace import drop_hermes_home
drop_hermes_home(
    Path("/home/sister/.hermes"),
    model=os.environ["HERMES_MODEL"],
    body_base=os.environ["BODY_BASE"],
    hive_url=os.environ["HIVE_URL"],
    node_key=os.environ["HERMES_NODE_MCP_KEY"],
    glm_key=os.environ["HERMES_KEY"],
    uid=int(os.environ["SISTER_UID"]),
    gid=int(os.environ["SISTER_GID"]),
)
' || log "hermes home render failed: the hermes harness will not run"

# 3. Nothing secret may ride further: not into the runner's /proc/environ (execve
#    rebuilds it from what is left), not into any child the runner spawns.
unset HIVE_RUNNER_KEY HIVE_NODE_KEY GATEWAY_NODE_KEY ZAI_API_KEY

exec "$@"
