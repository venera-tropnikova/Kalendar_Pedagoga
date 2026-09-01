from io import BytesIO
from pathlib import Path
from functools import lru_cache

import pytest
from calendar_pedagoga.docx_generation import (
    STANDARD_TEMPLATE_PATH,
    build_output_filename,
    generate_calendar_docx,
)
from calendar_pedagoga.docx_qa import QASeverity, has_blocking_qa_issues, validate_calendar_docx
from calendar_pedagoga.lesson_resolution import resolve_lesson_content
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.lesson_content import build_lesson_content
from calendar_pedagoga.scheduling import build_schedule
from docx import Document


REFERENCES = Path(__file__).resolve().parents[1] / "references"


@lru_cache(maxsize=1)
def _key_docx() -> bytes:
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    content = build_content_model(build_schedule(utp), utp, program, utp_path.name)
    resolved = resolve_lesson_content(build_lesson_content(content))
    return generate_calendar_docx(
        utp,
        resolved,
        select_calendar_template(),
        "2026–2027",
    )


def test_standard_template_exists() -> None:
    assert STANDARD_TEMPLATE_PATH.is_file()


def test_key_generation_produces_36_data_rows() -> None:
    content = _key_docx()
    document = Document(BytesIO(content))
    assert document.paragraphs[0].text == "Календарный план"
    assert "КЛЮЧ" in document.paragraphs[1].text
    table = document.tables[0]
    assert len(table.rows) == 38
    assert len(table.columns) >= 8


def test_key_generation_passes_structural_qa() -> None:
    issues = validate_calendar_docx(_key_docx(), expected_weeks=36)
    assert not has_blocking_qa_issues(issues)


def test_output_filename_uses_program_and_year() -> None:
    utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    filename = build_output_filename(utp, "2026–2027")
    assert filename.endswith(".docx")
    assert "2026-2027" in filename


def test_tour_guides_without_program_generates_valid_empty_content_docx() -> None:
    utp_path = REFERENCES / "УТП ТП 3г. 2ч.docx"
    utp = parse_utp(utp_path)
    content = build_content_model(build_schedule(utp), utp, None, utp_path.name)
    resolved = resolve_lesson_content(build_lesson_content(content))
    docx_bytes = generate_calendar_docx(
        utp,
        resolved,
        select_calendar_template(),
        "2026–2027",
    )
    issues = validate_calendar_docx(docx_bytes, expected_weeks=36)
    assert not has_blocking_qa_issues(issues)
    document = Document(BytesIO(docx_bytes))
    data_row = document.tables[0].rows[2]
    assert data_row.cells[0].text.strip() == "Сентябрь"


def test_key_reference_calendar_matches_generated_structure() -> None:
    """Structural QA: сгенерированный календарь совпадает с эталоном по размерности таблицы."""
    generated = Document(BytesIO(_key_docx()))
    reference = Document(REFERENCES / "Календарный_план_КЛЮЧ_2026-2027_с_датами.docx")
    gen_table = generated.tables[0]
    ref_table = reference.tables[0]
    assert len(gen_table.rows) == len(ref_table.rows)
    assert len(gen_table.columns) >= 8
    assert generated.paragraphs[0].text == reference.paragraphs[0].text


def test_key_generation_passes_visual_qa_all_pages() -> None:
    from calendar_pedagoga.docx_qa import validate_calendar_docx_visual, has_blocking_qa_issues
    from calendar_pedagoga.program_parsing import find_libreoffice

    if find_libreoffice() is None:
        pytest.skip("LibreOffice недоступен для visual QA")

    issues = validate_calendar_docx_visual(_key_docx())
    assert not has_blocking_qa_issues(issues)


def test_qa_detects_missing_weeks() -> None:
    issues = validate_calendar_docx(_key_docx(), expected_weeks=40)
    assert any(issue.severity is QASeverity.ERROR for issue in issues)


def test_visual_qa_checks_all_data_rows_have_week_and_month() -> None:
    content = _key_docx()
    document = Document(BytesIO(content))
    for index, row in enumerate(document.tables[0].rows[2:], start=1):
        assert row.cells[0].text.strip(), f"month missing row {index}"
        assert row.cells[1].text.strip(), f"week missing row {index}"
        assert str(index) in row.cells[1].text.splitlines()[0]


def test_key_docx_does_not_truncate_source_with_ellipsis() -> None:
    document = Document(BytesIO(_key_docx()))
    table = document.tables[0]
    week3 = table.rows[4].cells[2].text  # week 3 theory
    week7 = table.rows[8].cells[4].text  # week 7 practice
    week16 = table.rows[17].cells[4].text  # week 16 practice
    for text in (week3, week7, week16):
        assert "…" not in text
        assert "..." not in text
    assert "строительства города" in week3
    assert "Праздник курая" in week7
    assert "Найди середину" in week16
    assert "Мой город" in week3
    assert "История родного края" in week7
    assert "Ориентирование" in week16


def test_key_docx_fills_type_result_control_and_keeps_mark_empty() -> None:
    document = Document(BytesIO(_key_docx()))
    data_rows = document.tables[0].rows[2:]
    assert len(data_rows) == 36
    for index, row in enumerate(data_rows, start=1):
        cells = [cell.text.strip() for cell in row.cells]
        assert cells[5], f"lesson type empty week {index}"
        assert cells[6], f"planned result empty week {index}"
        assert cells[7], f"assessment empty week {index}"
        assert cells[6].startswith("Учащийся сможет")
        assert cells[3] == ""
