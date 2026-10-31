#!/usr/bin/env bash
# T-052: the board-and-chat run on the live stand, slot 1 only.
#
# One subcommand per step, so the run can be driven by hand (each step waits with
# dev/wait_for.sh, no sleep loops); `run` chains the whole choreography for the
# operator's recipe (docker-topology.md, "Board and chat"). Secrets are lifted from
# the root .env like docker/node/up.sh does and never echoed.
#
#   bash dev/board_chat_run.sh up            # stop stale slot-2/3 bodies, raise slot 1
#   bash dev/board_chat_run.sh serial        # the living sister's serial (from node-1 logs)
#   bash dev/board_chat_run.sh post <to> <text>   # a board task (to: net | <serial>)
#   bash dev/board_chat_run.sh say <text…>   # the operator's chat line via RCON tellraw
#   bash dev/board_chat_run.sh entry <id>    # the board entry with its thread
#   bash dev/board_chat_run.sh mind <grep>   # mind-1 log.jsonl excerpts (mind MCP + events)
#   bash dev/board_chat_run.sh bridge <grep> # the runner's boardlink lines (node-1 logs)
#   bash dev/board_chat_run.sh run           # up → net task → wait claim → addressed task
#                                           #   → operator line → wait answer → excerpts
# Env: OPERATOR_MC_NAME (default ZeusThunderer), HIVE_BOARD_PORT (default 18080).
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(git -C "$HERE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
[ -n "${ROOT:-}" ] || ROOT="$HERE"

OP_NAME="${OPERATOR_MC_NAME:-ZeusThunderer}"
BOARD_PORT="${HIVE_BOARD_PORT:-18080}"
BOARD="http://127.0.0.1:${BOARD_PORT}"
SLOT1=(mc-server body-1 mind-1 node-1 hive llm-gateway zai-egress)

# lift <var>: env wins, then the root .env; the value is never printed
lift() {
  local key="$1" val=""
  if [ -z "${!key:-}" ] && [ -f "$ROOT/.env" ]; then
    val="$(sed -n "s/^${key}=//p" "$ROOT/.env" | head -1 | tr -d '\r')"
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    [ -n "$val" ] && export "$key=$val"
  fi
  return 0
}
lift HIVE_OPERATOR_KEY  # up.sh's list has no such key (a T-042 gap, see the run report)
lift RCON_PASSWORD

need_key() { [ -n "${!1:-}" ] || { echo "board_chat_run: $1 is not set (env or $ROOT/.env)" >&2; exit 1; }; }

cmd="${1:-run}"; shift 2>/dev/null || true
case "$cmd" in
  up)
    # the operator's stale leftovers would share the project: slot-2/3 bodies hang
    # without their server, the pi console crash-loops — stop, do not remove
    docker stop lastorder-body-2-1 lastorder-body-3-1 >/dev/null 2>&1 || true
    docker stop lastorder-console-pi-web-ui-1 >/dev/null 2>&1 || true
    OPERATOR_MC_NAME="$OP_NAME" bash "$HERE/docker/node/up.sh" "${SLOT1[@]}"
    bash "$HERE/dev/wait_for.sh" 300 --log lastorder-mc-server-1 'RCON running'
    bash "$HERE/dev/wait_for.sh" 600 --log lastorder-node-1 'life\.birth'
    echo "board_chat_run: slot 1 is up; serial: $(bash "$0" serial)"
    ;;
  serial)
    docker logs --tail 500 lastorder-node-1-1 2>&1 \
      | grep -oE '"type": ?"life\.birth", ?"serial": ?[0-9]+' | grep -oE '[0-9]+$' | tail -1
    ;;
  post)
    need_key HIVE_OPERATOR_KEY
    to="${1:?usage: post <to: net | serial> <text>}"; text="${2:?usage: post <to> <text>}"
    curl -sS -X POST "$BOARD/board/entries" -H "X-Operator-Key: $HIVE_OPERATOR_KEY" \
      -H 'Content-Type: application/json' \
      -d "{\"kind\": \"task\", \"to\": \"$to\", \"text\": \"$text\"}"
    echo
    ;;
  say)
    need_key RCON_PASSWORD
    [ $# -ge 1 ] || { echo "usage: say <text…>" >&2; exit 1; }
    # tellraw with a plain string shows the text verbatim in every client's chat —
    # the closest RCON gets to a player line; whether uc reports it as chat.line is
    # exactly what this run records (T-052 context)
    docker exec -e RCON_PASSWORD="$RCON_PASSWORD" lastorder-mc-server-1 \
      rcon-cli tellraw @a "\"<$OP_NAME> $*\""
    ;;
  entry)
    need_key HIVE_OPERATOR_KEY
    id="${1:?usage: entry <id>}"
    curl -sS "$BOARD/board/entries/$id" -H "X-Operator-Key: $HIVE_OPERATOR_KEY"; echo
    ;;
  claimed)
    # a wait_for probe: exit 0 once the entry left `open`
    need_key HIVE_OPERATOR_KEY
    id="${1:?usage: claimed <id>}"
    curl -sS "$BOARD/board/entries/$id" -H "X-Operator-Key: $HIVE_OPERATOR_KEY" 2>/dev/null \
      | grep -qE '"status": ?"(taken|done|failed|needs_input|declined)"'
    ;;
  mind)
    grep -E "${1:-board|chat\.line}" < <(docker exec lastorder-mind-1-1 cat /mind-run/log.jsonl 2>/dev/null) \
      | tail -"${MIND_TAIL:-40}"
    ;;
  bridge)
    docker logs --tail "${NODE_TAIL:-300}" lastorder-node-1-1 2>&1 \
      | grep -E "${1:-boardlink}" | tail -40
    ;;
  run)
    bash "$0" up
    echo "== the net task"
    posted="$(bash "$0" post net "Собери 3 дубовых бревна возле спавна и закрой задачу на доске с итогом")"
    echo "$posted"
    net_id="$(printf '%s' "$posted" | grep -oE '"id": ?[0-9]+' | grep -oE '[0-9]+' | head -1)"
    [ -n "$net_id" ] || { echo "board_chat_run: no entry id in the answer" >&2; exit 1; }
    bash "$HERE/dev/wait_for.sh" 900 -- bash "$0" claimed "$net_id" || echo "not claimed in 15 min"
    serial="$(bash "$0" serial)"
    echo "== the addressed task for serial $serial"
    bash "$0" post "$serial" "Подойди к воде и скажи в чате, что видишь"
    bash "$0" say "здравствуй, как проходит смена?"
    echo "== excerpts"
    bash "$0" mind 'board|chat\.line' || true
    bash "$0" bridge || true
    ;;
  *)
    echo "usage: board_chat_run.sh up | serial | post <to> <text> | say <text…> | entry <id> | mind [ere] | bridge [ere] | run" >&2
    exit 2
    ;;
esac
