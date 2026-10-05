"""The RoughReader logo: A4 page, landscape banner, square icon, and the bare arrow used as the app
icon at small sizes and as the in-app mark. One arrow drawing, placed on each layout.

    python branding/make_logo.py        (needs fonttools, pymupdf and Pillow; uses Bahnschrift from Windows)

Lettering is Bahnschrift converted to outlines, so the SVGs need no font. Light falls from the upper
left: lit faces BURG_HI / light grey, shaded faces BURG_DEEP / darker grey, hard deep-feldgrau shadows
down-right on the head, the book and the name plates (never on the shaft).
"""

import io
import os

import pymupdf
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE
FONT = r"C:\Windows\Fonts\bahnschrift.ttf"

FELD = "#4D5D53"        # background
FELD_DEEP = "#222A26"   # shadows, panels
FELD_DARK = "#3B4841"
FELD_LIGHT = "#677A6F"
BURG = "#800020"        # lettering
BURG_HI = "#A01B36"
GREY = "#D6D9D7"        # keylines, small type
W, H = 210, 297

_fonts = {}


def font(weight=700, width=100):
    key = (weight, width)
    if key not in _fonts:
        _fonts[key] = instantiateVariableFont(TTFont(FONT), {"wght": weight, "wdth": width})
    return _fonts[key]


def text(s, size, x, y, weight=700, width=100, tracking=0.0, anchor="start"):
    """Outline path for a line of text; size is the cap-to-baseline height in mm, y the baseline."""
    f = font(weight, width)
    glyphs = f.getGlyphSet()
    cmap = f.getBestCmap()
    caps = f["OS/2"].sCapHeight or f["head"].unitsPerEm * 0.7
    scale = size / caps
    advances, names = [], []
    for ch in s:
        name = cmap.get(ord(ch), ".notdef")
        names.append(name)
        advances.append(glyphs[name].width * scale)
    total = sum(advances) + tracking * (len(s) - 1)
    start = {"start": x, "middle": x - total / 2, "end": x - total}[anchor]
    pen = SVGPathPen(glyphs)
    cursor = start
    for name, advance in zip(names, advances):
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, cursor, y)))
        cursor += advance + tracking
    return pen.getCommands(), total


def poly(points, fill, extra=""):
    return f'<polygon points="{" ".join(f"{x:.3f},{y:.3f}" for x, y in points)}" fill="{fill}" {extra}/>'


def chamfer(x, y, w, h, c):
    """Rectangle with the top-left and bottom-right corners cut (the house motif)."""
    return [(x + c, y), (x + w, y), (x + w, y + h - c), (x + w - c, y + h), (x, y + h), (x, y + c)]


def path(d, fill, extra=""):
    return f'<path d="{d}" fill="{fill}" {extra}/>'



BURG_DEEP = "#5A0017"
GREY_SHADE = "#B3BCB7"
SHADOW = 1.8


