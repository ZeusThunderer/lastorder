# HAPI hub (ADR-042, T-044): the console's server side, upstream AGPL-3.0,
# unmodified — npm-pinned like every other tool in the stack, no curl|bash.
# @twsxtd/hapi is a launcher that execs the platform binary from the matching
# @twsxtd/hapi-<os>-<arch> optional dependency (a self-contained build with the
# web UI assets embedded), so the one npm pin brings hub + web page + CLI.
# The hub holds NO provider keys: agent LLM traffic never passes through it, it
# only brokers session UI/events (ADR-042). Data (settings.json + hapi.db)
# lives in HAPI_HOME=/data, a named volume. CLI_API_TOKEN comes from the env
# (dev/stand_keys.py generates HAPI_CLI_API_TOKEN) and is persisted into
# settings.json on first start [проверено: T-044]. --no-relay keeps it off the
# public relay tunnel; the operator reaches the web page through the
# loopback-only published port, sandboxes through the internal docker networks.
FROM node:22-slim

ARG HAPI_VERSION=0.30.7

RUN npm install -g --no-fund --no-audit "@twsxtd/hapi@${HAPI_VERSION}" \
    && hapi --version \
    && npm cache clean --force

ENV HAPI_HOME=/data HAPI_LISTEN_HOST=0.0.0.0 HAPI_LISTEN_PORT=3006

RUN mkdir -p /data && chown node:node /data
USER node
WORKDIR /data
EXPOSE 3006

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3006/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["hapi", "hub", "--no-relay"]
