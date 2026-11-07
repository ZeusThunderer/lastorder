"""Build the T-067 intro video from dev/video/cut.json (edit decision list) and local footage.

    python dev/video/build_intro.py [--cut dev/video/cut.json] [--only ID,ID] [--preset lab|misaka]

Cards: HTML (dev/video/cards/card.css) -> PNG through headless Edge/Chrome.
Clips: cut from the spectator camera takes by UTC time (the take's start is in its shot log),
overlays (REC badge, captions, speech subtitles) burned with ASS. Every segment is encoded to the
same format, then concatenated and loudness-normalized. Output: dev/video/out/intro.mp4.
Node names come from the preset ({node:160} in any text), never hard-coded (ADR-018).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VID = ROOT / "dev" / "video"
OUT = VID / "out"
W, H, FPS, AR = 1920, 1080, 30, 48000
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe", "msedge", "chromium", "google-chrome"]
PRESETS = {"lab": "узел {s}", "misaka": "Мисака {s}"}


def utc(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        sys.exit(f"failed: {' '.join(map(str, cmd))}\n{r.stderr[-3000:]}")


def names(text: str, preset: str) -> str:
    return re.sub(r"\{node:(\d+)\}", lambda m: PRESETS[preset].format(s=f"{int(m.group(1)):05d}"), text)


# ---------- cards ----------

def browser() -> str:
    for b in BROWSERS:
        if Path(b).exists() or shutil.which(b):
            return b
    sys.exit("no Edge/Chrome for card rendering")


def card_html(c: dict, preset: str, step: int | None = None) -> str:
    e = lambda s: names(html.escape(str(s)), preset)  # noqa: E731
    # head: "TS  EVENT  rest" -> event in node colour, or <i> for warnings via "!" prefix
    head = ""
    if c.get("head"):
        h = e(c["head"])
        h = re.sub(r"\[\[(.+?)\]\]", r"<b>\1</b>", h)
        h = re.sub(r"\[!(.+?)!\]", r"<i>\1</i>", h)
        head = f'<div class="head">{h}</div>'
    rich = lambda s: re.sub(r"\[\[(.+?)\]\]", r'<span class="n">\1</span>',  # noqa: E731
                            re.sub(r"\[!(.+?)!\]", r'<span class="w">\1</span>', e(s)))
    t = c["type"]
    if t == "text":
        sub = f'<span class="sub">{rich(c["sub"])}</span>' if c.get("sub") else ""
        size = f' style="font-size:{c["size"]}px"' if c.get("size") else ""
        body = f'<div class="main"{size}>{rich(c["main"])}{sub}</div>'
    elif t == "lines":
        ps = []
        for i, ln in enumerate(c["lines"]):
            hide = step is not None and i > step
            ps.append(f'<p class="{"hide" if hide else ""}">{rich(ln)}</p>')
        body = f'<div class="lines">{"".join(ps)}</div>'
    elif t == "chain":
        items = []
        for i, it in enumerate(c["items"]):
            if i:
                items.append('<span class="arrow">→</span>')
            sm = f"<small>{rich(it['small'])}</small>" if it.get("small") else ""
            items.append(f'<div class="box {it.get("cls", "")}">{rich(it["label"])}{sm}</div>')
        sub = f'<div class="main" style="top:76%;font-size:44px;font-weight:400">{rich(c["main"])}</div>' if c.get("main") else ""
        body = f'<div class="center"><div class="row">{"".join(items)}</div></div>{sub}'
    elif t == "slots":
        top = ('<div class="row" style="margin-bottom:70px"><div class="box on">Last Order<small>администратор сети</small></div>'
               '<div class="box">hive<small>реестр · база · доска</small></div></div>')
        slots = "".join(f'<div class="box on">LastOrder0{i}<small>тело · сестра</small></div>' for i in (1, 2, 3))
        sub = f'<div class="main" style="top:82%;font-size:46px;font-weight:400">{rich(c["main"])}</div>' if c.get("main") else ""
        body = f'<div class="center" style="top:44%">{top}<div class="row">{slots}</div></div>{sub}'
    elif t == "skin":
        img = (ROOT / c["image"]).as_uri()
        crop = c.get("crop")  # [x, y, w, h] in source pixels; shown scaled to 680 px high
        if crop:
            x, y, w, h = crop
            k = 680 / h
            pic = (f'<div style="position:absolute;left:120px;top:200px;width:{w*k:.0f}px;height:680px;overflow:hidden">'
                   f'<img src="{img}" style="position:absolute;left:{-x*k:.0f}px;top:{-y*k:.0f}px;'
                   f'height:{c["src_h"]*k:.0f}px;image-rendering:pixelated"></div>')
        else:
            pic = f'<img class="skin" src="{img}">'
        sub = f'<span class="sub">{rich(c["sub"])}</span>' if c.get("sub") else ""
        body = f'{pic}<div class="side">{rich(c["main"])}{sub}</div>'
    elif t == "image":
        body = f'<img src="{(ROOT / c["image"]).as_uri()}" style="width:1920px;height:1080px;object-fit:contain">'
    else:
        sys.exit(f"unknown card type {t}")
    foot = f'<div class="foot">{e(c.get("foot", "last-order · журнал наблюдения"))}</div>'
    css = (VID / "cards" / "card.css").as_uri()
    return f'<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="{css}"></head><body>{head}{body}{foot}</body></html>'


def render_card(c: dict, name: str, preset: str, step: int | None = None) -> Path:
    d = OUT / "cards"
    d.mkdir(parents=True, exist_ok=True)
    src, png = d / f"{name}.html", d / f"{name}.png"
    src.write_text(card_html(c, preset, step), encoding="utf-8")
    run([browser(), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--window-size={W},{H}",
         "--force-device-scale-factor=1", f"--screenshot={png}", src.as_uri()])
    return png


# ---------- overlays (ASS) ----------

ASS_HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Segoe UI Semibold,46,&H00EAE8E8,&H00EAE8E8,&H66000000,&H66000000,0,0,0,0,100,100,0,0,4,14,0,2,220,220,70,1
Style: Cap,Segoe UI Semibold,54,&H00EAE8E8,&H00EAE8E8,&H80000000,&H80000000,0,0,0,0,100,100,0,0,4,18,0,8,200,200,90,1
Style: Badge,Cascadia Mono,26,&H00EAE8E8,&H00EAE8E8,&H80000000,&H80000000,0,0,0,0,100,100,0,0,4,8,0,7,48,48,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
GREEN, AMBER, RED = r"{\c&H7AD33F&}", r"{\c&H40A0F0&}", r"{\c&H4040E0&}"


def ass_t(x: float) -> str:
    x = max(0.0, x)
    return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"


def ass_text(s: str) -> str:
    s = s.replace("\\", "").replace("{", "(").replace("}", ")")
    s = re.sub(r"\[\[(.+?)\]\]", lambda m: GREEN + m.group(1) + r"{\c&HEAE8E8&}", s)
    s = re.sub(r"\[!(.+?)!\]", lambda m: AMBER + m.group(1) + r"{\c&HEAE8E8&}", s)
    return s.replace("\n", r"\N")


def clip_ass(seg: dict, t0: float, preset: str) -> str:
    dur = seg["dur"]
    ev = []
    if seg.get("badge"):
        b = seg["badge"]
        # the badge clock runs with the footage: one event per second
        for k in range(int(dur) + 1):
            ts = datetime.fromtimestamp(t0 + k, timezone.utc).strftime("%H:%M:%SZ")
            who = names(f"{{node:{b['serial']}}}", preset) if b.get("serial") else ""
            txt = f"{RED}●{{\\c&HEAE8E8&}} REC  {b['slot']}" + (f" · {who}" if who else "") + f" · {ts}"
            ev.append(f"Dialogue: 0,{ass_t(k)},{ass_t(min(k + 1, dur))},Badge,,0,0,0,,{txt}")
    for cap in seg.get("captions", []):
        a = cap.get("at", 0.0)
        ev.append(f"Dialogue: 1,{ass_t(a)},{ass_t(min(a + cap.get('dur', dur - a), dur))},Cap,,0,0,0,,{ass_text(names(cap['text'], preset))}")
    for sub in seg.get("subs", []):
        a = (utc(sub["at_utc"]) - t0) if "at_utc" in sub else sub.get("at", 0.0)
        txt = names(sub["text"], preset)
        d = sub.get("dur") or max(2.5, len(txt) / 15)
        who = f"[[{names('{node:%d}' % sub['serial'], preset)}:]] " if sub.get("serial") else ""
        ev.append(f"Dialogue: 2,{ass_t(a)},{ass_t(min(a + d, dur))},Sub,,0,0,0,,{ass_text(who + txt)}")
    return ASS_HEAD + "\n".join(ev) + "\n"


# ---------- segments ----------

ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2"]


def fades(seg: dict, dur: float) -> tuple[str, str]:
    v, a = [], []
    if seg.get("fade_in", True):
        v.append("fade=t=in:st=0:d=0.27"); a.append("afade=t=in:st=0:d=0.27")
    if seg.get("fade_out", True):
        v.append(f"fade=t=out:st={dur - 0.27:.2f}:d=0.27"); a.append(f"afade=t=out:st={dur - 0.27:.2f}:d=0.27")
    return ",".join(v), ",".join(a)


def take_t0(cut: dict, take: str) -> tuple[Path, float]:
    tk = cut["takes"][take]
    return OUT / tk["file"], utc(tk["start_utc"])


def build_segment(seg: dict, cut: dict, preset: str) -> Path:
    sd = OUT / "seg"
    sd.mkdir(parents=True, exist_ok=True)
    out = sd / f"{seg['id']}.mp4"
    dur = float(seg["dur"])
    vf_f, af_f = fades(seg, dur)
    if seg["kind"] == "card":
        c = seg["card"]
        steps = len(c["lines"]) if c["type"] == "lines" and c.get("reveal") else 0
        if steps:  # line-by-line reveal: one PNG per step, equal shares, last one holds longer
            pngs = [render_card(c, f"{seg['id']}-{i}", preset, i) for i in range(steps)]
            share = dur / (steps + 1)
            lst = sd / f"{seg['id']}.txt"
            lst.write_text("".join(f"file '{p.as_posix()}'\nduration {share * (2 if i == steps - 1 else 1):.3f}\n"
                                   for i, p in enumerate(pngs)) + f"file '{pngs[-1].as_posix()}'\n", encoding="utf-8")
            vin = ["-f", "concat", "-safe", "0", "-i", str(lst)]
        else:
            vin = ["-loop", "1", "-framerate", str(FPS), "-i", str(render_card(c, seg["id"], preset))]
        bed = seg.get("audio_bed")  # game sound under the card, ducked
        if bed:
            f, t0 = take_t0(cut, bed["take"])
            ain = ["-ss", f"{utc(bed['at_utc']) - t0:.3f}", "-i", str(f)]
            af = f"volume={bed.get('db', -18)}dB" + ("," + af_f if af_f else "")
            amap = "1:a"
        else:
            ain = ["-f", "lavfi", "-i", f"anullsrc=r={AR}:cl=stereo"]
            af, amap = af_f or "anull", "1:a"
        pad = f"fps={FPS},tpad=stop_mode=clone:stop_duration={dur:.3f}," if steps else ""
        vf = f"{pad}scale={W}:{H},format=yuv420p" + ("," + vf_f if vf_f else "")
        run(["ffmpeg", "-y", *vin, *ain, "-t", f"{dur:.3f}", "-map", "0:v", "-map", amap,
             "-vf", vf, "-af", af, *ENC, str(out)])
    elif seg["kind"] == "clip":
        f, t0 = take_t0(cut, seg["take"])
        start = utc(seg["at_utc"]) if "at_utc" in seg else t0 + float(seg["at"])
        ass = sd / f"{seg['id']}.ass"
        ass.write_text(clip_ass(seg, start, preset), encoding="utf-8-sig")
        vf = f"scale={W}:{H},fps={FPS},ass={ass.name}" + ("," + vf_f if vf_f else "")
        # game sound is sparse and distance-attenuated: lift it, a limiter keeps the peaks
        af = f"volume={seg.get('db', 15)}dB,alimiter=limit=0.89" + ("," + af_f if af_f else "")
        run(["ffmpeg", "-y", "-ss", f"{start - t0:.3f}", "-i", str(f), "-t", f"{dur:.3f}",
             "-vf", vf, "-af", af, *ENC, out.name], cwd=sd)
    else:
        sys.exit(f"unknown segment kind {seg['kind']}")
    return out


def storyboard(segs: list[dict], files: list[Path], dest: Path) -> None:
    """One frame from the middle of every segment, with its timecode and note: the storyboard
    always matches the cut it was built from."""
    from PIL import Image, ImageDraw, ImageFont
    tw, th, cols, pad, cap = 480, 270, 4, 16, 96
    rows = -(-len(segs) // cols)
    sheet = Image.new("RGB", (cols * (tw + pad) + pad, rows * (th + cap + pad) + pad), (23, 24, 29))
    dr = ImageDraw.Draw(sheet)
    f1 = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", 18) if os.name == "nt" else ImageFont.load_default()
    f2 = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 17) if os.name == "nt" else ImageFont.load_default()
    t = 0.0
    sd = OUT / "storyboard"
    sd.mkdir(parents=True, exist_ok=True)
    for i, (s, p) in enumerate(zip(segs, files)):
        fr = sd / f"{i:02d}-{s['id']}.png"
        run(["ffmpeg", "-y", "-ss", f"{float(s['dur']) / 2:.2f}", "-i", str(p), "-frames:v", "1", str(fr)])
        x, y = pad + (i % cols) * (tw + pad), pad + (i // cols) * (th + cap + pad)
        sheet.paste(Image.open(fr).resize((tw, th)), (x, y))
        tc = f"{int(t // 60):02d}:{t % 60:05.2f} +{float(s['dur']):.1f}s  {s['id']}"
        dr.text((x, y + th + 6), tc, font=f1, fill=(63, 211, 122))
        note = s.get("note", "")
        lines, cur = [], ""
        for w in note.split():
            if len(cur) + len(w) > 52:
                lines.append(cur); cur = w
            else:
                cur = f"{cur} {w}".strip()
        lines.append(cur)
        for k, ln in enumerate(lines[:3]):
            dr.text((x, y + th + 32 + k * 20), ln, font=f2, fill=(232, 232, 234))
        t += float(s["dur"])
    sheet.save(dest, quality=82)
    print(dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cut", default=str(VID / "cut.json"))
    ap.add_argument("--only", default="")
    ap.add_argument("--preset", default=None)
    ap.add_argument("--storyboard", default="", help="also write a storyboard sheet (jpg) here")
    a = ap.parse_args()
    cut = json.loads(Path(a.cut).read_text(encoding="utf-8"))
    preset = a.preset or cut.get("preset", "lab")
    segs = [s for s in cut["segments"] if not s.get("skip")]
    only = set(filter(None, a.only.split(",")))
    files = []
    for s in segs:
        p = OUT / "seg" / f"{s['id']}.mp4"
        if not only or s["id"] in only or not p.exists():
            print(f"segment {s['id']} ({s['kind']}, {s['dur']} s)", flush=True)
            p = build_segment(s, cut, preset)
        files.append(p)
    if a.storyboard:
        storyboard(segs, files, Path(a.storyboard))
    lst = OUT / "seg" / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in files), encoding="utf-8")
    joined = OUT / "seg" / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    final = OUT / "intro.mp4"
    run(["ffmpeg", "-y", "-i", str(joined), "-c:v", "copy", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-movflags", "+faststart", str(final)])
    print(final)


if __name__ == "__main__":
    main()
