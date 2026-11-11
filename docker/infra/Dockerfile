# The infra executor (ADR-050): the Docker CLI, compose, bash and python — nothing else.
# The code is the repo's own (dev/infra_bridge.py, runtime/hive/infra.py, docker/node/up.sh),
# mounted read-only at the daemon's path, so the image carries tools, not logic.
# Compose is pinned to the host's version: both sides then compute the same container
# configs and never recreate each other's containers over a version difference.
FROM docker:29.9.2-cli-alpine3.24

ARG COMPOSE_VERSION=v5.6.1
ARG COMPOSE_SHA256=47737f4236e6d3ccea7291995203f8f018058783b4c3a9469119811a0b5db8c0

RUN apk add --no-cache bash python3 curl ca-certificates \
 && mkdir -p /usr/local/libexec/docker/cli-plugins \
 && curl -fsSL -o /usr/local/libexec/docker/cli-plugins/docker-compose \
      "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
 && echo "${COMPOSE_SHA256}  /usr/local/libexec/docker/cli-plugins/docker-compose" | sha256sum -c - \
 && chmod +x /usr/local/libexec/docker/cli-plugins/docker-compose \
 && docker compose version | grep -q "${COMPOSE_VERSION#v}" \
 && apk del curl

ENTRYPOINT []
CMD ["python3", "dev/infra_bridge.py"]
