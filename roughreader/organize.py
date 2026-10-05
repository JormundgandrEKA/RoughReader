"""The managed library: one folder per author (or comic series), one per work, the variants inside.

    RoughReader-Library/
      Ernst Jünger/
        Storm of Steel/
          Storm of Steel (Penguin Classics Deluxe Edition).pdf
          Storm of Steel (Original 1929 Translation, Mystery Grove Publishing Co., 2019).epub
      Der Werwolf/
        Der Werwolf v01/
          Der Werwolf v01.cbz
          Der Werwolf v01 - 1080p.cbz          <- a shrunk copy is just another variant

Files are copied in, never moved, and an identical file is never copied twice. Authors and titles
come from the file's own metadata and from its name (Anna's Archive style "Title -- Author -- ...",
"Author - Title", "Title by Author", "(Publisher, year)"). The folders are the truth: moving things
around in Explorer is fine. `.roughreader.json` only remembers labels and where files came from.
No Qt in here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

from . import archive, documents

INDEX_NAME = ".roughreader.json"
UNKNOWN_AUTHOR = "Unknown Author"
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "of", "on", "or", "over",
               "the", "to", "upon", "with", "der", "die", "das", "und", "von", "vom", "zu", "de", "la", "le", "et"}
ARTICLES = {"the", "a", "an", "der", "die", "das", "le", "la", "les", "el", "il"}
PARTICLES = {"de", "da", "di", "du", "van", "von", "der", "den", "le", "la", "del", "della", "ten", "ter", "zu"}
EDITION = re.compile(r"\b(edition|ed\.|translation|translated|translator|revised|abridged|unabridged|deluxe|annotated|"
                     r"illustrated|original|anniversary|classics|reprint|expanded|complete|definitive|paperback|"
                     r"hardcover|first|second|third|fourth|fifth|sixth|\d+(st|nd|rd|th))\b", re.I)
ROLES = re.compile(r"\b(author|editor|ed|eds|translator|trans|tr|illustrator|introduction|foreword|afterword|contributor)\b\.?", re.I)
SHRINK_TAG = re.compile(r"\s+-\s+(720p|1080p|1440p|4K|\d+px)(\s*\(\d+\))?$", re.I)
VOLUME = re.compile(r"\s+(v|vol\.?|volume|#|book|tome)\s*\.?\s*\d+.*$", re.I)
YEAR = re.compile(r"\b(1[4-9]\d\d|20\d\d|2100)\b")
MD5 = re.compile(r"^[0-9a-f]{32}$", re.I)
ISBN = re.compile(r"^(97[89])?\d{9}[\dX]$", re.I)
FORMAT_ORDER = ("EPUB", "AZW3", "MOBI", "AZW", "PRC", "FB2", "PDF", "XPS", "DOCX", "CBZ", "HTML", "MD", "TXT")


# ------------------------------------------------------------------ names


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def fold(text: str) -> str:
    """For comparing names: no accents, no case, letters and digits only."""
    text = unicodedata.normalize("NFKD", nfc(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^0-9a-z]+", " ", text).strip()


def smart_title(text: str) -> str:
    """'STARSHIP TROOPERS' -> 'Starship Troopers' (only used on all-capital or all-lower titles)."""
    words = text.lower().split()
    out = []
    for index, word in enumerate(words):
        if index and word in SMALL_WORDS and not out[-1].endswith(":"):
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def clean_title(text: str) -> str:
    text = nfc(text).replace("_ ", ": ").replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip(" ,;-")
    letters = [c for c in text if c.isalpha()]
    words = [w for w in re.findall(r"[^\W\d_]{3,}", text)]
    shouting = words and sum(w.isupper() for w in words) >= 0.6 * len(words)
    if letters and (shouting or all(c.islower() for c in letters)):
        text = smart_title(text)
    return text


def split_title(title: str) -> tuple[str, str]:
    """'The Storm of Steel: Original 1929 Translation' -> ('The Storm of Steel', 'Original 1929 Translation')."""
    match = re.match(r"^(.+?)\s*[:–—]\s+(.+)$", title)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    match = re.match(r"^(.+?),\s+((?:\w+\s+)?(?:edition|ed\.))$", title, re.I)  # 'Readings ..., Fifth Edition'
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return title, ""


def work_key(title: str) -> str:
    """Titles that name the same work give the same key: no subtitle, no leading article, no punctuation."""
    main, _sub = split_title(clean_title(title))
    words = fold(main).split()
    if len(words) > 1 and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words)


def clean_author(text: str) -> str:
    """'Jünger, Ernst, 1895-1998, author' -> 'Ernst Jünger'; 'Hansen, Thomas S(Translator)' -> 'Thomas S Hansen'."""
    text = nfc(text)
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = re.sub(r"\b\d{3,4}\s*-\s*(\d{3,4})?|\bb\.\s*\d{4}|\bd\.\s*\d{4}", " ", text)
    parts = [p.strip() for p in text.split(",")]
    parts = [p for p in parts if p and not ROLES.fullmatch(p.strip(". ")) and not re.fullmatch(r"[\d\s.-]+", p)]
    if len(parts) == 2 and len(parts[0].split()) <= 3 and len(parts[1].split()) <= 3:
        parts = [parts[1], parts[0]]          # 'Last, First'
    name = re.sub(r"\s+", " ", " ".join(parts)).strip(" .;")
    if name and (name.isupper() or name.islower()):
        name = " ".join(w[:1].upper() + w[1:].lower() for w in name.split())
    return name


def split_authors(text: str) -> list[str]:
    """A list of people from one field: 'A;B', 'A & B', 'A and B', or 'First Last, First Last'."""
    text = nfc(text)
    if ";" in text:
        pieces = text.split(";")
    elif re.search(r"\s(&|and|und)\s", text):
        pieces = re.split(r"\s(?:&|and|und)\s", text)
    else:
        parts = [p.strip() for p in re.sub(r"\[[^\]]*\]", "", text).split(",") if p.strip()]
        if len(parts) >= 2 and all(len(p.split()) >= 2 for p in parts):
            pieces = parts                    # 'Antoine de Saint-Exupéry, Alan Wakeman'
        else:
            pieces = [text]
    return [name for name in (clean_author(p) for p in pieces) if name]


def surname(name: str) -> str:
    """The word a name is filed under: 'Antoine de Saint-Exupéry' -> 'Saint-Exupéry'."""
    words = [w for w in nfc(name).replace(".", " ").split() if w.lower() not in ("jr", "sr", "ii", "iii")]
    return words[-1] if words else ""


def sort_key(name: str) -> str:
    return fold(surname(name) + " " + name)


def inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:  # different drives
        return False


def safe_name(text: str, limit: int = 90) -> str:
    """A folder or file name Windows accepts."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", nfc(text).replace(":", " -"))
    text = re.sub(r"\s+", " ", text).strip(" .")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].strip(" .")
    return text or "Untitled"


