#!/usr/bin/env bash
# Prepare the game dir and launch the client. Every value the bot depends on is rewritten on each start,
# so a body is reproducible from env alone. MC_USERNAME is the slot account (LastOrder01), not a node.
set -euo pipefail
: "${MC_USERNAME:?MC_USERNAME is required (slot account, e.g. LastOrder01)}"
: "${MC_SERVER:?MC_SERVER is required (host[:port])}"

GAME=/data/game
mkdir -p "$GAME/mods" "$GAME/altoclef" /data/HeadlessMC
rm -f "$GAME"/mods/*.jar
cp /opt/body/mods/*.jar "$GAME/mods/"

# unionclef: connect by itself, keep the py4j bridge on localhost (the mind shares this network namespace),
# no MCP (it binds 0.0.0.0), no idle behaviour of its own: the mind decides.
cat > "$GAME/altoclef/altoclef_settings.json" <<JSON
{
  "autoConnectServer": "${MC_SERVER}",
  "autoReconnect": true,
  "autoRespawn": ${BODY_AUTO_RESPAWN:-true},
  "idleCommand": "",
  "deathCommand": "",
  "pythonGatewayPort": 25333,
  "mcpEnabled": false
}
JSON

# options.txt is rewritten on every start like every other file: a volume from an
# older stand must not pin stale render settings. The body does not need to see far,
# and a low fps cap is plenty for uc. simulationDistance stays at the vanilla
# minimum: the client rejects lower values and falls back to the default 12 (T-003).
cat > "$GAME/options.txt" <<OPT
pauseOnLostFocus:false
onboardAccessibility:false
renderDistance:${BODY_RENDER_DISTANCE:-2}
simulationDistance:${BODY_SIM_DISTANCE:-5}
maxFps:${BODY_MAX_FPS:-10}
soundCategory_master:0.0
narrator:0
OPT

LWJGL=""
if [ "$BODY_RENDER" = "headless" ]; then
  LWJGL="-lwjgl"
fi

# Body camera (T-063, ADR-045 §9): BODY_CAMERA=1 renders the game on a FIXED Xvfb
# display and streams the screen to the media service as rtsp://media:8554/<username>.
# The display number is fixed (:99) because ffmpeg must know it — xvfb-run -a would
# pick a free one and never tell. ffmpeg runs in a restart loop, so a media restart
# neither takes the body down nor loses the stream for good.
CAMERA_SIZE="${BODY_CAMERA_SIZE:-854x480}"
if [ "${BODY_CAMERA:-}" = "1" ]; then
  LWJGL=""   # the camera needs the real render path, whatever BODY_RENDER said
  Xvfb :99 -screen 0 "${CAMERA_SIZE}x24" -nolisten tcp &
  export DISPLAY=:99
  (
    set +e   # the loop's whole job is to outlive ffmpeg exits (a media restart is expected)
    while true; do
      ffmpeg -hide_banner -loglevel error -draw_mouse 0 \
        -f x11grab -framerate "${BODY_CAMERA_FPS:-10}" -video_size "$CAMERA_SIZE" -i :99+0,0 \
        -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p -g 20 \
        -b:v "${BODY_CAMERA_BITRATE:-1M}" -maxrate "${BODY_CAMERA_BITRATE:-1M}" -bufsize 2M \
        -f rtsp -rtsp_transport tcp "rtsp://media:8554/${MC_USERNAME}"
      echo "camera: ffmpeg exited (rc=$?), restarting in 3 s" >&2
      sleep 3
    done
  ) &
fi

# The link to the mind (ADR-050): the mind is a peer container now, not a tenant of
# this network namespace. uc's py4j gateway binds 127.0.0.1:25333 and dials its
# callback at 127.0.0.1:25334 (AltoClef.java:177; uc is not ours to change), so two
# relays carry the bridge across the slot's private link network:
#   mind  -> BODY_RELAY_PORT (only MIND_LINK_IP may connect) -> uc's gateway;
#   uc's callback dial 127.0.0.1:25334 -> MIND_HOST:25334 (the mind's callback server).
# Each rides a restart loop like the camera's ffmpeg; init (tini) reaps them.
if [ -n "${MIND_HOST:-}" ]; then
  RELAY_PORT="${BODY_RELAY_PORT:-25433}"
  RANGE=""
  if [ -n "${MIND_LINK_IP:-}" ]; then RANGE=",range=${MIND_LINK_IP}/32"; fi
  (
    set +e
    while true; do
      socat "TCP-LISTEN:${RELAY_PORT},reuseaddr,fork${RANGE}" TCP:127.0.0.1:25333
      echo "relay: gateway relay exited (rc=$?), restarting in 1 s" >&2
      sleep 1
    done
  ) &
  (
    set +e
    while true; do
      socat TCP-LISTEN:25334,bind=127.0.0.1,reuseaddr,fork "TCP:${MIND_HOST}:25334"
      echo "relay: callback relay exited (rc=$?), restarting in 1 s" >&2
      sleep 1
    done
  ) &
fi

cat > /data/HeadlessMC/config.properties <<PROPS
hmc.offline=true
hmc.offline.username=${MC_USERNAME}
hmc.gamedir=${GAME}
hmc.mcdir=/data/.minecraft
hmc.java.versions=/opt/java/openjdk/bin/java
hmc.auto.download.java=false
hmc.jline.enabled=false
# Native-memory caps: metaspace and the code cache are uncapped by default, and glibc
# keeps up to 8 arenas per thread (MALLOC_ARENA_MAX comes from compose); with Xmx ≤ 1G
# they decide whether the body fits the T-003 idle budget.
hmc.jvmargs=-Xmx${BODY_XMX} -Xms${BODY_XMS:-256M} -XX:MaxMetaspaceSize=256M -XX:ReservedCodeCacheSize=128M
hmc.exit.on.failed.command=true
PROPS
# An OFFLINE account makes HeadlessMC force its no-op LWJGL ("You are offline, game
# will start in headless mode!") unless it finds Xvfb in `ps aux` — and it only looks
# when hmc.check.xvfb=true (default false). Without this the camera streams a black
# screen [проверено: T-069, HMC 2.10.0 LaunchOptions/XvfbService bytecode, body-2].
if [ "${BODY_CAMERA:-}" = "1" ] || [ "$BODY_RENDER" = "xvfb" ]; then
  echo "hmc.check.xvfb=true" >> /data/HeadlessMC/config.properties
fi

cd /data
# Java takes user.home from passwd, not $HOME: without this the 0.5 GB .minecraft lands outside the volume.
# Fabric Loader pin (T-062): with FABRIC_LOADER set (compose default = the server's pin)
# HeadlessMC gets the fabric:<mc>:<loader> form — verified on HMC 2.10.0 ("Installing
# 1.21.11 with fabric 0.20.1"); the bare fabric:<mc> form would keep HMC's own pick,
# the latest loader at boot time.
FABRIC_TARGET="fabric:${MC_VERSION}"
if [ -n "${FABRIC_LOADER:-}" ]; then
  FABRIC_TARGET="fabric:${MC_VERSION}:${FABRIC_LOADER}"
fi
CMD=(java -Duser.home=/data -jar /opt/body/headlessmc-launcher.jar --command launch "$FABRIC_TARGET" $LWJGL)
if [ "$BODY_RENDER" = "xvfb" ]; then
  exec xvfb-run -a -s "-screen 0 854x480x24" "${CMD[@]}"
fi
exec "${CMD[@]}"
