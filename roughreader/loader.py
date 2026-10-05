"""Background decoding and scaling of pages, with small in-memory caches."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from . import imaging

DECODED_BUDGET = 320 * 1024 * 1024
RENDERED_BUDGET = 420 * 1024 * 1024


def to_qimage(image) -> QImage:
    """Pillow image to a QImage that owns its pixels and paints fast."""
    width, height = image.size
    if image.mode == "L":
        fmt, stride = QImage.Format_Grayscale8, width
    elif image.mode == "RGBA":
        fmt, stride = QImage.Format_RGBA8888, width * 4
    else:
        if image.mode != "RGB":
            image = image.convert("RGB")
        fmt, stride = QImage.Format_RGB888, width * 3
    data = image.tobytes()
    q = QImage(data, width, height, stride, fmt)
    target = QImage.Format_ARGB32_Premultiplied if image.mode == "RGBA" else QImage.Format_RGB32
    return q.convertToFormat(target)  # deep copy, detached from `data`


class PageLoader(QObject):
    """Renders (page, width, height) requests off the UI thread: comic pages are decoded and scaled,
    documents draw themselves (book.render)."""

    ready = Signal(int, int)  # token, page

    def __init__(self, parent=None):
        super().__init__(parent)
        workers = max(2, min(4, (os.cpu_count() or 2) - 1))
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="roughreader-page")
        self._lock = threading.Lock()
        self._book = None
        self.token = 0
        self._decoded: OrderedDict = OrderedDict()
        self._rendered: OrderedDict = OrderedDict()
        self._failed: set[int] = set()
        self._pending: set = set()
        self._wanted: frozenset = frozenset()

    def set_book(self, book) -> None:
        with self._lock:
            self.token += 1
            self._book = book
            self._decoded.clear()
            self._rendered.clear()
            self._failed.clear()
            self._pending.clear()
            self._wanted = frozenset()

    def get(self, key):
        """Exact render if it is ready."""
        with self._lock:
            image = self._rendered.get(key)
            if image is not None:
                self._rendered.move_to_end(key)
            return image

    def any(self, page: int):
        """Newest render of a page at any size, to show while the right one is made."""
        with self._lock:
            for key in reversed(self._rendered):
                if key[0] == page:
                    return self._rendered[key]
        return None

    def failed(self, page: int) -> bool:
        return page in self._failed

    def request(self, keys) -> None:
        """Ask for these renders, most important first. Anything else is dropped."""
        if self._book is None:
            return
        self._wanted = frozenset(keys)
        for key in keys:
            with self._lock:
                if key in self._rendered or key in self._pending or key[0] in self._failed:
                    continue
                self._pending.add(key)
            self._pool.submit(self._work, self.token, self._book, key)

    def _work(self, token: int, book, key) -> None:
        page, width, height = key
        try:
            if token != self.token or key not in self._wanted:
                return
            if hasattr(book, "render"):  # documents are drawn straight at the size shown
                rendered = to_qimage(book.render(page, width, height))
            else:
                with self._lock:
                    image = self._decoded.get(page)
                    if image is not None:
                        self._decoded.move_to_end(page)
                if image is None:
                    image = imaging.decode(book.read(page))
                    with self._lock:
                        if token == self.token:
                            self._decoded[page] = image
                            self._trim(self._decoded, DECODED_BUDGET, _pil_bytes, keep=())
                rendered = to_qimage(imaging.scale(image, width, height))
            with self._lock:
                if token != self.token:
                    return
                self._rendered[key] = rendered
                self._trim(self._rendered, RENDERED_BUDGET, QImage.sizeInBytes, keep=self._wanted)
        except Exception:
            with self._lock:
                if token == self.token:
                    self._failed.add(page)
        finally:
            with self._lock:
                self._pending.discard(key)
        if token == self.token:
            try:
                self.ready.emit(token, page)
            except RuntimeError:
                pass  # window already closed

    @staticmethod
    def _trim(cache: OrderedDict, budget: int, size_of, keep) -> None:
        total = sum(size_of(v) for v in cache.values())
        for key in list(cache):
            if total <= budget or len(cache) <= 2:
                break
            if key in keep:
                continue
            total -= size_of(cache.pop(key))

    def shutdown(self) -> None:
        self.token += 1
        self._pool.shutdown(wait=False, cancel_futures=True)


def _pil_bytes(image) -> int:
    return image.width * image.height * len(image.getbands())
