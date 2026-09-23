"""Генерация итогового календарного плана DOCX на основе шаблона и данных УТП."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from math import ceil
from pathlib import Path
import re

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from calendar_pedagoga.lesson_resolution import ResolvedLessonRow
from calendar_pedagoga.lesson_display import format_schedule_channel_cell
from calendar_pedagoga.organization_template import CalendarTemplateSelection, CalendarTemplateSource
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.content_generation import WeekTopicPart
from calendar_pedagoga.academic_year import normalize_academic_year
from calendar_pedagoga.program_parsing import infer_study_year_number
from calendar_pedagoga.generator_revision import generator_provenance


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
STANDARD_TEMPLATE_PATH = _PROJECT_ROOT / "references" / "Календарный план Образец.docx"
STANDARD_TABLE_FONT_FAMILY = "Times New Roman"
CALENDAR_BODY_FONT_SIZE_PT = 11
STANDARD_COMPACT_COLUMN_WEIGHTS = (600, 650, 2100, 1800, 3950, 1800, 2500, 2477)
STANDARD_HORIZONTAL_CELL_MARGIN_DXA = 45
STANDARD_CHARACTER_SCALE_PERCENT = 100
STANDARD_GROUP_SPACE_AFTER_PT = 8
ORGANIZATION_YEAR_SPACE_AFTER_PT = 8
ORGANIZATION_HEADER_TABLE_GAP_PT = 8
_MONTH_CELL_MARGIN_DXA = 40
PRINT_TOP_MARGIN_CM = 1.0


def _week_from_cells(cells) -> str:
    if len(cells) < 2:
        return ""
    lines = (cells[1] or "").splitlines()
    return lines[0].strip() if lines else ""


def _raise_docx_qa(stage: str, message: str, *, content: bytes | None = None, **extra) -> None:
    """Log fail-closed QA details, then raise the same ValueError as before."""

    from calendar_pedagoga.docx_qa import log_docx_qa_failure

    try:
        raise ValueError(message)
    except ValueError as error:
        log_docx_qa_failure(stage, error, content=content, **extra)
        raise


def _layouts_summary(layouts) -> dict:
    if not layouts:
        return {"logical_rows": 0, "pages": None, "split_rows": []}
    pages = max(layout.span.end_page for layout in layouts)
    split_rows = [
        {
            "logical_row": index + 1,
            "week": _week_from_cells(layout.segments[0].cells) if layout.segments else "",
            "pages": [layout.span.start_page, layout.span.end_page],
            "segments": len(layout.segments),
        }
        for index, layout in enumerate(layouts)
        if len(layout.segments) > 1
    ]
    return {
        "logical_rows": len(layouts),
        "pages": pages,
        "first_row_page": layouts[0].span.start_page,
        "split_rows": split_rows,
    }


def _span_failure(spans, continuation_rows, table) -> dict:
    """First physical row whose page-span is missing or still crosses a page."""

    rows = table.rows[2:]
    if spans is None:
        return {
            "result": "unavailable",
            "physical_rows": len(rows),
            "pages": None,
        }
    pages = max((span.end_page for span in spans), default=None)
    for index, span in enumerate(spans):
        crosses = span.start_page != span.end_page
        unsafe = index in continuation_rows and not span.split_safe
        if not crosses and not unsafe:
            continue
        week = _week_from_cells([cell.text for cell in rows[index].cells]) if index < len(rows) else ""
        return {
            "result": "crosses_page" if crosses else "unconfirmed_split",
            "physical_row": index + 1,
            "week": week,
            "start_page": span.start_page,
            "end_page": span.end_page,
            "split_safe": span.split_safe,
            "physical_rows": len(spans),
            "pages": pages,
        }
    return {
        "result": "ok",
        "physical_rows": len(spans),
        "pages": pages,
    }


DATA_ROW_LINE_SPACING_TWIPS = 220


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


def _normalize_run_character_spacing(run) -> None:
    properties = run._r.get_or_add_rPr()
    width = properties.find(qn("w:w"))
    if width is None:
        width = OxmlElement("w:w")
        properties.append(width)
    width.set(qn("w:val"), str(STANDARD_CHARACTER_SCALE_PERCENT))
    for spacing in list(properties.findall(qn("w:spacing"))):
        properties.remove(spacing)


def _apply_standard_table_character_spacing(table) -> None:
    """Use normal character width and spacing throughout the calendar table."""

    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    _normalize_run_character_spacing(run)


def _apply_standard_narrow_column_typography(table) -> None:
    """Keep the theory heading on word boundaries without glyph distortion."""

    if len(table.rows) < 2:
        return
    columns = _columns_for_table(table)
    header = table.rows[1].cells[columns.theory].paragraphs[0]
    words = header.text.split()
    if len(words) == 2 and header.runs:
        primary = header.runs[0]
        for run in header.runs:
            run.text = ""
        primary.text = words[0]
        primary.add_break()
        primary.add_text(words[1])

    _apply_standard_table_character_spacing(table)


def _apply_standard_compact_table_layout(table) -> None:
    """Use content-oriented widths and small horizontal padding.

    The proportions come from the compact standard calendar structure and are
    scaled to the template's actual printable table width.  No row height is
    imposed: renderers size data rows from their 11 pt contents.
    """

    grid = table._tbl.tblGrid.findall(qn("w:gridCol"))
    if len(grid) != len(STANDARD_COMPACT_COLUMN_WEIGHTS):
        return
    current = [int(column.get(qn("w:w"), "0")) for column in grid]
    total = sum(current)
    weight_total = sum(STANDARD_COMPACT_COLUMN_WEIGHTS)
    if total <= 0 or weight_total <= 0:
        return
    widths = [round(total * weight / weight_total) for weight in STANDARD_COMPACT_COLUMN_WEIGHTS]
    widths[-1] += total - sum(widths)
    for column, width in zip(grid, widths):
        column.set(qn("w:w"), str(width))

    for row in table.rows:
        cursor = 0
        for tc in row._tr.tc_lst:
            tc_pr = tc.get_or_add_tcPr()
            span = tc_pr.find(qn("w:gridSpan"))
            count = int(span.get(qn("w:val"), "1")) if span is not None else 1
            tc_width = tc_pr.find(qn("w:tcW"))
            if tc_width is None:
                tc_width = OxmlElement("w:tcW")
                tc_pr.insert(0, tc_width)
            tc_width.set(qn("w:w"), str(sum(widths[cursor : cursor + count])))
            tc_width.set(qn("w:type"), "dxa")
            cursor += count

    for row in table.rows[2:]:
        properties = row._tr.find(qn("w:trPr"))
        height = properties.find(qn("w:trHeight")) if properties is not None else None
        if height is not None:
            properties.remove(height)

    tbl_pr = table._tbl.tblPr
    margins = tbl_pr.find(qn("w:tblCellMar"))
    if margins is None:
        margins = OxmlElement("w:tblCellMar")
        tbl_pr.append(margins)
    for side_name in ("left", "right"):
        side = margins.find(qn(f"w:{side_name}"))
        if side is None:
            side = OxmlElement(f"w:{side_name}")
            margins.append(side)
        side.set(qn("w:w"), str(STANDARD_HORIZONTAL_CELL_MARGIN_DXA))
        side.set(qn("w:type"), "dxa")

    _apply_standard_narrow_column_typography(table)


def _prevent_row_split(row) -> None:
    """Не разрывать строку таблицы между страницами."""

    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _allow_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    for marker in list(tr_pr.findall(qn("w:cantSplit"))):
        tr_pr.remove(marker)
    for marker in row._tr.xpath(".//w:pPr/w:pageBreakBefore"):
        marker.getparent().remove(marker)


def _protect_vertical_cell_height(table) -> None:
    """Give rotated identifiers an intrinsic minimum, never an exact height.

    Word's automatic row sizing can ignore the inline extent of rotated text.
    Measure explicit lines without wrapping: after rotation their width is the
    required row height. Paragraph/line spacing lies on the perpendicular axis;
    retaining an unwrapped line prevents Word from adding a clipped third line.
    """
    import pymupdf

    table_styles = []
    style = table.style
    while style is not None:
        table_styles.append(style.element)
        style = style.base_style
    defaults = table.part.document.styles.element

    def value(elements, path, attribute, default):
        for element in elements:
            if element is not None:
                matches = element.xpath(path)
                if matches and matches[0].get(qn('w:' + attribute)) is not None:
                    return matches[0].get(qn('w:' + attribute))
        return default

    fonts = {}
    for row in table.rows[2:]:
        minimum = 0.0
        for cell in row.cells:
            direction = value([cell._tc], './w:tcPr/w:textDirection', 'val', '')
            if direction not in ('btLr', 'tbRl') or not cell.text.strip():
                continue
            margins = {}
            for side in ('left', 'right', 'top', 'bottom'):
                inherited = value([table._tbl] + table_styles,
                                  './w:tblPr/w:tblCellMar/w:' + side, 'w', '0')
                margins[side] = float(value([cell._tc],
                    './w:tcPr/w:tcMar/w:' + side, 'w', inherited)) / 20
            for paragraph in cell.paragraphs:
                styles = []
                style = paragraph.style
                while style is not None:
                    styles.append(style.element)
                    style = style.base_style
                inherited = styles + table_styles
                line_width = widest = largest = 0.0
                for run in paragraph.runs:
                    run_styles = []
                    style = run.style
                    while style is not None:
                        run_styles.append(style.element)
                        style = style.base_style
                    sources = [run._r] + run_styles + inherited
                    default_size = value([defaults], './w:docDefaults/w:rPrDefault/w:rPr/w:sz', 'val', '24')
                    size = float(value(sources, './w:rPr/w:sz', 'val', default_size)) / 2
                    family = value(sources, './w:rPr/w:rFonts', 'ascii', 'Times New Roman').lower()
                    bold = value(sources, './w:rPr/w:b', 'val', '0') not in ('0', 'false', 'off')
                    # MuPDF supplies portable serif/sans metrics and Unicode
                    # fallback glyphs; no installed desktop font is required.
                    face = ('hebo' if bold else 'helv') if any(
                        name in family for name in ('arial', 'calibri', 'sans')) else ('tibo' if bold else 'tiro')
                    if face not in fonts:
                        fonts[face] = pymupdf.Font(face)
                    largest = max(largest, size)
                    for index, text in enumerate(run.text.replace('\r', '\n').split('\n')):
                        if index:
                            widest = max(widest, line_width)
                            line_width = 0
                        line_width += fonts[face].text_length(text, fontsize=size)
                widest = max(widest, line_width)
                # Include indents in the rotated inline axis. Line/paragraph
                # spacing is not added to this axis; it is preserved unchanged.
                indents = sum(float(value([paragraph._p] + inherited,
                    './w:pPr/w:ind', side, '0')) / 20 for side in ('left', 'right'))
                # A small font-relative allowance covers glyph overhang and
                # renderer rounding, in addition to the inherited cell padding.
                minimum = max(minimum, widest + indents + margins['left'] +
                              margins['right'] + margins['top'] + margins['bottom'] + largest / 4)
        if minimum:
            properties = row._tr.get_or_add_trPr()
            height = properties.find(qn('w:trHeight'))
            if height is None:
                height = OxmlElement('w:trHeight')
                properties.append(height)
            height.set(qn('w:val'), str(max(ceil(minimum * 20), int(height.get(qn('w:val'), '0')))))
            height.set(qn('w:hRule'), 'atLeast')


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


_GROUP_CLASS_RE = re.compile(
    r"Группа\s*№\s*(?:_{2,}|\S+)\s*\(\s*Класс\s+(?:_{2,}|\S+)\s*\)",
    flags=re.IGNORECASE,
)


def _group_class_line(
    group_number: str | None = None,
    class_name: str | None = None,
) -> str:
    group = (group_number or "").strip() or "___________"
    klass = (class_name or "").strip() or "_________"
    return f"Группа № {group} (Класс {klass})"


def _replace_group_class_prefix(
    text: str,
    group_number: str | None,
    class_name: str | None,
) -> str:
    if "группа" not in text.casefold() or not (group_number or class_name):
        return text
    if not _GROUP_CLASS_RE.search(text):
        return text
    return _GROUP_CLASS_RE.sub(_group_class_line(group_number, class_name), text, count=1)


def _append_teacher_name_to_group_line(text: str, teacher_name: str | None) -> str:
    if "группа" not in text.casefold():
        return text
    match = _GROUP_CLASS_RE.search(text.replace("\t", ""))
    if match is None:
        match = _GROUP_CLASS_RE.search(text)
    if match is None:
        return text
    group = match.group(0)
    name = (teacher_name or "").strip()
    if name:
        return f"\t{group}\t{name}"
    return f"\t{group}"


def _content_width_twips(document) -> int:
    section = document.sections[0]
    return int(
        section.page_width.twips - section.left_margin.twips - section.right_margin.twips
    )


def _apply_group_teacher_tabs(paragraph, document) -> None:
    """Группа/класс по центру, ФИО справа: один абзац, center tab + right tab."""

    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    width = _content_width_twips(document)
    properties = paragraph._p.get_or_add_pPr()
    tabs = properties.find(qn("w:tabs"))
    if tabs is None:
        tabs = OxmlElement("w:tabs")
        properties.append(tabs)
    for old in list(tabs.findall(qn("w:tab"))):
        tabs.remove(old)
    center = OxmlElement("w:tab")
    center.set(qn("w:val"), "center")
    center.set(qn("w:pos"), str(width // 2))
    tabs.append(center)
    right = OxmlElement("w:tab")
    right.set(qn("w:val"), "right")
    right.set(qn("w:pos"), str(width))
    tabs.append(right)


def _enable_cell_wrap(cell) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    no_wrap = tc_pr.find(qn("w:noWrap"))
    if no_wrap is not None:
        tc_pr.remove(no_wrap)


def _first_run_properties(paragraph):
    for run in paragraph.runs:
        properties = run._r.find(qn("w:rPr"))
        if properties is not None:
            return deepcopy(properties)
    paragraph_properties = paragraph._p.find(qn("w:pPr"))
    if paragraph_properties is not None:
        properties = paragraph_properties.find(qn("w:rPr"))
        if properties is not None:
            return deepcopy(properties)
    return None


def _strip_header_sample_paragraph_layout(paragraph_properties) -> None:
    """Убрать свойства пустой/заголовочной строки, которые растягивают данные.

    Шапка таблицы не вызывается отсюда. Размер и шрифт остаются.
    firstLine/tabs сужают колонку; jc=center — выравнивание шапки, не текста данных.
    Без явного spacing действует docDefaults шаблона (after=200, line=276).
    """

    if paragraph_properties is None:
        return
    for tag in ("w:ind", "w:tabs", "w:jc"):
        node = paragraph_properties.find(qn(tag))
        if node is not None:
            paragraph_properties.remove(node)
    spacing = paragraph_properties.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        paragraph_properties.append(spacing)
    spacing.set(qn("w:before"), "0")
    spacing.set(qn("w:after"), "0")
    spacing.set(qn("w:line"), str(DATA_ROW_LINE_SPACING_TWIPS))
    spacing.set(qn("w:lineRule"), "auto")
    snap = paragraph_properties.find(qn("w:snapToGrid"))
    if snap is None:
        snap = OxmlElement("w:snapToGrid")
        paragraph_properties.append(snap)
    snap.set(qn("w:val"), "0")


def _strip_cloned_sample_row_layout(table_row) -> None:
    """Строка данных не наследует фиксированную высоту образца шапки/пустышки."""

    row_properties = table_row.find(qn("w:trPr"))
    if row_properties is not None:
        height = row_properties.find(qn("w:trHeight"))
        if height is not None:
            row_properties.remove(height)
        leaked_header = row_properties.find(qn("w:tblHeader"))
        if leaked_header is not None:
            row_properties.remove(leaked_header)
    for cell in table_row.findall(qn("w:tc")):
        for paragraph in cell.findall(qn("w:p")):
            paragraph_properties = paragraph.find(qn("w:pPr"))
            if paragraph_properties is None:
                paragraph_properties = OxmlElement("w:pPr")
                paragraph.insert(0, paragraph_properties)
            _strip_header_sample_paragraph_layout(paragraph_properties)


def _without_bold(properties):
    """Свойства шрифта без начертания: данные календаря должны быть regular."""

    if properties is None:
        return None
    cleaned = deepcopy(properties)
    for tag in ("w:b", "w:bCs"):
        node = cleaned.find(qn(tag))
        if node is not None:
            cleaned.remove(node)
    bold = OxmlElement("w:b")
    bold.set(qn("w:val"), "0")
    bold_cs = OxmlElement("w:bCs")
    bold_cs.set(qn("w:val"), "0")
    cleaned.insert(0, bold_cs)
    cleaned.insert(0, bold)
    return cleaned


def _set_run_font_family(run, family: str) -> None:
    """Явно закрепить одно семейство шрифта для всех наборов символов OOXML."""

    run.font.name = family
    run_properties = run._r.get_or_add_rPr()
    fonts = run_properties.get_or_add_rFonts()
    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(qn(f"w:{attribute}"), family)
    for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        qualified = qn(f"w:{attribute}")
        if qualified in fonts.attrib:
            del fonts.attrib[qualified]


def _apply_standard_table_font(table) -> None:
    """Унифицировать шрифт стандартной таблицы, сохранив размер и начертание."""

    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    _set_run_font_family(run, STANDARD_TABLE_FONT_FAMILY)


def _apply_calendar_body_font_size(table) -> None:
    """Установить 11 pt только для строк календарного плана, сохранив шапку."""

    for row in table.rows[2:]:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(CALENDAR_BODY_FONT_SIZE_PT)


def _apply_standard_header_font(document) -> None:
    """Закрепить шрифт создаваемой шапки standard-template без смены начертания."""

    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            _set_run_font_family(run, STANDARD_TABLE_FONT_FAMILY)


def _set_paragraph_text_keep_format(paragraph, text: str, run_properties=None) -> None:
    """Заменить текст абзаца, сохранив pPr и rPr шаблона."""

    seed = run_properties if run_properties is not None else _first_run_properties(paragraph)
    for child in list(paragraph._p):
        if child.tag == qn("w:r"):
            paragraph._p.remove(child)
    run = paragraph.add_run(text)
    if seed is not None:
        run._r.insert(0, deepcopy(seed))


def _set_cell_text(cell, value: str) -> None:
    """Подставить текст ячейки, сохранив шрифт и размер.

    Переносы строк — мягкие w:br в одном абзаце, как при cell.text,
    а не отдельные w:p: лишние абзацы наследуют межстрочный зазор шаблона
    и растягивают строку таблицы.
    """

    _enable_cell_wrap(cell)
    paragraphs = list(cell.paragraphs)
    if not paragraphs:
        cell.add_paragraph()
        paragraphs = list(cell.paragraphs)
    seed_paragraph = paragraphs[0]
    seed_rpr = _without_bold(_first_run_properties(seed_paragraph))
    seed_ppr = seed_paragraph._p.get_or_add_pPr()
    _strip_header_sample_paragraph_layout(seed_ppr)
    mark = seed_ppr.find(qn("w:rPr"))
    if mark is not None:
        for tag in ("w:b", "w:bCs"):
            node = mark.find(qn(tag))
            if node is not None:
                mark.remove(node)
    for extra in list(cell.paragraphs)[1:]:
        cell._tc.remove(extra._p)
    _set_paragraph_text_keep_format(seed_paragraph, value, seed_rpr)


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


def _prepare_week_cell(cell, value: str) -> None:
    """Keep the vertical week/date identifier centered after any text rewrite."""

    _set_cell_text(cell, value)
    _ensure_text_direction_bt_lr(cell)
    _center_cell_text(cell)


def _format_month_cell(cell, month: str) -> None:
    """Подпись месяца по центру объединённого блока на странице."""

    _prepare_month_cell(cell, month)


def _clear_month_continuation_cell(cell) -> None:
    _set_cell_text(cell, "")
    _ensure_text_direction_bt_lr(cell)
    _set_cell_vertical_align(cell, "center")
    _set_month_paragraph_format(cell)


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
    _prepare_week_cell(cells[columns.week], f"{week_number}\n{date_range}")
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


def _topic_part_key(part: WeekTopicPart) -> tuple[str | None, str, str]:
    return (part.topic_number, part.topic_title, part.section)


def _content_occurrence_key(
    part: WeekTopicPart,
) -> tuple[str | None, str, str, str]:
    return (
        part.topic_number,
        part.topic_title,
        part.section,
        (part.program_topic or "").strip(),
    )


def _practice_appearance_counts(
    rows: tuple[ResolvedLessonRow, ...],
) -> dict[tuple[str | None, str, str, str], int]:
    counts: dict[tuple[str | None, str, str, str], int] = {}
    for lesson in rows:
        for part in _week_topic_parts(lesson.source.source):
            if part.practice_hours <= 0:
                continue
            key = _content_occurrence_key(part)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _topic_cells_for_lesson(
    lesson: ResolvedLessonRow,
    display_numbers: dict[tuple[str | None, str, str], str],
    *,
    topic_counts: dict[tuple[str | None, str, str, str], int],
    topic_occurrences: dict[tuple[str | None, str, str, str], int],
) -> tuple[str, str]:
    del topic_counts
    source_row = lesson.source.source
    theory_lines: list[str] = []
    practice_lines: list[str] = []
    for part in _week_topic_parts(source_row):
        display_key = _topic_part_key(part)
        occurrence_key = _content_occurrence_key(part)
        if part.practice_hours:
            topic_occurrences[occurrence_key] = topic_occurrences.get(occurrence_key, 0) + 1
        display_number = display_numbers.get(display_key, part.topic_number or "?")
        # Allocated SOURCE stays on the part for SentenceFrame; the table prints
        # quoted work/catalog shorts when present, otherwise the UTP title.
        theory_cell = format_schedule_channel_cell(
            display_number,
            part.topic_title,
            part.theory_hours,
            part.theory_units,
        )
        practice_cell = format_schedule_channel_cell(
            display_number,
            part.topic_title,
            part.practice_hours,
            part.practice_units,
        )
        if theory_cell:
            theory_lines.append(theory_cell)
        if practice_cell:
            practice_lines.append(practice_cell)
    return "\n".join(theory_lines), "\n".join(practice_lines)


def _apply_print_safe_margins(document) -> None:
    """Верхнее поле 1 см — заголовок и шапка таблицы в безопасной зоне печати."""

    target = Cm(PRINT_TOP_MARGIN_CM)
    for section in document.sections:
        section.top_margin = target


def _load_template(template: CalendarTemplateSelection) -> Document:
    if template.source is CalendarTemplateSource.STANDARD:
        if not STANDARD_TEMPLATE_PATH.is_file():
            raise FileNotFoundError(
                f"Стандартный шаблон не найден: {STANDARD_TEMPLATE_PATH}"
            )
        document = Document(str(STANDARD_TEMPLATE_PATH))
    else:
        assert template.content is not None
        document = Document(BytesIO(template.content))
    _apply_print_safe_margins(document)
    return document


def _ensure_data_rows(table, expected_rows: int) -> None:
    source_index = 2 if len(table.rows) > 2 else len(table.rows) - 1
    prototype = deepcopy(table.rows[source_index]._tr)
    _strip_cloned_sample_row_layout(prototype)
    while len(table.rows) < 2 + expected_rows:
        table._tbl.append(deepcopy(prototype))
    while len(table.rows) > 2 + expected_rows:
        table._tbl.remove(table.rows[-1]._tr)
    for row in table.rows[2:]:
        _strip_cloned_sample_row_layout(row._tr)


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

    Сегменты включают только целые строки одной измеренной страницы;
    после merge границы проверяются повторным рендером. Поэтому:
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
            if (months[row_index] != months[group_start]
                    or row_index != page_rows[index - 1] + 1):
                group_rows = table.rows[2 + group_start : 2 + page_rows[index - 1] + 1]
                _merge_month_cell_group(group_rows, columns, months[group_start])
                group_start = row_index

        group_rows = table.rows[2 + group_start : 2 + page_rows[-1] + 1]
        _merge_month_cell_group(group_rows, columns, months[group_start])


def _quoted_program_name(program_title: str | None, utp: UtpParseResult) -> str:
    program = (program_title or utp.metadata.program_name or "").strip().strip("«»\"'")
    return program


def _study_year_number(utp: UtpParseResult, study_year_hints: tuple[str | None, ...]) -> int | None:
    for raw in (utp.metadata.study_year, *study_year_hints):
        number = infer_study_year_number(raw)
        if number is not None:
            return number
    return None


def _fill_hours_paren(paren: str, *, weekly: int | None, yearly: int | None) -> str:
    weekly_slot = "недел" in paren.casefold()
    hours = weekly if weekly_slot and weekly else (yearly or weekly)
    if not hours:
        return paren
    if re.search(r"\d+", paren):
        return re.sub(r"\d+", str(hours), paren, count=1)
    if weekly_slot:
        return f"({hours} {_hours_word(hours)} в неделю)"
    return f"({hours})"


def _fill_organization_header_paragraph(
    text: str,
    utp: UtpParseResult,
    *,
    program_title: str | None,
    study_year_hints: tuple[str | None, ...],
    group_number: str | None,
    class_name: str | None,
    teacher_name: str | None = None,
) -> str:
    updated = text
    program = _quoted_program_name(program_title, utp)
    if program and "«" in updated and "»" in updated:
        updated = re.sub(r"«[^»]*»", f"«{program}»", updated, count=1)
    elif program and "название программы" in updated.casefold():
        updated = re.sub(r"название программы", program, updated, count=1, flags=re.IGNORECASE)

    year_number = _study_year_number(utp, study_year_hints)
    if year_number is not None:
        if re.search(r"(?:_{2,}|\d+)\s+год обучения", updated, flags=re.IGNORECASE):
            updated = re.sub(
                r"(?:_{2,}|\d+)\s+год обучения",
                f"{year_number} год обучения",
                updated,
                count=1,
                flags=re.IGNORECASE,
            )
        elif re.search(r"(?:_{2,}|\d+)\s*г\.об", updated, flags=re.IGNORECASE):
            updated = re.sub(
                r"(?:_{2,}|\d+)\s*г\.об\.?",
                f"{year_number} г.об.",
                updated,
                count=1,
                flags=re.IGNORECASE,
            )

    if "(" in updated and ")" in updated and (
        "час" in updated.casefold() or "чса" in updated.casefold() or "недел" in updated.casefold()
    ):
        updated = re.sub(
            r"\([^()]*\)",
            lambda match: _fill_hours_paren(
                match.group(0),
                weekly=utp.metadata.hours_per_week,
                yearly=utp.metadata.hours_per_year,
            ),
            updated,
            count=1,
        )

    updated = _replace_group_class_prefix(updated, group_number, class_name)
    return _append_teacher_name_to_group_line(updated, teacher_name)


def _paragraph_before_first_table(document):
    """Последний body-абзац перед первой таблицей, без абзацев внутри таблицы."""

    last_element = None
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:tbl"):
            break
        if child.tag == qn("w:p"):
            last_element = child
    if last_element is None:
        return None
    for paragraph in document.paragraphs:
        if paragraph._p is last_element:
            return paragraph
    return None


_ACADEMIC_YEAR_LINE_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}"
    r"\s+учебный\s+год(?!\w)",
    flags=re.IGNORECASE,
)


def _fill_organization_academic_year(
    document, academic_year: str | None
) -> bool:
    """Обновить только существующую в загруженном шаблоне строку учебного года."""

    canonical = normalize_academic_year(academic_year)
    if canonical is None:
        return False
    for paragraph in document.paragraphs:
        original = paragraph.text
        if not _ACADEMIC_YEAR_LINE_RE.search(original):
            continue
        updated = _ACADEMIC_YEAR_LINE_RE.sub(
            f"{canonical} учебный год",
            original,
            count=1,
        )
        if updated != original:
            _set_paragraph_text_keep_format(paragraph, updated)
        paragraph.paragraph_format.space_after = Pt(
            ORGANIZATION_YEAR_SPACE_AFTER_PT
        )
        return True
    return False


def _compact_empty_header_spacer(document) -> None:
    """Оставить аккуратный интервал, не отдавая целую строку абзацу."""

    spacer = _paragraph_before_first_table(document)
    if spacer is None or spacer.text.strip():
        return
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(0)
    spacer.paragraph_format.line_spacing = Pt(ORGANIZATION_HEADER_TABLE_GAP_PT)


def _fill_organization_header(
    document,
    utp: UtpParseResult,
    *,
    academic_year: str | None = None,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
    group_number: str | None = None,
    class_name: str | None = None,
    teacher_name: str | None = None,
) -> None:
    """Подставить значения в шапку шаблона организации, не меняя её состав и стили."""

    for paragraph in document.paragraphs:
        original = paragraph.text
        if not original.strip():
            continue
        updated = _fill_organization_header_paragraph(
            original,
            utp,
            program_title=program_title,
            study_year_hints=study_year_hints,
            group_number=group_number,
            class_name=class_name,
            teacher_name=teacher_name,
        )
        if updated != original:
            _set_paragraph_text_keep_format(paragraph, updated)
        if "группа" in paragraph.text.casefold() and _GROUP_CLASS_RE.search(
            paragraph.text.replace("\t", "")
        ):
            _apply_group_teacher_tabs(paragraph, document)
    if not _fill_organization_academic_year(document, academic_year):
        _compact_empty_header_spacer(document)


def _write_document_header(
    document,
    utp: UtpParseResult,
    *,
    academic_year: str,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
    group_number: str | None = None,
    class_name: str | None = None,
    teacher_name: str | None = None,
    uses_organization_template: bool = False,
) -> None:
    if uses_organization_template:
        _fill_organization_header(
            document,
            utp,
            academic_year=academic_year,
            program_title=program_title,
            study_year_hints=study_year_hints,
            group_number=group_number,
            class_name=class_name,
            teacher_name=teacher_name,
        )
        return

    if document.paragraphs:
        _set_paragraph_text_keep_format(document.paragraphs[0], "Календарный план")
    program_line = _program_header_line(
        utp,
        program_title=program_title,
        study_year_hints=study_year_hints,
    )
    year_line = f"{academic_year} учебный год"
    group_line = _group_class_line(group_number, class_name)
    teacher = (teacher_name or "").strip()
    group_teacher_line = f"\t{group_line}\t{teacher}" if teacher else group_line
    group_paragraph = None
    if len(document.paragraphs) > 3:
        _set_paragraph_text_keep_format(document.paragraphs[1], program_line)
        _set_paragraph_text_keep_format(document.paragraphs[2], year_line)
        group_paragraph = document.paragraphs[3]
        _set_paragraph_text_keep_format(group_paragraph, group_teacher_line)
    elif len(document.paragraphs) > 2:
        _set_paragraph_text_keep_format(
            document.paragraphs[1], f"{program_line}\n{year_line}"
        )
        group_paragraph = document.paragraphs[2]
        _set_paragraph_text_keep_format(group_paragraph, group_teacher_line)
    elif len(document.paragraphs) > 1:
        _set_paragraph_text_keep_format(
            document.paragraphs[1],
            f"{program_line}\n{year_line}\n{group_teacher_line}",
        )
        group_paragraph = document.paragraphs[1]
    if teacher and group_paragraph is not None:
        _apply_group_teacher_tabs(group_paragraph, document)
    if group_paragraph is not None:
        group_paragraph.paragraph_format.space_after = Pt(
            STANDARD_GROUP_SPACE_AFTER_PT
        )
    for paragraph in document.paragraphs:
        blob = paragraph.text.casefold()
        if "название программы" in blob or "г.об" in blob:
            _set_paragraph_text_keep_format(paragraph, program_line)
    _apply_standard_header_font(document)


def _write_draft_notice(document, table, review_weeks: tuple[int, ...]) -> None:
    """Review notes stay in the UI; the calendar DOCX has no draft stamps."""

    del document, table, review_weeks


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
    teacher_name: str | None = None,
    uses_organization_template: bool = False,
    draft_review_weeks: tuple[int, ...] = (),
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
        teacher_name=teacher_name,
        uses_organization_template=uses_organization_template,
    )

    table = document.tables[0]
    _write_draft_notice(document, table, draft_review_weeks)
    columns = _columns_for_table(table)
    display_numbers = _topic_display_numbers(utp)
    _ensure_data_rows(table, len(rows))
    _repeat_table_header_rows(table)
    topic_counts = _practice_appearance_counts(rows)
    topic_occurrences: dict[tuple[str | None, str, str], int] = {}

    for index, lesson in enumerate(rows):
        theory_cell, practice_cell = _topic_cells_for_lesson(
            lesson,
            display_numbers,
            topic_counts=topic_counts,
            topic_occurrences=topic_occurrences,
        )
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

    if not uses_organization_template:
        _apply_standard_compact_table_layout(table)
        _apply_standard_table_font(table)
    _apply_calendar_body_font_size(table)

    if uses_organization_template:
        _protect_vertical_cell_height(table)

    months = tuple(lesson.source.source.month for lesson in rows)
    return table, columns, months


def _inject_generator_provenance(content: bytes) -> bytes:
    """Attach hidden custom properties without changing visible document body."""

    from zipfile import ZIP_DEFLATED, ZipFile

    props = generator_provenance(_PROJECT_ROOT)
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        (
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
            'custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/'
            '2006/docPropsVTypes">'
        ),
    ]
    for index, (name, value) in enumerate(props.items(), start=1):
        safe_name = name.replace('"', "")
        safe_value = (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        lines.append(
            f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" '
            f'pid="{index + 1}" name="{safe_name}">'
            f"<vt:lpwstr>{safe_value}</vt:lpwstr></property>"
        )
    lines.append("</Properties>")
    custom_xml = "\n".join(lines).encode("utf-8")

    source = BytesIO(content)
    target = BytesIO()
    with ZipFile(source, "r") as zin, ZipFile(target, "w", compression=ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "[Content_Types].xml":
                text = data.decode("utf-8")
                if "docProps/custom.xml" not in text:
                    overlay = (
                        '<Override PartName="/docProps/custom.xml" '
                        'ContentType="application/vnd.openxmlformats-officedocument.'
                        'custom-properties+xml"/>'
                    )
                    text = text.replace("</Types>", overlay + "</Types>")
                data = text.encode("utf-8")
            elif info.filename == "_rels/.rels":
                text = data.decode("utf-8")
                if "custom-properties" not in text:
                    rel = (
                        '<Relationship Id="rIdGeneratorCustom" '
                        'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                        'relationships/custom-properties" Target="docProps/custom.xml"/>'
                    )
                    text = text.replace("</Relationships>", rel + "</Relationships>")
                data = text.encode("utf-8")
            elif info.filename == "docProps/custom.xml":
                continue
            zout.writestr(info, data)
        zout.writestr("docProps/custom.xml", custom_xml)
    return target.getvalue()


def _save_document(document) -> bytes:
    buffer = BytesIO()
    document.save(buffer)
    return _inject_generator_provenance(buffer.getvalue())


def _apply_page_row_segments(
    table,
    layouts,
    *,
    protect_vertical_height: bool = True,
) -> tuple[tuple[str, ...], frozenset[int]]:
    """Project logical rows into exact, non-splitting physical page segments."""

    from docx.table import _Cell

    columns = _columns_for_table(table)
    logical_rows = [deepcopy(row._tr) for row in table.rows[2:]]
    logical_texts = [[cell.text for cell in row.cells] for row in table.rows[2:]]
    if len(logical_rows) != len(layouts):
        _raise_docx_qa(
            "apply_page_row_segments",
            "Не удалось сопоставить строки календаря с page-segments.",
            logical_rows=len(logical_rows),
            layout_rows=len(layouts),
            page_segmentation=_layouts_summary(layouts),
        )
    for row in list(table.rows[2:]):
        table._tbl.remove(row._tr)

    months: list[str] = []
    continuation_rows: set[int] = set()
    physical_index = 0
    for logical_index, (logical_row, source_cells, layout) in enumerate(
        zip(logical_rows, logical_texts, layouts)
    ):
        week = _week_from_cells(source_cells)
        if not layout.segments:
            _raise_docx_qa(
                "apply_page_row_segments",
                "Получена пустая сегментация строки календаря.",
                logical_row=logical_index + 1,
                week=week,
                physical_rows=physical_index,
                page_segmentation=_layouts_summary(layouts),
            )
        for column in range(2, len(source_cells)):
            restored = "".join(segment.cells[column] for segment in layout.segments)
            if restored != source_cells[column]:
                _raise_docx_qa(
                    "apply_page_row_segments",
                    "Page-segmentation не восстанавливает исходное содержимое строки.",
                    logical_row=logical_index + 1,
                    week=week,
                    column=column,
                    physical_rows=physical_index,
                    page_segmentation=_layouts_summary(layouts),
                )
        split = len(layout.segments) > 1
        for segment_index, segment in enumerate(layout.segments):
            if len(segment.cells) != len(logical_row.tc_lst):
                _raise_docx_qa(
                    "apply_page_row_segments",
                    "Page-segment содержит неполный набор ячеек.",
                    logical_row=logical_index + 1,
                    week=week,
                    segment_index=segment_index,
                    physical_rows=physical_index,
                    page_segmentation=_layouts_summary(layouts),
                )
            table._tbl.append(deepcopy(logical_row))
            row = table.rows[-1]
            for column, text in enumerate(segment.cells):
                cell = _Cell(row._tr.tc_lst[column], row)
                _remove_vmerge(cell)
                _set_cell_text(cell, text)
            _prepare_month_cell(
                _Cell(row._tr.tc_lst[columns.month], row),
                segment.cells[columns.month],
            )
            _prepare_week_cell(
                _Cell(row._tr.tc_lst[columns.week], row),
                segment.cells[columns.week],
            )
            _prevent_row_split(row)
            months.append(segment.cells[0])
            if split:
                continuation_rows.add(physical_index)
            physical_index += 1
    if protect_vertical_height:
        _protect_vertical_cell_height(table)
    return tuple(months), frozenset(continuation_rows)


def _build_segmented_document(
    document,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    layouts,
    **header,
) -> bytes:
    """Build physical rows whose identifiers travel with every page segment."""

    from calendar_pedagoga.docx_qa import detect_data_row_page_spans

    table, columns, _months = _populate_calendar_table(
        document, utp, rows, **header
    )
    months, continuation_rows = _apply_page_row_segments(
        table,
        layouts,
        protect_vertical_height=bool(header.get("uses_organization_template")),
    )
    unmerged = _save_document(document)
    spans = detect_data_row_page_spans(unmerged, total_rows=len(months))
    # Each physical page-segment must fit on one page with confirmed identifiers.
    # An empty lower portion of a page is allowed when the next logical week is
    # kept whole because it fits on a clean page but not in the remaining room.
    if spans is None or any(
        span.start_page != span.end_page
        or (index in continuation_rows and not span.split_safe)
        for index, span in enumerate(spans)
    ):
        _raise_docx_qa(
            "verify_unmerged_spans",
            "DOCX не прошёл QA: page-segment не помещается на одной странице "
            "или его идентификаторы не подтверждены render-проверкой.",
            content=unmerged,
            physical_rows=len(months),
            page_segmentation=_span_failure(spans, continuation_rows, table),
            layouts=_layouts_summary(layouts),
        )

    pages: dict[int, list[int]] = {}
    for index, span in enumerate(spans):
        if index not in continuation_rows:
            pages.setdefault(span.start_page, []).append(index)
    # Preserve the cross-renderer boundary guard: a page-edge row is never
    # placed inside a vertical merge. Split logical rows are excluded entirely.
    rows_by_page = tuple(
        tuple(indices[:-1]) for indices in pages.values() if len(indices) > 1
    )
    _merge_month_cells_by_page_segments(table, columns, months, rows_by_page)
    output = _save_document(document)
    final_spans = detect_data_row_page_spans(
        output,
        total_rows=len(months),
        render_exact=True,
    )
    if final_spans is None or any(
        span.start_page != span.end_page
        or (index in continuation_rows and not span.split_safe)
        for index, span in enumerate(final_spans)
    ):
        _raise_docx_qa(
            "verify_merged_spans",
            "DOCX не прошёл QA: финальная merge-структура нарушила "
            "целостность page-segment или его идентификаторов.",
            content=output,
            physical_rows=len(months),
            page_segmentation=_span_failure(final_spans, continuation_rows, table),
            layouts=_layouts_summary(layouts),
        )
    return output


def _merge_month_cells_for_pages(
    document,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    rows_by_page: tuple[tuple[int, ...], ...],
    *,
    keep_together: frozenset[int] = frozenset(),
    academic_year: str,
    program_title: str | None = None,
    study_year_hints: tuple[str | None, ...] = (),
    group_number: str | None = None,
    class_name: str | None = None,
    teacher_name: str | None = None,
    uses_organization_template: bool = False,
    draft_review_weeks: tuple[int, ...] = (),
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
        teacher_name=teacher_name,
        uses_organization_template=uses_organization_template,
        draft_review_weeks=draft_review_weeks,
    )
    for index in keep_together:
        _prevent_row_split(table.rows[index + 2])
    _merge_month_cells_by_page_segments(table, columns, months, rows_by_page)
    return _save_document(document)


def build_output_filename(utp: UtpParseResult, academic_year: str) -> str:
    program = utp.metadata.program_name or "программа"
    safe = re.sub(r'[<>:"/\\|?*\s]+', "_", program).strip("_") or "календарь"
    year = academic_year.replace("–", "-")
    return f"Календарный_план_{safe}_{year}.docx"


def _measure_preview_layouts(
    template: CalendarTemplateSelection,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    *,
    splittable: frozenset[int] = frozenset(),
    page_break_before: frozenset[int] = frozenset(),
    **header,
):
    """Measure the unmerged preview: only *splittable* rows may break on a page.

    The preview is the semantic source for exact page segmentation.
    Oversize rows may also request pageBreakBefore so Word does not pull them
    into the leftover gap of the previous page (which creates thin last tails).
    """

    from calendar_pedagoga.docx_qa import detect_data_row_page_layout

    document = _load_template(template)
    table, columns, _ = _populate_calendar_table(document, utp, rows, **header)
    for index, row in enumerate(table.rows[2:]):
        if index in splittable:
            _allow_row_split(row)
        else:
            _prevent_row_split(row)
        if index in page_break_before:
            _set_page_break_before_row(row, columns)
    return detect_data_row_page_layout(_save_document(document), total_rows=len(rows))


def _prior_content_page(layouts, index: int) -> int:
    """Page where content before *index* ended (title page for the first row)."""

    return 1 if index == 0 else layouts[index - 1].span.end_page


# Forced-split last segments below these sizes are rebalanced so RESULT /
# CONTROL cannot sit alone as a 1–2 line tail on the next page.
_MIN_FORCED_TAIL_BODY_ALNUM = 200
_MIN_FORCED_TAIL_SOURCE_ALNUM = 60
_MIN_FORCED_TAIL_RC_ALNUM = 80


def _segment_body_alnum(segment) -> int:
    from calendar_pedagoga.docx_qa import _normalize_match_text

    return sum(len(_normalize_match_text(cell)) for cell in segment.cells[2:])


def _segment_source_alnum(segment) -> int:
    """Theory + practice body (columns 2 and 4 in the 8-column calendar)."""

    from calendar_pedagoga.docx_qa import _normalize_match_text

    total = 0
    for column in (2, 4):
        if column < len(segment.cells):
            total += len(_normalize_match_text(segment.cells[column]))
    return total


def _is_thin_forced_tail(segment) -> bool:
    from calendar_pedagoga.docx_qa import _normalize_match_text

    if _segment_body_alnum(segment) < _MIN_FORCED_TAIL_BODY_ALNUM:
        return True
    if _segment_source_alnum(segment) < _MIN_FORCED_TAIL_SOURCE_ALNUM:
        return True
    for column in (6, 7):
        if column >= len(segment.cells):
            continue
        text = segment.cells[column] or ""
        if text and len(_normalize_match_text(text)) < _MIN_FORCED_TAIL_RC_ALNUM:
            return True
    return False


def _oversize_weeks_from_cantsplit_spans(
    template: CalendarTemplateSelection,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    **header,
) -> frozenset[int]:
    """Weeks whose unbroken row height exceeds one full page.

    Detected by rendering the calendar with every data row marked cantSplit and
    checking which logical rows still cross a page boundary (or clip).
    """

    from calendar_pedagoga import docx_qa as qa
    from calendar_pedagoga.docx_qa import detect_data_row_page_spans

    document = _load_template(template)
    table, _, _ = _populate_calendar_table(document, utp, rows, **header)
    for row in table.rows[2:]:
        _prevent_row_split(row)
    content = _save_document(document)
    spans = detect_data_row_page_spans(content, total_rows=len(rows))
    if spans is None:
        clipped = qa._SEGMENTATION_DIAG.get("clipped_rows") or ()
        return frozenset(int(index) for index in clipped)
    return frozenset(
        index
        for index, span in enumerate(spans)
        if span.start_page != span.end_page
    )


def _find_tail_cut(text: str, *, min_move: int) -> int | None:
    """Cut near the end of *text* so the moved suffix has at least *min_move* chars."""

    available = len(text) - 8
    if min_move <= 0 or available < 12:
        return None
    min_move = min(min_move, available)
    earliest = max(8, len(text) - max(min_move * 4, 280))
    latest = len(text) - min_move

    def _boundary(index: int) -> bool:
        if index <= 0 or index >= len(text):
            return False
        if text[index - 1 : index + 1] == "\n\n":
            return True
        if text[index - 1] == "\n":
            return True
        if text[index - 1] in ".!?…" and text[index : index + 1] in (" ", "\n"):
            return True
        if text[index - 1] == " " and text[index : index + 1].isalpha():
            return True
        return False

    for index in range(latest, earliest - 1, -1):
        if _boundary(index):
            if text[index - 1 : index + 1] == "\n\n":
                return index + 1 if index + 1 <= len(text) else index
            if text[index - 1] in ".!?…" and text[index : index + 1] in (" ", "\n"):
                return index + 1
            return index
    for index in range(latest, earliest - 1, -1):
        if text[index - 1] == " ":
            return index
    return latest


def _rebalance_thin_last_segments(layouts):
    """Move content into a forced-split tail that would otherwise be 1–2 lines."""

    from calendar_pedagoga.docx_qa import (
        DataRowPageLayout,
        DataRowPageSegment,
        DataRowPageSpan,
    )

    rebuilt = []
    for layout in layouts:
        segments = list(layout.segments)
        if len(segments) < 2:
            rebuilt.append(layout)
            continue
        guard = 0
        while len(segments) >= 2 and _is_thin_forced_tail(segments[-1]) and guard < 16:
            guard += 1
            prev = segments[-2]
            last = segments[-1]
            need = max(
                _MIN_FORCED_TAIL_BODY_ALNUM - _segment_body_alnum(last),
                _MIN_FORCED_TAIL_SOURCE_ALNUM - _segment_source_alnum(last),
                64,
            )
            new_prev = list(prev.cells)
            new_last = list(last.cells)
            moved = False
            # Prefer theory/practice so the tail is not RESULT/CONTROL-only.
            for column in (4, 2, 6, 7, 5, 3):
                if column >= len(prev.cells):
                    continue
                text = new_prev[column]
                if not text:
                    continue
                col_need = need
                if column in (6, 7):
                    from calendar_pedagoga.docx_qa import _normalize_match_text

                    col_need = max(
                        col_need,
                        _MIN_FORCED_TAIL_RC_ALNUM
                        - len(_normalize_match_text(new_last[column] or "")),
                    )
                chunk = max(16, min(max(col_need, 48), 120, max(16, len(text) - 8)))
                cut = _find_tail_cut(text, min_move=chunk)
                if cut is None:
                    continue
                candidate_prev = text[:cut]
                candidate_last = text[cut:] + new_last[column]
                if candidate_prev + candidate_last != text + new_last[column]:
                    continue
                new_prev[column] = candidate_prev
                new_last[column] = candidate_last
                moved = True
                if not _is_thin_forced_tail(
                    DataRowPageSegment(last.page_number, tuple(new_last))
                ):
                    break
            if not moved:
                break
            segments[-2] = DataRowPageSegment(prev.page_number, tuple(new_prev))
            segments[-1] = DataRowPageSegment(last.page_number, tuple(new_last))
        rebuilt.append(
            DataRowPageLayout(
                DataRowPageSpan(
                    layout.span.start_page,
                    segments[-1].page_number,
                    layout.span.split_safe,
                ),
                tuple(segments),
            )
        )
    return tuple(rebuilt)


def _split_text_balanced(text: str, parts: int) -> list[str]:
    """Split *text* into *parts* chunks, biasing a bit toward later segments."""

    if parts <= 1:
        return [text]
    if not text:
        return [""] * parts
    if len(text) < parts * 12:
        return [text] + [""] * (parts - 1)

    if parts == 2:
        ratios = (0.42, 0.58)
    elif parts == 3:
        ratios = (0.28, 0.36, 0.36)
    else:
        ratios = tuple(1 / parts for _ in range(parts))

    cuts: list[int] = []
    cumulative = 0.0
    for index in range(1, parts):
        cumulative += ratios[index - 1]
        ideal = int(len(text) * cumulative)
        window = max(24, len(text) // (parts * 2))
        lo = max(cuts[-1] + 8 if cuts else 8, ideal - window)
        hi = min(len(text) - 8 * (parts - index), ideal + window)
        chosen = None
        for pos in range(max(lo, ideal), hi + 1):
            if text[pos - 1 : pos + 1] == "\n\n":
                chosen = pos + 1
                break
            if text[pos - 1] == "\n":
                chosen = pos
                break
            if text[pos - 1] in ".!?…" and text[pos : pos + 1] in (" ", "\n"):
                chosen = pos + 1
                break
        if chosen is None:
            for pos in range(min(ideal, hi), lo - 1, -1):
                if text[pos - 1] in ".!?…" and text[pos : pos + 1] in (" ", "\n"):
                    chosen = pos + 1
                    break
                if text[pos - 1] == "\n":
                    chosen = pos
                    break
                if text[pos - 1] == " ":
                    chosen = pos
                    break
        cuts.append(chosen if chosen is not None else ideal)

    pieces: list[str] = []
    start = 0
    for cut in cuts:
        pieces.append(text[start:cut])
        start = cut
    pieces.append(text[start:])
    while len(pieces) < parts:
        pieces.append("")
    return pieces[:parts]


def _repartition_split_layouts(layouts):
    """Rebalance multi-page week text so the last segment is not a thin stub."""

    from calendar_pedagoga.docx_qa import (
        DataRowPageLayout,
        DataRowPageSegment,
        DataRowPageSpan,
    )

    rebuilt = []
    for layout in layouts:
        segments = list(layout.segments)
        if len(segments) < 2 or not _is_thin_forced_tail(segments[-1]):
            rebuilt.append(layout)
            continue
        parts = len(segments)
        columns = len(segments[0].cells)
        full = [
            "".join(segment.cells[column] or "" for segment in segments)
            for column in range(columns)
        ]
        split_columns = [
            _split_text_balanced(text, parts) if column >= 2 else [text] + [""] * (parts - 1)
            for column, text in enumerate(full)
        ]
        # Month/week identifiers travel with every segment.
        for column in (0, 1):
            split_columns[column] = [full[column]] * parts
        new_segments = []
        for part in range(parts):
            cells = tuple(split_columns[column][part] for column in range(columns))
            new_segments.append(
                DataRowPageSegment(segments[part].page_number, cells)
            )
        # Keep original Word cuts if repartition emptied a middle/body column oddly.
        if any(
            "".join(seg.cells[column] for seg in new_segments) != full[column]
            for column in range(columns)
        ):
            rebuilt.append(layout)
            continue
        rebuilt.append(
            DataRowPageLayout(
                DataRowPageSpan(
                    layout.span.start_page,
                    new_segments[-1].page_number,
                    layout.span.split_safe,
                ),
                tuple(new_segments),
            )
        )
    return tuple(rebuilt)


def _merge_segment_cells(left, right) -> tuple[str, ...]:
    return tuple(
        (left.cells[index] or "") + (right.cells[index] or "")
        for index in range(len(left.cells))
    )


def _coalesce_thin_last_segments(layouts):
    """If a forced-split last segment is thin, fold it into the previous segment.

    Used only when the merged segment still fits one page (caller verifies).
    """

    from calendar_pedagoga.docx_qa import (
        DataRowPageLayout,
        DataRowPageSegment,
        DataRowPageSpan,
    )

    rebuilt = []
    for layout in layouts:
        segments = list(layout.segments)
        while len(segments) >= 2 and _is_thin_forced_tail(segments[-1]):
            prev = segments[-2]
            last = segments[-1]
            merged = DataRowPageSegment(
                prev.page_number,
                _merge_segment_cells(prev, last),
            )
            segments = segments[:-2] + [merged]
        rebuilt.append(
            DataRowPageLayout(
                DataRowPageSpan(
                    layout.span.start_page,
                    segments[-1].page_number,
                    layout.span.split_safe,
                ),
                tuple(segments),
            )
        )
    return tuple(rebuilt)


def _layouts_segments_fit_pages(
    template: CalendarTemplateSelection,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    layouts,
    **header,
) -> bool:
    """True when every physical page-segment fits on a single rendered page."""

    from calendar_pedagoga.docx_qa import detect_data_row_page_spans

    document = _load_template(template)
    table, _, _ = _populate_calendar_table(document, utp, rows, **header)
    months, continuation_rows = _apply_page_row_segments(
        table,
        layouts,
        protect_vertical_height=bool(header.get("uses_organization_template")),
    )
    spans = detect_data_row_page_spans(
        _save_document(document), total_rows=len(months)
    )
    if spans is None:
        return False
    return not any(
        span.start_page != span.end_page
        or (index in continuation_rows and not span.split_safe)
        for index, span in enumerate(spans)
    )


def _measure_whole_week_layouts(
    template: CalendarTemplateSelection,
    utp: UtpParseResult,
    rows: tuple[ResolvedLessonRow, ...],
    **header,
):
    """Pack weeks whole: split only oversize weeks, never to fill a page gap.

    Empty space at the bottom of a page is intentional when the next week fits
    on a clean page but not in the remaining room. Oversize weeks start on a
    clean page (pageBreakBefore) so Word does not pull them into a leftover gap.
    """

    from calendar_pedagoga import docx_qa as qa

    whole = _measure_preview_layouts(template, utp, rows, **header)
    oversize = set(
        _oversize_weeks_from_cantsplit_spans(template, utp, rows, **header)
    )
    if whole is None:
        clipped = qa._SEGMENTATION_DIAG.get("clipped_rows") or ()
        oversize.update(int(index) for index in clipped)

    if not oversize:
        return whole, frozenset()

    # Start oversize weeks on a clean page; allow split only after that.
    page_break_before = frozenset(index for index in oversize if index > 0)
    layouts = _measure_preview_layouts(
        template,
        utp,
        rows,
        splittable=frozenset(oversize),
        page_break_before=page_break_before,
        **header,
    )
    if layouts is None:
        return whole, frozenset(oversize)

    for candidate in (
        _rebalance_thin_last_segments(layouts),
        _repartition_split_layouts(layouts),
        _repartition_split_layouts(_rebalance_thin_last_segments(layouts)),
    ):
        if candidate != layouts and _layouts_segments_fit_pages(
            template, utp, rows, candidate, **header
        ):
            layouts = candidate
            if not any(
                len(layout.segments) > 1 and _is_thin_forced_tail(layout.segments[-1])
                for layout in layouts
            ):
                break

    # Fold a thin last into the previous segment only when the week stays split
    # for oversize rows (a single segment cannot hold an oversize week).
    coalesced = _coalesce_thin_last_segments(layouts)
    if coalesced != layouts:
        preserves_oversize_split = all(
            index not in oversize
            or len(before.segments) <= 1
            or len(after.segments) >= 2
            for index, (before, after) in enumerate(zip(layouts, coalesced))
        )
        if preserves_oversize_split and _layouts_segments_fit_pages(
            template, utp, rows, coalesced, **header
        ):
            layouts = coalesced
    return layouts, frozenset(oversize)


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
    teacher_name: str | None = None,
    draft_review_weeks: tuple[int, ...] = (),
) -> bytes:
    """Сформировать DOCX: месяц совпадает с датой и merge не пересекает страницы."""

    header = {
        "academic_year": academic_year,
        "program_title": program_title,
        "study_year_hints": study_year_hints,
        "group_number": group_number,
        "class_name": class_name,
        "teacher_name": teacher_name,
        "uses_organization_template": template.uses_organization_template,
        "draft_review_weeks": draft_review_weeks,
    }

    # Whole weeks by default. A week that does not fit in the remaining space
    # moves to the next page intact. Only weeks taller than one full page are
    # marked splittable; gap-fill splits are not used.
    layouts, _oversize = _measure_whole_week_layouts(template, utp, rows, **header)
    if layouts is None:
        _raise_docx_qa(
            "measure_preview_layouts",
            "DOCX не прошёл QA: не удалось надёжно определить границы текста "
            "для page-segmentation.",
            logical_rows=len(rows),
            page_segmentation="unavailable",
        )
    return _build_segmented_document(
        _load_template(template), utp, rows, layouts, **header
    )
