#!/usr/bin/env bash
# Wait for a condition inside one agent call instead of sleep/poll loops (every agent call re-sends
# its whole context). Set the agent's Bash timeout above <timeout_s>.
#   bash dev/wait_for.sh <timeout_s> -- <command…>           # until the command exits 0
#   bash dev/wait_for.sh <timeout_s> --log <container> <ERE>  # until the container's logs match
#   bash dev/wait_for.sh <timeout_s> --healthy <container>    # until healthy (running, if no healthcheck)
# Env: WAIT_INTERVAL (s, default 3); WAIT_SINCE (docker logs --since, e.g. 5m); WAIT_TAIL (default 2000).
# Prints at most 20 lines: the match or the last output. Exit 0 ok, 1 timeout, 2 usage.
set -uo pipefail

die() { echo "wait_for: $*" >&2; exit 2; }
usage="usage: wait_for.sh <timeout_s> -- <command…> | --log <container> <ERE> | --healthy <container>"

t="${1:-}"; [[ "$t" =~ ^[0-9]+$ ]] || die "$usage"
mode="${2:-}"; shift 2 2>/dev/null || die "$usage"
case "$mode" in
  --)        [ "$#" -ge 1 ] || die "$usage" ;;
  --log)     [ "$#" -eq 2 ] || die "$usage" ;;
  --healthy) [ "$#" -eq 1 ] || die "$usage" ;;
  *)         die "$usage" ;;
esac
interval="${WAIT_INTERVAL:-3}"
last=""

probe() {
  case "$mode" in
    --)
      last="$("$@" 2>&1)" ;;
    --log)
      last="$(docker logs --tail "${WAIT_TAIL:-2000}" ${WAIT_SINCE:+--since "$WAIT_SINCE"} "$1" 2>&1 \
        | grep -E -- "$2" | tail -n 20)"
      [ -n "$last" ] ;;
    --healthy)
      last="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>&1)"
      [ "$last" = healthy ] || { [ "$last" = running ] && \
        [ -z "$(docker inspect -f '{{if .State.Health}}x{{end}}' "$1" 2>/dev/null)" ]; } ;;
  esac
}

start=$SECONDS
while :; do
  if probe "$@"; then
    echo "wait_for: ok after $((SECONDS - start))s"
    [ -n "$last" ] && printf '%s\n' "$last" | tail -n 20
    exit 0
  fi
  [ $((SECONDS - start)) -ge "$t" ] && break
  sleep "$interval"
done
echo "wait_for: timeout after ${t}s"
[ -n "$last" ] && printf '%s\n' "$last" | tail -n 20
exit 1
