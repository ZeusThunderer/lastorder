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
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from runtime.tts.backends import OUT_RATE, Backend, from_env
from runtime.tts.normalize import normalize

log = logging.getLogger("tts.server")

MAX_TEXT = 400  # say caps at 256; a margin, not a feature


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT)


def create_app(backend: Backend | None = None) -> FastAPI:
    """`backend=None` loads the one from env at startup (the model takes ~a minute)."""
    state: dict[str, Backend] = {}
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["backend"] = backend or await run_in_threadpool(from_env)
        log.info("tts ready: backend %s", state["backend"].name)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"backend": state["backend"].name, "rate": str(OUT_RATE)}

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
