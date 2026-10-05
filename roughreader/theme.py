"""Colours, stylesheet and hand-drawn angular icons."""

from __future__ import annotations

from dataclasses import dataclass

import os
import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPalette, QPen, QPixmap, QPolygonF


# Sizes. Text is a third larger than the first design; things that must hold text grow with it (`u`).
TEXT_SCALE = 4 / 3
RULE = 4          # burgundy rules under the toolbars (was 2)
BORDER = 2        # outlines of controls and key caps (was 1)
SEEK_TRACK = 3    # page slider track (was 2)
SEEK_FILL = 6     # page slider progress (was 4)


def u(pixels: float) -> int:
    """A layout size that has to keep up with the larger text."""
    return round(pixels * TEXT_SCALE)


@dataclass(frozen=True)
class Scheme:
    name: str
    bar: str         # toolbars, library, dialogs
    well: str        # inputs, empty covers, key caps
    canvas: str      # behind the pages
    line: str        # hairlines, hover wash
    ink: str         # text
    dim: str         # secondary text
    accent: str      # burgundy
    accent_hi: str   # burgundy, hovered
    dark: bool       # dark title bar


# Dark to bright. Burgundy stays burgundy; its content is always near-white (ON_ACCENT).
SCHEMES: tuple[Scheme, ...] = (
    Scheme("Obsidian", "#101214", "#08090A", "#030303", "#30363B", "#DADDDF", "#939BA1", "#8E1234", "#B01D45", True),
    Scheme("Graphite", "#2E3033", "#232527", "#161718", "#50555A", "#E3E5E6", "#A6ABAF", "#9E1D3E", "#BF2B50", True),
    Scheme("Feldgrau", "#4D5D53", "#3B4841", "#222A26", "#677A6F", "#D6D9D7", "#B3BCB7", "#800020", "#A01B36", True),
    Scheme("Steel", "#52657A", "#3F5062", "#27323E", "#6B829A", "#E9EEF2", "#BECAD6", "#800020", "#A01B36", True),
    Scheme("Sand", "#DCD2BF", "#CDC1AA", "#BDB097", "#A59679", "#2A251F", "#5A5246", "#800020", "#A01B36", False),
    Scheme("Paper", "#F5F5F3", "#E5E5E2", "#C9CCCE", "#B4B8BB", "#191B1D", "#54595D", "#800020", "#A01B36", False),
)
DEFAULT_SCHEME = "Feldgrau"
ON_ACCENT = QColor("#F4F0F0")  # text and icons on a burgundy fill, in every scheme

# Current colours (names kept from the first design, where bars were feldgrau).
FELD = FELD_DARK = FELD_DEEP = FELD_LIGHT = GREY = GREY_DIM = BURG = BURG_HI = QColor()
DARK_TITLE = True
SCHEME = DEFAULT_SCHEME
BORDERS = False  # outlines around items (buttons, covers, hotkey rows)


def scheme_names() -> list[str]:
    return [s.name for s in SCHEMES]


def set_scheme(name: str) -> str:
    """Make `name` the current colour scheme (unknown names fall back to the default). Returns the name used."""
    global FELD, FELD_DARK, FELD_DEEP, FELD_LIGHT, GREY, GREY_DIM, BURG, BURG_HI, DARK_TITLE, SCHEME
    scheme = next((s for s in SCHEMES if s.name == name), None) or next(s for s in SCHEMES if s.name == DEFAULT_SCHEME)
    FELD, FELD_DARK, FELD_DEEP, FELD_LIGHT = (QColor(c) for c in (scheme.bar, scheme.well, scheme.canvas, scheme.line))
    GREY, GREY_DIM, BURG, BURG_HI = (QColor(c) for c in (scheme.ink, scheme.dim, scheme.accent, scheme.accent_hi))
    DARK_TITLE, SCHEME = scheme.dark, scheme.name
    return scheme.name


def set_borders(on: bool) -> None:
    global BORDERS
    BORDERS = bool(on)


