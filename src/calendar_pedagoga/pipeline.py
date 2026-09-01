"""Сквозная оркестрация: анализ → содержание → DOCX."""

from __future__ import annotations

from dataclasses import dataclass

from calendar_pedagoga.ai_preparation import prepare_ai_requests
from calendar_pedagoga.ai_provider import AIProvider, AIUsage, OpenAIProvider
from calendar_pedagoga.content_generation import build_content_model
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
from calendar_pedagoga.lesson_content import build_lesson_content
from calendar_pedagoga.lesson_resolution import ResolvedLessonRow, resolve_lesson_content
from calendar_pedagoga.organization_template import CalendarTemplateSelection
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import ProgramData
from calendar_pedagoga.scheduling import build_schedule


class PipelineError(RuntimeError):
    """Операция формирования календаря не может быть завершена."""


@dataclass(frozen=True)
class PipelineResult:
    filename: str
    content: bytes
    warnings: tuple[str, ...]
    resolved_lessons: tuple[ResolvedLessonRow, ...]
    ai_usage: AIUsage | None = None


def run_calendar_pipeline(
    utp: UtpParseResult,
    program: ProgramData | None,
    *,
    academic_year: str,
    template: CalendarTemplateSelection,
    source_utp_name: str,
    use_ai: bool = False,
    ai_provider: AIProvider | None = None,
) -> PipelineResult:
    """Выполнить полный конвейер формирования календарного плана."""

    schedule = build_schedule(utp, academic_year)
    content_rows = build_content_model(schedule, utp, program, source_utp_name)
    lesson_rows = build_lesson_content(content_rows)

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
    docx_bytes = generate_calendar_docx(utp, resolved, template, academic_year)
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
