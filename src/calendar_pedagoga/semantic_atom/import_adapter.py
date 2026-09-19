"""Read-only projection of parse / ConfirmedStudyPlan into ProgramSource."""

from __future__ import annotations

from calendar_pedagoga.confirmed_study_plan import ConfirmedStudyPlan
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.semantic_atom.models import (
    ImportStatus,
    ObjectStatus,
    ProgramSource,
    ProjectedPlanTopic,
    Provenance,
    fingerprint_source,
    make_object_id,
    projected_span,
)

ADAPTER_NAME = "import_c3"
_IMPORT_CALLS = 0


def import_adapter_calls() -> int:
    return _IMPORT_CALLS


def reset_import_adapter_calls() -> None:
    global _IMPORT_CALLS
    _IMPORT_CALLS = 0


def _hour(value: object) -> str:
    return "" if value is None else str(value)


def _topics_from_plan(plan: ConfirmedStudyPlan | UtpParseResult) -> tuple[ProjectedPlanTopic, ...]:
    topics = plan.topics
    projected: list[ProjectedPlanTopic] = []
    for topic in topics:
        projected.append(
            ProjectedPlanTopic(
                number="" if topic.number is None else str(topic.number),
                title=topic.title,
                section=topic.parent_section or "",
                theory_hours=_hour(topic.hours.theory),
                practice_hours=_hour(topic.hours.practice),
                total_hours=_hour(topic.hours.total),
            )
        )
    return tuple(projected)


def project_import(
    *,
    plan: ConfirmedStudyPlan | UtpParseResult | None = None,
    program: ProgramData | None = None,
    error: BaseException | None = None,
    source_utp_name: str = "",
    source_program_name: str = "",
) -> ProgramSource:
    """Copy year, hours, topics and BLOCK/NOTICE. Do not confirm or match."""

    global _IMPORT_CALLS
    _IMPORT_CALLS += 1
    documents = tuple(name for name in (source_utp_name, source_program_name) if name)
    content_hashes = ()
    if program is not None:
        content_hashes = tuple(
            (item.title, fingerprint_source(item.content))
            for item in program.content_items
        )
    if error is not None:
        span = projected_span(
            adapter=ADAPTER_NAME,
            role="block",
            document=source_utp_name,
            extra=type(error).__name__,
        )
        return ProgramSource(
            id=make_object_id("import", type(error).__name__, str(error)),
            span=span,
            source_fingerprint=span.source_fingerprint,
            provenance=Provenance(adapter=ADAPTER_NAME, role="block"),
            status=ObjectStatus.UNRESOLVED,
            import_status=ImportStatus.BLOCK,
            statuses=(ImportStatus.BLOCK,),
            study_year=None,
            study_weeks=None,
            theory_hours="",
            practice_hours="",
            total_hours="",
            hours_per_week="",
            topics=(),
            notices=(),
            error_type=type(error).__name__,
            error=str(error),
            document_names=documents,
            content_hashes=content_hashes,
        )

    if plan is None:
        raise TypeError("project_import requires plan or error")

    notices = tuple(plan.warnings)
    statuses: list[ImportStatus] = []
    if isinstance(plan, ConfirmedStudyPlan):
        statuses.append(ImportStatus.PLAN_CONFIRMED)
        study_year = plan.study_year
        study_weeks = plan.study_weeks
        theory = _hour(plan.theory_hours)
        practice = _hour(plan.practice_hours)
        total = _hour(plan.total_hours)
        weekly = _hour(plan.hours_per_week)
    else:
        statuses.append(ImportStatus.CONTENT_PARSED)
        study_year = None
        study_weeks = plan.metadata.study_weeks
        totals = plan.table_totals
        theory = _hour(totals.theory if totals else None)
        practice = _hour(totals.practice if totals else None)
        total = _hour(totals.total if totals else None)
        weekly = _hour(plan.metadata.hours_per_week)
    if program is not None and program.content_items:
        statuses.append(ImportStatus.CONTENT_PARSED)
    if notices:
        statuses.append(ImportStatus.NOTICE)
    fingerprint = fingerprint_source(
        "|".join(
            (
                str(study_year or ""),
                str(study_weeks or ""),
                theory,
                practice,
                total,
                *(f"{topic.number}:{topic.title}:{topic.total_hours}" for topic in _topics_from_plan(plan)),
            )
        )
    )
    span = projected_span(
        adapter=ADAPTER_NAME,
        role="plan",
        document=source_utp_name,
        extra=fingerprint,
    )
    return ProgramSource(
        id=make_object_id("import", fingerprint),
        span=span,
        source_fingerprint=fingerprint,
        provenance=Provenance(adapter=ADAPTER_NAME, role="plan"),
        status=ObjectStatus.PROJECTED,
        import_status=statuses[0],
        statuses=tuple(statuses),
        study_year=study_year,
        study_weeks=study_weeks,
        theory_hours=theory,
        practice_hours=practice,
        total_hours=total,
        hours_per_week=weekly,
        topics=_topics_from_plan(plan),
        notices=notices,
        error_type="",
        error="",
        document_names=documents,
        content_hashes=content_hashes,
    )


def import_projection_gaps(
    plan: ConfirmedStudyPlan | UtpParseResult,
    projected: ProgramSource,
    *,
    program: ProgramData | None = None,
) -> list[str]:
    """FAIL if any authoritative plan field was dropped."""

    gaps: list[str] = []
    if isinstance(plan, ConfirmedStudyPlan):
        if projected.study_year != plan.study_year:
            gaps.append("study_year")
        if projected.study_weeks != plan.study_weeks:
            gaps.append("study_weeks")
        if projected.theory_hours != _hour(plan.theory_hours):
            gaps.append("theory_hours")
        if projected.practice_hours != _hour(plan.practice_hours):
            gaps.append("practice_hours")
        if projected.total_hours != _hour(plan.total_hours):
            gaps.append("total_hours")
        if projected.hours_per_week != _hour(plan.hours_per_week):
            gaps.append("hours_per_week")
        if projected.import_status is not ImportStatus.PLAN_CONFIRMED:
            gaps.append("import_status")
    if len(projected.topics) != len(plan.topics):
        gaps.append("topic_count")
    for left, right in zip(plan.topics, projected.topics, strict=False):
        if (left.number or "") != right.number or left.title != right.title:
            gaps.append(f"topic:{left.title}")
        if _hour(left.hours.theory) != right.theory_hours:
            gaps.append(f"topic_theory:{left.title}")
        if _hour(left.hours.practice) != right.practice_hours:
            gaps.append(f"topic_practice:{left.title}")
        if _hour(left.hours.total) != right.total_hours:
            gaps.append(f"topic_total:{left.title}")
    if tuple(plan.warnings) != projected.notices:
        gaps.append("notices")
    if program is not None:
        expected = tuple(
            (item.title, fingerprint_source(item.content))
            for item in program.content_items
        )
        if expected != projected.content_hashes:
            gaps.append("content_hashes")
    required = (
        "id",
        "span",
        "source_fingerprint",
        "provenance",
        "status",
        "import_status",
        "topics",
    )
    for field in required:
        if getattr(projected, field, None) in {None, ""}:
            gaps.append(f"lost:{field}")
    return gaps
