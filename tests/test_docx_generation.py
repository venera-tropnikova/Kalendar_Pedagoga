from io import BytesIO
from pathlib import Path
from functools import lru_cache

import pytest
from calendar_pedagoga.docx_generation import (
    STANDARD_TEMPLATE_PATH,
    _group_class_line,
    _program_header_line,
    _resolve_header_from_rows,
    _write_document_header,
    build_output_filename,
    generate_calendar_docx,
)
from calendar_pedagoga.docx_qa import (
    QASeverity,
    _month_cell_is_continuation,
    detect_data_row_indices_by_page,
    has_blocking_qa_issues,
    validate_calendar_docx,
    verify_month_labels_by_page,
)
from calendar_pedagoga.lesson_resolution import resolve_lesson_content
from calendar_pedagoga.organization_template import select_calendar_template
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.lesson_content import build_lesson_content
from calendar_pedagoga.scheduling import build_schedule
from docx import Document
from docx.oxml.ns import qn


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


def test_generated_table_marks_header_rows_for_word_repeat() -> None:
    document = Document(BytesIO(_key_docx()))
    table = document.tables[0]
    header_tag = qn("w:tblHeader")
    for index, row in enumerate(table.rows):
        tr_pr = row._tr.find(qn("w:trPr"))
        marker = tr_pr.find(header_tag) if tr_pr is not None else None
        if index < 2:
            assert marker is not None, f"header row {index} without tblHeader"
            assert not marker.attrib, f"header row {index} must be <w:tblHeader/>"
        else:
            assert marker is None, f"data row {index} must not repeat as header"


def test_key_generation_produces_36_data_rows() -> None:
    content = _key_docx()
    document = Document(BytesIO(content))
    assert document.paragraphs[0].text == "Календарный план"
    assert document.paragraphs[1].text == "«КЛЮЧ» — 2 год обучения (2 часа в неделю)"
    assert document.paragraphs[2].text == "2026–2027 учебный год"
    assert document.paragraphs[3].text == "Группа № ___________ (Класс _________)"
    table = document.tables[0]
    assert len(table.rows) == 38
    assert len(table.columns) >= 8


def test_key_generation_passes_structural_qa() -> None:
    issues = validate_calendar_docx(_key_docx(), expected_weeks=36)
    assert not has_blocking_qa_issues(issues)


def test_header_line_uses_program_title_and_filename_year() -> None:
    utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    assert _program_header_line(utp) == "«КЛЮЧ» — 2 год обучения (2 часа в неделю)"
    assert _group_class_line() == "Группа № ___________ (Класс _________)"
    assert _group_class_line("12", "5Б") == "Группа № 12 (Класс 5Б)"


def test_tour_guides_header_uses_program_and_filename_year() -> None:
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    validated_program = validate_upload(
        UploadPurpose.PROGRAM,
        program_path.name,
        program_path.read_bytes(),
    )
    utp = resolve_utp(None, validated_program)
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=1)
    line = _program_header_line(
        utp,
        program_title=program.title,
        study_year_hints=(f"УТП из файла «{program_path.name}»", program_path.name),
    )
    assert line == "«Туристы-проводники» — 1 год обучения (2 часа в неделю)"
    title, hints = _resolve_header_from_rows(
        resolve_lesson_content(
            build_lesson_content(
                build_content_model(
                    build_schedule(utp),
                    utp,
                    program,
                    f"УТП из файла «{program_path.name}»",
                )
            )
        ),
        program_title=None,
        study_year_hints=(),
    )
    assert _program_header_line(
        utp,
        program_title=title,
        study_year_hints=hints,
    ) == "«Туристы-проводники» — 1 год обучения (2 часа в неделю)"


