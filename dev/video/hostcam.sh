#!/usr/bin/env bash
# GPU spectator camera on the Windows host (T-067, films 2026-11-08). Replaces the CPU camera
# container for filming: `docker run --gpus all` hangs Docker Desktop's VM on this machine.
# A plain Fabric client (fabric-api only, no unionclef) joins localhost:25565 as LoCamera;
# the window sits borderless on monitor 2 and is captured by ddagrab + h264_nvenc; game sound
# goes to the VB-Audio cable and is recorded from "CABLE Output".
#   bash dev/video/hostcam.sh up            write config, launch the client (background)
#   bash dev/video/hostcam.sh place         borderless 1920x1080 on monitor 2
#   bash dev/video/hostcam.sh key F1        send a key to the game window
#   bash dev/video/hostcam.sh shot NAME     one PNG frame of monitor 2
#   bash dev/video/hostcam.sh rec NAME [S]  record monitor 2 + cable -> dev/video/out/footage/NAME.mp4
#   bash dev/video/hostcam.sh stop          stop the recording (then fin)
#   bash dev/video/hostcam.sh fin           remux finished .mkv takes to .mp4
#   bash dev/video/hostcam.sh down          close the client
# Java 21: Prism Launcher's bundled runtime (no install). Game files: dev/video/out/hostcam (not in git).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd -W 2>/dev/null || pwd)
H="$ROOT/dev/video/out/hostcam"; F="$ROOT/dev/video/out/footage"; mkdir -p "$F"
JAVA="${HOSTCAM_JAVA:-$APPDATA/PrismLauncher/java/java-runtime-delta/bin/java.exe}"
USER_NAME="${CAMERA_USERNAME:-LoCamera}"
MON_X=2560; MON_Y=181; W=1920; HT=1080; OUT_IDX="${HOSTCAM_OUTPUT:-1}"; FPS="${HOSTCAM_FPS:-60}"
ps1() { powershell -NoProfile -ExecutionPolicy Bypass -Command "$1"; }
WIN32='Add-Type @"
using System; using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr h,int i,int v);
[DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h,IntPtr a,int x,int y,int cx,int cy,uint f);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
"@; $p = Get-Process javaw,java -ErrorAction SilentlyContinue | ? { $_.MainWindowTitle -like "Minecraft*" } | select -First 1;
if (-not $p) { throw "no Minecraft window" }; $h = $p.MainWindowHandle;'

case "${1:-}" in
  up)
    mkdir -p "$H/game/mods" "$H/HeadlessMC"
    rm -f "$H"/game/mods/*.jar; cp "$H"/body-opt/mods/fabric-api-*.jar "$H/game/mods/"
    cat > "$H/game/options.txt" <<OPT
pauseOnLostFocus:false
onboardAccessibility:false
skipMultiplayerWarning:true
joinedFirstServer:true
tutorialStep:none
renderDistance:${HOSTCAM_RD:-16}
simulationDistance:8
maxFps:${FPS}
enableVsync:false
graphicsMode:1
ao:true
renderClouds:"true"
entityShadows:true
biomeBlendRadius:3
mipmapLevels:4
fov:0.0
chatVisibility:2
narrator:0
soundDevice:"${HOSTCAM_SOUND:-OpenAL Soft on CABLE Input (VB-Audio Virtual Cable)}"
soundCategory_master:1.0
soundCategory_music:0.0
soundCategory_ambient:1.0
OPT
    # game files: HeadlessMC downloads them once (`fetch`); the launch itself is hostcam_launch.py
    [ -d "$H/.minecraft/versions" ] || "$0" fetch
    python "$ROOT/dev/video/hostcam_launch.py" "$H" "$JAVA" "$USER_NAME" "$W" "$HT" localhost:25565
    ;;
  fetch)   # one-time download of vanilla + Fabric (HeadlessMC; it then starts headless — killed)
    mkdir -p "$H/HeadlessMC"
    cat > "$H/HeadlessMC/config.properties" <<PROPS
hmc.offline=true
hmc.gamedir=$H/game
hmc.mcdir=$H/.minecraft
hmc.java.versions=$JAVA
hmc.auto.download.java=false
hmc.jline.enabled=false
PROPS
    (cd "$H" && timeout 600 "$JAVA" -jar headlessmc-launcher.jar --command launch "fabric:1.21.11" > "$H/fetch.log" 2>&1 || true) ;;
  place)
    ps1 "$WIN32 [W]::SetWindowLong(\$h,-16,0x10000000) | Out-Null; [W]::SetWindowPos(\$h,[IntPtr]::Zero,$MON_X,$MON_Y,$W,$HT,0x0020) | Out-Null; [W]::SetForegroundWindow(\$h) | Out-Null; 'placed'" ;;
  key)
    ps1 "$WIN32 [W]::SetForegroundWindow(\$h) | Out-Null; Start-Sleep -m 200; Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('{$2}')" ;;
  shot)
    ffmpeg -hide_banner -loglevel error -y -f lavfi -i "ddagrab=output_idx=${OUT_IDX}:draw_mouse=0"       -vf hwdownload,format=bgra -frames:v 1 -update 1 "$F/$2.png" && echo "$F/$2.png" ;;
  rec)
    [ -f "$H/rec.pid" ] && kill -0 "$(cat "$H/rec.pid")" 2>/dev/null && { echo "already recording"; exit 1; }
    cap=(); [ -n "${3:-}" ] && cap=(-t "$3")
    nohup ffmpeg -hide_banner -loglevel error -y \
      -f lavfi -i "ddagrab=output_idx=${OUT_IDX}:framerate=${FPS}:draw_mouse=0" \
      -f dshow -audio_buffer_size 50 -i audio="CABLE Output (VB-Audio Virtual Cable)" "${cap[@]}" \
      -c:v h264_nvenc -preset p6 -tune hq -rc vbr -cq 17 -b:v 0 -g "$FPS" \
      -c:a aac -b:a 192k "$F/$2.mkv" > "$H/rec-$2.log" 2>&1 < /dev/null &
    echo $! > "$H/rec.pid"; echo "recording $F/$2.mkv" ;;
  stop)
    [ -f "$H/rec.pid" ] || { echo "not recording"; exit 0; }
    pid=$(cat "$H/rec.pid"); wpid=$(ps -p "$pid" -o winpid= 2>/dev/null | tr -d ' ' || true)
    [ -n "$wpid" ] && taskkill //F //PID "$wpid" > /dev/null 2>&1 || kill "$pid" 2>/dev/null || true
    rm -f "$H/rec.pid"; "$0" fin ;;
  wait)  # until a capped recording ends, then fin
    [ -f "$H/rec.pid" ] && while kill -0 "$(cat "$H/rec.pid")" 2>/dev/null; do sleep 0.5; done
    rm -f "$H/rec.pid"; "$0" fin ;;
  fin)   # mkv survives a kill; remux every finished take to mp4
    for m in "$F"/*.mkv; do [ -e "$m" ] || continue; o="${m%.mkv}.mp4"
      ffmpeg -hide_banner -loglevel error -y -i "$m" -c copy -movflags +faststart "$o" && rm -f "$m" && echo "$o"; done ;;
  down)
    ps1 "Get-CimInstance Win32_Process -Filter \"Name like 'java%'\" | ? { \$_.CommandLine -match 'hostcam' } | % { Stop-Process -Id \$_.ProcessId -Force }" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
