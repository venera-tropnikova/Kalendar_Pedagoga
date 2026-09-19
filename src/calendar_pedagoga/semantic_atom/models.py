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
