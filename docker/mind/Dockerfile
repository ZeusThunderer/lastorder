# Mind: the Python runtime of one slot (and the hive). Runs in its body's network namespace.
FROM python:3.12-slim
# git is a hive dependency (T-022 review P1-1): the knowledge base is a git repository
# and KnowledgeStore commits on every write, so the image needs a git binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
# --extra hive installs fastapi and uvicorn for the hive service (T-022).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra hive
COPY runtime ./runtime
COPY agent ./agent
COPY tools ./tools
RUN uv sync --frozen --no-dev --extra hive
ENV PATH=/app/.venv/bin:$PATH
CMD ["python", "-c", "print('mind image: pass a command')"]