def swatch(name: str, size: int = 22) -> QIcon:
    """A tiny preview of a scheme: its bar, canvas and ink, with the burgundy corner."""
    scheme = next((s for s in SCHEMES if s.name == name), SCHEMES[0])
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    box = QRectF(1, 1, size - 2, size - 2)
    p.fillRect(box, QColor(scheme.bar))
    p.fillRect(QRectF(1, size * 0.62, size - 2, size * 0.38 - 1), QColor(scheme.canvas))
    p.fillRect(QRectF(size * 0.2, size * 0.2, size * 0.5, 3), QColor(scheme.ink))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(scheme.accent))
    p.drawPolygon(QPolygonF([QPointF(size - 1, size - 1), QPointF(size - 1, size * 0.5), QPointF(size * 0.5, size - 1)]))
    p.setPen(QPen(QColor("#808080"), 1))
    p.setBrush(Qt.NoBrush)
    p.drawRect(box)
    p.end()
    return QIcon(pix)


set_scheme(DEFAULT_SCHEME)

FONT_FAMILIES = ["Bahnschrift", "DIN Alternate", "Segoe UI", "DejaVu Sans"]


def build_qss() -> str:
    c = lambda colour: colour.name()  # noqa: E731
    return f"""
QWidget {{ color: {c(GREY)}; }}
QMainWindow, QDialog, QWidget#page {{ background: {c(FELD)}; }}
QWidget#rule {{ background: {c(BURG)}; }}
QToolTip {{
    background: {c(FELD_DEEP)}; color: {c(GREY)};
    border: {BORDER}px solid {c(BURG)}; padding: {u(4)}px {u(8)}px;
}}
QMenu {{ background: {c(FELD_DARK)}; border: {BORDER}px solid {c(FELD_LIGHT)}; padding: {u(4)}px 0; }}
QMenu::item {{ padding: {u(6)}px {u(30)}px {u(6)}px {u(22)}px; }}
QMenu::item:selected {{ background: {c(BURG)}; color: {c(ON_ACCENT)}; }}
QMenu::item:disabled {{ color: {c(FELD_LIGHT)}; }}
QMenu::separator {{ height: {BORDER}px; background: {c(FELD_LIGHT)}; margin: {u(4)}px 0; }}
QMenu::indicator {{ width: {u(8)}px; height: {u(8)}px; left: {u(8)}px; }}
QMenu::indicator:checked {{ background: {c(GREY)}; }}
QLineEdit, QSpinBox {{
    background: {c(FELD_DARK)}; border: {BORDER}px solid {c(FELD_LIGHT)}; border-radius: 0;
    padding: {u(5)}px {u(9)}px; selection-background-color: {c(BURG)}; selection-color: {c(ON_ACCENT)};
}}
QLineEdit:focus, QSpinBox:focus {{ border-color: {c(GREY)}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; }}
QPushButton {{
    background: {c(FELD_DARK)}; border: {BORDER}px solid {c(FELD_LIGHT)}; border-radius: 0;
    padding: {u(6)}px {u(18)}px; min-width: {u(60)}px;
}}
QPushButton:hover {{ border-color: {c(GREY)}; }}
QPushButton:default {{ background: {c(BURG)}; border-color: {c(BURG)}; color: {c(ON_ACCENT)}; }}
QPushButton:default:hover {{ background: {c(BURG_HI)}; }}
QPushButton:disabled {{ color: {c(GREY_DIM)}; }}
QPushButton[choice="true"] {{ min-width: {u(54)}px; padding: {u(7)}px {u(14)}px; }}
QPushButton[choice="true"]:checked {{ background: {c(BURG)}; border-color: {c(BURG)}; color: {c(ON_ACCENT)}; }}
QPushButton[choice="true"]:checked:hover {{ background: {c(BURG_HI)}; }}
QProgressBar {{ background: {c(FELD_DARK)}; border: {BORDER}px solid {c(FELD_LIGHT)}; border-radius: 0;
    height: {u(10)}px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c(BURG)}; }}
QListView {{ background: {c(FELD)}; border: none; outline: none; }}
QScrollBar:vertical {{ background: {c(FELD_DARK)}; width: {u(10)}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {c(FELD_LIGHT)}; min-height: {u(36)}px; }}
QScrollBar::handle:vertical:hover {{ background: {c(BURG)}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QTreeView {{ background: {c(FELD)}; border: none; outline: none; }}
QTreeView::item:selected, QTreeView::item:hover {{ background: transparent; }}
QHeaderView::section {{ background: {c(FELD_DARK)}; color: {c(GREY_DIM)}; border: none;
    border-bottom: {BORDER}px solid {c(FELD_LIGHT)}; padding: {u(6)}px {u(8)}px; }}
QHeaderView::section:hover {{ color: {c(GREY)}; }}
QFrame#hotkeys {{ background: {c(FELD)}; border: {BORDER}px solid {c(GREY_DIM)}; }}
QScrollArea {{ background: {c(FELD)}; border: none; }}
QLabel#title {{ color: {c(GREY)}; }}
QLabel#dim {{ color: {c(GREY_DIM)}; }}
"""


