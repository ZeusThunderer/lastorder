"""Synthesis backends of the voice service (ADR-055). Every backend returns the same thing:
raw PCM s16le, 48 kHz, mono — what SVC carries and what the body's `paplay` expects.

- `qwen3`: Qwen3-TTS Base on the host GPU, the voice cloned once from the reference
  sample at start (`create_voice_clone_prompt`), every line reuses the prompt.
- `openrouter`: `POST /api/v1/audio/speech` with the reference sample in `input_references`
  (Fish Audio by default). The key lives in this service alone; a new kind of spending,
  switched on by the operator (ADR-055).

The reference is a pair of files: `ref.wav` (3-15 s of clean speech) and `ref.txt` (its
exact transcript). Both are the operator's, outside git (`.stand/voice/`).
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger("tts.backends")

OUT_RATE = 48000  # SVC's rate; the body plays exactly this


@dataclass(frozen=True)
class VoiceRef:
    wav: Path
    text: str

    @classmethod
    def load(cls, voice_dir: Path) -> "VoiceRef":
        wav, txt = voice_dir / "ref.wav", voice_dir / "ref.txt"
        if not wav.is_file() or not txt.is_file():
            raise FileNotFoundError(
                f"voice reference missing: need {wav} and {txt} (see runtime/tts/design_voice.py)"
            )
        text = txt.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"{txt} is empty: the clone needs the exact transcript of ref.wav")
        return cls(wav=wav, text=text)


class Backend(Protocol):
    name: str

    def synthesize(self, text: str) -> bytes:
        """PCM s16le 48 kHz mono for one normalized line."""


def to_pcm48(data: bytes, *, in_args: list[str]) -> bytes:
    """Any audio ffmpeg can read → PCM s16le 48 kHz mono. `in_args` describe raw input
    (`-f f32le -ar 24000 -ac 1`) or are empty for a container format (mp3, wav)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *in_args, "-i", "pipe:0",
         "-f", "s16le", "-ar", str(OUT_RATE), "-ac", "1", "pipe:1"],
        input=data,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {proc.stderr.decode(errors='replace').strip()[:300]}")
    return proc.stdout


class Qwen3Backend:
    name = "qwen3"

    def __init__(self, ref: VoiceRef, *, model_id: str, device: str, language: str = "Russian",
                 dtype: str = "bfloat16"):
        import torch
        from qwen_tts import Qwen3TTSModel

        log.info("loading %s on %s", model_id, device)
        # sdpa, not flash-attn: flash-attn has no prebuilt wheel for Blackwell (sm_120)
        self._model = Qwen3TTSModel.from_pretrained(
            model_id, device_map=device, dtype=getattr(torch, dtype), attn_implementation="sdpa",
        )
        self._language = language
        self._prompt = self._model.create_voice_clone_prompt(
            ref_audio=str(ref.wav), ref_text=ref.text, x_vector_only_mode=False,
        )
        log.info("voice prompt built from %s", ref.wav)

    def synthesize(self, text: str) -> bytes:
        import numpy as np

        wavs, sr = self._model.generate_voice_clone(
            text=text, language=self._language, voice_clone_prompt=self._prompt,
        )
        audio = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        return to_pcm48(audio.tobytes(), in_args=["-f", "f32le", "-ar", str(int(sr)), "-ac", "1"])


class OpenRouterBackend:
    name = "openrouter"

    def __init__(self, ref: VoiceRef, *, api_key: str, model: str, base_url: str, timeout_s: float):
        import httpx

        if not api_key:
            raise ValueError("TTS_BACKEND=openrouter needs OPENROUTER_API_KEY")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
        self._model = model
        b64 = base64.b64encode(ref.wav.read_bytes()).decode("ascii")
        self._refs = [
            {"type": "input_audio", "input_audio": {"data": f"data:audio/wav;base64,{b64}"}},
            {"type": "text", "text": ref.text},
        ]

    def synthesize(self, text: str) -> bytes:
        resp = self._client.post(
            "/audio/speech",
            json={
                "model": self._model,
                "input": text,
                "response_format": "mp3",  # pcm's rate is per provider and undocumented
                "input_references": self._refs,
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"openrouter {resp.status_code}: {resp.text[:300]}")
        return to_pcm48(resp.content, in_args=[])


def from_env() -> Backend:
    """The backend this service runs, from its env (compose `tts`)."""
    ref = VoiceRef.load(Path(os.environ.get("TTS_VOICE_DIR", "/voice")))
    kind = os.environ.get("TTS_BACKEND", "qwen3")
    if kind == "qwen3":
        return Qwen3Backend(
            ref,
            model_id=os.environ.get("TTS_QWEN_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
            device=os.environ.get("TTS_DEVICE", "cuda:0"),
            dtype=os.environ.get("TTS_DTYPE", "bfloat16"),  # float16 on Turing (RTX 20xx): no bf16
        )
    if kind == "openrouter":
        return OpenRouterBackend(
            ref,
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            model=os.environ.get("TTS_OPENROUTER_MODEL", "fish-audio/s2.1-pro"),
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            timeout_s=float(os.environ.get("TTS_OPENROUTER_TIMEOUT_S", "20")),
        )
    raise ValueError(f"unknown TTS_BACKEND {kind!r}: qwen3 or openrouter")
