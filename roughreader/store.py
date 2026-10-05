"""Settings and reading progress, kept in one small JSON file."""

from __future__ import annotations

import json
import os
import time

DEFAULTS = {
    "double": False,
    "rtl": True,
    "fit": "page",
    "cover_alone": True,
    "library": "",
    "geometry": "",
    "view": "library",
    "keys": {},
    "shrink": {},
    "scheme": "Feldgrau",
    "borders": False,
    "modes": {},      # per kind of book: {"double": bool, "scroll": bool}
    "text": {},       # text style for reflowing books
    "sources": [],    # folders the library copies from
    "library_view": "gallery",   # or "list"
    "manual": 0,      # version of the manual put in the library (it is put there once)
}
MAX_BOOKS = 2000


class Store:
    def __init__(self, folder: str):
        self.folder = folder
        self.path = os.path.join(folder, "state.json")
        self.settings = dict(DEFAULTS)
        self.books: dict[str, dict] = {}
        self.dirty = False
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            saved = data.get("settings")
            if isinstance(saved, dict):
                self.settings.update({k: v for k, v in saved.items() if k in DEFAULTS})
            books = data.get("books")
            if isinstance(books, dict):
                self.books = {k: v for k, v in books.items() if isinstance(v, dict)}

    def get(self, key: str):
        return self.settings.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        if self.settings.get(key) != value:
            self.settings[key] = value
            self.dirty = True

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def book(self, path: str) -> dict:
        return self.books.get(self._key(path), {})

    def update_book(self, path: str, **fields) -> None:
        entry = self.books.setdefault(self._key(path), {})
        entry.update(fields)
        entry["ts"] = time.time()
        self.dirty = True

    def adopt_progress(self, source: str, copy: str) -> bool:
        """Give a copy the reading progress of the file it was copied from (if it has none of its own)."""
        old = self.books.get(self._key(source))
        if not old or self._key(copy) in self.books:
            return False
        self.books[self._key(copy)] = dict(old)
        self.dirty = True
        return True

    def move_book(self, old: str, new: str) -> None:
        """A book was renamed or moved: its progress goes with it."""
        entry = self.books.pop(self._key(old), None)
        if entry is not None:
            self.books[self._key(new)] = entry
            self.dirty = True

    def progress(self, path: str) -> tuple[int, int]:
        """(last page index, page count), or (0, 0) if never opened."""
        entry = self.book(path)
        return int(entry.get("page", 0)), int(entry.get("count", 0))

    def save(self) -> None:
        if not self.dirty:
            return
        if len(self.books) > MAX_BOOKS:
            newest = sorted(self.books.items(), key=lambda kv: kv[1].get("ts", 0))[-MAX_BOOKS:]
            self.books = dict(newest)
        try:
            os.makedirs(self.folder, exist_ok=True)
            temp = self.path + ".tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump({"settings": self.settings, "books": self.books}, handle, indent=1)
            os.replace(temp, self.path)
            self.dirty = False
        except OSError:
            pass
