"""What every start makes sure of: the folders exist, and a new library gets its manual (no Qt here)."""

from __future__ import annotations

import os
import shutil
import sys

MANUAL_NAME = "RoughReader Manual.md"
MANUAL_VERSION = 1


def manual_source() -> str:
    base = getattr(sys, "_MEIPASS", None)
    folder = os.path.join(base, "roughreader", "assets") if base else os.path.join(os.path.dirname(__file__), "assets")
    return os.path.join(folder, MANUAL_NAME)


def ensure_folders(data_dir: str, cache_dir: str, library_root: str) -> None:
    for folder in (data_dir, os.path.join(data_dir, "annotations"), cache_dir, library_root):
        os.makedirs(folder, exist_ok=True)


def install_manual(library_root: str, store) -> str:
    """Put the manual in the library, once (a reader who deletes it does not get it back). Returns its
    path, or '' if nothing was done."""
    if int(store.get("manual") or 0) >= MANUAL_VERSION:
        return ""
    source = manual_source()
    if not os.path.isfile(source):
        return ""
    from . import analyze, organize

    target_dir = os.path.join(library_root, "RoughReader", "RoughReader Manual")
    target = os.path.join(target_dir, MANUAL_NAME)
    os.makedirs(target_dir, exist_ok=True)
    shutil.copyfile(source, target)
    index = organize.Index(library_root)
    details = analyze.Details(title="RoughReader Manual", authors=["RoughReader"], format="MD", manual=True,
                              origins={"title": "comes with RoughReader", "authors": "comes with RoughReader"})
    index.files[index.rel(target)] = {"title": "RoughReader Manual", "author": "RoughReader", "label": "",
                                      "format": "MD", "details": details.as_dict(), "source": "comes with RoughReader"}
    index.save()
    store.set("manual", MANUAL_VERSION)
    store.dirty = True
    store.save()
    return target
