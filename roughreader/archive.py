"""Reading comic archives straight from the zip, without extracting them."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import zipfile

from PIL import Image

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".tif", ".tiff"}
BOOK_EXT = {".cbz"}
OPENABLE_EXT = {".cbz", ".zip"}
FALLBACK_SIZE = (1400, 2000)

_DIGITS = re.compile(r"(\d+)")
_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")


class BookError(Exception):
    """The archive could not be opened as a comic."""


def natural_key(text: str):
    """Sort key that orders 'p2' before 'p10'."""
    return [int(part) if part.isdigit() else part.casefold() for part in _DIGITS.split(text)]


def display_name(path: str) -> str:
    """File name without extension, release tags in brackets, or doubled spaces."""
    stem = os.path.splitext(os.path.basename(path))[0]
    clean = re.sub(r"\s+", " ", _TAGS.sub("", stem)).strip()
    return clean or stem


def is_page_entry(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith(".") or name.startswith("__MACOSX/"):
        return False
    return os.path.splitext(base)[1].lower() in IMAGE_EXT


def fingerprint(path: str) -> str:
    """Changes whenever the file is replaced or modified."""
    st = os.stat(path)
    return f"{st.st_size}-{st.st_mtime_ns}"


def list_books(folder: str) -> list[str]:
    """All comic archives below a folder, in natural order."""
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if os.path.splitext(name)[1].lower() in BOOK_EXT:
                found.append(os.path.join(root, name))
    found.sort(key=lambda p: natural_key(os.path.relpath(p, folder)))
    return found


def sibling_books(path: str) -> list[str]:
    """Archives in the same folder as `path`, in natural order."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    books = [n for n in names if os.path.splitext(n)[1].lower() in BOOK_EXT]
    books.sort(key=natural_key)
    return [os.path.join(folder, n) for n in books]


def read_cover(path: str) -> bytes:
    """Raw bytes of the first page of an archive."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = sorted((n for n in zf.namelist() if is_page_entry(n)), key=natural_key)
            if not names:
                raise BookError("No images in archive")
            return zf.read(names[0])
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise BookError(str(exc)) from exc


class Book:
    """An open archive. Pages are decoded on demand from the zip itself."""

    kind = "comic"

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.name = display_name(path)
        self._lock = threading.Lock()
        try:
            self._zf = zipfile.ZipFile(self.path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise BookError(f"Could not open archive: {exc}") from exc
        infos = [i for i in self._zf.infolist() if not i.is_dir() and is_page_entry(i.filename)]
        infos.sort(key=lambda i: natural_key(i.filename))
        if not infos:
            self._zf.close()
            raise BookError("This archive contains no images.")
        if any(i.flag_bits & 0x1 for i in infos):
            self._zf.close()
            raise BookError("This archive is password-protected.")
        self._infos = infos
        self.sizes: list[tuple[int, int] | None] = [None] * len(infos)
        self._fallback = FALLBACK_SIZE

    def __len__(self) -> int:
        return len(self._infos)

    def page_name(self, index: int) -> str:
        return self._infos[index].filename

    def read(self, index: int) -> bytes:
        with self._lock:
            return self._zf.read(self._infos[index])

    def size(self, index: int) -> tuple[int, int]:
        return self.sizes[index] or self._fallback

    def scan_sizes(self, cache_dir: str = "") -> None:
        """Read every page's pixel size from its header (a few KB per page).

        With `cache_dir` the result is remembered, so reopening a volume
        does not touch the disk again until the file changes.
        """
        cached = self._size_cache_path(cache_dir) if cache_dir else ""
        sizes = self._load_sizes(cached) if cached else None
        if sizes is None:
            sizes = [self._probe(info) for info in self._infos]
            if cached:
                self._save_sizes(cached, sizes)
        self.sizes = sizes
        known = sorted(s for s in self.sizes if s)
        if known:
            self._fallback = known[len(known) // 2]

    def _size_cache_path(self, cache_dir: str) -> str:
        tag = f"{os.path.normcase(self.path)}|{fingerprint(self.path)}"
        return os.path.join(cache_dir, hashlib.sha1(tag.encode("utf-8")).hexdigest() + ".json")

    def _load_sizes(self, cached: str):
        try:
            with open(cached, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(data, list) or len(data) != len(self._infos):
            return None
        try:
            return [(int(v[0]), int(v[1])) if v else None for v in data]
        except (TypeError, ValueError, IndexError):
            return None

    @staticmethod
    def _save_sizes(cached: str, sizes) -> None:
        try:
            os.makedirs(os.path.dirname(cached), exist_ok=True)
            with open(cached, "w", encoding="utf-8") as handle:
                json.dump(sizes, handle)
        except OSError:
            pass

    def _probe(self, info: zipfile.ZipInfo) -> tuple[int, int] | None:
        try:
            with self._lock, self._zf.open(info) as handle:
                data = b""
                for step in (16384, 65536, 262144, -1):
                    data += handle.read(step)
                    try:
                        with Image.open(io.BytesIO(data)) as image:
                            width, height = image.size
                        if width > 0 and height > 0:
                            return width, height
                        return None
                    except Exception:
                        if step == -1 or len(data) >= info.file_size:
                            return None
        except Exception:
            return None
        return None

    def close(self) -> None:
        with self._lock:
            self._zf.close()
