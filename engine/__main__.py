"""`python3 -m engine …` — same entry point as bin/report-maker."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
