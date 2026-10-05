"""The library: authors (or comic series), their works, and each work's variants, as grids of covers."""

from __future__ import annotations

import hashlib
import html
import os
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEvent, QModelIndex, QObject, QPoint, QRect, QRectF, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPolygon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QMenu, QProgressBar, QPushButton,
    QStyle, QStyledItemDelegate, QTreeView, QVBoxLayout, QWidget,
)

from . import archive, documents, imaging, organize, theme
from .loader import to_qimage
from .widgets import IconButton

PATH_ROLE = Qt.UserRole + 1      # the file whose cover the tile shows
THUMB_ROLE = Qt.UserRole + 2
PROGRESS_ROLE = Qt.UserRole + 3
KIND_ROLE = Qt.UserRole + 4      # "author", "work" or "variant"
KEY_ROLE = Qt.UserRole + 5       # where the tile leads: (author index,) or (author index, work index) or a file
STATUS_ROLE = Qt.UserRole + 6    # the small line under the title
BADGE_ROLE = Qt.UserRole + 7     # format tag drawn on the cover
FILES_ROLE = Qt.UserRole + 8     # every file the tile stands for (for progress)
SORT_ROLE = Qt.UserRole + 9      # list view: what a column sorts by

COVER_W, COVER_H = 168, 252
PAD = 14
TEXT_H = theme.u(62)
TILE = QSize(COVER_W + 2 * PAD, COVER_H + PAD + TEXT_H)


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") or size >= 100 else f"{size:.1f} {unit}"
        size /= 1000
    return ""


class Thumbnailer(QObject):
    """Reads the library and makes cover thumbnails in the background, keeping them on disk."""

    done = Signal(int, str, QImage)
    scanned = Signal(int, str, list)

    def __init__(self, cache_dir: str, parent=None):
        super().__init__(parent)
        self.cache_dir = cache_dir
        self.generation = 0
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="roughreader-cover")

    def scan(self, root: str) -> None:
        self.generation += 1
        self._pool.submit(self._scan, self.generation, root)

    def _scan(self, generation: int, root: str) -> None:
        try:
            authors = organize.scan(root) if root else []
        except OSError:
            authors = []
        self._emit(self.scanned, generation, root, authors)

    def request(self, path: str) -> None:
        self._pool.submit(self._cover, self.generation, path)

    def _cover(self, generation: int, path: str) -> None:
        if generation != self.generation:
            return
        image = QImage()
        try:
            tag = f"{os.path.normcase(path)}|{archive.fingerprint(path)}"
            cached = os.path.join(self.cache_dir, hashlib.sha1(tag.encode("utf-8")).hexdigest() + ".jpg")
            if os.path.exists(cached):
                try:
                    image = to_qimage(imaging.load_rgb(cached))
                except Exception:
                    image = QImage()  # damaged cache file: rebuild it
            if image.isNull():
                thumb = documents.cover_thumbnail(path)
                os.makedirs(self.cache_dir, exist_ok=True)
                thumb.save(cached, "JPEG", quality=88)
                image = to_qimage(thumb)
        except Exception:
            image = QImage()
        self._emit(self.done, generation, path, image)

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    def shutdown(self) -> None:
        self.generation += 1
        self._pool.shutdown(wait=False, cancel_futures=True)


def _two_lines(text: str, metrics: QFontMetrics, width: int) -> list[str]:
    """Word-wrap into at most two lines, eliding the second."""
    if metrics.horizontalAdvance(text) <= width:
        return [text]
    words, first = text.split(" "), ""
    while words and metrics.horizontalAdvance((first + " " + words[0]).strip()) <= width:
        first = (first + " " + words.pop(0)).strip()
    if not first:  # one very long word
        return [metrics.elidedText(text, Qt.ElideRight, width)]
    return [first, metrics.elidedText(" ".join(words), Qt.ElideRight, width)]


class CoverDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = theme.font(10, bold=True)
        self._status = theme.font(8, bold=True, spacing=1.4)
        self._badge = theme.font(7.5, bold=True, spacing=1.2)

    def sizeHint(self, _option, _index) -> QSize:
        return TILE

    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        p.save()
        cover = QRect(option.rect.x() + PAD, option.rect.y() + PAD, COVER_W, COVER_H)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        kind = index.data(KIND_ROLE)

        if kind == "author":  # a stack: the author's other books peek out behind
            for step, colour in ((8, theme.FELD_DARK), (4, theme.FELD_LIGHT)):
                p.fillRect(cover.translated(step, -step), colour)
        thumb = index.data(THUMB_ROLE)
        if isinstance(thumb, QImage) and not thumb.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(cover, thumb)
        else:
            p.fillRect(cover, theme.FELD_DARK)
            p.setOpacity(0.45)
            theme.draw_mark(p, QRectF(cover.center().x() - 22, cover.center().y() - 22, 44, 44))
            p.setOpacity(1.0)

        badge = index.data(BADGE_ROLE)
        if badge:
            p.setFont(self._badge)
            metrics = QFontMetrics(self._badge)
            box = QRect(cover.x(), cover.bottom() - 12 - metrics.height() - 6, metrics.horizontalAdvance(badge) + 16, metrics.height() + 6)
            p.fillRect(box, theme.BURG)
            p.setPen(theme.ON_ACCENT)
            p.drawText(box, Qt.AlignCenter, badge)

        status = index.data(STATUS_ROLE) or ""
        page, count = index.data(PROGRESS_ROLE) or (0, 0)
        if count:
            frac = 1.0 if page >= count - 1 else page / count
            bar = QRect(cover.x(), cover.bottom() - 7, cover.width(), 8)
            p.fillRect(bar, theme.FELD_DEEP)
            p.fillRect(QRect(bar.x(), bar.y(), round(bar.width() * frac), bar.height()), theme.BURG_HI)
            reading = "READ" if page >= count - 1 else f"{page + 1} / {count}"
            status = f"{reading}  ·  {status}" if status and kind != "author" else (status or reading)
        elif not status:
            status = "UNREAD"

        edge = theme.BORDER
        if selected:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(theme.BURG, edge * 3))
            p.drawRect(cover.adjusted(-3, -3, 2, 2))
            p.setPen(Qt.NoPen)
            p.setBrush(theme.BURG)
            corner = cover.topLeft() + QPoint(-4, -4)
            p.drawPolygon(QPolygon([corner, corner + QPoint(32, 0), corner + QPoint(0, 32)]))
        elif hovered or theme.BORDERS:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(theme.GREY if hovered else theme.FELD_LIGHT, edge))
            p.drawRect(cover.adjusted(-1, -1, 0, 0))

        p.setFont(self._title)
        p.setPen(theme.GREY)
        metrics = QFontMetrics(self._title)
        y = cover.bottom() + 8
        for line in _two_lines(index.data(Qt.DisplayRole) or "", metrics, COVER_W + 8):
            p.drawText(QRect(cover.x(), y, COVER_W + 8, metrics.height()), Qt.AlignLeft | Qt.AlignVCenter, line)
            y += metrics.height()
        p.setFont(self._status)
        p.setPen(theme.GREY if selected else theme.GREY_DIM)
        status_metrics = QFontMetrics(self._status)
        p.drawText(QRect(cover.x(), y + 2, COVER_W + 8, theme.u(16)), Qt.AlignLeft | Qt.AlignVCenter,
                   status_metrics.elidedText(status.upper(), Qt.ElideRight, COVER_W + 8))
        p.restore()


MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Lucida Console", "Courier New"]
LIST_BORDERS = (4, 2, 1)          # accent line under an author, a work, a version


def _blend(a: QColor, b: QColor, amount: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * amount), round(a.green() + (b.green() - a.green()) * amount),
                  round(a.blue() + (b.blue() - a.blue()) * amount))


def _depth(index: QModelIndex) -> int:
    depth = 0
    parent = index.parent()
    while parent.isValid():
        depth += 1
        parent = parent.parent()
    return min(depth, 2)


def level_colour(depth: int) -> QColor:
    """Authors darkest, works lighter, versions in the scheme's own colour."""
    return _blend(theme.FELD, theme.FELD_DEEP, (0.75, 0.35, 0.0)[depth])


