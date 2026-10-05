"""The Appearance dropdown: colour scheme and borders between items."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QLabel, QMenu, QWidget, QWidgetAction

from . import theme


def _heading(menu: QMenu, text: str) -> None:
    label = QLabel(text.upper())
    label.setObjectName("dim")
    label.setFont(theme.font(8, bold=True, spacing=2.4))
    label.setContentsMargins(theme.u(12), theme.u(8), theme.u(12), theme.u(4))
    holder = QWidgetAction(menu)
    holder.setDefaultWidget(label)
    menu.addAction(holder)


def show_menu(anchor: QWidget, on_scheme: Callable[[str], None], on_borders: Callable[[bool], None]) -> None:
    """Pop the menu up under `anchor`. Choices are applied through the callbacks as they are made."""
    menu = QMenu(anchor)
    menu.setToolTipsVisible(True)
    _heading(menu, "Colour scheme")
    group = QActionGroup(menu)
    for name in theme.scheme_names():
        action = menu.addAction(theme.swatch(name, theme.u(18)), name)
        action.setCheckable(True)
        action.setChecked(name == theme.SCHEME)
        group.addAction(action)
        action.triggered.connect(lambda _=False, n=name: on_scheme(n))
    menu.addSeparator()
    _heading(menu, "Items")
    borders = menu.addAction("Borders between items")
    borders.setCheckable(True)
    borders.setChecked(theme.BORDERS)
    borders.setToolTip("Outlines around buttons, covers and hotkey rows")
    borders.triggered.connect(lambda checked=False: on_borders(bool(checked)))
    menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))


TONES = (("paper", "Paper"), ("sepia", "Sepia"), ("night", "Night"), ("scheme", "Match the colour scheme"))
FAMILIES = (("serif", "Serif"), ("sans", "Sans serif"), ("original", "The book's own fonts"))
SPACINGS = (("compact", "Compact"), ("normal", "Normal"), ("relaxed", "Relaxed"), ("loose", "Loose"))


def show_text_menu(anchor: QWidget, current: dict, on_change: Callable[[str, object], None]) -> None:
    """Text settings for reflowing books: size, font, spacing, alignment and page tone."""
    menu = QMenu(anchor)
    _heading(menu, f"Text size  ·  {current['size']} px")
    menu.addAction("Larger", lambda: on_change("size", current["size"] + 1))
    menu.addAction("Smaller", lambda: on_change("size", current["size"] - 1))
    for title, key, options in (("Font", "family", FAMILIES), ("Line spacing", "spacing", SPACINGS), ("Page", "tone", TONES)):
        menu.addSeparator()
        _heading(menu, title)
        group = QActionGroup(menu)
        for value, label in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current.get(key) == value)
            group.addAction(action)
            action.triggered.connect(lambda _=False, k=key, v=value: on_change(k, v))
    menu.addSeparator()
    justify = menu.addAction("Justified lines")
    justify.setCheckable(True)
    justify.setChecked(bool(current.get("justify")))
    justify.triggered.connect(lambda checked=False: on_change("justify", bool(checked)))
    menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
