"""Small custom-painted controls."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from . import theme


class IconButton(QAbstractButton):
    """Flat angular button: an icon, optionally with an upper-case label."""

    def __init__(self, icon: str, tip: str = "", text: str = "", checkable: bool = False, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._label = text.upper()
        self._font = theme.font(8.5, bold=True, spacing=1.2)
        self.setCheckable(checkable)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self.update()

    def sizeHint(self) -> QSize:
        width = 44
        if self._label:
            width += QFontMetrics(self._font).horizontalAdvance(self._label) + 14
        return QSize(width, 40)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        active = self.isChecked() or self.isDown()
        if active or self.underMouse():
            p.setPen(Qt.NoPen)
            if active:
                p.setBrush(theme.BURG_HI if self.underMouse() else theme.BURG)
            else:
                p.setBrush(theme.FELD_LIGHT)
            p.drawPolygon(theme.chamfer(rect, 9))
        elif theme.BORDERS:
            pen = QPen(theme.FELD_LIGHT, theme.BORDER)
            pen.setJoinStyle(Qt.MiterJoin)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(theme.chamfer(rect.adjusted(1, 1, -1, -1), 9))
        colour = theme.ON_ACCENT if active else (theme.GREY if self.isEnabled() else theme.FELD_LIGHT)
        icon_rect = QRectF(rect.left() + 10, rect.center().y() - 11, 22, 22)
        theme.draw_icon(p, self._icon, icon_rect, colour, 1.9)
        if self._label:
            p.setFont(self._font)
            p.setPen(colour)
            text_rect = QRectF(icon_rect.right() + 10, rect.top(), rect.width(), rect.height())
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self._label)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class SeekBar(QWidget):
    """Page slider. Runs right-to-left when the book does."""

    moved = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._max = 0
        self._value = 0
        self._rtl = False
        self._drag = False
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

    def set_state(self, value: int, maximum: int, rtl: bool) -> None:
        self._max, self._rtl = max(0, maximum), rtl
        self._value = max(0, min(value, self._max))
        self.update()

    def _span(self) -> tuple[float, float]:
        return 12.0, max(13.0, self.width() - 12.0)

    def _x_of(self, value: int) -> float:
        left, right = self._span()
        frac = value / self._max if self._max else 0.0
        return right - frac * (right - left) if self._rtl else left + frac * (right - left)

    def _value_at(self, x: float) -> int:
        left, right = self._span()
        frac = min(1.0, max(0.0, (x - left) / (right - left)))
        if self._rtl:
            frac = 1.0 - frac
        return round(frac * self._max)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        left, right = self._span()
        y = self.height() / 2.0
        track = theme.SEEK_TRACK
        p.fillRect(QRectF(left, y - track / 2, right - left, track), theme.FELD_LIGHT if theme.BORDERS else theme.FELD_DEEP)
        x = self._x_of(self._value)
        start = right if self._rtl else left
        fill = theme.SEEK_FILL
        p.fillRect(QRectF(min(start, x), y - fill / 2, abs(x - start), fill), theme.BURG)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.GREY)
        p.drawPolygon(QPolygonF([QPointF(x, y - 11), QPointF(x + 7, y), QPointF(x, y + 11), QPointF(x - 7, y)]))

    def _seek(self, x: float) -> None:
        value = self._value_at(x)
        if value != self._value:
            self._value = value
            self.update()
            self.moved.emit(value)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._max:
            self._drag = True
            self._seek(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._drag:
            self._seek(event.position().x())

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = False


class SelectionBar(QWidget):
    """The small bar that appears over selected text: copy it, or highlight it in a colour."""

    copyRequested = Signal()
    highlightRequested = Signal(str)

    def __init__(self, colours: dict[str, str], parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout, QPushButton

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("selectionbar")
        self.setStyleSheet(f"QWidget#selectionbar {{ background: {theme.FELD_DARK.name()}; border: {theme.BORDER}px solid {theme.FELD_LIGHT.name()}; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(6)
        copy = QPushButton("COPY")
        copy.setFocusPolicy(Qt.NoFocus)
        copy.clicked.connect(self.copyRequested)
        row.addWidget(copy)
        for name, colour in colours.items():
            dot = QPushButton("")
            dot.setFocusPolicy(Qt.NoFocus)
            dot.setToolTip(f"Highlight ({name})")
            dot.setFixedSize(theme.u(26), theme.u(26))
            dot.setStyleSheet(f"QPushButton {{ background: {colour}; border: 2px solid {theme.FELD_LIGHT.name()}; min-width: 0; padding: 0; }}"
                              f"QPushButton:hover {{ border-color: {theme.GREY.name()}; }}")
            dot.clicked.connect(lambda _=False, n=name: self.highlightRequested.emit(n))
            row.addWidget(dot)
        self.hide()

    def show_at(self, point) -> None:
        self.adjustSize()
        parent = self.parentWidget()
        x = max(4, min(point.x() - self.width() // 2, parent.width() - self.width() - 4))
        y = point.y() + 18
        if y + self.height() > parent.height() - 4:
            y = point.y() - self.height() - 18
        self.move(x, max(4, y))
        self.show()
        self.raise_()