def test_document_header_writes_year_and_optional_group() -> None:
    document = Document(str(STANDARD_TEMPLATE_PATH))
    utp = parse_utp(REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx")
    _write_document_header(
        document,
        utp,
        academic_year="2026–2027",
        program_title="КЛЮЧ",
        group_number="5",
        class_name="7А",
    )
    assert document.paragraphs[0].text == "Календарный план"
    assert document.paragraphs[1].text == "«КЛЮЧ» — 2 год обучения (2 часа в неделю)"
    assert document.paragraphs[2].text == "2026–2027 учебный год"
    assert document.paragraphs[3].text == "Группа № 5 (Класс 7А)"


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
    first_page_rows = detect_data_row_indices_by_page(docx_bytes, total_rows=36)
    assert first_page_rows is not None and first_page_rows[0]
    first_page_labels = [
        document.tables[0].rows[2 + index].cells[0].text.strip()
        for index in first_page_rows[0]
    ]
    assert "Сентябрь" in first_page_labels


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
        month_cell = row.cells[0]
        if not month_cell.text.strip():
            assert _month_cell_is_continuation(month_cell), f"month missing row {index}"
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
        assert not cells[6].casefold().startswith("учащийся сможет изучить тему")
        assert cells[3] == ""


def test_organization_template_preserves_vertical_columns_and_merges_months() -> None:
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    template_path = REFERENCES / "Календарный план.docx"
    program_upload = parse_program(program_path.read_bytes(), program_path.name, study_year=1)
    validated_program = validate_upload(
        UploadPurpose.PROGRAM,
        program_path.name,
        program_path.read_bytes(),
    )
    utp = resolve_utp(None, validated_program)
    schedule = build_schedule(utp)
    content = build_content_model(schedule, utp, program_upload, program_path.name)
    resolved = resolve_lesson_content(build_lesson_content(content))
    generated = generate_calendar_docx(
        utp,
        resolved,
        select_calendar_template(template_path.name, template_path.read_bytes()),
        "2026–2027",
    )

    table = Document(BytesIO(generated)).tables[0]
    data_rows = table.rows[2:]
    expected_months = tuple(week.month for week in schedule.weeks)
    rows_by_page = detect_data_row_indices_by_page(generated, total_rows=len(expected_months))
    assert rows_by_page is not None

    expected_label_rows: set[int] = set()
    expected_continue_rows: set[int] = set()
    multi_row_starts: set[int] = set()
    for page_rows in rows_by_page:
        if not page_rows:
            continue
        group_start_pos = 0
        for pos in range(1, len(page_rows) + 1):
            if pos < len(page_rows) and expected_months[page_rows[pos]] == expected_months[page_rows[group_start_pos]]:
                continue
            group = page_rows[group_start_pos:pos]
            expected_label_rows.add(group[0])
            if len(group) > 1:
                multi_row_starts.add(group[0])
                expected_continue_rows.update(group[1:])
            group_start_pos = pos

    for index, row in enumerate(data_rows):
        raw_cells = row._tr.tc_lst
        for column in (0, 1):
            direction = raw_cells[column].tcPr.find(qn("w:textDirection"))
            assert direction is not None
            assert direction.get(qn("w:val")) == "btLr"
            vertical_alignment = raw_cells[column].tcPr.find(qn("w:vAlign"))
            assert vertical_alignment is not None
            assert vertical_alignment.get(qn("w:val")) == "center"

        month_merge = raw_cells[0].tcPr.find(qn("w:vMerge"))
        month_xml_text = "".join(raw_cells[0].xpath(".//w:t/text()")).strip()
        if index in expected_label_rows:
            assert month_xml_text == expected_months[index]
            if index in multi_row_starts:
                assert month_merge is not None
                assert month_merge.get(qn("w:val")) == "restart"
            else:
                assert month_merge is None
        elif index in expected_continue_rows:
            assert month_xml_text == ""
            assert month_merge is not None
            assert month_merge.get(qn("w:val")) != "restart"
        else:
            assert False, f"row {index} not classified"


def test_tour_guides_month_labels_visible_on_each_page_segment() -> None:
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    template_path = REFERENCES / "Календарный план.docx"
    program_upload = parse_program(program_path.read_bytes(), program_path.name, study_year=1)
    validated_program = validate_upload(
        UploadPurpose.PROGRAM,
        program_path.name,
        program_path.read_bytes(),
    )
    utp = resolve_utp(None, validated_program)
    schedule = build_schedule(utp)
    content = build_content_model(schedule, utp, program_upload, program_path.name)
    resolved = resolve_lesson_content(build_lesson_content(content))
    generated = generate_calendar_docx(
        utp,
        resolved,
        select_calendar_template(template_path.name, template_path.read_bytes()),
        "2026–2027",
    )

    expected_months = tuple(week.month for week in schedule.weeks)
    rows_by_page = detect_data_row_indices_by_page(generated, total_rows=len(expected_months))
    assert rows_by_page is not None

    issues = verify_month_labels_by_page(generated, months=expected_months)
    assert not issues, "; ".join(issues)
