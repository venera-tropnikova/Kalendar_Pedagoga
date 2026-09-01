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
from calendar_pedagoga.organization_template import CalendarTemplateSelection, CalendarTemplateSource
from calendar_pedagoga.parsing import UtpParseResult


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


def _first_snippet(text: str, limit: int = 90) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    for delimiter in (". ", "; ", "? ", "! "):
        if delimiter in cleaned:
            part = cleaned.split(delimiter, 1)[0].strip()
            if part:
                cleaned = part + delimiter.strip()
                break
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _format_topic_cell(
    display_number: str,
    topic_title: str,
    content_text: str,
    hours: int,
) -> str:
    if hours <= 0:
        return ""
    label = f"{display_number}. {topic_title}"
    snippet = _first_snippet(content_text)
    if snippet and snippet.casefold() not in label.casefold():
        label = f"{label}. {snippet}"
    return f"{label} ({hours})"


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


def _set_cell_text(cell, value: str) -> None:
    cell.text = value


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
        source = lesson.source.source
        key = (source.topic_number, source.topic_title, source.section)
        display_number = display_numbers.get(key, source.topic_number or "?")
        theory_cell = _format_topic_cell(
            display_number,
            source.topic_title,
            lesson.theory_text,
            source.theory_hours,
        )
        practice_cell = _format_topic_cell(
            display_number,
            source.topic_title,
            lesson.practice_text,
            source.practice_hours,
        )
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
