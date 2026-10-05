# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for RoughReader. Run through tools/build_exe.py, which sets the environment.

ROUGHREADER_ONEFILE=1   one RoughReader.exe that unpacks itself on launch (else a folder with the exe)

The app only uses QtCore, QtGui and QtWidgets with Qt's own painting, so the software OpenGL
renderer, translations, SVG, networking and the unused plugins are left out (about 45 MB).
"""
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.dirname(SPECPATH)
ONEFILE = os.environ.get("ROUGHREADER_ONEFILE") == "1"

# Binary or data files (by path fragment) that the app never loads.
DROP = (
    "opengl32sw.dll", "d3dcompiler", "Qt6Network", "Qt6Svg", "Qt6Pdf", "Qt6OpenGL", "Qt6Quick", "Qt6Qml",
    "QtNetwork.pyd", "QtSvg.pyd", "QtOpenGL.pyd",
    "PySide6\\translations", "PySide6/translations",
    "plugins\\tls", "plugins/tls", "plugins\\generic", "plugins/generic", "plugins\\iconengines",
    "plugins/iconengines", "plugins\\networkinformation", "plugins/networkinformation",
    "qdirect2d.dll", "qminimal.dll",
    "qsvg.dll", "qicns.dll", "qtga.dll", "qtiff.dll", "qwbmp.dll",
)


def wanted(entry) -> bool:
    name = entry[0].replace("/", "\\")
    source = str(entry[1])
    return not any(bit.replace("/", "\\") in name or bit.replace("/", "\\") in source.replace("/", "\\") for bit in DROP)


a = Analysis(
    [os.path.join(ROOT, "run_roughreader.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "roughreader", "assets", "RoughReader Manual.md"), os.path.join("roughreader", "assets"))],
    hiddenimports=collect_submodules("markdown.extensions") + collect_submodules("winrt") + ["mammoth"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test", "lib2to3", "xmlrpc", "PySide6.QtNetwork",
              "PySide6.QtSvg", "PySide6.QtOpenGL"],
    noarchive=False,
    optimize=1,
)
# Qt's display plugin is named explicitly so it is never lost, whatever the hooks find.
import importlib.util

_spec = importlib.util.find_spec("PySide6")
_plugin = os.path.join(os.path.dirname(_spec.origin), "plugins", "platforms", "qwindows.dll")
if os.path.isfile(_plugin) and not any(b[0].endswith("qwindows.dll") for b in a.binaries):
    a.binaries.append((os.path.join("PySide6", "plugins", "platforms", "qwindows.dll"), _plugin, "BINARY"))
a.binaries = [b for b in a.binaries if wanted(b)]
a.datas = [d for d in a.datas if wanted(d)]

pyz = PYZ(a.pure)
icon = os.path.join(ROOT, "roughreader", "assets", "roughreader.ico")

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="RoughReader", debug=False, strip=False, upx=False, console=False, icon=[icon],
    )
else:
    exe = EXE(
        pyz, a.scripts, [], exclude_binaries=True,
        name="RoughReader", debug=False, strip=False, upx=False, console=False, icon=[icon],
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="RoughReader")
