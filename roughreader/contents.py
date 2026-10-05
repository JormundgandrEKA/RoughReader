"""Finding a printed contents page and making it navigable (no Qt here).

For books whose file has no linked table of contents (typically scanned or plainly exported PDFs):
  1. look through the first pages for a contents page (a 'Contents' heading, or many lines that end
     in a page number), using the text layer or OCR;
  2. pair titles with page numbers by their height on the page (titles and numbers are often separate
     blocks in the file);
  3. work out which page of the file a printed page number is: the PDF's own page labels if it has
     them, otherwise the running page numbers printed at the top or bottom of sample pages.
"""

from __future__ import annotations

import re
from collections import Counter

HEADING = re.compile(r"^(table of )?contents$|^contents\b|^inhalt(sverzeichnis)?$|^sommaire$|^indice$|^содержание$", re.I)
ROMAN = re.compile(r"^(?=[ivxlc]+$)c{0,3}(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)
NUMBER = re.compile(r"^\d{1,4}$")
LEADERS = re.compile(r"[\s.·…_\-–]+$")


def roman_value(text: str) -> int:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    total, last = 0, 0
    for char in reversed(text.lower()):
        value = values[char]
        total = total - value if value < last else total + value
        last = max(last, value)
    return total


def _clean_number(token: str) -> str:
    """OCR often reads 1 as I or l in page numbers ('I41' -> '141')."""
    if re.fullmatch(r"[\dIl|O]{1,4}", token) and re.search(r"\d", token):
        return token.replace("I", "1").replace("l", "1").replace("|", "1").replace("O", "0")
    return token


def visual_lines(words: list) -> list[list]:
    """Words grouped into lines by height on the page, each sorted left to right."""
    if not words:
        return []
    heights = sorted(w[3] - w[1] for w in words)
    tolerance = max(0.004, heights[len(heights) // 2] * 0.55)
    lines: list[list] = []
    for word in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        middle = (word[1] + word[3]) / 2
        if lines and abs(middle - lines[-1][0]) <= tolerance:
            lines[-1][1].append(word)
        else:
            lines.append([middle, [word]])
    return [sorted(line, key=lambda w: w[0]) for _m, line in lines]


def page_entries(words: list) -> tuple[list[dict], bool]:
    """(entries, has a 'Contents' heading) for one page."""
    lines = visual_lines(words)
    heading = any(HEADING.match(" ".join(w[4] for w in line).strip(" .:")) for line in lines[:6])
    entries = []
    for line in lines:
        tokens = [w[4] for w in line]
        last = _clean_number(tokens[-1].lstrip(".…·_"))  # dot leaders may stick to the number; a full stop may not
        title = LEADERS.sub("", " ".join(tokens[:-1])).strip()
        if len(tokens) >= 2 and (NUMBER.match(last) or ROMAN.match(last)) and re.search(r"[^\W\d_]{2,}", title):
            if line[-1][0] > 0.45:  # the number sits on the right, as page numbers do
                entries.append({"title": title, "printed": last})
    return entries, heading


def _increasing(entries: list[dict]) -> bool:
    numbers = [int(e["printed"]) for e in entries if NUMBER.match(e["printed"])]
    if len(numbers) < 3:
        return False
    ordered = sum(1 for a, b in zip(numbers, numbers[1:]) if b >= a)
    return ordered >= 0.8 * (len(numbers) - 1)


def _plausible(entries: list[dict], pages: int) -> list[dict]:
    """Drop 'entries' whose number cannot be a page (years on a copyright page, for instance)."""
    keep = []
    for entry in entries:
        printed = entry["printed"]
        if NUMBER.match(printed) and int(printed) > pages + 60:
            continue
        keep.append(entry)
    return keep


def find_contents(book, first_pages: int = 30, progress=None) -> tuple[list[dict], list[int]]:
    """Entries of the printed contents and the pages they were found on ([] if there is none)."""
    found, pages, started = [], [], False
    for index in range(min(first_pages, len(book))):
        if progress:
            progress(index)
        layer = book.text_layer(index)
        entries, heading = page_entries(layer.words)
        entries = _plausible(entries, len(book))
        titled = sum(1 for line in visual_lines(layer.words) if re.search(r"[^\W\d_]{3,}", " ".join(w[4] for w in line)))
        if heading and len(entries) < 0.5 * max(1, titled - 1) and hasattr(book, "ocr_layer"):
            # the file's own text lost the page numbers (common in exported PDFs): read the page instead
            scanned, _ = page_entries(book.ocr_layer(index).words)
            scanned = _plausible(scanned, len(book))
            if len(scanned) > len(entries):
                entries = scanned
        share = len(entries) / max(1, titled)  # on a contents page most lines are entries
        good = len(entries) >= 4 and (heading or (_increasing(entries) and share >= 0.45))
        if good or (heading and entries):
            found.extend(entries)
            pages.append(index)
            started = True
        elif started:
            break  # the contents ended on the previous page
    return found, pages


def running_numbers(book, samples: int = 24, progress=None) -> dict[str, int]:
    """Offsets between printed page numbers and the file's pages, from numbers printed at the top or
    bottom edge of sample pages: {'arabic': offset, 'roman': offset} for the ones that agree."""
    count = len(book)
    if count < 4:
        return {}
    picks = sorted({min(count - 1, round(count * (0.08 + 0.84 * k / max(1, samples - 1)))) for k in range(samples)})
    picks += [p for p in range(1, min(count, 14)) if p not in picks]  # front matter: roman numbers
    votes: dict[str, Counter] = {"arabic": Counter(), "roman": Counter()}
    for step, page in enumerate(picks):
        if progress:
            progress(step)
        for word in book.text_layer(page).words:
            if word[1] > 0.09 and word[3] < 0.91:
                continue  # not in a header or footer
            token = _clean_number(word[4].strip(".,[]()"))
            if NUMBER.match(token):
                votes["arabic"][page - int(token)] += 1
            elif ROMAN.match(token) and len(token) <= 6:
                votes["roman"][page - roman_value(token)] += 1
    offsets = {}
    for kind, counter in votes.items():
        if counter:
            offset, hits = counter.most_common(1)[0]
            if hits >= 3 and hits >= 0.3 * sum(counter.values()):
                offsets[kind] = offset
    return offsets


def label_map(book) -> dict[str, int]:
    """The PDF's own page labels (printed numbers), if it has any."""
    labels = {}
    for index in range(len(book)):
        label = book.page_label(index).strip().lower() if hasattr(book, "page_label") else ""
        if label and label not in labels:
            labels[label] = index
    return labels if len(labels) > len(book) // 3 else {}


def resolve(entries: list[dict], book, offsets: dict, labels: dict) -> list[dict]:
    out = []
    count = len(book)
    for entry in entries:
        printed = entry["printed"]
        page = None
        if printed.lower() in labels:
            page = labels[printed.lower()]
        elif NUMBER.match(printed) and "arabic" in offsets:
            page = int(printed) + offsets["arabic"]
        elif ROMAN.match(printed) and "roman" in offsets:
            page = roman_value(printed) + offsets["roman"]
        if page is not None and not 0 <= page < count:
            page = None
        out.append({"title": entry["title"], "printed": printed, "page": page})
    return out


def detect(book, progress=None) -> dict:
    """Everything above in one go. The result is stored with the book's notes, so it runs once."""
    entries, pages = find_contents(book, progress=progress)
    labels = label_map(book)
    offsets = {}
    if not labels or any(e["printed"].lower() not in labels for e in entries):
        offsets = running_numbers(book, progress=progress) if entries or not labels else {}
    method = "page labels" if labels else ("printed page numbers" if offsets else "")
    return {"entries": resolve(entries, book, offsets, labels), "pages": pages, "offsets": offsets,
            "method": method, "ocr": any(book.text_layer(p).ocr for p in pages[:1]), "version": 1}


def with_offset(result: dict, book, arabic_offset: int) -> dict:
    """The same contents with a hand-corrected offset ('this page is printed page N')."""
    offsets = dict(result.get("offsets") or {})
    offsets["arabic"] = arabic_offset
    raw = [{"title": e["title"], "printed": e["printed"]} for e in result.get("entries", [])]
    return dict(result, entries=resolve(raw, book, offsets, {}), offsets=offsets, method="set by hand")
