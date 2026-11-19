"""Speech to text for the voice service (ADR-055): a phrase heard through SVC (PCM s16le
48 kHz mono, what uc's onVoiceFeed hands the mind) → Russian text.

Whisper large-v3-turbo through transformers on the same GPU as the TTS: it rides the torch
build this image already has. GigaAM v3 (ADR-010) is the better Russian model and the
planned replacement; Whisper is the one that runs today without a new stack.
"""

from __future__ import annotations

import logging
import os

import numpy as np

from runtime.tts.backends import OUT_RATE

log = logging.getLogger("tts.stt")

STT_RATE = 16000
# Whisper's well-known inventions on silence and noise (Russian subtitle credits)
HALLUCINATIONS = (
    "продолжение следует", "субтитры", "редактор субтитров", "спасибо за просмотр",
    "подписывайтесь", "dimatorzok", "игорь негода",
)


def to_16k_float(pcm48: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm48, dtype="<i2").astype(np.float32) / 32768.0
    if not len(audio):
        return audio
    # 48 kHz → 16 kHz: an exact 3:1, the mean of each triple is a fair low-pass for speech
    n = len(audio) // 3 * 3
    return audio[:n].reshape(-1, 3).mean(axis=1)


class WhisperStt:
    name = "whisper"

    def __init__(self, *, model_id: str, device: str, dtype: str):
        import torch
        from transformers import pipeline

        log.info("loading %s on %s", model_id, device)
        self._pipe = pipeline(
            "automatic-speech-recognition", model=model_id,
            torch_dtype=getattr(torch, dtype), device=device,
        )

    def transcribe(self, pcm48: bytes) -> str:
        audio = to_16k_float(pcm48)
        if len(audio) < STT_RATE * 0.3:
            return ""
        if float(np.abs(audio).max()) < 0.01:  # silence: Whisper would invent a caption
            return ""
        out = self._pipe(
            {"raw": audio, "sampling_rate": STT_RATE},
            generate_kwargs={"language": "russian", "task": "transcribe"},
        )
        text = str(out.get("text", "")).strip()
        if any(h in text.lower() for h in HALLUCINATIONS):
            return ""
        return text


def stt_from_env() -> WhisperStt | None:
    if os.environ.get("TTS_STT", "1") != "1":
        return None
    device = os.environ.get("TTS_STT_DEVICE") or os.environ.get("TTS_DEVICE", "cuda:0")
    return WhisperStt(
        model_id=os.environ.get("TTS_STT_MODEL", "openai/whisper-large-v3-turbo"),
        device=device,
        dtype="float32" if device == "cpu" else "float16",
    )


assert OUT_RATE == 48000  # to_16k_float's 3:1 depends on it
