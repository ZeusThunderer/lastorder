#!/usr/bin/env bash
# T-024 spike orchestrator — runs on the HOST (Git Bash on Windows), not in a container.
#
#   bash dev/harness_spike/spike.sh opencode|zcode [minutes] [serial]
#
# What it does (review P1-5, P1-7, P2-2, P2-3):
# 1. builds the throwaway sister workplace (sister_prompt.py) outside the repo;
# 2. brings up mc-server, body-1 and the MCP mind-1 (compose overlay);
# 3. verifies the sandbox: body-1:8700 reachable, body-1:25333 and mc-server:25575 NOT;
# 4. watches the run: RCON /kill at minute 10 if the node has not died (host-side, no secrets
#    in the harness container); after a death waits for will.md, else writes the emergency one;
#    births the next node if more than 3 minutes remain;
# 5. samples docker stats; at the deadline stops the harness container (never compose down);
# 6. collects logs and prints the summary (summarize.py).
set -uo pipefail

HARNESS="${1:?usage: spike.sh opencode|zcode [minutes] [serial]}"
MINUTES="${2:-15}"
SERIAL="${3:-1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose -f docker/compose.yml -f dev/compose.harness-spike.yml)
RUN_BASE="${SPIKE_RUN_BASE:-/tmp/lo-spike}"
RUN="$RUN_BASE/$HARNESS-$(date +%Y%m%d-%H%M%S)"
PROFILE_SERVICE="harness"
[ "$HARNESS" = zcode ] && PROFILE_SERVICE="harness-zcode"
MC_USER="${MC_USERNAME:-LastOrder01}"

log() { echo "[spike] $*"; }

# Compose looks for .env in the project dir (docker/), not the repo root: lift the secrets the
# harness needs into the environment without echoing them (review P1-6: single variables only).
for key in OPENROUTER_API_KEY ZAI_API_KEY RCON_PASSWORD; do
  if [ -z "${!key:-}" ] && [ -f "$ROOT/.env" ]; then
    val="$(sed -n "s/^${key}=//p" "$ROOT/.env" | head -1 | tr -d '\r')"
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ -n "$val" ] && export "$key=$val"
  fi
done

mkdir -p "$RUN/logs"
log "run dir: $RUN (windows path for compose: $(cygpath -w "$RUN" 2>/dev/null || echo "$RUN"))"

birth_node() {
  local serial="$1" seed="$2"
  if [ -d "$RUN/workplace" ]; then
    mv "$RUN/workplace" "$RUN/archive-serial-$(printf '%05d' "$LAST_SERIAL")"
  fi
  (cd "$ROOT" && uv run python dev/harness_spike/sister_prompt.py \
    --serial "$serial" --seed "$seed" --username "$MC_USER" --minutes "$MINUTES" --run "$RUN") >/dev/null
  LAST_SERIAL="$serial"
  log "node $(printf '%05d' "$serial") born (seed $seed), workplace ready"
}

LAST_SERIAL="$SERIAL"
SEED=$(( (RANDOM << 15) ^ RANDOM ^ $$ ))
birth_node "$SERIAL" "$SEED"

# Bind source must be a Windows path for Docker Desktop (the daemon resolves it, not the shell).
export SPIKE_RUN_DIR="$(cygpath -w "$RUN/workplace" 2>/dev/null || echo "$RUN/workplace")"
export SPIKE_MINUTES="$MINUTES"

log "starting the stack (mc-server, body-1, mind-1)…"
(cd "$ROOT" && "${COMPOSE[@]}" up -d --build mc-server body-1 mind-1) || { log "compose up failed"; exit 1; }
sleep 5

log "starting the $HARNESS harness…"
(cd "$ROOT" && "${COMPOSE[@]}" --profile "$HARNESS" up -d --build "$PROFILE_SERVICE") || { log "harness up failed"; exit 1; }

# --- sandbox check (review P1-1): only the MCP port may be reachable -------------------
sleep 8
HARNESS_CT="$(docker ps --format '{{.Names}}' | grep -E "harness" | head -1)"
{
  echo "body-1:8700 (body MCP): $(docker exec "$HARNESS_CT" timeout 3 bash -c 'echo > /dev/tcp/body-1/8700' >/dev/null 2>&1 && echo open || echo closed)"
  echo "body-1:25333 (uc py4j): $(docker exec "$HARNESS_CT" timeout 3 bash -c 'echo > /dev/tcp/body-1/25333' >/dev/null 2>&1 && echo open || echo closed)"
  echo "mc-server:25575 (RCON): $(docker exec "$HARNESS_CT" timeout 3 bash -c 'echo > /dev/tcp/mc-server/25575' >/dev/null 2>&1 && echo open || echo closed)"
} | tee "$RUN/logs/sandbox-check.txt"

START=$(date +%s)
NODE_START=$START
DEATHS=0
KILLED=0
LAST_MCP_LINES=0
emergency_will() {
  local cause="$1"
  cat > "$RUN/workplace/will.md" <<EOF
---
emergency: true
cause: $cause
serial: $(printf '%05d' "$LAST_SERIAL")
---

Аварийное завещание узла $(printf '%05d' "$LAST_SERIAL"): сессия харнесса не оставила завещания
(таймаут после смерти). Следующему узлу: смотри вокруг сразу после возрождения и не доверяй
тишине — она уже один раз кого-то убила.
EOF
  log "emergency will written (cause: $cause)"
}

