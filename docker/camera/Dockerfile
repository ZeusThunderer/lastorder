# Spectator camera (T-067, operator request 2026-11-07): a plain Fabric client, no unionclef,
# rendered on CPU in Xvfb at high detail and recorded by ffmpeg inside the container.
# It is a filming tool, not a slot: no agent, no py4j, no keys. Driven from the host by
# dev/video/cam.sh (RCON through mc-server for spectate/tp, docker exec for record/shot).
FROM lastorder-body:dev

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg xdotool pulseaudio \
 && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /opt/camera/entrypoint.sh
COPY cam.sh /usr/local/bin/cam
RUN chmod +x /opt/camera/entrypoint.sh /usr/local/bin/cam

VOLUME /footage
ENV DISPLAY=:99 CAM_W=1920 CAM_H=1080 BODY_XMX=2G
ENTRYPOINT ["/opt/camera/entrypoint.sh"]
