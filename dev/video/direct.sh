#!/usr/bin/env bash
# Auto-director for the spectator camera (T-067): one continuous recording while sisters live,
# the camera cycling between slots and shot types; every cut goes to a shot log with UTC time,
# so the edit can line footage up with hive events and the sisters' `say` lines.
#   bash dev/video/direct.sh NAME MINUTES [SLOTS...]     e.g. direct.sh take1 20 LastOrder01 LastOrder02
# Shot types: pov (spectate, first person), back (spectate + F5, behind her), wide (free camera
# placed in open air near her, facing her; re-placed when she walks away). Dead slot -> skipped.
# Stop early with Ctrl-C: the recording is closed and copied out.
set -uo pipefail
export MSYS_NO_PATHCONV=1
NAME="$1"; MIN="$2"; shift 2
SLOTS=("${@:-LastOrder01 LastOrder02}")
[ $# -eq 0 ] && SLOTS=(LastOrder01 LastOrder02)
CAM_USER="${CAMERA_USERNAME:-LoCamera}"
SHOT_S="${SHOT_S:-45}"          # length of one shot
OUT=dev/video/out/footage
LOG="$OUT/$NAME.shots.jsonl"
mkdir -p "$OUT"
rcon() { docker exec lastorder-mc-server-1 rcon-cli "$@"; }
key() { docker exec lastorder-camera-1 sh -c "export DISPLAY=:99; xdotool windowfocus --sync \$(xdotool search --name Minecraft | head -1) key $1"; }
now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
log() { printf '{"t":"%s","slot":"%s","shot":"%s","note":"%s"}\n' "$(now)" "$1" "$2" "${3:-}" | tee -a "$LOG"; }
alive() { rcon data get entity "$1" Health 2>/dev/null | grep -qv ': 0.0f'; }
third=0
set_third() { [ "$third" = "$1" ] && return; key F5; [ "$1" = 1 ] || key F5; third=$1; }
# F5 cycles first -> third back -> third front -> first; set_third 1 = one press, 0 = two more.
place_wide() {   # first open-air offset wins
  for off in "5 3 5" "-5 3 5" "5 3 -5" "-5 3 -5" "7 5 0" "0 5 7" "-7 5 0" "0 5 -7" "3 8 3"; do
    set -- $off
    r=$(rcon "execute at $SLOT positioned ~$1 ~$2 ~$3 if block ~ ~ ~ #minecraft:air if block ~ ~1 ~ #minecraft:air run tp $CAM_USER ~ ~ ~ facing entity $SLOT eyes")
    case "$r" in Teleported*) return 0 ;; esac
  done
  rcon "execute at $SLOT run tp $CAM_USER ~ ~12 ~ facing entity $SLOT eyes" >/dev/null; return 0
}
dist2() { # squared horizontal distance between the slot and the last wide anchor
  p=$(rcon data get entity "$SLOT" Pos | grep -o '\[.*\]' | tr -d '[]d ' )
  x=${p%%,*}; z=${p##*,}
  python -c "print(int(($x-${AX:-1e6})**2+($z-${AZ:-1e6})**2))"; }
anchor() { p=$(rcon data get entity "$SLOT" Pos | grep -o '\[.*\]' | tr -d '[]d '); AX=${p%%,*}; AZ=${p##*,}; }

finish() { docker exec lastorder-camera-1 cam stop >/dev/null 2>&1; set_third 0; log "-" end; bash dev/video/cam.sh pull >/dev/null; echo "footage: $OUT/$NAME.mp4, shots: $LOG"; exit 0; }
trap finish INT TERM

rcon gamemode spectator "$CAM_USER" >/dev/null
docker exec lastorder-camera-1 cam rec "$NAME" >/dev/null
log "-" start "rec $NAME.mp4"
END=$(( $(date +%s) + MIN*60 ))
SHOTS=(back wide pov wide)
i=0
while [ "$(date +%s)" -lt "$END" ]; do
  SLOT=${SLOTS[$(( (i / ${#SHOTS[@]}) % ${#SLOTS[@]} ))]}
  SHOT=${SHOTS[$(( i % ${#SHOTS[@]} ))]}
  i=$((i+1))
  alive "$SLOT" || { log "$SLOT" skip dead; sleep 2; continue; }
  case "$SHOT" in
    pov|back)
      rcon spectate "$SLOT" "$CAM_USER" >/dev/null
      [ "$SHOT" = back ] && set_third 1 || set_third 0
      log "$SLOT" "$SHOT"
      t=0; while [ $t -lt "$SHOT_S" ] && [ "$(date +%s)" -lt "$END" ]; do
        sleep 5; t=$((t+5))
        alive "$SLOT" || { log "$SLOT" died; break; }
        rcon spectate "$SLOT" "$CAM_USER" >/dev/null   # re-bind (a respawn is a new entity)
      done ;;
    wide)
      set_third 0
      place_wide; anchor; log "$SLOT" wide
      t=0; while [ $t -lt "$SHOT_S" ] && [ "$(date +%s)" -lt "$END" ]; do
        sleep 3; t=$((t+3))
        alive "$SLOT" || { log "$SLOT" died; break; }
        [ "$(dist2)" -gt 64 ] && { place_wide; anchor; log "$SLOT" wide replace; }
      done ;;
  esac
done
finish
