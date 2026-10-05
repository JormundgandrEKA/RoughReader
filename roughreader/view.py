"""The page view: lays out a spread, paints it, and handles navigation."""

from __future__ import annotations

import math
import time
from bisect import bisect_right

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import QAbstractScrollArea, QFrame

from . import annotations, theme
from .layout import build_spreads, place, spread_of
from .loader import PageLoader

MAX_RENDER_PIXELS = 24_000_000
ZOOM_MIN, ZOOM_MAX = 0.05, 8.0
FITS = ("page", "width", "height", "zoom")


class PageView(QAbstractScrollArea):
    changed = Signal()            # page, layout or zoom changed
    endReached = Signal()         # tried to go past the last page
    fullscreenRequested = Signal()
    resized = Signal()            # the viewport changed size (text may need laying out again)
    linkActivated = Signal(object)  # (page, y fraction) or a URI
    selectionChanged = Signal(QPoint)  # where the selection ended (viewport coordinates), or (-1, -1) when cleared
    backRequested = Signal()      # the mouse's back button

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

        self.loader = PageLoader(self)
        self.loader.ready.connect(self._on_ready)

        self.book = None
        self.double = False
        self.rtl = True
        self.cover_alone = True
        self.fit = "page"
        self.zoom = 1.0
        self.scale = 1.0
        self._spreads: list[tuple[int, ...]] = []
        self._index = 0
        self._items: list[tuple[int, int, int, int, int]] = []  # (page, x, y, w, h) on screen
        self._content = (0, 0)
        self.scroll = False   # one continuous column of every page instead of spreads
        self._ys: list[int] = []
        self._ws: list[int] = []
        self._hs: list[int] = []
        self._pad = 0
        self._held = None     # snapshot shown while the book is being laid out again
        self._hold_text = ""
        self.notes = None     # annotations.Notes of the open document (highlights, bookmarks)
        self._sel = None      # ((page, word), (page, word)): anchor and head of a text selection
        self._selecting = False
        self._press_link = None
        self._flash = None    # (page, y fraction): where a link landed, marked for a moment
        self._flash_timer = QTimer(self, singleShot=True, interval=1600, timeout=self._end_flash)

        self._press: QPoint | None = None
        self._press_scroll = (0, 0)
        self._dragging = False
        self._wheel_acc = 0
        self._wheel_time = 0.0

        self._toast = ""
        self._toast_timer = QTimer(self, singleShot=True, timeout=self._clear_toast)
        self._hide_cursor = False
        self._cursor_timer = QTimer(self, singleShot=True, interval=2500, timeout=self._blank_cursor)

    # ------------------------------------------------------------- state

    def set_book(self, book, page: int = 0) -> None:
        self._held = None
        self._sel = None
        self.loader.set_book(book)
        self.book = book
        self._rebuild(page)

    def hold(self, text: str) -> None:
        """Freeze the picture with a note while the book is busy (a text re-layout); set_book() ends it."""
        self._held = self.viewport().grab()
        self._hold_text = text.upper()
        self.loader.set_book(None)
        self.viewport().update()

    def held(self) -> bool:
        return self._held is not None

    def set_scroll(self, on: bool) -> None:
        if on != self.scroll:
            page = self.current_page()
            self.scroll = on
            if self.book is not None and self._held is None:
                self._rebuild(page)

    def set_double(self, on: bool) -> None:
        if on != self.double:
            self.double = on
            self._rebuild(self.current_page())

    def set_cover_alone(self, on: bool) -> None:
        if on != self.cover_alone:
            self.cover_alone = on
            self._rebuild(self.current_page())

    def set_rtl(self, on: bool) -> None:
        if on != self.rtl:
            self.rtl = on
            self._relayout("top")

    def set_fit(self, fit: str) -> None:
        if fit == "zoom":
            self.zoom = self.scale
        self.fit = fit
        self._relayout("top")

    def _rebuild(self, page: int) -> None:
        if self.book is None:
            self._spreads, self._index = [], 0
        elif self.scroll:
            self._spreads = [(i,) for i in range(len(self.book))]
            self._index = max(0, min(page, len(self.book) - 1))
            self._ys = []
        else:
            self._spreads = build_spreads(
                [self.book.size(i) for i in range(len(self.book))], self.double, self.cover_alone
            )
            self._index = spread_of(self._spreads, max(0, min(page, len(self.book) - 1)))
        self._relayout("top")

    # ------------------------------------------------------------ queries

    def spread_count(self) -> int:
        return len(self._spreads)

    def spread_index(self) -> int:
        return self._index

    def pages(self) -> tuple[int, ...]:
        return self._spreads[self._index] if self._spreads else ()

    def current_page(self) -> int:
        pages = self.pages()
        return pages[0] if pages else 0

    def at_last(self) -> bool:
        return bool(self._spreads) and self._index == len(self._spreads) - 1

    # --------------------------------------------------------- navigation

    def go_to_spread(self, index: int, position: str = "top") -> None:
        if not self._spreads:
            return
        index = max(0, min(index, len(self._spreads) - 1))
        if self.scroll:
            if not self._ys:
                return
            self._index = index
            bar = self.verticalScrollBar()
            top = self._ys[index] - (self._pad if index == 0 else 0)
            bar.setValue(top if position != "end" else self._ys[index] + self._hs[index] - self._viewport_px()[1])
            self._update_scroll(force=True)
            return
        if index != self._index:
            self._index = index
            self._relayout(position)

    def go_to_page(self, page: int) -> None:
        if self._spreads:
            self.go_to_spread(spread_of(self._spreads, max(0, min(page, len(self.book) - 1))))

    def next(self) -> None:
        if not self._spreads:
            return
        if self.scroll:
            bar = self.verticalScrollBar()
            if bar.value() >= bar.maximum():
                self.endReached.emit()
            else:
                bar.setValue(bar.value() + int(bar.pageStep() * 0.9))
            return
        if self._index >= len(self._spreads) - 1:
            self.endReached.emit()
        else:
            self.go_to_spread(self._index + 1)

    def prev(self, at_end: bool = False) -> None:
        if self.scroll:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - int(bar.pageStep() * 0.9))
            return
        self.go_to_spread(self._index - 1, "end" if at_end else "top")

    def go_visual(self, direction: int) -> None:
        """direction -1 = towards the left of the screen, +1 = right."""
        if self.scroll:
            if direction > 0:
                self.next()
            else:
                self.prev()
        elif (direction > 0) != self.rtl:
            self.next()
        else:
            self.prev()

    def scroll_forward(self) -> None:
        bar = self.verticalScrollBar()
        if bar.value() < bar.maximum():
            bar.setValue(bar.value() + int(bar.pageStep() * 0.9))
        else:
            self.next()

    def scroll_back(self) -> None:
        bar = self.verticalScrollBar()
        if bar.value() > 0:
            bar.setValue(bar.value() - int(bar.pageStep() * 0.9))
        else:
            self.prev(at_end=True)

    def nudge(self, direction: int) -> None:
        """Scroll a small step; at the edge, turn the page instead."""
        bar = self.verticalScrollBar()
        if direction > 0:
            if bar.value() < bar.maximum():
                bar.setValue(bar.value() + bar.singleStep())
            else:
                self.next()
        elif bar.value() > 0:
            bar.setValue(bar.value() - bar.singleStep())
        else:
            self.prev(at_end=True)

    def go_to_last(self) -> None:
        self.go_to_spread(len(self._spreads) - 1, "end" if self.scroll else "top")

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        if not self._spreads:
            return
        if self.scroll:
            self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.scale * factor))
            self.fit = "zoom"
            self._relayout("keep")
            return
        dpr = self.devicePixelRatioF()
        view_w, view_h = self._viewport_px()
        ax, ay = (anchor.x() * dpr, anchor.y() * dpr) if anchor is not None else (view_w / 2, view_h / 2)
        ox, oy = self._origin()
        old = self.scale
        rel_x, rel_y = (ax - ox) / old, (ay - oy) / old
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, old * factor))
        self.fit = "zoom"
        self._relayout("keep")
        content_w, content_h = self._content
        self.horizontalScrollBar().setValue(round(rel_x * self.scale - ax + max(0, (view_w - content_w) // 2)))
        self.verticalScrollBar().setValue(round(rel_y * self.scale - ay + max(0, (view_h - content_h) // 2)))

    # ------------------------------------------------------------- layout

    def _viewport_px(self) -> tuple[int, int]:
        dpr = self.devicePixelRatioF()
        size = self.viewport().size()
        return max(1, round(size.width() * dpr)), max(1, round(size.height() * dpr))

    def _place(self, index: int):
        pages = self._spreads[index]
        view_w, view_h = self._viewport_px()
        scale, content_w, content_h, items = place(
            [self.book.size(p) for p in pages], view_w, view_h, self.fit, self.zoom, self.rtl
        )
        return scale, content_w, content_h, [(pages[pos], x, 0, w, h) for pos, x, w, h in items]

    @staticmethod
    def _render_size(width: int, height: int) -> tuple[int, int]:
        pixels = width * height
        if pixels <= MAX_RENDER_PIXELS:
            return width, height
        k = math.sqrt(MAX_RENDER_PIXELS / pixels)
        return max(1, int(width * k)), max(1, int(height * k))

    def _relayout(self, position: str = "keep") -> None:
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        if self._held is not None:
            return
        if self.scroll and self._spreads:
            self._relayout_scroll(position)
            return
        if not self._spreads:
            self._items, self._content = [], (0, 0)
            hbar.setRange(0, 0)
            vbar.setRange(0, 0)
            self.viewport().update()
            self.changed.emit()
            return
        view_w, view_h = self._viewport_px()
        self.scale, content_w, content_h, self._items = self._place(self._index)
        self._content = (content_w, content_h)
        hbar.setPageStep(view_w)
        vbar.setPageStep(view_h)
        hbar.setSingleStep(max(1, view_w // 12))
        vbar.setSingleStep(max(1, view_h // 12))
        hbar.setRange(0, max(0, content_w - view_w))
        vbar.setRange(0, max(0, content_h - view_h))
        if position == "top":
            vbar.setValue(0)
            hbar.setValue(hbar.maximum() if self.rtl else 0)
        elif position == "end":
            vbar.setValue(vbar.maximum())
            hbar.setValue(0 if self.rtl else hbar.maximum())
        self._request()
        self.viewport().update()
        self.changed.emit()

    def _request(self) -> None:
        keys = [(page, *self._render_size(w, h)) for page, _x, _y, w, h in self._items]
        for offset in (1, -1, 2):
            index = self._index + offset
            if 0 <= index < len(self._spreads):
                keys += [(page, *self._render_size(w, h)) for page, _x, _y, w, h in self._place(index)[3]]
        self.loader.request(keys)

    # ------------------------------------------------------ scroll mode

    def _relayout_scroll(self, position: str) -> None:
        """Every page in one column. Reflowed text runs on without gaps; other pages sit a little apart."""
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        anchor = self._scroll_anchor() if position == "keep" and self._ys else None
        view_w, view_h = self._viewport_px()
        dpr = self.devicePixelRatioF()
        sizes = [self.book.size(i) for i in range(len(self.book))]
        reflow = getattr(self.book, "kind", "") == "reflow"
        margin = 0 if reflow else round(12 * dpr)
        gap = 0 if reflow else round(10 * dpr)
        self._pad = round(theme.u(18) * dpr) if reflow else margin
        if reflow or self.fit == "zoom":
            scales = [self.zoom] * len(sizes)
        else:
            if self.fit == "width":
                column = view_w - 2 * margin
            else:  # page / height: the column a typical page needs to fit the height
                aspects = sorted(w / h for w, h in sizes)
                column = min(view_w - 2 * margin, round(aspects[len(aspects) // 2] * (view_h - 2 * margin)))
            column = max(50, column)
            scales = [column / w for w, _h in sizes]
        self._ws = [max(1, round(w * k)) for (w, _h), k in zip(sizes, scales)]
        self._hs = [max(1, round(h * k)) for (_w, h), k in zip(sizes, scales)]
        ys, y = [], self._pad
        for height in self._hs:
            ys.append(y)
            y += height + gap
        self._ys = ys
        content_w = max(self._ws) + 2 * margin
        content_h = y - gap + self._pad
        self._content = (content_w, content_h)
        self._index = max(0, min(self._index, len(ys) - 1))
        self.scale = scales[self._index] if scales else 1.0
        hbar.setPageStep(view_w)
        vbar.setPageStep(view_h)
        hbar.setSingleStep(max(1, view_w // 12))
        vbar.setSingleStep(max(1, view_h // 14))
        hbar.setRange(0, max(0, content_w - view_w))
        vbar.setRange(0, max(0, content_h - view_h))
        if anchor is not None:
            page, frac = anchor
            vbar.setValue(round(self._ys[page] + frac * self._hs[page]))
        elif position == "end":
            vbar.setValue(self._ys[self._index] + self._hs[self._index] - view_h)
        else:
            vbar.setValue(self._ys[self._index] - (self._pad if self._index == 0 else 0))
        self._update_scroll(force=True)

    def _scroll_anchor(self) -> tuple[int, float]:
        y = self.verticalScrollBar().value()
        page = max(0, min(len(self._ys) - 1, bisect_right(self._ys, y) - 1))
        return page, (y - self._ys[page]) / max(1, self._hs[page])

    def _update_scroll(self, force: bool = False) -> None:
        """Work out what is on screen after scrolling; load what is near, and let the rest go."""
        if not self.scroll or not self._ys or self.book is None or self._held is not None:
            return
        _view_w, view_h = self._viewport_px()
        content_w = self._content[0]
        top = self.verticalScrollBar().value()
        count = len(self._ys)
        first = max(0, bisect_right(self._ys, top) - 1)
        items, page = [], first
        while page < count and self._ys[page] < top + view_h:
            items.append((page, (content_w - self._ws[page]) // 2, self._ys[page], self._ws[page], self._hs[page]))
            page += 1
        self._items = items
        current = max(0, min(count - 1, bisect_right(self._ys, top + view_h // 4) - 1))
        moved = current != self._index
        self._index = current
        shown = {item[0] for item in items}
        keys = [(p, *self._render_size(w, h)) for p, _x, _y, w, h in items]
        for p in list(range(page, min(count, page + 2))) + list(range(max(0, first - 2), first)):
            if p not in shown:
                keys.append((p, *self._render_size(self._ws[p], self._hs[p])))
        self.loader.request(keys)
        self.viewport().update()
        if moved or force:
            self.changed.emit()

    def _origin(self) -> tuple[int, int]:
        """Top-left of the content in viewport device pixels."""
        view_w, view_h = self._viewport_px()
        content_w, content_h = self._content
        if self.scroll:
            return max(0, (view_w - content_w) // 2) - self.horizontalScrollBar().value(), -self.verticalScrollBar().value()
        return (
            max(0, (view_w - content_w) // 2) - self.horizontalScrollBar().value(),
            max(0, (view_h - content_h) // 2) - self.verticalScrollBar().value(),
        )

    def _on_ready(self, token: int, page: int) -> None:
        if token == self.loader.token and any(item[0] == page for item in self._items):
            self.viewport().update()

    # -------------------------------------------------------------- text

    def has_text(self) -> bool:
        return self.book is not None and hasattr(self.book, "text_layer")

    def _layer(self, page: int, allow_ocr: bool = True):
        try:
            return self.book.text_layer(page, allow_ocr=allow_ocr)
        except Exception:
            return None

    def _hit(self, pos: QPointF):
        """(page, x fraction, y fraction) under a viewport point, or None."""
        if not self._items or self._held is not None:
            return None
        dpr = self.devicePixelRatioF()
        ox, oy = self._origin()
        px, py = pos.x() * dpr - ox, pos.y() * dpr - oy
        for page, x, y, w, h in self._items:
            if x <= px < x + w and y <= py < y + h:
                return page, (px - x) / w, (py - y) / h
        return None

    @staticmethod
    def _word_at(layer, nx: float, ny: float, nearest: bool = False):
        if layer is None or not layer.words:
            return None
        pad_x, pad_y = 0.004, 0.003
        for index, (x0, y0, x1, y1, _t, _l) in enumerate(layer.words):
            if x0 - pad_x <= nx <= x1 + pad_x and y0 - pad_y <= ny <= y1 + pad_y:
                return index
        if not nearest:
            return None
        best, score = None, None
        for index, (x0, y0, x1, y1, _t, _l) in enumerate(layer.words):
            dy = 0 if y0 <= ny <= y1 else min(abs(ny - y0), abs(ny - y1))
            dx = 0 if x0 <= nx <= x1 else min(abs(nx - x0), abs(nx - x1))
            value = dy * 10 + dx
            if score is None or value < score:
                best, score = index, value
        return best

    @staticmethod
    def _link_at(layer, nx: float, ny: float):
        if layer is None:
            return None
        for x0, y0, x1, y1, target in layer.links:
            if x0 <= nx <= x1 and y0 <= ny <= y1:
                return target
        return None

    def _ordered(self):
        if self._sel is None:
            return None
        a, b = self._sel
        return (a, b) if a <= b else (b, a)

    def has_selection(self) -> bool:
        return self._sel is not None

    def clear_selection(self) -> None:
        if self._sel is not None:
            self._sel = None
            self.viewport().update()
            self.selectionChanged.emit(QPoint(-1, -1))

    def _selected_ranges(self):
        """{page: (first word, last word)} of the selection."""
        ordered = self._ordered()
        if ordered is None:
            return {}
        (pa, ka), (pb, kb) = ordered
        out = {}
        for page in range(pa, pb + 1):
            layer = self._layer(page, allow_ocr=False) if page not in (pa, pb) else self._layer(page)
            if layer is None or not layer.words:
                continue
            first = ka if page == pa else 0
            last = kb if page == pb else len(layer.words) - 1
            out[page] = (first, last)
        return out

    def selected_text(self) -> str:
        parts = []
        for page, (first, last) in sorted(self._selected_ranges().items()):
            parts.append(self._layer(page).text(first, last))
        return " ".join(parts).strip()

    def selection_positions(self):
        """((unit, word), (unit, word)) of the selection, for a highlight."""
        ranges = self._selected_ranges()
        if not ranges:
            return None
        first_page, last_page = min(ranges), max(ranges)
        a, b = self._layer(first_page), self._layer(last_page)
        return (a.unit, a.first + ranges[first_page][0]), (b.unit, b.first + ranges[last_page][1])

    def position_at(self, pos: QPointF):
        """(unit, word) under a viewport point, or None."""
        hit = self._hit(pos)
        if hit is None or not self.has_text():
            return None
        layer = self._layer(hit[0], allow_ocr=False)
        index = self._word_at(layer, hit[1], hit[2])
        return None if index is None else (layer.unit, layer.first + index)

    def page_span(self, page: int):
        """(unit, first word, last word) of a page, for its bookmark."""
        layer = self._layer(page)
        if layer is None:
            return None
        return layer.unit, layer.first, layer.first + max(0, len(layer.words) - 1)

    def flash(self, page: int, y: float) -> None:
        self._flash = (page, y)
        self._flash_timer.start()
        self.viewport().update()

    def _end_flash(self) -> None:
        self._flash = None
        self.viewport().update()

    def go_to_position(self, page: int, y: float = 0.0) -> None:
        """Show a page with a point on it (a link's target) in view, and mark it for a moment."""
        self.go_to_page(page)
        if self.scroll and self._ys and 0 <= page < len(self._ys):
            self.verticalScrollBar().setValue(round(self._ys[page] + y * self._hs[page]) - self._viewport_px()[1] // 5)
        elif self.verticalScrollBar().maximum() > 0 and self._items:
            item = next((i for i in self._items if i[0] == page), None)
            if item is not None:
                self.verticalScrollBar().setValue(round(item[2] + y * item[4]) - self._viewport_px()[1] // 5)
        self.flash(page, y)

    def _paint_text_marks(self, p: QPainter, page: int, target: QRect) -> None:
        """Highlights, the selection, a link's landing spot and the bookmark ribbon on one page."""
        if not self.has_text():
            return
        layer = None
        if self.notes is not None and (self.notes.highlights or self.notes.bookmarks):
            layer = self._layer(page, allow_ocr=False)
        ranges = []
        if layer is not None and layer.words and self.notes is not None:
            last = layer.first + len(layer.words) - 1
            for start, end, colour in self.notes.highlights_in(layer.unit, layer.first, last):
                tone = QColor(annotations.COLOURS.get(colour, "#F2D94E"))
                tone.setAlpha(105)
                ranges.append((start, end, tone))
            if self.notes.bookmark_on(layer.unit, layer.first, last) is not None:
                size = max(14.0, target.width() * 0.035)
                x, y = target.right() - size * 1.8, target.top()
                p.setPen(Qt.NoPen)
                p.setBrush(theme.BURG)
                p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x + size, y), QPointF(x + size, y + size * 1.6),
                                         QPointF(x + size / 2, y + size * 1.15), QPointF(x, y + size * 1.6)]))
        selected = self._selected_ranges().get(page) if self._sel is not None else None
        if selected is not None:
            layer = self._layer(page, allow_ocr=False)
            tone = QColor(theme.BURG)
            tone.setAlpha(90)
            ranges.append((selected[0], selected[1], tone))
        if ranges and layer is not None:
            for start, end, tone in ranges:
                lines: dict[int, list[float]] = {}
                for x0, y0, x1, y1, _t, line in layer.words[max(0, start):end + 1]:
                    box = lines.setdefault(line, [x0, y0, x1, y1])
                    box[0], box[1], box[2], box[3] = min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1)
                for x0, y0, x1, y1 in lines.values():
                    p.fillRect(QRectF(target.x() + x0 * target.width() - 1, target.y() + y0 * target.height(),
                                      (x1 - x0) * target.width() + 2, (y1 - y0) * target.height()), tone)
        if self._flash is not None and self._flash[0] == page:
            y = target.y() + self._flash[1] * target.height()
            p.fillRect(QRectF(target.x(), y - 3, max(6.0, target.width() * 0.012), target.height() * 0.06), theme.BURG)

    # -------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), theme.FELD_DEEP)
        if self._held is not None:
            p.drawPixmap(0, 0, self._held)
            self._paint_note(p, self._hold_text)
            return
        if not self._spreads:
            self._paint_empty(p)
            return
        dpr = self.devicePixelRatioF()
        view_w, view_h = self._viewport_px()
        p.save()
        p.scale(1.0 / dpr, 1.0 / dpr)
        ox, oy = self._origin()
        for page, x, y, w, h in self._items:
            target = QRect(ox + x, oy + y, w, h)
            key = (page, *self._render_size(w, h))
            image = self.loader.get(key)
            if image is not None and image.width() == w and image.height() == h:
                p.drawImage(target.topLeft(), image)
            else:
                if image is None:
                    image = self.loader.any(page)
                if image is not None:
                    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                    p.drawImage(target, image)
                else:
                    self._paint_placeholder(p, target, page, dpr)
                    continue
            self._paint_text_marks(p, page, target)
        vbar = self.verticalScrollBar()
        if vbar.maximum() > 0:
            thumb = max(24 * dpr, view_h * view_h / self._content[1])
            top = (view_h - thumb) * vbar.value() / vbar.maximum()
            p.fillRect(QRectF(view_w - 3 * dpr, top, 3 * dpr, thumb), theme.BURG)
        p.restore()
        if self._toast:
            self._paint_toast(p)

    def _paint_placeholder(self, p: QPainter, target: QRect, page: int, dpr: float) -> None:
        p.fillRect(target, theme.FELD_DARK)
        p.setPen(theme.GREY_DIM)
        p.setFont(theme.font(11 * dpr, bold=True, spacing=2))
        text = "PAGE COULD NOT BE READ" if self.loader.failed(page) else f"{page + 1}"
        p.drawText(target, Qt.AlignCenter, text)

    def _paint_empty(self, p: QPainter) -> None:
        rect = QRectF(self.viewport().rect())
        side = 72.0
        theme.draw_mark(p, QRectF(rect.center().x() - side / 2, rect.center().y() - side - 6, side, side))
        p.setPen(theme.GREY_DIM)
        p.setFont(theme.font(10, bold=True, spacing=2.5))
        p.drawText(QRectF(rect.left(), rect.center().y() + 14, rect.width(), 30), Qt.AlignHCenter | Qt.AlignTop,
                   "DROP A .CBZ FILE HERE")

    def _paint_note(self, p: QPainter, text: str) -> None:
        """A note in the middle of the view (shown while laying out)."""
        p.setFont(theme.font(9.5, bold=True, spacing=1.6))
        width = p.fontMetrics().horizontalAdvance(text) + 44
        view = self.viewport().rect()
        rect = QRectF((view.width() - width) / 2, (view.height() - 48) / 2, width, 48)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.BURG)
        p.drawPolygon(theme.chamfer(rect, 10))
        p.setPen(theme.ON_ACCENT)
        p.drawText(rect, Qt.AlignCenter, text)

    def _paint_toast(self, p: QPainter) -> None:
        p.setFont(theme.font(9.5, bold=True, spacing=1.6))
        metrics = p.fontMetrics()
        width = metrics.horizontalAdvance(self._toast) + 44
        view = self.viewport().rect()
        rect = QRectF((view.width() - width) / 2, view.height() - 84, width, 48)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.BURG)
        p.drawPolygon(theme.chamfer(rect, 10))
        p.setPen(theme.ON_ACCENT)
        p.drawText(rect, Qt.AlignCenter, self._toast)

    def toast(self, text: str, ms: int = 1400) -> None:
        self._toast = text.upper()
        self._toast_timer.start(ms)
        self.viewport().update()

    def _clear_toast(self) -> None:
        self._toast = ""
        self.viewport().update()

    # ------------------------------------------------------------- events

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout("keep")
        self.resized.emit()

    def scrollContentsBy(self, _dx: int, _dy: int) -> None:
        if self.scroll:
            self._update_scroll()
        self.viewport().update()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta()
        dy = delta.y() or delta.x()
        if not dy or not self._spreads:
            return
        event.accept()
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.1 ** (dy / 120.0), event.position())
            return
        if event.modifiers() & Qt.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - round(dy / 120.0 * bar.pageStep() * 0.18))
            return
        if self.scroll:  # a continuous column: the wheel just scrolls
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() - round(dy / 120.0 * bar.pageStep() * 0.12))
            return
        now = time.monotonic()
        bar = self.verticalScrollBar()
        if bar.maximum() > 0:
            can_move = bar.value() < bar.maximum() if dy < 0 else bar.value() > 0
            if can_move:
                bar.setValue(bar.value() - round(dy / 120.0 * bar.pageStep() * 0.18))
                self._wheel_acc, self._wheel_time = 0, now
                return
            if now - self._wheel_time < 0.25:
                return  # still coasting from the scroll that hit the edge
        if (dy > 0) != (self._wheel_acc > 0):
            self._wheel_acc = 0
        self._wheel_acc += dy
        if abs(self._wheel_acc) >= 120:
            self._wheel_acc = 0
            self._wheel_time = now
            if dy < 0:
                self.next()
            else:
                self.prev(at_end=True)

    def mousePressEvent(self, event) -> None:
        self._wake_cursor()
        if event.button() == Qt.LeftButton:
            self._press = event.position().toPoint()
            self._press_scroll = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self._dragging = False
            self._selecting = False
            self._press_link = None
            hit = self._hit(event.position()) if self.has_text() else None
            if hit is not None:
                layer = self._layer(hit[0])
                self._press_link = self._link_at(layer, hit[1], hit[2])
                index = self._word_at(layer, hit[1], hit[2])
                if index is not None and self._press_link is None:  # pressing on text starts a selection
                    if self._sel is not None:
                        self._sel = None
                        self.selectionChanged.emit(QPoint(-1, -1))
                    self._selecting = True
                    self._anchor = (hit[0], index)
        elif event.button() == Qt.BackButton:
            self.backRequested.emit()
        elif event.button() == Qt.ForwardButton:
            self.next()

    def mouseMoveEvent(self, event) -> None:
        self._wake_cursor()
        if self._press is None:
            self._hover_cursor(event.position())
            return
        moved = event.position().toPoint() - self._press
        if self._selecting:
            if moved.manhattanLength() > 4 or self._sel is not None:
                hit = self._hit(event.position())
                if hit is not None:
                    index = self._word_at(self._layer(hit[0]), hit[1], hit[2], nearest=True)
                    if index is not None:
                        self._sel = (self._anchor, (hit[0], index))
                        self.viewport().update()
            return
        can_pan = self.horizontalScrollBar().maximum() > 0 or self.verticalScrollBar().maximum() > 0
        if not self._dragging and can_pan and moved.manhattanLength() > 6:
            self._dragging = True
            self.viewport().setCursor(Qt.ClosedHandCursor)
        if self._dragging:
            dpr = self.devicePixelRatioF()
            self.horizontalScrollBar().setValue(self._press_scroll[0] - round(moved.x() * dpr))
            self.verticalScrollBar().setValue(self._press_scroll[1] - round(moved.y() * dpr))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._press is None:
            return
        press, self._press = self._press, None
        if self._selecting:
            self._selecting = False
            if self._sel is not None:
                self.selectionChanged.emit(event.position().toPoint())
                return
        if self._press_link is not None and (event.position().toPoint() - press).manhattanLength() <= 6:
            target, self._press_link = self._press_link, None
            self.linkActivated.emit(target)
            return
        if self._sel is not None:
            self.clear_selection()
            return
        if self._dragging:
            self._dragging = False
            self.viewport().unsetCursor()
            return
        if (event.position().toPoint() - press).manhattanLength() > 6:
            return
        zone = event.position().x() / max(1, self.viewport().width())
        if zone < 0.35:
            self.go_visual(-1)
        elif zone > 0.65:
            self.go_visual(+1)

    def _hover_cursor(self, pos: QPointF) -> None:
        if not self.has_text():
            return
        hit = self._hit(pos)
        layer = self._layer(hit[0], allow_ocr=False) if hit is not None else None
        if layer is not None and self._link_at(layer, hit[1], hit[2]) is not None:
            self.viewport().setCursor(Qt.PointingHandCursor)
        elif layer is not None and self._word_at(layer, hit[1], hit[2]) is not None:
            self.viewport().setCursor(Qt.IBeamCursor)
        elif not self._dragging:
            self.viewport().unsetCursor()

    def mouseDoubleClickEvent(self, event) -> None:
        hit = self._hit(event.position()) if self.has_text() and event.button() == Qt.LeftButton else None
        if hit is not None:  # a double-click on a word selects it
            index = self._word_at(self._layer(hit[0]), hit[1], hit[2])
            if index is not None:
                self._sel = ((hit[0], index), (hit[0], index))
                self._press = None
                self.viewport().update()
                self.selectionChanged.emit(event.position().toPoint())
                return
        zone = event.position().x() / max(1, self.viewport().width())
        if event.button() == Qt.LeftButton and 0.35 <= zone <= 0.65:
            self.fullscreenRequested.emit()
        else:
            self.mousePressEvent(event)

    # ------------------------------------------------------------- cursor

    def set_cursor_autohide(self, on: bool) -> None:
        self._hide_cursor = on
        self._wake_cursor()

    def _wake_cursor(self) -> None:
        if not self._dragging and not self.has_text():
            self.viewport().unsetCursor()
        if self._hide_cursor:
            self._cursor_timer.start()
        else:
            self._cursor_timer.stop()

    def _blank_cursor(self) -> None:
        if self._hide_cursor and not self._dragging:
            self.viewport().setCursor(Qt.BlankCursor)
