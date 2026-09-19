"""Read-only passthrough from current CE2 rows into C2 shadow models.

One production clause becomes one temporary SourceAtom. This is a
transitional adapter, not a lasting one-clause-one-atom rule.
"""

from __future__ import annotations

from typing import Protocol

from calendar_pedagoga.semantic_atom.models import (
    CoverageBinding,
    CoverageReport,
    FrameKind,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    SourceClause,
    SourceSpan,
    fingerprint_source,
    make_object_id,
)
from calendar_pedagoga.semantic_atom.passthrough import run_passthrough_shadow

ADAPTER_NAME = "passthrough_c2"


class _CoverageRow(Protocol):
    lesson_type: str
    planned_result: str
    assessment_method: str
    clause_coverage: tuple[tuple[str, str], ...]


def _span(source: str, start: int, end: int, index: int) -> SourceSpan:
    fingerprint = fingerprint_source(source, (start, end))
    return SourceSpan(
        id=make_object_id("span", fingerprint, str(start), str(end), str(index)),
        start=start,
        end=end,
        source_fingerprint=fingerprint,
        provenance=Provenance(adapter=ADAPTER_NAME, role="span", clause_index=index),
        status=ObjectStatus.PROJECTED,
        document="",
    )


def project_passthrough_graph(
    row: _CoverageRow,
    *,
    source: str = "",
) -> CoverageReport:
    """Project current TYPE/RESULT/CONTROL without rewriting them."""

    shadow = run_passthrough_shadow(row, source=source)
    clauses: list[SourceClause] = []
    atoms: list[SourceAtom] = []
    frames: list[SemanticFrame] = []
    bindings: list[CoverageBinding] = []
    for index, atom in enumerate(shadow.atoms):
        span = _span(source, atom.span[0], atom.span[1], index)
        provenance = Provenance(adapter=ADAPTER_NAME, role="clause", clause_index=index)
        clause = SourceClause(
            id=make_object_id("clause", span.source_fingerprint, atom.text, str(index)),
            span=span,
            source_fingerprint=span.source_fingerprint,
            provenance=provenance,
            status=ObjectStatus.PROJECTED,
            text=atom.text,
        )
        source_atom = SourceAtom(
            id=make_object_id("atom", clause.id, atom.text),
            span=span,
            source_fingerprint=span.source_fingerprint,
            provenance=Provenance(adapter=ADAPTER_NAME, role="atom", clause_index=index),
            status=ObjectStatus.PROJECTED,
            text=atom.text,
            clause_id=clause.id,
            transitional=True,
        )
        frame = SemanticFrame(
            id=make_object_id("frame", source_atom.id, shadow.planned_result),
            span=span,
            source_fingerprint=span.source_fingerprint,
            provenance=Provenance(adapter=ADAPTER_NAME, role="frame", clause_index=index),
            status=ObjectStatus.PROJECTED,
            kind=FrameKind.PROJECTED,
            atom_id=source_atom.id,
            clause_id=clause.id,
            projected_type=shadow.lesson_type,
            projected_result=shadow.planned_result,
            projected_control=shadow.assessment_method,
            coverage_status=atom.status,
        )
        binding_status = (
            ObjectStatus.COVERED
            if atom.status == "COVERED"
            else ObjectStatus.PROJECTED
        )
        binding = CoverageBinding(
            id=make_object_id("binding", source_atom.id, frame.id),
            span=span,
            source_fingerprint=span.source_fingerprint,
            provenance=Provenance(
                adapter=ADAPTER_NAME, role="binding", clause_index=index
            ),
            status=binding_status,
            atom_id=source_atom.id,
            frame_id=frame.id,
        )
        clauses.append(clause)
        atoms.append(source_atom)
        frames.append(frame)
        bindings.append(binding)

    report_span = (
        clauses[0].span
        if clauses
        else _span(source, 0, 0, 0)
    )
    return CoverageReport(
        id=make_object_id(
            "report",
            shadow.lesson_type,
            shadow.planned_result,
            shadow.assessment_method,
        ),
        span=report_span,
        source_fingerprint=fingerprint_source(source),
        provenance=Provenance(adapter=ADAPTER_NAME, role="report"),
        status=ObjectStatus.PROJECTED,
        clauses=tuple(clauses),
        atoms=tuple(atoms),
        frames=tuple(frames),
        bindings=tuple(bindings),
        projected_type=shadow.lesson_type,
        projected_result=shadow.planned_result,
        projected_control=shadow.assessment_method,
    )
