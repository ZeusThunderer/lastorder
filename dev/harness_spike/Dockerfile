# Harness image of the T-024 spike: OpenCode only. The ZCode harness lives in its own image
# (dev/harness_spike/zcode/Dockerfile, the operator's zcode-headless setup: zcode-app-cli
# npm with `zcode -p --model`, which the AppImage CLI bundle lacks).
# The agent's workplace is bind-mounted at /workplace by the compose overlay; secrets arrive as
# environment variables only (review P1-6) — no env_file, no repo mounts.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl jq xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG OPENCODE_VERSION=1.19.3
RUN npm install -g opencode-ai@${OPENCODE_VERSION} && opencode --version

COPY dev/harness_spike/opencode.json /etc/harness/opencode.json
COPY dev/harness_spike/run_opencode.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/run_opencode.sh

WORKDIR /workplace
CMD ["bash", "-c", "echo 'harness image: run_opencode.sh'"]
