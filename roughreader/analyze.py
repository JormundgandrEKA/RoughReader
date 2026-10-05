"""Reading a book's front matter to find out what edition it is (no Qt here).

The title page and copyright page say who published this edition, when, under which ISBN, who
translated it and which edition it is. They are read from the text layer, or with OCR for scans, and
combined with the file's metadata and its name. Every value records where it came from, and anything
missing or contradictory is listed as a warning, for the Details dialog to show and the user to fix.
This runs once per file (a few seconds at most) and the result is kept in the library index.
"""

from __future__ import annotations

import datetime
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Callable

from . import documents, ocr, organize

VERSION = 1   # bump to have every file read again
FRONT_PAGES = 10
BACK_PAGES = 3
PUBLISHERS = (
    "Penguin Classics", "Penguin Books", "Penguin Random House", "Penguin", "Allen Lane", "Vintage", "Random House",
    "HarperCollins", "Harper Perennial", "Harper & Row", "Simon & Schuster", "Houghton Mifflin Harcourt",
    "Houghton Mifflin", "Little, Brown", "Macmillan", "St. Martin's Press", "W. W. Norton", "Norton", "Knopf",
    "Doubleday", "Bantam", "Ballantine", "Del Rey", "Ace Books", "Tor Books", "Orbit", "Gollancz", "Faber and Faber",
    "Faber & Faber", "Bloomsbury", "Picador", "Granta", "Verso", "Routledge", "Pan Books", "Arcturus", "Wordsworth",
    "Oxford University Press", "Cambridge University Press", "Columbia University Press", "Yale University Press",
    "Harvard University Press", "Princeton University Press", "University of Chicago Press", "MIT Press",
    "University of Toronto Press", "New York Review Books", "NYRB", "Telos Press", "Howard Fertig", "Yen Press",
    "Seven Seas", "Kodansha", "Viz Media", "Marsilio", "Klett-Cotta", "Suhrkamp", "Fischer", "Rowohlt", "dtv",
    "Chatto & Windus", "Hodder & Stoughton", "Hutchinson", "Jonathan Cape", "Secker & Warburg", "Dover",
    "Basic Books", "Pantheon", "Anchor Books", "Grove Press", "New Directions", "Tuttle", "RosettaBooks",
    "Houghton Mifflin Company", "Signet", "Bantam Books", "Mystery Grove", "Random Shack", "Perseus Books",
)
EDITION_WORDS = r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d{1,2}(?:st|nd|rd|th))"


@dataclass
class Details:
    title: str = ""
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    translators: list[str] = field(default_factory=list)
    editors: list[str] = field(default_factory=list)
    publisher: str = ""
    year: str = ""             # this edition
    original_year: str = ""    # the work's first publication, if the book says
    edition: str = ""
    isbn: str = ""
    language: str = ""
    series: str = ""
    format: str = ""
    pages: int = 0
    source: str = ""           # where the file came from, as far as its name tells (e.g. Anna's Archive)
    origins: dict = field(default_factory=dict)   # field -> where the value was found
    warnings: list[str] = field(default_factory=list)
    used_ocr: bool = False
    manual: bool = False       # set by the user: never overwritten by a new analysis
    version: int = VERSION

    @property
    def author(self) -> str:
        return self.authors[0] if self.authors else ""

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Details":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def label(self) -> str:
        """What sets this version apart, for the library: edition, publisher, year."""
        bits = [b for b in (self.edition, self.publisher, self.year) if b]
        return ", ".join(bits)


# ------------------------------------------------------------------ ISBN


def isbn_valid(isbn: str) -> bool:
    digits = isbn.replace("-", "").replace(" ", "").upper()
    if len(digits) == 10 and re.fullmatch(r"\d{9}[\dX]", digits):
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(digits))
        return total % 11 == 0
    if len(digits) == 13 and digits.isdigit() and digits[:3] in ("978", "979"):
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits[:12]))
        return (10 - total % 10) % 10 == int(digits[12])
    return False


