# FastJev decision server (T-005, ADR-004/023): fastjev-serve pinned to 0.2.0.
# The torch backend reads option logits in one pass; output tokens are zero, so no text is
# generated per decision. Runs on the RTX 2070 via compose device_ids (ARCHITECTURE §8).
FROM python:3.12-slim

RUN pip install --no-cache-dir 'fastjev[api,torch]==0.2.0'

COPY pick_gpu.py /usr/local/bin/pick_gpu.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV HF_HOME=/data/hf \
    PYTHONUNBUFFERED=1 \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    JEV_GPU_NAME="RTX 2070"
# Models download into the named volume mounted at /data, never a bind mount (task constraint).
VOLUME /data
EXPOSE 8000

# The bind is non-loopback only inside the compose network; the host publish is 127.0.0.1-only
# and exists for the spike measurement (docs/agents/jev.md). fastjev-serve would reject an
# unauthenticated non-loopback bind, hence the explicit flag; see the report for the token plan.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["--host", "0.0.0.0", "--allow-unauthenticated"]
