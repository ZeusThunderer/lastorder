# syntax=docker/dockerfile:1.7
# ZCode headless agent harness (runtime 0.16.x via zcode-app-cli)
FROM node:22-bookworm-slim

ARG ZCODE_APP_CLI_VERSION=3.15.3-36
ARG AGENT_UID=1000
ARG AGENT_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    ZCODE_DISABLE_UPDATE_CHECK=1 \
    NO_UPDATE_NOTIFIER=1 \
    NO_COLOR=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    ZCODE_WORKDIR=/workspace \
    ZCODE_MODE=yolo \
    ZCODE_MODEL=GLM-5.3 \
    ZCODE_PROVIDER_ID=zai-cp

# Инструменты, которые агент реально дёргает через Bash-tool
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates git ripgrep fd-find jq curl openssh-client \
      python3 python3-venv build-essential tini \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-fund --no-audit "zcode-app-cli@${ZCODE_APP_CLI_VERSION}" \
 && npm cache clean --force \
 && zcode --version

# node-образ уже содержит пользователя node с uid 1000 — переиспользуем под именем agent
RUN usermod -l agent -d /home/agent -m node \
 && groupmod -n agent node \
 && usermod -u "${AGENT_UID}" agent && groupmod -g "${AGENT_GID}" agent \
 && mkdir -p /workspace /home/agent/.zcode \
 && chown -R agent:agent /workspace /home/agent

# T-024: build context is the repo root (same as the opencode harness service of the overlay).
COPY --chmod=0755 dev/harness_spike/zcode/entrypoint.sh /usr/local/bin/zcode-harness
COPY --chmod=0755 dev/harness_spike/run_zcode.sh /usr/local/bin/run_zcode.sh
# Project config for the -p path: zcode merges `zcode.json` from the working directory into
# settings and spawns its mcp.servers (verified live; the .mcp.json array form is NOT read).
COPY dev/harness_spike/zcode.project.json /etc/harness/zcode.json

USER agent
WORKDIR /workspace
ENV HOME=/home/agent

VOLUME ["/home/agent/.zcode"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/zcode-harness"]
CMD ["help"]
