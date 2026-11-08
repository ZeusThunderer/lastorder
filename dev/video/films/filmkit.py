"""Shared builder for the T-067 films (2026-11-08): an edit list in frames -> index.html + shots.json.

A film's edl.py builds a list of events with `Edit` and calls `write()`. Every timed element is
written into the HTML with a stable id (Studio edits the tags). Footage shots name a time in the
take (seconds); write() also emits shots.json for cut.py, which bakes the grade into the clips.

Look (SPEC.md): silent-film print + Constructivist letterpress. Three inks: paper #EDE6D6,
black #111111, red #D2261E. Cards are printed (appear whole), cuts are splices, the iris is the
only soft transition. Gate weave and flicker are seeded by the frame number.
"""
from __future__ import annotations

import html
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

FPS = 30
INK = {"paper": "#EDE6D6", "black": "#111111", "red": "#D2261E"}
TEXT_ON = {"paper": "black", "black": "paper", "red": "black"}
GRADING = {"preset": None, "intensity": 1,
           "adjust": {k: 0 for k in ("exposure", "contrast", "highlights", "shadows", "whites", "blacks",
                                     "temperature", "tint", "vibrance", "saturation")},
           "details": {"vignette": 0.18, "vignetteMidpoint": 0.5, "vignetteRoundness": 0.3,
                       "vignetteFeather": 0.65, "grain": 0.22, "grainSize": 0.3, "grainRoughness": 0.5},
           "effects": {"filmArtifacts": 0.35}, "lut": None, "colorSpace": "rec709"}


def sec(frames: int) -> str:
    return f"{frames / FPS:.4f}".rstrip("0").rstrip(".") or "0"


def esc(s: str) -> str:
    """Escape, then *word* -> red ink (printed 3 px off register); a dash never starts a line."""
    s = s.replace(" —", " —")
    return re.sub(r"\*([^*]+)\*", r'<span class="r">\1</span>', html.escape(s)).replace("\n", "<br>")


