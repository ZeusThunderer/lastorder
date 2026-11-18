"""The voice service (ADR-055): `POST /speak {"text": ...}` → PCM s16le 48 kHz mono.

One synthesis at a time (one model on one GPU); the minds queue on their own side, so a
second sister's line simply waits here. `204` means "nothing in this line to voice" — the
mind then sends it to chat silently. Headers carry the clip length and the synthesis time.

Run: `python -m runtime.tts.server` (the `tts` compose service, profile `voice`).
"""

from __future__ import annotations

import logging
import os
import threading
import time

import anyio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from runtime.tts.backends import OUT_RATE, Backend, from_env
from runtime.tts.normalize import normalize

log = logging.getLogger("tts.server")

MAX_TEXT = 400  # say caps at 256; a margin, not a feature


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


def create_app(backend: Backend | None = None) -> FastAPI:
    """`backend=None` loads the one from env at startup (the model takes ~a minute)."""
    state: dict[str, object] = {}
    lock = threading.Lock()
    stt_lock = lock

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["backend"] = backend or await run_in_threadpool(from_env)
        if backend is None:
            from runtime.tts.stt import stt_from_env

            state["stt"] = await run_in_threadpool(stt_from_env)
        log.info("tts ready: backend %s", state["backend"].name)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"backend": state["backend"].name, "rate": str(OUT_RATE),
                "model": str(getattr(state["backend"], "model_id", "")),
                "gpu": str(getattr(state["backend"], "gpu", "")),
                "gpu_uuid": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
                "first_chunk_s": str(state.get("first_chunk_s", "")),
                "ready": str(getattr(state["backend"], "ready", True))}

    @app.post("/transcribe")
    async def transcribe(request: Request) -> dict[str, str]:
        """Raw PCM s16le 48 kHz mono in (one phrase), {"text": ...} out; "" = nothing said."""
        stt = state.get("stt")
        if stt is None:
            raise HTTPException(status_code=503, detail="stt is off (TTS_STT=0)")
        pcm = await request.body()

        def run() -> str:
            with stt_lock:
                return stt.transcribe(pcm)

        t0 = time.monotonic()
        text = await run_in_threadpool(run)
        log.info("heard %.1f s in %.2f s: %r", len(pcm) / 2 / OUT_RATE, time.monotonic() - t0, text[:80])
        return {"text": text}

    @app.post("/speak/stream")
    async def speak_stream(body: SpeakIn):
        text = normalize(body.text)
        if not text:
            return Response(status_code=204)
        t0 = time.monotonic()
        engine = state["backend"]
        def generate():
            if hasattr(engine, "stream"):
                yield from engine.stream(text)
            else:
                yield engine.synthesize(text)
        source = generate()
        sentinel = object()

        def advance():
            return next(source, sentinel)

        def finish():
            try:
                close = getattr(source, "close", None)
                if close:
                    close()
            finally:
                lock.release()

        # Shield GPU work from cancellation: close the iterator only after its current
        # CUDA call returns, and always release the shared TTS/STT lock on disconnect.
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(lock.acquire)
            try:
                first = await run_in_threadpool(advance)
            except Exception as exc:
                await run_in_threadpool(finish)
                raise HTTPException(status_code=502, detail="synthesis failed before audio") from exc
        if first is sentinel:
            await run_in_threadpool(finish)
            return Response(status_code=204)
        elapsed = time.monotonic() - t0
        state["first_chunk_s"] = elapsed
        log.info("first audio chunk in %.3f s", elapsed)

        async def chunks():
            try:
                yield first
                while True:
                    with anyio.CancelScope(shield=True):
                        pcm = await run_in_threadpool(advance)
                    if pcm is sentinel:
                        break
                    yield pcm
            finally:
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(finish)
        return StreamingResponse(chunks(), media_type="application/octet-stream",
                                 headers={"X-Audio-Rate": str(OUT_RATE)})

    @app.post("/speak")
    def speak(body: SpeakIn) -> Response:
        text = normalize(body.text)
        if not text:
            return Response(status_code=204)
        t0 = time.monotonic()
        with lock:
            try:
                pcm = state["backend"].synthesize(text)
            except Exception as exc:  # a failed line is the mind's fallback case, not a crash
                log.exception("synthesis failed for %r", text[:80])
                raise HTTPException(status_code=502, detail=f"synthesis failed: {exc}") from exc
        synth_s = time.monotonic() - t0
        duration_s = len(pcm) / 2 / OUT_RATE
        log.info("spoke %.1f s of audio in %.2f s: %r", duration_s, synth_s, text[:80])
        return Response(
            content=pcm,
            media_type=f"audio/L16; rate={OUT_RATE}; channels=1",
            headers={"X-Duration-S": f"{duration_s:.3f}", "X-Synth-S": f"{synth_s:.3f}"},
        )

    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("TTS_PORT", "8790")))


if __name__ == "__main__":
    main()
