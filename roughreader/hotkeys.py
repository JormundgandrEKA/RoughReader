"""The hotkeys drop-down: every command with its keys drawn as key caps, remappable in place."""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QKeyCombination, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QKeySequence, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from . import keymap, theme

SCALE = theme.TEXT_SCALE   # the panel is laid out at 1x and painted this much larger
HAIR = theme.BORDER / SCALE  # a border, in layout units
PAD = 24
COL_W = 376
COL_GAP = 34
ROW_H = 30
HEAD_H = 40
GROUP_GAP = 14
TITLE_H = 66
FOOT_H = 58
CAP_H = 22
CAP_GAP = 2        # between the caps of one combination
SEQ_GAP = 14       # between alternative keys
RESET_W = 132
LABEL_MIN = 96    # width always kept for a row's label

ARROWS = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1)}
CAP_TEXT = {
    "PgDown": "PG DN", "PgUp": "PG UP", "Backspace": "BKSP", "Return": "ENTER", "Del": "DEL",
    "Ins": "INS", "Esc": "ESC", "Space": "SPACE", "Meta": "WIN",
}
MODIFIER_KEYS = {
    Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr, Qt.Key_CapsLock,
    Qt.Key_NumLock, Qt.Key_ScrollLock, Qt.Key_unknown,
}
HINT = "CLICK A KEY TO REMAP   ·   RIGHT-CLICK TO REMOVE   ·   HOVER A ROW TO ADD"


def normalise(sequence: str) -> str:
    """Canonical spelling of a key combination, '' if it is not one."""
    return QKeySequence(sequence).toString(QKeySequence.PortableText)


def native(sequence: str) -> str:
    """How the combination is written on this system, for tooltips and messages."""
    return QKeySequence(sequence).toString(QKeySequence.NativeText)


