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

# Voice (ADR-055): BODY_VOICE=1 gives this body Simple Voice Chat and a virtual microphone.
# PulseAudio holds two null sinks: lo_out, the default — the game's own sound and the voice
# of others go nowhere — and lo_voice, where the mind's speech is played; the monitor of
# lo_voice is remapped into lo_mic, the default source, which SVC records as its microphone.
# Silence on lo_voice is below the activation threshold, so SVC sends only speech.
if [ "${BODY_VOICE:-}" = "1" ]; then
  cp /opt/body/mods-voice/*.jar "$GAME/mods/"
  export XDG_RUNTIME_DIR=/tmp/xdg PULSE_RUNTIME_PATH=/tmp/pulse
  mkdir -p "$XDG_RUNTIME_DIR" "$PULSE_RUNTIME_PATH"
  chmod 700 "$XDG_RUNTIME_DIR"
  # -n: no default.pa (no hardware probing in a container); the native socket is all we need
  pulseaudio --daemonize=yes -n --exit-idle-time=-1 --disallow-exit --disable-shm=yes \
    --load=module-native-protocol-unix
  for _ in $(seq 50); do pactl info >/dev/null 2>&1 && break; sleep 0.1; done
  pactl load-module module-null-sink sink_name=lo_out rate=48000 channels=2 >/dev/null
  pactl load-module module-null-sink sink_name=lo_voice rate=48000 channels=1 >/dev/null
  pactl load-module module-remap-source master=lo_voice.monitor source_name=lo_mic >/dev/null
  pactl set-default-sink lo_out
  pactl set-default-source lo_mic
  # Java Sound (SVC's fallback microphone) reads ALSA: route ALSA's default to pulse
  printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' > /etc/asound.conf
  echo "voice: pulse up, default source $(pactl get-default-source)" >&2

  # SVC client config, rewritten on every start like options.txt. Voice activation on a
  # pure level threshold: no denoiser, no AGC, no VAD model between the TTS and the wire.
  mkdir -p "$GAME/config/voicechat"
  cat > "$GAME/config/voicechat/voicechat-client.properties" <<SVC
config_version=2
onboarding_finished=true
disabled=false
muted=false
microphone=
speaker=
microphone_activation_type=VOICE
voice_activation_threshold=${BODY_VOICE_THRESHOLD_DB:--50.0}
voice_activity_detection=false
voice_deactivation_delay=${BODY_VOICE_TAIL:-25}
denoiser=false
automatic_gain_control=false
microphone_gain=${BODY_VOICE_GAIN_DB:-0.0}
java_microphone_implementation=${BODY_VOICE_JAVA_MIC:-false}
SVC

  # SVC needs a real OpenAL; the -lwjgl stub has none: a voice body renders on Xvfb
  if [ "$BODY_RENDER" = "headless" ]; then
    BODY_RENDER=xvfb
    echo "voice: BODY_RENDER headless → xvfb (SVC needs real LWJGL/OpenAL)" >&2
  fi
fi

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
  BIND=""
  if [ -n "${BODY_LINK_IP:-}" ]; then BIND="bind=${BODY_LINK_IP},"; fi   # the link only, never default/media
  (
    set +e
    while true; do
      socat "TCP4-LISTEN:${RELAY_PORT},${BIND}reuseaddr,fork${RANGE}" TCP:127.0.0.1:25333
      echo "relay: gateway relay exited (rc=$?), restarting in 1 s" >&2
      sleep 1
    done
  ) &
  (
    set +e
    while true; do
      socat TCP4-LISTEN:25334,bind=127.0.0.1,reuseaddr,fork "TCP:${MIND_HOST}:25334"
      echo "relay: callback relay exited (rc=$?), restarting in 1 s" >&2
      sleep 1
    done
  ) &
  # The voice inlet (ADR-055): the mind streams one line of PCM s16le 48 kHz mono, pacat
  # plays it into lo_voice in real time. On the mind's half-close pacat drains and exits,
  # socat closes the connection — the mind's "the line is said". One line at a time
  # (max-children=1); -t keeps the connection open past the half-close until pacat is done.
  if [ "${BODY_VOICE:-}" = "1" ]; then
    VOICE_PORT="${BODY_VOICE_PORT:-25500}"
    (
      set +e
      while true; do
        socat -t 120 -T 180 "TCP4-LISTEN:${VOICE_PORT},${BIND}reuseaddr,fork,max-children=1${RANGE}" \
          EXEC:"pacat --playback --raw --format=s16le --rate=48000 --channels=1 --device=lo_voice --latency-msec=100"
        echo "voice: inlet exited (rc=$?), restarting in 1 s" >&2
        sleep 1
      done
    ) &
  fi
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
