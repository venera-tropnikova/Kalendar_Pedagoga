"""Сквозная оркестрация: анализ → содержание → DOCX."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum

from calendar_pedagoga.ai_preparation import prepare_ai_requests
from calendar_pedagoga.ai_provider import AIProvider, AIUsage, OpenAIProvider
from calendar_pedagoga.content_generation import CalendarContentRow, build_content_model
from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlan,
    confirmed_plan_from_external_utp,
)
from calendar_pedagoga.docx_generation import build_output_filename, generate_calendar_docx
from calendar_pedagoga.docx_qa import (
    has_blocking_qa_issues,
    libreoffice_pdf_render_cache,
    log_docx_qa_failure,
    validate_calendar_docx,
    validate_calendar_docx_visual,
)
from calendar_pedagoga.organization_template import (
    OrganizationTemplateError,
    validate_organization_template,
)
from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    build_lesson_content_v2,
    format_unresolved_review_block_message,
    unresolved_mandatory_review_blocks,
)
from calendar_pedagoga.lesson_content import LessonContentRow, build_lesson_content
from calendar_pedagoga.lesson_resolution import ResolvedLessonRow, resolve_lesson_content
from calendar_pedagoga.organization_template import CalendarTemplateSelection
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.generator_revision import generator_revision
from calendar_pedagoga.semantic_review import (
    ManualSemanticConfirmation,
    SemanticReviewCase,
    apply_manual_semantic_confirmations,
    build_review_context_fingerprint,
    build_semantic_review_cases,
    review_proposal_docx_issues,
    review_context_fingerprint_from_rows,
    source_grounded_review_proposal,
)


# Внутренний флаг. True = CE 2.0 в UI-пайплайне. False мгновенно возвращает CE 1.0.
USE_CONTENT_ENGINE_V2 = True


class CalendarDocumentStatus(str, Enum):
    HARD_BLOCK = "HARD_BLOCK"
    DRAFT_READY = "DRAFT_READY"
    FINAL_READY = "FINAL_READY"


class PipelineError(RuntimeError):
    """Операция формирования календаря не может быть завершена."""

    status = CalendarDocumentStatus.HARD_BLOCK


class SemanticReviewRequired(PipelineError):
    """Structured fail-closed result for unresolved CE2 weeks."""

    status = "REVIEW_REQUIRED"

    def __init__(
        self,
        review_cases: tuple[SemanticReviewCase, ...],
        *,
        confirmation_errors: tuple[tuple[str, tuple[str, ...]], ...] = (),
        accepted_review_ids: tuple[str, ...] = (),
    ) -> None:
        self.review_cases = review_cases
        self.confirmation_errors = confirmation_errors
        self.accepted_review_ids = accepted_review_ids
        blocks = tuple(
            (case.week_number, case.reasons or case.required_clauses)
            for case in review_cases
        )
        message = format_unresolved_review_block_message(blocks)
        if not message and confirmation_errors:
            message = (
                "Календарный план не готов: подтверждения semantic review "
                "недействительны или устарели."
            )
        super().__init__(message)


def _content_engine_v2_enabled(override: bool | None) -> bool:
    return USE_CONTENT_ENGINE_V2 if override is None else override


def _lesson_rows_from_v2(rows: tuple[LessonContentV2Row, ...]) -> tuple[LessonContentRow, ...]:
    """Свести поля 2.0 к контракту CE 1.0, чтобы resolution/DOCX не менять."""

    return tuple(
        LessonContentRow(
            source=row.source,
            theory_text=row.theory_text,
            practice_text=row.practice_text,
            lesson_type=row.lesson_type,
            planned_result=row.planned_result,
            assessment_method=row.assessment_method,
            warnings=row.warnings,
        )
        for row in rows
    )


@dataclass(frozen=True)
class _LessonContentBuild:
    rows: tuple[LessonContentRow, ...]
    v2_rows: tuple[LessonContentV2Row, ...]
    status: CalendarDocumentStatus
    review_cases: tuple[SemanticReviewCase, ...] = ()
    accepted_review_ids: tuple[str, ...] = ()
    confirmation_errors: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _build_pipeline_lesson_content_outcome(
    content_rows: tuple[CalendarContentRow, ...],
    *,
    use_content_engine_v2: bool,
    manual_confirmations: Mapping[str, ManualSemanticConfirmation] | None = None,
    review_context_fingerprint: str | None = None,
    semantic_revision: str | None = None,
) -> _LessonContentBuild:
    """Build every row while keeping review separate from hard failures."""

    if not use_content_engine_v2:
        return _LessonContentBuild(
            rows=build_lesson_content(content_rows),
            v2_rows=(),
            status=CalendarDocumentStatus.FINAL_READY,
        )

    v2_rows = build_lesson_content_v2(content_rows)
    blocks = unresolved_mandatory_review_blocks(v2_rows)
    if not blocks:
        return _LessonContentBuild(
            rows=_lesson_rows_from_v2(v2_rows),
            v2_rows=v2_rows,
            status=CalendarDocumentStatus.FINAL_READY,
        )

    context = review_context_fingerprint or review_context_fingerprint_from_rows(
        v2_rows,
        semantic_revision=semantic_revision or generator_revision(),
    )
    cases = build_semantic_review_cases(v2_rows, context_fingerprint=context)
    application = apply_manual_semantic_confirmations(
        v2_rows,
        cases,
        manual_confirmations,
    )
    status = (
        CalendarDocumentStatus.DRAFT_READY
        if application.pending_cases
        else CalendarDocumentStatus.FINAL_READY
    )
    return _LessonContentBuild(
        rows=_lesson_rows_from_v2(application.rows),
        v2_rows=application.rows,
        status=status,
        review_cases=application.pending_cases,
        accepted_review_ids=application.accepted_review_ids,
        confirmation_errors=application.errors,
    )


def _build_pipeline_lesson_content(
    content_rows: tuple[CalendarContentRow, ...],
    *,
    use_content_engine_v2: bool,
    manual_confirmations: Mapping[str, ManualSemanticConfirmation] | None = None,
    review_context_fingerprint: str | None = None,
    semantic_revision: str | None = None,
) -> tuple[LessonContentRow, ...]:
    """Final-only compatibility facade for immutable semantic checks."""

    outcome = _build_pipeline_lesson_content_outcome(
        content_rows,
        use_content_engine_v2=use_content_engine_v2,
        manual_confirmations=manual_confirmations,
        review_context_fingerprint=review_context_fingerprint,
        semantic_revision=semantic_revision,
    )
    if outcome.status is CalendarDocumentStatus.DRAFT_READY or outcome.confirmation_errors:
        raise SemanticReviewRequired(
            outcome.review_cases,
            confirmation_errors=outcome.confirmation_errors,
            accepted_review_ids=outcome.accepted_review_ids,
        )
    return outcome.rows


def _draft_resolved_rows(
    rows: tuple[ResolvedLessonRow, ...],
    v2_rows: tuple[LessonContentV2Row, ...],
    cases: tuple[SemanticReviewCase, ...],
) -> tuple[ResolvedLessonRow, ...]:
    """Write safe proposed RESULT/CONTROL; blank grammar/R13/control failures.

    An unsafe proposal gets one more chance through a SOURCE-grounded rebuild
    from the week's own clauses; cells stay empty when that proof also fails.
    """

    pending_weeks = {case.week_number for case in cases}
    v2_by_week = {row.source.week_number: row for row in v2_rows}
    output: list[ResolvedLessonRow] = []
    for row in rows:
        week = row.source.source.week_number
        if week not in pending_weeks:
            output.append(row)
            continue
        candidate = v2_by_week[week]
        if not review_proposal_docx_issues(candidate):
            output.append(row)
            continue
        rebuilt = source_grounded_review_proposal(candidate)
        planned_result, assessment_method = rebuilt if rebuilt else ("", "")
        output.append(
            replace(
                row,
                planned_result=planned_result,
                assessment_method=assessment_method,
            )
        )
    return tuple(output)


@dataclass(frozen=True)
class PipelineResult:
    filename: str
    content: bytes
    warnings: tuple[str, ...]
    resolved_lessons: tuple[ResolvedLessonRow, ...]
    status: CalendarDocumentStatus = CalendarDocumentStatus.FINAL_READY
    review_cases: tuple[SemanticReviewCase, ...] = ()
    accepted_review_ids: tuple[str, ...] = ()
    confirmation_errors: tuple[tuple[str, tuple[str, ...]], ...] = ()
    ai_usage: AIUsage | None = None


def _study_year_hints(
    *,
    source_utp_name: str,
    program_filename: str | None,
) -> tuple[str, ...]:
    hints: list[str] = []
    for value in (source_utp_name, program_filename):
        cleaned = (value or "").strip()
        if cleaned and cleaned not in hints:
            hints.append(cleaned)
    return tuple(hints)


def run_calendar_pipeline(
    plan: ConfirmedStudyPlan | UtpParseResult,
    program: ProgramData | None,
    *,
    academic_year: str,
    template: CalendarTemplateSelection,
    source_utp_name: str,
    use_ai: bool = False,
    ai_provider: AIProvider | None = None,
    program_filename: str | None = None,
    group_number: str | None = None,
    class_name: str | None = None,
    teacher_name: str | None = None,
    use_content_engine_v2: bool | None = None,
    match_reviews: Mapping | None = None,
    manual_confirmations: Mapping[str, ManualSemanticConfirmation] | None = None,
    semantic_revision: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> PipelineResult:
    """Выполнить полный конвейер формирования календарного плана."""

    confirmed = (
        plan
        if isinstance(plan, ConfirmedStudyPlan)
        else confirmed_plan_from_external_utp(plan, source_name=source_utp_name)
    )
    # Legacy consumers receive a projection whose topics and every workload
    # value originate exclusively from ConfirmedStudyPlan.
    utp = confirmed.as_utp_parse_result()
    if on_progress is not None:
        on_progress("Формируем календарный план…")
    schedule = build_schedule(utp, academic_year)
    content_rows = build_content_model(
        schedule,
        utp,
        program,
        source_utp_name,
        match_reviews=match_reviews,
    )
    use_ce2 = _content_engine_v2_enabled(use_content_engine_v2)
    revision = semantic_revision or generator_revision()
    review_context = build_review_context_fingerprint(
        plan=confirmed,
        program=program,
        academic_year=academic_year,
        schedule=schedule,
        semantic_revision=revision,
    )
    lesson_build = _build_pipeline_lesson_content_outcome(
        content_rows,
        use_content_engine_v2=use_ce2,
        manual_confirmations=manual_confirmations,
        review_context_fingerprint=review_context,
        semantic_revision=revision,
    )
    lesson_rows = lesson_build.rows

    ai_result = None
    ai_usage = None
    if use_ai:
        if program is None:
            raise PipelineError(
                "AI-генерация недоступна без образовательной программы."
            )
        provider = ai_provider or OpenAIProvider()
        requests = prepare_ai_requests(
            lesson_rows,
            program_lesson_forms=program.lesson_forms,
            program_teaching_methods=program.teaching_methods,
        )
        ai_result = provider.generate(requests)
        ai_usage = ai_result.usage

    if template.uses_organization_template:
        assert template.content is not None
        try:
            validate_organization_template(template.content)
        except OrganizationTemplateError as error:
            raise PipelineError(str(error)) from error

    resolved = resolve_lesson_content(
        lesson_rows,
        ai_result,
        freeze_pedagogical_fields=use_ce2 and not use_ai,
    )
    rows_for_docx = (
        _draft_resolved_rows(
            resolved,
            lesson_build.v2_rows,
            lesson_build.review_cases,
        )
        if lesson_build.status is CalendarDocumentStatus.DRAFT_READY
        else resolved
    )
    with libreoffice_pdf_render_cache():
        try:
            docx_bytes = generate_calendar_docx(
                utp,
                rows_for_docx,
                template,
                academic_year,
                program_title=program.title if program else None,
                study_year_hints=_study_year_hints(
                    source_utp_name=source_utp_name,
                    program_filename=program_filename,
                ),
                group_number=group_number,
                class_name=class_name,
                teacher_name=teacher_name,
                draft_review_weeks=tuple(
                    case.week_number for case in lesson_build.review_cases
                ),
            )
        except Exception as error:
            log_docx_qa_failure(
                "generate_calendar_docx",
                error,
                logical_rows=len(rows_for_docx),
            )
            raise
        if on_progress is not None:
            on_progress("Проверяем готовый документ…")
        try:
            qa_issues = validate_calendar_docx(
                docx_bytes,
                expected_weeks=len(schedule.weeks),
            )
            visual_issues = validate_calendar_docx_visual(docx_bytes)
            if has_blocking_qa_issues(qa_issues + visual_issues):
                messages = "; ".join(
                    issue.message
                    for issue in (*qa_issues, *visual_issues)
                    if issue.severity.value == "error"
                )
                raise PipelineError(f"DOCX не прошёл QA: {messages}")
        except Exception as error:
            log_docx_qa_failure(
                "validate_calendar_docx",
                error,
                content=docx_bytes,
                logical_rows=len(rows_for_docx),
            )
            raise

    warnings = sorted(
        {
            *schedule.warnings,
            *(
                warning
                for row in content_rows
                for warning in row.warnings
            ),
            *(
                warning
                for row in resolved
                for warning in row.warnings
            ),
            *(
                (
                    "Черновик: требуется проверка недель "
                    + ", ".join(
                        str(case.week_number) for case in lesson_build.review_cases
                    ),
                )
                if lesson_build.status is CalendarDocumentStatus.DRAFT_READY
                else ()
            ),
            *(
                issue.message
                for issue in (*qa_issues, *visual_issues)
                if issue.severity.value == "warning"
            ),
        }
    )
    return PipelineResult(
        filename=build_output_filename(utp, academic_year),
        content=docx_bytes,
        warnings=tuple(warnings),
        resolved_lessons=resolved,
        status=lesson_build.status,
        review_cases=lesson_build.review_cases,
        accepted_review_ids=lesson_build.accepted_review_ids,
        confirmation_errors=lesson_build.confirmation_errors,
        ai_usage=ai_usage,
    )