def to_isbn13(isbn: str) -> str:
    digits = isbn.replace("-", "").replace(" ", "").upper()
    if len(digits) == 13:
        return digits
    core = "978" + digits[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


def find_isbns(text: str) -> list[str]:
    """Valid ISBNs in a text, as ISBN-13, the ones labelled 'ISBN' first."""
    found = []
    for match in re.finditer(r"(ISBN[\s:-]*(?:1[03])?[\s:]*)?((?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx])", text):
        candidate = re.sub(r"[\s-]", "", match.group(2))
        for fix in (candidate, candidate.replace("O", "0").replace("l", "1")):
            if isbn_valid(fix):
                isbn = to_isbn13(fix)
                if isbn not in found:
                    found.append(isbn) if match.group(1) else found.append(isbn)
                break
    return found


# ------------------------------------------------------------------ reading the text


COPYRIGHT_CUES = re.compile(r"ISBN|all rights reserved|©|copyright|first published|published by|printed in", re.I)


def front_matter(path: str, use_ocr: bool = True) -> tuple[list[tuple[str, bool]], int]:
    """([(text, read by OCR)] for the first and last few pages, page count)."""
    book = documents.open_book(path)
    try:
        if book.kind == "comic":
            return [], len(book)
        if book.kind == "reflow":
            book.configure(700, 1000)
        count = len(book)
        pages = list(range(min(FRONT_PAGES, count))) + list(range(max(FRONT_PAGES, count - BACK_PAGES), count))
        out = []
        for page in pages:
            layer = book.text_layer(page, allow_ocr=use_ocr)
            out.append((layer.text(), layer.ocr))
        return out, count
    finally:
        book.close()


def _legal_score(text: str) -> int:
    low = text.lower()
    score = 4 * min(2, low.count("isbn")) + 4 * ("all rights reserved" in low)
    score += 2 * sum(phrase in low for phrase in ("first published", "published by", "printed in", "this edition", "library of congress"))
    score += min(2, low.count("©") + low.count("copyright"))
    score -= 3 * (low.count("reprinted by permission") + low.count("reproduced with permission")) // 2
    return score


def copyright_text(pages: list[tuple[str, bool]]) -> tuple[str, bool]:
    """The copyright page (and its runner-up if nearly as good): where this edition is described.
    Credits and permissions pages, which also carry many (c) marks, lose. (text, read by OCR)"""
    scored = sorted(((_legal_score(t), i) for i, (t, _o) in enumerate(pages)), reverse=True)
    if not scored or scored[0][0] < 3:
        return "", False
    best = scored[0][0]
    chosen = sorted(i for score, i in scored[:2] if score >= 0.6 * best)
    text = " ".join(re.sub(r"\s+", " ", pages[i][0]) for i in chosen)
    return text, any(pages[i][1] for i in chosen)


def _years(text: str) -> list[int]:
    now = datetime.date.today().year
    return [int(y) for y in re.findall(r"\b(1[5-9]\d\d|20\d\d)\b", text) if int(y) <= now]


def edition_year(text: str) -> tuple[str, str]:
    """(year of this edition, year of first publication) from a copyright page."""
    this = ""
    for pattern in (r"this (?:\w+ )?edition[^.\n]{0,80}?(?:published|first published|issued)[^.\n]{0,40}?\b(1[5-9]\d\d|20\d\d)\b",
                    r"published in [\w ]{0,30}?(?:paperback|penguin|vintage|books)\s+(\d{4})",
                    r"(?:paperback|reprint|reissue)d? (?:edition )?(?:published|issued)?[^.\n]{0,40}?\b(1[5-9]\d\d|20\d\d)\b",
                    r"(?<!first )(?<!originally )(?<!first )published[^©]{0,50}?\b(1[5-9]\d\d|20\d\d)\b"):
        found = re.findall(pattern, text, re.I)
        if found:
            this = str(max(int(y) for y in found))
            break
    first = re.findall(r"first published[^©]{0,80}?\b(1[5-9]\d\d|20\d\d)\b|originally published[^©]{0,80}?\b(1[5-9]\d\d|20\d\d)\b|(?:©|copyright)[^©]{0,60}?\b(1[5-9]\d\d|20\d\d)\b", text, re.I)
    years = [int(y) for group in first for y in group if y]
    original = str(min(years)) if years else ""
    if not this and years:
        this = str(max(years))
    return this, original


STOP_WORDS = {"copyright", "all", "isbn", "printed", "first", "this", "the", "in", "inc", "inc.", "ltd", "ltd.", "llc",
              "co", "co.", "gmbh", "library", "manufactured", "published", "reprinted", "edition", "london", "new"}


def _published_by(text: str) -> str:
    """The name after 'published ... by', as a run of capitalised words (& / and / of allowed inside)."""
    match = re.search(r"(?i:published)(?: [\w,]+){0,4}? by ((?:[A-Z][\w&'\-]*\.?|&|and|of)(?: (?:[A-Z][\w&'\-]*\.?|&|and|of))*)", text)
    if not match:
        return ""
    words = []
    for word in match.group(1).split():
        if word.lower().strip(".,") in STOP_WORDS and words:
            break
        words.append(word.strip(","))
    while words and words[-1].lower() in ("&", "and", "of"):
        words.pop()
    name = " ".join(words).strip(" ,.")
    return name if 2 < len(name) < 50 else ""


def find_publisher(text: str) -> str:
    published_by = _published_by(text)
    for name in PUBLISHERS:  # a known name on the copyright page beats a guess
        if re.search(r"\b" + re.escape(name) + r"\b", text):
            return name
    return published_by


def find_edition(text: str) -> str:
    found = re.search(EDITION_WORDS + r"\s+(?:(paperback|hardcover|revised|expanded|american|british|english|deluxe|anniversary)\s+)?edition", text, re.I)
    if not found:
        return ""
    words = [w for w in found.groups() if w]
    return " ".join(w.capitalize() for w in words) + " Edition"


def find_translators(text: str) -> list[str]:
    names = []
    for match in re.finditer(r"(?i:translated (?:from the \w+ )?(?:and (?:edited|introduced) )?(?:with an introduction )?by )([A-Z][\w.\-]+(?: [A-Z][\w.\-]+){0,3})", text):
        kept = []
        for token in match.group(1).split():
            if token.endswith(".") and len(token) > 2:  # a whole word with a full stop ends the sentence
                kept.append(token.rstrip("."))
                break
            kept.append(token)
        name = " ".join(kept).strip(" .")
        if name not in names:
            names.append(name)
    return names


# ------------------------------------------------------------------ putting it together


def analyse(path: str, known_authors: list[str] | None = None, use_ocr: bool = True, hints: dict | None = None) -> Details:
    """`hints` from the library index (author, title found when the file was added, and the original
    file name, which often carries publisher, year and ISBN) take the place of the copy's own name."""
    hints = hints or {}
    entry = organize.describe(path, known_authors or [])
    source_name = hints.get("source") or path
    if hints.get("author"):
        entry.author = hints["author"]
    if hints.get("title"):
        entry.title = hints["title"]
        entry.work = organize.split_title(hints["title"])[0] if entry.kind != "comic" else entry.work
    meta = documents.read_metadata(path)
    parsed = organize.parse_filename(source_name)
    details = Details(format=entry.format)
    origins = details.origins

    main, sub = organize.split_title(entry.title)
    details.title, details.subtitle = entry.work, (sub if sub and sub != main else "")
    origins["title"] = "metadata" if meta.title else "file name"
    if entry.kind == "comic":
        details.series = entry.author
        details.title = entry.work
        origins["title"] = "file name"
    else:
        if entry.author:
            details.authors = [entry.author]
            origins["authors"] = "file name" if (parsed.anna or not meta.authors) else "metadata"
        others = [organize.clean_author(n) for n, role in meta.authors if role in ("trl", "translator")]
        details.translators = [n for n in others if n]

    try:
        pages, count = front_matter(path, use_ocr) if entry.kind != "comic" else ([], 0)
    except Exception:
        pages, count = [], 0
    text = "\n".join(t for t, _o in pages)
    legal, legal_ocr = copyright_text(pages)
    details.pages, details.used_ocr = count, any(o for _t, o in pages)
    copyright_page = "copyright page (read by OCR)" if legal_ocr else "copyright page"

    def plain(value: str) -> str:
        value = re.sub(r",?\s*\b(Ltd|LLC|Inc|GmbH|Co)\.?$", "", (value or "").replace("_", "")).strip(" ,.")
        return "" if value.lower() in ("independently published", "unknown", "self-published", "n/a") else value

    # publisher: the file's own name (which edition this copy is) unless the copyright page says the same
    # at greater length; then the copyright page; then the metadata
    page_publisher = find_publisher(legal)
    named = plain(parsed.publisher)
    if named:
        if page_publisher and named.lower() in page_publisher.lower():
            details.publisher, origins["publisher"] = page_publisher, copyright_page
        else:
            details.publisher, origins["publisher"] = named, "original file name"
    elif page_publisher:
        details.publisher, origins["publisher"] = page_publisher, copyright_page
    elif plain(meta.publisher):
        details.publisher, origins["publisher"] = plain(meta.publisher), "metadata"

    this_year, first_year = edition_year(legal or re.sub(r"\s+", " ", text))
    explicit = bool(re.search(r"this (?:\w+ )?edition|published in (?:\w+ ){0,3}(?:paperback|penguin|vintage)", legal, re.I))
    meta_year = organize._year_of(meta.date)
    if this_year and explicit:
        details.year, origins["year"] = this_year, copyright_page
    elif parsed.year:
        details.year, origins["year"] = parsed.year, "original file name"
    elif this_year:
        details.year, origins["year"] = this_year, copyright_page
    elif meta_year and not meta_year.startswith("0"):
        details.year, origins["year"] = meta_year, "metadata"
    if first_year and first_year != details.year:
        details.original_year = first_year

    edition = find_edition(legal) or next((e for e in parsed.edition if organize.EDITION.search(e)), "")
    if not edition and sub and organize.EDITION.search(sub):
        edition = sub
    details.edition = edition
    if edition:
        origins["edition"] = copyright_page if find_edition(legal) else "title"

    isbns = find_isbns(legal) or find_isbns(text)
    file_isbns = [to_isbn13(p) for p in re.findall(r"\b(97[89]\d{10}|\d{9}[\dX])\b", source_name) if isbn_valid(p)]
    if file_isbns:
        details.isbn, origins["isbn"] = file_isbns[0], "original file name"
    elif isbns:
        details.isbn, origins["isbn"] = isbns[0], copyright_page

    translators = find_translators(legal) or find_translators(text)
    if translators and not details.translators:
        details.translators, origins["translators"] = translators, copyright_page
    details.language = meta.language or ("de" if ocr.looks_german(text) else ("en" if text else ""))
    if parsed.anna:
        details.source = "Anna's Archive"
    if hints.get("author"):
        origins["authors"] = "when added to the library"

    # what a person should look at
    if entry.kind != "comic":
        if not details.authors:
            details.warnings.append("No author found.")
        elif origins.get("authors") == "file name" and not parsed.anna:
            details.warnings.append("The author comes only from the file name.")
        if not details.publisher:
            details.warnings.append("No publisher found.")
        if not details.year:
            details.warnings.append("No date of publication found.")
        if meta.title and organize.work_key(meta.title) != organize.work_key(details.title):
            details.warnings.append(f"The file's metadata calls it “{meta.title}”.")
        if not text:
            details.warnings.append("No readable text in the first pages" + ("" if use_ocr and ocr.available() else " (OCR not available)") + ".")
    return details


# ------------------------------------------------------------------ naming


def file_name(details: Details, extension: str, tag: str = "", kind: str = "") -> str:
    """'Title, Surname, Publisher, Year.ext' (missing parts left out); a shrink tag goes at the end."""
    if kind == "comic":
        parts = [details.title]
    else:
        surname = organize.surname(details.author) if details.author else ""
        parts = [details.title, surname, details.publisher, details.year]
    name = ", ".join(organize.safe_name(p, 70) for p in parts if p)
    if tag:
        name += f" - {tag}"
    return organize.safe_name(name, 150) + extension


# ------------------------------------------------------------------ applying to the library


def _tag(path: str) -> str:
    found = organize.SHRINK_TAG.search(organize.nfc(os.path.splitext(os.path.basename(path))[0]))
    return found.group(1) if found else ""


def _remove_empty(folder: str, root: str) -> None:
    root = os.path.abspath(root)
    while folder and os.path.abspath(folder) != root and organize.inside(folder, root):
        try:
            if any(not n.startswith(".") for n in os.listdir(folder)):
                return
            for name in os.listdir(folder):
                os.remove(os.path.join(folder, name))
            os.rmdir(folder)
        except OSError:
            return
        folder = os.path.dirname(folder)


def target_path(root: str, path: str, details: Details) -> str:
    """Where a library file belongs: Author/Work/'Title, Surname, Publisher, Year.ext'."""
    kind = documents.kind_of(path)
    author = details.series if kind == "comic" and details.series else (details.author or organize.UNKNOWN_AUTHOR)
    author_dir = organize.safe_name(author, 60)
    author_dir = next((a for a in organize.existing_authors(root) if organize.fold(a) == organize.fold(author_dir)), author_dir)
    key = organize.fold(details.title) if kind == "comic" else organize.work_key(details.title)
    work_dir = next((w for w in organize._subfolders(os.path.join(root, author_dir))
                     if (organize.fold(w) if kind == "comic" else organize.work_key(w)) == key), organize.safe_name(details.title, 80))
    name = file_name(details, documents.extension(path), _tag(path), kind)
    return os.path.join(root, author_dir, work_dir, name)


def apply(root: str, path: str, details: Details, index: "organize.Index") -> str:
    """Rename/move one library file to match its details and record them. Returns the new path."""
    root, path = os.path.abspath(root), os.path.abspath(path)
    target = target_path(root, path, details)
    if os.path.normcase(target) != os.path.normcase(path):
        stem, ext = os.path.splitext(target)
        number = 2
        while os.path.exists(target) and os.path.normcase(target) != os.path.normcase(path):
            target = f"{stem} ({number}){ext}"
            number += 1
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(path, target)
        _remove_empty(os.path.dirname(path), root)
    entry = index.files.pop(index.rel(path), {})
    entry.update({"details": details.as_dict(), "title": details.title, "author": details.author or details.series,
                  "label": _tag(target) if documents.kind_of(target) == "comic" else details.label(),
                  "format": details.format})
    index.files[index.rel(target)] = entry
    return target


def pending(root: str, index: "organize.Index") -> list[str]:
    """Library files that have not been read yet (or were read by an older version of this code)."""
    out = []
    for author in organize.scan(root):
        for work in author.works:
            for variant in work.variants:
                saved = index.entry(variant.path).get("details") or {}
                if not saved.get("manual") and saved.get("version", 0) < VERSION:
                    out.append(variant.path)
    return out


def run(root: str, paths: list[str], progress: Callable[[int, int, str], None] | None = None,
        cancel: threading.Event | None = None, use_ocr: bool = True) -> list[tuple[str, str]]:
    """Read every file (in parallel: OCR keeps the processor busy) and rename it to match.
    Returns (old path, new path) pairs for the files that moved."""
    index = organize.Index(root)
    known = [a for a in organize.existing_authors(root)]
    hints = {}
    for path in paths:
        saved = index.entry(path)
        hints[path] = {"author": saved.get("author", ""), "title": saved.get("title", ""),
                       "source": os.path.basename(saved.get("source", "")) or ""}
        if not hints[path]["author"] and documents.kind_of(path) != "comic":  # its folder says who wrote it
            folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
            if organize.inside(path, root) and folder and folder != os.path.basename(root):
                hints[path]["author"] = folder
    results: dict[str, Details] = {}
    done = 0
    lock = threading.Lock()

    def work(path: str) -> None:
        nonlocal done
        if cancel is not None and cancel.is_set():
            return
        try:
            results[path] = analyse(path, known, use_ocr, hints[path])
        except Exception:
            pass
        with lock:
            done += 1
            if progress:
                progress(done, len(paths), os.path.basename(path))

    workers = max(2, min(6, (os.cpu_count() or 2) - 1))
    with ThreadPoolExecutor(workers, thread_name_prefix="roughreader-analyse") as pool:
        list(pool.map(work, paths))
    moved = []
    for path, details in results.items():
        if not os.path.exists(path):
            continue
        try:
            new = apply(root, path, details, index)
        except OSError:
            continue
        if os.path.normcase(new) != os.path.normcase(path):
            moved.append((path, new))
    index.save()
    return moved
