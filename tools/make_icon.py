"""Make the app's icons from the logo in branding/ (run branding/make_logo.py first if it changed).

Writes to roughreader/assets/:
  roughreader.ico      the exe's icon: the bare arrow at 16-40 px (lettering would be a blur), the square
                       logo with ROUGH and READER from 48 px up
  icon-<size>.png      the same images, for the window and taskbar icon
  mark.png             the bare arrow on a transparent background, drawn inside the app
  roughreader.png      the square logo at 256 px

Needs pymupdf and Pillow.
"""

import io
import os

import pymupdf
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANDING = os.path.join(ROOT, "branding")
OUT = os.path.join(ROOT, "roughreader", "assets")
SMALL = [16, 20, 24, 32, 40]
LARGE = [48, 64, 96, 128, 256]


def render(svg_name: str, size: int) -> Image.Image:
    """Drawn four times larger, then reduced: crisp edges at every size."""
    doc = pymupdf.open(os.path.join(BRANDING, svg_name))
    page = doc[0]
    zoom = size * 4 / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
    return image.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    images = [render("roughreader-arrow.svg", s) for s in SMALL] + [render("roughreader-icon.svg", s) for s in LARGE]
    for image in images:
        image.save(os.path.join(OUT, f"icon-{image.width}.png"))
    images[-1].save(os.path.join(OUT, "roughreader.png"))
    images[-1].save(os.path.join(OUT, "roughreader.ico"), format="ICO", sizes=[(i.width, i.width) for i in images],
                    append_images=images[:-1])
    render("roughreader-mark.svg", 256).save(os.path.join(OUT, "mark.png"))
    print("wrote", sorted(os.listdir(OUT)))
