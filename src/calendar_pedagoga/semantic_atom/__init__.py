"""Shadow safety harness for the future semantic-atom engine (C1).

Production matching, CE2, CONTROL, review, UI and DOCX must not import
this package. The flag stays OFF until an explicit cutover commit.
"""

from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.flags import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.import_adapter import project_import
from calendar_pedagoga.semantic_atom.match_adapter import (
    project_match_bindings,
    project_schedule_rows,
)
from calendar_pedagoga.semantic_atom.lexical import shadow_lexical_violations
from calendar_pedagoga.semantic_atom.models import (
    CoverageBinding,
    CoverageReport,
    FrameKind,
    ImportStatus,
    MatchBinding,
    MatchConfidence,
    ObjectStatus,
    ProgramSource,
    Provenance,
    ScheduleRow,
    SemanticFrame,
    SourceAtom,
    SourceClause,
    SourceSpan,
)
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
    "CoverageBinding",
    "CoverageReport",
    "DiffKind",
    "FrameKind",
    "IdentityDiff",
    "ImportStatus",
    "MatchBinding",
    "MatchConfidence",
    "ObjectStatus",
    "ProgramSource",
    "Provenance",
    "ScheduleRow",
    "SemanticFrame",
    "project_import",
    "project_match_bindings",
    "project_schedule_rows",
    "ShadowAtom",
    "ShadowRow",
    "SourceAtom",
    "SourceClause",
    "SourceSpan",
    "project_passthrough_graph",
    "compare_identity",
    "reset_shadow_invocation_count",
    "run_passthrough_shadow",
    "shadow_invocation_count",
    "shadow_lexical_violations",
)