class HotkeyPanel(QWidget):
    changed = Signal()

    def __init__(self, keys: keymap.Keymap, parent=None):
        super().__init__(parent)
        self.keymap = keys
        self._capture: tuple[str, int] | None = None
        self._hover = None
        self._hover_row = ""
        self._note = ""
        self._hits: list[tuple[QRectF, tuple]] = []
        self._rows: list[tuple[QRectF, str]] = []
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._font_label = theme.font(10, scaled=False)
        self._font_cap = theme.font(8.5, bold=True, spacing=0.8, scaled=False)
        self._font_head = theme.font(8.5, bold=True, spacing=2.4, scaled=False)
        self._font_title = theme.font(14, bold=True, spacing=4, scaled=False)
        self._cap_metrics = QFontMetricsF(self._font_cap)
        self._label_metrics = QFontMetricsF(self._font_label)

        def group(name: str):
            return name, [c for c in keymap.COMMANDS if c.group == name]

        self._columns = [[group("Pages"), group("Text"), ("Mouse", list(keymap.MOUSE))], [group("View"), group("Window")]]
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:
        tallest = max(
            sum(HEAD_H + len(rows) * ROW_H for _, rows in column) + GROUP_GAP * (len(column) - 1)
            for column in self._columns
        )
        return QSize(round((PAD * 2 + COL_W * 2 + COL_GAP) * SCALE), round((TITLE_H + tallest + FOOT_H) * SCALE))

    def _logical(self) -> tuple[float, float]:
        return self.width() / SCALE, self.height() / SCALE

    def stop(self) -> None:
        """Forget any half-finished remap (the drop-down is closing)."""
        self._capture, self._hover, self._hover_row, self._note = None, None, "", ""

    # -------------------------------------------------------------- paint

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), theme.FELD)
        p.scale(SCALE, SCALE)
        width = self.width() / SCALE
        self._hits.clear()
        self._rows.clear()

        theme.draw_icon(p, "keys", QRectF(PAD, 21, 24, 24), theme.GREY)
        p.setFont(self._font_title)
        p.setPen(theme.GREY)
        p.drawText(QRectF(PAD + 36, 14, 400, 38), Qt.AlignLeft | Qt.AlignVCenter, "HOTKEYS")
        p.fillRect(QRectF(PAD, TITLE_H - 10, width - 2 * PAD, theme.RULE / SCALE), theme.BURG)

        for index, column in enumerate(self._columns):
            x = PAD + index * (COL_W + COL_GAP)
            y = float(TITLE_H)
            for title, rows in column:
                p.setFont(self._font_head)
                p.setPen(theme.GREY_DIM)
                p.drawText(QRectF(x, y, COL_W, HEAD_H - 8), Qt.AlignLeft | Qt.AlignBottom, title.upper())
                p.fillRect(QRectF(x, y + HEAD_H - 3, COL_W, HAIR), theme.FELD_LIGHT)
                y += HEAD_H
                for command in rows:
                    self._paint_row(p, QRectF(x, y, COL_W, ROW_H), command)
                    y += ROW_H
                y += GROUP_GAP
        self._paint_footer(p)

    def _paint_row(self, p: QPainter, rect: QRectF, command: keymap.Command) -> None:
        editable = not command.fixed
        hovered = editable and self._hover_row == command.id
        if editable:
            self._rows.append((rect, command.id))
        if theme.BORDERS:
            p.fillRect(QRectF(rect.left(), rect.bottom() - HAIR, rect.width(), HAIR), theme.FELD_LIGHT)
        if hovered:
            wash = QColor(theme.FELD_LIGHT)
            wash.setAlpha(80)
            p.fillRect(rect.adjusted(-8, 1, 8, -1), wash)
        sequences = list(command.keys if command.scope == "mouse" else self.keymap.keys(command.id))
        capture_slot = self._capture[1] if self._capture and self._capture[0] == command.id else -1
        if capture_slot >= len(sequences):
            sequences.append("")
            capture_slot = len(sequences) - 1

        def measure(compact: bool, count: int):
            built = []
            for slot, sequence in enumerate(sequences[:count]):
                if slot == capture_slot:
                    parts = ["PRESS KEY"]
                elif compact:  # the whole combination on one cap
                    parts = ["+".join(CAP_TEXT.get(part, part).upper() for part in keymap.split_parts(sequence))]
                else:
                    parts = keymap.split_parts(sequence)
                built.append((slot, parts, [self._cap_width(part) for part in parts]))
            if count < len(sequences):
                more = f"+{len(sequences) - count}"
                built.append((-1, [more], [self._cap_width(more)]))
            width = sum(sum(w) + CAP_GAP * (len(w) - 1) for _, _, w in built) + SEQ_GAP * max(0, len(built) - 1)
            return built, width

        # Long custom combinations: first one cap per combination, then show fewer of them.
        limit = rect.width() - LABEL_MIN
        groups, total = measure(False, len(sequences))
        if total > limit:
            groups, total = measure(True, len(sequences))
        shown = len(sequences)
        while total > limit and shown > 1:
            shown -= 1
            groups, total = measure(True, shown)
        x = rect.right() - total
        top = rect.center().y() - CAP_H / 2 - 1

        can_add = editable and capture_slot < 0 and len(sequences) < keymap.MAX_KEYS
        add = None
        if can_add and (hovered or not sequences):  # an unbound command always shows its "+"
            add = QRectF(x - (SEQ_GAP if sequences else 0) - CAP_H, top, CAP_H, CAP_H)
        x_label_limit = add.left() if add is not None else x
        p.setFont(self._font_label)
        p.setPen(theme.GREY if editable else theme.GREY_DIM)
        room = max(40.0, x_label_limit - rect.left() - 14)
        label = self._label_metrics.elidedText(command.label, Qt.ElideRight, room)
        p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, label)
        if add is not None:
            self._cap(p, add, "+", "add-hot" if self._hover == ("add", command.id) else "add")
            self._hits.append((add, ("add", command.id)))

        for slot, parts, widths in groups:
            start = x
            if slot < 0 or not editable:
                state = "fixed"
            elif slot == capture_slot:
                state = "capture"
            elif self._hover == ("key", command.id, slot):
                state = "hot"
            else:
                state = "normal"
            for part, width in zip(parts, widths):
                self._cap(p, QRectF(x, top, width, CAP_H), part, state)
                x += width + CAP_GAP
            x -= CAP_GAP
            if editable and slot >= 0:
                self._hits.append((QRectF(start, top, x - start, CAP_H), ("key", command.id, slot)))
            x += SEQ_GAP

    def _cap_width(self, part: str) -> float:
        if part in ARROWS:
            return 26.0
        text = CAP_TEXT.get(part, part).upper()
        return max(26.0, self._cap_metrics.horizontalAdvance(text) + 16.0)

    def _cap(self, p: QPainter, rect: QRectF, part: str, state: str) -> None:
        """One key cap: a chamfered plate sitting on a darker edge."""
        fill, edge, ink = theme.FELD_DARK, theme.FELD_LIGHT, theme.GREY
        if state == "hot":
            edge = theme.GREY
        elif state == "capture":
            fill, edge, ink = theme.BURG, theme.BURG_HI, theme.ON_ACCENT
        elif state == "fixed":
            fill, ink = theme.FELD, theme.GREY_DIM
        elif state == "add":
            fill, ink = theme.FELD, theme.GREY_DIM
        elif state == "add-hot":
            fill, edge = theme.FELD, theme.GREY
        p.setPen(Qt.NoPen)
        if state not in ("fixed", "add", "add-hot"):
            p.setBrush(theme.FELD_DEEP)
            p.drawPolygon(theme.chamfer(rect.translated(0, 2.5), 5))
        pen = QPen(edge, HAIR)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawPolygon(theme.chamfer(rect, 5))
        if part in ARROWS:
            self._arrow(p, rect.center(), ARROWS[part], ink)
            return
        p.setFont(self._font_cap)
        p.setPen(ink)
        p.drawText(rect, Qt.AlignCenter, CAP_TEXT.get(part, part).upper())

    @staticmethod
    def _arrow(p: QPainter, centre: QPointF, direction: tuple[int, int], colour) -> None:
        dx, dy = direction
        px, py = -dy, dx  # perpendicular
        tip = QPointF(centre.x() + dx * 5.5, centre.y() + dy * 5.5)
        tail = QPointF(centre.x() - dx * 5.5, centre.y() - dy * 5.5)
        pen = QPen(colour, 1.6)
        pen.setJoinStyle(Qt.MiterJoin)
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(tail, tip)
        p.drawPolyline(QPolygonF([
            QPointF(tip.x() - dx * 4 + px * 4, tip.y() - dy * 4 + py * 4), tip,
            QPointF(tip.x() - dx * 4 - px * 4, tip.y() - dy * 4 - py * 4),
        ]))

    def _paint_footer(self, p: QPainter) -> None:
        width, height = self._logical()
        top = height - FOOT_H
        p.fillRect(QRectF(PAD, top + 6, width - 2 * PAD, HAIR), theme.FELD_LIGHT)
        p.setFont(self._font_head)
        p.setPen(theme.GREY if self._note else theme.GREY_DIM)
        text_rect = QRectF(PAD, top + 7, width - 2 * PAD - RESET_W - 16, FOOT_H - 7)
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self._note or HINT)

        changed = bool(self.keymap.overrides())
        button = QRectF(width - PAD - RESET_W, top + 18, RESET_W, 28)
        hot = changed and self._hover == ("reset",)
        pen = QPen(theme.GREY if hot else theme.FELD_LIGHT, HAIR)
        pen.setJoinStyle(Qt.MiterJoin)
        p.setPen(pen)
        p.setBrush(theme.BURG if hot else theme.FELD_DARK)
        p.drawPolygon(theme.chamfer(button, 7))
        p.setPen((theme.ON_ACCENT if hot else theme.GREY) if changed else theme.FELD_LIGHT)
        p.drawText(button, Qt.AlignCenter, "RESET ALL")
        if changed:
            self._hits.append((button, ("reset",)))

    # ------------------------------------------------------------- input

    def _target_at(self, pos: QPointF):
        for rect, target in self._hits:
            if rect.contains(pos):
                return target
        return None

    def _label(self, cid: str) -> str:
        return keymap.BY_ID[cid].label.upper()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position() / SCALE
        row = next((cid for rect, cid in self._rows if rect.contains(pos)), "")
        target = self._target_at(pos)
        if (row, target) != (self._hover_row, self._hover):
            self._hover_row, self._hover = row, target
            self.setCursor(Qt.PointingHandCursor if target else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _event) -> None:
        self._hover_row, self._hover = "", None
        self.update()

    def mousePressEvent(self, event) -> None:
        target = self._target_at(event.position() / SCALE)
        left = event.button() == Qt.LeftButton
        if target is None:
            if self._capture is not None:
                self._capture, self._note = None, ""
        elif target[0] == "key":
            _, cid, slot = target
            if event.button() == Qt.RightButton:
                keys = self.keymap.keys(cid)
                if slot < len(keys):
                    self._note = f"{native(keys[slot]).upper()} REMOVED FROM {self._label(cid)}"
                    self.keymap.remove(cid, slot)
                    self._capture = None
                    self.changed.emit()
            elif left:
                self._capture = (cid, slot)
                self._note = f"PRESS A KEY FOR {self._label(cid)}   ·   ESC CANCELS"
        elif target[0] == "add" and left:
            cid = target[1]
            self._capture = (cid, len(self.keymap.keys(cid)))
            self._note = f"PRESS A KEY FOR {self._label(cid)}   ·   ESC CANCELS"
        elif target[0] == "reset" and left:
            self.keymap.reset()
            self._capture, self._note = None, "DEFAULTS RESTORED"
            self.changed.emit()
        self.setFocus()
        self.update()

    def event(self, event) -> bool:
        if self._capture is not None:
            if event.type() == QEvent.ShortcutOverride:
                event.accept()  # keep the key away from shortcuts while remapping
                return True
            if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
                self.keyPressEvent(event)  # Tab would otherwise move focus
                return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if self._capture is None:
            event.ignore()  # lets Esc close the drop-down
            return
        key = event.key()
        if key in MODIFIER_KEYS:
            return
        mods = event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier)
        if key == Qt.Key_Escape and not mods:
            self._capture, self._note = None, ""
            self.update()
            return
        if key == Qt.Key_Backtab:
            key = Qt.Key_Tab
        text = event.text()
        if (mods & Qt.ShiftModifier) and len(text) == 1 and text.isprintable() \
                and not text.isalnum() and not text.isspace():
            mods &= ~Qt.ShiftModifier  # Shift only served to type the symbol
        sequence = QKeySequence(QKeyCombination(mods, Qt.Key(key))).toString(QKeySequence.PortableText)
        if not sequence:
            return
        cid, slot = self._capture
        ok, taken = self.keymap.assign(cid, slot, sequence)
        self._capture = None
        shown = native(sequence).upper()
        if not ok:
            self._note = f"{shown} IS RESERVED"
        elif taken:
            self._note = f"{shown} MOVED TO {self._label(cid)} FROM {self._label(taken)}"
        else:
            self._note = f"{shown} SET FOR {self._label(cid)}"
        if ok:
            self.changed.emit()
        self.update()


