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

API_HOST_ENV = "CALENDAR_GENERATION_API_HOST"
API_PORT_ENV = "CALENDAR_GENERATION_API_PORT"


def api_bind_host() -> str:
    """Standalone APP_ROLE=api keeps the public bind unless a host is set."""

    value = (os.environ.get(API_HOST_ENV) or "").strip()
    return value or "0.0.0.0"


def api_bind_port() -> int:
    """Explicit API port wins; otherwise standalone mode uses PORT or 8000."""

    explicit = (os.environ.get(API_PORT_ENV) or "").strip()
    if explicit:
        return int(explicit)
    return int(os.environ.get("PORT", "8000"))


if __name__ == "__main__":
    if not (os.environ.get("CALENDAR_GENERATION_API_TOKEN") or "").strip():
        raise SystemExit("CALENDAR_GENERATION_API_TOKEN is required for APP_ROLE=api.")

    import uvicorn

    uvicorn.run(
        app,
        host=api_bind_host(),
        port=api_bind_port(),
    )
