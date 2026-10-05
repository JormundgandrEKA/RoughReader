"""Draw the app mark with Pillow and write roughreader/assets/roughreader.ico and roughreader.png."""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from roughreader import mark  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "roughreader", "assets")
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def render(size: int) -> Image.Image:
    ss = 8 if size <= 64 else 4
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    k = size * ss / 64.0
    for points, colour in mark.SHAPES:
        draw.polygon([(x * k, y * k) for x, y in points], fill=colour)
    return big.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    images = [render(s) for s in SIZES]
    images[-1].save(os.path.join(OUT, "roughreader.png"))
    images[-1].save(os.path.join(OUT, "roughreader.ico"), format="ICO", sizes=[(s, s) for s in SIZES],
                    append_images=images[:-1], bitmap_format="bmp")
    print("wrote", os.listdir(OUT))
