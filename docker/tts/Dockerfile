# The voice service (ADR-055): Qwen3-TTS on the host GPU (or OpenRouter), PCM 48 kHz out.
# Build context is the repo root (compose `tts`). CUDA 12.8 + torch cu128: the first
# combination with Blackwell (sm_120, RTX 50xx) kernels. flash-attn is not installed —
# no Blackwell wheel; the model runs on sdpa.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 HF_HOME=/data/hf
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg sox libsndfile1 curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# torch first from the cu128 index, so qwen-tts finds it and does not pull its own
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
RUN pip install --no-cache-dir qwen-tts soundfile numpy num2words httpx "fastapi>=0.115" "uvicorn>=0.30"

WORKDIR /app
COPY runtime/__init__.py ./runtime/__init__.py
COPY runtime/tts ./runtime/tts

EXPOSE 8790
CMD ["python", "-m", "runtime.tts.server"]
