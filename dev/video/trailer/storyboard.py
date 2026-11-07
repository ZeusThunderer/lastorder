"""Storyboard sheet from the rendered trailer: one frame per beat with timecode and note (T-067)."""
import subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
VID = HERE.parents[0] / "out/trailer.mp4"
BEATS = [
    (1.6, "Хук: две сестры; строка смерти печатается"), (5.5, "«За 36 минут до этого»"),
    (8.6, "Рождение 00160 — строка журнала"), (13.5, "00160 цитирует завещание 00157"),
    (17.2, "Удар музыки, вспышка: LAST ORDER"), (21.3, "«Один разум» — две сестры у оврага"),
    (27.6, "Встреча: «вижу тебя»"), (31.8, "Уголь: «факт в базу»"),
    (35.95, "Glitch на границе секции"), (40.2, "Строка смерти и «прости» на чёрном"),
    (44.0, "Пещера: «через 9 минут — её часы»"), (49.6, "Смерть по часам: вещи падают"),
    (53.4, "Завещание 00160: урок"), (55.25, "Световая утечка: рождение 00163"),
    (59.0, "00163 берёт урок первой фразой"), (63.0, "«Смерть — не конец»"),
    (67.5, "«Это передача смены»"), (71.6, "«И решает сама»"),
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
