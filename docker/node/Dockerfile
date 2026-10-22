# Node container (NETWORK.md section 3, ADR-032/033): the runner plus both harnesses.
# The runner (python -m runtime.node) starts as root, reads its secrets from 0400 files
# under /run/secrets, renders nothing into env, and drops every harness process to the
# `sister` uid (plan review P1-1). Base is python:3.12-slim like the mind image; node 22
# comes from NodeSource (zcode-app-cli needs node:sqlite, i.e. Node >= 22).
FROM python:3.12-slim

ARG OPENCODE_VERSION=1.19.3
ARG ZCODE_APP_CLI_VERSION=3.15.3-36
ARG HERMES_AGENT_VERSION=v0.22.4
ARG SISTER_UID=1001
ARG SISTER_GID=1001

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg git jq \
       ripgrep fd-find tini procps xz-utils less \
    && ln -s /usr/bin/fdfind /usr/local/bin/fd \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g --no-fund --no-audit \
      "opencode-ai@${OPENCODE_VERSION}" \
      "zcode-app-cli@${ZCODE_APP_CLI_VERSION}" \
    && npm cache clean --force \
    && opencode --version && zcode --version

# Hermes Agent (ADR-032 third harness), pinned to the upstream git tag: PyPI lags
# behind (0.19.0 at build time) and upstream blocks wheel builds outright — the
# supported install is a source checkout + `uv sync --frozen` (editable), which is
# what their own setup script does through pm. The checkout stays at /opt/hermes-src
# (runtime assets — locales, skills, plugin manifests — resolve from it); deps land
# in the upstream uv.lock, hash-verified. Upstream pins its every dependency to
# Python 3.14 (uv.lock environments: python_version >= '3.14'), so Hermes runs on a
# uv-managed CPython 3.14 in its own venv /opt/hermes — the runner's /app/.venv
# (python 3.12) stays untouched. No curl|bash installer, no runtime updates:
# updates.check is false in the rendered config.yaml and `hermes update` only
# touches a live checkout the operator runs, not this container.
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
RUN uv python install 3.14 \
    && git clone --depth 1 --branch "${HERMES_AGENT_VERSION}" \
      https://github.com/NousResearch/hermes-agent.git /opt/hermes-src \
    && cd /opt/hermes-src \
    && UV_PROJECT_ENVIRONMENT=/opt/hermes uv sync --frozen --no-dev --python 3.14 \
    && ln -s /opt/hermes/bin/hermes /usr/local/bin/hermes \
    && HERMES_HOME=/tmp/hermes-build-home /opt/hermes/bin/hermes --version \
    && rm -rf /tmp/hermes-build-home

WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 \
    ZCODE_DISABLE_UPDATE_CHECK=1 NO_UPDATE_NOTIFIER=1 NO_COLOR=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY runtime ./runtime
COPY agent ./agent
COPY tools ./tools
RUN uv sync --frozen --no-dev
ENV PATH=/app/.venv/bin:$PATH

# The harness (and its bash) runs as `sister`; the runner keeps root only to supervise.
RUN groupadd -g "${SISTER_GID}" sister \
    && useradd -u "${SISTER_UID}" -g "${SISTER_GID}" -m -d /home/sister -s /bin/bash sister

COPY docker/node/entrypoint.sh /usr/local/bin/node-entrypoint
COPY docker/node/zcode-provider.cjs /usr/local/lib/zcode-provider.cjs
RUN chmod 0755 /usr/local/bin/node-entrypoint

WORKDIR /workplaces
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/node-entrypoint"]
CMD ["python", "-m", "runtime.node"]
