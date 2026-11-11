#!/usr/bin/env bash
# Apply the sister skin to all three bodies on a running stand (T-061).
# The skinrestorer mod on mc-server serves the operator's sister.png through its
# collection provider (docker/mc-server/skinrestorer/config.json names the source
# "sister"); this script points every slot account at it with one rcon command.
#   bash dev/apply_skin.sh [--dry-run] [--project NAME] [-f <compose.yml>]
# Run from the repo (or worktree) root. The mod saves the skin per player and
# reapplies it on every join, so an offline body picks it up at the next join.
set -uo pipefail
# Self-contained on purpose (no dev/_common.sh, no git): the script must run
# under any bash — including a WSL one that cannot follow a worktree's
# Windows-style .git pointer.

# Git Bash rewrites container paths (/skins, /data) into Windows ones for
# docker.exe; everything below rides `docker compose exec` instead, but keep
# the export so any added path argument stays a container path.
export MSYS_NO_PATHCONV=1

usage() { sed -n '2,9p' "$0" >&2; exit 2; }
die() { echo "apply_skin: $*" >&2; exit 2; }

SLOTS="LastOrder01 LastOrder02 LastOrder03"
SEED=sister   # the named collection source in the skinrestorer config
compose=docker/compose.yml
project=lastorder
dry=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) dry=1 ;;
    --project) project="${2:?}"; shift ;;
    -f) compose="${2:?}"; shift ;;
    *) usage ;;
  esac
  shift
done

[ -f "$compose" ] || die "compose file not found: $compose — run from the repo root"

cmd=(docker compose -f "$compose" -p "$project")

# one rcon `list` answers the online question for all three slots at once
online=""
if [ "$dry" = 0 ]; then
  command -v docker >/dev/null || die "docker CLI not found"
  online="$("${cmd[@]}" exec -T mc-server rcon-cli list)" \
    || die "rcon list failed — is the mc-server of project '$project' up?"
fi

for slot in $SLOTS; do
  if [ "$dry" = 1 ]; then
    printf '# '; printf '%q ' "${cmd[@]}" exec -T mc-server rcon-cli skin set collection "$SEED" "$slot"; printf '\n'
    continue
  fi
  case "$online" in
    *"$slot"*) note="online" ;;
    *) note="offline — the saved skin shows at the next join" ;;
  esac
  echo "apply_skin: $slot ($note)"
  "${cmd[@]}" exec -T mc-server rcon-cli skin set collection "$SEED" "$slot" \
    || die "skin set failed for $slot"
done
[ "$dry" = 1 ] && echo "apply_skin: dry run — nothing was sent"
exit 0
