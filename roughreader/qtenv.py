"""Make Qt load its plugins from the PySide6 this app imported, and nothing else.

Qt decides where to look for its "platform plugin" (qwindows.dll on Windows) from
several places that other software can hijack: the QT_PLUGIN_PATH and
QT_QPA_PLATFORM_PLUGIN_PATH environment variables, and a qt.conf file sitting next to
python.exe (Anaconda ships one that points at its own, older Qt). When that happens Qt
finds no usable plugin and aborts with "no Qt platform plugin could be initialized".
`prepare()` pins every lookup to our own copy before the application object exists.
"""

from __future__ import annotations

import os
import subprocess
import sys

PATH_VARS = ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")
WINDOWS_PLATFORMS = ("windows", "direct2d", "offscreen", "minimal")
PLATFORM_FILES = {"win32": "qwindows.dll", "darwin": "libqcocoa.dylib"}


def platform_file(platform: str = sys.platform) -> str:
    return PLATFORM_FILES.get(platform, "")


def has_platform_plugin(plugins: str, platform: str = sys.platform) -> bool:
    folder = os.path.join(plugins, "platforms")
    wanted = platform_file(platform)
    if wanted:
        return os.path.isfile(os.path.join(folder, wanted))
    try:
        return any(name.startswith("libq") for name in os.listdir(folder))
    except OSError:
        return False


def locate_plugins(package_dir: str, bundle_dir: str = "", platform: str = sys.platform) -> str:
    """The plugins folder belonging to the PySide6 at `package_dir` ('' if none is usable)."""
    candidates = [os.path.join(package_dir, "plugins"), os.path.join(package_dir, "Qt", "plugins")]
    if bundle_dir:  # PyInstaller layouts
        candidates += [
            os.path.join(bundle_dir, "PySide6", "plugins"),
            os.path.join(bundle_dir, "PySide6", "Qt", "plugins"),
        ]
    for candidate in candidates:
        if has_platform_plugin(candidate, platform):
            return candidate
    return ""


def clean_environment(environ, platform: str = sys.platform) -> dict[str, str]:
    """Drop inherited settings that redirect Qt's plugin search. Returns what was removed."""
    removed = {}
    for name in PATH_VARS:
        if name in environ:
            removed[name] = environ.pop(name)
    chosen = environ.get("QT_QPA_PLATFORM", "")
    if platform == "win32" and chosen and not chosen.lower().startswith(WINDOWS_PLATFORMS):
        removed["QT_QPA_PLATFORM"] = environ.pop("QT_QPA_PLATFORM")
    return removed


def foreign_qt_conf() -> str:
    """Path of a qt.conf next to the running interpreter, which overrides PySide6's own."""
    for exe in {sys.executable, getattr(sys, "_base_executable", sys.executable)}:
        if exe:
            path = os.path.join(os.path.dirname(os.path.abspath(exe)), "qt.conf")
            if os.path.isfile(path):
                return path
    return ""


def prepare() -> dict:
    """Pin Qt's plugin lookup to our PySide6. Call before creating QApplication."""
    import PySide6

    package_dir = os.path.dirname(os.path.abspath(PySide6.__file__))
    removed = clean_environment(os.environ)
    plugins = locate_plugins(package_dir, getattr(sys, "_MEIPASS", ""))
    if plugins:
        os.environ["QT_PLUGIN_PATH"] = plugins
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(plugins, "platforms")
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.setLibraryPaths([plugins])
    return {"package_dir": package_dir, "plugins": plugins, "removed": removed, "qt_conf": foreign_qt_conf()}


def load_error(plugins: str) -> str:
    """On Windows, try loading the platform plugin the way Qt will. '' means it loads."""
    if sys.platform != "win32" or not plugins:
        return ""
    try:
        import ctypes

        import PySide6.QtGui  # noqa: F401  (brings in Qt6Gui.dll, which the plugin needs)

        # winmode=0 is a plain LoadLibrary, the same call Qt makes
        ctypes.WinDLL(os.path.join(plugins, "platforms", platform_file()), winmode=0)
    except OSError as exc:
        return str(exc)
    except Exception:
        return ""  # ctypes itself unavailable: nothing to test with, let Qt try
    return ""


