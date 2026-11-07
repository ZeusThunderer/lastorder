#!/usr/bin/env bash
# The stand (compose project `lastorder`) is one for all worktrees: only one task uses it at a time.
# Take the lock before any `docker compose` that starts, rebuilds or restarts stand services, release it after.
#   bash dev/stand.sh lock T-NNN     # take or refresh; exit 1 with the holder if another task has it
#   bash dev/stand.sh unlock T-NNN   # release (only the holder's id releases)
#   bash dev/stand.sh status         # "free" or "T-NNN <minutes> min"
# A lock older than STAND_LOCK_STALE_MIN (default 120) minutes counts as abandoned.
set -uo pipefail
. "$(dirname "$0")/_common.sh"

LOCKF="$COMMON/lo-stand.lock"
STALE="${STAND_LOCK_STALE_MIN:-120}"
cmd="${1:-status}"; id="${2:-}"

holder() { [ -f "$LOCKF" ] && cut -d' ' -f1 "$LOCKF" | tr -d '\r\n'; }
age_min() { echo $(( ($(date +%s) - $(cut -d' ' -f2 "$LOCKF")) / 60 )); }

case "$cmd" in
  status)
    if [ -f "$LOCKF" ]; then echo "$(holder) $(age_min) min"; else echo free; fi ;;
  lock)
    check_id "$id"
    if [ -f "$LOCKF" ] && [ "$(holder)" != "$id" ]; then
      if [ "$(age_min)" -ge "$STALE" ]; then
        echo "stand: removing stale lock of $(holder) ($(age_min) min)" >&2
      else
        echo "stand: busy — $(holder) holds it for $(age_min) min; do the work that needs no stand, retry later" >&2
        exit 1
      fi
    fi
    echo "$id $(date +%s)" > "$LOCKF" && echo "stand: locked by $id" ;;
  unlock)
    check_id "$id"
    if [ "$(holder)" = "$id" ]; then rm -f "$LOCKF" && echo "stand: released by $id"
    else echo "stand: not held by $id ($(holder || echo free))" >&2; fi ;;
  *) die "usage: dev/stand.sh lock|unlock T-NNN | status" ;;
esac
