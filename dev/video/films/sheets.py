"""Contact sheets of the film take: one frame per director shot (+offset), labelled with take time,
UTC and shot -> dev/video/out/footage/sheet-N.jpg (T-067 films)."""
import datetime, json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
F = Path(__file__).resolve().parents[1] / "out/footage"
TAKE, OFF = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
T0 = datetime.datetime(2026, 11, 8, 21, 2, 28, 900000, tzinfo=datetime.timezone.utc)
rows = [json.loads(l) for l in (F / f"{TAKE}.shots.jsonl").open(encoding="utf-8")]
rows = [r for r in rows if r["shot"] in ("pov", "back", "wide", "low", "down")]
tw, th, cols, per = 320, 180, 6, 36
font = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", 15)
for s in range(0, len(rows), per):
    chunk = rows[s:s + per]
    sheet = Image.new("RGB", (cols * tw, -(-len(chunk) // cols) * (th + 22)), (16, 16, 18))
    d = ImageDraw.Draw(sheet)
    for i, r in enumerate(chunk):
        t = (datetime.datetime.fromisoformat(r["t"].replace("Z", "+00:00")) - T0).total_seconds() + OFF
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.1f}", "-i", str(F / f"{TAKE}.mp4"), "-frames:v", "1",
                        "-vf", f"scale={tw}:{th}", "C:/tmp/_s.png"], check=True)
        x, y = (i % cols) * tw, (i // cols) * (th + 22)
        sheet.paste(Image.open("C:/tmp/_s.png"), (x, y))
        d.text((x + 4, y + th + 2), f"{t:6.0f}s {r['t'][11:19]} {r['slot'][-2:]} {r['shot']}", font=font, fill=(230, 230, 230))
    out = F / f"sheet-{s // per + 1}.jpg"; sheet.save(out, quality=80); print(out)
