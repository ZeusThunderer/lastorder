"""Fine contact sheet of a take range: python fine.py TAKE START END STEP NAME (T-067 films)."""
import subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
F = Path(__file__).resolve().parents[1] / "out/footage"
take, a, b, st, name = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
ts = []; t = a
while t <= b: ts.append(t); t += st
tw, th, cols = 320, 180, 6
sheet = Image.new("RGB", (cols * tw, -(-len(ts) // cols) * (th + 20)), (16, 16, 18)); d = ImageDraw.Draw(sheet)
font = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", 15)
for i, t in enumerate(ts):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(F / f"{take}.mp4"), "-frames:v", "1",
                    "-vf", f"scale={tw}:{th}", "C:/tmp/_f.png"], check=True)
    x, y = (i % cols) * tw, (i // cols) * (th + 20)
    sheet.paste(Image.open("C:/tmp/_f.png"), (x, y)); d.text((x + 4, y + th + 1), f"{t:7.1f}s", font=font, fill=(230, 230, 230))
out = F / f"fine-{name}.jpg"; sheet.save(out, quality=80); print(out)
