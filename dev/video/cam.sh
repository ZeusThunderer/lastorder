#!/usr/bin/env bash
# Operator-side control of the spectator camera (T-067). Run from the repo root in Git Bash.
#   bash dev/video/cam.sh up                 build and start the camera (docker/compose.camera.yml)
#   bash dev/video/cam.sh ready              spectator mode + HUD off (after the client joined)
#   bash dev/video/cam.sh follow LastOrder02 first-person view of a slot (re-run after her death; tp ends it)
#   bash dev/video/cam.sh third              toggle F5 perspective while following
#   bash dev/video/cam.sh tp X Y Z [YAW PITCH] wide shot from a fixed point
#   bash dev/video/cam.sh shot NAME          PNG frame -> dev/video/out/footage/NAME.png
#   bash dev/video/cam.sh rec NAME [SECS]    start recording (video + game sound)
#   bash dev/video/cam.sh stop               stop recording and copy every file out
#   bash dev/video/cam.sh pull               copy /footage into dev/video/out/footage/
#   bash dev/video/cam.sh where LastOrder02  position of a slot (for tp)
#   bash dev/video/cam.sh down               stop the camera
# RCON goes through mc-server's own rcon-cli; the camera container never holds the password.
set -euo pipefail
export MSYS_NO_PATHCONV=1
CAM_USER="${CAMERA_USERNAME:-LoCamera}"
CAM_CT=lastorder-camera-1
MC_CT=lastorder-mc-server-1
OUT=dev/video/out/footage
COMPOSE=(docker compose -p lastorder -f docker/compose.yml -f docker/compose.camera.yml --profile camera)
rcon() { docker exec "$MC_CT" rcon-cli "$@"; }
cam() { docker exec "$CAM_CT" cam "$@"; }
pull() { mkdir -p "$OUT"; docker cp "$CAM_CT:/footage/." "$OUT/"; ls -la "$OUT"; }

case "${1:-}" in
  up)     "${COMPOSE[@]}" up -d --build --no-deps camera ;;
  down)   "${COMPOSE[@]}" stop camera ;;
  ready)  rcon gamemode spectator "$CAM_USER"; cam key F1 ;;
  follow) rcon spectate "$2" "$CAM_USER" ;;
  third)  cam key F5 ;;
  tp)     rcon tp "$CAM_USER" "$2" "$3" "$4" "${5:-0}" "${6:-20}" ;;
  where)  rcon data get entity "$2" Pos ;;
  shot)   cam shot "$2"; pull >/dev/null; echo "$OUT/$2.png" ;;
  rec)    cam rec "$2" "${3:-}" ;;
  stop)   cam stop; pull ;;
  pull)   pull ;;
  *)      sed -n '2,15p' "$0"; exit 2 ;;
esac
