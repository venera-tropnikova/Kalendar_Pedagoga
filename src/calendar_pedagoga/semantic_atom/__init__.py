"""Shadow safety harness for the future semantic-atom engine (C1).

Production matching, CE2, CONTROL, review, UI and DOCX must not import
this package. The flag stays OFF until an explicit cutover commit.
"""

from calendar_pedagoga.semantic_atom.flags import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.lexical import shadow_lexical_violations
from calendar_pedagoga.semantic_atom.passthrough import (
    DiffKind,
    IdentityDiff,
    ShadowAtom,
    ShadowRow,
    compare_identity,
    reset_shadow_invocation_count,
    run_passthrough_shadow,
    shadow_invocation_count,
)

__all__ = (
    "USE_SEMANTIC_ATOM_ENGINE",
    "DiffKind",
    "IdentityDiff",
    "ShadowAtom",
    "ShadowRow",
    "compare_identity",
    "reset_shadow_invocation_count",
    "run_passthrough_shadow",
    "shadow_invocation_count",
    "shadow_lexical_violations",
)
