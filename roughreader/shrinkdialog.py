"""The Shrink dialog: pick a screen size and format, see the likely result, write a smaller copy."""

from __future__ import annotations

import io
import os
import threading

from PIL import Image
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from . import archive, shrink, theme

def megabytes(size: float) -> str:
    return f"{size / 1e6:,.0f} MB" if size >= 9.5e6 else f"{size / 1e6:.1f} MB"


def _caption(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("dim")
    label.setFont(theme.font(8.5, bold=True, spacing=2.4))
    label.setFixedWidth(theme.u(104))
    return label


class ShrinkDialog(QDialog):
    openRequested = Signal(str)
    _estimated = Signal(int, int, str)
    _progressed = Signal(int, int)
    _finished = Signal(object, str)

    def __init__(self, source: str, store=None, parent=None, default_folder: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Shrink")
        self.setMinimumWidth(theme.u(640))
        self.source = source
        self.store = store
        self._saved = (store.get("shrink") if store is not None else None) or {}
        self._cancel = threading.Event()
        self._running = False
        self._closing = False
        self._ticket = 0
        self._output_edited = False
        self._result = None
        self._source_bytes = os.path.getsize(source) if os.path.isfile(source) else 0
        self._pages, self._page_size = self._inspect()

        title = QLabel("SHRINK")
        title.setFont(theme.font(14, bold=True, spacing=4))
        name = QLabel(archive.display_name(source))
        name.setFont(theme.font(11, bold=True))
        self.facts = QLabel(self._facts())
        self.facts.setObjectName("dim")

        self.preset_buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        row.setSpacing(6)
        for item in shrink.PRESETS:
            button = self._choice(item.label)
            button.clicked.connect(lambda _=False, i=item.id: self._pick_preset(i))
            self.preset_buttons[item.id] = button
            row.addWidget(button)
        row.addStretch(1)
        self.preset_note = QLabel("")
        self.preset_note.setObjectName("dim")

        self.format_buttons: dict[str, QPushButton] = {}
        frow = QHBoxLayout()
        frow.setSpacing(6)
        for fmt in shrink.FORMATS:
            button = self._choice(fmt.upper())
            button.clicked.connect(lambda _=False, f=fmt: self._pick_format(f))
            self.format_buttons[fmt] = button
            frow.addWidget(button)
        frow.addStretch(1)
        self.format_note = QLabel("")
        self.format_note.setObjectName("dim")

        self.height_box = QSpinBox()
        self.height_box.setRange(shrink.MIN_HEIGHT, shrink.MAX_HEIGHT)
        self.height_box.setSingleStep(50)
        self.height_box.setSuffix(" px")
        self.quality_box = QSpinBox()
        self.quality_box.setRange(1, 100)
        self.height_box.valueChanged.connect(self._tuned)
        self.quality_box.valueChanged.connect(self._tuned)
        tune = QHBoxLayout()
        tune.setSpacing(10)
        tune.addWidget(self.height_box)
        quality_label = QLabel("QUALITY")
        quality_label.setObjectName("dim")
        quality_label.setFont(theme.font(8.5, bold=True, spacing=2.4))
        tune.addSpacing(14)
        tune.addWidget(quality_label)
        tune.addWidget(self.quality_box)
        tune.addStretch(1)

        self.output = QLineEdit()
        self.output.textEdited.connect(self._output_typed)
        self.browse = QPushButton("BROWSE")
        self.browse.clicked.connect(self._browse)
        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        out_row.addWidget(self.output, 1)
        out_row.addWidget(self.browse)

        self.estimate = QLabel("")
        self.estimate.setFont(theme.font(10.5, bold=True))
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("dim")
        self.status.setWordWrap(True)

        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.go_button = QPushButton("SHRINK")
        self.go_button.setDefault(True)
        self.go_button.clicked.connect(self._go)
        self.open_button = QPushButton("OPEN COPY")
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._open_copy)
        self.open_button.hide()
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.go_button)

        def line(caption: str, content) -> QHBoxLayout:
            box = QHBoxLayout()
            box.setSpacing(10)
            box.addWidget(_caption(caption), 0, Qt.AlignTop)
            if isinstance(content, QWidget):
                box.addWidget(content, 1)
            else:
                box.addLayout(content, 1)
            return box

        rule = QWidget()
        rule.setFixedHeight(theme.RULE)
        rule.setObjectName("rule")
        rule.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(rule)
        layout.addSpacing(4)
        layout.addWidget(name)
        layout.addWidget(self.facts)
        layout.addSpacing(8)
        layout.addLayout(line("Screen", row))
        layout.addLayout(line("", self.preset_note))
        layout.addLayout(line("Format", frow))
        layout.addLayout(line("", self.format_note))
        layout.addLayout(line("Page height", tune))
        layout.addLayout(line("Save as", out_row))
        layout.addSpacing(6)
        layout.addWidget(self.estimate)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        layout.addSpacing(4)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(450)
        self._timer.timeout.connect(self._start_estimate)
        self._estimated.connect(self._on_estimate)
        self._progressed.connect(self._on_progress)
        self._finished.connect(self._on_finished)

        self._preset = self._saved.get("preset") if self._saved.get("preset") in self.preset_buttons else shrink.DEFAULT_PRESET
        self._fmt = self._saved.get("format") if self._saved.get("format") in shrink.FORMATS else "webp"
        self._default_folder = default_folder
        self._folder = default_folder or (self._saved.get("folder") if os.path.isdir(self._saved.get("folder") or "") else "")
        self._pick_preset(self._preset, estimate=False)
        self._pick_format(self._fmt, estimate=False)
        self._start_estimate()

    @property
    def result_path(self) -> str:
        return self._result.path if self._result is not None else ""

    # -------------------------------------------------------------- inputs

    @staticmethod
    def _choice(text: str) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setProperty("choice", True)
        button.setFocusPolicy(Qt.TabFocus)
        return button

    def _inspect(self) -> tuple[int, tuple[int, int]]:
        try:
            book = archive.Book(self.source)
        except archive.BookError:
            return 0, (0, 0)
        try:
            with Image.open(io.BytesIO(book.read(0))) as image:
                return len(book), image.size
        except Exception:
            return len(book), (0, 0)
        finally:
            book.close()

    def _facts(self) -> str:
        parts = []
        if self._pages:
            parts.append(f"{self._pages} pages")
        if self._source_bytes:
            parts.append(megabytes(self._source_bytes))
        if self._page_size[0]:
            parts.append(f"{self._page_size[0]} × {self._page_size[1]} px pages")
        return "  ·  ".join(parts)

    def _pick_preset(self, preset_id: str, estimate: bool = True) -> None:
        item = shrink.preset(preset_id)
        self._preset = item.id
        for key, button in self.preset_buttons.items():
            button.setChecked(key == item.id)
        self.preset_note.setText(item.screen)
        self._set_boxes(item.height, shrink.quality_for(item, self._fmt))
        self._after_change(estimate)

    def _pick_format(self, fmt: str, estimate: bool = True) -> None:
        self._fmt = fmt
        for key, button in self.format_buttons.items():
            button.setChecked(key == fmt)
        self.format_note.setText(
            "Smallest files and quick to open. Fewer other readers understand AVIF." if fmt == "avif"
            else "Opens almost everywhere, in any comic reader."
        )
        if self._preset:
            self._set_boxes(self.height_box.value(), shrink.quality_for(shrink.preset(self._preset), fmt))
        else:  # a hand-tuned quality does not carry over between formats
            self._set_boxes(self.height_box.value(), 50 if fmt == "avif" else 80)
        self._after_change(estimate)

    def _set_boxes(self, height: int, quality: int) -> None:
        for box, value in ((self.height_box, height), (self.quality_box, quality)):
            box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(False)

    def _tuned(self) -> None:
        """Height or quality typed by hand: no longer one of the presets, unless it matches one."""
        self._preset = ""
        for item in shrink.PRESETS:
            if item.height == self.height_box.value() and shrink.quality_for(item, self._fmt) == self.quality_box.value():
                self._preset = item.id
        for key, button in self.preset_buttons.items():
            button.setChecked(key == self._preset)
        self.preset_note.setText(shrink.preset(self._preset).screen if self._preset else "Custom")
        self._after_change(True)

    def _after_change(self, estimate: bool) -> None:
        if not self._output_edited:
            self.output.setText(shrink.default_output(self.source, self._preset or f"{self.height_box.value()}px", self._folder))
        if self._page_size[1] and self._page_size[1] <= self.height_box.value():
            self.status.setText("The pages are already this size or smaller; they keep their size and are only re-encoded.")
        elif not self._running:
            self.status.setText("")
        if estimate:
            self.estimate.setText("ESTIMATING…")
            self._timer.start()

    def _output_typed(self, _text: str) -> None:
        self._output_edited = True

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save the smaller copy as", self.output.text(), "Comic archive (*.cbz)")
        if path:
            self.output.setText(path if path.lower().endswith(".cbz") else path + ".cbz")
            self._output_edited = True

    # -------------------------------------------------------------- estimate

    def _start_estimate(self) -> None:
        self._ticket += 1
        ticket, height, fmt, quality = self._ticket, self.height_box.value(), self._fmt, self.quality_box.value()

        def work() -> None:
            try:
                size, error = shrink.estimate(self.source, height, fmt, quality), ""
            except Exception as exc:
                size, error = 0, str(exc)
            try:
                self._estimated.emit(ticket, size, error)
            except RuntimeError:  # dialog closed meanwhile
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_estimate(self, ticket: int, size: int, error: str) -> None:
        if ticket != self._ticket or self._running:
            return
        if error:
            self.estimate.setText("")
            self.status.setText(error)
        elif self._source_bytes:
            self.estimate.setText(f"ABOUT {megabytes(size).upper()}  ·  {size / self._source_bytes:.0%} OF THE ORIGINAL")
        else:
            self.estimate.setText(f"ABOUT {megabytes(size).upper()}")

    # -------------------------------------------------------------- running

    def _set_busy(self, busy: bool) -> None:
        self._running = busy
        for widget in (*self.preset_buttons.values(), *self.format_buttons.values(), self.height_box,
                       self.quality_box, self.output, self.browse, self.go_button):
            widget.setEnabled(not busy)

    def _go(self) -> None:
        path = self.output.text().strip()
        if not path:
            self.status.setText("Choose where to save the copy.")
            return
        if not path.lower().endswith(".cbz"):
            path += ".cbz"
            self.output.setText(path)
        if os.path.exists(path):
            self.status.setText("A file with that name already exists. Pick another name.")
            return
        self._remember()
        self._cancel.clear()
        self._timer.stop()
        self._ticket += 1  # drop any estimate still in flight
        self._set_busy(True)
        self.estimate.setText("SHRINKING…")
        self.status.setText("")
        self.bar.setRange(0, max(1, self._pages))
        self.bar.setValue(0)
        self.bar.show()
        height, fmt, quality = self.height_box.value(), self._fmt, self.quality_box.value()

        def work() -> None:
            result, error = None, ""
            try:
                result = shrink.shrink(self.source, path, height, fmt, quality,
                                       progress=lambda done, total: self._progressed.emit(done, total), cancel=self._cancel)
            except shrink.Cancelled:
                error = "cancelled"
            except Exception as exc:
                error = str(exc) or "Something went wrong."
            try:
                self._finished.emit(result, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        self.bar.setRange(0, total)
        self.bar.setValue(done)
        self.status.setText(f"Page {done} of {total}")

    def _on_finished(self, result, error: str) -> None:
        self._set_busy(False)
        if self._closing:
            self.done(QDialog.Rejected)
            return
        if error:
            self.bar.hide()
            self.estimate.setText("")
            self.status.setText("Stopped. Nothing was written." if error == "cancelled" else error)
            return
        self._result = result
        self.bar.setValue(self.bar.maximum())
        saved = 1 - result.output_bytes / result.source_bytes if result.source_bytes else 0
        self.estimate.setText(f"DONE  ·  {megabytes(result.output_bytes).upper()}  ·  {saved:.0%} SMALLER")
        note = f"Saved as {os.path.basename(result.path)} in {result.seconds:.0f} s."
        if result.kept_original:
            note += f" {result.kept_original} page(s) could not be re-encoded and were copied unchanged."
        self.status.setText(note)
        for widget in (self.preset_buttons, self.format_buttons):
            for button in widget.values():
                button.setEnabled(False)
        for widget in (self.height_box, self.quality_box, self.output, self.browse, self.go_button):
            widget.setEnabled(False)
        self.go_button.hide()
        self.open_button.show()
        self.open_button.setDefault(True)
        self.open_button.setFocus()
        self.cancel_button.setText("CLOSE")

    def _open_copy(self) -> None:
        if self._result is not None:
            self.openRequested.emit(self._result.path)
        self.accept()

    def _remember(self) -> None:
        if self.store is None:
            return
        folder = os.path.dirname(os.path.abspath(self.output.text()))
        usual = (os.path.dirname(os.path.abspath(self.source)), os.path.abspath(self._default_folder or self.source))
        self.store.set("shrink", {
            "preset": self._preset or "", "format": self._fmt,
            "folder": "" if folder in usual else folder,
        })

    def reject(self) -> None:
        """Cancel stops a running job first; the dialog closes when the worker has cleaned up."""
        if self._running:
            self._cancel.set()
            self._closing = True
            self.cancel_button.setEnabled(False)
            self.status.setText("Stopping…")
            return
        super().reject()
