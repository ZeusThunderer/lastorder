"""Storyboard sheet from the rendered trailer: one frame per beat with timecode and note (T-067)."""
import subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
VID = HERE.parents[0] / "out/trailer.mp4"
BEATS = [
    (1.6, "Хук: две сестры; строка смерти печатается"), (5.5, "«За 36 минут до этого»"),
    (8.6, "«LastOrder01 — не игрок, а ИИ-агент»"), (14.5, "«45 минут жизни»; 00160 берёт завет 00156"),
    (17.2, "Удар музыки, вспышка: LAST ORDER"), (21.3, "«Две сестры» — у оврага"),
    (27.6, "«Договариваются в чате»; «вижу тебя»"), (31.8, "Уголь: находка — в общую базу"),
    (34.5, "«Еда кончается… охота на свинью»"), (40.6, "Строка смерти, «прости», attack без цели"),
    (44.0, "Пещера: «45 минут истекли»"), (49.6, "Смерть по сроку: вещи падают"),
    (53.4, "Завещание 00160: урок"), (56.5, "Световая утечка: «Тело то же. Сестра новая»"),
    (59.5, "00163 первой фразой берёт урок"), (63.0, "«Сёстры умирают»"),
    (67.5, "«Знание остаётся»"), (71.6, "«И решает сама»"),
    (73.45, "Зум-проход в финал"), (79.0, "Финальная карточка, github, атрибуция"),
]
tw, th, cols, cap, pad = 480, 270, 4, 64, 14
rows = -(-len(BEATS) // cols)
sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + cap + pad) + pad), (16, 16, 18))
d = ImageDraw.Draw(sheet)
f1 = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", 18); f2 = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 17)
for i, (t, note) in enumerate(BEATS):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(VID), "-frames:v", "1", "-vf", f"scale={tw}:{th}", "C:/tmp/_sb.png"], check=True)
    x, y = pad + (i % cols) * (tw + pad), pad + (i // cols) * (th + cap + pad)
    sheet.paste(Image.open("C:/tmp/_sb.png"), (x, y))
    d.text((x, y + th + 6), f"{int(t//60):02d}:{t%60:05.2f}", font=f1, fill=(63, 211, 122))
    d.text((x, y + th + 32), note, font=f2, fill=(232, 232, 234))
out = HERE / "film/storyboard.jpg"
sheet.save(out, quality=78); print(out)
