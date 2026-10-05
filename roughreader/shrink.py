"""Make a smaller copy of a volume: pages scaled to suit a screen and re-encoded as WebP or AVIF.

The original is only ever read. Pages are never enlarged, keep their order (renamed 0001, 0002, ...),
and other files in the archive (ComicInfo.xml and the like) are carried over. No Qt in here.
"""

from __future__ import annotations

import io
import os
import re
import threading
import time
import zipfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageChops

from . import archive, imaging


@dataclass(frozen=True)
class Preset:
    id: str
    label: str      # shown on the button
    screen: str     # who it is for
    height: int     # page height in pixels (about 1.25x the screen, so zooming still has detail)
    webp: int       # WebP quality
    avif: int       # AVIF quality


PRESETS: tuple[Preset, ...] = (
    Preset("720p", "720p", "Small, light: 720p screens", 900, 74, 45),
    Preset("1080p", "1080p", "Balanced: 1080p screens", 1350, 80, 50),
    Preset("1440p", "1440p", "Sharp: 1440p screens", 1800, 82, 55),
    Preset("4k", "4K", "Fine: 4K screens", 2400, 85, 58),
)
DEFAULT_PRESET = "1080p"
FORMATS = ("webp", "avif")
MIN_HEIGHT, MAX_HEIGHT = 400, 6000
TAG = {"720p": "720p", "1080p": "1080p", "1440p": "1440p", "4k": "4K"}


class Cancelled(Exception):
    """The caller asked to stop; nothing has been left behind."""


class ShrinkError(Exception):
    """The volume could not be shrunk (message is fit to show)."""


@dataclass
class Result:
    path: str
    pages: int
    source_bytes: int
    output_bytes: int
    seconds: float
    kept_original: int = 0  # pages that could not be re-encoded and were copied as they were


def preset(preset_id: str) -> Preset:
    for item in PRESETS:
        if item.id == preset_id:
            return item
    return next(item for item in PRESETS if item.id == DEFAULT_PRESET)


def quality_for(item: Preset, fmt: str) -> int:
    return item.avif if fmt == "avif" else item.webp


def default_output(source: str, preset_id: str, folder: str = "") -> str:
    """Unused path for the copy, e.g. 'Name (tags) - 1080p.cbz' beside the source (or in `folder`)."""
    stem = os.path.splitext(os.path.basename(source))[0]
    folder = folder or os.path.dirname(os.path.abspath(source))
    base = f"{stem} - {TAG.get(preset_id, preset_id)}"
    path = os.path.join(folder, base + ".cbz")
    number = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{base} ({number}).cbz")
        number += 1
    return path


def _target_size(width: int, height: int, target: int) -> tuple[int, int]:
    if height <= target:
        return width, height  # never enlarge
    return max(1, round(width * target / height)), target


def _looks_grey(image: Image.Image) -> bool:
    """An RGB page whose colour channels are (nearly) identical: store it as greyscale."""
    if image.mode != "RGB":
        return False
    probe = image.copy()
    probe.thumbnail((256, 256))
    r, g, b = probe.split()
    return max(ImageChops.difference(r, g).getextrema()[1], ImageChops.difference(g, b).getextrema()[1]) <= 3


def encode_page(data: bytes, target_height: int, fmt: str, quality: int) -> bytes:
    """One page in, one smaller page out."""
    image = imaging.decode(data)
    width, height = _target_size(*image.size, target_height)
    image = imaging.scale(image, width, height)
    if _looks_grey(image):
        image = image.convert("L")
    out = io.BytesIO()
    if fmt == "avif":
        image.save(out, "AVIF", quality=quality, speed=6)
    else:
        image.save(out, "WEBP", quality=quality, method=4)
    return out.getvalue()


def _split_entries(zf: zipfile.ZipFile):
    pages, extras = [], []
    for info in zf.infolist():
        if info.is_dir() or info.filename.startswith("__MACOSX/") or info.filename.rsplit("/", 1)[-1].startswith("."):
            continue
        (pages if archive.is_page_entry(info.filename) else extras).append(info)
    pages.sort(key=lambda i: archive.natural_key(i.filename))
    return pages, extras


def _open(source: str) -> zipfile.ZipFile:
    try:
        zf = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ShrinkError(f"Could not open the volume: {exc}") from exc
    return zf


def estimate(source: str, target_height: int, fmt: str, quality: int, samples: int = 6) -> int:
    """Rough size in bytes of the finished copy, from encoding a few pages spread through the volume."""
    with _open(source) as zf:
        pages, extras = _split_entries(zf)
        if not pages:
            raise ShrinkError("This archive contains no images.")
        step = max(1, len(pages) // samples)
        chosen = pages[step // 2::step][:samples] or pages[:1]
        total = sum(len(encode_page(zf.read(i), target_height, fmt, quality)) for i in chosen)
        return int(total / len(chosen) * len(pages)) + sum(i.file_size for i in extras)


def shrink(
    source: str,
    output: str,
    target_height: int,
    fmt: str = "webp",
    quality: int = 80,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    workers: int | None = None,
) -> Result:
    """Write a smaller copy of `source` to `output`. Raises Cancelled or ShrinkError; on any failure
    the half-written file is removed and `output` is untouched."""
    if fmt not in FORMATS:
        raise ShrinkError(f"Unknown format: {fmt}")
    target_height = max(MIN_HEIGHT, min(MAX_HEIGHT, int(target_height)))
    if os.path.abspath(source) == os.path.abspath(output):
        raise ShrinkError("The copy cannot replace the original.")
    if os.path.exists(output):
        raise ShrinkError("A file with that name already exists.")
    started = time.time()
    workers = workers or min(8, os.cpu_count() or 2)
    part = output + ".part"
    ext = "." + fmt
    kept = 0
    try:
        with _open(source) as zf:
            pages, extras = _split_entries(zf)
            if not pages:
                raise ShrinkError("This archive contains no images.")
            digits = max(4, len(str(len(pages))))
            total = len(pages)
            os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
            with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED) as out, ThreadPoolExecutor(workers) as pool:
                pending: deque = deque()

                def drain(limit: int) -> None:
                    nonlocal kept
                    while len(pending) > limit:
                        index, info, raw, future = pending.popleft()
                        try:
                            data, name = future.result(), f"{index + 1:0{digits}d}{ext}"
                        except Exception:  # unreadable page: keep it as it was rather than lose it
                            data = raw
                            name = f"{index + 1:0{digits}d}{os.path.splitext(info.filename)[1].lower()}"
                            kept += 1
                        out.writestr(name, data)
                        if progress:
                            progress(index + 1, total)

                for index, info in enumerate(pages):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    raw = zf.read(info)
                    pending.append((index, info, raw, pool.submit(encode_page, raw, target_height, fmt, quality)))
                    drain(workers * 2)
                drain(0)
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                for info in extras:
                    out.writestr(re.sub(r"^.*/", "", info.filename), zf.read(info))
            source_bytes = os.path.getsize(source)
        with zipfile.ZipFile(part) as check:  # the copy must be a readable archive with every page
            if sum(1 for n in check.namelist() if archive.is_page_entry(n)) != total:
                raise ShrinkError("The copy came out incomplete.")
        os.replace(part, output)
    except (Cancelled, ShrinkError):
        _remove(part)
        raise
    except Exception as exc:
        _remove(part)
        raise ShrinkError(f"Could not finish the copy: {exc}") from exc
    return Result(output, total, source_bytes, os.path.getsize(output), time.time() - started, kept)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
