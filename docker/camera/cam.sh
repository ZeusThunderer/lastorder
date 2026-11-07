#!/usr/bin/env bash
# In-container camera control (called by dev/video/cam.sh through docker exec).
#   cam shot NAME          one PNG frame -> /footage/NAME.png
#   cam rec NAME [SECS]    video+audio -> /footage/NAME.mp4 (background; SECS caps it)
#   cam stop               stop the running recording
#   cam key KEY            send a key to the game (F1 hides the HUD, F5 changes perspective)
#   cam ls                 list /footage
set -euo pipefail
export DISPLAY=:99 PULSE_SERVER=unix:/tmp/pulse.sock
F=/footage
running() { [ -f /tmp/rec.pid ] && [ -d "/proc/$(cat /tmp/rec.pid)" ] && ! grep -q '^[^ ]* ([^)]*) Z' "/proc/$(cat /tmp/rec.pid)/stat"; }
case "${1:-}" in
  shot)
    ffmpeg -loglevel error -y -f x11grab -video_size "${CAM_W}x${CAM_H}" -i :99 -frames:v 1 "$F/$2.png"
    echo "$F/$2.png" ;;
  rec)
    running && { echo "already recording"; exit 1; }
    cap=(); [ -n "${3:-}" ] && cap=(-t "$3")
    audio=(); pactl info >/dev/null 2>&1 && audio=(-f pulse -i cam.monitor)
    nohup ffmpeg -loglevel error -y -f x11grab -framerate "${CAM_REC_FPS:-30}" -video_size "${CAM_W}x${CAM_H}" -i :99 \
      "${audio[@]}" "${cap[@]}" -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
      -c:a aac -b:a 160k -movflags +faststart "$F/$2.mp4" >"/tmp/rec-$2.log" 2>&1 &
    echo $! > /tmp/rec.pid
    echo "recording $F/$2.mp4 (pid $!)" ;;
  stop)
    [ -f /tmp/rec.pid ] || { echo "not recording"; exit 0; }
    kill -INT "$(cat /tmp/rec.pid)" 2>/dev/null || true
    for _ in $(seq 20); do running || break; sleep 0.5; done
    rm -f /tmp/rec.pid; echo stopped ;;
  key)
    xdotool mousemove "$((CAM_W/2))" "$((CAM_H/2))" key "$2" ;;
  ls) ls -la "$F" ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