# ------------------------------------------------------------------ describing a file


@dataclass
class Parsed:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    publisher: str = ""
    year: str = ""
    edition: list[str] = field(default_factory=list)
    anna: bool = False           # 'Title -- Authors -- ...' names, whose author field is reliable
    author_side_known: bool = False


def parse_filename(path: str, known: Callable[[str], bool] = lambda name: False) -> Parsed:
    stem = nfc(os.path.splitext(os.path.basename(path))[0]).replace("’", "'")
    out = Parsed()
    if " -- " in stem:
        parts = [p.strip() for p in stem.split(" -- ") if p.strip()]
        if parts and "archive" in parts[-1].lower():
            parts = parts[:-1]
        out.anna = True
        out.title = parts[0]
        if len(parts) > 1:
            out.authors = split_authors(parts[1])
        others = []
        for part in parts[2:]:
            clean = part.replace("_", " ").strip(" ,")
            if MD5.match(clean) or ISBN.match(clean.replace("-", "")):
                continue
            if re.search(r"\bseries\b", clean, re.I):
                continue  # a book series name (often cut short), not the publisher
            year = YEAR.search(clean)
            if year:
                out.year = out.year or year.group(1)
                continue
            others.append(clean)
        if others:  # the last loose field is the publisher ('Harmondsworth : Penguin' -> 'Penguin')
            publisher = re.split(r"\s+[:]\s+|\s{2,}", others[-1])[-1]
            out.publisher = re.sub(r"\bCo\s*$", "Co.", publisher).strip()
        return out
    for group in re.findall(r"\(([^)]*)\)", stem):
        year = YEAR.search(group)
        if year:
            out.year = out.year or year.group(1)
        bits = [b.strip() for b in YEAR.sub("", group).split(",") if b.strip()]
        out.edition.extend(b for b in bits if not ROLES.fullmatch(b.strip(". ")))
    stem = re.sub(r"\s*\([^)]*\)|\s*\[[^\]]*\]", "", stem).strip()
    by = re.match(r"^(.+?)\s+by\s+(.+)$", stem)
    if by:
        out.title, out.authors = by.group(1), split_authors(by.group(2))
        out.author_side_known = True
        return out
    if " - " in stem:
        left, right = (s.strip() for s in stem.split(" - ", 1))
        out.title, author = _pick_sides(left, right, known)
        if author:
            out.authors = split_authors(author)
            out.author_side_known = True
        return out
    out.title = stem
    return out


