"""Build the 4-slide course deck: template.html + sister skin -> index.html.

Sprites are cut from agent/last_order/skin/sister.png (slim model, front view);
icons are drawn in the skin's own palette. Stdlib only:
    python docs/progress/slides/build.py
"""
import random
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SKIN = ROOT / "agent" / "last_order" / "skin" / "sister.png"


def load_png(path):
    d = path.read_bytes()
    assert d[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, plte, trns = 8, b"", None, None
    while i < len(d):
        n, = struct.unpack(">I", d[i:i + 4])
        t, c = d[i + 4:i + 8], d[i + 8:i + 8 + n]
        i += 12 + n
        if t == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", c)
        elif t == b"PLTE":
            plte = [tuple(c[j:j + 3]) for j in range(0, len(c), 3)]
        elif t == b"tRNS":
            trns = c
        elif t == b"IDAT":
            idat += c
    assert bd == 8 and il == 0
    ch = {6: 4, 2: 3, 3: 1}[ct]
    raw, s, rows, prev, p = zlib.decompress(idat), w * ch, [], bytearray(w * ch), 0
    for _ in range(h):
        f, line = raw[p], bytearray(raw[p + 1:p + 1 + s])
        p += 1 + s
        for x in range(s):
            a = line[x - ch] if x >= ch else 0
            b, cc = prev[x], (prev[x - ch] if x >= ch else 0)
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - cc), abs(a - cc), abs(a + b - 2 * cc)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else cc)) & 255
        row = []
        for x in range(w):
            px = line[x * ch:(x + 1) * ch]
            if ct == 6:
                row.append(tuple(px))
            elif ct == 2:
                row.append(tuple(px) + (255,))
            else:
                k = px[0]
                row.append(plte[k] + ((trns[k] if trns and k < len(trns) else 255),))
        rows.append(row)
        prev = line
    return rows


def hexc(p):
    return "#%02x%02x%02x" % p[:3]


def svg_from_grid(grid, cls=""):
    """grid: list of rows of '#rrggbb' or None -> crisp SVG, horizontal runs merged."""
    h, w = len(grid), max(len(r) for r in grid)
    rects = []
    for y, row in enumerate(grid):
        x = 0
        while x < len(row):
            c = row[x]
            if c is None:
                x += 1
                continue
            x0 = x
            while x < len(row) and row[x] == c:
                x += 1
            rects.append(f'<rect x="{x0}" y="{y}" width="{x - x0}" height="1" fill="{c}"/>')
    return (f'<svg class="{cls}" viewBox="0 0 {w} {h}" shape-rendering="crispEdges" '
            f'xmlns="http://www.w3.org/2000/svg">{"".join(rects)}</svg>')


# slim (Alex) model, front faces: (u, v, w, h) of base and overlay
PARTS = [  # (dest_x, dest_y, base, overlay)
    (3, 0, (8, 8, 8, 8), (40, 8, 8, 8)),        # head / hat
    (3, 8, (20, 20, 8, 12), (20, 36, 8, 12)),   # body / jacket
    (0, 8, (44, 20, 3, 12), (44, 36, 3, 12)),   # right arm (viewer's left)
    (11, 8, (36, 52, 3, 12), (52, 52, 3, 12)),  # left arm
    (3, 20, (4, 20, 4, 12), (4, 36, 4, 12)),    # right leg
    (7, 20, (20, 52, 4, 12), (4, 52, 4, 12)),   # left leg
]


def sprite(px, head_only=False):
    W, H = (8, 8) if head_only else (14, 32)
    grid = [[None] * W for _ in range(H)]
    parts = PARTS[:1] if head_only else PARTS
    for dx, dy, base, over in parts:
        if head_only:
            dx = 0
        for layer in (base, over):
            u, v, w, h = layer
            for yy in range(h):
                for xx in range(w):
                    p = px[v + yy][u + xx]
                    if p[3] > 0:
                        grid[dy + yy][dx + xx] = hexc(p)
    return grid


PAL = {
    "k": "#161616", "w": "#e4ebf7", "s": "#cbd2dc", "v": "#d9bfb8", "V": "#bda6a0",
    "h": "#a17465", "H": "#593d3a", "g": "#36c651", "G": "#228935", "n": "#2d3c5a",
    "N": "#53658a", "p": "#ffd9c2", "m": "#786f7d", "M": "#988c9e",
}

