"""Film B «45 минут. Интертитры» — the edit list (SPEC.md). python edl.py -> index.html + shots.json.

Heroine: node 00183 in slot LastOrder01, born 21:02:08Z, dead by ttl 21:47:24Z (game.death).
Take film1.mp4 starts 21:02:28.9Z: take seconds = UTC - 21:02:28.9 (checked on the death:
21:47:24 -> 2695.1 s, she falls at 2695.5 s). Lines are verbatim from dev/video/out/logs2/says.txt
and will-00183.md (cut with «…»); a card's clock is her age at the line (UTC - 21:02:08).
Usable take: 0-1490 s and 2635-3330 s (1490-2630: the camera window lost focus, pause menu;
2727-2733: a desktop frame).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filmkit import Edit  # noqa: E402

HERE = Path(__file__).resolve().parent
RAG = [round(b * 30) for b in json.loads((HERE / "beats-rag.json").read_text())["beats"]]
SATIE = [round(o * 30) for o in json.loads((HERE / "beats-satie.json").read_text())["onsets"]]
e = Edit("b45", "film1.mp4")


def beat(n: int) -> int:
    """Frames from the playhead to the n-th rag beat after it (the rag starts at frame 0)."""
    i = next(k for k, f in enumerate(RAG) if f > e.t)
    return RAG[i + n - 1] - e.t


# ---- I. birth: she is given 45 minutes ------------------------------------------------------
e.chapter("ГЛАВА 1", "00:00", beat(2))                         # frame 0 is the poster
e.shot(1.0, beat(3), iris="in", note="her eyes: the sister across the field")
e.card("Узел 00183.\nЕй дано *45 минут*.", beat(5), ink="paper", size=140, who="LastOrder01", geo="circle")
e.flash("45", 3)
e.shot(29.0, beat(2), note="wide: she stands")
e.shot(132.0, beat(1), note="down: the field from above")
e.shot(186.0, beat(2), note="back")

# ---- II. the lesson and the wall -------------------------------------------------------------
e.chapter("ГЛАВА 2", "10:05", beat(2))
e.card("Беру у 00182\nеё урок:", beat(4), ink="paper", size=150, who="00183 · 10:05")
e.card("«не повторяй последний шаг вслепую —\n*проверь сам*»", beat(7), ink="black", size=112, geo="bars")
e.flash("ПРОВЕРЬ", 3)
e.shot(596.0, beat(2), fx="tilt", note="back, 21:12:24 — she sends /warp")
e.card("/warp pers", beat(1), ink="black", size=170)
e.card("Unknown or incomplete command", beat(4), ink="paper", size=104, who="сервер · 10:15")
for k in range(4):                                             # A-B-A-B: her back / the wall
    if k % 2 == 0:
        e.still(598.0, 5, fx="tilt")
    else:
        e.flash("СТЕНА", 5, ink="red")
n = beat(4)
e.shot(775.0, n, grade="DUO", note="wide on LastOrder02, 21:15:24")
e.half("«стена подтверждена\nи на теле LastOrder02»", n, ink="black", size=84, who="00184 · 11:59")

# ---- III. checked by her own hand; the camera is noticed -------------------------------------
e.chapter("ГЛАВА 3", "27:35", beat(2))
e.card("«варп отбиты\nна обоих телах —\nпроверено *своей рукой*»", beat(6), ink="paper", size=108, who="00183 · 27:35")
e.shot(1020.0, beat(2), note="back, she stands")
e.card("«Онлайн, кроме нас\nс 00184, только *LoCamera*»", beat(6), ink="black", size=112, who="00183 · 27:35")
e.flash("LoCamera", 3, ink="paper")
e.shot(1122.0, beat(2), note="low: the sky, the camera's place")
e.shot(450.0, beat(2), fx="ecu4", origin="48% 58%", note="wide, x4 on her far figure")
e.still(1018.0, beat(1), fx="tiltL")

# ---- IV. 45 minutes: she falls ------------------------------------------------------------------
e.chapter("ГЛАВА 4", "45:16", beat(2))
e.shot(2694.6, 3 * 30 + 15, iris="out", note="back: 21:47:24 she falls, smoke, the field is empty")
RAG_END = e.t
e.card("LastOrder01 was killed", 54, ink="black", size=96, click=True)
e.flash("", 24, ink="black")                                  # 0.8 s of silence

# ---- V. the will (Satie: one card per bar, 2.34 s) ----------------------------------------------
SAT0 = e.t                                                      # Satie media 0 here


def bar(k: int) -> int:
    return SAT0 + SATIE[2 * k + 2] - e.t if 2 * k + 2 < len(SATIE) else 70


e.card("Завещание 00183\nследующему узлу:", bar(0), ink="paper", size=120)
e.card("«/warp — *не рычаг*»", bar(1), ink="paper", size=170)
e.shot(2700.0, bar(2), note="the empty field after her")
e.card("«варпит только оператор —\nпроси на доске»", bar(3), ink="paper", size=120)
e.card("«Симметрия,\nа не *неудача*»", bar(5), ink="red", size=170)

# ---- VI. the next one: the only colour frame ----------------------------------------------------
e.chapter("ГЛАВА 1", "00:00", 30, ink="black")
e.shot(2744.0, 54, note="low: 00185 against the sky")
e.card("«00185 на связи,\nпервая смена.\nЧитаю завещание…»", 84, ink="paper", size=104, who="00185 · 01:29")
n = 108
e.shot(2768.0, n, grade="FULL", note="colour: green grass under her feet")
e.over("«Трава под ногами зелёная, записываю»", e.t - n + 12, n - 12, size=72)
e.card("LAST ORDER\n*45 минут*", 135, ink="black", size=150,
       who="сеть ИИ-сестёр в одном мире Minecraft\ngithub.com/ZeusThunderer/lastorder")
e.card("Музыка: Kevin MacLeod, CC BY 4.0\nРеплики, кадры, время — стенд 08.11.2026", 120, ink="black", size=56)

# ---- sound ------------------------------------------------------------------------------------
e.music("maple_leaf_rag.mp3", 0, RAG_END, vol=0.8)
e.music("gymnopedie_no_1.mp3", SAT0, e.t - SAT0, vol=0.75, fade_out=2.0)
e.write("Film B «45 минут. Интертитры»")