def _pick_sides(left: str, right: str, known: Callable[[str], bool]) -> tuple[str, str]:
    """Which half of 'A - B' is the author? Returns (title, author)."""
    def person(text):
        words = text.replace(",", " ").split()
        return 1 <= len(words) <= 4 and all(w[:1].isupper() or w.lower() in PARTICLES or len(w) <= 2 for w in words)

    for side, other in ((left, right), (right, left)):
        if known(side):
            return other, side
    for side, other in ((left, right), (right, left)):
        if "," in side and person(side):
            return other, side
    if person(right) and len(right.split()) <= 3:
        return left, right
    if person(left) and len(left.split()) <= 2:
        return right, left
    return f"{left} - {right}", ""


@dataclass
class Entry:
    path: str
    kind: str
    format: str
    author: str
    work: str            # the work's title, used for its folder
    title: str           # the full title (with subtitle) for display
    label: str           # what sets this variant apart: edition, publisher, year, size tag
    key: str = ""

    def destination(self) -> tuple[str, str, str]:
        """(author folder, work folder, file name) inside the library."""
        author = safe_name(self.author or UNKNOWN_AUTHOR, 60)
        work = safe_name(self.work, 80)
        label = f" ({safe_name(self.label, 70)})" if self.label else ""
        if self.kind == "comic" and self.label:
            label = f" - {safe_name(self.label, 40)}"
        return author, work, work + label + documents.extension(self.path)


def _case_score(title: str) -> int:
    """Higher for normal title case: capitalised words count for, words in capitals against."""
    words = re.findall(r"[^\W\d_]{3,}", title)
    return sum(1 for w in words if w[:1].isupper() and not w.isupper()) - 2 * sum(1 for w in words if w.isupper())


def _year_of(date: str) -> str:
    found = YEAR.search(date or "")
    return found.group(1) if found else ""


def describe(path: str, known_authors: list[str] | None = None) -> Entry:
    """Work out who wrote a file, which work it is, and what makes this copy different."""
    known_authors = known_authors or []
    kind = documents.kind_of(path)
    fmt = documents.format_name(path)
    meta = documents.read_metadata(path)
    if kind == "comic":
        return _describe_comic(path, meta, fmt)

    def known(text: str) -> bool:
        return _match_author(clean_author(text), known_authors) != ""

    parsed = parse_filename(path, known)
    if parsed.anna and parsed.authors:
        author = parsed.authors[0]
    elif meta.authors:
        author = (split_authors(meta.author) or [clean_author(meta.author)])[0]
    elif parsed.authors:
        author = parsed.authors[0]
    else:
        author = ""
    author = _match_author(author, known_authors) or author

    meta_title, file_title = clean_title(meta.title), clean_title(parsed.title)
    if meta_title and file_title and fold(meta_title) == fold(file_title):
        title = file_title if _case_score(file_title) > _case_score(meta_title) else meta_title
    elif meta_title and not (parsed.anna and len(file_title) > len(meta_title) and fold(file_title).startswith(fold(meta_title))):
        title = meta_title
    else:
        title = file_title or meta_title or archive.display_name(path)
    main, sub = split_title(title)
    edition = list(parsed.edition)
    if sub and EDITION.search(sub):
        edition.insert(0, sub)
        title = main
    work = main if (sub and not re.search(r"\b(vol(ume)?|part|book|tome)\b", sub, re.I)) else title
    work = re.sub(r",\s*(\w+\s+)?edition$", "", work, flags=re.I).strip()  # 'Readings ..., Fifth Edition'

    publisher = ""
    for candidate in (meta.publisher, parsed.publisher):  # the first one that names somebody
        candidate = re.sub(r"\s*\b(Ltd|LLC|Inc|GmbH)\.?$", "", candidate or "").strip(" ,")
        if candidate and candidate.lower() not in ("independently published", "unknown", "self-published", "n/a"):
            publisher = candidate
            break
    year = parsed.year or _year_of(meta.date)
    bits = []
    for bit in edition + [publisher, year]:
        bit = clean_title(bit) if bit and not bit.isdigit() else bit
        if bit and fold(bit) not in {fold(b) for b in bits} and not any(fold(bit) in fold(b) for b in bits):
            bits.append(bit)
    entry = Entry(path, kind, fmt, author, work, title, ", ".join(bits))
    entry.key = work_key(work)
    return entry


