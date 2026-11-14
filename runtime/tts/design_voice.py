"""Make a reference voice from a description (ADR-055): Qwen3-TTS VoiceDesign speaks a
Russian line in the described voice, the result becomes `ref.wav` + `ref.txt` for the
clone. A voice nobody owns — the alternative to a clip cut from the anime.

    docker compose -f docker/compose.yml --profile voice run --rm \
        -v "$(pwd -W)/.stand/voice:/voice-out" tts \
        python -m runtime.tts.design_voice --out-dir /voice-out

(`pwd -W` in Git Bash gives C:/… — the daemon resolves bind paths, not the shell.)

Listen to `ref.wav`; run again with another `--seed` or `--instruct` until it fits.
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_INSTRUCT = (
    "A girl of about fourteen speaking quietly and completely flat: monotone, emotionless, "
    "precise and slightly mechanical, like reading a laboratory report aloud. Soft clear "
    "articulation, steady pace, no rises or falls in pitch, no breathiness."
)
DEFAULT_TEXT = (
    "Эксперимент продолжается, докладывает Мисака. Отклонений не обнаружено. "
    "Мисака ждёт следующей команды и записывает наблюдения в журнал."
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--instruct", default=DEFAULT_INSTRUCT)
    ap.add_argument("--text", default=DEFAULT_TEXT)
    ap.add_argument("--model", default="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--seed", type=int, default=10032)
    ap.add_argument("--dtype", default="bfloat16", help="float16 on an RTX 20xx")
    args = ap.parse_args()

    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    torch.manual_seed(args.seed)
    model = Qwen3TTSModel.from_pretrained(
        args.model, device_map=args.device, dtype=getattr(torch, args.dtype), attn_implementation="sdpa",
    )
    wavs, sr = model.generate_voice_design(text=args.text, language="Russian", instruct=args.instruct)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    sf.write(args.out_dir / "ref.wav", wavs[0], sr)
    (args.out_dir / "ref.txt").write_text(args.text + "\n", encoding="utf-8")
    print(f"wrote {args.out_dir / 'ref.wav'} ({len(wavs[0]) / sr:.1f} s at {sr} Hz) and ref.txt")


if __name__ == "__main__":
    main()