class HotkeyPopup(QFrame):
    """Drop-down holding the panel. Closes on Esc or a click outside."""

    def __init__(self, keys: keymap.Keymap, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("hotkeys")
        self.closed_at = 0.0
        self.panel = HotkeyPanel(keys)
        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self.panel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.BORDER, theme.BORDER, theme.BORDER, theme.BORDER)
        layout.addWidget(self._scroll)

    def open(self, anchor: QWidget | None, window: QWidget) -> None:
        """Drop down from `anchor`, or centre on `window` when there is no anchor."""
        size = self.panel.sizeHint()
        screen = (anchor or window).screen().availableGeometry().adjusted(8, 8, -8, -8)
        pad = 2 * theme.BORDER
        height = min(size.height() + pad, screen.height())
        width = size.width() + pad
        if height < size.height() + pad:
            width += self._scroll.verticalScrollBar().sizeHint().width()
        if anchor is not None:
            corner = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 6))
            x, y = corner.x() - width, corner.y()
        else:
            centre = window.mapToGlobal(window.rect().center())
            x, y = centre.x() - width // 2, centre.y() - height // 2
        x = max(screen.left(), min(x, screen.right() - width + 1))
        y = max(screen.top(), min(y, screen.bottom() - height + 1))
        self.setGeometry(x, y, width, height)
        self.show()
        self.panel.setFocus()

    def hideEvent(self, event) -> None:
        self.closed_at = time.monotonic()
        self.panel.stop()
        super().hideEvent(event)