def font(size: float = 10, bold: bool = False, spacing: float = 0.0, scaled: bool = True) -> QFont:
    k = TEXT_SCALE if scaled else 1.0
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(size * k)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing * k)
    return f


def apply(app) -> None:
    """(Re)apply the current scheme to the whole application."""
    app.setStyle("Fusion")
    app.setFont(font(10))
    pal = QPalette()
    for role, colour in (
        (QPalette.Window, FELD), (QPalette.Base, FELD_DARK), (QPalette.AlternateBase, FELD),
        (QPalette.Button, FELD_DARK), (QPalette.WindowText, GREY), (QPalette.Text, GREY),
        (QPalette.ButtonText, GREY), (QPalette.BrightText, GREY), (QPalette.ToolTipBase, FELD_DEEP),
        (QPalette.ToolTipText, GREY), (QPalette.Highlight, BURG), (QPalette.HighlightedText, ON_ACCENT),
        (QPalette.PlaceholderText, GREY_DIM), (QPalette.Link, GREY),
    ):
        pal.setColor(role, colour)
    app.setPalette(pal)
    app.setStyleSheet(build_qss())


def chamfer(rect: QRectF, cut: float) -> QPolygonF:
    """Rectangle with the top-left and bottom-right corners sliced off."""
    l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
    return QPolygonF([
        QPointF(l + cut, t), QPointF(r, t), QPointF(r, b - cut),
        QPointF(r - cut, b), QPointF(l, b), QPointF(l, t + cut),
    ])


