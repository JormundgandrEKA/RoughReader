---
title: RoughReader Manual
author: RoughReader
---

# RoughReader Manual

RoughReader is an offline reader and library for Windows: comics, books, papers and other writing,
kept in one organised folder beside the program. Nothing is sent anywhere; everything, including text
recognition for scanned pages, runs on your own PC.

This manual is the one book RoughReader comes with. You are reading it in RoughReader, so everything
described below can be tried right here. Delete it from the library whenever you like; it is not put
back.

## 1. The three things beside the program

When `RoughReader.exe` runs for the first time it creates, next to itself:

| Folder | What is in it |
|---|---|
| `RoughReader-Library\` | the books: copies, organised as Author \ Work \ versions |
| `RoughReader-data\` | settings, reading progress, highlights, bookmarks, caches, error log |

Keep the exe and both folders together and you can move or copy the whole thing (to another drive, a
USB stick, another PC) and carry on where you left off. If the program's folder cannot be written to,
it falls back to `%APPDATA%\RoughReader` and `%LOCALAPPDATA%\RoughReader`.

The exe is a single file. It unpacks itself to a temporary folder on every start (about a second and a
half) and removes it on exit.

## 2. Adding books

- **Add** (top right of the library) copies a folder or some files into the library. Originals are only
  read, never moved, renamed or changed. A file that is already in the library (byte for byte) is not
  copied twice.
- Folders you add are remembered as **sources**: whenever RoughReader starts, anything new in them is
  copied in quietly.
- **Open** reads a single file without adding it.
- Dropping a folder on the window adds it; dropping a file opens it.
- Ctrl+Shift+O adds a folder; Ctrl+O opens a file.

### How books are organised

Each book goes to `Author \ Work \ file`. Comics without an author go under their series
(`Der Werwolf \ Der Werwolf v01`). Two files of the same work (a PDF and an EPUB, two editions, a shrunk
copy of a comic) become **versions** of one work instead of two entries.

The author and title come from the file's own metadata and its name. Names in the style used by
Anna's Archive (`Title -- Author -- Publisher -- ISBN -- ... .pdf`), `Author - Title`, `Title - Author`,
`Title by Author` and `(Publisher, year)` are all understood; a bare surname such as `Jünger - Copse 125`
is matched to a full author found among your other books.

### Reading the title pages, and naming

Once, in the background, every new book is read: its title page and copyright page (the page with the
ISBN and "All rights reserved"). Scanned pages are read with the text recognition (OCR) built into
Windows. This gives the publisher, the year of this edition, the year of first publication, the
edition, the ISBN (its check digit is verified) and any translator.

The library copy is then renamed:

    Title, Surname, Publisher, Year.ext

for example `Storm of Steel, Jünger, Penguin Books, 2016.pdf`. Parts that are unknown are left out.
The status line at the top of the library shows the progress ("Reading title pages · 5 of 33").

### Details: checking and correcting

Right-click a book (or a page while reading) and choose **Details**. It shows everything found, where
each value came from ("copyright page", "copyright page (read by OCR)", "original file name",
"metadata"), and warns about anything missing. Change what is wrong and **Save**: the file is renamed
and moved to the right author and work. Values you save are marked as yours and are never overwritten
by a later automatic reading. **Read the book again** repeats the reading without changing anything
until you save.

You can also rename and move files and folders in Explorer; the library follows the folders.

## 3. The library screen

Two views, switched with the list button left of **Add**:

- **Covers**: authors and series as stacks of covers; click one for its works, a work for its versions
  (a work with one version opens directly). The trail at the top (LIBRARY › AUTHOR › WORK) and the
  Backspace key go back up.
- **List**: a tree of author, title and version. Authors are open and works closed at first; the arrow
  at the start of a row opens or closes it. Columns: format, publisher, year, reading progress, size.
  Click a column heading to sort by it, again to reverse.

**Search** finds titles and authors anywhere in the library. Right-click for Open, Details, Make a
smaller copy (comics), and Show in Explorer.

## 4. Reading

| Kind | Formats | Pages |
|---|---|---|
| Comics | CBZ | the images in the archive |
| Fixed pages | PDF, XPS, OXPS | the file's own pages, drawn sharp at any zoom |
| Text | EPUB, MOBI, AZW, AZW3, PRC, FB2, FBZ, TXT, Markdown, HTML, DOCX | laid out into pages for your window, font and text size |

- **Turning pages**: Left and Right (they follow the reading direction), Space and PgDn / PgUp, the mouse
  wheel, or a click on the left or right third of the page.
- **Two pages** (D) shows a spread; wide scans always stand alone. **Right to left** (R) is for manga.
  **Cover on its own** (C) shifts the pairing by one.
- **Scroll mode** (S) shows every page in one continuous column, loaded and released as you scroll. In
  text books the pages join up seamlessly.
- **Fit** page, width or height (B, W, H); **actual size** (1); zoom with + and - or Ctrl and the wheel;
  drag to pan.
- **Fullscreen** with F or F11, or a double-click in the middle; Esc leaves. Tab hides the toolbars.
- RoughReader remembers your place in every book, even across a change of text size or window, and at
  the end of a comic volume offers the next one.

### Text settings

In text books the **Aa** button sets the text size (also + and -), the font (serif, sans serif or the
book's own), line spacing, justified lines, and the page colour: paper, sepia, night, or the colour
scheme's own. Any change lays the book out again and keeps your place.

## 5. Selecting, highlighting, bookmarks, links, contents

- **Select text** by dragging over it; a double-click selects one word. A small bar offers **Copy** and
  four highlight colours. Ctrl+C copies, Ctrl+H highlights. On a scanned page the text is recognised
  (OCR) the first time you select there.
- **Highlights** are listed under Contents › Highlights; right-click one on the page to copy or remove it.
- **Bookmarks**: Ctrl+B bookmarks the page you are on (a burgundy ribbon marks it) or removes the mark.
- **Links** (footnotes, endnotes, cross-references) are followed with a click. Where you land is marked
  for a moment; **Alt+Left** or the mouse's back button returns you.
- **Contents** (T, or the list button in the toolbar) lists the book's contents, bookmarks and
  highlights; click an entry to go there.

### Books without linked contents

Many PDFs (scans of older books, plain exports) have a printed contents page but no clickable contents.
When such a book is first opened, RoughReader looks for the printed contents page, pairs each title with
its page number (by OCR if it is a scan) and works out which page of the file that is, from the PDF's own
page labels or from the page numbers printed at the top or bottom of the pages. The result appears under
Contents. If the page numbers come out shifted, go to any page and enter which printed page it is
("This page is printed page …") and every entry is corrected.

## 6. Making a comic smaller

Right-click a comic and choose **Make a smaller copy**. Pick the screen you read on (720p to 4K) and
WebP or AVIF; the likely size is shown before you start. The copy is written beside the original as
another version of the same volume (`Der Werwolf v01 - 1080p.cbz`); the original is never touched. A
400 MB volume typically becomes 15 to 40 MB.

## 7. Appearance

The Appearance button (a square with a diagonal) offers six colour schemes from extremely dark to bright
white (Obsidian, Graphite, Feldgrau, Steel, Sand, Paper) and an option for borders around buttons,
covers and other items. Hotkeys (K or F1) lists every key; click a key cap to change it, right-click to
remove it.

## 8. Keys

| Action | Keys |
|---|---|
| Turn page | Left / Right, click a side third |
| Next / previous screen | Space, PgDn / Shift+Space, PgUp, Backspace |
| First / last page, go to page | Home / End, G |
| Two pages, right to left, cover alone | D, R, C |
| Scroll mode | S |
| Fit page / width / height, actual size | B / W / H, 1 |
| Zoom (text size in text books) | + / -, Ctrl+wheel |
| Contents, bookmark | T, Ctrl+B |
| Copy, highlight | Ctrl+C, Ctrl+H |
| Back from a link | Alt+Left, mouse back button |
| Fullscreen, toolbars | F or F11 (Esc leaves), Tab |
| Library, hotkeys | L, K or F1 |
| Open file, add a folder | Ctrl+O, Ctrl+Shift+O |
| Quit | Ctrl+Q |

## 9. Technical notes

**Files RoughReader writes**

| File | Contents |
|---|---|
| `RoughReader-data\state.json` | settings and reading progress (by file path; moved along when RoughReader renames a file) |
| `RoughReader-data\annotations\<print>.json` | highlights, bookmarks and found contents of one book, named after the file's fingerprint (its size plus its first and last megabyte), so they survive renames and moves |
| `RoughReader-data\cache\` | cover thumbnails and page sizes; safe to delete |
| `RoughReader-data\error.log` | unexpected errors, if any |
| `RoughReader-Library\.roughreader.json` | per library file: the details found or set, where it was copied from, its fingerprint |

**Positions** in text books are stored as (chapter, word number) rather than page numbers, so a highlight
or bookmark stays on the same words whatever the window, font or text size. In PDFs they are (page, word).

**Text recognition** uses the OCR engine that comes with Windows 10 and 11, through the languages
installed in Windows (Settings › Time & language › Language). Without it, scanned pages simply have no
selectable text.

**Engines**: page images are drawn with Pillow (Lanczos scaling); PDF, XPS, EPUB, MOBI, FB2, HTML and text
are drawn and laid out by MuPDF (PyMuPDF); Word files are converted with mammoth, Markdown with
Python-Markdown; the interface is Qt (PySide6).

**Not supported**: books protected by DRM (Kindle and others) are refused with a message; DjVu, CBR,
CB7, DOC, ODT and RTF cannot be opened.

**Command line**

    RoughReader.exe [book or folder]     open a book, or add a folder
    RoughReader.exe --selftest <folder>  run the interface once on generated samples; report and screenshots
    RoughReader.exe --diagnose           write a report about the environment (diagnose-exe.txt)

**Building from source**: the source is on GitHub. With a standard Python 3.10 or newer on Windows,
`build.bat` makes the single-file exe and self-tests it; `run.bat` runs from source;
`python tests/test_core.py` runs the tests.

**Licence**: RoughReader is free software under the GNU Affero General Public License v3 (AGPL-3.0), as
required by MuPDF, which it includes.
