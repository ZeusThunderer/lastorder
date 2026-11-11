"""Cut and grade the films' shots from the GPU takes (T-067 films, 2026-11-08).

    python dev/video/films/cut.py b45|kinoglaz [NAME ...]

Shot lists live in <film>/shots.json: {"name": [source, start_s, dur_s, grade, extra_vf?]}.
start_s is seconds into the source file (dev/video/out/footage/<source>). Grades are baked here
(ffmpeg, encoded on the GPU with h264_nvenc) so the composition only places clips:
  MONO  silent-film black and white, steep S-curve
  DUO   two-ink poster: black -> red #D2261E -> paper #EDE6D6
  FULL  untouched colour (one shot per film at most)
  NMONO, NDUO  the same after a shadow lift, for the night/rain part of the take
Clips go to <film>/assets/clips (not in git).
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[0] / "out/footage"

MONO = ("format=gray,curves=all='0/0 0.25/0.13 0.5/0.5 0.75/0.87 1/1',"
        "eq=contrast=1.2:gamma=0.95,format=yuv420p")
# gradient map on luma: 0 -> #111111, 110 -> #D2261E, 175+ -> #EDE6D6 (paper holds the highlights)
def _ramp(a: int, b: int, c: int) -> str:
    return f"'if(lt(val,110),{a}+val*({b}-{a})/110,if(lt(val,175),{b}+(val-110)*({c}-{b})/65,{c}))'"
DUO = ("format=gray,curves=all='0/0 0.3/0.2 0.7/0.8 1/1',format=rgb24,"
       f"lutrgb=r={_ramp(17, 210, 237)}:g={_ramp(17, 38, 230)}:b={_ramp(17, 30, 214)},format=yuv420p")
FULL = "eq=contrast=1.06:saturation=1.1,format=yuv420p"
# night/rain takes (after 21:50Z): lift the shadows first, then the same grades
LIFT = "curves=all='0/0 0.08/0.3 0.25/0.62 0.6/0.9 1/1',"
GRADES = {"MONO": MONO, "DUO": DUO, "FULL": FULL, "NMONO": LIFT + MONO, "NDUO": LIFT + DUO}


def cut(film: str, name: str, spec: list) -> None:
    src, ss, dur, grade, *extra = spec
    vf = ",".join([*(e for e in extra if e), GRADES[grade], "fps=30"])
    out = HERE / film / "assets/clips" / f"{name}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not dur:   # freeze frame
        out = out.with_suffix(".png")
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{ss}", "-i", str(SRC / src), "-frames:v", "1",
               "-vf", vf.replace("format=yuv420p", "format=rgb24"), str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"{name}: {r.stderr[-800:]}")
        print(out.name)
        return
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{ss}", "-i", str(SRC / src), "-t", f"{dur}",
           "-vf", vf, "-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "18", "-b:v", "0",
           "-g", "15", "-bf", "0", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"{name}: {r.stderr[-800:]}")
    print(out.name)


if __name__ == "__main__":
    film, names = sys.argv[1], sys.argv[2:]
    shots = json.loads((HERE / film / "shots.json").read_text(encoding="utf-8"))
    for n in (names or shots):
        cut(film, n, shots[n])
