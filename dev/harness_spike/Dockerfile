# Harness image of the T-024 spike: one container, two harnesses.
# - opencode CLI, pinned (review P1-7)
# - the ZCode headless CLI bundle extracted from the AppImage (step 0: it runs without GUI on
#   Node >= 22; the Electron shell is not shipped here), pinned by URL + sha256 (review P1-7)
# The agent's workplace is bind-mounted at /workplace by the compose overlay; secrets arrive as
# environment variables only (review P1-6) — no env_file, no repo mounts.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl jq xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG OPENCODE_VERSION=1.19.3
RUN npm install -g opencode-ai@${OPENCODE_VERSION} && opencode --version

ARG ZCODE_VERSION=3.15.3
ARG ZCODE_SHA256=2edc79650fcff72358670af4ffbfbf7d979ba95476611a80ad1151d577e90fe3
RUN curl -sSfL -o /tmp/zcode.AppImage \
      https://cdn-zcode.z.ai/zcode/electron/releases/${ZCODE_VERSION}/linux-x64/ZCode-${ZCODE_VERSION}-linux-x64.AppImage \
    && echo "${ZCODE_SHA256}  /tmp/zcode.AppImage" | sha256sum -c - \
    && chmod +x /tmp/zcode.AppImage \
    && cd /opt && /tmp/zcode.AppImage --appimage-extract >/dev/null \
    && mkdir -p /opt/zcode-cli \
    && cp -r squashfs-root/resources/glm /opt/zcode-cli/ \
    && cp -r squashfs-root/resources/config /opt/zcode-cli/ \
    && rm -rf squashfs-root /tmp/zcode.AppImage

ENV ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=/opt/zcode-cli/config/provider/zcode-builtin.json

COPY dev/harness_spike/opencode.json /etc/harness/opencode.json
COPY dev/harness_spike/zcode.mcp.json /etc/harness/zcode.mcp.json
COPY dev/harness_spike/run_opencode.sh dev/harness_spike/run_zcode.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/run_opencode.sh /usr/local/bin/run_zcode.sh

WORKDIR /workplace
CMD ["bash", "-c", "echo 'harness image: pick run_opencode.sh or run_zcode.sh'"]
