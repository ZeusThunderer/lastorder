"""CPU-only reference voice adapter. LiteLLM authorizes each line with the slot key."""
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from runtime.tts.backends import VoiceRef, to_pcm48
from runtime.tts.normalize import normalize
from runtime.tts.server import SpeakIn

MODEL = "fish-audio/s2.1-pro"


@asynccontextmanager
async def lifespan(app):
    reference = Path(os.environ.get("TTS_CLOUD_REFERENCE", "/voice"))
    app.state.refs = VoiceRef.load(reference).openrouter_references()
    async with httpx.AsyncClient(timeout=25) as client:
        app.state.client = client
        yield


app = FastAPI(lifespan=lifespan)


class GatewaySpeech(BaseModel):
    input: str = Field(min_length=1, max_length=4096)
    model: str
    voice: str


async def audio_request(url, authorization, payload):
    if not authorization.startswith("Bearer ") or not authorization[7:]:
        raise HTTPException(401, "missing bearer credential")
    try:
        response = await app.state.client.post(url, headers={"Authorization": authorization}, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(502, "speech service unavailable") from exc
    if not response.is_success:
        raise HTTPException(response.status_code, "speech service rejected request")
    return response.content


@app.post("/v1/audio/speech")
async def upstream(body: GatewaySpeech, authorization: str = Header(default="")):
    # The gateway supplies the upstream credential. Fish selects the speaker by reference.
    if body.model != MODEL or body.voice != "sister":
        raise HTTPException(400, "unknown voice or model")
    data = await audio_request("https://openrouter.ai/api/v1/audio/speech", authorization, {
        "model": MODEL, "input": body.input, "response_format": "mp3",
        "input_references": app.state.refs,
    })
    return Response(data, media_type="audio/mpeg")


@app.post("/speak/stream")
async def speak(body: SpeakIn, authorization: str = Header(default="")):
    text = normalize(body.text)
    if not text:
        return Response(status_code=204)
    data = await audio_request(os.environ["GATEWAY_URL"].rstrip("/") + "/audio/speech",
                               authorization, {"model": "sister-voice", "input": text,
                                               "voice": "sister", "response_format": "mp3"})
    pcm = await run_in_threadpool(to_pcm48, data, in_args=[])
    return Response(pcm, media_type="application/octet-stream",
                    headers={"X-Audio-Rate": "48000"})
