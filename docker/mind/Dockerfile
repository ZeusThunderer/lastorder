# Mind: the Python runtime of one slot (and the hive). Runs in its body's network namespace.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY runtime ./runtime
COPY agent ./agent
COPY tools ./tools
RUN uv sync --frozen --no-dev
ENV PATH=/app/.venv/bin:$PATH
CMD ["python", "-c", "print('mind image: pass a command')"]
