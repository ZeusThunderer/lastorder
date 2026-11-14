# The operator's temporary GUI onto a body's own X display (dev/gui.sh): x11vnc reads
# the body's Xvfb through the abstract X socket of the body's network namespace, noVNC
# serves it to a browser. Nothing in the body restarts; removing the sidecar ends it.
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends x11vnc xauth novnc websockify procps \
 && rm -rf /var/lib/apt/lists/*
