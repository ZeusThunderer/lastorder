"""Trim and grade the trailer's shots from the camera takes into film/assets/clips/ (T-067).

    python dev/video/trailer/cut_clips.py [NAME ...]

Every shot: source file in dev/video/out/footage, start, duration, grade. The grade is baked here
(ffmpeg), so the composition only places clips. Sources stay outside git; clips too.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "dev/video/out/footage"
DST = Path(__file__).resolve().parent / "film/assets/clips"

GRADE = "eq=contrast=1.08:saturation=1.12:gamma=1.02"
CAVE = "curves=all='0/0 0.05/0.22 0.25/0.55 1/1',eq=saturation=1.15"

# name: (source, start s, duration s, grade)
SHOTS = {
    "hook_pair":   ("take1.mp4", 740.0, 3.8, GRADE),
    "crane_coast": ("c2_crane.mp4", 1.0, 3.0, GRADE),
    "birth_pov":   ("take1.mp4", 119.0, 9.2, GRADE),
    "title_fly":   ("d1_fly.mp4", 0.5, 3.3, GRADE),
    "grove_high":  ("o2g_high.mp4", 5.0, 1.8, GRADE),
    "ravine_two":  ("take1.mp4", 358.0, 1.8, GRADE),
    "path_walk":   ("take1.mp4", 449.3, 3.3, GRADE),
    "meet_back":   ("take1.mp4", 548.0, 3.3, GRADE),
    "slope_two":   ("take1.mp4", 776.0, 1.8, GRADE),
    "coal_pov":    ("take1.mp4", 889.3, 3.3, GRADE),
    "q_orbit":     ("o2g_mid.mp4", 10.0, 1.0, GRADE),
    "q_coast":     ("b2_back.mp4", 5.0, 1.0, GRADE),
    "q_close":     ("o2_close.mp4", 20.0, 1.0, GRADE),
    "q_forest":    ("take1.mp4", 252.0, 1.0, GRADE),
    "cave_death":  ("take2.mp4", 133.7, 9.0, CAVE),
    "reborn_back": ("take2.mp4", 330.0, 6.5, GRADE),
    "c_ocean":     ("d2_fly.mp4", 2.0, 3.3, GRADE),
    "c_grove":     ("o2g_high.mp4", 15.0, 1.8, GRADE),
    "c_crane":     ("c1g_crane.mp4", 4.0, 3.3, GRADE),
    "c_pair":      ("take1.mp4", 741.5, 1.8, GRADE),
    "c_table":     ("b1g_back.mp4", 4.0, 1.8, GRADE),
    "c_orbit":     ("o1g_mid.mp4", 8.0, 1.8, GRADE),
    "end_ocean":   ("d2_fly.mp4", 20.0, 8.0, GRADE),
}


def cut(name: str) -> None:
    src, ss, dur, vf = SHOTS[name]
    DST.mkdir(parents=True, exist_ok=True)
    out = DST / f"{name}.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{ss}", "-i", str(SRC / src), "-t", f"{dur}",
           "-vf", f"{vf},fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
           "-g", "15", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"{name}: {r.stderr[-800:]}")
    print(out.name)


if __name__ == "__main__":
    for n in (sys.argv[1:] or SHOTS):
        cut(n)
