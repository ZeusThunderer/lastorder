"""Storyboard sheet from a rendered film: one frame every STEP s with its timecode (T-067 films).
    python dev/video/films/board.py b45 3.0  ->  dev/video/films/b45/storyboard.jpg"""
import subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
film, step = sys.argv[1], float(sys.argv[2])
vid = HERE.parent / "out" / f"film-{film}.mp4"
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(vid)],
                           capture_output=True, text=True).stdout)
ts = [round(0.4 + k * step, 2) for k in range(int((dur - 0.5) / step) + 1)]
tw, th, cols = 384, 216, 6
sheet = Image.new("RGB", (cols * tw, -(-len(ts) // cols) * (th + 24)), (16, 16, 18)); d = ImageDraw.Draw(sheet)
font = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", 17)
for i, t in enumerate(ts):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t}", "-i", str(vid), "-frames:v", "1", "-vf", f"scale={tw}:{th}",
                    "C:/tmp/_b.png"], check=True)
    x, y = (i % cols) * tw, (i // cols) * (th + 24)
    sheet.paste(Image.open("C:/tmp/_b.png"), (x, y))
    d.text((x + 4, y + th + 2), f"{int(t // 60):02d}:{t % 60:05.2f}", font=font, fill=(63, 211, 122))
out = HERE / film / "storyboard.jpg"; sheet.save(out, quality=78); print(out)