@dataclass
class Edit:
    film: str
    take: str                                   # source file in dev/video/out/footage
    t: int = 0                                  # playhead, frames
    els: list = field(default_factory=list)     # (z, html)
    shots: dict = field(default_factory=dict)   # name -> [take, ss, dur, grade, extra_vf]
    tween: list = field(default_factory=list)   # js lines
    audio: list = field(default_factory=list)
    n: int = 0

    def _id(self, kind: str) -> str:
        self.n += 1
        return f"{kind}{self.n:03d}"

    # ---- footage -------------------------------------------------------------------------
    def shot(self, at: float, frames: int, grade: str = "MONO", fx: str = "", origin: str = "50% 50%",
             sound: float = 0.0, iris: str = "", note: str = "") -> str:
        """`at` = seconds into the take. fx: tilt | tiltL | ecu | ecu4 | '' ; iris: in | out | inout."""
        i = self._id("s")
        dur = frames / FPS
        self.shots[i] = [self.take, round(at, 3), round(dur + 0.2, 3), grade]
        style = {"tilt": "transform: rotate(14deg) scale(1.32)", "tiltL": "transform: rotate(-14deg) scale(1.32)",
                 "ecu": f"transform: scale(3); transform-origin: {origin}",
                 "ecu4": f"transform: scale(4); transform-origin: {origin}"}.get(fx, "")
        audio = f'data-has-audio="true" data-volume="{sound}"' if sound else "muted"
        g = html.escape(json.dumps(GRADING, separators=(",", ":")))
        self.els.append((10, f'<div class="shot" id="{i}w"><div class="fx" style="{style}">'
                             f'<video id="{i}" class="clip" src="assets/clips/{i}.mp4" data-start="{sec(self.t)}" '
                             f'data-duration="{sec(frames)}" data-track-index="1" {audio} playsinline '
                             f'data-color-grading="{g}"></video></div></div>'
                             + (f"<!-- {html.escape(note)} -->" if note else "")))
        self._vis(f"{i}w", self.t, frames)
        if iris in ("in", "inout"):
            self.tween.append(f'tl.fromTo("#{i}w",{{clipPath:"circle(0% at 50% 50%)"}},'
                              f'{{clipPath:"circle(75% at 50% 50%)",duration:0.5,ease:"expo.inOut"}},{sec(self.t)});')
        if iris in ("out", "inout"):
            self.tween.append(f'tl.fromTo("#{i}w",{{clipPath:"circle(75% at 50% 50%)"}},'
                              f'{{clipPath:"circle(0% at 50% 50%)",duration:0.5,ease:"expo.inOut",immediateRender:false}},'
                              f'{sec(self.t + frames - 15)});')
        self.t += frames
        return i

    def still(self, at: float, frames: int, grade: str = "MONO", fx: str = "", origin: str = "50% 50%") -> str:
        """Freeze frame: a PNG exported from the take (cut.py: dur 0 = still)."""
        i = self._id("f")
        self.shots[i] = [self.take, round(at, 3), 0, grade]
        style = {"tilt": "transform: rotate(14deg) scale(1.32)", "tiltL": "transform: rotate(-14deg) scale(1.32)",
                 "ecu": f"transform: scale(3); transform-origin: {origin}"}.get(fx, "")
        self.els.append((10, f'<div class="shot clip" id="{i}" data-start="{sec(self.t)}" data-duration="{sec(frames)}" '
                             f'data-track-index="1"><div class="fx" style="{style}">'
                             f'<img src="assets/clips/{i}.png" alt=""></div></div>'))
        self.t += frames
        return i

    # ---- film A layouts: several shots at once inside masks (no playhead move) --------------
    def vid(self, at: float, start: int, frames: int, grade: str = "MONO", style: str = "") -> str:
        """One timed <video> for a layout cell; returns its html."""
        i = self._id("v")
        self.shots[i] = [self.take, round(at, 3), round(frames / FPS + 0.2, 3), grade]
        g = html.escape(json.dumps(GRADING, separators=(",", ":")))
        return (f'<video id="{i}" class="clip" src="assets/clips/{i}.mp4" data-start="{sec(start)}" '
                f'data-duration="{sec(frames)}" data-track-index="1" muted playsinline style="{style}" '
                f'data-color-grading="{g}"></video>')

    def layout(self, kind: str, cells: list, frames: int, ground: str = "paper", label: str = "",
               slide: bool = True) -> str:
        """kind: strips3 | grid9 | wedge2 | circle | eye. cells: [(at, grade), ...] in reading order.
        The layout is one timed host; its cells are timed videos inside it."""
        i = self._id("l")
        t0 = self.t
        cell_html = []

        def deco(cls: str, inner: str = "") -> str:   # decorations are timed on their own
            return (f'<div class="{cls} clip" id="{self._id("d")}" data-start="{sec(t0)}" '
                    f'data-duration="{sec(frames)}" data-track-index="3">{inner}</div>')
        if kind == "strips3":       # three bands slanted 30°, footage kept upright inside
            for k, (at, gr) in enumerate(cells):
                v = self.vid(at, t0, frames, gr, "left:50%;top:50%;width:1920px;height:1080px;"
                                                   "transform:translate(-50%,-50%) rotate(-30deg) scale(1.15)")
                cell_html.append(f'<div class="band" style="left:{1500 - 360 + (k - 1) * 736 - 60}px">{v}</div>')
            inner = f'<div class="slant">{"".join(cell_html)}</div>'
        elif kind == "grid9":
            for k, (at, gr) in enumerate(cells):
                x, y = k % 3, k // 3
                v = self.vid(at, t0, frames, gr, "left:50%;top:50%;width:1920px;height:1080px;"
                                                   "transform:translate(-50%,-50%) scale(0.36)")
                cell_html.append(f'<div class="cell" style="left:{x * 648}px;top:{y * 368}px">{v}</div>')
            inner = "".join(cell_html)
        elif kind == "wedge2":      # a diagonal cut at 30°: left shot | right shot
            a = self.vid(cells[0][0], t0, frames, cells[0][1], "inset:0;width:1920px;height:1080px")
            b = self.vid(cells[1][0], t0, frames, cells[1][1], "inset:0;width:1920px;height:1080px")
            inner = f'<div class="wa">{a}</div><div class="wb">{b}</div>{deco("wline")}'
        elif kind == "circle":      # Lissitzky: footage in a circle, a wedge pierces it
            a = self.vid(cells[0][0], t0, frames, cells[0][1], "left:50%;top:50%;width:1920px;height:1080px;"
                                                                "transform:translate(-50%,-50%)")
            inner = f'<div class="disc">{a}</div>{deco("pierce")}'
        elif kind == "eye":         # the camera's eye: the aperture opens on the shot inside a red ring
            a = self.vid(cells[0][0], t0, frames, cells[0][1], "left:50%;top:50%;width:1920px;height:1080px;"
                                                                "transform:translate(-50%,-50%)")
            inner = f'{deco("ring")}<div class="eye" id="{i}e">{a}</div>'
            self.tween.append(f'tl.fromTo("#{i}e",{{clipPath:"circle(0px at 50% 50%)"}},{{clipPath:"circle(470px at 50% 50%)",'
                              f'duration:0.8,ease:"expo.inOut"}},{sec(t0)});')
        else:
            raise ValueError(kind)
        lab = deco("lab", esc(label)) if label else ""
        # the ground is the timed plate; the frame of cells is untimed and transparent (only the
        # timed videos and decorations inside it paint), so no <video> sits in a timed parent
        self.els.append((19, f'<div class="lay-g clip {ground}" id="{i}g" data-start="{sec(t0)}" '
                             f'data-duration="{sec(frames)}" data-track-index="2"></div>'))
        self.els.append((20, f'<div class="lay k-{kind}" id="{i}"><div class="layin" id="{i}i">'
                             f'{inner}</div>{lab}</div>'))
        if slide and kind in ("strips3", "wedge2"):   # bands arrive along the 30° axis, 6 frames
            self.tween.append(f'tl.fromTo("#{i}i",{{x:-260,y:150}},{{x:0,y:0,duration:0.2,ease:"expo.out"}},{sec(t0)});')
        self.t += frames
        return i

    def word(self, text: str, frames: int, ink: str = "paper", size: int = 360, angle: int = -30,
             stamp: str = "") -> str:
        """A beat word: Rubik Mono One on a field, slanted on the 30° axis; stamp = small mono timecode."""
        i = self._id("w")
        size = min(size, int(1750 / max(1, len(text)) / 0.86))
        st = f'<div class="stamp t-{TEXT_ON[ink]}">{esc(stamp)}</div>' if stamp else ""
        self.els.append((40, f'<div class="card clip {ink}" id="{i}" data-start="{sec(self.t)}" '
                             f'data-duration="{sec(frames)}" data-track-index="3"><div class="g bars"></div>'
                             f'<div class="bw t-{TEXT_ON[ink]}" style="font-size:{size}px;transform:translate(-50%,-50%) '
                             f'rotate({angle}deg)">{esc(text)}</div>{st}</div>'))
        self.t += frames
        return i

    def overlay(self, text: str, start: int, frames: int, cls: str = "tele", size: int = 64) -> str:
        """Text printed over whatever plays (chat telegraph line, timecode)."""
        i = self._id("o")
        self.els.append((60, f'<div class="{cls} clip" id="{i}" data-start="{sec(start)}" data-duration="{sec(frames)}" '
                             f'data-track-index="4" data-layout-allow-occlusion style="font-size:{size}px">{esc(text)}</div>'))
        return i

    # ---- cards (printed whole) -----------------------------------------------------------
    def card(self, text: str, frames: int, ink: str = "paper", size: int = 120, who: str = "",
             geo: str = "", top: int = 0, click: bool = False) -> str:
        """A line card. geo: circle | wedge | bars | '' (Lissitzky accents, kept off the text)."""
        i = self._id("c")
        g = {"circle": '<div class="g circle"></div>', "wedge": '<div class="g wedge"></div>',
             "bars": '<div class="g bars"></div>'}.get(geo, "")
        pos = f"top:{top}px" if top else "top:50%;transform:translateY(-50%)"
        pos += {"bars": ";right:720px", "wedge": ";right:700px", "circle": ";right:380px"}.get(geo, "")
        w = f'<div class="who">{esc(who)}</div>' if who else ""
        self.els.append((30, f'<div class="card clip {ink}" id="{i}" data-start="{sec(self.t)}" '
                             f'data-duration="{sec(frames)}" data-track-index="2">{g}'
                             f'<div class="txt t-{TEXT_ON[ink]}" style="font-size:{size}px;{pos}">{esc(text)}{w}</div></div>'))
        if click:
            self.click(self.t)
        self.t += frames
        return i

    def chapter(self, n: str, clock: str, frames: int, ink: str = "red") -> str:
        i = self._id("h")
        self.els.append((30, f'<div class="card clip {ink}" id="{i}" data-start="{sec(self.t)}" '
                             f'data-duration="{sec(frames)}" data-track-index="2"><div class="g wedge thin"></div>'
                             f'<div class="chap t-{TEXT_ON[ink]}">{esc(n)}</div>'
                             f'<div class="clock t-{TEXT_ON[ink]}">{esc(clock)}</div></div>'))
        self.click(self.t)
        self.t += frames
        return i

    def flash(self, word: str, frames: int = 3, ink: str = "red") -> str:
        i = self._id("x")
        body = f'<div class="flash t-{TEXT_ON[ink]}">{esc(word)}</div>' if word else ""
        self.els.append((40, f'<div class="card clip {ink}" id="{i}" data-start="{sec(self.t)}" '
                             f'data-duration="{sec(frames)}" data-track-index="3">{body}</div>'))
        self.click(self.t)
        self.t += frames
        return i

    def half(self, text: str, frames: int, ink: str = "black", size: int = 96, who: str = "") -> str:
        """Card on the left 46 % with a 30° edge, over the shot that plays under it (place after shot())."""
        i = self._id("p")
        start = self.t - frames
        w = f'<div class="who">{esc(who)}</div>' if who else ""
        self.els.append((35, f'<div class="half clip {ink}" id="{i}" data-start="{sec(start)}" '
                             f'data-duration="{sec(frames)}" data-track-index="2">'
                             f'<div class="txt t-{TEXT_ON[ink]}" style="font-size:{size}px">{esc(text)}{w}</div></div>'))
        return i

    def over(self, text: str, start: int, frames: int, size: int = 64, pos: str = "bottom") -> str:
        """A printed caption over footage (no backing box): paper ink with a hard black offset."""
        i = self._id("o")
        self.els.append((36, f'<div class="over clip {pos}" id="{i}" data-start="{sec(start)}" '
                             f'data-duration="{sec(frames)}" data-track-index="4" style="font-size:{size}px">{esc(text)}</div>'))
        return i

    # ---- sound ---------------------------------------------------------------------------
    def music(self, src: str, start: int, frames: int, media_start: float = 0, vol: float = 0.8,
              fade_out: float = 0) -> None:
        i = self._id("m")
        fo = f' data-fade-out="{fade_out}"' if fade_out else ""
        self.audio.append(f'<audio id="{i}" class="clip" src="assets/music/{src}" data-start="{sec(start)}" '
                          f'data-duration="{sec(frames)}" data-media-start="{media_start}" data-volume="{vol}"'
                          f'{fo} data-track-index="8" data-timeline-role="music"></audio>')

    def click(self, at: int, vol: float = 0.5) -> None:
        i = self._id("k")
        self.audio.append(f'<audio id="{i}" class="clip" src="assets/sfx/click.wav" data-start="{sec(at)}" '
                          f'data-duration="0.05" data-volume="{vol}" data-track-index="9"></audio>')

    def _vis(self, *_):
        pass

    # ---- output ----------------------------------------------------------------------------
    def write(self, title: str, end_pad: int = 0) -> None:
        total = self.t + end_pad
        here = Path(__file__).resolve().parent / self.film
        (here / "shots.json").write_text(json.dumps(self.shots, ensure_ascii=False, indent=1), encoding="utf-8")
        rnd = random.Random(1923)
        weave = [f"[{rnd.randint(-2, 2)},{rnd.randint(-2, 2)},{rnd.random() * 0.06:.3f}]" for _ in range(total)]
        body = "\n      ".join(h for _, h in sorted(self.els, key=lambda e: e[0]))
        page = TEMPLATE.replace("%TITLE%", html.escape(title)).replace("%DUR%", sec(total)) \
            .replace("%BODY%", body).replace("%AUDIO%", "\n    ".join(self.audio)) \
            .replace("%TWEENS%", "\n      ".join(self.tween)).replace("%WEAVE%", ",".join(weave))
        (here / "index.html").write_text(page, encoding="utf-8")
        print(f"{self.film}: {total} frames = {total / FPS:.2f} s, {len(self.shots)} media, {len(self.els)} elements")


