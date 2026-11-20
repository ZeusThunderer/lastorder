"""Warm, paired Russian F5 step comparison; run in the stand TTS environment."""
import json
import sys
import time
import wave
from pathlib import Path

import torch

from runtime.tts.russian_f5 import RussianF5

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
model = RussianF5("/voice/cloud")
phrases = ["Ключ висит на двери́.", "Снег белый. Я вижу тебя у ворот.",
           "Я ставлю последнюю доску и возвращаюсь к двери́."]
model.synthesize(phrases[0])
rows = []
for i, phrase in enumerate(phrases):
    for steps in ([32, 24, 16], [24, 16, 32], [16, 32, 24])[i]:
        torch.manual_seed(42)
        model.steps = steps
        start = time.perf_counter()
        pcm = model.synthesize(phrase)
        elapsed = time.perf_counter() - start
        with wave.open(str(out / f"{i+1}-{steps}.wav"), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", ""))
            wav.writeframes(pcm)
        rows.append(dict(phrase=phrase, steps=steps, synth_s=round(elapsed, 3),
                         audio_s=round(len(pcm) / 96000, 3)))
        print(rows[-1], flush=True)
(out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
