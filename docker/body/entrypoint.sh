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