def preflight() -> str:
    """Prepare the environment; return a plain-language problem description, or ''."""
    state = prepare()
    if not state["plugins"]:
        return (
            "Qt's display plugin (platforms\\qwindows.dll) is missing from this copy of the app.\n\n"
            f"Looked under:\n{state['package_dir']}\n\n"
            "If this is the built app, rebuild it with build.bat. If you run from source, "
            "delete the .venv folder and start run.bat again so PySide6 is reinstalled."
        )
    problem = load_error(state["plugins"])
    if problem:
        return (
            "Qt's display plugin was found but Windows could not load it:\n\n"
            f"{problem}\n\n{os.path.join(state['plugins'], 'platforms')}\n\n"
            "This usually means Windows older than 10, or a damaged install. "
            "Run diagnose.bat and send the report."
        )
    return ""


def message_box(text: str, title: str = "RoughReader") -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
            return
        except Exception:
            pass
        try:  # no ctypes in this build: put the message in a text file and open it
            path = os.path.join(data_dir(), "message.txt")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"{title}\n\n{text}\n")
            os.startfile(path)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    if sys.stderr is not None:
        print(text, file=sys.stderr)


PORTABLE_FOLDER = "RoughReader-data"


def _appdata_dir() -> str:
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        root = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(root, "RoughReader")


def _writable(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        probe = os.path.join(folder, ".write-test")
        with open(probe, "w") as handle:
            handle.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def portable_dir() -> str:
    """`RoughReader-data` next to the exe when running as the built app and that spot can be written
    (so settings and reading progress travel with the exe); '' when running from source."""
    if not getattr(sys, "frozen", False):
        return ""
    folder = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), PORTABLE_FOLDER)
    return folder if _writable(folder) else ""


LIBRARY_FOLDER = "RoughReader-Library"


def library_dir() -> str:
    """Where the library's copies live: beside the exe for the built app, else in LocalAppData."""
    if getattr(sys, "frozen", False):
        folder = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), LIBRARY_FOLDER)
        if _writable(folder):
            return folder
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(root, "RoughReader", "Library")


def data_dir() -> str:
    """Where settings and logs live, worked out without Qt."""
    return portable_dir() or _appdata_dir()


def cache_dir() -> str:
    """Cover thumbnails and page-size caches: beside the settings when portable, else in LocalAppData."""
    portable = portable_dir()
    if portable:
        return os.path.join(portable, "cache")
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(root, "RoughReader", "cache")


