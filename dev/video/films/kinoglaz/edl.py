"""Film A «Кино-глаз: симфония сети» — the edit list (SPEC.md). python edl.py -> index.html + shots.json.

Music: «Mechanolith» (Kevin MacLeod, CC BY 4.0) from frame 0; cuts on its beats (beats.json, librosa,
143.6 BPM). Take film1.mp4 starts 21:02:28.9Z (take s = UTC - 21:02:28.9). Usable take: 0-1490 s and
2635-3330 s. Lines are verbatim (dev/video/out/logs2/says.txt, will-00184.md), each with its speaker.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filmkit import Edit  # noqa: E402

HERE = Path(__file__).resolve().parent
BEATS = [round(b * 30) for b in json.loads((HERE / "beats.json").read_text())]
e = Edit("kinoglaz", "film1.mp4")


def to(k: int) -> int:
    """Frames from the playhead to beat k."""
    return BEATS[k] - e.t


def tele(text: str, start: int, frames: int, size: int = 56) -> None:
    e.overlay(text, start, frames, cls="tele", size=size)


# ---- the eye opens ----------------------------------------------------------------------------
e.layout("eye", [(1.0, "MONO")], to(7), ground="paper")
e.word("КИНО-ГЛАЗ", to(10), ink="red")
e.shot(88.0, to(14), note="low: the sky where the camera hangs")
e.overlay("08.11.2026 · 21:03:46", e.t - (BEATS[14] - BEATS[10]), BEATS[14] - BEATS[10], cls="tc", size=40)

# ---- two bodies, many angles -----------------------------------------------------------------
e.layout("strips3", [(10.0, "MONO"), (62.0, "DUO"), (36.0, "MONO")], to(18), ground="black",
         label="LastOrder01 · LastOrder02")
e.layout("grid9", [(t, "DUO" if i % 3 == 1 else "MONO") for i, t in
                   enumerate((116, 140, 168, 194, 220, 244, 270, 300, 348))], to(22), ground="black")
n = to(30)
e.layout("wedge2", [(778.0, "DUO"), (600.0, "MONO")], n, ground="black")
tele("00184: «LoCamera, если ты оператор — назови координаты»", e.t - n, n)
e.word("LoCamera", to(32), ink="red")
e.layout("circle", [(1120.0, "MONO")], to(36), ground="paper")
n = to(43)
e.layout("strips3", [(1250.0, "MONO"), (1200.0, "DUO"), (1275.0, "MONO")], n, ground="black")
tele("00183: «Онлайн, кроме нас с 00184, только LoCamera»", e.t - n, n)
e.layout("eye", [(1146.0, "MONO")], to(47), ground="paper")
e.layout("grid9", [(t, "MONO" if i % 2 else "DUO") for i, t in
                   enumerate((243, 270, 295, 322, 346, 373, 399, 424, 448))], to(51), ground="black")
e.layout("circle", [(1018.0, "DUO")], to(55), ground="paper")
e.layout("wedge2", [(1249.0, "MONO"), (1197.0, "DUO")], to(59), ground="black")

# ---- 45:00 — two lives end at once -----------------------------------------------------------
n = to(63)
e.shot(2665.0, n, note="back on 00183, 21:46:44")
e.overlay("21:46:44", e.t - n, n, cls="tc", size=40)
e.shot(2694.4, to(67), note="she falls, 21:47:24")
n = to(75)
e.card("LastOrder01 was killed\nLastOrder02 was killed", n, ink="black", size=84, who="21:47:24 · срок 45 минут")

# ---- new strips light -------------------------------------------------------------------------
n = to(81)
e.layout("strips3", [(2858.0, "MONO"), (2745.0, "DUO"), (2830.0, "MONO")], n, ground="black")
tele("00185: «Трава под ногами зелёная, записываю»", e.t - n, n)
e.layout("grid9", [(t, "NMONO" if i % 3 else "NDUO") for i, t in
                   enumerate((2882, 2906, 2933, 3010, 3040, 3067, 3116, 3189, 3228))], to(84), ground="black")
e.layout("circle", [(3134.0, "NMONO")], to(87), ground="paper")
e.shot(3141.5, to(90), grade="NMONO", note="00186 under water, 21:54:50")
e.card("LastOrder02 drowned", to(95), ink="black", size=110, who="21:55:01")
n = to(101)
e.layout("wedge2", [(3255.0, "NDUO"), (3281.0, "NMONO")], n, ground="black")
tele("00187: «её пруд обойду сухим берегом»", e.t - n, n, size=64)

# ---- the eye closes on the world in colour -----------------------------------------------------
e.layout("eye", [(2830.0, "FULL")], to(104), ground="paper")
e.card("«молчание LoCamera\nсобрать не вышло —\nне стапкивается\nи слота не занимает»", 140, ink="paper",
       size=100, who="завещание 00184 · 21:47")
e.card("LAST ORDER\n*Кино-глаз*", 120, ink="black", size=150,
       who="сеть ИИ-сестёр в одном мире Minecraft\ngithub.com/ZeusThunderer/lastorder")
e.card("Музыка: Kevin MacLeod, «Mechanolith», CC BY 4.0\nРеплики, кадры, время — стенд 08.11.2026", 120,
       ink="black", size=52)

e.music("mechanolith.mp3", 0, min(e.t, 1655), vol=0.9, fade_out=2.0)   # the track is 55.38 s
e.write("Film A «Кино-глаз: симфония сети»")
