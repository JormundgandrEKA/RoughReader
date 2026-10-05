"""Main window: library and reader, toolbars, shortcuts, saved state."""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QByteArray, QPointF, Qt, QTimer, Signal
from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, analyze, annotations, archive, contents, documents, keymap, organize, qtenv, theme
from .archive import BookError
from .hotkeys import HotkeyPopup, native, normalise
from .library import LibraryPage
from .view import PageView
from .notespanel import NotesPanel
from .widgets import IconButton, SelectionBar, SeekBar

FIT_LABELS = {"page": "Fit page", "width": "Fit width", "height": "Fit height", "zoom": "Zoom"}
TEXT_DEFAULTS = {"size": 19, "family": "serif", "spacing": "normal", "justify": True, "tone": "paper"}
TONE_COLOURS = {"paper": ("#FFFFFF", "#000000"), "sepia": ("#F4ECD8", "#3B2F23"), "night": ("#1B1C1E", "#C9CCCE")}
TEXT_SIZES = (10, 48)


def _rule() -> QWidget:
    line = QWidget()
    line.setFixedHeight(theme.RULE)
    line.setObjectName("rule")
    line.setAttribute(Qt.WA_StyledBackground, True)
    return line


class MainWindow(QMainWindow):
    _relaid = Signal(int, object, int, str)
    _contents_found = Signal(object, dict)
    _analyse_progress = Signal(int, int, str)
    _analyse_done = Signal(list)
    _import_progress = Signal(int, int, str)
    _import_done = Signal(int, int, list, bool, list)   # copied, already there, errors, quiet, (source, copy) pairs
    _opened = Signal(int, object, str, str)  # ticket, Book or None, error, path

    def __init__(self, store, cache_dir: str, library_root: str = ""):
        super().__init__()
        self.store = store
        self._sizes_dir = os.path.join(os.path.dirname(cache_dir), "pages")
        self._open_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="roughreader-open")
        self._open_ticket = 0
        self._opened.connect(self._on_opened)
        self.book: Book | None = None
        self.keymap = keymap.Keymap(store.get("keys"), normalise)
        self._next_armed = 0.0
        self._layout_ticket = 0
        self._layout_key = None
        self._relayout_timer = QTimer(self, singleShot=True, interval=300, timeout=self._relayout_text)
        self._relaid.connect(self._on_relaid)
        self._tinted = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(theme.app_icon())
        self.setAcceptDrops(True)
        self.resize(1180, 860)

        self.library = LibraryPage(store, cache_dir)
        self.library.openRequested.connect(self.open_book)
        self.library.shrinkRequested.connect(self.show_shrink)
        self.library.addFolderRequested.connect(self.choose_folder)
        self.library.addFilesRequested.connect(self.choose_files_to_add)
        self.library.openFileRequested.connect(self.choose_file)
        self.library.stopImportRequested.connect(self._stop_import)
        self.library.detailsRequested.connect(self.show_details)
        self.library.hotkeysRequested.connect(lambda: self.show_hotkeys(self.library.keys_button))
        self.library.appearanceRequested.connect(lambda: self.show_appearance(self.library.appearance_button))

        self.view = PageView()
        self.view.double = bool(store.get("double"))
        self.view.rtl = bool(store.get("rtl"))
        self.view.cover_alone = bool(store.get("cover_alone"))
        self.view.fit = store.get("fit") if store.get("fit") in ("page", "width", "height") else "page"
        self.view.changed.connect(self._on_view_changed)
        self.view.endReached.connect(self._on_end)
        self.view.fullscreenRequested.connect(self.toggle_fullscreen)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.resized.connect(self._relayout_timer.start)
        self.view.linkActivated.connect(self._on_link)
        self.view.backRequested.connect(self.link_back)
        self.view.selectionChanged.connect(self._on_selection)
        self.selection_bar = SelectionBar(annotations.COLOURS, self.view.viewport())
        self.selection_bar.copyRequested.connect(self.copy_selection)
        self.selection_bar.highlightRequested.connect(self.highlight_selection)
        self.notes_panel = NotesPanel(self)
        self.notes_panel.goTo.connect(self._go_to_target)
        self.notes_panel.removeBookmark.connect(self._remove_bookmark)
        self.notes_panel.removeHighlight.connect(self._remove_highlight)
        self.notes_panel.setPrinted.connect(self._set_printed)
        self.notes_panel.findContents.connect(self._find_contents)
        self._contents_found.connect(self._on_contents_found)
        self._history: list[int] = []
        self._searching_contents = None

        self._build_actions()
        self.reader = self._build_reader()
        self.hotkeys = HotkeyPopup(self.keymap, self)
        self.hotkeys.panel.changed.connect(self._on_keys_changed)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.library)
        self.stack.addWidget(self.reader)
        self.setCentralWidget(self.stack)

        self._save_timer = QTimer(self, singleShot=True, interval=1200, timeout=self.store.save)

        geometry = store.get("geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        # The library is a folder of copies beside the app; the folders it was given are its sources.
        self.library_root = os.path.abspath(library_root or qtenv.library_dir())
        old = store.get("library")
        if old:  # the folder the earlier versions showed becomes the first source
            sources = list(store.get("sources") or [])
            if old not in sources and os.path.isdir(old):
                sources.append(old)
            store.set("sources", sources)
            store.set("library", "")
        self._import_cancel = threading.Event()
        self._importing = False
        self._import_queue: list[tuple[list[str], bool, bool]] = []
        self._import_progress.connect(self._on_import_progress)
        self._analyse_progress.connect(self._on_analyse_progress)
        self._analyse_done.connect(self._on_analyse_done)
        self._analysing = False
        self._import_done.connect(self._on_import_done)
        self.library.set_root(self.library_root)
        self._apply_keymap()

    # ------------------------------------------------------------ building

    def _build_actions(self) -> None:
        """One QAction per command in the keymap; the keymap supplies the shortcuts."""
        view = self.view
        slots = {
            "page_left": (lambda: view.go_visual(-1), False),
            "page_right": (lambda: view.go_visual(+1), False),
            "forward": (lambda: view.scroll_forward(), False),
            "back": (lambda: view.scroll_back(), False),
            "line_down": (lambda: view.nudge(+1), False),
            "line_up": (lambda: view.nudge(-1), False),
            "first": (lambda: view.go_to_spread(0), False),
            "last": (lambda: view.go_to_last(), False),
            "goto": (lambda: self._go_to_page(), False),
            "double": (self._set_double, True),
            "rtl": (self._set_rtl, True),
            "cover": (self._set_cover_alone, True),
            "scroll": (self._set_scroll, True),
            "contents": (lambda: self.show_notes(), False),
            "bookmark": (lambda: self.toggle_bookmark(), False),
            "copy": (lambda: self.copy_selection(), False),
            "highlight": (lambda: self.highlight_selection(), False),
            "link_back": (lambda: self.link_back(), False),
            "fit_page": (lambda: self._set_fit("page"), True),
            "fit_width": (lambda: self._set_fit("width"), True),
            "fit_height": (lambda: self._set_fit("height"), True),
            "actual": (lambda: self._actual_size(), False),
            "zoom_in": (lambda: self._zoom(+1), False),
            "zoom_out": (lambda: self._zoom(-1), False),
            "fullscreen": (lambda: self.toggle_fullscreen(), True),
            "leave_fullscreen": (lambda: self._escape(), False),
            "bars": (self._set_bars, True),
            "library": (lambda: self.show_library(), False),
            "open": (lambda: self.choose_file(), False),
            "folder": (lambda: self.choose_folder(), False),
            "hotkeys": (lambda: self.show_hotkeys(), False),
            "quit": (lambda: self.close(), False),
        }
        texts = {
            "open": "Open file…", "folder": "Add a folder to the library…", "goto": "Go to page…",
            "hotkeys": "Hotkeys…", "rtl": "Right to left (manga)", "actual": "Actual size (100%)",
            "scroll": "Scroll mode (one continuous column)",
        }
        self.acts: dict[str, QAction] = {}
        self._reader_actions: list[QAction] = []
        for command in keymap.COMMANDS:
            slot, checkable = slots[command.id]
            action = QAction(texts.get(command.id, command.label), self)
            action.setCheckable(checkable)
            action.triggered.connect(slot)
            if command.scope == "reader":
                action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                self._reader_actions.append(action)
            else:
                self.addAction(action)
            self.acts[command.id] = action

        acts = self.acts
        self.act_open, self.act_folder, self.act_full = acts["open"], acts["folder"], acts["fullscreen"]
        self.act_library, self.act_goto, self.act_bars = acts["library"], acts["goto"], acts["bars"]
        self.act_double, self.act_rtl, self.act_cover = acts["double"], acts["rtl"], acts["cover"]
        self.act_scroll = acts["scroll"]
        self.act_actual, self.act_zoom_in, self.act_zoom_out = acts["actual"], acts["zoom_in"], acts["zoom_out"]
        self.act_fit = {"page": acts["fit_page"], "width": acts["fit_width"], "height": acts["fit_height"]}
        group = QActionGroup(self)
        group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        for action in self.act_fit.values():
            group.addAction(action)
        self.act_bars.setChecked(True)

    def _tip(self, cid: str, text: str = "") -> str:
        """Tooltip text with the command's current first hotkey."""
        keys = self.keymap.keys(cid)
        label = text or keymap.BY_ID[cid].label
        return f"{label}  ({native(keys[0])})" if keys else label

    def _apply_keymap(self) -> None:
        """Push the keymap's bindings onto the actions and refresh every tooltip."""
        for cid, action in self.acts.items():
            action.setShortcuts([QKeySequence(key) for key in self.keymap.keys(cid)])
        tip = self._tip
        self.btn_library.setToolTip(tip("library"))
        self.btn_double.setToolTip(tip("double"))
        self.btn_rtl.setToolTip(tip("rtl"))
        self.btn_scroll.setToolTip(tip("scroll"))
        for fit, button in self.btn_fit.items():
            button.setToolTip(tip(f"fit_{fit}"))
        self.btn_full.setToolTip(tip("fullscreen"))
        self.btn_keys.setToolTip(tip("hotkeys"))
        self.page_label.setToolTip(tip("goto"))
        self.library.folder_button.setToolTip(tip("folder"))
        self.library.file_button.setToolTip(tip("open", "Open a single file"))
        self.library.keys_button.setToolTip(tip("hotkeys"))
        self._sync_controls()

    def show_hotkeys(self, anchor=None) -> None:
        """Toggle the hotkeys drop-down under its button (or centred if no button is showing)."""
        if self.hotkeys.isVisible():
            self.hotkeys.close()
            return
        if time.monotonic() - self.hotkeys.closed_at < 0.25:
            return  # this click is the one that just closed it
        if not isinstance(anchor, QWidget) or not anchor.isVisible():
            anchor = next((b for b in (self.btn_keys, self.library.keys_button) if b.isVisible()), None)
        self.hotkeys.open(anchor, self)

    def _on_keys_changed(self) -> None:
        self.store.set("keys", self.keymap.overrides())
        self._save_timer.start()
        self._apply_keymap()

    def _build_reader(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page")
        page.addActions(self._reader_actions)

        self.btn_library = IconButton("library", "Library", "Library")
        self.btn_library.clicked.connect(self.show_library)
        self.title = QLabel("")
        self.title.setObjectName("title")
        self.title.setFont(theme.font(10.5, bold=True, spacing=1.5))
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.btn_double = IconButton("double", "Two pages", checkable=True)
        self.btn_rtl = IconButton("rtl", "Right to left", checkable=True)
        self.btn_scroll = IconButton("scroll", "Scroll mode", checkable=True)
        self.btn_contents = IconButton("contents", "Contents, bookmarks and highlights")
        self.btn_contents.clicked.connect(lambda: self.show_notes(self.btn_contents))
        self.btn_text = IconButton("text", "Text size, font and page colour")
        self.btn_text.clicked.connect(lambda: self.show_text_menu(self.btn_text))
        self.btn_fit = {
            "page": IconButton("fit_page", "Fit page", checkable=True),
            "width": IconButton("fit_width", "Fit width", checkable=True),
            "height": IconButton("fit_height", "Fit height", checkable=True),
        }
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("dim")
        self.zoom_label.setFont(theme.font(9, bold=True, spacing=1))
        self.zoom_label.setFixedWidth(theme.u(52))
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.btn_look = IconButton("scheme", "Colours and borders")
        self.btn_look.clicked.connect(lambda: self.show_appearance(self.btn_look))
        self.btn_keys = IconButton("keys", "Hotkeys")
        self.btn_keys.clicked.connect(lambda: self.show_hotkeys(self.btn_keys))
        self.btn_full = IconButton("fullscreen", "Fullscreen")
        self.btn_double.clicked.connect(self._set_double)
        self.btn_rtl.clicked.connect(self._set_rtl)
        self.btn_scroll.clicked.connect(self._set_scroll)
        for fit, button in self.btn_fit.items():
            button.clicked.connect(lambda _=False, f=fit: self._set_fit(f))
        self.btn_full.clicked.connect(self.toggle_fullscreen)

        top = QHBoxLayout()
        top.setContentsMargins(10, 7, 10, 7)
        top.setSpacing(4)
        top.addWidget(self.btn_library)
        top.addSpacing(10)
        top.addWidget(self.title, 1)
        top.addSpacing(10)
        top.addWidget(self.btn_double)
        top.addWidget(self.btn_rtl)
        top.addWidget(self.btn_scroll)
        top.addSpacing(14)
        for button in self.btn_fit.values():
            top.addWidget(button)
        top.addWidget(self.zoom_label)
        top.addSpacing(14)
        top.addWidget(self.btn_contents)
        top.addWidget(self.btn_text)
        top.addWidget(self.btn_look)
        top.addWidget(self.btn_keys)
        top.addWidget(self.btn_full)
        self.top_bar = QWidget()
        self.top_bar.setLayout(top)

        self.btn_left = IconButton("left", "Turn page")
        self.btn_right = IconButton("right", "Turn page")
        self.btn_left.clicked.connect(lambda: self.view.go_visual(-1))
        self.btn_right.clicked.connect(lambda: self.view.go_visual(+1))
        self.seek = SeekBar()
        self.seek.moved.connect(self.view.go_to_spread)
        self.page_label = QLabel("")
        self.page_label.setFont(theme.font(10, bold=True, spacing=1))
        self.page_label.setMinimumWidth(theme.u(118))
        self.page_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 5, 16, 5)
        bottom.setSpacing(6)
        bottom.addWidget(self.btn_left)
        bottom.addWidget(self.seek, 1)
        bottom.addWidget(self.btn_right)
        bottom.addWidget(self.page_label)
        self.bottom_bar = QWidget()
        self.bottom_bar.setLayout(bottom)

        self.top_rule, self.bottom_rule = _rule(), _rule()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(self.top_rule)
        layout.addWidget(self.view, 1)
        layout.addWidget(self.bottom_rule)
        layout.addWidget(self.bottom_bar)
        return page

    # ------------------------------------------------------------- opening

    def open_path(self, path: str) -> None:
        """A folder is added to the library; a file is opened."""
        if os.path.isdir(path):
            self.add_to_library([path], remember=True)
        elif os.path.isfile(path):
            self.open_book(path)

    def show_appearance(self, anchor) -> None:
        from .appearance import show_menu

        show_menu(anchor, self.set_scheme, self.set_borders)

    def set_scheme(self, name: str) -> None:
        self.store.set("scheme", theme.set_scheme(name))
        self.refresh_appearance()

    def set_borders(self, on: bool) -> None:
        theme.set_borders(on)
        self.store.set("borders", bool(on))
        self.refresh_appearance()

    def refresh_appearance(self) -> None:
        """Repaint everything after a colour or border change."""
        app = QApplication.instance()
        theme.apply(app)
        _tint_title_bar(self)
        for widget in app.allWidgets():
            widget.update()
        self.store.save()

    def show_shrink(self, path: str) -> None:
        """Dialog for writing a smaller copy of a volume; the original is never changed."""
        from .shrinkdialog import ShrinkDialog

        try:  # the copy goes in the library, beside the other versions of the same work
            folder = organize.destination_folder(path, self.library_root)
        except Exception:
            folder = ""
        dialog = ShrinkDialog(path, self.store, self, default_folder=folder)
        dialog.openRequested.connect(self.open_path)
        dialog.exec()
        self.store.save()
        if dialog.result_path:
            self.library.rescan()  # let the new copy appear

    def choose_file(self) -> None:
        start = os.path.dirname(self.book.path) if self.book else self.library_root
        path, _ = QFileDialog.getOpenFileName(self, "Open a book", start, _file_filter(documents.OPENABLE_EXT))
        if path:
            self.open_book(path)

    def choose_files_to_add(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add books to the library", "", _file_filter(documents.BOOK_EXT))
        if paths:
            self.add_to_library(paths)

    def choose_folder(self) -> None:
        sources = self.store.get("sources") or []
        folder = QFileDialog.getExistingDirectory(self, "Add a folder to the library", sources[-1] if sources else "")
        if folder:
            self.add_to_library([folder], remember=True)

    # ------------------------------------------------------------ library

    def add_to_library(self, paths: list[str], remember: bool = False, quiet: bool = False) -> None:
        """Copy books into the library (in the background). Folders are remembered as sources, so books
        added to them later are picked up the next time the app starts."""
        paths = [os.path.abspath(p) for p in paths]
        if remember:
            sources = list(self.store.get("sources") or [])
            for path in paths:
                if os.path.isdir(path) and path not in sources and not organize.inside(path, self.library_root):
                    sources.append(path)
            self.store.set("sources", sources)
            self._save_timer.start()
        if self._importing:
            self._import_queue.append((paths, quiet, True))
            return
        self._importing = True
        self._import_cancel.clear()
        if not quiet:
            self.show_library()
            self.library.show_import(0, 0, "Looking through the files…")
        threading.Thread(target=self._import_worker, args=(paths, quiet), daemon=True).start()

    def sync_sources(self) -> None:
        """Copy in whatever is new in the source folders (quietly; shown only if something arrives)."""
        sources = [s for s in (self.store.get("sources") or []) if os.path.isdir(s)]
        if sources:
            self.add_to_library(sources, quiet=True)

    def _import_worker(self, paths: list[str], quiet: bool) -> None:
        copied, duplicates, errors, pairs = 0, 0, [], []
        try:
            index = organize.Index(self.library_root)
            plan = organize.plan_import(paths, self.library_root, index, only_new=quiet)
            duplicates = len(plan.duplicates)
            if plan.copies:
                copied, errors = organize.run_import(
                    plan, index, progress=lambda d, t, title: self._emit(self._import_progress, d, t, title),
                    cancel=self._import_cancel)
                pairs = plan.copied
            else:
                index.save()
        except Exception as exc:  # noqa: BLE001 - shown to the user
            errors.append(str(exc))
        self._emit(self._import_done, copied, duplicates, errors, quiet, pairs)

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass  # window closed meanwhile

    def _on_import_progress(self, done: int, total: int, title: str) -> None:
        self.library.show_import(done - 1, total, f"Adding {done} of {total}  ·  {title}")

    def _on_import_done(self, copied: int, duplicates: int, errors: list, quiet: bool, pairs: list) -> None:
        self._importing = False
        for source, copy in pairs:  # reading progress follows a book into the library
            self.store.adopt_progress(source, copy)
        if pairs:
            self._save_timer.start()
        if copied or errors or not quiet:
            parts = [f"Added {copied} book{'s' if copied != 1 else ''}"]
            if duplicates:
                parts.append(f"{duplicates} already in the library")
            if errors:
                parts.append(f"{len(errors)} could not be copied")
            self.library.end_import("  ·  ".join(parts))
            QTimer.singleShot(8000, lambda: self.library.end_import(""))
            self.library.rescan()
        else:
            self.library.end_import("")
        if errors:
            QMessageBox.warning(self, APP_NAME, "Some books could not be added:\n\n" + "\n".join(errors[:12]))
        if self._import_queue:
            paths, quiet, _remember = self._import_queue.pop(0)
            self.add_to_library(paths, quiet=quiet)
        else:
            QTimer.singleShot(300, self.analyse_library)

    # ------------------------------------------------------------ reading title pages, renaming

    def analyse_library(self) -> None:
        """Read the title and copyright pages of every book not read yet, and rename it to match
        (once per file, in the background; the book being read is left until it is closed)."""
        if self._analysing or self._importing:
            return
        try:
            paths = analyze.pending(self.library_root, organize.Index(self.library_root))
        except OSError:
            return
        if self.book is not None:
            paths = [p for p in paths if os.path.normcase(p) != os.path.normcase(self.book.path)]
        if not paths:
            return
        self._analysing = True
        self._import_cancel.clear()
        self.library.show_import(0, len(paths), f"Reading title pages  \u00b7  0 of {len(paths)}")

        def work():
            try:
                moved = analyze.run(self.library_root, paths,
                                    progress=lambda d, t, name: self._emit(self._analyse_progress, d, t, name),
                                    cancel=self._import_cancel)
            except Exception:  # noqa: BLE001
                moved = []
            self._emit(self._analyse_done, moved)

        threading.Thread(target=work, daemon=True).start()

    def _on_analyse_progress(self, done: int, total: int, name: str) -> None:
        self.library.show_import(done, total, f"Reading title pages  \u00b7  {done} of {total}  \u00b7  {name}")

    def _on_analyse_done(self, moved: list) -> None:
        self._analysing = False
        for old, new in moved:  # progress follows the renamed files
            self.store.move_book(old, new)
        if moved:
            self._save_timer.start()
        self.library.end_import(f"Read and named {len(moved)} book{'s' if len(moved) != 1 else ''}" if moved else "")
        QTimer.singleShot(6000, lambda: self.library.end_import(""))
        self.library.rescan()

    def show_details(self, path: str = "") -> None:
        """What is known about a library file, with room to correct it; saving renames the copy."""
        from .detailsdialog import DetailsDialog

        path = path or (self.book.path if self.book is not None else "")
        if not path:
            return
        index = organize.Index(self.library_root)
        saved = index.entry(path)
        if saved.get("details"):
            details = analyze.Details.from_dict(saved["details"])
        else:
            self.setCursor(Qt.WaitCursor)
            try:
                details = analyze.analyse(path, hints={"author": saved.get("author", ""), "title": saved.get("title", ""),
                                                       "source": os.path.basename(saved.get("source", ""))})
            finally:
                self.unsetCursor()
        dialog = DetailsDialog(path, details, saved.get("source", ""), self)
        if dialog.exec() != QDialog.Accepted or dialog.result_details is None:
            return
        reading = self.book is not None and os.path.normcase(self.book.path) == os.path.normcase(path)
        page = self.view.current_page() if reading else 0
        if reading:  # the file is about to move: let go of it first
            self._close_book()
        if organize.inside(path, self.library_root):
            try:
                new = analyze.apply(self.library_root, path, dialog.result_details, index)
                index.save()
            except OSError as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not rename the file:\n\n{exc}")
                new = path
        else:  # not a library copy: only remember the details
            index.files[index.rel(path)] = dict(saved, details=dialog.result_details.as_dict())
            new = path
        if new != path:
            self.store.move_book(path, new)
            self._save_timer.start()
        self.library.rescan()
        if reading:
            self.store.update_book(new, page=page)
            self.open_book(new)

    def _stop_import(self) -> None:
        self._import_cancel.set()
        self._import_queue.clear()
        self.library.show_import(0, 0, "Stopping…")

    def open_book(self, path: str) -> None:
        """Open a book of any kind. Indexing and text layout happen off the UI thread."""
        self._open_ticket += 1
        self.setCursor(Qt.WaitCursor)
        kind = documents.kind_of(path)
        modes = self._modes(kind)
        dims = self._text_page(modes["double"], modes["scroll"])
        mark = self.store.book(path).get("mark")
        self._open_pool.submit(self._load_book, self._open_ticket, path, dims, self._text_style(), mark, modes["scroll"])

    def _load_book(self, ticket: int, path: str, dims=(0, 0), style=None, mark=None, scroll=False) -> None:
        book, error = None, ""
        try:
            book = documents.open_book(path, style)
            if book.kind != "comic":
                book.notes = annotations.Notes(self.store.folder, organize.fingerprint(path), path, book.name)
            if book.kind == "comic":
                book.scan_sizes(self._sizes_dir)
            elif book.kind == "reflow":
                book.scroll = scroll
                book.start_page = book.configure(*dims, style, keep=mark)
                book.layout_key = (tuple(dims), style)
        except BookError as exc:
            book, error = None, str(exc)
        except Exception as exc:  # unreadable drive, permissions, ...
            book, error = None, f"Could not be opened: {exc}"
        try:
            self._opened.emit(ticket, book, error, path)
        except RuntimeError:  # window closed meanwhile
            if book is not None:
                book.close()

    def _on_opened(self, ticket: int, book, error: str, path: str) -> None:
        if ticket != self._open_ticket:  # a newer request superseded this one
            if book is not None:
                book.close()
            return
        self.unsetCursor()
        if book is None:
            QMessageBox.warning(self, APP_NAME, f"{os.path.basename(path)}\n\n{error}")
            return
        self._close_book()
        self.book = book
        saved = self.store.book(book.path)
        modes = self._modes(book.kind)
        if book.kind == "reflow":
            page = getattr(book, "start_page", 0)
            self._layout_key = getattr(book, "layout_key", None)
        else:
            page = int(saved.get("page", 0))
        if page >= len(book) - 1:
            page = 0  # finished last time: start over
        view = self.view
        view.rtl = bool(saved.get("rtl", self.store.get("rtl") if book.kind == "comic" else False))
        view.double, view.scroll = modes["double"], modes["scroll"]
        if book.kind == "reflow":  # pages are laid out for the screen: show them at exactly 100%
            view.cover_alone, view.fit, view.zoom = False, "zoom", self.view.devicePixelRatioF()
        else:
            view.cover_alone = bool(self.store.get("cover_alone"))
            view.fit = self.store.get("fit") if self.store.get("fit") in ("page", "width", "height") else "page"
        view.notes = getattr(book, "notes", None)
        self._history.clear()
        if book.kind == "fixed" and view.notes is not None and not view.notes.contents.get("version"):
            try:
                has_toc = bool(book.toc())
            except Exception:
                has_toc = True
            if not has_toc:  # find the printed contents page once, quietly, while the reader reads
                QTimer.singleShot(400, self._find_contents)
        self.selection_bar.hide()
        view.set_book(book, page)
        self.title.setText(book.name.upper())
        self.title.setToolTip(book.path)
        self.setWindowTitle(f"{book.name} - {APP_NAME}")
        self.stack.setCurrentWidget(self.reader)
        self.view.setFocus()
        self._sync_controls()

    def _close_book(self) -> None:
        if self.book is None:
            return
        self._record_progress()
        old, self.book = self.book, None
        self.view.set_book(None)
        old.close()

    def show_library(self) -> None:
        if self.isFullScreen():
            self.toggle_fullscreen()
        if self.book is not None:
            self._record_progress()
            self.library.refresh_progress(self.book.path)
            self.library.enter_author_of(self.book.path)
        self.stack.setCurrentWidget(self.library)
        self.library.focus_grid()
        self.setWindowTitle(APP_NAME)

    # --------------------------------------------------------------- state

    def _record_progress(self) -> None:
        if self.book is None or not self.view.spread_count() or self.view.held():
            return
        pages = self.view.pages()
        page = len(self.book) - 1 if self.view.at_last() else pages[0]
        fields = {"page": page, "count": len(self.book), "rtl": self.view.rtl}
        if self.book.kind == "reflow":  # page numbers change with the layout; the bookmark does not
            fields["mark"] = self.book.bookmark(pages[0])
        self.store.update_book(self.book.path, **fields)
        self._save_timer.start()

    def _on_view_changed(self) -> None:
        self._next_armed = 0.0
        if self.book is not None:
            self._record_progress()
        self._sync_controls()

    def _sync_controls(self) -> None:
        view = self.view
        self.btn_double.setChecked(view.double)
        self.act_double.setChecked(view.double)
        self.btn_rtl.setChecked(view.rtl)
        self.act_rtl.setChecked(view.rtl)
        reflow = self.book is not None and self.book.kind == "reflow"
        self.act_cover.setChecked(view.cover_alone)
        self.act_cover.setEnabled(view.double and not view.scroll and not reflow)
        self.btn_scroll.setChecked(view.scroll)
        self.act_scroll.setChecked(view.scroll)
        self.btn_rtl.setEnabled(not view.scroll)
        self.act_rtl.setEnabled(not view.scroll)
        self.btn_text.setVisible(reflow)
        self.btn_contents.setVisible(self.book is not None and self.book.kind != "comic")
        for fit in ("page", "width", "height"):
            self.btn_fit[fit].setChecked(view.fit == fit and not reflow)
            self.act_fit[fit].setChecked(view.fit == fit and not reflow)
            self.btn_fit[fit].setEnabled(not reflow)
            self.act_fit[fit].setEnabled(not reflow)
        if reflow:
            self.zoom_label.setText(f"{self._text_settings()['size']} PX")
            self.zoom_label.setToolTip("Text size (+ and - change it)")
        else:
            self.zoom_label.setText(f"{round(view.scale * 100)}%")
            self.zoom_label.setToolTip("Scale relative to the page's own size")
        self.btn_left.setToolTip(self._tip("page_left", "Next page" if view.rtl else "Previous page"))
        self.btn_right.setToolTip(self._tip("page_right", "Previous page" if view.rtl else "Next page"))
        if self.book is None:
            self.page_label.setText("")
            self.seek.set_state(0, 0, view.rtl)
            return
        pages = view.pages()
        shown = f"{pages[0] + 1}" if len(pages) == 1 else f"{pages[0] + 1}-{pages[-1] + 1}"
        self.page_label.setText(f"{shown} / {len(self.book)}")
        self.seek.set_state(view.spread_index(), view.spread_count() - 1, view.rtl)

    def _set_double(self, on: bool) -> None:
        self.view.set_double(bool(on))
        kind = self.book.kind if self.book is not None else "comic"
        self._set_mode(kind, "double", self.view.double)
        if kind == "comic":
            self.store.set("double", self.view.double)
        self.view.toast("Two pages" if self.view.double else "Single page")
        self._sync_controls()
        self._relayout_text()

    def _set_scroll(self, on: bool) -> None:
        on = bool(on)
        if self.book is not None and self.book.kind == "reflow":
            if self.view.held():
                self._sync_controls()
                return
            self.book.scroll = on  # text pages lose their top and bottom margins in a column
        self.view.set_scroll(on)
        self._set_mode(self.book.kind if self.book is not None else "comic", "scroll", on)
        self.view.toast("Scroll mode" if on else "Page by page")
        self._sync_controls()
        self._relayout_text()

    # ------------------------------------------------------------ notes, links, selection

    def _notes(self):
        return getattr(self.book, "notes", None) if self.book is not None else None

    def _on_selection(self, point) -> None:
        if point.x() < 0 or not self.view.has_selection():
            self.selection_bar.hide()
        else:
            self.selection_bar.show_at(point)

    def copy_selection(self) -> None:
        text = self.view.selected_text()
        if text:
            QApplication.clipboard().setText(text)
            self.view.toast("Copied")
        self.selection_bar.hide()

    def highlight_selection(self, colour: str = "yellow") -> None:
        notes, positions = self._notes(), self.view.selection_positions()
        if notes is None or positions is None:
            return
        notes.add_highlight(positions[0], positions[1], self.view.selected_text(), colour)
        self.view.clear_selection()
        self.selection_bar.hide()
        self.view.toast("Highlighted")

    def toggle_bookmark(self) -> None:
        notes = self._notes()
        if notes is None or not self.view.spread_count():
            return
        page = self.view.current_page()
        span = self.view.page_span(page)
        if span is None:
            return
        layer = self.book.text_layer(page)
        label = layer.text(0, 14).replace("\n", " ")[:90] or f"Page {page + 1}"
        added = notes.toggle_bookmark(*span, label, page)
        self.view.viewport().update()
        self.view.toast("Bookmarked" if added else "Bookmark removed")

    def _on_link(self, target) -> None:
        if isinstance(target, str):
            if re.match(r"^(https?|mailto):", target, re.I):
                QDesktopServices.openUrl(QUrl(target))
            return
        page, y = target
        if not 0 <= page < len(self.book):
            return
        self._history.append(self.view.current_page())
        self.view.go_to_position(page, y)
        keys = self.keymap.keys("link_back")
        self.view.toast(f"{native(keys[0])} to go back" if keys else "Followed the link", 2200)

    def link_back(self) -> None:
        if self._history:
            self.view.go_to_page(self._history.pop())
        else:
            self.view.prev()

    def show_notes(self, anchor=None) -> None:
        if self.book is None or self.book.kind == "comic":
            return
        if self.notes_panel.isVisible():
            self.notes_panel.close()
            return
        if time.monotonic() - self.notes_panel.closed_at < 0.25:
            return
        self.notes_panel.populate(self._notes_data())
        self.notes_panel.open(anchor if anchor is not None else self.btn_contents, self)

    def _notes_data(self) -> dict:
        book, notes = self.book, self._notes()
        data = {"toc": [], "fixed": book.kind == "fixed", "bookmarks": [], "highlights": [],
                "searching": self._searching_contents is book}
        try:
            data["toc"] = book.toc()
        except Exception:
            pass
        if notes is not None:
            if notes.contents.get("version"):
                data["detected"] = notes.contents
            for entry in notes.bookmarks:
                data["bookmarks"].append((entry, book.page_of_word(*entry["at"]), entry.get("label", "")))
            for entry in notes.highlights:
                data["highlights"].append((entry, book.page_of_word(*entry["start"]), entry.get("text", ""), entry.get("colour", "yellow")))
        offsets = (notes.contents.get("offsets") or {}) if notes is not None else {}
        label = book.page_label(self.view.current_page()) if hasattr(book, "page_label") else ""
        data["printed_guess"] = int(label) if label.isdigit() else self.view.current_page() - offsets.get("arabic", 0)
        return data

    def _go_to_target(self, page: int, y: float) -> None:
        self._history.append(self.view.current_page())
        if y:
            self.view.go_to_position(page, y)
        else:
            self.view.go_to_page(page)
        self.view.setFocus()

    def _remove_bookmark(self, entry) -> None:
        notes = self._notes()
        if notes is not None:
            notes.remove_bookmark(entry)
            self.notes_panel.populate(self._notes_data())
            self.view.viewport().update()

    def _remove_highlight(self, entry) -> None:
        notes = self._notes()
        if notes is not None:
            notes.remove_highlight(entry)
            self.notes_panel.populate(self._notes_data())
            self.view.viewport().update()

    def _find_contents(self) -> None:
        """Look for the printed contents page in the background (OCR on scans), once per book."""
        book = self.book
        if book is None or book.kind != "fixed" or self._searching_contents is book:
            return
        self._searching_contents = book
        if self.notes_panel.isVisible():
            self.notes_panel.populate(self._notes_data())

        def work():
            try:
                found = contents.detect(book)
            except Exception:
                found = {"entries": [], "method": "", "version": 1}
            self._emit(self._contents_found, book, found)

        threading.Thread(target=work, daemon=True).start()

    def _on_contents_found(self, book, found: dict) -> None:
        if self._searching_contents is book:
            self._searching_contents = None
        notes = getattr(book, "notes", None)
        if notes is None:
            return
        notes.contents = found
        notes.save()
        if book is self.book and self.notes_panel.isVisible():
            self.notes_panel.populate(self._notes_data())

    def _set_printed(self, printed: int) -> None:
        notes = self._notes()
        if notes is None or not notes.contents.get("entries"):
            return
        notes.contents = contents.with_offset(notes.contents, self.book, self.view.current_page() - printed)
        notes.save()
        self.notes_panel.populate(self._notes_data())
        self.view.toast("Contents page numbers corrected")

    # ------------------------------------------------------------ text

    def _modes(self, kind: str) -> dict:
        saved = (self.store.get("modes") or {}).get(kind) or {}
        double = bool(self.store.get("double")) if kind == "comic" else False
        return {"double": bool(saved.get("double", double)), "scroll": bool(saved.get("scroll", False))}

    def _set_mode(self, kind: str, key: str, value: bool) -> None:
        modes = dict(self.store.get("modes") or {})
        entry = dict(modes.get(kind) or {})
        entry[key] = bool(value)
        modes[kind] = entry
        self.store.set("modes", modes)
        self._save_timer.start()

    def _text_settings(self) -> dict:
        saved = self.store.get("text") or {}
        settings = {key: saved.get(key, value) for key, value in TEXT_DEFAULTS.items()}
        settings["size"] = max(TEXT_SIZES[0], min(TEXT_SIZES[1], int(settings["size"])))
        return settings

    def _text_style(self) -> documents.TextStyle:
        settings = self._text_settings()
        if settings["tone"] == "scheme":
            paper, ink = theme.FELD_DEEP.name(), theme.GREY.name()
        else:
            paper, ink = TONE_COLOURS.get(settings["tone"], TONE_COLOURS["paper"])
        return documents.TextStyle(size=settings["size"], family=settings["family"], spacing=settings["spacing"],
                                   justify=bool(settings["justify"]), paper=paper, ink=ink)

    def _text_page(self, double: bool, scroll: bool) -> tuple[int, int]:
        """Page size (screen px, margins included) that fits the view, for laying text out."""
        viewport = self.view.viewport()
        width, height = viewport.width(), viewport.height()
        if width < 200 or height < 200 or not self.view.isVisible():  # not shown yet: work it out from the window
            bars = 0 if self.isFullScreen() else (self.top_bar.sizeHint().height() + self.bottom_bar.sizeHint().height() + 2 * theme.RULE)
            width, height = self.stack.width(), self.stack.height() - bars
        pad = theme.u(14)
        style = self._text_style()
        column = (width - 3 * pad) // 2 if double and not scroll else width - 2 * pad
        return max(240, min(column, style.measure())), max(240, height - 2 * pad)

    def _relayout_text(self) -> None:
        """Lay the open text out again if the view, mode or style no longer match its pages."""
        book = self.book
        if book is None or book.kind != "reflow":
            return
        if self.view.held():
            self._relayout_timer.start()
            return
        key = (self._text_page(self.view.double, self.view.scroll), self._text_style())
        if key == self._layout_key:
            return
        mark = book.bookmark(self.view.current_page())
        self._layout_ticket += 1
        self._layout_key = key
        self.view.hold("Laying out")
        self._open_pool.submit(self._configure, self._layout_ticket, book, key, mark)

    def _configure(self, ticket: int, book, key, mark) -> None:
        (width, height), style = key
        try:
            page, error = book.configure(width, height, style, keep=mark), ""
        except Exception as exc:  # noqa: BLE001 - shown to the user
            page, error = 0, str(exc)
        try:
            self._relaid.emit(ticket, book, page, error)
        except RuntimeError:
            pass

    def _on_relaid(self, ticket: int, book, page: int, error: str) -> None:
        if ticket != self._layout_ticket or book is not self.book:
            return
        self.view.zoom = self.view.devicePixelRatioF()
        self.view.set_book(book, page)
        if error:
            QMessageBox.warning(self, APP_NAME, error)
        self._sync_controls()
        self._record_progress()

    def show_text_menu(self, anchor=None) -> None:
        if self.book is None or self.book.kind != "reflow":
            return
        from .appearance import show_text_menu

        show_text_menu(anchor or self.btn_text, self._text_settings(), self._on_text)

    def _on_text(self, key: str, value) -> None:
        settings = self._text_settings()
        if key == "size":
            value = max(TEXT_SIZES[0], min(TEXT_SIZES[1], int(value)))
        settings[key] = value
        self.store.set("text", settings)
        self._save_timer.start()
        if key == "size":
            self.view.toast(f"Text {value} px")
        self._relayout_text()
        self._sync_controls()

    def _zoom(self, direction: int) -> None:
        if self.book is not None and self.book.kind == "reflow":
            self._on_text("size", self._text_settings()["size"] + direction)
        else:
            self.view.zoom_by(1.15 if direction > 0 else 1 / 1.15)

    def _set_rtl(self, on: bool) -> None:
        self.view.set_rtl(bool(on))
        self.store.set("rtl", self.view.rtl)
        self._record_progress()
        self.view.toast("Right to left" if self.view.rtl else "Left to right")
        self._sync_controls()

    def _set_cover_alone(self, on: bool) -> None:
        self.view.set_cover_alone(bool(on))
        self.store.set("cover_alone", self.view.cover_alone)
        self._sync_controls()

    def _set_fit(self, fit: str) -> None:
        if self.book is not None and self.book.kind == "reflow":
            self._sync_controls()
            return
        self.view.set_fit(fit)
        self.store.set("fit", fit)
        self.view.toast(FIT_LABELS[fit])
        self._sync_controls()

    def _actual_size(self) -> None:
        if self.book is not None and self.book.kind == "reflow":
            self.view.zoom_by(self.view.devicePixelRatioF() / self.view.scale)
            self.view.toast("Actual size")
            return
        self.view.zoom_by(1.0 / self.view.scale)
        self.view.toast("Actual size")

    def _go_to_page(self) -> None:
        if self.book is None:
            return
        page, ok = QInputDialog.getInt(
            self, "Go to page", f"Page (1-{len(self.book)})", self.view.current_page() + 1, 1, len(self.book)
        )
        if ok:
            self.view.go_to_page(page - 1)
        self.view.setFocus()

    def _on_end(self) -> None:
        following = self._next_volume()
        now = time.monotonic()
        if following and now - self._next_armed < 5.0:
            self.open_book(following)
            return
        if following:
            self._next_armed = now
            self.view.toast(f"End  ·  again for {archive.display_name(following)}", 4000)
        else:
            self.view.toast("End of volume", 2000)

    def _next_volume(self) -> str:
        if self.book is None:
            return ""
        following = organize.next_work(self.book.path, self.library_root)
        if following or organize.inside(self.book.path, self.library_root):
            return following
        books = archive.sibling_books(self.book.path)
        keys = [os.path.normcase(p) for p in books]
        try:
            index = keys.index(os.path.normcase(self.book.path))
        except ValueError:
            return ""
        return books[index + 1] if index + 1 < len(books) else ""

    # ------------------------------------------------------------- chrome

    def _set_bars(self, on: bool) -> None:
        for widget in (self.top_bar, self.top_rule, self.bottom_rule, self.bottom_bar):
            widget.setVisible(bool(on))
        self.act_bars.setChecked(bool(on))

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showMaximized() if self._was_maximized else self.showNormal()
            self._set_bars(True)
            self.view.set_cursor_autohide(False)
        else:
            if self.stack.currentWidget() is not self.reader:
                self.act_full.setChecked(False)
                return
            self._was_maximized = self.isMaximized()
            self._set_bars(False)
            self.showFullScreen()
            self.view.set_cursor_autohide(True)
            bars = self.keymap.keys("bars")
            hint = f"{native(bars[0])} shows toolbars  ·  " if bars else ""
            self.view.toast(f"{hint}Esc leaves fullscreen", 2600)
        self.act_full.setChecked(self.isFullScreen())
        self.view.setFocus()

    _was_maximized = False

    def _escape(self) -> None:
        if self.isFullScreen():
            self.toggle_fullscreen()

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        notes = self._notes()
        if self.view.has_selection():
            menu.addAction("Copy", self.copy_selection)
            menu.addAction("Highlight", self.highlight_selection)
            menu.addSeparator()
        if notes is not None:
            at = self.view.position_at(QPointF(pos))
            mark = notes.highlight_at(at) if at is not None else None
            if mark is not None:
                menu.addAction("Copy highlighted text", lambda: QApplication.clipboard().setText(mark.get("text", "")))
                menu.addAction("Remove highlight", lambda: (notes.remove_highlight(mark), self.view.viewport().update()))
                menu.addSeparator()
            menu.addAction(self.acts["contents"])
            menu.addAction(self.acts["bookmark"])
            menu.addSeparator()
        if self.book is not None:
            menu.addAction("Details of this book\u2026", lambda: self.show_details(self.book.path))
            menu.addSeparator()
        menu.addAction(self.act_library)
        menu.addAction(self.act_open)
        menu.addAction(self.act_goto)
        menu.addSeparator()
        menu.addAction(self.act_double)
        menu.addAction(self.act_rtl)
        menu.addAction(self.act_cover)
        menu.addSeparator()
        for action in self.act_fit.values():
            menu.addAction(action)
        menu.addAction(self.act_actual)
        menu.addAction(self.act_zoom_in)
        menu.addAction(self.act_zoom_out)
        menu.addSeparator()
        menu.addAction(self.act_bars)
        menu.addAction(self.act_full)
        menu.addSeparator()
        menu.addAction(self.acts["hotkeys"])
        if self.book:
            menu.addSeparator()
            menu.addAction("Make a smaller copy…", lambda: self.show_shrink(self.book.path))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    # -------------------------------------------------------------- events

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_path(url.toLocalFile())
                break

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._tinted:
            self._tinted = True
            _tint_title_bar(self)
            QTimer.singleShot(1500, self.sync_sources)
            QTimer.singleShot(4000, self.analyse_library)

    def closeEvent(self, event) -> None:
        self._record_progress()
        if not self.isFullScreen():
            self.store.set("geometry", self.saveGeometry().toBase64().data().decode("ascii"))
        self.store.dirty = True
        self.store.save()
        self._open_ticket += 1
        self._import_cancel.set()
        self._open_pool.shutdown(wait=False, cancel_futures=True)
        self.view.loader.shutdown()
        self.library.thumbs.shutdown()
        if self.book is not None:
            self.book.close()
        super().closeEvent(event)


def _file_filter(extensions) -> str:
    patterns = " ".join(f"*{e}" for e in sorted(extensions))
    return f"Books ({patterns});;All files (*)"


def _tint_title_bar(window) -> None:
    """Windows 10/11: dark title bar; Windows 11: feldgrau caption with light-grey text."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi

        def set_attr(attribute: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

        def colorref(colour) -> int:
            return colour.red() | (colour.green() << 8) | (colour.blue() << 16)

        set_attr(20, 1 if theme.DARK_TITLE else 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        set_attr(35, colorref(theme.FELD))     # DWMWA_CAPTION_COLOR
        set_attr(36, colorref(theme.GREY))     # DWMWA_TEXT_COLOR
        set_attr(34, colorref(theme.FELD_DARK))  # DWMWA_BORDER_COLOR
    except Exception:
        pass
