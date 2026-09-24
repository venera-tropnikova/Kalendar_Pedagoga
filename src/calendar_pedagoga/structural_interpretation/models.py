"""Evidence-backed structural candidates; no application/domain adapter."""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
from calendar_pedagoga.lossless_document.models import ExtractedDocument, SourceSpan


@dataclass(frozen=True)
class Evidence:
    code: str
    block_ids: tuple[str, ...]
    spans: tuple[SourceSpan, ...]
    observed: str


@dataclass(frozen=True)
class Decision:
    status: str
    confidence: float
    evidence: tuple[Evidence, ...] = ()
    contradictions: tuple[Evidence, ...] = ()
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class YearMention:
    id: str
    block_id: str
    years: tuple[int, ...]
    effect: str
    source: SourceSpan
    decision: Decision


@dataclass(frozen=True)
class YearRegion:
    id: str
    years: tuple[int, ...]
    channel: str
    start_block_id: str
    end_block_id_exclusive: str | None
    block_ids: tuple[str, ...]
    source: SourceSpan
    decision: Decision


@dataclass(frozen=True)
class ColumnRole:
    role: str
    column: int
    header_block_ids: tuple[str, ...]
    decision: Decision


@dataclass(frozen=True)
class ColumnMapping:
    id: str
    header_rows: tuple[int, ...]
    columns: tuple[ColumnRole, ...]
    combined_number_title: bool
    decision: Decision
    unmapped_columns: tuple[int, ...] = ()


@dataclass(frozen=True)
class HourValue:
    raw_text: str
    state: str  # NUMBER, EMPTY, DASH, TEXT, UNRESOLVED
    value: Fraction | None
    source_ids: tuple[str, ...]
    spans: tuple[SourceSpan, ...]
    derived_value: Fraction | None = None
    derivation: tuple[Evidence, ...] = ()

    @property
    def arithmetic_value(self) -> Fraction | None:
        return self.value if self.value is not None else self.derived_value


@dataclass(frozen=True)
class PlanRow:
    id: str
    physical_row: int
    kind: str
    key: tuple[int, ...]
    title: str
    title_block_ids: tuple[str, ...]
    parent_id: str | None
    hours: tuple[tuple[str, HourValue], ...]
    embedded_block_ids: tuple[str, ...]
    source: SourceSpan
    decision: Decision


@dataclass(frozen=True)
class ArithmeticCheck:
    id: str
    kind: str
    left: Fraction | None
    right: Fraction | None
    decision: Decision


@dataclass(frozen=True)
class StudyPlanCandidate:
    id: str
    table_id: str
    years: tuple[int, ...]
    mapping: ColumnMapping
    rows: tuple[PlanRow, ...]
    totals: tuple[tuple[str, HourValue], ...]
    arithmetic: tuple[ArithmeticCheck, ...]
    decision: Decision


@dataclass(frozen=True)
class TableInterpretation:
    table_id: str
    classification: str
    subtype: str | None
    decision: Decision
    mappings: tuple[ColumnMapping, ...]
    plans: tuple[StudyPlanCandidate, ...]
    block_ids: tuple[str, ...]


@dataclass(frozen=True)
class ContentSection:
    id: str
    heading_block_id: str
    title: str
    key: tuple[int, ...]
    parent_id: str | None
    years: tuple[int, ...]
    origin: str
    source: SourceSpan
    decision: Decision
    declared_hours: Fraction | None = None


@dataclass(frozen=True)
class SourceFragment:
    id: str
    block_id: str
    section_id: str | None
    kind: str
    raw_text: str
    years: tuple[int, ...]
    source: SourceSpan
    decision: Decision
    parent_fragment_id: str | None = None
    list_level: int | None = None


@dataclass(frozen=True)
class BlockRole:
    block_id: str
    role: str
    years: tuple[int, ...]
    owner_id: str | None
    decision: Decision


@dataclass(frozen=True)
class TopicSourceBinding:
    id: str
    topic_id: str
    section_ids: tuple[str, ...]
    years: tuple[int, ...]
    decision: Decision


@dataclass(frozen=True)
class PlanChoice:
    years: tuple[int, ...]
    candidate_ids: tuple[str, ...]
    accepted_id: str | None
    decision: Decision


@dataclass(frozen=True)
class StructuralDocument:
    schema_version: str
    source_document: ExtractedDocument
    year_mentions: tuple[YearMention, ...]
    year_regions: tuple[YearRegion, ...]
    tables: tuple[TableInterpretation, ...]
    content_sections: tuple[ContentSection, ...]
    source_fragments: tuple[SourceFragment, ...]
    expected_results: tuple[SourceFragment, ...]
    block_roles: tuple[BlockRole, ...]
    bindings: tuple[TopicSourceBinding, ...]
    plan_choices: tuple[PlanChoice, ...]
    conflicts: tuple[Evidence, ...]

    @property
    def unresolved(self) -> tuple[BlockRole, ...]:
        return tuple(role for role in self.block_roles if role.role == 'UNRESOLVED')
