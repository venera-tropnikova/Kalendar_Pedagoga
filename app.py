from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
src_text = str(SRC_DIR)
if src_text in sys.path:
    sys.path.remove(src_text)
sys.path.insert(0, src_text)

from calendar_pedagoga.generator_revision import generator_revision
from calendar_pedagoga.ui import run_app

print(f"APP_REVISION={generator_revision(PROJECT_ROOT)}", flush=True)


if __name__ == "__main__":
    run_app()
