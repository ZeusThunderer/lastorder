# Body: a Minecraft 1.21.11 client with Fabric + unionclef, launched by HeadlessMC (ADR-015).
# Headless by default (-lwjgl patches rendering out). BODY_RENDER=xvfb is the fallback if uc needs GL.
FROM eclipse-temurin:21-jre-noble

ARG HMC_VERSION=2.10.0
ARG HMC_SHA256=52bd5006f478377b3893011d458562977d38c65ead6d2b31089beb4d614f13cd
ARG UC_VERSION=0.95.66
ARG UC_SHA256=ff23056a32f7e2fe9fe6a7f5dda73f3529e4ab3df543b1488123bf9a90007961
ARG FABRIC_API=0.140.0+1.21.11
ARG FABRIC_API_SHA256=db5f2884996adf54cb1a4a5e0c6348abeba2772d057ad89fd15c1c08336f736a
# Simple Voice Chat, client side (ADR-055): only voice bodies load it (mods-voice/), the
# others keep the old mod set — SVC without an audio device crashes on joining a world.
ARG SVC_VERSION=2.6.27
ARG SVC_URL=https://cdn.modrinth.com/data/9eGKb6K1/versions/arpQQSyx/voicechat-fabric-1.21.11-2.6.27.jar
ARG SVC_SHA512=d20183ff6de8248ed7d55492bf3e620fd9531fa1eebcf2992c72e3b530a72fc459499fa6ed7feeb09be845ad5b70f175445690bb8ee5eec182ada564b429e36a

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates xvfb libgl1 libglx-mesa0 libxrender1 libxtst6 libxi6 ffmpeg socat \
    pulseaudio pulseaudio-utils libpulse0 libasound2-plugins \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/body
RUN set -eu; \
    get() { curl -fsSL -o "$1" "$2" && echo "$3  $1" | sha256sum -c -; }; \
    get headlessmc-launcher.jar \
        "https://github.com/headlesshq/headlessmc/releases/download/${HMC_VERSION}/headlessmc-launcher-${HMC_VERSION}.jar" "$HMC_SHA256"; \
    mkdir mods; \
    get "mods/unionclef-1.21.11-${UC_VERSION}.jar" \
        "https://github.com/3ndetz/unionclef/releases/download/v${UC_VERSION}/unionclef-1.21.11-${UC_VERSION}.jar" "$UC_SHA256"; \
    get "mods/fabric-api-${FABRIC_API}.jar" \
        "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/$(echo "$FABRIC_API" | sed 's/+/%2B/')/fabric-api-${FABRIC_API}.jar" "$FABRIC_API_SHA256"; \
    mkdir mods-voice; \
    curl -fsSL -o "mods-voice/voicechat-fabric-1.21.11-${SVC_VERSION}.jar" "$SVC_URL"; \
    echo "$SVC_SHA512  mods-voice/voicechat-fabric-1.21.11-${SVC_VERSION}.jar" | sha512sum -c -

COPY entrypoint.sh /opt/body/entrypoint.sh
RUN chmod +x /opt/body/entrypoint.sh

# /data keeps downloaded versions, libraries and the game dir between restarts (one volume per body).
VOLUME /data
# The heap default matches compose (x-body BODY_XMX): one value, two places, kept equal (T-003).
ENV HOME=/data MC_VERSION=1.21.11 BODY_RENDER=headless BODY_XMX=768M
ENTRYPOINT ["/opt/body/entrypoint.sh"]
