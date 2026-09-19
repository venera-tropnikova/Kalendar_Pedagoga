"""Shadow safety harness for the future semantic-atom engine (C1).

Production matching, CE2, CONTROL, review, UI and DOCX must not import
this package. The flag stays OFF until an explicit cutover commit.
"""

from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.atom_adapter import atomize, identity_fields
from calendar_pedagoga.semantic_atom.flags import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.frame_adapter import project_frames
from calendar_pedagoga.semantic_atom.import_adapter import project_import
from calendar_pedagoga.semantic_atom.match_adapter import (
    project_match_bindings,
    project_schedule_rows,
)
from calendar_pedagoga.semantic_atom.lexical import shadow_lexical_violations
from calendar_pedagoga.semantic_atom.models import (
    AtomizationResult,
    CoverageBinding,
    CoverageReport,
    FrameKind,
    FrameProjection,
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
    SourceDelimiter,
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
    "AtomizationResult",
    "CoverageBinding",
    "CoverageReport",
    "DiffKind",
    "FrameKind",
    "FrameProjection",
    "IdentityDiff",
    "ImportStatus",
    "MatchBinding",
    "MatchConfidence",
    "ObjectStatus",
    "ProgramSource",
    "Provenance",
    "ScheduleRow",
    "SemanticFrame",
    "atomize",
    "identity_fields",
    "project_frames",
    "project_import",
    "project_match_bindings",
    "project_schedule_rows",
    "ShadowAtom",
    "ShadowRow",
    "SourceAtom",
    "SourceClause",
    "SourceDelimiter",
    "SourceSpan",
    "project_passthrough_graph",
    "compare_identity",
    "reset_shadow_invocation_count",
    "run_passthrough_shadow",
    "shadow_invocation_count",
    "shadow_lexical_violations",
)
