#!/usr/bin/env bash
# Restart one slot in the order the py4j bridge survives (T-055, T-052 a/b):
#   1. stop mind-N (SIGTERM: uvicorn down, C1 down, bridge closed gracefully —
#      a SIGKILLed python partner leaves uc's Java gateway dead forever);
#   2. recreate body-N (a fresh container reconnects to the server at once;
#      a plain `restart` of a worn body does not — T-053);
#   3. wait for the body's healthcheck (uc's gateway listening, T-055);
#   4. start mind-N — it joins the body's NEW network namespace.
# dev/slot_restart.sh --all touches every slot the same way.
#
# Usage:  bash dev/slot_restart.sh N [--dry-run] [--project NAME]
#         bash dev/slot_restart.sh all [--dry-run] [--project NAME]
# N is the slot number (1..3). --project/-p selects a parallel stand's compose
# project (default: the compose file's own, `lastorder`); the stand must be up.
#
# WARNING (T-054 review note): a mid-life mind restart loses the armed life
# clock and the stale-dead gate (both live in the mind's memory). Restart a
# slot while its node-N sandbox is NOT running; the script refuses otherwise
# without --force.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$HERE/../docker/compose.yml"
GRACE_S=420     # body health: warm JVM ~60 s; a cold volume downloads assets first
DRY_RUN=0
FORCE=0
PROJECT_ARGS=()
SLOTS=()

usage() { echo "usage: bash dev/slot_restart.sh N|all [--dry-run] [--force] [--project NAME]" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        all) SLOTS=(1 2 3) ;;
        [1-3]) SLOTS+=("$1") ;;
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
        --project|-p) shift; [ "${1:-}" ] || usage; PROJECT_ARGS=(-p "$1") ;;
        *) usage ;;
    esac
    shift
done
[ ${#SLOTS[@]} -gt 0 ] || usage

compose() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "docker compose ${PROJECT_ARGS[*]:+${PROJECT_ARGS[*]} }-f docker/compose.yml $*"
    else
        docker compose "${PROJECT_ARGS[@]}" -f "$COMPOSE_FILE" "$@"
    fi
}

node_running() {  # the sandbox of slot N: an up mid-life session dies with the mind
    [ "$DRY_RUN" = 1 ] && return 1
    [ "$(docker compose "${PROJECT_ARGS[@]}" -f "$COMPOSE_FILE" ps --status running -q "node-$1" | wc -l)" -gt 0 ]
}

cd "$HERE/.."

for n in "${SLOTS[@]}"; do
    if node_running "$n" && [ "$FORCE" != 1 ]; then
        echo "slot_restart: node-$n is RUNNING — its sister session dies with the mind," >&2
        echo "  and the armed life clock is lost (T-054 note). Use --force to restart anyway." >&2
        exit 1
    fi
done

for n in "${SLOTS[@]}"; do
    echo "== slot $n: mind stop (SIGTERM, graceful bridge close)"
    compose stop "mind-$n"
    echo "== slot $n: body recreate (fresh container, fresh netns)"
    compose up -d --force-recreate --no-deps "body-$n"
    echo "== slot $n: wait for the gateway healthcheck"
    if [ "$DRY_RUN" != 1 ]; then
        bash dev/wait_for.sh "$GRACE_S" --healthy "body-$n" || {
            echo "slot_restart: body-$n not healthy in ${GRACE_S}s — not starting the mind" >&2
            exit 1
        }
    else
        echo "bash dev/wait_for.sh $GRACE_S --healthy body-$n"
    fi
    echo "== slot $n: mind start (joins the new netns; compose gates on body healthy)"
    compose up -d "mind-$n"
done
echo "slot_restart: done (${SLOTS[*]})"