TEMPLATE = """<!doctype html>
<html lang="ru" data-composition-variables='[
  {"id":"paper","type":"color","label":"Paper","default":"#EDE6D6"},
  {"id":"black","type":"color","label":"Black ink","default":"#111111"},
  {"id":"red","type":"color","label":"Red ink","default":"#D2261E"}]'>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=1920, height=1080" />
  <!-- %TITLE% — generated by edl.py (dev/video/films/filmkit.py); edit the edit list, not this file. -->
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"></script>
  <style>
    @font-face { font-family: "Playfair Display"; src: url("assets/fonts/PlayfairDisplay.ttf"); font-weight: 400 900; }
    @font-face { font-family: "Rubik Mono One"; src: url("assets/fonts/RubikMonoOne-Regular.ttf"); }
    @font-face { font-family: "Oswald"; src: url("assets/fonts/Oswald.ttf"); font-weight: 200 700; }
    :root { --paper: #EDE6D6; --black: #111111; --red: #D2261E; }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1920px; height: 1080px; overflow: hidden; background: var(--black); }
    #root { position: relative; width: 1920px; height: 1080px; overflow: hidden; background: var(--black); }
    #gate { position: absolute; inset: -4px; }
    .shot, .card, .half { position: absolute; inset: 0; overflow: hidden; }
    .fx { position: absolute; inset: 0; }
    .fx video, .fx img { position: absolute; inset: 0; width: 1920px; height: 1080px; object-fit: cover; }
    .paper { background: var(--paper) url("assets/paper.png"); }
    .black { background: var(--black); }
    .red { background: var(--red); }
    .t-black { color: var(--black); } .t-paper { color: var(--paper); } .t-red { color: var(--red); }
    .r { color: var(--red); position: relative; left: -3px; top: 2px; }
    .red .r, .t-red .r { color: var(--paper); }
    .txt { position: absolute; left: 160px; right: 200px; font-family: "Playfair Display", serif; font-weight: 900;
           line-height: 1.04; letter-spacing: -0.005em; z-index: 2; }
    .who { font-family: "IBM Plex Mono", monospace; font-weight: 500; font-size: 44px; letter-spacing: 0.06em;
           margin-top: 40px; opacity: 0.85; }
    .chap { position: absolute; left: 150px; top: 250px; font-family: "Rubik Mono One"; font-size: 230px;
            line-height: 1; z-index: 2; }
    .clock { position: absolute; left: 160px; top: 560px; font-family: "IBM Plex Mono", monospace; font-weight: 500;
             font-size: 150px; letter-spacing: 0.04em; z-index: 2; }
    .flash { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
             font-family: "Rubik Mono One"; font-size: 400px; line-height: 1; }
    .half { right: auto; width: 1180px; clip-path: polygon(0 0, 100% 0, 72% 100%, 0 100%); }
    .half .txt { left: 110px; right: 330px; top: 50%; transform: translateY(-50%); }
    .over { position: absolute; left: 140px; right: 140px; font-family: "Oswald"; font-weight: 700;
            color: var(--paper); text-shadow: 6px 6px 0 var(--black); line-height: 1.08; z-index: 3; }
    .over.bottom { bottom: 120px; } .over.top { top: 110px; }
    .g { position: absolute; z-index: 1; }
    .g.circle { width: 640px; height: 640px; border-radius: 50%; background: var(--red); right: -120px; bottom: -180px; }
    .red .g.circle { background: var(--black); }
    .g.wedge { width: 0; height: 0; right: 0; top: 0; border-top: 1080px solid var(--black);
               border-left: 620px solid transparent; opacity: 1; }
    .black .g.wedge { border-top-color: var(--red); }
    .g.wedge.thin { border-left-width: 380px; }
    .g.bars { right: -200px; top: -100px; width: 900px; height: 1400px; transform: rotate(30deg);
              background: repeating-linear-gradient(90deg, var(--red) 0 48px, transparent 48px 110px); }
    #flicker { position: absolute; inset: 0; background: #000; opacity: 0; z-index: 90; pointer-events: none; }
    /* film A layouts */
    .lay, .lay-g { position: absolute; inset: 0; overflow: hidden; }
    .layin { position: absolute; inset: 0; }
    .lay video { position: absolute; object-fit: cover; }
    .slant { position: absolute; left: 50%; top: 50%; width: 3000px; height: 2400px;
             transform: translate(-50%, -50%) rotate(30deg); }
    .band { position: absolute; top: 0; width: 720px; height: 2400px; overflow: hidden; }
    .band video { left: 50% !important; top: 50% !important; }
    .cell { position: absolute; width: 624px; height: 344px; margin: 12px; overflow: hidden; }
    .wa, .wb { position: absolute; inset: 0; overflow: hidden; }
    .wa { clip-path: polygon(0 0, 62% 0, 38% 100%, 0 100%); }
    .wb { clip-path: polygon(62% 0, 100% 0, 100% 100%, 38% 100%); }
    .wline { position: absolute; left: 50%; top: -100px; width: 22px; height: 1300px; background: var(--red);
             transform: translateX(-50%) rotate(24.4deg); }
    .disc { position: absolute; width: 860px; height: 860px; left: 260px; top: 110px; border-radius: 50%; overflow: hidden; }
    .pierce { position: absolute; right: -60px; top: 380px; width: 1300px; height: 0;
              border-top: 160px solid var(--red); border-left: 1300px solid transparent; transform: rotate(-30deg); }
    .eye { position: absolute; left: 50%; top: 50%; width: 940px; height: 940px; margin: -470px 0 0 -470px;
           border-radius: 50%; overflow: hidden; }
    .ring { position: absolute; left: 50%; top: 50%; width: 1020px; height: 1020px; margin: -510px 0 0 -510px;
            border-radius: 50%; border: 34px solid var(--red); }
    .lab { position: absolute; left: 90px; bottom: 70px; font-family: "IBM Plex Mono", monospace; font-weight: 500;
           font-size: 40px; color: var(--paper); background: var(--black); padding: 6px 16px; z-index: 5; }
    .bw { position: absolute; left: 50%; top: 50%; font-family: "Rubik Mono One"; line-height: 1; white-space: nowrap; z-index: 2; }
    .stamp { position: absolute; right: 110px; bottom: 90px; font-family: "IBM Plex Mono", monospace; font-weight: 500;
             font-size: 48px; z-index: 2; }
    .tele { position: absolute; left: 70px; top: 930px; white-space: nowrap; font-family: "Oswald"; font-weight: 700;
            color: var(--paper); background: var(--black); padding: 8px 26px; transform: rotate(-30deg);
            transform-origin: 0 100%; z-index: 80; }
    .tc { position: absolute; right: 90px; top: 70px; font-family: "IBM Plex Mono", monospace; font-weight: 500;
          color: var(--paper); background: var(--black); padding: 4px 14px; z-index: 6; }
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="%DUR%" data-width="1920" data-height="1080">
    <div id="gate">
      %BODY%
    </div>
    <div id="flicker"></div>
    %AUDIO%
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    // gate weave and flicker: one seeded value per frame (filmkit.py, seed 1923)
    const W = [%WEAVE%];
    W.forEach((w, f) => {
      tl.set("#gate", { x: w[0], y: w[1] }, f / 30);
      tl.set("#flicker", { opacity: w[2] }, f / 30);
    });
    %TWEENS%
    window.__timelines["main"] = tl;
  </script>
</body>
</html>
"""
