"""Генерация итогового календарного плана DOCX на основе шаблона и данных УТП."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from calendar_pedagoga.lesson_resolution import ResolvedLessonRow
from calendar_pedagoga.lesson_display import format_practice_cell, format_theory_cell
from calendar_pedagoga.organization_template import CalendarTemplateSelection, CalendarTemplateSource
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.content_generation import WeekTopicPart
from calendar_pedagoga.program_parsing import infer_study_year_number


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
STANDARD_TEMPLATE_PATH = _PROJECT_ROOT / "references" / "Календарный план Образец.docx"
_MONTH_CELL_MARGIN_DXA = 40


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


def _repeat_table_header_rows(table, header_row_count: int = 2) -> None:
    """Повторять шапку таблицы на каждой странице Word через w:tblHeader."""

    header_tag = qn("w:tblHeader")
    for index, row in enumerate(table.rows):
        tr_pr = row._tr.get_or_add_trPr()
        existing = tr_pr.find(header_tag)
        if index < header_row_count:
            if existing is None:
                existing = OxmlElement("w:tblHeader")
                tr_pr.append(existing)
            for attr in list(existing.attrib):
                del existing.attrib[attr]
        elif existing is not None:
            tr_pr.remove(existing)


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


def _hours_word(hours: int) -> str:
    if hours == 1:
        return "час"
    if hours in {2, 3, 4}:
        return "часа"
    return "часов"


def _study_year_label(
    utp: UtpParseResult,
    study_year_hints: tuple[str | None, ...] = (),
) -> str:
    for raw in (utp.metadata.study_year, *study_year_hints):
        number = infer_study_year_number(raw)
        if number is not None:
            return f"{number} год обучения"
    raw = (utp.metadata.study_year or "").strip()
    if raw:
        return raw if "год" in raw.casefold() else f"{raw} год обучения"
    return "____ год обучения"


def _resolve_header_from_rows(
    rows: tuple[ResolvedLessonRow, ...],
    *,
    program_title: str | None,
    study_year_hints: tuple[str | None, ...],
) -> tuple[str | None, tuple[str, ...]]:
    """Название и год обучения брать из строк занятия, не только из метаданных УТП."""

    names: list[str] = []
    hints: list[str] = []
    for raw in study_year_hints:
        cleaned = (raw or "").strip()
        if cleaned and cleaned not in hints:
            hints.append(cleaned)
    for row in rows:
        content = row.source.source
        name = (content.source_program_name or "").strip()
        if name and name not in names:
            names.append(name)
        utp_name = (content.source_utp_name or "").strip()
        if utp_name and utp_name not in hints:
            hints.append(utp_name)
    return program_title or (names[0] if names else None), tuple(hints)


def _program_header_line(
    utp: UtpParseResult,
    *,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
) -> str:
    program = (program_title or utp.metadata.program_name or "").strip()
    program = program.strip("«»\"'") or "название программы"
    weekly = utp.metadata.hours_per_week
    weekly_part = (
        f"({weekly} {_hours_word(weekly)} в неделю)"
        if weekly
        else "(количество часов в неделю)"
    )
    return f"«{program}» — {_study_year_label(utp, study_year_hints)} {weekly_part}"


def _group_class_line(
    group_number: str | None = None,
    class_name: str | None = None,
) -> str:
    group = (group_number or "").strip() or "___________"
    klass = (class_name or "").strip() or "_________"
    return f"Группа № {group} (Класс {klass})"


def _enable_cell_wrap(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    no_wrap = tc_pr.find(qn("w:noWrap"))
    if no_wrap is not None:
        tc_pr.remove(no_wrap)


def _set_cell_text(cell, value: str) -> None:
    cell.text = value
    _enable_cell_wrap(cell)


def _center_cell_text(cell) -> None:
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for paragraph in cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _ensure_text_direction_bt_lr(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    direction = tc_pr.find(qn("w:textDirection"))
    if direction is None:
        direction = OxmlElement("w:textDirection")
        tc_pr.append(direction)
    direction.set(qn("w:val"), "btLr")


def _set_cell_vertical_align(cell, value: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    valign = tc_pr.find(qn("w:vAlign"))
    if valign is None:
        valign = OxmlElement("w:vAlign")
        tc_pr.append(valign)
    valign.set(qn("w:val"), value)


def _set_month_cell_margins(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.find(qn("w:tcMar"))
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for tag in ("top", "left", "bottom", "right"):
        side = tc_mar.find(qn(f"w:{tag}"))
        if side is None:
            side = OxmlElement(f"w:{tag}")
            tc_mar.append(side)
        side.set(qn("w:w"), str(_MONTH_CELL_MARGIN_DXA))
        side.set(qn("w:type"), "dxa")


def _set_month_paragraph_format(cell) -> None:
    for paragraph in cell.paragraphs:
        # Для вертикального текста центрирование абзаца даёт визуальный центр
        # внутри объединённой ячейки.
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph_properties = paragraph._p.get_or_add_pPr()
        text_alignment = paragraph_properties.find(qn("w:textAlignment"))
        if text_alignment is None:
            text_alignment = OxmlElement("w:textAlignment")
            paragraph_properties.append(text_alignment)
        text_alignment.set(qn("w:val"), "center")
        spacing = paragraph_properties.find(qn("w:spacing"))
        if spacing is None:
            spacing = OxmlElement("w:spacing")
            paragraph_properties.append(spacing)
        spacing.set(qn("w:before"), "0")
        spacing.set(qn("w:after"), "0")


def _remove_vmerge(cell) -> None:
    tc_pr = cell._tc.tcPr
    if tc_pr is None:
        return
    merge = tc_pr.find(qn("w:vMerge"))
    if merge is not None:
        tc_pr.remove(merge)


def _set_vmerge_restart(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    merge = tc_pr.find(qn("w:vMerge"))
    if merge is None:
        merge = OxmlElement("w:vMerge")
        tc_pr.insert(0, merge)
    merge.set(qn("w:val"), "restart")


def _set_vmerge_continue(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    merge = tc_pr.find(qn("w:vMerge"))
    if merge is None:
        merge = OxmlElement("w:vMerge")
        tc_pr.insert(0, merge)
    # Пустой w:vMerge без val = continue (стандарт OOXML).
    if merge.get(qn("w:val")) is not None:
        del merge.attrib[qn("w:val")]


def _prepare_month_cell(cell, month: str) -> None:
    _set_cell_text(cell, month)
    _ensure_text_direction_bt_lr(cell)
    _set_cell_vertical_align(cell, "center")
    _set_month_cell_margins(cell)
    _set_month_paragraph_format(cell)


def _format_month_cell(cell, month: str) -> None:
    """Подпись месяца по центру объединённого блока на странице."""

    _prepare_month_cell(cell, month)


def _clear_month_continuation_cell(cell) -> None:
    _set_cell_text(cell, "")
    _ensure_text_direction_bt_lr(cell)
    _set_cell_vertical_align(cell, "center")


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
    _format_month_cell(cells[columns.month], month)
    _set_cell_text(cells[columns.week], f"{week_number}\n{date_range}")
    _center_cell_text(cells[columns.week])
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
    source_index = 2 if len(table.rows) > 2 else len(table.rows) - 1
    prototype = deepcopy(table.rows[source_index]._tr)
    prototype_pr = prototype.find(qn("w:trPr"))
    if prototype_pr is not None:
        leaked_header = prototype_pr.find(qn("w:tblHeader"))
        if leaked_header is not None:
            prototype_pr.remove(leaked_header)
    while len(table.rows) < 2 + expected_rows:
        table._tbl.append(deepcopy(prototype))
    while len(table.rows) > 2 + expected_rows:
        table._tbl.remove(table.rows[-1]._tr)


def _month_cell_at(row, column_index: int):
    """Ячейка месяца по физическому tc, без remap python-docx после vMerge."""

    from docx.table import _Cell

    return _Cell(row._tr.tc_lst[column_index], row)


def _merge_month_cell_group(
    group_rows,
    columns: _TableColumns,
    month: str,
) -> None:
    """Объединить месяц внутри одного сегмента страницы через w:vMerge.

    Merge не пересекает границы страниц: сегменты считаются заранее, а на
    старте каждой новой страницы ставится pageBreakBefore. Поэтому:
    - одинаковые месяцы сливаются в один блок на странице;
    - переходная неделя («Сентябрь / Октябрь») остаётся отдельной строкой;
    - при продолжении месяца на новом листе подпись появляется снова;
    - текст (btLr + vAlign=center) оказывается строго по центру блока,
      начинаясь с первой строки сегмента (без пустой колонки вверху страницы).
    """
    if not group_rows:
        return

    # Важно брать tc до установки vMerge: иначе row.cells[i] начинает
    # возвращать ячейку restart и затирает подпись при очистке continue.
    cells = [_month_cell_at(row, columns.month) for row in group_rows]
    first_cell = cells[0]
    _format_month_cell(first_cell, month)
    if len(cells) == 1:
        _remove_vmerge(first_cell)
        return

    _set_vmerge_restart(first_cell)
    for cell in cells[1:]:
        _clear_month_continuation_cell(cell)
        _set_vmerge_continue(cell)


def _merge_month_cells(table, columns: _TableColumns, months: tuple[str, ...]) -> None:
    if not months:
        return

    group_start = 0
    for index in range(1, len(months) + 1):
        if index < len(months) and months[index] == months[group_start]:
            continue

        group_rows = table.rows[2 + group_start : 2 + index]
        _merge_month_cell_group(group_rows, columns, months[group_start])
        group_start = index


def _set_page_break_before_row(row, columns: _TableColumns) -> None:
    """Жёстко начать строку с новой страницы в Microsoft Word.

    Границы страниц считаются по пагинации Word. Явный pageBreakBefore
    закрепляет найденные старты страниц в итоговом DOCX, чтобы merge месяца
    не «уездал» при повторном открытии.
    """

    cell = row.cells[columns.theory]
    paragraph = cell.paragraphs[0]
    p_pr = paragraph._p.get_or_add_pPr()
    page_break = p_pr.find(qn("w:pageBreakBefore"))
    if page_break is None:
        p_pr.append(OxmlElement("w:pageBreakBefore"))


def _clear_page_break_before_row(row, columns: _TableColumns) -> None:
    cell = row.cells[columns.theory]
    for paragraph in cell.paragraphs:
        p_pr = paragraph._p.pPr
        if p_pr is None:
            continue
        page_break = p_pr.find(qn("w:pageBreakBefore"))
        if page_break is not None:
            p_pr.remove(page_break)


def _apply_explicit_page_breaks(
    table,
    columns: _TableColumns,
    rows_by_page: tuple[tuple[int, ...], ...],
) -> None:
    """Закрепить начало каждой рассчитанной страницы в самом DOCX."""

    for row in table.rows[2:]:
        _clear_page_break_before_row(row, columns)

    for page_rows in rows_by_page[1:]:
        if not page_rows:
            continue
        _set_page_break_before_row(table.rows[2 + page_rows[0]], columns)


def _merge_month_cells_by_page_segments(
    table,
    columns: _TableColumns,
    months: tuple[str, ...],
    rows_by_page: tuple[tuple[int, ...], ...],
) -> None:
    if not months:
        return

    for page_rows in rows_by_page:
        if not page_rows:
            continue

        group_start = page_rows[0]
        for index in range(1, len(page_rows)):
            row_index = page_rows[index]
            if months[row_index] != months[group_start]:
                group_rows = table.rows[2 + group_start : 2 + page_rows[index - 1] + 1]
                _merge_month_cell_group(group_rows, columns, months[group_start])
                group_start = row_index

        group_rows = table.rows[2 + group_start : 2 + page_rows[-1] + 1]
        _merge_month_cell_group(group_rows, columns, months[group_start])


def _write_document_header(
    document,
    utp: UtpParseResult,
    *,
    academic_year: str,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
    group_number: str | None = None,
    class_name: str | None = None,
) -> None:
    if document.paragraphs:
        document.paragraphs[0].text = "Календарный план"
    program_line = _program_header_line(
        utp,
        program_title=program_title,
        study_year_hints=study_year_hints,
    )
    year_line = f"{academic_year} учебный год"
    group_line = _group_class_line(group_number, class_name)
    if len(document.paragraphs) > 3:
        document.paragraphs[1].text = program_line
        document.paragraphs[2].text = year_line
        document.paragraphs[3].text = group_line
    elif len(document.paragraphs) > 2:
        document.paragraphs[1].text = f"{program_line}\n{year_line}"
        document.paragraphs[2].text = group_line
    elif len(document.paragraphs) > 1:
        document.paragraphs[1].text = f"{program_line}\n{year_line}\n{group_line}"
    for paragraph in document.paragraphs:
        blob = paragraph.text.casefold()
        if "название программы" in blob or "г.об" in blob:
            paragraph.text = program_line


def _populate_calendar_table(
    document,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    *,
    academic_year: str,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
    group_number: str | None = None,
    class_name: str | None = None,
) -> tuple:
    """Заполнить таблицу календаря строками данных (без объединения месяцев)."""

    resolved_title, resolved_hints = _resolve_header_from_rows(
        rows,
        program_title=program_title,
        study_year_hints=study_year_hints,
    )
    _write_document_header(
        document,
        utp,
        academic_year=academic_year,
        program_title=resolved_title,
        study_year_hints=resolved_hints,
        group_number=group_number,
        class_name=class_name,
    )

    table = document.tables[0]
    columns = _columns_for_table(table)
    display_numbers = _topic_display_numbers(utp)
    _ensure_data_rows(table, len(rows))
    _repeat_table_header_rows(table)

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

    months = tuple(lesson.source.source.month for lesson in rows)
    return table, columns, months


def _save_document(document) -> bytes:
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _merge_month_cells_for_pages(
    document,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    rows_by_page: tuple[tuple[int, ...], ...],
    *,
    academic_year: str,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
    group_number: str | None = None,
    class_name: str | None = None,
) -> bytes:
    """Собрать DOCX с объединением месяцев по сегментам страниц."""

    table, columns, months = _populate_calendar_table(
        document,
        utp,
        rows,
        academic_year=academic_year,
        program_title=program_title,
        study_year_hints=study_year_hints,
        group_number=group_number,
        class_name=class_name,
    )
    _apply_explicit_page_breaks(table, columns, rows_by_page)
    _merge_month_cells_by_page_segments(table, columns, months, rows_by_page)
    return _save_document(document)


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
    *,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
    group_number: str | None = None,
    class_name: str | None = None,
) -> bytes:
    """Сформировать DOCX: месяц совпадает с датой и merge не пересекает страницы."""

    header = {
        "academic_year": academic_year,
        "program_title": program_title,
        "study_year_hints": study_year_hints,
        "group_number": group_number,
        "class_name": class_name,
    }

    # Первый проход: без merge. Он нужен, чтобы получить фактическую пагинацию
    # Microsoft Word для текущего шаблона и объёма текста.
    preview_document = _load_template(template)
    _populate_calendar_table(preview_document, utp, rows, **header)
    preview = _save_document(preview_document)

    from calendar_pedagoga.docx_qa import detect_data_row_indices_by_page

    rows_by_page = detect_data_row_indices_by_page(preview, total_rows=len(rows))
    if not rows_by_page:
        # Без надёжной пагинации безопаснее оставить месяц в каждой строке,
        # чем объединить его через границу страницы и получить пустой столбец.
        return preview

    # После merge высоты строк могут слегка измениться. Каждый найденный старт
    # страницы Word фиксируем pageBreakBefore и пересобираем до стабилизации.
    for _ in range(6):
        document = _load_template(template)
        merged = _merge_month_cells_for_pages(
            document, utp, rows, rows_by_page, **header
        )
        detected = detect_data_row_indices_by_page(merged, total_rows=len(rows))
        if not detected or detected == rows_by_page:
            return merged
        rows_by_page = detected

    document = _load_template(template)
    return _merge_month_cells_for_pages(document, utp, rows, rows_by_page, **header)
