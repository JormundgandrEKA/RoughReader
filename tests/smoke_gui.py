"""Offscreen walk through the UI, saving screenshots.

    QT_QPA_PLATFORM=offscreen python tests/smoke_gui.py <file.cbz> <output folder>
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from roughreader import theme  # noqa: E402
from roughreader.store import Store  # noqa: E402
from roughreader.window import MainWindow  # noqa: E402


def settle(app, seconds=1.5):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    sample, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    app = QApplication(sys.argv[:1])
    theme.apply(app)
    work = tempfile.mkdtemp()
    win = MainWindow(Store(work), os.path.join(work, "cache", "covers"))
    win.resize(1280, 860)
    win.show()

    def shot(name):
        settle(app)
        win.grab().save(os.path.join(out, name + ".png"))
        print("saved", name)

    shot("01_library_empty")
    win.set_library(os.path.dirname(sample))
    shot("02_library")
    win.open_book(sample)
    shot("03_reader_single")
    win.view.go_to_page(10)
    win._set_double(True)
    shot("04_reader_double_rtl")
    win._set_rtl(False)
    shot("05_reader_double_ltr")
    win._set_fit("width")
    shot("06_fit_width")
    win.view.zoom_by(1.6)
    shot("07_zoom")
    win._set_fit("page")
    win.view.go_to_page(7)
    shot("08_wide_page")
    win.show_library()
    shot("09_library_progress")
    win.open_book(sample)
    settle(app)
    win.show_hotkeys()
    settle(app)
    win.hotkeys.grab().save(os.path.join(out, "10_hotkeys.png"))
    panel = win.hotkeys.panel
    ok, taken = panel.keymap.assign("rtl", 1, "W")
    assert ok and taken == "fit_width"
    panel.changed.emit()
    assert [k.toString() for k in win.acts["rtl"].shortcuts()] == ["R", "W"]
    assert win.acts["fit_width"].shortcuts() == []
    panel._capture = ("fit_width", 0)
    settle(app, 0.3)
    win.hotkeys.grab().save(os.path.join(out, "11_hotkeys_remap.png"))
    win.hotkeys.close()
    print("saved hotkeys")
    win.close()
    assert os.path.exists(os.path.join(work, "state.json"))
    print("ok")


if __name__ == "__main__":
    main()