ICONS = {
    "clock": """
...kkkk...
..kwwwwk..
.kwwwkwwk.
kwwwwkwwwk
kwwwwkkkwk
kwwwwwwwwk
kwwwwwwwwk
.kwwwwwwk.
..kwwwwk..
...kkkk...""",
    "skull": """
..kkkkkk..
.kwwwwwwk.
kwwwwwwwwk
kwkkwwkkwk
kwkkwwkkwk
kwwwkkwwwk
.kwwwwwwk.
..kwkwkwk.
..kkkkkkk.
..........""",
    "scroll": """
.hhhhhhhh.
hHvvvvvvHh
.hvkkkkvh.
..vvvvvv..
..vkkkvv..
..vvvvvv..
..vkkkkv..
.hvvvvvvh.
hHvvvvvvHh
.hhhhhhhh.""",
    "chest": """
..........
.kkkkkkkk.
khhhhhhhhk
khHHHHHHhk
kkkkggkkkk
khhhkkhhhk
khhhhhhhhk
khhhhhhhhk
kkkkkkkkkk
..........""",
    "book": """
.kkkkkkk..
kHhhhhhhk.
kHhwwwwhk.
kHhwkkwhk.
kHhwwwwhk.
kHhhhhhhk.
kHhhhhhhk.
kHwwwwwwwk
.kkkkkkkk.
..........""",
    "plane": """
.........k
.......kkk
.....kkwwk
...kkwwwk.
.kkwwwwwk.
kwwwwwwk..
.kkkswwk..
...kswk...
...kskk...
...kk.....""",
    "picture": """
kkkkkkkkkk
kwwwwwwwwk
kwnnnnngwk
kwnnnnnnwk
kwnNnnnnwk
kwNNNnhnwk
kwgggghhwk
kwggggggwk
kwwwwwwwwk
kkkkkkkkkk""",
    "brush": """
........kk
.......kHk
......kHk.
.....kHk..
....kHk...
...kVk....
..kggk....
.kgGgk....
.kGGk.....
..kk......""",
    "glass": """
..kkkk....
.kwwwwk...
kwwsswwk..
kwswwswk..
kwswwswk..
kwwsswwk..
.kwwwwkk..
..kkkkhhk.
.......hhk
........k.""",
    "bubble": """
.kkkkkkkk.
kwwwwwwwwk
kwkkwkkkwk
kwwwwwwwwk
kwkkkwkkwk
kwwwwwwwwk
.kkwkkkkk.
...kwk....
...kk.....
..........""",
    "note": """
....kkkkk.
....kgggk.
....kkkkk.
....k...k.
....k...k.
.kkkk.kkk.
kgggkkgggk
kgggkkgggk
.kkk..kkk.
..........""",
    "goggles": """
.kkkkk..kkkkk.
kgggGgkkgGgggk
kgwgggnnggwggk
kggggGkkGggggk
.kkkkk..kkkkk.""",
    "check": """
.........
.......kk
......kgk
.kk..kgk.
kgk.kgk..
.kgkgk...
..kgk....
...k.....
.........""",
    "cross": """
.........
kk.....kk
kHk...kHk
.kHk.kHk.
..kHkHk..
.kHk.kHk.
kHk...kHk
kk.....kk
.........""",
}


def icon(name):
    rows = [r for r in ICONS[name].strip("\n").splitlines()]
    return svg_from_grid([[PAL.get(ch) for ch in r] for r in rows], f"ic ic-{name}")


def dirt_tile():
    rnd = random.Random(14)
    cols = ["#91695b"] * 6 + ["#a17465"] * 5 + ["#ad7d6d"] * 2 + ["#634441"] * 2 + ["#593d3a"]
    grid = [[rnd.choice(cols) for _ in range(16)] for _ in range(16)]
    for x in range(16):  # vest-coloured top, like a path block
        grid[0][x] = grid[1][x] = "#d9bfb8"
        grid[2][x] = rnd.choice(["#d9bfb8", "#c9b1ab", "#bda6a0"])
    svg = svg_from_grid(grid)
    return "data:image/svg+xml;utf8," + svg.replace('class="" ', "").replace("#", "%23").replace('"', "'")


def main():
    px = load_png(SKIN)
    body, face = svg_from_grid(sprite(px), "sprite"), svg_from_grid(sprite(px, True), "face")
    html = (HERE / "template.html").read_text(encoding="utf-8")
    html = html.replace("{{SPRITE}}", body).replace("{{FACE}}", face).replace("{{DIRT}}", dirt_tile())
    for name in ICONS:
        html = html.replace("{{IC:%s}}" % name, icon(name))
    assert "{{" not in html, html[html.index("{{"):html.index("{{") + 40]
    out = HERE / "index.html"
    out.write_text(html, encoding="utf-8")
    print("wrote", out, len(html), "bytes")
    make_pdf(out, HERE / "last-order.pdf")


def find_browser():
    names = ["msedge", "chrome", "google-chrome", "chromium"]
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if Path(p).exists():
            return p
    return None


def make_pdf(src, dst):
    browser = find_browser()
    if not browser:
        raise SystemExit("no Edge/Chrome found: HTML is built, PDF is not")
    subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=8000", f"--print-to-pdf={dst}", src.as_uri()],
                   check=True, stderr=subprocess.DEVNULL, timeout=120)
    print("wrote", dst, dst.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
