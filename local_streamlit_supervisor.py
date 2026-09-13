#!/usr/bin/env python
"""CLI entry for the local Streamlit supervisor (localhost:8501 only)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
src_text = str(SRC)
if src_text in sys.path:
    sys.path.remove(src_text)
sys.path.insert(0, src_text)

from calendar_pedagoga.local_app_supervisor import main


if __name__ == "__main__":
    raise SystemExit(main())
