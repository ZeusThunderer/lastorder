#!/usr/bin/env bash
# Spectator camera: Xvfb + PulseAudio null sink + a vanilla Fabric client that joins by quick play.
# The game dir is rewritten on every start, like the body's (docker/body/entrypoint.sh).
set -euo pipefail
: "${MC_USERNAME:?MC_USERNAME is required (camera account, e.g. LoCamera)}"
: "${MC_SERVER:?MC_SERVER is required (host:port)}"

GAME=/data/game
mkdir -p "$GAME/mods" /data/HeadlessMC /footage
rm -f "$GAME"/mods/*.jar
# fabric-api only: no unionclef, so no service lines, overlays or py4j on the camera
cp /opt/body/mods/fabric-api-*.jar "$GAME/mods/"

# High detail for the picture; chat hidden (no raw nicks or [Alto Clef] lines in frame);
# music off, game sounds on. The HUD is hidden with F1 by `cam hud off` after the join.
cat > "$GAME/options.txt" <<OPT
pauseOnLostFocus:false
onboardAccessibility:false
skipMultiplayerWarning:true
joinedFirstServer:true
tutorialStep:none
renderDistance:${CAM_RENDER_DISTANCE:-10}
simulationDistance:5
maxFps:${CAM_MAX_FPS:-30}
graphicsMode:1
ao:true
renderClouds:"true"
entityShadows:true
biomeBlendRadius:2
mipmapLevels:4
fov:${CAM_FOV:-0.0}
chatVisibility:2
narrator:0
soundCategory_master:1.0
soundCategory_music:0.0
soundCategory_ambient:1.0
OPT

cat > /data/HeadlessMC/config.properties <<PROPS
hmc.offline=true
hmc.offline.username=${MC_USERNAME}
hmc.gamedir=${GAME}
hmc.mcdir=/data/.minecraft
hmc.java.versions=/opt/java/openjdk/bin/java
hmc.auto.download.java=false
hmc.jline.enabled=false
hmc.jvmargs=-Xmx${BODY_XMX} -Xms512M
hmc.exit.on.failed.command=true
# offline + no xvfb check = HeadlessMC patches rendering out ("game will start in headless mode")
hmc.check.xvfb=true
hmc.gameargs=--width ${CAM_W} --height ${CAM_H} --quickPlayMultiplayer ${MC_SERVER}
PROPS

# Display and audio sink for ffmpeg (x11grab :99, pulse cam.monitor).
Xvfb :99 -screen 0 "${CAM_W}x${CAM_H}x24" -nolisten tcp &
# OpenAL (LWJGL's openal-soft) finds pulse only through an explicit socket; without it
# the game falls back to "Null Output" and the recording is silent.
export PULSE_SERVER=unix:/tmp/pulse.sock ALSOFT_DRIVERS=pulse
pulseaudio -D --exit-idle-time=-1 --disallow-exit -n   --load="module-native-protocol-unix socket=/tmp/pulse.sock auth-anonymous=1"   --load="module-null-sink sink_name=cam" --load="module-always-sink" 2>/dev/null || echo "camera: no pulseaudio, video only"
sleep 1
pactl set-default-sink cam 2>/dev/null || true

cd /data
exec java -Duser.home=/data -jar /opt/body/headlessmc-launcher.jar --command launch "fabric:${MC_VERSION}"
