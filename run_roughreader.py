"""Launcher script: `python run_roughreader.py [file.cbz | folder]`."""

import sys

from roughreader.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
