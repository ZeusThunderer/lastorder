#!/usr/bin/env bash
# Auto-director for the films (T-067, 2026-11-08) on the GPU host camera (dev/video/hostcam.sh).
# One continuous recording; the camera cycles shots on a heroine slot (and sometimes the others).
# Static framing on purpose: Rodchenko angles (straight down, worm's eye), locked wides, her eyes.
#   bash dev/video/films/direct.sh NAME MINUTES HERO [OTHER...]
# Shots: pov (her eyes), back (behind her, F5), wide (locked, 5 m off, facing her),
#        down (12 m above, pitch 90), low (0.3 m off the ground, 2 m off, looking up at her).
# Every cut -> dev/video/out/footage/NAME.shots.jsonl with UTC time (to match logs). Ctrl-C ends.
set -uo pipefail
export MSYS_NO_PATHCONV=1
NAME="$1"; MIN="$2"; HERO="$3"; shift 3; OTHERS=("$@")
CAM=LoCamera; SHOT_S="${SHOT_S:-20}"
OUT=dev/video/out/footage; LOG="$OUT/$NAME.shots.jsonl"; mkdir -p "$OUT"
HC=dev/video/hostcam.sh
rcon() { docker exec lastorder-mc-server-1 rcon-cli "$@"; }
now() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
log() { printf '{"t":"%s","slot":"%s","shot":"%s","note":"%s"}\n' "$(now)" "$1" "$2" "${3:-}" | tee -a "$LOG"; }
alive() { local h; h=$(rcon data get entity "$1" Health 2>/dev/null); case "$h" in *"entity data"*) case "$h" in *": 0.0f"*) return 1 ;; esac; return 0 ;; esac; return 1; }
third=0
set_third() { [ "$third" = "$1" ] && return; bash $HC key F5 >/dev/null; [ "$1" = 1 ] || bash $HC key F5 >/dev/null; third=$1; }
free_tp() {   # first open-air offset from the list wins; $1 = facing suffix
  local face="$1"; shift
  for off in "$@"; do set -- $off
    r=$(rcon "execute at $SLOT positioned ~$1 ~$2 ~$3 if block ~ ~ ~ #minecraft:air if block ~ ~1 ~ #minecraft:air run tp $CAM ~ ~ ~ $face")
    case "$r" in Teleported*) return 0 ;; esac
  done; return 1; }
place() {
  rcon "execute as $CAM run spectate" >/dev/null 2>&1   # leave any spectate
  case "$1" in
    wide) free_tp "facing entity $SLOT eyes" "5 2 5" "-5 2 5" "5 2 -5" "-5 2 -5" "7 4 0" "0 4 7" || \
          rcon "execute at $SLOT run tp $CAM ~ ~10 ~ facing entity $SLOT eyes" >/dev/null ;;
    down) free_tp "0 90" "0 12 0" "0 9 0" "0 6 0" || rcon "execute at $SLOT run tp $CAM ~ ~14 ~ 0 90" >/dev/null ;;
    low)  free_tp "facing entity $SLOT eyes" "2 0.3 0" "0 0.3 2" "-2 0.3 0" "0 0.3 -2" "2 1 2" || \
          free_tp "facing entity $SLOT eyes" "5 2 5" ;;
  esac; }
pos() { rcon data get entity "$SLOT" Pos | grep -o '\[.*\]' | tr -d '[]d '; }
moved() { local p; p=$(pos); python -c "a=[float(v) for v in '$1'.split(',')];b=[float(v) for v in '$p'.split(',')];print(1 if (a[0]-b[0])**2+(a[2]-b[2])**2>36 else 0)"; }

finish() { bash $HC stop >/dev/null 2>&1; set_third 0; log "-" end; echo "footage: $OUT/$NAME.mp4  shots: $LOG"; exit 0; }
trap finish INT TERM

rcon gamemode spectator "$CAM" >/dev/null
bash $HC rec "$NAME" >/dev/null; log "-" start "rec $NAME"
END=$(( $(date +%s) + MIN*60 ))
SHOTS=(pov wide back low pov down wide back)
i=0; k=0
while [ "$(date +%s)" -lt "$END" ]; do
  SHOT=${SHOTS[$(( i % ${#SHOTS[@]} ))]}; i=$((i+1))
  SLOT=$HERO
  if [ ${#OTHERS[@]} -gt 0 ] && [ $(( i % 4 )) = 3 ]; then SLOT=${OTHERS[$(( k % ${#OTHERS[@]} ))]}; k=$((k+1)); fi
  alive "$SLOT" || { log "$SLOT" skip dead; sleep 2; continue; }
  case "$SHOT" in
    pov|back)
      rcon spectate "$SLOT" "$CAM" >/dev/null
      [ "$SHOT" = back ] && set_third 1 || set_third 0
      log "$SLOT" "$SHOT"
      t=0; while [ $t -lt "$SHOT_S" ] && [ "$(date +%s)" -lt "$END" ]; do
        sleep 4; t=$((t+4))
        alive "$SLOT" || { log "$SLOT" died; break; }
        rcon spectate "$SLOT" "$CAM" >/dev/null   # re-bind after a respawn
      done ;;
    *)
      set_third 0; place "$SHOT"; p0=$(pos); log "$SLOT" "$SHOT"
      t=0; while [ $t -lt "$SHOT_S" ] && [ "$(date +%s)" -lt "$END" ]; do
        sleep 3; t=$((t+3))
        alive "$SLOT" || { log "$SLOT" died; break; }
        [ "$(moved "$p0")" = 1 ] && { place "$SHOT"; p0=$(pos); log "$SLOT" "$SHOT" replace; }
      done ;;
  esac
done
finish