def _describe_comic(path: str, meta: documents.Meta, fmt: str) -> Entry:
    name = archive.display_name(path)
    tag = SHRINK_TAG.search(name)
    label = tag.group(1) if tag else ""
    name = SHRINK_TAG.sub("", name).strip()
    series = meta.series or VOLUME.sub("", name).strip() or name
    author = meta.author or series
    entry = Entry(path, "comic", fmt, author, name, name, label)
    entry.key = fold(name)
    return entry


def _match_author(name: str, known: list[str]) -> str:
    """'Jünger' or 'ernst junger' -> 'Ernst Jünger' when that is the only author it can be."""
    if not name:
        return ""
    key = fold(name)
    for other in known:
        if fold(other) == key:
            return other
    if len(key.split()) == 1:
        hits = {fold(o): o for o in known if fold(surname(o)) == key}
        if len(hits) == 1:
            return next(iter(hits.values()))
    return ""


# ------------------------------------------------------------------ the index


def fingerprint(path: str) -> str:
    """Identical files give identical prints (size plus the first and last megabyte)."""
    size = os.path.getsize(path)
    digest = hashlib.sha1(str(size).encode())
    with open(path, "rb") as handle:
        digest.update(handle.read(1 << 20))
        if size > 2 << 20:
            handle.seek(-(1 << 20), os.SEEK_END)
            digest.update(handle.read(1 << 20))
    return digest.hexdigest()


def signature(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_size}-{int(st.st_mtime)}"


class Index:
    """Remembers, per file in the library: its label and title, its print, and where it was copied from."""

    def __init__(self, root: str):
        self.root = root
        self.path = os.path.join(root, INDEX_NAME)
        self.files: dict[str, dict] = {}
        self.sources: dict[str, str] = {}   # source path -> its signature when copied (or skipped as a duplicate)
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.files = {k: v for k, v in data.get("files", {}).items() if isinstance(v, dict)}
            self.sources = {k: v for k, v in data.get("sources", {}).items() if isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            pass

    def rel(self, path: str) -> str:
        return os.path.relpath(os.path.abspath(path), self.root).replace("\\", "/")

    def entry(self, path: str) -> dict:
        return self.files.get(self.rel(path), {})

    def prints(self) -> dict[str, str]:
        return {v["fp"]: k for k, v in self.files.items() if v.get("fp")}

    def save(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        temp = self.path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "files": self.files, "sources": self.sources}, handle, indent=1, ensure_ascii=False)
        os.replace(temp, self.path)


# ------------------------------------------------------------------ importing


@dataclass
class Plan:
    copies: list[tuple[Entry, str]] = field(default_factory=list)   # (entry, destination path)
    duplicates: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)                # not a book
    copied: list[tuple[str, str]] = field(default_factory=list)     # (source, copy) once run_import is done


def find_books(paths: list[str]) -> list[str]:
    """Files and the books inside folders, in a stable order."""
    found = []
    for path in paths:
        if os.path.isdir(path):
            for folder, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files, key=archive.natural_key):
                    if documents.extension(name) in documents.BOOK_EXT and not name.startswith("."):
                        found.append(os.path.join(folder, name))
        elif os.path.isfile(path):
            found.append(path)
    return found


def existing_authors(root: str) -> list[str]:
    try:
        return [n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)) and not n.startswith(".")]
    except OSError:
        return []


