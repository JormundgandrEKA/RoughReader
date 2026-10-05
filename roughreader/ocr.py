"""Text recognition for scanned pages, with the OCR engine built into Windows (runs locally; no Qt here).

recognize() returns lines of words with boxes as fractions of the image (0..1). If the engine or the
language is missing, available() is False and callers carry on without text.
"""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass

_lock = threading.RLock()
_engines: dict[str, object] = {}


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    line: int


def _imports():
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    return Language, BitmapPixelFormat, SoftwareBitmap, OcrEngine, DataWriter


def languages() -> list[str]:
    try:
        return [lang.language_tag for lang in _imports()[3].available_recognizer_languages]
    except Exception:
        return []


def available() -> bool:
    return bool(languages())


def _engine(language: str):
    Language, _f, _b, OcrEngine, _w = _imports()
    tags = languages()
    wanted = next((t for t in tags if t.lower().startswith(language.lower()[:2])), None) or \
        next((t for t in tags if t.lower().startswith("en")), None) or (tags[0] if tags else None)
    if wanted is None:
        return None
    if wanted not in _engines:
        _engines[wanted] = OcrEngine.try_create_from_language(Language(wanted))
    return _engines[wanted]


def recognize(rgba: bytes, width: int, height: int, language: str = "en") -> list[Word]:
    """Words on an RGBA image, in reading order, with boxes as fractions of the image."""
    try:
        _language, BitmapPixelFormat, SoftwareBitmap, _engine_cls, DataWriter = _imports()
    except Exception:
        return []
    with _lock:
        engine = _engine(language)
    if engine is None:
        return []
    scale = 1.0
    limit = 9_000
    if max(width, height) > limit:  # the engine refuses very large images
        return []

    async def run():
        writer = DataWriter()
        writer.write_bytes(rgba)
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.RGBA8, width, height)
        return await engine.recognize_async(bitmap)

    try:
        with _lock:  # the engine does not take overlapping requests well; it is quick enough in turn
            result = asyncio.run(run())
    except Exception:
        return []
    words = []
    for number, line in enumerate(result.lines):
        for word in line.words:
            box = word.bounding_rect
            words.append(Word(word.text, box.x / width * scale, box.y / height * scale,
                              (box.x + box.width) / width * scale, (box.y + box.height) / height * scale, number))
    return words


def lines(words: list[Word]) -> list[str]:
    out: dict[int, list[str]] = {}
    for word in words:
        out.setdefault(word.line, []).append(word.text)
    return [" ".join(parts) for _n, parts in sorted(out.items())]


def looks_german(text: str) -> bool:
    return len(re.findall(r"\b(der|die|das|und|nicht|ist|ein|eine|mit)\b", text, re.I)) > 6