def adopt_existing_settings() -> bool:
    """First run of the portable app: take over the settings and reading progress the installed-style
    version kept in AppData (copied, never moved), but not its library sources. Returns True if done."""
    portable = portable_dir()
    if not portable:
        return False
    target = os.path.join(portable, "state.json")
    source = os.path.join(_appdata_dir(), "state.json")
    if os.path.exists(target) or not os.path.isfile(source):
        return False
    try:
        import json

        with open(source, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        settings = data.get("settings") if isinstance(data, dict) else None
        if isinstance(settings, dict):  # a new copy starts with its own, empty library: no sources to copy from
            settings.pop("library", None)
            settings.pop("sources", None)
        os.makedirs(portable, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        return True
    except (OSError, ValueError):
        return False


def loaded_dlls() -> list[str]:
    """Full paths of the DLLs loaded into this process (Windows; [] elsewhere or on failure)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.EnumProcessModules.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        process = kernel32.GetCurrentProcess()
        handles = (wintypes.HMODULE * 4096)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModules(process, handles, ctypes.sizeof(handles), ctypes.byref(needed)):
            return []
        count = min(needed.value // ctypes.sizeof(wintypes.HMODULE), 4096)
        name = ctypes.create_unicode_buffer(1024)
        found = []
        for index in range(count):
            if psapi.GetModuleFileNameExW(process, wintypes.HMODULE(handles[index]), name, 1024):
                found.append(name.value)
        return found
    except Exception:
        return []


RUNTIME_DLLS = ("msvcp", "vcruntime", "concrt", "ucrtbase", "qt6", "python3", "shiboken", "pyside6", "ffi", "libffi")


# ------------------------------------------------------------------ diagnostics

def _launch_args(flag: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, flag]
    return [sys.executable, "-m", "roughreader", flag]


def probe() -> int:
    """Child process of `diagnose`: start Qt for real and say which platform came up."""
    prepare()
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    print(f"probe ok: platform={app.platformName()}")
    return 0


def diagnose() -> str:
    """Everything needed to tell why Qt will not start, as text."""
    from . import __version__

    lines = [f"RoughReader {__version__} diagnostics"]

    def add(label, value="") -> None:
        lines.append(f"{label}: {value}" if value != "" else str(label))

    add("python", sys.version.replace("\n", " "))
    add("executable", sys.executable)
    add("base executable", getattr(sys, "_base_executable", ""))
    add("frozen", f"{getattr(sys, 'frozen', False)} {getattr(sys, '_MEIPASS', '')}".strip())
    add("prefix", f"{sys.prefix} | base {sys.base_prefix}")
    add("platform", f"{sys.platform} {getattr(sys, 'getwindowsversion', lambda: '')()}")
    for name in sorted(os.environ):
        if name.startswith(("QT_", "QML", "CONDA_", "PYTHONPATH", "PYTHONHOME")):
            add(f"env {name}", os.environ[name])
    conf = foreign_qt_conf()
    add("qt.conf next to interpreter", conf or "none")
    if conf:
        try:
            with open(conf, "r", encoding="utf-8", errors="replace") as handle:
                lines += ["    " + row.rstrip() for row in handle.readlines()[:20]]
        except OSError as exc:
            add("    unreadable", exc)
    for stage in ("ctypes", "zlib", "PIL", "shiboken6", "shiboken6.Shiboken", "PySide6", "PySide6.QtCore",
                  "PySide6.QtGui", "PySide6.QtWidgets"):
        try:
            __import__(stage)
            add(f"import {stage}", "ok")
        except Exception as exc:
            add(f"import {stage}", f"FAILED {exc!r}")
    shown = [path for path in loaded_dlls() if os.path.basename(path).lower().startswith(RUNTIME_DLLS)]
    lines += ["runtime DLLs in this process:"] + ["    " + path for path in shown]
    try:
        import PySide6
        from PySide6 import QtCore

        add("PySide6", f"{PySide6.__version__} at {os.path.dirname(PySide6.__file__)}")
        add("Qt", QtCore.qVersion())
        before = [str(p) for p in QtCore.QCoreApplication.libraryPaths()]
        add("library paths before fix", before)
        state = prepare()
        add("removed settings", state["removed"] or "none")
        add("plugins chosen", state["plugins"] or "NOT FOUND")
        folder = os.path.join(state["plugins"], "platforms") if state["plugins"] else ""
        if folder:
            add("platforms folder", sorted(os.listdir(folder)))
        add("plugin load test", load_error(state["plugins"]) or "ok")
        add("library paths after fix", [str(p) for p in QtCore.QCoreApplication.libraryPaths()])
    except Exception as exc:  # report, never crash
        add("PySide6 problem", repr(exc))
    try:
        env = dict(os.environ, QT_DEBUG_PLUGINS="1", QT_FORCE_STDERR_LOGGING="1")
        flags = 0x08000000 if sys.platform == "win32" else 0  # no console window
        done = subprocess.run(_launch_args("--probe"), env=env, capture_output=True, text=True,
                              timeout=60, creationflags=flags, errors="replace")
        add("start test exit code", done.returncode)
        add("start test said", (done.stdout or "").strip() or "nothing")
        tail = [row for row in (done.stderr or "").splitlines() if row.strip()][-40:]
        lines += ["start test log (last lines):"] + ["    " + row for row in tail]
    except Exception as exc:
        add("start test problem", repr(exc))
    return "\n".join(lines) + "\n"
