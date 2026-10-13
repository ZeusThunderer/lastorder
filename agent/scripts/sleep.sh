#!/usr/bin/env bash
# Set your own next wake, in minutes.
#
#   sleep.sh <minutes>
#
# The character decides this, not a fixed schedule. Somewhere busy and interesting means come back
# soon; nothing happening means come back later. A fixed interval either wastes money on an empty
# room or misses everything that mattered.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: sleep.sh <minutes>" >&2; exit 2; }
me="${AGENT_NAME:?AGENT_NAME is not set; refusing to guess whose memory to write}"
[[ "$me" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "bad AGENT_NAME: $me" >&2; exit 2; }
[ -f "agent/$me/PERSONA.md" ] || { echo "no character agent/$me (run from the repo root)" >&2; exit 2; }
[[ "$1" =~ ^[0-9]+$ ]] || { echo "minutes must be a whole number: $1" >&2; exit 2; }
mkdir -p "agent/$me/state"
printf '%s\n' "$1" > "agent/$me/state/sleep.txt"
echo "next wake in $1 minutes"
