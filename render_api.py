from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
src_text = str(SRC_DIR)
if src_text in sys.path:
    sys.path.remove(src_text)
sys.path.insert(0, src_text)

from calendar_pedagoga.generation_api import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
