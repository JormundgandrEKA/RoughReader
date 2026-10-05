"""Commands and their hotkeys. Bindings are remappable and stored as overrides of the defaults."""

from __future__ import annotations

from dataclasses import dataclass

MAX_KEYS = 3


@dataclass(frozen=True)
class Command:
    id: str
    label: str
    group: str
    keys: tuple[str, ...] = ()
    scope: str = "reader"  # "reader": while reading; "window": anywhere; "mouse": illustration only
    fixed: bool = False    # shown, but not remappable


COMMANDS: tuple[Command, ...] = (
    Command("page_left", "Turn page, left", "Pages", ("Left",)),
    Command("page_right", "Turn page, right", "Pages", ("Right",)),
    Command("forward", "Next screen", "Pages", ("Space", "PgDown")),
    Command("back", "Previous screen", "Pages", ("Shift+Space", "PgUp", "Backspace")),
    Command("line_down", "Nudge down", "Pages", ("Down",)),
    Command("line_up", "Nudge up", "Pages", ("Up",)),
    Command("first", "First page", "Pages", ("Home",)),
    Command("last", "Last page", "Pages", ("End",)),
    Command("goto", "Go to page", "Pages", ("G", "Ctrl+G")),
    Command("double", "Two pages", "View", ("D",)),
    Command("rtl", "Right to left", "View", ("R",)),
    Command("cover", "Cover on its own", "View", ("C",)),
    Command("scroll", "Scroll mode", "View", ("S",)),
    Command("fit_page", "Fit page", "View", ("B", "0")),
    Command("fit_width", "Fit width", "View", ("W",)),
    Command("fit_height", "Fit height", "View", ("H",)),
    Command("actual", "Actual size", "View", ("1",)),
    Command("zoom_in", "Zoom in, larger text", "View", ("+", "=", "Ctrl++")),
    Command("zoom_out", "Zoom out, smaller text", "View", ("-", "Ctrl+-")),
    Command("contents", "Contents, bookmarks, highlights", "Text", ("T",)),
    Command("bookmark", "Bookmark this page", "Text", ("Ctrl+B",)),
    Command("copy", "Copy selected text", "Text", ("Ctrl+C",)),
    Command("highlight", "Highlight selected text", "Text", ("Ctrl+H",)),
    Command("link_back", "Back from a link", "Text", ("Alt+Left",)),
    Command("fullscreen", "Fullscreen", "Window", ("F", "F11")),
    Command("leave_fullscreen", "Leave fullscreen", "Window", ("Esc",), fixed=True),
    Command("bars", "Show toolbars", "Window", ("Tab", "M")),
    Command("library", "Library", "Window", ("L", "Ctrl+L")),
    Command("open", "Open file", "Window", ("Ctrl+O",), scope="window"),
    Command("folder", "Add folder to library", "Window", ("Ctrl+Shift+O",), scope="window"),
    Command("hotkeys", "Hotkeys", "Window", ("K", "F1"), scope="window"),
    Command("quit", "Quit", "Window", ("Ctrl+Q",), scope="window"),
)

MOUSE: tuple[Command, ...] = (
    Command("m_turn", "Turn page", "Mouse", ("Click side",), "mouse", True),
    Command("m_scroll", "Scroll, then turn", "Mouse", ("Wheel",), "mouse", True),
    Command("m_zoom", "Zoom", "Mouse", ("Ctrl+Wheel",), "mouse", True),
    Command("m_pan", "Pan a zoomed page", "Mouse", ("Drag",), "mouse", True),
    Command("m_full", "Fullscreen", "Mouse", ("Double-click centre",), "mouse", True),
    Command("m_menu", "Menu", "Mouse", ("Right-click",), "mouse", True),
)

BY_ID = {c.id: c for c in COMMANDS}


def split_parts(sequence: str) -> list[str]:
    """'Ctrl+Shift+O' -> ['Ctrl', 'Shift', 'O'], coping with the plus key itself."""
    if sequence == "+":
        return ["+"]
    if sequence.endswith("++"):
        return sequence[:-2].split("+") + ["+"]
    return sequence.split("+")


class Keymap:
    """Current bindings. One key belongs to at most one command."""

    def __init__(self, overrides=None, normalise=None):
        self._norm = normalise or (lambda text: text)
        self._keys: dict[str, list[str]] = {}
        self.reset()
        self._load(overrides)

    def _load(self, overrides) -> None:
        if not isinstance(overrides, dict):
            return
        fixed = {k for c in COMMANDS if c.fixed for k in c.keys}
        clean: dict[str, list[str]] = {}
        for cid, keys in overrides.items():
            command = BY_ID.get(cid)
            if command is None or command.fixed or not isinstance(keys, list):
                continue
            wanted: list[str] = []
            for key in keys:
                key = self._norm(key) if isinstance(key, str) else ""
                if key and key not in wanted and key not in fixed:
                    wanted.append(key)
            clean[cid] = wanted[:MAX_KEYS]
        seen = {k for keys in clean.values() for k in keys}
        for command in COMMANDS:  # overridden commands win; defaults give way
            if command.id in clean:
                continue
            self._keys[command.id] = [k for k in command.keys if k not in seen]
        taken: set[str] = set()
        for cid, keys in clean.items():
            self._keys[cid] = [k for k in keys if k not in taken]
            taken.update(keys)

    def keys(self, cid: str) -> tuple[str, ...]:
        return tuple(self._keys[cid])

    def owner(self, sequence: str) -> str | None:
        for cid, keys in self._keys.items():
            if sequence in keys:
                return cid
        return None

    def assign(self, cid: str, slot: int, sequence: str) -> tuple[bool, str | None]:
        """Bind `sequence` to a slot of `cid`. Returns (ok, command it was taken from)."""
        sequence = self._norm(sequence)
        if not sequence or BY_ID[cid].fixed:
            return False, None
        previous = self.owner(sequence)
        if previous is not None and BY_ID[previous].fixed:
            return False, previous
        keys = self._keys[cid]
        if slot < len(keys) and keys[slot] == sequence:
            return True, None
        if previous == cid:  # same command, other slot: move it
            old = keys.index(sequence)
            keys.pop(old)
            if old < slot:
                slot -= 1
        elif previous is not None:
            self._keys[previous].remove(sequence)
        if slot < len(keys):
            keys[slot] = sequence
        elif len(keys) < MAX_KEYS:
            keys.append(sequence)
        else:
            keys[-1] = sequence
        return True, previous if previous != cid else None

    def remove(self, cid: str, slot: int) -> None:
        keys = self._keys[cid]
        if not BY_ID[cid].fixed and 0 <= slot < len(keys):
            keys.pop(slot)

    def reset(self) -> None:
        self._keys = {c.id: list(c.keys) for c in COMMANDS}

    def overrides(self) -> dict[str, list[str]]:
        """Only what differs from the defaults; this is what gets saved."""
        return {c.id: list(self._keys[c.id]) for c in COMMANDS if tuple(self._keys[c.id]) != c.keys}
