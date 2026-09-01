from pathlib import Path
from functools import lru_cache

from calendar_pedagoga.pipeline import run_calendar_pipeline
from calendar_pedagoga.docx_qa import has_blocking_qa_issues, validate_calendar_docx
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program


REFERENCES = Path(__file__).resolve().parents[1] / "references"


@lru_cache(maxsize=1)
def _key_pipeline():
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    return run_calendar_pipeline(
        utp,
        program,
        academic_year="2026–2027",
        template=select_calendar_template(),
        source_utp_name=utp_path.name,
        use_ai=False,
    )


def test_key_end_to_end_pipeline_returns_docx() -> None:
    result = _key_pipeline()
    assert result.content.startswith(b"PK\x03\x04")
    assert result.filename.endswith(".docx")
    assert len(result.resolved_lessons) == 36
    assert not has_blocking_qa_issues(
        validate_calendar_docx(result.content, expected_weeks=36)
    )


def test_tour_guides_pipeline_without_program_is_limited_qa_reference() -> None:
    utp_path = REFERENCES / "УТП ТП 3г. 2ч.docx"
    utp = parse_utp(utp_path)
    result = run_calendar_pipeline(
        utp,
        None,
        academic_year="2026–2027",
        template=select_calendar_template(),
        source_utp_name=utp_path.name,
        use_ai=False,
    )
    assert len(result.resolved_lessons) == 36
    assert all(
        not row.theory_text and not row.practice_text
        for row in result.resolved_lessons
    )
    assert all(
        row.lesson_type and row.planned_result and row.assessment_method
        for row in result.resolved_lessons
    )
    assert any("программа не загружена" in warning.casefold() for warning in result.warnings)
