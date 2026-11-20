"""Russian reference voice selected in the T-082 blind listening test (sample B)."""
import os
import re
from pathlib import Path

from runtime.tts.backends import VoiceRef


class RussianF5:
    name = "f5-russian"
    model_id = "Andreevai/f5-tts-russian-emotional"

    def __init__(self, reference_dir):
        import torch
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT
        from huggingface_hub import hf_hub_download
        from ruaccent import RUAccent

        self.gpu = torch.cuda.get_device_name()
        self.steps = int(os.environ.get("TTS_F5_STEPS", "32"))
        if self.steps not in (16, 24, 32):
            raise ValueError("TTS_F5_STEPS must be 16, 24 or 32")
        ref = VoiceRef.load(Path(reference_dir))
        self.accent = RUAccent()
        self.accent.load(omograph_model_size="turbo3.1", use_dictionary=True)
        # The unknown-word ONNX graph also expects segment IDs; CharTokenizer's
        # defaults omit them. Match the loaded graph before stressing the reference.
        accent_model = self.accent.accent_model
        accent_model.tokenizer.model_input_names = [arg.name for arg in accent_model.session.get_inputs()]
        self.reference = str(ref.wav)
        self.transcript = self.accent.process_all(ref.text)
        self.vocoder = load_vocoder(device="cuda")
        cfg = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)
        def model_file(name):
            directory = os.environ.get("TTS_F5_WEIGHTS")
            return str(Path(directory) / name) if directory else hf_hub_download(self.model_id, name)

        self.model = load_model(DiT, cfg, model_file("model.safetensors"),
                                vocab_file=model_file("vocab.txt"), device="cuda")

    def synthesize(self, text):
        import numpy as np
        import soxr
        from f5_tts.infer.utils_infer import infer_process

        # Preserve explicit stress from the speaker and auto-accent the surrounding text.
        words = re.split(r"([А-Яа-яЁё]*[АЕЁИОУЫЭЮЯаеёиоуыэюя]\u0301[А-Яа-яЁё]*)", text)
        def stress(i, part):
            if i % 2:
                return re.sub(r"(.)\u0301", r"+\1", part)
            return re.sub(r"\S(?:.*\S)?", lambda m: self.accent.process_all(m[0]), part)

        marked = "".join(stress(i, part) for i, part in enumerate(words))
        wave, rate, _ = infer_process(self.reference, self.transcript, marked, self.model,
                                     self.vocoder, device="cuda", nfe_step=self.steps, speed=1.0, progress=None)
        pcm = soxr.resample(wave, rate, 48000)
        return np.clip(np.rint(pcm * 32768), -32768, 32767).astype("<i2").tobytes()
