"""Build RoughReader with PyInstaller (tools/roughreader.spec), check the result, self-test the built app.

    python tools/build_exe.py                      folder:  dist\\RoughReader\\RoughReader.exe (+ _internal)
    python tools/build_exe.py --onefile            single:  dist\\RoughReader.exe
    python tools/build_exe.py --onefile --out DIR  single exe written to DIR

Run by build.bat with the Python chosen in setup_env.bat (never Anaconda's).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "tools", "roughreader.spec")


def exe_path(out: str, onefile: bool) -> str:
    return os.path.join(out, "RoughReader.exe" if onefile else os.path.join("RoughReader", "RoughReader.exe"))


def pyinstaller_args(out: str, work: str) -> list[str]:
    return ["--noconfirm", "--clean", "--distpath", out, "--workpath", work, SPEC]


def missing_from_build(out: str, onefile: bool) -> list[str]:
    """Essential files that should be in the finished build but are not (a single exe is opaque)."""
    exe = exe_path(out, onefile)
    wanted = {"the app itself (RoughReader.exe)": [exe]}
    if not onefile:
        internal = os.path.join(os.path.dirname(exe), "_internal")
        wanted["Qt display plugin (qwindows.dll)"] = [os.path.join(internal, "PySide6", "plugins", "platforms", "qwindows.dll")]
        wanted["Qt core library"] = [os.path.join(internal, "PySide6", "Qt6Core.dll"), os.path.join(internal, "Qt6Core.dll")]
        wanted["WebP and AVIF encoders (for Shrink)"] = [os.path.join(internal, "PIL", "_webp.cp%d%d-win_amd64.pyd" % sys.version_info[:2])]
    return [label for label, paths in wanted.items() if not any(os.path.isfile(p) for p in paths)]


def folder_size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(path) for f in fs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onefile", action="store_true", help="one self-contained exe instead of a folder")
    parser.add_argument("--out", default=os.path.join(ROOT, "dist"), help="where to put the result")
    options = parser.parse_args(argv)
    out = os.path.abspath(options.out)
    os.chdir(ROOT)
    if "conda" in (sys.version + sys.base_prefix).lower():
        print("This Python is Anaconda's; Qt and PyInstaller misbehave with it. Use .venv (run build.bat).")
        return 1

    os.environ["ROUGHREADER_ONEFILE"] = "1" if options.onefile else "0"
    work = tempfile.mkdtemp(prefix="roughreader-build-")
    try:
        import PyInstaller.__main__ as pyinstaller

        pyinstaller.run(pyinstaller_args(out, work))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    exe = exe_path(out, options.onefile)
    missing = missing_from_build(out, options.onefile)
    if missing:
        print("\nBUILD INCOMPLETE. Missing: " + "; ".join(missing))
        if not os.path.isfile(exe):
            return 1

    print("\nSelf-testing the built app (a window will open for a few seconds)...")
    report_dir = tempfile.mkdtemp(prefix="roughreader-selftest-exe-")
    try:
        done = subprocess.run([exe, "--selftest", report_dir], capture_output=True, text=True, timeout=300)
        passed = done.returncode == 0
        detail = (done.stdout or "").strip() or (done.stderr or "").strip()[-600:]
    except Exception as exc:
        passed, detail = False, repr(exc)
    report = os.path.join(report_dir, "selftest.txt")
    if passed and not missing:
        shutil.rmtree(report_dir, ignore_errors=True)
        shown = exe if options.onefile else os.path.dirname(exe)
        print(f"\nDONE. Self-test passed.\nApp: {exe}  ({folder_size(shown) / 1e6:.0f} MB)")
        if not options.onefile:
            print("(keep the whole RoughReader folder together)")
        return 0
    print(f"\nSELF-TEST {'passed' if passed else 'FAILED'}: {detail}")
    print(f"Report: {report}" + ("" if os.path.isfile(report) else "  (not written: the app stopped before it could)"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