def plan_import(paths: list[str], root: str, index: Index, only_new: bool = False) -> Plan:
    """Decide where every book goes. Nothing is copied yet."""
    plan = Plan()
    root = os.path.abspath(root)
    candidates = []
    prints = index.prints()
    for path in find_books(paths):
        path = os.path.abspath(path)
        if documents.extension(path) not in documents.BOOK_EXT:
            plan.skipped.append(path)
            continue
        if inside(path, root):
            continue  # already in the library
        try:
            sig = signature(path)
        except OSError:
            plan.skipped.append(path)
            continue
        if only_new and index.sources.get(path) == sig:
            continue
        try:
            fp = fingerprint(path)
        except OSError:
            plan.skipped.append(path)
            continue
        if fp in prints:
            plan.duplicates.append(path)
            index.sources[path] = sig
            continue
        prints[fp] = path
        candidates.append((path, sig, fp))

    known = existing_authors(root)
    entries = []
    # two passes: full names found anywhere first resolve 'Jünger - Copse 125' to 'Ernst Jünger'
    first = [describe(path, known) for path, _sig, _fp in candidates]
    known += [e.author for e in first if e.author and len(e.author.split()) >= 2 and e.kind != "comic"]
    for (path, sig, fp), rough in zip(candidates, first):
        entry = describe(path, known) if rough.author and len(rough.author.split()) == 1 else rough
        entry.fp, entry.sig = fp, sig  # type: ignore[attr-defined]
        entries.append(entry)

    works: dict[tuple[str, str], str] = {}      # (author key, work key) -> work folder
    used: set[str] = set()
    for author_folder in existing_authors(root):
        for work_folder in _subfolders(os.path.join(root, author_folder)):
            works[(fold(author_folder), work_key(work_folder))] = os.path.join(author_folder, work_folder)
    for entry in entries:
        author_dir, work_dir, file_name = entry.destination()
        author_dir = next((a for a in existing_authors(root) if fold(a) == fold(author_dir)), author_dir)
        # one folder per work: later variants join the folder of the first, under its name
        slot = works.setdefault((fold(author_dir), entry.key or fold(work_dir)), os.path.join(author_dir, work_dir))
        work_name = os.path.basename(slot)
        if entry.kind != "comic":
            file_name = work_name + file_name[len(safe_name(entry.work, 80)):]
        target = os.path.join(root, slot, file_name)
        stem, ext = os.path.splitext(target)
        number = 2
        while os.path.exists(target) or target.lower() in used:
            target = f"{stem} ({number}){ext}"
            number += 1
        used.add(target.lower())
        plan.copies.append((entry, target))
    return plan


def _subfolders(folder: str) -> list[str]:
    try:
        return [n for n in os.listdir(folder) if os.path.isdir(os.path.join(folder, n)) and not n.startswith(".")]
    except OSError:
        return []


def run_import(plan: Plan, index: Index, progress: Callable[[int, int, str], None] | None = None,
               cancel: threading.Event | None = None) -> tuple[int, list[str]]:
    """Copy the planned files in. Returns (number copied, error messages)."""
    done, errors = 0, []
    total = len(plan.copies)
    for number, (entry, target) in enumerate(plan.copies, 1):
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(number, total, entry.title or os.path.basename(entry.path))
        part = target + ".part"
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(entry.path, part)
            shutil.copystat(entry.path, part)
            os.replace(part, target)
        except OSError as exc:
            errors.append(f"{os.path.basename(entry.path)}: {exc}")
            try:
                os.remove(part)
            except OSError:
                pass
            continue
        index.files[index.rel(target)] = {
            "title": entry.title, "label": entry.label, "author": entry.author, "format": entry.format,
            "fp": getattr(entry, "fp", ""), "source": entry.path,
        }
        index.sources[entry.path] = getattr(entry, "sig", "")
        plan.copied.append((entry.path, target))
        done += 1
        if number % 10 == 0:
            index.save()
    index.save()
    return done, errors


