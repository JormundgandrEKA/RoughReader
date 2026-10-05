"""Decoding and scaling with Pillow (no Qt in here)."""

from __future__ import annotations

import io

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 400_000_000
THUMB_SIZE = (336, 504)  # 2:3, twice the on-screen tile for sharp high-DPI covers


def decode(data: bytes) -> Image.Image:
    """Full-resolution page as an L, RGB or RGBA image."""
    image = Image.open(io.BytesIO(data))
    image.load()
    if image.mode in ("L", "RGB", "RGBA"):
        return image
    if image.mode in ("1", "I;16", "I", "F"):
        return image.convert("L")
    if image.mode in ("LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        return image.convert("RGBA")
    return image.convert("RGB")


def scale(image: Image.Image, width: int, height: int) -> Image.Image:
    """Lanczos resample; keeps line art and lettering crisp."""
    if image.size == (width, height):
        return image
    return image.resize((width, height), Image.Resampling.LANCZOS, reducing_gap=3.0)


def thumbnail(data: bytes, size: tuple[int, int] = THUMB_SIZE) -> Image.Image:
    """Cover cropped to the tile's 2:3 shape."""
    image = Image.open(io.BytesIO(data))
    image.draft("RGB", (size[0] * 2, size[1] * 2))
    image = image.convert("RGB")
    return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def load_rgb(path: str) -> Image.Image:
    """A small image file from disk (cached cover), fully read."""
    with Image.open(path) as image:
        return image.convert("RGB")
