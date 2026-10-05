"""Entry point: `python -m roughreader [file.cbz | folder]`.

  --diagnose          write a report about the Python/Qt setup instead of starting
  --selftest FOLDER   run the interface once, save screenshots and a step-by-step log
"""

from __future__ import annotations

import os
import sys
import traceback


def _run_diagnose() -> int:
    from . import qtenv

    report = qtenv.diagnose()
    name = "diagnose-exe.txt" if getattr(sys, "frozen", False) else "diagnose.txt"
    saved = ""
    for target in (os.path.join(os.getcwd(), name), os.path.join(qtenv.data_dir(), name)):
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(report)
            saved = target
            break
        except OSError:
            continue
    if sys.stdout is not None:
        try:
            print(report)
            print(f"Saved to {saved}" if saved else "Could not save the report.")
        except Exception:
            pass
    return 0


def _start() -> int:
    from . import qtenv

    args = sys.argv[1:]
    if "--diagnose" in args:
        return _run_diagnose()
    if "--probe" in args:
        return qtenv.probe()
    if "--selftest" in args:
        from . import selftest

        where = args.index("--selftest")
        folder = args[where + 1] if where + 1 < len(args) else "selftest"
        return selftest.run(folder)

    # Before any QApplication exists: point Qt at our own plugins, and stop with a
    # readable message instead of Qt's hard abort if they cannot be used.
    problem = qtenv.preflight()
    if problem:
        qtenv.message_box(problem)
        return 2

    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, theme
    from .store import Store
    from .window import MainWindow

    if sys.platform == "win32":
        try:  # own taskbar identity and icon instead of the Python runtime's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("RoughReader.App")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)

    qtenv.adopt_existing_settings()
    data_dir = qtenv.data_dir()
    cache_dir = os.path.join(qtenv.cache_dir(), "covers")
    store = Store(data_dir)
    from . import firstrun

    library_root = qtenv.library_dir()
    try:  # every run: the folders beside the exe exist, and a new library gets the manual
        firstrun.ensure_folders(data_dir, cache_dir, library_root)
        firstrun.install_manual(library_root, store)
    except OSError as exc:
        _log(f"Could not prepare the folders: {exc}", data_dir)
    theme.set_scheme(store.get("scheme"))
    theme.set_borders(store.get("borders"))
    theme.apply(app)

    def log_crash(kind, value, trace) -> None:
        _log("".join(traceback.format_exception(kind, value, trace)), data_dir)
        sys.__excepthook__(kind, value, trace)

    sys.excepthook = log_crash

    window = MainWindow(store, cache_dir, library_root)
    window.show()
    files = [a for a in app.arguments()[1:] if not a.startswith("-")]
    if files:
        window.open_path(files[0])
    return app.exec()


def _log(text: str, folder: str = "") -> str:
    """Append to error.log; returns its path ('' if it could not be written)."""
    try:
        if not folder:
            from . import qtenv

            folder = qtenv.data_dir()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "error.log")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n\n")
        return path
    except Exception:
        return ""


def main() -> int:
    """Never fail silently: a start-up error is logged and shown."""
    try:
        return _start()
    except SystemExit:
        raise
    except BaseException:
        text = traceback.format_exc()
        path = _log(text)
        try:
            from . import qtenv

            last = "\n".join(text.rstrip().splitlines()[-6:])
            qtenv.message_box(f"RoughReader could not start.\n\n{last}\n\nFull details: {path or 'not saved'}")
        except Exception:
            pass
        if sys.stderr is not None:
            print(text, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
