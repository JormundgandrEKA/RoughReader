"""The Details dialog: everything found out about one file, where each piece came from, and room to
correct it. Saving renames the library copy to 'Title, Surname, Publisher, Year' and files it under
its author and work; values set here are never overwritten by a later automatic reading."""

from __future__ import annotations

import datetime
import os
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import analyze, documents, organize, theme

FIELDS = (
    ("title", "Title"), ("subtitle", "Subtitle"), ("authors", "Author(s)"), ("translators", "Translator(s)"),
    ("editors", "Editor(s)"), ("publisher", "Publisher"), ("year", "Published"), ("original_year", "First published"),
    ("edition", "Edition"), ("isbn", "ISBN"), ("language", "Language"), ("series", "Series"),
)
LISTS = {"authors", "translators", "editors"}


def human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") or size >= 100 else f"{size:.1f} {unit}"
        size /= 1000
    return ""


class DetailsDialog(QDialog):
    _analysed = Signal(object)

    def __init__(self, path: str, details: analyze.Details, source: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Details")
        self.setMinimumWidth(theme.u(700))
        self.path = path
        self.details = details
        self.result_details: analyze.Details | None = None
        self._analysed.connect(self._on_analysed)

        title = QLabel("DETAILS")
        title.setFont(theme.font(14, bold=True, spacing=4))
        rule = QWidget()
        rule.setFixedHeight(theme.RULE)
        rule.setObjectName("rule")
        rule.setAttribute(Qt.WA_StyledBackground, True)
        self.file_label = QLabel(os.path.basename(path))
        self.file_label.setFont(theme.font(10, bold=True))
        self.file_label.setWordWrap(True)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        facts = [details.format or documents.format_name(path), human_size(size)]
        if details.pages:
            facts.append(f"{details.pages} pages")
        if details.used_ocr:
            facts.append("scanned (read by OCR)")
        if details.source:
            facts.append(f"from {details.source}")
        self.facts = QLabel("  ·  ".join(f for f in facts if f))
        self.facts.setObjectName("dim")
        self.source = QLabel(f"Copied from: {source}" if source else "")
        self.source.setObjectName("dim")
        self.source.setWordWrap(True)
        self.source.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet(f"background: {theme.BURG.name()}; color: {theme.ON_ACCENT.name()}; padding: {theme.u(8)}px;")

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.u(10))
        grid.setVerticalSpacing(theme.u(6))
        self.edits: dict[str, QLineEdit] = {}
        self.origins: dict[str, QLabel] = {}
        for row, (key, label) in enumerate(FIELDS):
            caption = QLabel(label.upper())
            caption.setObjectName("dim")
            caption.setFont(theme.font(8.5, bold=True, spacing=2))
            edit = QLineEdit()
            edit.textChanged.connect(self._preview)
            origin = QLabel("")
            origin.setObjectName("dim")
            origin.setFont(theme.font(8))
            grid.addWidget(caption, row, 0)
            grid.addWidget(edit, row, 1)
            grid.addWidget(origin, row, 2)
            self.edits[key], self.origins[key] = edit, origin
        grid.setColumnStretch(1, 1)
        form = QWidget()
        form.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setFont(theme.font(9.5, bold=True))
        self.status = QLabel("")
        self.status.setObjectName("dim")

        again = QPushButton("READ THE BOOK AGAIN")
        again.setAutoDefault(False)
        again.clicked.connect(self._read_again)
        cancel = QPushButton("CANCEL")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        save = QPushButton("SAVE")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons = QHBoxLayout()
        buttons.addWidget(again)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.u(22), theme.u(18), theme.u(22), theme.u(16))
        layout.setSpacing(theme.u(10))
        layout.addWidget(title)
        layout.addWidget(rule)
        layout.addWidget(self.file_label)
        layout.addWidget(self.facts)
        layout.addWidget(self.source)
        layout.addWidget(self.warnings)
        layout.addWidget(scroll, 1)
        layout.addWidget(self.preview)
        layout.addWidget(self.status)
        layout.addLayout(buttons)
        self._fill(details)
        self.resize(theme.u(760), theme.u(720))

    def _fill(self, details: analyze.Details) -> None:
        for key, edit in self.edits.items():
            value = getattr(details, key)
            edit.setText(", ".join(value) if key in LISTS else str(value or ""))
            origin = details.origins.get(key, "")
            self.origins[key].setText(("set by you" if details.manual else origin) if (value and origin) or details.manual and value else "")
        self.warnings.setText("\n".join("⚠  " + w for w in details.warnings))
        self.warnings.setVisible(bool(details.warnings) and not details.manual)
        self._preview()

    def _collect(self) -> analyze.Details:
        values = analyze.Details.from_dict(self.details.as_dict())
        for key, edit in self.edits.items():
            text = edit.text().strip()
            if key in LISTS:
                setattr(values, key, [n.strip() for n in text.split(",") if n.strip()] if text else [])
            else:
                setattr(values, key, text)
        values.isbn = values.isbn.replace("-", "").replace(" ", "")
        return values

    def _preview(self, *_args) -> None:
        values = self._collect()
        kind = documents.kind_of(self.path)
        name = analyze.file_name(values, documents.extension(self.path), analyze._tag(self.path), kind)
        folder = values.series if kind == "comic" and values.series else (values.author or organize.UNKNOWN_AUTHOR)
        self.preview.setText(f"Saved as:  {folder}  ›  {values.title}  ›  {name}")

    def _read_again(self) -> None:
        self.status.setText("Reading the title and copyright pages…")

        def work():
            try:
                found = analyze.analyse(self.path, hints={"author": self.details.author, "title": self.details.title})
            except Exception as exc:  # noqa: BLE001 - shown to the user
                found = exc
            try:
                self._analysed.emit(found)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_analysed(self, found) -> None:
        if isinstance(found, Exception):
            self.status.setText(f"Could not read it: {found}")
            return
        self.details = found
        self._fill(found)
        self.status.setText("Read again. Nothing is changed until you save.")

    def _save(self) -> None:
        values = self._collect()
        problems = []
        if not values.title:
            problems.append("A title is needed.")
        now = datetime.date.today().year
        for key in ("year", "original_year"):
            value = getattr(values, key)
            if value and not (value.isdigit() and 1400 <= int(value) <= now):
                problems.append(f"“{value}” is not a year between 1400 and {now}.")
        if problems:
            QMessageBox.warning(self, "Details", "\n".join(problems))
            return
        if values.isbn and not analyze.isbn_valid(values.isbn):
            answer = QMessageBox.question(self, "Details", f"{values.isbn} is not a valid ISBN (its check digit is wrong). Save it anyway?")
            if answer != QMessageBox.Yes:
                return
        if values.isbn and analyze.isbn_valid(values.isbn):
            values.isbn = analyze.to_isbn13(values.isbn)
        values.manual = True
        values.warnings = []
        self.result_details = values
        self.accept()
