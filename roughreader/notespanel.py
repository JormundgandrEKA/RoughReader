"""The Contents drop-down: the book's contents (or its printed contents page, made navigable),
its bookmarks and its highlights. Click an entry to go there."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import annotations, theme

TABS = (("contents", "Contents"), ("bookmarks", "Bookmarks"), ("highlights", "Highlights"))
TARGET_ROLE = Qt.UserRole + 1
ENTRY_ROLE = Qt.UserRole + 2


def _dot(colour: str) -> QIcon:
    pix = QPixmap(14, 14)
    pix.fill(QColor(annotations.COLOURS.get(colour, "#F2D94E")))
    return QIcon(pix)


class NotesPanel(QFrame):
    goTo = Signal(int, float)            # page, y fraction
    removeBookmark = Signal(object)
    removeHighlight = Signal(object)
    setPrinted = Signal(int)             # 'this page is printed page N'
    findContents = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("hotkeys")
        self.closed_at = 0.0
        self.tab = "contents"
        self.tab_buttons: dict[str, QPushButton] = {}
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        for key, label in TABS:
            button = QPushButton(label.upper())
            button.setCheckable(True)
            button.setProperty("choice", True)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tab_buttons[key] = button
            tabs.addWidget(button)
        tabs.addStretch(1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setIndentation(theme.u(14))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemActivated.connect(self._activate)
        self.tree.itemClicked.connect(self._activate)
        self.tree.setStyleSheet(f"QTreeWidget {{ background: {theme.FELD_DARK.name()}; border: none; }}"
                                f"QTreeWidget::item {{ padding: {theme.u(4)}px 0; }}"
                                f"QTreeWidget::item:selected {{ background: {theme.BURG.name()}; color: {theme.ON_ACCENT.name()}; }}")

        self.note = QLabel("")
        self.note.setObjectName("dim")
        self.note.setWordWrap(True)
        self.find_button = QPushButton("FIND THE CONTENTS PAGE")
        self.find_button.clicked.connect(self.findContents)
        self.printed = QSpinBox()
        self.printed.setRange(-50, 5000)
        self.set_button = QPushButton("SET")
        self.set_button.clicked.connect(lambda: self.setPrinted.emit(self.printed.value()))
        fix = QHBoxLayout()
        fix.setSpacing(8)
        self.fix_label = QLabel("This page is printed page")
        self.fix_label.setObjectName("dim")
        fix.addWidget(self.fix_label)
        fix.addWidget(self.printed)
        fix.addWidget(self.set_button)
        fix.addStretch(1)
        self.fix_row = QWidget()
        self.fix_row.setLayout(fix)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.u(14), theme.u(12), theme.u(14), theme.u(12))
        layout.setSpacing(theme.u(8))
        layout.addLayout(tabs)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.note)
        layout.addWidget(self.find_button)
        layout.addWidget(self.fix_row)
        self.data = {}

    # ------------------------------------------------------------------ content

    def populate(self, data: dict) -> None:
        """data: toc [(level, title, page)], detected {entries, method}, bookmarks [(entry, page, label)],
        highlights [(entry, page, text, colour)], fixed (bool), current page (int), label (printed label)."""
        self.data = data
        self.show_tab(self.tab)

    def show_tab(self, key: str) -> None:
        self.tab = key
        for name, button in self.tab_buttons.items():
            button.setChecked(name == key)
        self.tree.clear()
        data = self.data
        self.find_button.hide()
        self.fix_row.hide()
        self.note.setText("")
        if key == "contents":
            toc = data.get("toc") or []
            if toc:
                parents: list[QTreeWidgetItem] = []
                for level, title, page in toc:
                    item = QTreeWidgetItem([title, str(page + 1)])
                    item.setData(0, TARGET_ROLE, (page, 0.0))
                    item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    while len(parents) >= level:
                        parents.pop()
                    (parents[-1].addChild(item) if parents else self.tree.addTopLevelItem(item))
                    parents.append(item)
                self.tree.expandToDepth(0)
                self.note.setText("The book's own table of contents.")
            else:
                detected = data.get("detected")
                if detected is None and data.get("fixed"):
                    self.note.setText("This book has no linked contents. RoughReader can look for its printed contents page"
                                      " and work out which page each entry is on.")
                    self.find_button.setVisible(not data.get("searching"))
                    if data.get("searching"):
                        self.note.setText("Looking for the printed contents page…")
                elif detected is not None:
                    entries = detected.get("entries") or []
                    for entry in entries:
                        item = QTreeWidgetItem([entry["title"], entry["printed"]])
                        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                        if entry.get("page") is not None:
                            item.setData(0, TARGET_ROLE, (int(entry["page"]), 0.0))
                            item.setToolTip(0, f"Page {int(entry['page']) + 1} of the file")
                        else:
                            item.setForeground(0, theme.GREY_DIM)
                            item.setToolTip(0, "This page number could not be placed in the file")
                        self.tree.addTopLevelItem(item)
                    if entries:
                        method = detected.get("method") or "nothing"
                        self.note.setText(f"From the printed contents page; page numbers placed by {method}."
                                          " If they are off, go to a page and say which printed page it is:")
                        self.fix_row.show()
                        self.printed.setValue(max(1, int(data.get("printed_guess") or 1)))
                    else:
                        self.note.setText("No printed contents page was found in the first pages.")
                        self.find_button.show()
                        self.find_button.setText("LOOK AGAIN")
                else:
                    self.note.setText("This book has no contents.")
        elif key == "bookmarks":
            for entry, page, label in data.get("bookmarks", []):
                item = QTreeWidgetItem([label or "(no text)", str(page + 1)])
                item.setData(0, TARGET_ROLE, (page, 0.0))
                item.setData(0, ENTRY_ROLE, entry)
                item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                self.tree.addTopLevelItem(item)
            self.note.setText("Ctrl+B bookmarks the page you are on. Right-click an entry to remove it."
                              if data.get("bookmarks") else "No bookmarks yet. Ctrl+B bookmarks the page you are on.")
        else:
            for entry, page, text, colour in data.get("highlights", []):
                item = QTreeWidgetItem([text if len(text) < 220 else text[:217] + "…", str(page + 1)])
                item.setIcon(0, _dot(colour))
                item.setData(0, TARGET_ROLE, (page, entry.get("y", 0.0)))
                item.setData(0, ENTRY_ROLE, entry)
                item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                self.tree.addTopLevelItem(item)
            self.note.setText("Select text and press Ctrl+H (or use the bar that appears) to highlight it."
                              if not data.get("highlights") else "Right-click a highlight to remove it.")
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)

    def _activate(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        target = item.data(0, TARGET_ROLE)
        if target:
            self.goTo.emit(int(target[0]), float(target[1]))
            self.close()

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        entry = item.data(0, ENTRY_ROLE) if item is not None else None
        if entry is None:
            return
        menu = QMenu(self)
        if self.tab == "bookmarks":
            menu.addAction("Remove bookmark", lambda: self.removeBookmark.emit(entry))
        else:
            menu.addAction("Remove highlight", lambda: self.removeHighlight.emit(entry))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ showing

    def open(self, anchor: QWidget | None, window: QWidget) -> None:
        screen = (anchor or window).screen().availableGeometry().adjusted(8, 8, -8, -8)
        width = min(theme.u(520), screen.width())
        height = min(round(window.height() * 0.8), screen.height())
        if anchor is not None and anchor.isVisible():
            corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 6))
            x, y = corner.x() - width, corner.y()
        else:
            centre = window.mapToGlobal(window.rect().center())
            x, y = centre.x() - width // 2, centre.y() - height // 2
        x = max(screen.left(), min(x, screen.right() - width + 1))
        y = max(screen.top(), min(y, screen.bottom() - height + 1))
        self.setGeometry(x, y, width, height)
        self.show()
        self.tree.setFocus()
        self.show_tab(self.tab)

    def hideEvent(self, event) -> None:
        self.closed_at = time.monotonic()
        super().hideEvent(event)
