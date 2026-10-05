"""Run the real interface once, end to end, and write down what happened.

    RoughReader.exe --selftest <folder>      (or: python -m roughreader --selftest <folder>)

Opens a small generated volume, turns pages, switches modes, opens the hotkeys panel and
the library, saves screenshots, and writes <folder>/selftest.txt with every step and any
traceback. Uses throwaway settings, so nothing of the user's is touched.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import traceback
import zipfile

SAMPLE_SIZES = [(600, 850), (600, 850), (600, 850), (1200, 850), (600, 850), (600, 850)]


def make_sample(folder: str) -> str:
    """A six-page volume with one wide spread, drawn with Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    path = os.path.join(folder, "Selftest v01 (sample).cbz")
    try:
        font = ImageFont.load_default(size=220)
    except Exception:
        font = ImageFont.load_default()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (width, height) in enumerate(SAMPLE_SIZES):
            page = Image.new("L", (width, height), 232)
            draw = ImageDraw.Draw(page)
            draw.rectangle([18, 18, width - 19, height - 19], outline=40, width=6)
            draw.line([18, height - 19, width - 19, 18], fill=170, width=3)
            draw.text((width // 2, height // 2), str(index + 1), fill=30, font=font, anchor="mm")
            data = io.BytesIO()
            page.save(data, "JPEG", quality=85)
            archive.writestr(f"page_{index + 1:03d}.jpg", data.getvalue())
    return path


def make_documents(folder: str) -> list[str]:
    """A PDF, an EPUB and a text file of one short work, in folder/books (also used by the library step)."""
    import pymupdf

    from . import documents

    books = os.path.join(folder, "books")
    os.makedirs(books, exist_ok=True)
    text = "".join(f"<h2>Chapter {c}</h2>" + "".join(f"<p>Paragraph {c}.{i}. " + "The quick brown fox jumps over the lazy dog. " * 8 + "</p>"
                                                     for i in range(10)) for c in range(1, 4))
    epub = os.path.join(books, "Sample Work - Ann Example.epub")
    with open(epub, "wb") as handle:
        handle.write(documents.html_to_epub(text, title="Sample Work", author="Ann Example"))
    pdf = os.path.join(books, "Sample Work -- Ann Example -- Test Press -- 2024.pdf")
    with documents.MUPDF:
        doc = pymupdf.open()
        for number in range(3):
            page = doc.new_page(width=420, height=595)
            page.insert_textbox(pymupdf.Rect(40, 40, 380, 555), f"Page {number + 1}. " + "Words on a fixed page. " * 60, fontsize=11)
        doc.set_metadata({"title": "Sample Work", "author": "Ann Example"})
        doc.save(pdf)
        doc.close()
    txt = os.path.join(books, "Notes.txt")
    with open(txt, "w", encoding="utf-8") as handle:
        handle.write("Notes\n\n" + "\n\n".join("A paragraph of plain text. " * 12 for _ in range(30)))
    return [pdf, epub, txt]


def run(out_dir: str) -> int:
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    report = os.path.join(out_dir, "selftest.txt")
    lines: list[str] = []
    failures: list[str] = []

    def note(text: str) -> None:
        lines.append(text)
        try:  # keep the file current, so a hard crash still leaves a trail
            with open(report, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def indent(text: str) -> str:
        return "\n".join("      " + row for row in text.rstrip().splitlines())

    def step(name: str, action, optional: bool = False) -> bool:
        try:
            result = action()
        except Exception:
            if not optional:
                failures.append(name)
            note(f"{'warn ' if optional else 'FAIL '} {name}\n{indent(traceback.format_exc())}")
            return False
        note(f"ok    {name}" + (f": {result}" if result not in (None, True) else ""))
        return True

    def hook(kind, value, trace) -> None:
        failures.append("error inside a Qt callback")
        note("FAIL  error inside a Qt callback\n" + indent("".join(traceback.format_exception(kind, value, trace))))

    from . import __version__

    note(f"RoughReader {__version__} self-test")
    note(f"python {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}  {sys.executable}")
    previous_hook, sys.excepthook = sys.excepthook, hook
    try:
        _exercise(out_dir, step, note)
    except Exception:
        failures.append("self-test aborted")
        note("FAIL  self-test aborted\n" + indent(traceback.format_exc()))
    finally:
        sys.excepthook = previous_hook
    note("")
    note("RESULT: ok" if not failures else f"RESULT: FAILED ({len(failures)}): " + "; ".join(failures))
    if sys.stdout is not None:
        try:
            print("selftest ok" if not failures else f"selftest FAILED, see {report}")
        except Exception:
            pass
    return 0 if not failures else 1


def _exercise(out_dir: str, step, note) -> None:
    def need(condition, message: str = "") -> None:
        if not condition:
            raise AssertionError(message or "check failed")

    step("zlib (needed to read archives)", lambda: __import__("zlib").ZLIB_VERSION)
    step("ctypes (title-bar colour, taskbar icon)", lambda: __import__("ctypes").__name__, optional=True)
    if not step("Pillow", lambda: __import__("PIL").__version__):
        return
    work = tempfile.mkdtemp(prefix="roughreader-selftest-")
    holder: dict = {}
    if not step("build sample volume", lambda: holder.setdefault("sample", make_sample(work)) and None):
        return
    sample = holder["sample"]

    from . import qtenv

    if not step("Qt plugin setup", lambda: qtenv.prepare()["plugins"] or "no plugin folder found"):
        return

    def start():
        from PySide6.QtWidgets import QApplication

        from . import theme
        from .store import Store
        from .window import MainWindow

        app = QApplication.instance() or QApplication(sys.argv[:1])
        theme.apply(app)
        window = MainWindow(Store(os.path.join(work, "data")), os.path.join(work, "cache", "covers"),
                            os.path.join(work, "library"))
        window.resize(1180, 820)
        window.show()
        holder["app"], holder["window"] = app, window
        return f"platform={app.platformName()}"

    if not step("start Qt and build the window", start):
        return
    app, window = holder["app"], holder["window"]
    view = window.view

    def settle(seconds: float = 0.5) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)

    def wait(condition, seconds: float) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def shot(name: str, widget=None) -> None:
        settle(0.4)
        need((widget or window).grab().save(os.path.join(out_dir, name + ".png")), "screenshot not saved")

    step("screenshot: empty library", lambda: shot("1-library-empty"))

    def open_volume():
        window.open_book(sample)
        need(wait(lambda: window.book is not None, 20), "volume did not open within 20 s")
        need(len(window.book) == 6, f"expected 6 pages, got {len(window.book)}")
        need(wait(lambda: view.loader.any(0) is not None, 20), "first page was not rendered within 20 s")
        return f"{view.spread_count()} spreads, scale {view.scale:.2f}"

    if step("open volume and render first page", open_volume):
        step("screenshot: reader", lambda: shot("2-reader"))

        def turn_pages():
            view.next()
            settle(0.3)
            need(view.current_page() == 1, f"next() landed on page {view.current_page()}")
            view.prev()
            settle(0.2)
            need(view.current_page() == 0, "prev() did not return to the first page")

        step("turn pages", turn_pages)

        def two_pages():
            window._set_double(True)
            settle(0.4)
            need(view.spread_count() == 4, f"expected 4 spreads in two-page mode, got {view.spread_count()}")
            view.go_to_page(1)
            need(view.pages() == (1, 2), f"expected pages (1, 2) together, got {view.pages()}")
            need(wait(lambda: view.loader.any(2) is not None, 20), "second page of the spread was not rendered")

        step("two-page mode", two_pages)
        step("screenshot: two pages", lambda: shot("3-two-pages"))

        def directions():
            before = view.rtl
            window._set_rtl(not before)
            settle(0.2)
            window._set_rtl(before)
            settle(0.2)
            need(view.rtl == before)

        step("reading direction toggle", directions)

        def fits():
            for fit in ("width", "height", "page"):
                window._set_fit(fit)
                settle(0.2)
                need(view.fit == fit)
            view.zoom_by(1.5)
            settle(0.3)
            need(view.fit == "zoom")
            window._set_fit("page")

        step("fit modes and zoom", fits)

        def wide():
            view.go_to_page(3)
            settle(0.4)
            need(view.pages() == (3,), f"wide page should stand alone, got {view.pages()}")

        step("wide page stands alone", wide)
        step("screenshot: wide page", lambda: shot("4-wide-page"))

        def hotkeys():
            window.show_hotkeys()
            settle(0.4)
            need(window.hotkeys.isVisible(), "drop-down did not open")
            shot("5-hotkeys", window.hotkeys)
            panel = window.hotkeys.panel
            ok, taken = panel.keymap.assign("rtl", 1, "W")
            need(ok and taken == "fit_width", f"remap returned {(ok, taken)}")
            panel.changed.emit()
            keys = [k.toString() for k in window.acts["rtl"].shortcuts()]
            need(keys == ["R", "W"], f"shortcuts after remap: {keys}")
            panel.keymap.reset()
            panel.changed.emit()
            need(not panel.keymap.overrides())
            window.hotkeys.close()
            settle(0.2)

        step("hotkeys drop-down and remap", hotkeys)

    def documents_step():
        from . import documents as docs

        made = make_documents(work)
        results = []
        for path in made:
            window.open_book(path)
            need(wait(lambda: window.book is not None and os.path.normcase(window.book.path) == os.path.normcase(path)
                      and not view.held(), 30), f"{os.path.basename(path)} did not open")
            need(wait(lambda: view.loader.get(next(((i[0], *view._render_size(i[3], i[4])) for i in view._items), None)) is not None, 20),
                 f"{os.path.basename(path)}: first page not drawn")
            window._set_scroll(True)
            need(wait(lambda: not view.held(), 20), "scroll mode layout did not finish")
            view.scroll_forward()
            settle(0.3)
            window._set_scroll(False)
            need(wait(lambda: not view.held(), 20), "page mode layout did not finish")
            results.append(f"{docs.format_name(path)} {len(window.book)}p")
        shot("6-document")
        return ", ".join(results)

    step("documents: PDF, EPUB, TXT in pages and scroll mode", documents_step)

    def converters():
        import markdown

        import mammoth  # noqa: F401  (Word documents)
        html = markdown.markdown("# T\n\n*x* | y\n--|--\n1 | 2", extensions=["extra", "sane_lists"])
        need("<table>" in html and "<h1>" in html, "markdown extensions missing")
        return "Markdown, Word"

    step("converters for Markdown and Word", converters)

    def ocr_engine():
        from . import ocr

        languages = ocr.languages()
        need(languages, "the Windows OCR engine is not available (scanned pages cannot be read)")
        return ", ".join(languages)

    step("Windows OCR for scanned pages", ocr_engine, optional=True)

    def text_features():
        from PySide6.QtCore import QPoint

        epub = os.path.join(work, "books", "Sample Work - Ann Example.epub")
        window.open_book(epub)
        need(wait(lambda: window.book is not None and window.book.path.endswith(".epub") and not view.held(), 30), "EPUB did not open")
        page = view.current_page()
        layer = window.book.text_layer(page)
        need(len(layer.words) > 30, f"text layer has {len(layer.words)} words")
        view._sel = ((page, 3), (page, 20))
        view.selectionChanged.emit(QPoint(300, 300))
        settle(0.2)
        need(window.selection_bar.isVisible(), "selection bar did not appear")
        text = view.selected_text()
        window.copy_selection()
        need(app.clipboard().text() == text and text, "copy did not reach the clipboard")
        view._sel = ((page, 25), (page, 40))
        window.highlight_selection("blue")
        window.toggle_bookmark()
        notes = window.book.notes
        need(len(notes.highlights) == 1 and len(notes.bookmarks) == 1 and os.path.exists(notes.path), "notes not saved")
        window.show_notes(window.btn_contents)
        settle(0.4)
        need(window.notes_panel.isVisible(), "contents panel did not open")
        shot("6b-contents", window.notes_panel)
        window.notes_panel.close()
        shot("6c-highlight")
        return f"{len(layer.words)} words on the page, highlight and bookmark saved"

    step("text: selection, copy, highlight, bookmark, contents", text_features)

    def library():
        window.add_to_library([os.path.join(work, "books"), sample])
        need(wait(lambda: not window._importing, 60), "import did not finish")
        need(wait(lambda: window.library.model.rowCount() >= 2, 15), "library shows no authors")
        settle(1.5)  # covers
        authors = window.library.model.rowCount()
        need(any(len(a.works[0].variants) == 2 for a in window.library.authors if a.name == "Ann Example"),
             "PDF and EPUB of one work were not grouped as variants")
        return f"{authors} authors / series"

    step("library", library)

    def analysis():
        from . import analyze

        window.analyse_library()
        need(wait(lambda: not window._analysing, 60), "reading title pages did not finish")
        names = sorted(os.path.basename(v.path) for a in window.library.authors for w in a.works for v in w.variants)
        need(any(n.startswith("Sample Work, Example, Test Press, 2024") for n in names), f"not renamed: {names}")
        window.library.set_view("list")
        settle(0.8)
        need(window.library.list.isVisible() and window.library.list_proxy.rowCount() >= 2, "list view is empty")
        shot("7a-library-list")
        window.library.set_view("gallery")
        from .detailsdialog import DetailsDialog

        path = next(v.path for a in window.library.authors for w in a.works for v in w.variants if v.format == "PDF")
        from . import organize

        saved = organize.Index(window.library_root).entry(path)
        dialog = DetailsDialog(path, analyze.Details.from_dict(saved["details"]), saved.get("source", ""), window)
        dialog.show()
        settle(0.3)
        shot("7b-details", dialog)
        dialog.close()
        return f"{len(names)} files named"

    step("library: reading title pages, renaming, list view, details", analysis)
    step("screenshot: library", lambda: shot("6-library"))

    def appearance():
        from . import theme

        names = theme.scheme_names()
        need(len(names) == 6 and names[2] == "Feldgrau", f"schemes: {names}")
        for name in names:
            window.set_scheme(name)
            settle(0.15)
            need(theme.SCHEME == name and window.store.get("scheme") == name, f"scheme {name} not applied")
        window.set_borders(True)
        settle(0.2)
        shot("9-borders-library")
        window.set_borders(False)
        window.set_scheme("Feldgrau")
        settle(0.2)
        return f"{len(names)} schemes, borders on and off"

    step("appearance: schemes and borders", appearance)

    def shrink_dialog():
        from .archive import Book
        from .shrinkdialog import ShrinkDialog

        dialog = ShrinkDialog(sample, window.store, window)
        dialog.show()
        dialog.height_box.setValue(500)
        settle(1.5)  # estimate
        need("ABOUT" in dialog.estimate.text(), f"no estimate: {dialog.estimate.text()!r}")
        shot("7-shrink", dialog)
        target = os.path.join(work, "shrunk", "copy.cbz")
        dialog.output.setText(target)
        dialog.go_button.click()
        need(wait(lambda: dialog.result_path, 30), f"shrink did not finish: {dialog.status.text()!r}")
        settle(0.3)
        shot("8-shrink-done", dialog)
        book = Book(target)
        try:
            need(len(book) == 6, f"copy has {len(book)} pages")
        finally:
            book.close()
        dialog.close()
        return dialog.estimate.text()

    step("shrink dialog and copy", shrink_dialog)

    def close():
        window.close()
        settle(0.3)
        need(os.path.exists(os.path.join(work, "data", "state.json")), "settings were not saved")

    step("close and save settings", close)