class Arrow:
    """Shapes of the arrow in its own frame; `book_at` is the distance from the tip to the book."""

    def __init__(self, book_at, head_h=50.0, blade=11.5, half=4.5, edge=1.3, page_w=53.0, page_h=66.0, drop=21.0,
                 tail=34.0, scale=1.0):
        k = scale
        self.k = k
        self.head_h, self.blade, self.half, self.edge = head_h * k, blade * k, half * k, edge * k
        self.socket_h = 6.0 * k
        self.book_top = book_at * k
        self.page_w, self.page_h, self.drop = page_w * k, page_h * k, drop * k
        self.spine_bottom = self.book_top + self.page_h
        self.tail_end = self.spine_bottom + tail * k
        self.length = self.tail_end

    def parts(self):
        """[(points, fill, casts a shadow)] in drawing order."""
        out = []
        half, edge, k = self.half, self.edge, self.k
        # the book: pages sloping back from the spine, lines of print cut in
        top, bottom, w, drop = self.book_top, self.spine_bottom, self.page_w, self.drop
        for side, fill in ((-1, BURG_HI), (1, BURG)):
            out.append(([(side * half, top), (side * half, bottom), (side * w, bottom + drop), (side * w, top + drop)], fill, True))
        for i in range(4):
            f = (i + 1) / 5.4
            for side in (-1, 1):
                inner, outer = side * (half + w * 0.12), side * w * 0.86
                y_in = top + self.page_h * f
                y_out = y_in + drop / (w - half) * abs(outer - inner)
                t = self.page_h * 0.035
                out.append(([(inner, y_in), (outer, y_out), (outer, y_out + t), (inner, y_in + t)], FELD, False))
        # the blades' shadow goes under the shaft: the shaft and its socket cast none and have none on them
        h, b = self.head_h, self.blade
        widest, base = h * 0.68, h
        rib, spur, notch_y = b * 0.32, 2.2 * k, h - 3.5 * k
        collar = half + edge
        silhouette = [(0, 0), (b, widest), (b + spur, base + 2.5 * k), (b * 0.5, notch_y), (rib, base), (-rib, base),
                      (-b * 0.5, notch_y), (-b - spur, base + 2.5 * k), (-b, widest)]
        out.append((silhouette, None, True))
        # the shaft: light grey, burgundy border all round, forked tail
        start = self.head_h + self.socket_h
        flare_start, flare, notch = self.tail_end - 16 * k, half * 1.75, 13.5 * k

        def half_shaft(grow, side):
            g = grow
            return [(0, start), (side * (half + g), start), (side * (half + g), flare_start),
                    (side * (flare + g * 1.4), self.tail_end + g * 1.6), (0, self.tail_end - notch + g * 1.6)]

        border = [(0, start)] + half_shaft(edge, 1)[1:] + half_shaft(edge, -1)[1:][::-1]
        out.append((border, BURG, False))
        out.append((half_shaft(0, -1), GREY, False))
        out.append((half_shaft(0, 1), GREY_SHADE, False))
        # the head: three blades round a rib, spurs at the base, on a plain straight socket as wide as
        # the shaft's border
        out.append(([(0, 0), (-b, widest), (-b - spur, base + 2.5 * k), (-b * 0.5, notch_y), (-rib, base)], BURG_HI, False))
        out.append(([(0, 0), (b, widest), (b + spur, base + 2.5 * k), (b * 0.5, notch_y), (rib, base)], BURG_DEEP, False))
        out.append(([(0, 0), (rib, widest - 2 * k), (rib, base), (-rib, base), (-rib, widest - 2 * k)], BURG, False))
        out.append(([(-collar, base), (0, base), (0, base + self.socket_h), (-collar, base + self.socket_h)], BURG, False))
        out.append(([(0, base), (collar, base), (collar, base + self.socket_h), (0, base + self.socket_h)], BURG_DEEP, False))
        return out

    def svg(self, place):
        """Polygons placed on the page by `place(x, y) -> (X, Y)`; shadows always fall down-right."""
        out = []
        for points, fill, shadow in self.parts():
            mapped = [place(x, y) for x, y in points]
            if shadow:
                out.append(polygon([(x + SHADOW, y + SHADOW) for x, y in mapped], FELD_DEEP))
            if fill:
                out.append(polygon(mapped, fill))
        return "".join(out)


def polygon(points, fill):
    return f'<polygon points="{" ".join(f"{x:.3f},{y:.3f}" for x, y in points)}" fill="{fill}"/>'


def plate(x, y, w, h, cut):
    shape = chamfer(x, y, w, h, cut)
    return polygon([(a + SHADOW, b + SHADOW) for a, b in shape], FELD_DEEP) + polygon(shape, GREY)


def word(s, size, x, y, anchor="start", rotate=0, weight=700, width=87.5, tracking=None):
    """Burgundy outlined text; with `rotate`, (x, y) is the turning point and the text runs along it."""
    d, length = text(s, size, 0, 0, weight, width, tracking=1.6 * size / 10 if tracking is None else tracking, anchor=anchor)
    return f'<path d="{d}" fill="{BURG}" transform="translate({x:.3f},{y:.3f}) rotate({rotate})"/>', length


