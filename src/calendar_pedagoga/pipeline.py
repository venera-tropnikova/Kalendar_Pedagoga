"""Сквозная оркестрация: анализ → содержание → DOCX."""

from __future__ import annotations

from dataclasses import dataclass

from calendar_pedagoga.ai_preparation import prepare_ai_requests
from calendar_pedagoga.ai_provider import AIProvider, AIUsage, OpenAIProvider
from calendar_pedagoga.content_generation import CalendarContentRow, build_content_model
from calendar_pedagoga.docx_generation import build_output_filename, generate_calendar_docx
from calendar_pedagoga.docx_qa import (
    has_blocking_qa_issues,
    validate_calendar_docx,
    validate_calendar_docx_visual,
)
from calendar_pedagoga.organization_template import (
    OrganizationTemplateError,
    validate_organization_template,
)
from calendar_pedagoga.content_engine_v2 import LessonContentV2Row, build_lesson_content_v2
from calendar_pedagoga.lesson_content import LessonContentRow, build_lesson_content
from calendar_pedagoga.lesson_resolution import ResolvedLessonRow, resolve_lesson_content
from calendar_pedagoga.organization_template import CalendarTemplateSelection
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.scheduling import build_schedule


# Внутренний флаг. True = CE 2.0 в UI-пайплайне. False мгновенно возвращает CE 1.0.
USE_CONTENT_ENGINE_V2 = True


class PipelineError(RuntimeError):
    """Операция формирования календаря не может быть завершена."""


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


def _build_pipeline_lesson_content(
    content_rows: tuple[CalendarContentRow, ...],
    *,
    use_content_engine_v2: bool,
) -> tuple[LessonContentRow, ...]:
    if use_content_engine_v2:
        return _lesson_rows_from_v2(build_lesson_content_v2(content_rows))
    return build_lesson_content(content_rows)


@dataclass(frozen=True)
class PipelineResult:
    filename: str
    content: bytes
    warnings: tuple[str, ...]
    resolved_lessons: tuple[ResolvedLessonRow, ...]
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
    utp: UtpParseResult,
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
    use_content_engine_v2: bool | None = None,
) -> PipelineResult:
    """Выполнить полный конвейер формирования календарного плана."""

    schedule = build_schedule(utp, academic_year)
    content_rows = build_content_model(schedule, utp, program, source_utp_name)
    lesson_rows = _build_pipeline_lesson_content(
        content_rows,
        use_content_engine_v2=_content_engine_v2_enabled(use_content_engine_v2),
    )

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

    resolved = resolve_lesson_content(lesson_rows, ai_result)
    docx_bytes = generate_calendar_docx(
        utp,
        resolved,
        template,
        academic_year,
        program_title=program.title if program else None,
        study_year_hints=_study_year_hints(
            source_utp_name=source_utp_name,
            program_filename=program_filename,
        ),
        group_number=group_number,
        class_name=class_name,
    )
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
        ai_usage=ai_usage,
    )
