"""Versioned, immutable shadow handoff; canonical objects remain authoritative."""
from dataclasses import dataclass
from fractions import Fraction
from calendar_pedagoga.ingestion_confirmation.models import Session
from calendar_pedagoga.lossless_document.models import SourceSpan
from calendar_pedagoga.structural_interpretation.models import (
    ContentSection, PlanRow, SourceFragment, StudyPlanCandidate,
)

VERSION = "confirmed-ingestion/1"


@dataclass(frozen=True, order=True)
class CanonicalRef:
    source: str
    id: str


@dataclass(frozen=True)
class Provenance:
    ref: CanonicalRef
    year_scope: tuple[int, ...]
    original_year_scope: tuple[int, ...]
    spans: tuple[SourceSpan, ...]
    order: int
    parent_section: CanonicalRef | None
    topic_ids: tuple[CanonicalRef, ...]
    confirmation_events: tuple[int, ...]


@dataclass(frozen=True)
class PlanTopic:
    canonical: PlanRow
    provenance: Provenance


@dataclass(frozen=True)
class SectionRecord:
    canonical: ContentSection
    provenance: Provenance


@dataclass(frozen=True)
class FragmentRecord:
    canonical: SourceFragment
    provenance: Provenance


@dataclass(frozen=True)
class ConfirmedStudyPlanOverlay:
    """Versioned extension of the legacy plan, without invented calendar settings.

    All four hour categories and every non-topic row remain in canonical_plan.
    A legacy ConfirmedStudyPlan can only be projected when exactly representable.
    """
    canonical_plan: StudyPlanCandidate
    provenance: Provenance
    source: str
    study_year: int
    topics: tuple[PlanTopic, ...]
    row_provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class BindingRecord:
    provenance: Provenance
    canonical_binding_id: str | None
    topic: CanonicalRef
    sections: tuple[CanonicalRef, ...]
    year_scope: tuple[int, ...]
    confirmation_events: tuple[int, ...]


@dataclass(frozen=True)
class CoverageEntry:
    ref: CanonicalRef
    input_locations: tuple[str, ...]
    disposition: str  # TRANSFERRED, EXCLUDED, BLOCKED
    destinations: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class CoverageLedger:
    entries: tuple[CoverageEntry, ...]


@dataclass(frozen=True)
class AdapterPacket:
    version: str
    session: Session  # Complete A+B+C sidecar, including archived decisions.
    input_digest: str
    plan: ConfirmedStudyPlanOverlay
    sections: tuple[SectionRecord, ...]
    fragments: tuple[FragmentRecord, ...]
    bindings: tuple[BindingRecord, ...]
    ledger: CoverageLedger


@dataclass(frozen=True)
class ExplicitWorkload:
    """Optional existing UI input; never inferred from annual hours."""
    study_weeks: int
    hours_per_week: Fraction
    confirmation: str


@dataclass(frozen=True)
class LegacyPlanBundle:
    plan: object
    overlay: AdapterPacket
    workload: ExplicitWorkload


@dataclass(frozen=True)
class LegacyContentRecord:
    item: object
    provenance: Provenance
    fragment_id: str
    parent_fragment_id: str | None
    list_level: int | None


class AdaptationError(ValueError):
    def __init__(self, code, message, ledger):
        self.code = code
        self.ledger = ledger
        super().__init__(code + ": " + message)


class CompatibilityError(ValueError):
    """Lossy legacy projections must fail explicitly, never strip the overlay."""
