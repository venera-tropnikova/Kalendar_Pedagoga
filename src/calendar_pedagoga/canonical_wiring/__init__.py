"""Production entry for canonical ingestion. Off unless the runtime flag is true."""
from __future__ import annotations

import os

_TRUE = {"true", "1", "yes"}


def canonical_ingestion_enabled() -> bool:
    raw = os.environ.get("CANONICAL_INGESTION_ENABLED", "false")
    return raw.strip().lower() in _TRUE


def run_if_enabled() -> bool:
    """Render the canonical screen when the flag is on. Otherwise do nothing."""
    if not canonical_ingestion_enabled():
        return False
    from calendar_pedagoga.canonical_wiring.flow import render_canonical_flow

    render_canonical_flow()
    return True


__all__ = ["canonical_ingestion_enabled", "run_if_enabled"]
