from calendar_pedagoga.lesson_resolution import resolve_lesson_content
from calendar_pedagoga.ai_preparation import prepare_ai_requests
from calendar_pedagoga.ai_provider import AIBatchResult, AIUsage, AIWeekVariant, SourcedAIValue
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.lesson_content import build_lesson_content
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.scheduling import build_schedule
from pathlib import Path


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _key_rows():
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    content = build_content_model(build_schedule(utp), utp, program, utp_path.name)
    return build_lesson_content(content), program


def test_resolve_without_ai_keeps_rule_based_fields() -> None:
    rows, _ = _key_rows()
    resolved = resolve_lesson_content(rows)
    assert len(resolved) == 36
    assert all(not row.filled_by_ai for row in resolved)
    assert resolved[0].theory_text


def test_resolve_prefers_ai_values_when_present() -> None:
    rows, program = _key_rows()
    requests = prepare_ai_requests(
        rows,
        program_lesson_forms=program.lesson_forms,
        program_teaching_methods=program.teaching_methods,
    )
    variant = AIWeekVariant(
        request_id="week-01",
        week_number=1,
        theory_text=SourcedAIValue("AI теория", ("program_content",)),
        practice_text=SourcedAIValue("", ()),
        lesson_type=SourcedAIValue("Беседа", ("program_lesson_forms",)),
        planned_result=SourcedAIValue("Результат", ("program_content",)),
        assessment_method=SourcedAIValue("Опрос", ("program_content",)),
        warnings=(),
    )
    ai_result = AIBatchResult("test", (variant,), AIUsage(1, 1, 2, 0.0))
    resolved = resolve_lesson_content(rows, ai_result)
    first = resolved[0]
    assert first.theory_text == "AI теория"
    assert first.lesson_type == "Беседа"
    assert first.filled_by_ai
