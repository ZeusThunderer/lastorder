#!/usr/bin/env bash
# Cinematic shot list for the spectator camera (T-067 trailer). Each line of a shot file:
#   NAME SECONDS SLOT MODE key=value...
# MODE: orbit (speed h r), dolly (v yaw pitch dy; camera starts at SLOT + start=dx,dy,dz facing SLOT),
#       track (fixed point at SLOT + start=dx,dy,dz, pans to follow), follow (dx dy dz), pov, back.
# Rigs run server-side every tick (datapack dev/video/camrig/lo_cam); every clip is its own file,
# logged with UTC start so subtitles and events can be matched to the logs.
#   bash dev/video/shoot.sh dev/video/shots.txt
set -uo pipefail
export MSYS_NO_PATHCONV=1
LIST="$1"
OUT=dev/video/out/footage; LOG="$OUT/shots.jsonl"; mkdir -p "$OUT"
rcon() { docker exec lastorder-mc-server-1 rcon-cli "$@"; }
key() { docker exec lastorder-camera-1 sh -c "export DISPLAY=:99; xdotool windowfocus --sync \$(xdotool search --name Minecraft | head -1) key $1"; }
third=0
persp() { while [ "$third" != "$1" ]; do key F5; third=$(( (third + 1) % 3 )); done; }
rcon gamemode spectator LoCamera >/dev/null
while read -r NAME SECS SLOT MODE REST; do
  case "$NAME" in ''|\#*) continue ;; esac
  rcon scoreboard players set '#mode' lo_cam 0 >/dev/null
  rcon 'tag @a remove lo_target' >/dev/null; rcon tag "$SLOT" add lo_target >/dev/null
  h=$(rcon data get entity "$SLOT" Health); case "$h" in *": 0.0f"*) echo "skip $NAME: $SLOT dead"; continue ;; esac
  cfg='speed:"0.5",h:"3",r:"6",v:"0.15",yaw:"0",pitch:"0",dy:"0",dx:"5",dz:"5"'; start="6 3 6"; p0=""
  for kv in $REST; do k=${kv%%=*}; v=${kv#*=}
    case "$k" in start) start=${v//,/ } ;; p0) p0=$v ;; *) cfg="$cfg,$k:\"$v\"" ;; esac; done
  rcon "data merge storage lo_cam:cfg {$cfg}" >/dev/null
  set -- $start
  persp 0
  case "$MODE" in
    orbit)  rcon function lo_cam:rig >/dev/null; rcon scoreboard players set '#mode' lo_cam 1 >/dev/null ;;
    dolly)  rcon "execute at $SLOT run tp LoCamera ~$1 ~$2 ~$3 facing entity $SLOT eyes" >/dev/null
            [ -n "$p0" ] && rcon "execute as LoCamera at @s run tp @s ~ ~ ~ ~ $p0" >/dev/null   # level the flight
            rcon scoreboard players set '#mode' lo_cam 2 >/dev/null ;;
    track)  rcon "execute at $SLOT run tp LoCamera ~$1 ~$2 ~$3 facing entity $SLOT eyes" >/dev/null
            rcon scoreboard players set '#mode' lo_cam 3 >/dev/null ;;
    follow) rcon scoreboard players set '#mode' lo_cam 4 >/dev/null ;;
    pov)    rcon spectate "$SLOT" LoCamera >/dev/null ;;
    back)   rcon spectate "$SLOT" LoCamera >/dev/null; persp 1 ;;
  esac
  sleep 1.5
  t=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
  docker exec lastorder-camera-1 cam rec "$NAME" "$SECS" >/dev/null
  printf '{"t":"%s","name":"%s","slot":"%s","mode":"%s","args":"%s"}\n' "$t" "$NAME" "$SLOT" "$MODE" "$REST" | tee -a "$LOG"
  sleep "$SECS"; sleep 1.5
done < "$LIST"
rcon scoreboard players set '#mode' lo_cam 0 >/dev/null; persp 0
bash dev/video/cam.sh pull >/dev/null
echo "done: $LOG"