def destination_folder(path: str, root: str) -> str:
    """The work folder a file belongs in (for writing a shrunk copy beside its siblings)."""
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    if inside(path, root):
        return os.path.dirname(path)
    entry = describe(path, existing_authors(root))
    author_dir, work_dir, _name = entry.destination()
    author_dir = next((a for a in existing_authors(root) if fold(a) == fold(author_dir)), author_dir)
    for work in _subfolders(os.path.join(root, author_dir)):
        if work_key(work) == (entry.key or fold(work_dir)):
            return os.path.join(root, author_dir, work)
    return os.path.join(root, author_dir, work_dir)


# ------------------------------------------------------------------ reading the library


@dataclass
class Variant:
    path: str
    format: str
    label: str
    size: int
    details: dict = field(default_factory=dict)   # what analyze found (publisher, year, ...)


@dataclass
class Work:
    title: str
    folder: str
    variants: list[Variant]

    def best(self) -> Variant:
        """The variant to show the cover of and to open by default: the richest format."""
        return sorted(self.variants, key=lambda v: (FORMAT_ORDER.index(v.format) if v.format in FORMAT_ORDER else 99, bool(v.label)))[0]


@dataclass
class Author:
    name: str
    folder: str
    works: list[Work]


def scan(root: str) -> list[Author]:
    """The library as it is on disk."""
    index = Index(root)
    authors = []
    for author_name in existing_authors(root):
        author_dir = os.path.join(root, author_name)
        works = []
        try:
            names = os.listdir(author_dir)
        except OSError:
            continue
        for name in names:
            full = os.path.join(author_dir, name)
            if name.startswith("."):
                continue
            if os.path.isdir(full):
                variants = _variants(full, name, index)
                if variants:
                    works.append(Work(name, full, variants))
            elif documents.extension(name) in documents.BOOK_EXT:  # a loose file is a work of its own
                title = os.path.splitext(name)[0]
                works.append(Work(title, author_dir, [_variant(full, title, index)]))
        if works:
            works.sort(key=lambda w: archive.natural_key(w.title))
            authors.append(Author(author_name, author_dir, works))
    loose = [os.path.join(root, n) for n in _files(root)]
    if loose:
        authors.append(Author("Unsorted", root, [Work(os.path.splitext(os.path.basename(p))[0], root,
                                                      [_variant(p, "", index)]) for p in loose]))
    authors.sort(key=lambda a: sort_key(a.name) if a.name != "Unsorted" else "￿")
    return authors


def _files(folder: str) -> list[str]:
    try:
        return sorted((n for n in os.listdir(folder) if documents.extension(n) in documents.BOOK_EXT
                       and os.path.isfile(os.path.join(folder, n)) and not n.startswith(".")), key=archive.natural_key)
    except OSError:
        return []


def _variants(folder: str, work: str, index: Index) -> list[Variant]:
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(files, key=archive.natural_key):
            if documents.extension(name) in documents.BOOK_EXT and not name.startswith("."):
                found.append(_variant(os.path.join(root, name), work, index))
    order = {fmt: i for i, fmt in enumerate(FORMAT_ORDER)}
    found.sort(key=lambda v: (bool(v.label) and v.format == "CBZ", order.get(v.format, 99), archive.natural_key(v.label)))
    return found


def _variant(path: str, work: str, index: Index) -> Variant:
    saved = index.entry(path)
    label = saved.get("label")
    if label is None:  # not imported by us: read the label off the file name
        stem = nfc(os.path.splitext(os.path.basename(path))[0])
        rest = stem[len(work):] if work and fold(stem).startswith(fold(work)) else ""
        tag = SHRINK_TAG.search(stem)
        label = tag.group(1) if tag else re.sub(r"^[\s\-–(]+|[\s)]+$", "", rest)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return Variant(path, documents.format_name(path), label, size, saved.get("details") or {})


def next_work(path: str, root: str) -> str:
    """For 'next volume': the matching variant of the following work by the same author or series."""
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    if not inside(path, root):
        return ""
    for author in scan(root):
        for position, work in enumerate(author.works):
            for variant in work.variants:
                if os.path.normcase(variant.path) != os.path.normcase(path):
                    continue
                if position + 1 >= len(author.works):
                    return ""
                following = author.works[position + 1]
                current = Variant(path, variant.format, variant.label, 0)
                same = [v for v in following.variants if v.label == current.label and v.format == current.format]
                same = same or [v for v in following.variants if v.format == current.format]
                return (same or [following.best()])[0].path
    return ""
