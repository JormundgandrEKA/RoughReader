"""Which pages are shown together, and how big they are on screen."""

from __future__ import annotations


def is_wide(size: tuple[int, int]) -> bool:
    """A double-page spread scanned as one image."""
    width, height = size
    return width > height


def build_spreads(sizes, double: bool, cover_alone: bool = True) -> list[tuple[int, ...]]:
    """Group page indices into what is shown at once.

    In two-page mode wide pages always stand alone, and so does the cover
    unless `cover_alone` is off (switching it shifts the pairing by one).
    """
    count = len(sizes)
    if not double:
        return [(i,) for i in range(count)]
    spreads: list[tuple[int, ...]] = []
    i = 0
    while i < count:
        alone = is_wide(sizes[i]) or (i == 0 and cover_alone)
        if not alone and i + 1 < count and not is_wide(sizes[i + 1]):
            spreads.append((i, i + 1))
            i += 2
        else:
            spreads.append((i,))
            i += 1
    return spreads


def spread_of(spreads, page: int) -> int:
    """Index of the spread containing `page`."""
    for index, spread in enumerate(spreads):
        if page in spread:
            return index
    return max(0, len(spreads) - 1)


def place(sizes, view_w: int, view_h: int, fit: str, zoom: float, rtl: bool):
    """Lay a spread out for a viewport.

    `sizes` are the native pixel sizes of the pages in reading order.
    Returns (scale, content_w, content_h, items) where items are
    (position_in_spread, x, width, height) in visual left-to-right order.
    """
    height = max(h for _, h in sizes)
    widths = [w * height / h for w, h in sizes]
    total = sum(widths)
    if fit == "width":
        scale = view_w / total
    elif fit == "height":
        scale = view_h / height
    elif fit == "zoom":
        scale = zoom
    else:
        scale = min(view_w / total, view_h / height)
    scale = max(scale, 0.01)
    content_h = max(1, round(height * scale))
    order = list(enumerate(widths))
    if rtl:
        order.reverse()
    items, x = [], 0
    for position, width in order:
        w = max(1, round(width * scale))
        items.append((position, x, w, content_h))
        x += w
    return scale, x, content_h, items
