"""Highlights, bookmarks and contents corrections for one book, kept in RoughReader-data (no Qt here).

One JSON file per book in <data>/annotations/, named after the file's fingerprint, so notes stay with
the book when it is renamed or moved inside the library. Positions are (unit, word): the chapter of a
reflowing book or the page of a fixed one, and the word's number within it (see documents.TextLayer).
"""

from __future__ import annotations

import json
import os
import time

COLOURS = {"yellow": "#F2D94E", "green": "#7FD38A", "blue": "#7FB8E8", "rose": "#E88FA6"}


def _pos(value) -> tuple[int, int]:
    return int(value[0]), int(value[1])


class Notes:
    def __init__(self, folder: str, fingerprint: str, path: str = "", title: str = ""):
        self.path = os.path.join(folder, "annotations", f"{fingerprint}.json")
        self.book_path, self.title = path, title
        self.bookmarks: list[dict] = []
        self.highlights: list[dict] = []
        self.contents: dict = {}
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.bookmarks = [b for b in data.get("bookmarks", []) if "at" in b]
            self.highlights = [h for h in data.get("highlights", []) if "start" in h and "end" in h]
            self.contents = data.get("contents") or {}
        except (OSError, ValueError, AttributeError):
            pass
        self.sort()

    def sort(self) -> None:
        self.bookmarks.sort(key=lambda b: _pos(b["at"]))
        self.highlights.sort(key=lambda h: _pos(h["start"]))

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump({"file": self.book_path, "title": self.title, "bookmarks": self.bookmarks,
                       "highlights": self.highlights, "contents": self.contents}, handle, indent=1, ensure_ascii=False)
        os.replace(temp, self.path)

    # ---------------------------------------------------------------- highlights

    def add_highlight(self, start, end, text: str, colour: str = "yellow") -> dict:
        """Mark start..end (inclusive). Overlapping highlights are merged into one."""
        start, end = sorted((_pos(start), _pos(end)))
        keep, texts = [], [text]
        for other in self.highlights:
            a, b = _pos(other["start"]), _pos(other["end"])
            if a <= end and start <= b:
                start, end = min(start, a), max(end, b)
            else:
                keep.append(other)
        entry = {"start": list(start), "end": list(end), "text": " ".join(t for t in texts if t).strip(),
                 "colour": colour if colour in COLOURS else "yellow", "ts": time.time()}
        self.highlights = keep + [entry]
        self.sort()
        self.save()
        return entry

    def highlight_at(self, position) -> dict | None:
        position = _pos(position)
        for entry in self.highlights:
            if _pos(entry["start"]) <= position <= _pos(entry["end"]):
                return entry
        return None

    def remove_highlight(self, entry: dict) -> None:
        self.highlights = [h for h in self.highlights if h is not entry]
        self.save()

    def highlights_in(self, unit: int, first: int, last: int) -> list[tuple[int, int, str]]:
        """Word ranges (within a page that holds words first..last of `unit`) to paint, with colours."""
        out = []
        for entry in self.highlights:
            a, b = _pos(entry["start"]), _pos(entry["end"])
            lo, hi = max(a, (unit, first)), min(b, (unit, last))
            if lo <= hi:
                out.append((lo[1] - first, hi[1] - first, entry.get("colour", "yellow")))
        return out

    # ---------------------------------------------------------------- bookmarks

    def bookmark_on(self, unit: int, first: int, last: int) -> dict | None:
        for entry in self.bookmarks:
            at = _pos(entry["at"])
            if (unit, first) <= at <= (unit, last):
                return entry
        return None

    def toggle_bookmark(self, unit: int, first: int, last: int, label: str, page_hint: int = 0) -> bool:
        """Bookmark the page holding words first..last, or remove its bookmark. Returns True if added."""
        existing = self.bookmark_on(unit, first, last)
        if existing is not None:
            self.bookmarks.remove(existing)
            self.save()
            return False
        self.bookmarks.append({"at": [unit, first], "label": label, "page": page_hint, "ts": time.time()})
        self.sort()
        self.save()
        return True

    def remove_bookmark(self, entry: dict) -> None:
        self.bookmarks = [b for b in self.bookmarks if b is not entry]
        self.save()
