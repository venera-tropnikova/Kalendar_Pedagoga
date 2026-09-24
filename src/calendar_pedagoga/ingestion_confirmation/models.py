"""Immutable stage C contracts. All source objects remain owned by stages A/B."""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from fractions import Fraction
from hashlib import sha256
import json

from calendar_pedagoga.structural_interpretation.models import (
    Evidence, StructuralDocument, StudyPlanCandidate, TableInterpretation,
)


class State(str, Enum):
    VALID = 'VALID'
    YEAR_AMBIGUOUS = 'YEAR_AMBIGUOUS'
    PLAN_AMBIGUOUS = 'PLAN_AMBIGUOUS'
    COLUMN_AMBIGUOUS = 'COLUMN_AMBIGUOUS'
    HOURS_CONFLICT = 'HOURS_CONFLICT'
    CONTENT_BOUNDARY_AMBIGUOUS = 'CONTENT_BOUNDARY_AMBIGUOUS'
    BINDING_AMBIGUOUS = 'BINDING_AMBIGUOUS'
    SOURCE_CONFLICT = 'SOURCE_CONFLICT'


SOURCE_LABELS = {'EMBEDDED': 'План в программе', 'EXTERNAL': 'Загруженный учебный план',
                 'MANUAL': 'План, введённый вручную'}
ROLE_LABELS = {'NUMBER': 'Номер', 'TITLE': 'Название темы', 'TOTAL': 'Всего часов',
               'THEORY': 'Теория', 'TRAINING': 'Учебно-тренировочные занятия',
               'PRACTICE': 'Практика', 'IGNORE': 'Не использовать для плана'}


def encode(value):
    if isinstance(value, Fraction):
        return {'numerator': value.numerator, 'denominator': value.denominator}
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {f.name: encode(getattr(value, f.name)) for f in fields(value)
                if f.name != 'source_document'}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    return value


def canonical(value):
    return json.dumps(encode(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


@dataclass(frozen=True)
class SourceInput:
    kind: str
    document: StructuralDocument


@dataclass(frozen=True)
class ReviewModel:
    sources: tuple[SourceInput, ...]
    fingerprint: str

    @property
    def program(self):
        return next(s.document for s in self.sources if s.kind == 'EMBEDDED')

    def source(self, kind):
        return next(s for s in self.sources if s.kind == kind)


def build_model(program, external=None, manual=None):
    """Manual, if supplied, is an explicit input document, never an inferred fallback."""
    sources = tuple(SourceInput(k, d) for k, d in (
        ('EMBEDDED', program), ('EXTERNAL', external), ('MANUAL', manual)) if d is not None)
    # Include the complete B interpretation, not just filenames or document hashes:
    # a new interpretation of unchanged bytes also invalidates decisions.
    payload = [(s.kind, s.document.source_document.source_sha256,
                s.document.source_document.schema_version, s.document) for s in sources]
    fingerprint = sha256(canonical(('confirmation/1', payload)).encode('utf-8')).hexdigest()
    return ReviewModel(sources, fingerprint)


@dataclass(frozen=True)
class Event:
    action: str
    payload: str
    fingerprint: str
    evidence: tuple[Evidence, ...]
    actor: str = 'USER'
    confidence: float = 1.0  # Certainty of the explicit action, not inferred correctness.


@dataclass(frozen=True)
class Session:
    model: ReviewModel
    events: tuple[Event, ...] = ()
    archived: tuple[Event, ...] = ()

    @property
    def confirmed(self):
        return bool(self.events and self.events[-1].action == 'confirm')


@dataclass(frozen=True)
class PlanOption:
    id: str
    kind: str
    table: TableInterpretation
    plan: StudyPlanCandidate | None


@dataclass(frozen=True)
class Boundary:
    id: str
    start: str
    end: str | None
    block_ids: tuple[str, ...]
    section_ids: tuple[str, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class ContentOption:
    id: str
    title: str
    block_ids: tuple[str, ...]
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class BindingQuestion:
    topic_id: str
    title: str
    options: tuple[ContentOption, ...]
    suggested_ids: tuple[str, ...]
    evidence: tuple[Evidence, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class HourIssue:
    label: str
    declared: Fraction | None
    calculated: Fraction | None
    evidence: tuple[Evidence, ...]

    @property
    def difference(self):
        if self.declared is None or self.calculated is None:
            return None
        return self.calculated - self.declared


@dataclass(frozen=True)
class Assessment:
    state: State
    years: tuple[int, ...] = ()
    year: int | None = None
    sources: tuple[str, ...] = ()
    source: str | None = None
    plans: tuple[PlanOption, ...] = ()
    selected: PlanOption | None = None
    plan: StudyPlanCandidate | None = None
    boundaries: tuple[Boundary, ...] = ()
    content: tuple[ContentOption, ...] = ()
    bindings: tuple[tuple[str, tuple[str, ...]], ...] = ()
    questions: tuple[BindingQuestion, ...] = ()
    hour_issues: tuple[HourIssue, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    notice: str = ''
