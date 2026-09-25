"""Stage E shadow route. Production does not import this package.

SHADOW_ENABLED stays off. The route runs only when called explicitly.
"""
from .route import ShadowBlocked, ShadowResult, ingest, run_shadow

SHADOW_ENABLED = False

__all__ = ["SHADOW_ENABLED", "ShadowBlocked", "ShadowResult", "ingest", "run_shadow"]