class ListDelegate(QStyledItemDelegate):
    """Text for the list: Bahnschrift, larger for authors with an example cover; sizes in a monospace font."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fonts = (theme.font(12.5, bold=True, spacing=0.6), theme.font(10, bold=True), theme.font(9.5))
        self.small = theme.font(9)
        self.mono = QFont()
        self.mono.setFamilies(MONO_FAMILIES)
        self.mono.setPointSizeF(9 * theme.TEXT_SCALE)
        self.mono.setStyleHint(QFont.Monospace)

    def row_height(self, depth: int) -> int:
        line = QFontMetrics(self.fonts[1]).height()
        return line * 2 + theme.u(14) if depth == 0 else line + theme.u(12)

    def font_for(self, index: QModelIndex) -> QFont:
        depth, column = _depth(index), index.column()
        if column == 6:
            return self.mono
        if column == 0:
            return self.fonts[depth]
        return self.small if depth == 0 else self.fonts[2]

    def sizeHint(self, option, index) -> QSize:
        depth = _depth(index)
        width = QFontMetrics(self.font_for(index)).horizontalAdvance(index.data(Qt.DisplayRole) or "") + theme.u(20)
        if index.column() == 0 and depth == 0:
            width += round((self.row_height(0) - theme.u(8)) * 2 / 3) + theme.u(10)
        return QSize(width, self.row_height(depth))

    def paint(self, p: QPainter, option, index: QModelIndex) -> None:
        depth = _depth(index)
        selected = bool(option.state & QStyle.State_Selected)
        column = index.column()
        rect = QRect(option.rect).adjusted(theme.u(6), 0, -theme.u(6), -LIST_BORDERS[depth])
        p.save()
        if column == 0 and depth == 0:  # an example book of the author or series
            thumb = index.data(THUMB_ROLE)
            height = rect.height() - theme.u(8)
            cover = QRect(rect.x(), rect.y() + theme.u(4), round(height * 2 / 3), height)
            if isinstance(thumb, QImage) and not thumb.isNull():
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                p.drawImage(cover, thumb)
            else:
                p.fillRect(cover, theme.FELD_LIGHT)
            rect.setLeft(cover.right() + theme.u(10))
        font = self.font_for(index)
        p.setFont(font)
        dim = column != 0 and depth == 0
        p.setPen(theme.ON_ACCENT if selected else (theme.GREY_DIM if dim else theme.GREY))
        align = (Qt.AlignRight if column in (4, 5, 6) else Qt.AlignLeft) | Qt.AlignVCenter
        text = QFontMetrics(font).elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, max(10, rect.width()))
        p.drawText(rect, align, text)
        p.restore()


class LibraryTree(QTreeView):
    """The list view: each row shaded by its level and closed off by an accent line whose weight
    falls from author (4 px) to work (2 px) to version (1 px), instead of alternating stripes."""

    def drawRow(self, p: QPainter, option, index: QModelIndex) -> None:
        depth = _depth(index)
        row = QRect(0, option.rect.y(), self.viewport().width(), option.rect.height())
        p.fillRect(row, level_colour(depth))
        if self.selectionModel().isSelected(index):
            p.fillRect(row.adjusted(0, 0, 0, -LIST_BORDERS[depth]), theme.BURG)
        elif option.state & QStyle.State_MouseOver:
            wash = QColor(theme.FELD_LIGHT)
            wash.setAlpha(70)
            p.fillRect(row, wash)
        super().drawRow(p, option, index)
        weight = LIST_BORDERS[depth]
        p.fillRect(QRect(0, row.bottom() - weight + 1, row.width(), weight), theme.BURG)


class LibraryPage(QWidget):
    openRequested = Signal(str)
    addFolderRequested = Signal()
    addFilesRequested = Signal()
    openFileRequested = Signal()
    hotkeysRequested = Signal()
    shrinkRequested = Signal(str)
    appearanceRequested = Signal()
    stopImportRequested = Signal()
    detailsRequested = Signal(str)

    def __init__(self, store, cache_dir: str, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.store = store
        self.root = ""
        self.authors: list[organize.Author] = []
        self.level: tuple = ()        # () all authors, (a,) one author's works, (a, w) one work's variants
        self._by_path: dict[str, list[QStandardItem]] = {}

        self.thumbs = Thumbnailer(cache_dir, self)
        self.thumbs.done.connect(self._on_thumb)
        self.thumbs.scanned.connect(self._on_scanned)

        self.crumbs = QLabel("")
        self.crumbs.setFont(theme.font(13, bold=True, spacing=3))
        self.crumbs.setTextFormat(Qt.RichText)
        self.crumbs.linkActivated.connect(self._crumb)
        self.count_label = QLabel("")
        self.count_label.setObjectName("dim")
        self.count_label.setFont(theme.font(9))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("SEARCH TITLES AND AUTHORS")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(theme.u(250))
        self.filter.textChanged.connect(self._fill)
        self.add_button = IconButton("open", "Copy books into the library", "Add")
        self.file_button = IconButton("file", "Open a single file without adding it", "Open")
        self.appearance_button = IconButton("scheme", "Colours and borders")
        self.keys_button = IconButton("keys", "Hotkeys")
        self.view_button = IconButton("listview", "Show as a list (or as covers)", checkable=True)
        self.view_button.clicked.connect(lambda on: self.set_view("list" if on else "gallery"))
        self.folder_button = self.add_button  # the "Choose library folder" hotkey now adds a folder
        self.add_button.clicked.connect(self._add_menu)
        self.file_button.clicked.connect(self.openFileRequested)
        self.keys_button.clicked.connect(self.hotkeysRequested)
        self.appearance_button.clicked.connect(self.appearanceRequested)

        bar = QHBoxLayout()
        bar.setContentsMargins(PAD + 6, 14, PAD + 6, 10)
        bar.setSpacing(12)
        bar.addWidget(self.crumbs)
        bar.addWidget(self.count_label, 1)
        bar.addWidget(self.filter)
        bar.addWidget(self.view_button)
        bar.addWidget(self.add_button)
        bar.addWidget(self.file_button)
        bar.addWidget(self.appearance_button)
        bar.addWidget(self.keys_button)

        self.import_label = QLabel("")
        self.import_label.setFont(theme.font(9, bold=True, spacing=1.2))
        self.import_bar = QProgressBar()
        self.import_bar.setTextVisible(False)
        self.import_bar.setFixedWidth(theme.u(220))
        self.import_stop = QPushButton("STOP")
        self.import_stop.clicked.connect(self.stopImportRequested)
        self.import_row = QWidget()
        row = QHBoxLayout(self.import_row)
        row.setContentsMargins(PAD + 6, 0, PAD + 6, 10)
        row.setSpacing(12)
        row.addWidget(self.import_label, 1)
        row.addWidget(self.import_bar)
        row.addWidget(self.import_stop)
        self.import_row.hide()

        self.model = QStandardItemModel(self)
        self.grid = QListView()
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(CoverDelegate(self.grid))
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(TILE)
        self.grid.setSpacing(0)
        self.grid.setMouseTracking(True)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(40)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.activated.connect(self._activate)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.grid.installEventFilter(self)

        self.list_model = QStandardItemModel(self)
        self.list_proxy = QSortFilterProxyModel(self)
        self.list_proxy.setSourceModel(self.list_model)
        self.list_proxy.setSortRole(SORT_ROLE)
        self.list_proxy.setRecursiveFilteringEnabled(True)
        self.list_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.list_proxy.setFilterKeyColumn(-1)
        self.list = LibraryTree()
        self.list.setItemDelegate(ListDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setModel(self.list_proxy)
        self.list.setSortingEnabled(True)
        self.list.setAlternatingRowColors(False)
        self.list.setUniformRowHeights(False)
        self.list.setAllColumnsShowFocus(True)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setIndentation(theme.u(22))
        self.list.setExpandsOnDoubleClick(False)
        self.list.activated.connect(self._activate_row)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.header().setSectionsMovable(False)
        self.list.header().setFont(theme.font(8.5, bold=True, spacing=1.6))  # Bahnschrift, as everywhere
        self.list.header().setSortIndicator(0, Qt.AscendingOrder)
        self.view_mode = store.get("library_view") if store.get("library_view") in ("gallery", "list") else "gallery"
        self.view_button.setChecked(self.view_mode == "list")

        self.empty = QLabel("")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("dim")
        self.empty.setFont(theme.font(10, bold=True, spacing=2.5))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(bar)
        layout.addWidget(self.import_row)
        rule = QWidget()
        rule.setFixedHeight(theme.RULE)
        rule.setObjectName("rule")
        rule.setAttribute(Qt.WA_StyledBackground, True)
        layout.addWidget(rule)
        layout.addWidget(self.grid, 1)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty, 1)
        self.list.hide()
        self._update_crumbs()
        self._show_empty("READING THE LIBRARY")

    # -------------------------------------------------------------- data

    def set_root(self, root: str) -> None:
        self.root = root
        self.rescan()

    def rescan(self) -> None:
        if self.root:
            self.thumbs.scan(self.root)

    def _on_scanned(self, generation: int, root: str, authors: list) -> None:
        if generation != self.thumbs.generation or root != self.root:
            return
        keep = self._level_folders()
        self.authors = authors
        self.level = self._level_from_folders(keep)
        self._fill()

    def _level_folders(self) -> tuple:
        """The folders of the current level, so it survives a rescan."""
        try:
            if len(self.level) == 2:
                return (self.authors[self.level[0]].folder, self.authors[self.level[0]].works[self.level[1]].folder)
            if len(self.level) == 1:
                return (self.authors[self.level[0]].folder,)
        except IndexError:
            pass
        return ()

    def _level_from_folders(self, folders: tuple) -> tuple:
        for a, author in enumerate(self.authors):
            if folders and author.folder == folders[0]:
                if len(folders) == 2:
                    for w, work in enumerate(author.works):
                        if work.folder == folders[1] and len(work.variants) > 1:
                            return (a, w)
                return (a,)
        return ()

    def _progress(self, paths: list[str]) -> tuple[int, int]:
        """Progress of the most recently read of these files."""
        newest, stamp = "", -1.0
        for path in paths:
            ts = self.store.book(path).get("ts", -1.0)
            if ts > stamp:
                newest, stamp = path, ts
        return self.store.progress(newest) if stamp >= 0 else (0, 0)

    def _item(self, title: str, kind: str, key, cover: str, files: list[str], status: str = "", badge: str = "", tip: str = "") -> QStandardItem:
        item = QStandardItem(title)
        item.setEditable(False)
        item.setData(cover, PATH_ROLE)
        item.setData(kind, KIND_ROLE)
        item.setData(key, KEY_ROLE)
        item.setData(status, STATUS_ROLE)
        item.setData(badge, BADGE_ROLE)
        item.setData(files, FILES_ROLE)
        item.setData(self._progress(files) if kind != "author" else (0, 0), PROGRESS_ROLE)
        item.setToolTip(tip or title)
        self._by_path.setdefault(cover, []).append(item)
        return item

    def _work_item(self, a: int, w: int, with_author: bool = False) -> QStandardItem:
        author, work = self.authors[a], self.authors[a].works[w]
        files = [v.path for v in work.variants]
        formats = []
        for variant in work.variants:
            if variant.format not in formats:
                formats.append(variant.format)
        if len(work.variants) > 1:
            status = " · ".join(formats) if len(formats) > 1 else f"{len(work.variants)} versions"
        else:
            status = formats[0] if formats else ""
        if with_author:
            status = author.name
        tip = f"{work.title}\n{author.name}\n" + "\n".join(
            f"{v.format}  {v.label}  ({human_size(v.size)})".replace("  (", " (") for v in work.variants)
        return self._item(work.title, "work", (a, w), work.best().path, files, status, "", tip)

    def set_view(self, mode: str) -> None:
        self.view_mode = mode
        self.view_button.setChecked(mode == "list")
        self.store.set("library_view", mode)
        self._fill()

    def _fill_list(self) -> None:
        """The tree: authors (open), their works (closed), each work's versions."""
        expanded = {self.list_proxy.index(r, 0).data(KEY_ROLE) for r in range(self.list_proxy.rowCount())
                    if self.list.isExpanded(self.list_proxy.index(r, 0))} if self.list_model.rowCount() else None
        self.list_model.clear()
        self._list_covers = {}
        self.list_model.setHorizontalHeaderLabels(["TITLE", "AUTHOR", "FORMAT", "PUBLISHER", "YEAR", "READ", "SIZE"])
        for column in (4, 5, 6):  # numbers sit on the right, under their headings
            self.list_model.setHeaderData(column, Qt.Horizontal, Qt.AlignRight | Qt.AlignVCenter, Qt.TextAlignmentRole)

        def cells(name, key, kind, author="", formats="", publisher="", year="", progress=(0, 0), size=0, sort_name=""):
            page, count = progress
            read = "" if not count else ("read" if page >= count - 1 else f"{round(100 * page / count)}%")
            values = [name, author, formats, publisher, year, read, human_size(size) if size else ""]
            sorts = [sort_name or organize.fold(name), organize.sort_key(author) if author else "", formats, publisher.lower(),
                     year or "0", (page / count) if count else -1.0, size]
            row = []
            for value, order in zip(values, sorts):
                item = QStandardItem(value)
                item.setEditable(False)
                item.setData(order, SORT_ROLE)
                item.setData(key, KEY_ROLE)
                item.setData(kind, KIND_ROLE)
                row.append(item)
            return row

        for a, author in enumerate(self.authors):
            total = sum(v.size for w in author.works for v in w.variants)
            comics = all(v.format == "CBZ" for w in author.works for v in w.variants)
            count = f"{len(author.works)} {'volume' if comics else 'work'}{'s' if len(author.works) != 1 else ''}"
            top = cells(author.name, (a,), "author", "", count, "", "", (0, 0), total, organize.sort_key(author.name))
            example = author.works[0].best().path
            top[0].setData(example, PATH_ROLE)
            self._list_covers.setdefault(example, []).append(top[0])
            self.thumbs.request(example)
            for w, work in enumerate(author.works):
                best = work.best()
                files = [v.path for v in work.variants]
                formats = " · ".join(dict.fromkeys(v.format for v in work.variants))
                info = best.details or {}
                row = cells(work.title, (a, w), "work", author.name, formats, info.get("publisher", ""), info.get("year", ""),
                            self._progress(files), sum(v.size for v in work.variants))
                for variant in work.variants:
                    info = variant.details or {}
                    name = variant.label or ("Original" if variant.format == "CBZ" else variant.format)
                    row[0].appendRow(cells(name, variant.path, "variant", author.name, variant.format, info.get("publisher", ""),
                                           info.get("year", ""), self._progress([variant.path]), variant.size))
                top[0].appendRow(row)
            self.list_model.appendRow(top)
        self.list_proxy.setFilterFixedString(self.filter.text())
        header = self.list.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.list.sortByColumn(header.sortIndicatorSection(), header.sortIndicatorOrder())
        for row in range(self.list_proxy.rowCount()):  # authors open, works closed
            index = self.list_proxy.index(row, 0)
            self.list.setExpanded(index, expanded is None or index.data(KEY_ROLE) in expanded or bool(self.filter.text()))

    def _activate_row(self, index: QModelIndex) -> None:
        index = index.sibling(index.row(), 0)
        kind, key = index.data(KIND_ROLE), index.data(KEY_ROLE)
        if kind == "variant":
            self.openRequested.emit(key)
        elif kind == "work" and len(self.authors[key[0]].works[key[1]].variants) == 1:
            self.openRequested.emit(self.authors[key[0]].works[key[1]].variants[0].path)
        else:
            self.list.setExpanded(index, not self.list.isExpanded(index))

    def _list_menu(self, pos) -> None:
        index = self.list.indexAt(pos)
        if not index.isValid():
            return
        index = index.sibling(index.row(), 0)
        kind, key = index.data(KIND_ROLE), index.data(KEY_ROLE)
        self._menu_for(kind, key, self.list.viewport().mapToGlobal(pos), lambda: self._activate_row(index))

    def _fill(self, *_args) -> None:
        if self.view_mode == "list":
            self._update_crumbs()
            count = sum(len(a.works) for a in self.authors)
            self.count_label.setText(f"{len(self.authors)} authors and series  ·  {count} works" if self.authors else "")
            self.grid.hide()
            if not self.authors:
                self.list.hide()
                self._show_empty("THE LIBRARY IS EMPTY  ·  ADD A FOLDER, OR DROP ONE HERE")
                return
            self._fill_list()
            self.empty.hide()
            self.list.show()
            return
        self.list.hide()
        self.model.clear()
        self._by_path.clear()
        query = organize.fold(self.filter.text())
        rows: list[QStandardItem] = []
        if not self.authors:
            self._update_crumbs()
            self.count_label.setText("")
            self._show_empty("THE LIBRARY IS EMPTY  ·  ADD A FOLDER, OR DROP ONE HERE")
            return
        if query:
            for a, author in enumerate(self.authors):
                for w, work in enumerate(author.works):
                    if query in organize.fold(work.title + " " + author.name):
                        rows.append(self._work_item(a, w, with_author=True))
            noun = "work"
        elif not self.level:
            for a, author in enumerate(self.authors):
                comics = all(v.format == "CBZ" for work in author.works for v in work.variants)
                count = len(author.works)
                status = f"{count} {'volume' if comics else 'work'}{'s' if count != 1 else ''}"
                files = [v.path for work in author.works for v in work.variants]
                rows.append(self._item(author.name, "author", (a,), author.works[0].best().path, files, status))
            series = any(all(v.format == "CBZ" for w in x.works for v in w.variants) for x in self.authors)
            noun = "author or series" if series else "author"
        elif len(self.level) == 1:
            a = self.level[0]
            rows = [self._work_item(a, w) for w in range(len(self.authors[a].works))]
            noun = "work"
        else:
            a, w = self.level
            for variant in self.authors[a].works[w].variants:
                title = variant.label or ("Original" if variant.format == "CBZ" else variant.format)
                rows.append(self._item(title, "variant", variant.path, variant.path, [variant.path],
                                       human_size(variant.size), variant.format, variant.path))
            noun = "version"
        for item in rows:
            self.model.appendRow(item)
        for path in list(self._by_path):
            self.thumbs.request(path)
        count = len(rows)
        plural = noun.replace("author or series", "authors and series") if count != 1 else noun
        self.count_label.setText(f"{count} {plural if plural != noun else noun + ('s' if count != 1 else '')}" if count else "")
        self._update_crumbs()
        if not rows:
            self._show_empty("NOTHING MATCHES" if query else "NOTHING HERE")
            return
        self.empty.hide()
        self.grid.show()
        self.grid.setCurrentIndex(self.model.index(0, 0))

    def _on_thumb(self, generation: int, path: str, image: QImage) -> None:
        if generation != self.thumbs.generation or image.isNull():
            return
        for item in self._by_path.get(path, []) + getattr(self, "_list_covers", {}).get(path, []):
            try:
                item.setData(image, THUMB_ROLE)
            except RuntimeError:
                pass  # the item went away with a refill

    def refresh_progress(self, path: str = "") -> None:
        if self.view_mode == "list":
            self._fill_list()
            return
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            files = item.data(FILES_ROLE) or []
            if item.data(KIND_ROLE) != "author" and (not path or any(os.path.normcase(f) == os.path.normcase(path) for f in files)):
                item.setData(self._progress(files), PROGRESS_ROLE)

    def select(self, path: str) -> None:
        """Highlight the tile that holds this file, if the current level shows it."""
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if any(os.path.normcase(f) == os.path.normcase(path) for f in item.data(FILES_ROLE) or []):
                index = item.index()
                self.grid.setCurrentIndex(index)
                self.grid.scrollTo(index, QAbstractItemView.PositionAtCenter)
                return

    # -------------------------------------------------------------- navigation

    def _update_crumbs(self) -> None:
        names = ["LIBRARY"]
        if self.level and self.authors:
            try:
                names.append(self.authors[self.level[0]].name.upper())
                if len(self.level) == 2:
                    names.append(self.authors[self.level[0]].works[self.level[1]].title.upper())
            except IndexError:
                pass
        if self.filter.text():
            names = ["LIBRARY", "SEARCH"]
        link = theme.GREY_DIM.name()
        parts = []
        for depth, name in enumerate(names):
            text = html.escape(name if len(name) <= 42 else name[:40] + "…")
            if depth < len(names) - 1:
                parts.append(f'<a href="{depth}" style="color:{link}; text-decoration:none">{text}</a>')
            else:
                parts.append(text)
        self.crumbs.setText(f' <span style="color:{link}">›</span> '.join(parts))

    def _crumb(self, href: str) -> None:
        depth = int(href)
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.blockSignals(False)
        self.level = self.level[:depth]
        self._fill()

    def go_up(self) -> bool:
        if self.filter.text():
            self.filter.clear()
            return True
        if not self.level:
            return False
        came_from = self.level
        self.level = self.level[:-1]
        self._fill()
        for row in range(self.model.rowCount()):  # land on the tile we came out of
            key = self.model.item(row).data(KEY_ROLE)
            if isinstance(key, (tuple, list)) and tuple(key) == came_from[:len(key)] and len(key) == len(self.level) + 1:
                self.grid.setCurrentIndex(self.model.index(row, 0))
                break
        return True

    def enter_author_of(self, path: str) -> None:
        """Show the works of whoever wrote this file (used when coming back from reading)."""
        if self.view_mode == "list":
            return
        for a, author in enumerate(self.authors):
            for w, work in enumerate(author.works):
                if any(os.path.normcase(v.path) == os.path.normcase(path) for v in work.variants):
                    if self.level == () or self.level[0] != a:
                        self.level = (a,) if len(work.variants) == 1 or len(self.level) < 2 else (a, w)
                        self._fill()
                    self.select(path)
                    return

    def eventFilter(self, watched, event) -> bool:
        if watched is self.grid and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Backspace:
            return self.go_up()
        return super().eventFilter(watched, event)

    def _activate(self, index: QModelIndex) -> None:
        kind, key = index.data(KIND_ROLE), index.data(KEY_ROLE)
        if kind == "author":
            self.level = (key[0],)
            self.filter.blockSignals(True)
            self.filter.clear()
            self.filter.blockSignals(False)
            self._fill()
        elif kind == "work":
            a, w = key
            work = self.authors[a].works[w]
            if len(work.variants) == 1:
                self.openRequested.emit(work.variants[0].path)
            else:
                self.filter.blockSignals(True)
                self.filter.clear()
                self.filter.blockSignals(False)
                self.level = (a, w)
                self._fill()
        elif kind == "variant":
            self.openRequested.emit(key)

    def _add_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Add a folder…", self.addFolderRequested.emit)
        menu.addAction("Add files…", self.addFilesRequested.emit)
        menu.exec(self.add_button.mapToGlobal(QPoint(0, self.add_button.height())))

    def _context_menu(self, pos) -> None:
        index = self.grid.indexAt(pos)
        if not index.isValid():
            return
        self.grid.setCurrentIndex(index)
        self._menu_for(index.data(KIND_ROLE), index.data(KEY_ROLE), self.grid.viewport().mapToGlobal(pos),
                       lambda: self._activate(index))

    def _menu_for(self, kind, key, where, enter) -> None:
        menu = QMenu(self)
        if kind == "author":
            folder = self.authors[key[0]].folder
            menu.addAction("Show works", enter)
            menu.addAction("Show in Explorer", lambda: reveal(folder))
        else:
            if kind == "work":
                work = self.authors[key[0]].works[key[1]]
                path, folder = work.best().path, work.folder
                menu.addAction("Open", lambda: self.openRequested.emit(path))
                if len(work.variants) > 1:
                    menu.addAction("Show versions", enter)
            else:
                path, folder = key, key
                menu.addAction("Open", lambda: self.openRequested.emit(path))
            if kind == "variant" or len(self.authors[key[0]].works[key[1]].variants) == 1:
                menu.addAction("Details…", lambda: self.detailsRequested.emit(path))
            if documents.kind_of(path) == "comic":
                menu.addSeparator()
                menu.addAction("Make a smaller copy…", lambda: self.shrinkRequested.emit(path))
            menu.addSeparator()
            menu.addAction("Show in Explorer", lambda: reveal(folder))
        menu.exec(where)

    # -------------------------------------------------------------- import status

    def show_import(self, done: int, total: int, text: str) -> None:
        self.import_row.show()
        self.import_stop.show()
        self.import_bar.setRange(0, max(1, total))
        self.import_bar.setValue(done)
        self.import_bar.setVisible(total > 0)
        self.import_label.setText(text.upper())

    def end_import(self, text: str) -> None:
        self.import_stop.hide()
        self.import_bar.hide()
        self.import_label.setText(text.upper())
        self.import_row.setVisible(bool(text))

    # -------------------------------------------------------------- misc

    def _show_empty(self, text: str) -> None:
        self.empty.setText(text)
        self.grid.hide()
        self.empty.show()

    def focus_grid(self) -> None:
        self.grid.setFocus()


def reveal(path: str) -> None:
    """Show a file or folder in Explorer."""
    import subprocess
    import sys

    if sys.platform == "win32":
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(path)  # type: ignore[attr-defined]
