#!/usr/bin/env bash
# The operator's temporary GUI onto a running body (raid 2026-11-14): see and drive the
# slot's Minecraft client in a browser — register, the hub, a captcha link — while the
# mind, the sister and the body keep running. Nothing in the body restarts.
#
#   bash dev/gui.sh up N     # -> http://127.0.0.1:608N/vnc.html
#   bash dev/gui.sh down N
#
# How: `lastorder-gui-N` joins body-N's network namespace and reads its Xvfb :99 through
# the abstract X socket with the body's own cookie (x11vnc, port 5900 inside that
# namespace); `lastorder-gui-N-web` sits on the stand's default network, publishes
# 127.0.0.1:608N and bridges the browser (noVNC) to body-N:5900.
# While you drive, the sister's sandbox node-N is PAUSED (docker pause): she sends no
# intents, her session and life resume on `down`. Her mind (C1, clock) keeps running.
# A link clicked in the game's chat cannot open a browser in the body — choose
# "Copy to Clipboard"; it shows up in noVNC's clipboard panel (left side bar).
set -euo pipefail
export MSYS_NO_PATHCONV=1
cmd="${1:-}"; slot="${2:-}"
case "$slot" in 1|2|3) ;; *) echo "usage: $0 up|down 1|2|3" >&2; exit 2 ;; esac
body="lastorder-body-${slot}-1"; vnc="lastorder-gui-${slot}"; web="lastorder-gui-${slot}-web"
port="608${slot}"; image="lastorder-gui:dev"

node="lastorder-node-${slot}-1"
if [ "$cmd" = down ]; then
  docker rm -f "$vnc" "$web" >/dev/null 2>&1 || true
  docker unpause "$node" >/dev/null 2>&1 && echo "gui $slot: sister resumed" || true
  echo "gui $slot: down"; exit 0
fi
[ "$cmd" = up ] || { echo "usage: $0 up|down 1|2|3" >&2; exit 2; }

docker image inspect "$image" >/dev/null 2>&1 || docker build -q -t "$image" "$(dirname "$0")/gui"
[ "$(docker inspect -f '{{.State.Running}}' "$body" 2>/dev/null)" = true ] \
  || { echo "gui $slot: $body is not running" >&2; exit 1; }

# the body's X cookie (xvfb-run writes one; the camera's own Xvfb has none)
cookie="$(docker exec "$body" sh -c 'f=$(ls /tmp/xvfb-run.*/Xauthority 2>/dev/null | head -1); [ -n "$f" ] && od -An -tx1 -v "$f" | tr -d " \n"' || true)"
hex=""
if [ -n "$cookie" ]; then
  # Xauthority record: family(2) len+addr len+num len+name len+data — take the data
  hex="$(python -c "
import sys
b=bytes.fromhex(sys.argv[1]); i=2
for _ in range(3):
    n=int.from_bytes(b[i:i+2],'big'); i+=2+n
n=int.from_bytes(b[i:i+2],'big'); print(b[i+2:i+2+n].hex())" "$cookie")"
fi

docker rm -f "$vnc" "$web" >/dev/null 2>&1 || true
docker run -d --name "$vnc" --network "container:$body" -e COOKIE="$hex" "$image" sh -c '
  if [ -n "$COOKIE" ]; then touch /tmp/xa; XAUTHORITY=/tmp/xa xauth add :99 . "$COOKIE"; AUTH="-auth /tmp/xa"; fi
  exec x11vnc -display :99 $AUTH -forever -shared -nopw -rfbport 5900 -noxdamage -noshm -quiet' >/dev/null
docker run -d --name "$web" --network lastorder_default -p "127.0.0.1:${port}:6080" "$image" \
  websockify --web /usr/share/novnc 6080 "body-${slot}:5900" >/dev/null
sleep 3
if [ "$(docker inspect -f '{{.State.Running}}' "$vnc")" != true ]; then
  docker logs --tail 20 "$vnc" >&2; echo "gui $slot: x11vnc failed" >&2; exit 1
fi
[ "$(docker inspect -f '{{.State.Paused}}' "$node" 2>/dev/null)" = true ]   || { docker pause "$node" >/dev/null 2>&1 && echo "gui $slot: sister paused (node-${slot})"; } || true
echo "gui $slot: http://127.0.0.1:${port}/vnc.html  (down: bash dev/gui.sh down ${slot})"
