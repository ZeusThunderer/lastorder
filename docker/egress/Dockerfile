# The grants egress proxy (T-046): one stdlib-only file over python:3.12-slim.
# The allowlist is not baked in — the proxy asks hive at runtime (ADR-041).
FROM python:3.12-slim
COPY proxy.py /app/proxy.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/proxy.py"]