death_seen() {
  local n
  n="$(docker exec "$(docker ps -a --format '{{.Names}}' | grep -E 'mind-1' | head -1)" \
    sh -c 'wc -l < /spike-run/mind-calls.jsonl 2>/dev/null' 2>/dev/null || echo 0)"
  awk -v skip="$LAST_MCP_LINES" 'NR > skip && /"woke_by": "game.death"/ {found=1} END {exit !found}' \
    <(docker exec "$(docker ps -a --format '{{.Names}}' | grep -E 'mind-1' | head -1)" cat /spike-run/mind-calls.jsonl 2>/dev/null)
}

log "watching the run for ${MINUTES} minutes…"
NEXT_STATS=$(( START + 10 ))
while :; do
  NOW=$(date +%s)
  ELAPSED=$(( NOW - START ))
  [ "$ELAPSED" -ge $(( MINUTES * 60 )) ] && break

  # docker stats sample every 10 s
  if [ "$NOW" -ge "$NEXT_STATS" ]; then
    docker stats --no-stream --format "{{.Name}} $(date +%H:%M:%S) {{.MemUsage}} {{.CPUPerc}}" 2>/dev/null \
      | grep -E 'harness|mind-1' >> "$RUN/stats.log" || true
    NEXT_STATS=$(( NOW + 10 ))
  fi

  # cost check around minute 3 (review P1-7)
  if [ "$ELAPSED" -ge 180 ] && [ ! -f "$RUN/cost-check.txt" ] && [ "$HARNESS" = opencode ]; then
    docker logs "$HARNESS_CT" 2>/dev/null | uv run --no-project python -c '
import json, sys
total = 0.0
for line in sys.stdin:
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    c = (ev.get("cost") or {}) if isinstance(ev, dict) else {}
    total += float(c.get("total") or 0)
print(f"cost after ~3 min: ${total:.3f} (projected 15 min: ${total*5:.3f})")' > "$RUN/cost-check.txt" 2>&1 || true
    cat "$RUN/cost-check.txt"
  fi

  # death handling
  if death_seen; then
    DEATHS=$(( DEATHS + 1 ))
    log "death detected (#$DEATHS); waiting up to 90 s for will.md…"
    WAITED=0
    while [ ! -s "$RUN/workplace/will.md" ] && [ "$WAITED" -lt 90 ]; do
      sleep 5; WAITED=$(( WAITED + 5 ))
    done
    if [ ! -s "$RUN/workplace/will.md" ]; then
      emergency_will "death, no agent will within ${WAITED}s"
    else
      log "agent will.md received"
    fi
    LAST_MCP_LINES="$(docker exec "$(docker ps -a --format '{{.Names}}' | grep -E 'mind-1' | head -1)" \
      sh -c 'wc -l < /spike-run/mind-calls.jsonl 2>/dev/null' 2>/dev/null || echo 0)"
    LEFT=$(( MINUTES * 60 - ( $(date +%s) - START ) ))
    if [ "$LEFT" -gt 180 ]; then
      SERIAL=$(( SERIAL + 1 ))
      SEED=$(( (RANDOM << 15) ^ RANDOM ^ SERIAL ))
      birth_node "$SERIAL" "$SEED"
      docker restart "$HARNESS_CT" >/dev/null && log "harness restarted for the next node"
      NODE_START=$(date +%s)
    else
      log "less than 3 minutes left; staying with the respawned body"
    fi
  fi

  # RCON /kill at minute 10 of the current node if it has not died yet (review P1-5)
  NODE_ELAPSED=$(( $(date +%s) - NODE_START ))
  if [ "$NODE_ELAPSED" -ge 600 ] && [ "${LAST_KILL_SERIAL:-0}" != "$SERIAL" ]; then
    log "minute 10 of node $SERIAL and no death — RCON /kill"
    if (cd "$ROOT" && "${COMPOSE[@]}" exec -T mc-server rcon-cli "/kill $MC_USER") >/dev/null 2>&1; then
      LAST_KILL_SERIAL="$SERIAL"
    else
      log "rcon-cli failed (check manually)"
    fi
  fi

  sleep 2
done

log "deadline reached; stopping the harness container (the stand stays up)"
docker stop "$HARNESS_CT" >/dev/null 2>&1 || true

docker logs "$HARNESS_CT" > "$RUN/logs/harness.log" 2>&1 || true
MIND_CT="$(docker ps -a --format '{{.Names}}' | grep -E 'mind-1' | head -1)"
docker exec "$MIND_CT" cat /spike-run/mind-calls.jsonl > "$RUN/logs/mcp-calls.jsonl" 2>/dev/null || true
docker logs "$(docker ps -a --format '{{.Names}}' | grep -E 'mc-server' | head -1)" > "$RUN/logs/mc-server.log" 2>&1 || true

log "summary:"
(cd "$ROOT" && uv run python dev/harness_spike/summarize.py \
  --harness "$HARNESS" --minutes "$MINUTES" \
  --run "$(cygpath -w "$RUN" 2>/dev/null || echo "$RUN")") || log "summarize failed; logs are in $RUN"
log "done: $RUN"
