"""Чтение логических недель из сгенерированного календарного DOCX."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from docx import Document
from docx.table import _Cell

from calendar_pedagoga.docx_generation import _columns_for_table


@dataclass(frozen=True)
class ExtractedWeek:
    week_number: int
    date_range: str
    month: str
    theory: str
    theory_mark: str
    practice: str
    lesson_type: str
    planned_result: str
    assessment: str
    lesson_type_mirror: str | None
    planned_result_mirror: str | None


def _cell_text(row, index: int) -> str:
    return _Cell(row._tr.tc_lst[index], row).text


def _join_column(rows, index: int) -> str:
    return "".join(_cell_text(row, index) for row in rows)


def extract_logical_weeks(docx_content: bytes) -> tuple[ExtractedWeek, ...]:
    """Склеить физические page-split строки по номеру недели."""

    table = Document(BytesIO(docx_content)).tables[0]
    columns = _columns_for_table(table)
    groups: list[list] = []
    for row in table.rows[2:]:
        week_cell = _cell_text(row, columns.week)
        if not week_cell.strip():
            continue
        week_number = int(week_cell.splitlines()[0])
        if not groups or int(_cell_text(groups[-1][0], columns.week).splitlines()[0]) != week_number:
            groups.append([row])
        else:
            groups[-1].append(row)

    extracted: list[ExtractedWeek] = []
    for rows in groups:
        week_cell = _cell_text(rows[0], columns.week)
        lines = week_cell.splitlines()
        month = next(
            (text.strip() for row in rows if (text := _cell_text(row, columns.month)).strip()),
            "",
        )
        type_mirror = None
        result_mirror = None
        if columns.lesson_type_mirror is not None:
            type_mirror = _join_column(rows, columns.lesson_type_mirror)
        if columns.planned_result_mirror is not None:
            result_mirror = _join_column(rows, columns.planned_result_mirror)
        extracted.append(
            ExtractedWeek(
                week_number=int(lines[0]),
                date_range="\n".join(lines[1:]).strip(),
                month=month,
                theory=_join_column(rows, columns.theory),
                theory_mark=_join_column(rows, columns.theory_mark),
                practice=_join_column(rows, columns.practice),
                lesson_type=_join_column(rows, columns.lesson_type),
                planned_result=_join_column(rows, columns.planned_result),
                assessment=_join_column(rows, columns.assessment),
                lesson_type_mirror=type_mirror,
                planned_result_mirror=result_mirror,
            )
        )
    return tuple(extracted)