# Icons: polylines on a 20-unit grid. A trailing True closes the shape.
_BRACKETS = [
    ([(2, 7), (2, 2), (7, 2)], False), ([(13, 2), (18, 2), (18, 7)], False),
    ([(18, 13), (18, 18), (13, 18)], False), ([(7, 18), (2, 18), (2, 13)], False),
]
ICONS = {
    "library": [
        ([(2, 2), (9, 2), (9, 9), (2, 9)], True), ([(11, 2), (18, 2), (18, 9), (11, 9)], True),
        ([(2, 11), (9, 11), (9, 18), (2, 18)], True), ([(11, 11), (18, 11), (18, 18), (11, 18)], True),
    ],
    "open": [([(2, 4), (8, 4), (10, 7), (18, 7), (18, 16), (2, 16)], True)],
    "file": [([(4, 2), (12, 2), (16, 6), (16, 18), (4, 18)], True), ([(12, 2), (12, 6), (16, 6)], False)],
    "double": [([(2, 4), (18, 4), (18, 16), (2, 16)], True), ([(10, 4), (10, 16)], False)],
    "rtl": [([(17, 10), (3, 10)], False), ([(9, 4), (3, 10), (9, 16)], False)],
    "fit_page": _BRACKETS + [([(7, 6), (13, 6), (13, 14), (7, 14)], True)],
    "fit_width": [
        ([(2, 3), (2, 17)], False), ([(18, 3), (18, 17)], False), ([(5, 10), (15, 10)], False),
        ([(8, 7), (5, 10), (8, 13)], False), ([(12, 7), (15, 10), (12, 13)], False),
    ],
    "fit_height": [
        ([(3, 2), (17, 2)], False), ([(3, 18), (17, 18)], False), ([(10, 5), (10, 15)], False),
        ([(7, 8), (10, 5), (13, 8)], False), ([(7, 12), (10, 15), (13, 12)], False),
    ],
    "fullscreen": _BRACKETS,
    "keys": [
        ([(1, 5), (19, 5), (19, 15), (1, 15)], True),
        ([(4, 8.5), (6, 8.5)], False), ([(9, 8.5), (11, 8.5)], False), ([(14, 8.5), (16, 8.5)], False),
        ([(6, 12), (14, 12)], False),
    ],
    "scheme": [([(2, 2), (18, 2), (18, 18), (2, 18)], True), ([(2, 18), (18, 2)], False), ([(11, 4), (16, 4), (16, 9)], False)],
    "scroll": [([(4, 2), (16, 2), (16, 7), (4, 7)], True), ([(4, 10), (16, 10), (16, 15), (4, 15)], True), ([(7, 18), (10, 20), (13, 18)], False)],
    "text": [([(2, 17), (8, 3), (14, 17)], False), ([(4.5, 12), (11.5, 12)], False), ([(14, 17), (16.5, 10), (19, 17)], False), ([(15, 14.5), (18, 14.5)], False)],
    "contents": [([(3, 4), (5, 4)], False), ([(8, 4), (17, 4)], False), ([(3, 10), (5, 10)], False),
                 ([(8, 10), (17, 10)], False), ([(3, 16), (5, 16)], False), ([(8, 16), (14, 16)], False)],
    "listview": [([(2, 4), (5, 4)], False), ([(8, 4), (18, 4)], False), ([(5, 10), (8, 10)], False),
                 ([(11, 10), (18, 10)], False), ([(5, 16), (8, 16)], False), ([(11, 16), (18, 16)], False)],
    "left": [([(12, 3), (5, 10), (12, 17)], False)],
    "right": [([(8, 3), (15, 10), (8, 17)], False)],
}


def draw_icon(p: QPainter, name: str, rect: QRectF, colour: QColor, weight: float = 1.6) -> None:
    """Draw an icon centred in `rect` with sharp mitred strokes."""
    unit = min(rect.width(), rect.height()) / 20.0
    ox = rect.center().x() - 10 * unit
    oy = rect.center().y() - 10 * unit
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(colour, weight)
    pen.setJoinStyle(Qt.MiterJoin)
    pen.setCapStyle(Qt.SquareCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for points, closed in ICONS[name]:
        poly = QPolygonF([QPointF(ox + x * unit, oy + y * unit) for x, y in points])
        if closed:
            p.drawPolygon(poly)
        else:
            p.drawPolyline(poly)
    p.restore()


def asset(name: str) -> str:
    """A file from roughreader/assets, also inside the built exe."""
    base = getattr(sys, "_MEIPASS", None)
    folder = os.path.join(base, "roughreader", "assets") if base else os.path.join(os.path.dirname(__file__), "assets")
    return os.path.join(folder, name)


_mark: QImage | None = None


def draw_mark(p: QPainter, rect: QRectF) -> None:
    """The arrow from the logo, fitted into `rect` (placeholder covers, the empty reader)."""
    global _mark
    if _mark is None:
        _mark = QImage(asset("mark.png"))
    if _mark.isNull():
        return
    side = min(rect.width(), rect.height())
    target = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawImage(target, _mark)
    p.restore()


def app_icon() -> QIcon:
    """The square logo; the bare arrow at the small sizes (made by tools/make_icon.py)."""
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        path = asset(f"icon-{size}.png")
        if os.path.exists(path):
            icon.addFile(path, QSize(size, size))
    return icon