def svg_doc(width, height, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="{FELD}"/>{body}</svg>')


# ---------------------------------------------------------------- A4
def a4():
    W, H = 210, 297
    cx, tip = W / 2, 36
    arrow = Arrow(book_at=116)
    band_y, band_h, margin, gap = tip + 60, 32, 16, 6
    parts = [plate(margin, band_y, W - 2 * margin, band_h, band_h * 0.3)]
    room = W - 2 * margin - 2 * band_h * 0.42 - 2 * (gap + arrow.half + arrow.edge)
    _d, probe = text("ROUGHREADER", 10, 0, 0, 700, 87.5, tracking=1.6)
    size = 10 * room / probe
    base = band_y + band_h / 2 + size / 2
    parts.append(word("ROUGH", size, cx - arrow.half - arrow.edge - gap, base, anchor="end")[0])
    parts.append(word("READER", size, cx + arrow.half + arrow.edge + gap, base)[0])
    parts.append(arrow.svg(lambda x, y: (cx + x, tip + y)))
    d, _ = text("OFFLINE READER · LIBRARY", 3.6, cx, 278, 600, 100, tracking=1.6, anchor="middle")
    parts.append(f'<path d="{d}" fill="{GREY}"/>')
    return svg_doc(W, H, "".join(parts)), (W, H)


# ---------------------------------------------------------------- banner (2:1, e.g. 1280 x 640)
# The arrow is the horizon, pointing right; ROUGH sits above it to the left, READER below it to the
# right, both clear of the book and the forked tail at the left end.
def banner():
    W, H = 320, 160
    axis, tip_x = 80, 306
    arrow = Arrow(book_at=200, head_h=44, blade=10.5, page_w=38, page_h=46, drop=15, tail=24)
    place = lambda x, y: (tip_x - y, axis + x)  # noqa: E731  (lit left side faces up)
    tail_x = tip_x - arrow.tail_end
    book_right = tip_x - arrow.book_top
    clear = arrow.half + arrow.edge + 9
    size, plate_h = 19, 30
    out = []
    rough_d, rough_w = text("ROUGH", size, 0, 0, 700, 87.5, tracking=3.0)
    reader_d, reader_w = text("READER", size, 0, 0, 700, 87.5, tracking=3.0)
    pad = 11
    rough_x = book_right + 10
    out.append(plate(rough_x, axis - clear - plate_h, rough_w + 2 * pad + 6, plate_h, 8))
    out.append(f'<path d="{rough_d}" fill="{BURG}" transform="translate({rough_x + pad + 3:.3f},{axis - clear - plate_h / 2 + size / 2:.3f})"/>')
    head_base = tip_x - arrow.head_h - 6
    reader_x = head_base - (reader_w + 2 * pad + 6)
    out.append(plate(reader_x, axis + clear, reader_w + 2 * pad + 6, plate_h, 8))
    out.append(f'<path d="{reader_d}" fill="{BURG}" transform="translate({reader_x + pad + 3:.3f},{axis + clear + plate_h / 2 + size / 2:.3f})"/>')
    out.append(arrow.svg(place))
    d, _ = text("OFFLINE READER · LIBRARY", 3.4, tail_x, H - 10, 600, 100, tracking=1.6)
    out.append(f'<path d="{d}" fill="{GREY}"/>')
    return svg_doc(W, H, "".join(out)), (W, H)


# ---------------------------------------------------------------- square icon
# The upright arrow in the middle; ROUGH up the left side and READER up the right, each on a plate,
# reading bottom to top so both turn the same way.
def icon():
    S = 200
    cx = S / 2
    arrow = Arrow(book_at=84, head_h=44, blade=10.5, page_w=40, page_h=50, drop=16, tail=26)
    tip = (S - arrow.length) / 2
    out = []
    size, plate_w, margin = 17, 30, 11
    length = S - 2 * 22
    for s, x in (("ROUGH", margin), ("READER", S - margin - plate_w)):
        out.append(plate(x, 22, plate_w, length, 8))
        d, w = text(s, size, 0, 0, 700, 87.5, tracking=3.6, anchor="middle")
        out.append(f'<path d="{d}" fill="{BURG}" transform="translate({x + plate_w / 2 + size / 2:.3f},{S / 2:.3f}) rotate(-90)"/>')
    out.append(arrow.svg(lambda x, y: (cx + x, tip + y)))
    return svg_doc(S, S, "".join(out)), (S, S)


# ---------------------------------------------------------------- the bare arrow
# The icon at small sizes (where the plates' lettering would be a blur) and the in-app mark: the same
# arrow, larger in its square, on feldgrau or on nothing.
def arrow(background=True):
    S = 200
    shape = Arrow(book_at=78, head_h=50, blade=14, half=6.5, edge=1.9, page_w=60, page_h=54, drop=19, tail=28)
    tip = (S - shape.length) / 2 - 4
    body = shape.svg(lambda x, y: (S / 2 + x, tip + y))
    if background:
        return svg_doc(S, S, body), (S, S)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{S}mm" height="{S}mm" viewBox="0 0 {S} {S}">{body}</svg>', (S, S)


def render(svg: str, width_px: int) -> Image.Image:
    doc = pymupdf.open(stream=svg.encode("utf-8"), filetype="svg")
    page = doc[0]
    zoom = width_px / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    return image if "<rect" not in svg else image.convert("RGB")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    jobs = [("roughreader-logo-a4", a4, [2480]), ("roughreader-banner", banner, [1280, 2560]), ("roughreader-icon", icon, [1024, 512, 256]),
            ("roughreader-arrow", arrow, [256]), ("roughreader-mark", lambda: arrow(False), [256])]
    for name, make, widths in jobs:
        svg, _size = make()
        with open(os.path.join(OUT, name + ".svg"), "w", encoding="utf-8") as handle:
            handle.write(svg)
        for width in widths:
            suffix = "" if width == widths[0] else f"-{width}"
            render(svg, width).save(os.path.join(OUT, f"{name}{suffix}.png"))
    print("done")
