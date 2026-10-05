# RoughReader

A (slightly janky) all-in-one offline reader and library manager for Windows: comics, books, academic papers and other writing, kept in one
organised folder beside the program. Everything runs on your own PC, including text recognition for scanned pages for indexing and in-text links.

## Download

From the [Releases](../../releases) page:

| Download | What it is |
|---|---|
| **RoughReader-portable** | One file, `RoughReader.exe` (~54 MB). Put it in any folder and run it. Starts in about two seconds (it unpacks itself each time). |
| **RoughReader-folder** | The full build: `RoughReader.exe` with its `_internal` folder (~120 MB unpacked). Open the file in a folder of your choice to keep things organized. |

Both are the same program. Neither installs anything: on first run they create, next to the exe,
`RoughReader-Library\` (your books) and `RoughReader-data\` (settings, progress, highlights), so the whole
folder can be moved or copied as a unit. The only book that comes with it is the **RoughReader Manual**,
which opens in RoughReader itself and explains everything in detail.

Windows 10 or 11, 64-bit.

## What it does

- **Reads** CBZ comics; PDF and XPS; EPUB, MOBI, AZW, AZW3, PRC, FB2, TXT, Markdown, HTML and Word (DOCX).
  Text books are laid out into pages for your window, font and text size, and keep your place when any
  of those change.
- **Page by page or scrolling**: single or two pages, right to left for manga, wide scans on their own,
  or one continuous column.
- **Text**: select and copy, highlight in four colours, bookmark pages, follow footnote and cross-reference
  links (and come back). Scanned PDFs get selectable text through Windows' own OCR.
- **Contents**: the book's own, or, for PDFs without linked contents, the printed contents page found and
  made clickable, with page numbers mapped to the file's pages.
- **Library**: copies books in (never touching the originals), sorted into Author \ Work \ versions, so a
  PDF and an EPUB of the same book, or two editions, are one entry. Each book's title and copyright pages
  are read once to find publisher, year, edition, ISBN and translator, and the copy is named
  `Title, Surname, Publisher, Year`. Everything can be checked and corrected in a Details dialog.
  Shown as covers or as a sortable list.
- **Shrinks** comics for your screen (720p to 4K, WebP or AVIF), typically 400 MB to 15-40 MB.
- Six colour schemes from near-black to white; remappable keys.

## Building from source

Needs a standard Python 3.10+ for Windows (not Anaconda).

    build.bat                         single-file exe in ..\RoughRider-Portable, then a self-test
    python tools\build_exe.py         folder build in dist\RoughReader
    run.bat                           run from source
    python tests\test_core.py         tests
    python -m roughreader --selftest selftest   end-to-end check with screenshots

`setup_env.bat` (used by the .bat files) makes a private `.venv` from a Python already on the PC and
installs `requirements.txt`: PySide6, Pillow, PyMuPDF, mammoth, Markdown and the Windows OCR bindings.

## Licence

GNU Affero General Public License v3 (AGPL-3.0); see `LICENSE`. RoughReader includes MuPDF (via PyMuPDF),
which is AGPL-licensed.
