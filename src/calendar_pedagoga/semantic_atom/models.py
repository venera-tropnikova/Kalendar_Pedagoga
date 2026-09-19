"""Immutable shadow models. No production imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
import hashlib
from typing import Any, Mapping

from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text, text_hash


class ObjectStatus(StrEnum):
    PROJECTED = "PROJECTED"
    PROVEN = "PROVEN"
    UNRESOLVED = "UNRESOLVED"
    COVERED = "COVERED"
    OPTIONAL = "OPTIONAL"
    UNASSESSED = "UNASSESSED"


class ImportStatus(StrEnum):
    PLAN_CONFIRMED = "PLAN_CONFIRMED"
    CONTENT_PARSED = "CONTENT_PARSED"
    NOTICE = "NOTICE"
    BLOCK = "BLOCK"


class MatchConfidence(StrEnum):
    EXACT = "EXACT"
    NORMALIZED = "NORMALIZED"
    NUMBER_MATCH = "NUMBER_MATCH"
    TEXT_MATCH = "TEXT_MATCH"
    CONFIRMED = "CONFIRMED"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


class FrameKind(StrEnum):
    ACTION = "ACTION"
    KNOWLEDGE = "KNOWLEDGE"
    CLASSIFICATION = "CLASSIFICATION"
    DEFINITION = "DEFINITION"
    QUESTION = "QUESTION"
    CREATIVE_PRODUCT = "CREATIVE_PRODUCT"
    PROJECTED = "PROJECTED"


class CandidateConfidence(StrEnum):
    VALID = "VALID"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class Provenance:
    adapter: str
    role: str
    clause_index: int | None = None
    note: str = ""


def make_object_id(kind: str, *parts: str) -> str:
    payload = "\x1f".join((kind, *(canonicalize_text(part) for part in parts)))
    return f"{kind}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def fingerprint_source(text: str | None, span: tuple[int, int] | None = None) -> str:
    body = canonicalize_text(text)
    if span is not None:
        body = f"{body}:{span[0]}:{span[1]}"
    return text_hash(body)


def _enum(cls: type[StrEnum], value: str | StrEnum) -> StrEnum:
    return value if isinstance(value, cls) else cls(value)


def _provenance(value: Provenance | Mapping[str, Any]) -> Provenance:
    if isinstance(value, Provenance):
        return value
    return Provenance(
        adapter=str(value["adapter"]),
        role=str(value["role"]),
        clause_index=value.get("clause_index"),
        note=str(value.get("note") or ""),
    )


@dataclass(frozen=True)
class SourceSpan:
    id: str
    start: int
    end: int
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    document: str = ""

    @property
    def span(self) -> SourceSpan:
        return self

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceSpan:
        return cls(
            id=str(data["id"]),
            start=int(data["start"]),
            end=int(data["end"]),
            source_fingerprint=str(data["source_fingerprint"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
            document=str(data.get("document") or ""),
        )


@dataclass(frozen=True)
class SourceClause:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    text: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceClause:
        return cls(
            id=str(data["id"]),
            span=SourceSpan.from_dict(data["span"]),
            source_fingerprint=str(data["source_fingerprint"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
            text=str(data["text"]),
        )


@dataclass(frozen=True)
class SourceAtom:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    text: str
    clause_id: str
    transitional: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceAtom:
        return cls(
            id=str(data["id"]),
            span=SourceSpan.from_dict(data["span"]),
            source_fingerprint=str(data["source_fingerprint"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
            text=str(data["text"]),
            clause_id=str(data["clause_id"]),
            transitional=bool(data.get("transitional", True)),
        )


@dataclass(frozen=True)
class SourceDelimiter:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    text: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceDelimiter:
        return cls(
            id=str(data["id"]),
            span=SourceSpan.from_dict(data["span"]),
            source_fingerprint=str(data["source_fingerprint"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
            text=str(data["text"]),
        )


@dataclass(frozen=True)
class AtomizationResult:
    source: str
    atoms: tuple[SourceAtom, ...]
    delimiters: tuple[SourceDelimiter, ...]
    status: ObjectStatus
    note: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AtomizationResult:
        return cls(
            source=str(data.get("source") or ""),
            atoms=tuple(SourceAtom.from_dict(item) for item in data.get("atoms", ())),
            delimiters=tuple(
                SourceDelimiter.from_dict(item) for item in data.get("delimiters", ())
            ),
            status=_enum(ObjectStatus, data["status"]),
            note=str(data.get("note") or ""),
        )


@dataclass(frozen=True)
class StructuralEvidence:
    notes: tuple[str, ...] = ()
    specificity: int = 0

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StructuralEvidence:
        return cls(
            notes=tuple(str(item) for item in data.get("notes", ())),
            specificity=int(data.get("specificity") or 0),
        )


@dataclass(frozen=True)
class LexicalCheckResult:
    passed: bool
    violations: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LexicalCheckResult:
        return cls(
            passed=bool(data.get("passed")),
            violations=tuple(str(item) for item in data.get("violations", ())),
        )


@dataclass(frozen=True)
class FrameCandidate:
    builder_id: str
    atom_id: str
    span: SourceSpan
    source_fingerprint: str
    proposed_kind: FrameKind
    proposed_predicate: str
    proposed_object: str
    proposed_complement: str
    proposed_result: str
    proposed_control: str
    structural_evidence: StructuralEvidence
    lexical_check: LexicalCheckResult
    confidence: CandidateConfidence
    rejection_reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.confidence is CandidateConfidence.VALID and not self.rejection_reason

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FrameCandidate:
        return cls(
            builder_id=str(data["builder_id"]),
            atom_id=str(data["atom_id"]),
            span=SourceSpan.from_dict(data["span"]),
            source_fingerprint=str(data["source_fingerprint"]),
            proposed_kind=_enum(FrameKind, data["proposed_kind"]),
            proposed_predicate=str(data.get("proposed_predicate") or ""),
            proposed_object=str(data.get("proposed_object") or ""),
            proposed_complement=str(data.get("proposed_complement") or ""),
            proposed_result=str(data.get("proposed_result") or ""),
            proposed_control=str(data.get("proposed_control") or ""),
            structural_evidence=StructuralEvidence.from_dict(
                data.get("structural_evidence") or {}
            ),
            lexical_check=LexicalCheckResult.from_dict(data.get("lexical_check") or {}),
            confidence=_enum(CandidateConfidence, data["confidence"]),
            rejection_reason=str(data.get("rejection_reason") or ""),
        )


@dataclass(frozen=True)
class FrameProjection:
    source: str
    atoms: tuple[SourceAtom, ...]
    frames: tuple[SemanticFrame, ...]
    bindings: tuple[CoverageBinding, ...]
    candidate_result: str
    identity_type: str = ""
    identity_result: str = ""
    identity_control: str = ""
    status: ObjectStatus = ObjectStatus.UNASSESSED
    candidates: tuple[FrameCandidate, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FrameProjection:
        return cls(
            source=str(data.get("source") or ""),
            atoms=tuple(SourceAtom.from_dict(item) for item in data.get("atoms", ())),
            frames=tuple(SemanticFrame.from_dict(item) for item in data.get("frames", ())),
            bindings=tuple(
                CoverageBinding.from_dict(item) for item in data.get("bindings", ())
            ),
            candidate_result=str(data.get("candidate_result") or ""),
            identity_type=str(data.get("identity_type") or ""),
            identity_result=str(data.get("identity_result") or ""),
            identity_control=str(data.get("identity_control") or ""),
            status=_enum(ObjectStatus, data.get("status") or ObjectStatus.UNASSESSED),
            candidates=tuple(
                FrameCandidate.from_dict(item) for item in data.get("candidates", ())
            ),
        )


@dataclass(frozen=True)
class SemanticFrame:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    kind: FrameKind
    atom_id: str
    clause_id: str
    projected_type: str
    projected_result: str
    projected_control: str
    coverage_status: str
    predicate: str = ""
    object: str = ""
    complement: str = ""
    reason: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SemanticFrame:
        return cls(
            id=str(data["id"]),
            span=SourceSpan.from_dict(data["span"]),
            source_fingerprint=str(data["source_fingerprint"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
            kind=_enum(FrameKind, data["kind"]),
            atom_id=str(data["atom_id"]),
            clause_id=str(data["clause_id"]),
            projected_type=str(data.get("projected_type") or ""),
            projected_result=str(data.get("projected_result") or ""),
            projected_control=str(data.get("projected_control") or ""),
            coverage_status=str(data.get("coverage_status") or ""),
            predicate=str(data.get("predicate") or ""),
            object=str(data.get("object") or ""),
            complement=str(data.get("complement") or ""),
            reason=str(data.get("reason") or ""),
        )


@dataclass(frozen=True)
class CoverageBinding:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    atom_id: str
    frame_id: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CoverageBinding:
        return cls(
            id=str(data["id"]),
            span=SourceSpan.from_dict(data["span"]),
            source_fingerprint=str(data["source_fingerprint"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
            atom_id=str(data["atom_id"]),
            frame_id=str(data["frame_id"]),
        )


@dataclass(frozen=True)
class CoverageReport:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    clauses: tuple[SourceClause, ...]
    atoms: tuple[SourceAtom, ...]
    frames: tuple[SemanticFrame, ...]
    bindings: tuple[CoverageBinding, ...]
    projected_type: str
    projected_result: str
    projected_control: str
    uncovered: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CoverageReport:
        return cls(
            id=str(data["id"]),
            span=SourceSpan.from_dict(data["span"]),
            source_fingerprint=str(data["source_fingerprint"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
            clauses=tuple(SourceClause.from_dict(item) for item in data.get("clauses", ())),
            atoms=tuple(SourceAtom.from_dict(item) for item in data.get("atoms", ())),
            frames=tuple(SemanticFrame.from_dict(item) for item in data.get("frames", ())),
            bindings=tuple(
                CoverageBinding.from_dict(item) for item in data.get("bindings", ())
            ),
            projected_type=str(data.get("projected_type") or ""),
            projected_result=str(data.get("projected_result") or ""),
            projected_control=str(data.get("projected_control") or ""),
            uncovered=tuple(data.get("uncovered") or ()),
        )


class ControlKind(StrEnum):
    ORAL_SURVEY = "ORAL_SURVEY"
    PRODUCT_REVIEW = "PRODUCT_REVIEW"
    PEDAGOGICAL_OBSERVATION = "PEDAGOGICAL_OBSERVATION"
    PRACTICAL_CHECK = "PRACTICAL_CHECK"


@dataclass(frozen=True)
class ControlPiece:
    id: str
    frame_id: str
    atom_id: str
    span: SourceSpan
    kind: ControlKind
    label: str
    source_object: str
    source_complement: str
    provenance: Provenance
    lexical_check: LexicalCheckResult
    status: ObjectStatus
    reason: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ControlPiece:
        return cls(
            id=str(data["id"]),
            frame_id=str(data["frame_id"]),
            atom_id=str(data["atom_id"]),
            span=SourceSpan.from_dict(data["span"]),
            kind=_enum(ControlKind, data["kind"]),
            label=str(data.get("label") or ""),
            source_object=str(data.get("source_object") or ""),
            source_complement=str(data.get("source_complement") or ""),
            provenance=_provenance(data["provenance"]),
            lexical_check=LexicalCheckResult.from_dict(data.get("lexical_check") or {}),
            status=_enum(ObjectStatus, data["status"]),
            reason=str(data.get("reason") or ""),
        )


@dataclass(frozen=True)
class ControlBinding:
    id: str
    piece_id: str
    frame_id: str
    atom_id: str
    span: SourceSpan
    provenance: Provenance
    status: ObjectStatus

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ControlBinding:
        return cls(
            id=str(data["id"]),
            piece_id=str(data["piece_id"]),
            frame_id=str(data["frame_id"]),
            atom_id=str(data["atom_id"]),
            span=SourceSpan.from_dict(data["span"]),
            provenance=_provenance(data["provenance"]),
            status=_enum(ObjectStatus, data["status"]),
        )


@dataclass(frozen=True)
class ShadowControlReport:
    source: str
    pieces: tuple[ControlPiece, ...]
    bindings: tuple[ControlBinding, ...]
    composed_control: str
    uncovered_frame_ids: tuple[str, ...]
    status: ObjectStatus

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ShadowControlReport:
        return cls(
            source=str(data.get("source") or ""),
            pieces=tuple(ControlPiece.from_dict(item) for item in data.get("pieces", ())),
            bindings=tuple(
                ControlBinding.from_dict(item) for item in data.get("bindings", ())
            ),
            composed_control=str(data.get("composed_control") or ""),
            uncovered_frame_ids=tuple(data.get("uncovered_frame_ids") or ()),
            status=_enum(ObjectStatus, data.get("status") or ObjectStatus.UNASSESSED),
        )


def projected_span(*, adapter: str, role: str, document: str = "", extra: str = "") -> SourceSpan:
    fingerprint = fingerprint_source(f"{document}:{extra}:{role}")
    return SourceSpan(
        id=make_object_id("span", fingerprint, adapter, role),
        start=0,
        end=0,
        source_fingerprint=fingerprint,
        provenance=Provenance(adapter=adapter, role=role),
        status=ObjectStatus.PROJECTED,
        document=document,
    )


@dataclass(frozen=True)
class ProjectedPlanTopic:
    number: str
    title: str
    section: str
    theory_hours: str
    practice_hours: str
    total_hours: str


@dataclass(frozen=True)
class ProgramSource:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    import_status: ImportStatus
    statuses: tuple[ImportStatus, ...]
    study_year: int | None
    study_weeks: int | None
    theory_hours: str
    practice_hours: str
    total_hours: str
    hours_per_week: str
    topics: tuple[ProjectedPlanTopic, ...]
    notices: tuple[str, ...]
    error_type: str
    error: str
    document_names: tuple[str, ...]
    content_hashes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ScheduleRow:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    week_number: int
    date_range: str
    month: str
    topic_number: str
    topic_title: str
    theory_hours: str
    practice_hours: str
    total_hours: str


@dataclass(frozen=True)
class MatchBinding:
    id: str
    span: SourceSpan
    source_fingerprint: str
    provenance: Provenance
    status: ObjectStatus
    week_number: int
    match_status: str
    confidence: MatchConfidence
    source: str
    source_hash: str
    warnings: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]


def to_jsonable(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, Provenance):
        return {
            "adapter": value.adapter,
            "role": value.role,
            "clause_index": value.clause_index,
            "note": value.note,
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {key: to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value
