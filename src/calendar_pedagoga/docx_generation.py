"""Генерация итогового календарного плана DOCX на основе шаблона и данных УТП."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from calendar_pedagoga.lesson_resolution import ResolvedLessonRow
from calendar_pedagoga.lesson_display import format_practice_cell, format_theory_cell
from calendar_pedagoga.organization_template import CalendarTemplateSelection, CalendarTemplateSource
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.content_generation import WeekTopicPart


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
STANDARD_TEMPLATE_PATH = _PROJECT_ROOT / "references" / "Календарный план Образец.docx"


@dataclass(frozen=True)
class _TableColumns:
    month: int
    week: int
    theory: int
    theory_mark: int
    practice: int
    lesson_type: int
    planned_result: int
    assessment: int
    lesson_type_mirror: int | None = None
    planned_result_mirror: int | None = None


def _columns_for_table(table) -> _TableColumns:
    count = len(table.columns)
    if count >= 10:
        return _TableColumns(0, 1, 2, 3, 4, 5, 7, 9, 6, 8)
    return _TableColumns(0, 1, 2, 3, 4, 5, 6, 7)


def _prevent_row_split(row) -> None:
    """Не разрывать строку таблицы между страницами."""

    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _topic_display_numbers(utp: UtpParseResult) -> dict[tuple[str | None, str, str], str]:
    numbers: dict[tuple[str | None, str, str], str] = {}
    for section in utp.sections:
        child_index = 0
        for topic in utp.topics:
            section_name = topic.parent_section or topic.title
            if section_name != section.title:
                continue
            key = (topic.number, topic.title, section_name)
            if topic.number:
                numbers[key] = topic.number.rstrip(".")
            elif topic.is_standalone_section:
                numbers[key] = (section.number or "?").rstrip(".")
            else:
                child_index += 1
                section_number = (section.number or "?").rstrip(".")
                numbers[key] = f"{section_number}.{child_index}"
    return numbers


def _subtitle_line(utp: UtpParseResult) -> str:
    metadata = utp.metadata
    program = metadata.program_name or "название программы"
    study_year = metadata.study_year or "____ г.об."
    weekly = metadata.hours_per_week
    weekly_part = (
        f"({weekly} {'час' if weekly == 1 else 'часа' if weekly in {2, 3, 4} else 'часов'} в неделю)"
        if weekly
        else "(количество часов в неделю)"
    )
    return f"«{program}» {study_year} {weekly_part}"


def _enable_cell_wrap(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    no_wrap = tc_pr.find(qn("w:noWrap"))
    if no_wrap is not None:
        tc_pr.remove(no_wrap)


def _set_cell_text(cell, value: str) -> None:
    cell.text = value
    _enable_cell_wrap(cell)


def _write_row(
    row,
    columns: _TableColumns,
    *,
    month: str,
    week_number: int,
    date_range: str,
    theory_text: str,
    practice_text: str,
    lesson_type: str,
    planned_result: str,
    assessment_method: str,
) -> None:
    cells = row.cells
    _set_cell_text(cells[columns.month], month)
    _set_cell_text(cells[columns.week], f"{week_number}\n{date_range}")
    _set_cell_text(cells[columns.theory], theory_text)
    _set_cell_text(cells[columns.theory_mark], "")
    _set_cell_text(cells[columns.practice], practice_text)
    _set_cell_text(cells[columns.lesson_type], lesson_type)
    _set_cell_text(cells[columns.planned_result], planned_result)
    _set_cell_text(cells[columns.assessment], assessment_method)
    if columns.lesson_type_mirror is not None:
        _set_cell_text(cells[columns.lesson_type_mirror], lesson_type)
    if columns.planned_result_mirror is not None:
        _set_cell_text(cells[columns.planned_result_mirror], planned_result)


def _week_topic_parts(source_row) -> tuple[WeekTopicPart, ...]:
    if source_row.week_parts:
        return source_row.week_parts
    return (
        WeekTopicPart(
            topic_number=source_row.topic_number,
            topic_title=source_row.topic_title,
            section=source_row.section,
            theory_hours=source_row.theory_hours,
            practice_hours=source_row.practice_hours,
            match_status=source_row.match_status,
            program_section=source_row.program_section,
            program_topic=source_row.program_topic,
            program_content_full=source_row.program_content_full,
            warnings=source_row.warnings,
        ),
    )


def _topic_cells_for_lesson(
    lesson: ResolvedLessonRow,
    display_numbers: dict[tuple[str | None, str, str], str],
) -> tuple[str, str]:
    source_row = lesson.source.source
    theory_lines: list[str] = []
    practice_lines: list[str] = []
    for part in _week_topic_parts(source_row):
        key = (part.topic_number, part.topic_title, part.section)
        display_number = display_numbers.get(key, part.topic_number or "?")
        theory_cell = format_theory_cell(
            display_number,
            part.topic_title,
            part.program_content_full,
            part.theory_hours,
        )
        practice_cell = format_practice_cell(
            display_number,
            part.topic_title,
            part.program_content_full,
            part.practice_hours,
        )
        if theory_cell:
            theory_lines.append(theory_cell)
        if practice_cell:
            practice_lines.append(practice_cell)
    return "\n".join(theory_lines), "\n".join(practice_lines)


def _load_template(template: CalendarTemplateSelection) -> Document:
    if template.source is CalendarTemplateSource.STANDARD:
        if not STANDARD_TEMPLATE_PATH.is_file():
            raise FileNotFoundError(
                f"Стандартный шаблон не найден: {STANDARD_TEMPLATE_PATH}"
            )
        return Document(str(STANDARD_TEMPLATE_PATH))
    assert template.content is not None
    return Document(BytesIO(template.content))


def _ensure_data_rows(table, expected_rows: int) -> None:
    while len(table.rows) < 2 + expected_rows:
        table.add_row()
    while len(table.rows) > 2 + expected_rows:
        table._tbl.remove(table.rows[-1]._tr)


def build_output_filename(utp: UtpParseResult, academic_year: str) -> str:
    program = utp.metadata.program_name or "программа"
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", program).strip("_") or "календарь"
    year = academic_year.replace("–", "-")
    return f"Календарный_план_{safe}_{year}.docx"


def generate_calendar_docx(
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    template: CalendarTemplateSelection,
    academic_year: str,
) -> bytes:
    """Сформировать DOCX календарного плана в памяти, не изменяя шаблон на диске."""

    document = _load_template(template)
    if document.paragraphs:
        document.paragraphs[0].text = "Календарный план"
    if len(document.paragraphs) > 1:
        document.paragraphs[1].text = _subtitle_line(utp)
    if len(document.paragraphs) > 2:
        document.paragraphs[2].text = "Группа № ___________ (Класс _________)"

    table = document.tables[0]
    columns = _columns_for_table(table)
    display_numbers = _topic_display_numbers(utp)
    _ensure_data_rows(table, len(rows))

    for index, lesson in enumerate(rows):
        theory_cell, practice_cell = _topic_cells_for_lesson(lesson, display_numbers)
        source = lesson.source.source
        _write_row(
            table.rows[index + 2],
            columns,
            month=source.month,
            week_number=source.week_number,
            date_range=source.date_range,
            theory_text=theory_cell,
            practice_text=practice_cell,
            lesson_type=lesson.lesson_type,
            planned_result=lesson.planned_result,
            assessment_method=lesson.assessment_method,
        )
        _prevent_row_split(table.rows[index + 2])

    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
