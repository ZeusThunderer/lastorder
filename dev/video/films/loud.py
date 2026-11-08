"""Bring a rendered film to -14 LUFS with a limiter (-3 dB ceiling before AAC, so the file peaks under -1 dBFS); video stream copied (T-067 films).
    python dev/video/films/loud.py dev/video/out/film-b45.mp4"""
import re, subprocess, sys
from pathlib import Path
src = Path(sys.argv[1])
def measure(p: Path) -> tuple[float, float]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r)[-1]); pk = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r)[-1])
    return i, pk
i, pk = measure(src)
gain = round(-14.0 - i, 2)
tmp = src.with_suffix(".loud.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "copy",
                "-af", f"volume={gain}dB,aresample=192000,alimiter=limit=0.7:attack=2:level=false,aresample=48000",
                "-c:a", "aac", "-b:a", "256k",
                "-movflags", "+faststart", str(tmp)], check=True)
tmp.replace(src)
i2, pk2 = measure(src)
print(f"{src.name}: {i:.1f} LUFS / {pk:.1f} dBFS -> gain {gain} dB -> {i2:.1f} LUFS / {pk2:.1f} dBFS")
