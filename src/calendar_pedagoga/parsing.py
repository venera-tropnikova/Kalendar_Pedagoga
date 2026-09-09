"""Локальный разбор учебно-тематических планов в формате DOCX."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from io import BytesIO
from itertools import zip_longest
from pathlib import Path
import re
from typing import BinaryIO

from docx import Document


@dataclass(frozen=True)
class Hours:
    total: int
    theory: int
    practice: int


@dataclass(frozen=True)
class Topic:
    number: str | None
    title: str
    hours: Hours
    parent_section: str | None = None
    is_standalone_section: bool = False


@dataclass(frozen=True)
class Section:
    number: str | None
    title: str
    hours: Hours
    is_standalone_position: bool = False


@dataclass(frozen=True)
class UtpMetadata:
    program_name: str | None = None
    academic_year: str | None = None
    academic_year_mentions: tuple = ()
    study_year: str | None = None
    student_age: str | None = None
    hours_per_week: int | None = None
    hours_per_year: int | None = None
    study_weeks: int | None = None
    teacher_name: str | None = None
    stated_schedule_hours: int | None = None
    workload_provenance: str | None = None


@dataclass(frozen=True)
class UtpParseResult:
    metadata: UtpMetadata
    sections: tuple[Section, ...]
    topics: tuple[Topic, ...]
    table_totals: Hours | None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class CompactTableParseError(ValueError):
    """Ошибка позиционного соответствия строк компактного УТП."""


class UtpYearSelectionError(ValueError):
    """Нельзя однозначно выбрать годовую таблицу УТП."""


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _match(text: str, pattern: str) -> str | None:
    found = re.search(pattern, text, re.IGNORECASE)
    return _clean(found.group(1)) if found else None


def _integer(value: str | None) -> int:
    cleaned = _clean(value or "")
    if not cleaned or cleaned in {"-", "–", "—"}:
        return 0
    found = re.search(r"\d+", cleaned)
    return int(found.group()) if found else 0


def _number_and_title(value: str) -> tuple[str | None, str]:
    value = _clean(value)
    found = re.match(r"^(\d+(?:\.\d+)*)\.?\s*(.*)$", value)
    if not found:
        return None, value
    return found.group(1), _clean(found.group(2))


def _metadata(paragraphs: list[str]) -> UtpMetadata:
    text = "\n".join(_clean(p) for p in paragraphs if _clean(p))
    from calendar_pedagoga.academic_year import (
        extract_academic_year_mentions,
        unique_academic_years,
    )

    mentions = extract_academic_year_mentions(text)
    years = unique_academic_years(mentions)
    academic_year = years[0] if len(years) == 1 else None
    weekly = _match(text, r"Количество часов в неделю:\s*(\d+)")
    yearly = _match(text, r"Общее количество часов в год:\s*(\d+)")
    weeks = _match(text, r"(\d+)\s+учебн\w*\s+недел")
    schedule_hours = _match(text, r"\d+\s+учебн\w*\s+недел\w*\s+на\s+(\d+)\s+час")
    return UtpMetadata(
        program_name=_match(text, r"программ(?:ой|е)\s+[«\"]([^»\"]+)[»\"]"),
        academic_year=academic_year,
        academic_year_mentions=mentions,
        study_year=_match(text, r"Год обучения:\s*([^\n]+)"),
        student_age=_match(text, r"Возраст обучающихся:\s*([^\n]+)"),
        hours_per_week=int(weekly) if weekly else None,
        hours_per_year=int(yearly) if yearly else None,
        study_weeks=int(weeks) if weeks else None,
        teacher_name=_match(text, r"Педагог дополнительного образования:\s*([^\n]+)"),
        stated_schedule_hours=int(schedule_hours) if schedule_hours else None,
    )


_CALENDAR_TABLE_MARKERS = (
    "месяц",
    "неделя",
    "теоретические занятия",
    "практические занятия",
    "планируемый результат",
    "вид контроля",
)


def _header_blob(table: object, rows: int = 3) -> str:
    parts: list[str] = []
    for row in table.rows[: min(rows, len(table.rows))]:
        parts.extend(cell.text for cell in row.cells)
    return " ".join(parts).casefold()


def _is_calendar_table(table: object) -> bool:
    blob = _header_blob(table)
    return sum(marker in blob for marker in _CALENDAR_TABLE_MARKERS) >= 3


def _utp_table_score(table: object) -> int:
    if _is_calendar_table(table):
        return -1
    blob = _header_blob(table)
    score = 0
    if "час" in blob:
        score += 3
    if "тем" in blob or "раздел" in blob:
        score += 2
    if re.search(r"теор|лекц", blob):
        score += 2
    if "практик" in blob:
        score += 2
    if "всего" in blob:
        score += 1
    joined_rows = [" ".join(cell.text for cell in row.cells) for row in table.rows]
    if any(re.search(r"\bитого\b", text, re.IGNORECASE) for text in joined_rows):
        score += 5
    columns = len(table.columns)
    if columns in {4, 5, 6}:
        score += 2
    if columns >= 8:
        score -= 4
    return score


def _parse_numbered_hours_rows(
    rows: list[list[str]],
    *,
    label_idx: int,
    title_idx: int,
    hour_idxs: tuple[int, int, int],
) -> tuple[list[Section], list[Topic], Hours | None]:
    sections: list[Section] = []
    topics: list[Topic] = []
    totals: Hours | None = None
    current_section: str | None = None
    started = False
    for raw_cells in rows:
        cells = [_clean(cell) for cell in raw_cells]
        if not any(cells):
            continue
        label = cells[label_idx] if label_idx < len(cells) else ""
        title = cells[title_idx] if title_idx < len(cells) else ""
        blob = " ".join(cells)
        if re.search(r"\bитого\b", blob, re.IGNORECASE):
            totals = Hours(*(_integer(cells[i]) if i < len(cells) else 0 for i in hour_idxs))
            continue
        number, label_title = _number_and_title(label)
        if number is None:
            if started:
                continue
            continue
        started = True
        hours = Hours(*(_integer(cells[i]) if i < len(cells) else 0 for i in hour_idxs))
        if "." not in number:
            section_title = label_title or _number_and_title(title)[1]
            current_section = section_title
            sections.append(Section(number, section_title, hours))
        else:
            topic_title = title
            if topic_title == label or topic_title == f"{number}.":
                fallback = cells[1] if len(cells) > 1 else title
                topic_title = fallback
            topics.append(
                Topic(
                    number,
                    _number_and_title(topic_title)[1],
                    hours,
                    parent_section=current_section,
                )
            )
    return sections, topics, totals


def _parse_six_column_table(
    rows: list[list[str]],
) -> tuple[list[Section], list[Topic], Hours | None]:
    sections: list[Section] = []
    topics: list[Topic] = []
    totals: Hours | None = None
    current_section: str | None = None
    for cells in rows[2:]:
        cells = [_clean(cell) for cell in cells]
        if not any(cells):
            continue
        label, title = cells[0], cells[2] or cells[1]
        if re.match(r"^итого\b", title, re.IGNORECASE):
            totals = Hours(*(_integer(cells[i]) for i in (3, 4, 5)))
            continue
        number, label_title = _number_and_title(label)
        if number is None:
            continue
        hours = Hours(*(_integer(cells[i]) for i in (3, 4, 5)))
        if "." not in number:
            section_title = label_title or _number_and_title(title)[1]
            current_section = section_title
            sections.append(Section(number, section_title, hours))
        else:
            topic_title = title
            if topic_title == label or topic_title == f"{number}.":
                topic_title = cells[1]
            topics.append(
                Topic(
                    number,
                    _number_and_title(topic_title)[1],
                    hours,
                    parent_section=current_section,
                )
            )
    return sections, topics, totals


def _parse_compact_table(
    table: object,
) -> tuple[list[Section], list[Topic], Hours | None]:
    sections: list[Section] = []
    topics: list[Topic] = []
    totals: Hours | None = None
    section_order = 0
    for row in table.rows[2:]:
        cells = [cell.text for cell in row.cells]
        raw_title = cells[0].strip()
        if "всего часов" in _clean(raw_title).lower():
            totals = Hours(*(_integer(cells[i]) for i in (1, 2, 3)))
            continue
        title_paragraphs = [
            paragraph for paragraph in row.cells[0].paragraphs if _clean(paragraph.text)
        ]
        titles = [_clean(paragraph.text) for paragraph in title_paragraphs]
        columns = [
            [_clean(paragraph.text) for paragraph in row.cells[index].paragraphs]
            for index in (1, 2, 3)
        ]
        hour_rows = [
            values
            for values in zip_longest(*columns, fillvalue="")
            if any(values)
        ]
        if not titles:
            continue
        section_order += 1
        section_number, section_title = _number_and_title(titles[0])
        if section_number is None:
            num_pr = title_paragraphs[0]._p.pPr.numPr
            if num_pr is not None and int(num_pr.ilvl.val) == 0:
                section_number = str(section_order)
        section_label = " ".join(
            part for part in (section_number, section_title) if part
        )
        if len(hour_rows) != len(titles):
            raise CompactTableParseError(
                "Неоднозначное соответствие строк компактного УТП для позиции "
                f"«{section_label}»: названий={len(titles)}, "
                f"строк часов={len(hour_rows)}."
            )

        def parse_position_hours(
            number: str | None,
            title: str,
            raw_values: tuple[str, str, str],
        ) -> Hours:
            hours = Hours(*(_integer(value) for value in raw_values))
            if hours.total != hours.theory + hours.practice:
                position_label = " ".join(part for part in (number, title) if part)
                raise CompactTableParseError(
                    f"Некорректные часы позиции «{position_label}»: "
                    f"total={hours.total}, theory={hours.theory}, "
                    f"practice={hours.practice}."
                )
            return hours

        section_hours = parse_position_hours(
            section_number,
            section_title,
            hour_rows[0],
        )
        sections.append(
            Section(
                section_number,
                section_title,
                section_hours,
                is_standalone_position=len(titles) == 1,
            )
        )
        if len(titles) == 1:
            continue
        for index, raw_topic in enumerate(titles[1:], start=1):
            number, title = _number_and_title(raw_topic)
            topics.append(
                Topic(
                    number,
                    title,
                    parse_position_hours(number, title, hour_rows[index]),
                    parent_section=section_title,
                )
            )
    return sections, topics, totals


def _looks_like_valid_utp(
    sections: list[Section],
    topics: list[Topic],
    table_totals: Hours | None,
) -> bool:
    if not sections or not topics:
        return False
    titled = [
        topic
        for topic in topics
        if topic.title and topic.title.casefold() not in {"тема", "№ п/п", "№ пп"}
    ]
    if len(titled) < 3:
        return False
    if table_totals is not None and table_totals.total > 0:
        return True
    return any(topic.hours.total > 0 for topic in titled)


def _parse_table_structure(
    table: object,
) -> tuple[list[Section], list[Topic], Hours | None]:
    rows = [[cell.text for cell in row.cells] for row in table.rows]
    if not rows:
        raise ValueError("Пустая таблица УТП.")
    width = len(rows[0])
    if width >= 6:
        return _parse_six_column_table(rows)
    if width == 5:
        return _parse_numbered_hours_rows(
            rows,
            label_idx=0,
            title_idx=1,
            hour_idxs=(2, 3, 4),
        )
    if width >= 4:
        return _parse_compact_table(table)
    raise ValueError("Не удалось распознать структуру таблицы УТП.")


_UTP_PLAN_HEADING = re.compile(
    r"(?i)учебно[- ]тематическ\w*\s+план|\bутп\b"
)
_STUDY_YEAR_IN_HEADING = re.compile(
    r"(\d+)\s*[-–—]?\s*(?:го|ый|ой|ий|я)?\s*года?\s+обучен"
)


def _is_utp_plan_heading(text: str) -> bool:
    return bool(_UTP_PLAN_HEADING.search(_clean(text)))


def heading_study_year(text: str) -> int | None:
    """Год обучения из заголовка «УТП N-го года», не из учебного года 2026–2027."""

    cleaned = _clean(text)
    if not cleaned or not _is_utp_plan_heading(cleaned):
        return None
    low = cleaned.casefold()
    for token, number in (
        ("перв", 1),
        ("втор", 2),
        ("трет", 3),
        ("четв", 4),
    ):
        if token in low and "год" in low:
            return number
    found = _STUDY_YEAR_IN_HEADING.search(low)
    if not found:
        return None
    year = int(found.group(1))
    if 1 <= year <= 8:
        return year
    return None


def _iter_document_blocks(document):
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def walk(element):
        for child in element.iterchildren():
            if child.tag == qn("w:p"):
                yield "p", Paragraph(child, document)
            elif child.tag == qn("w:tbl"):
                yield "t", Table(child, document)
            elif child.tag in {qn("w:sdt"), qn("w:sdtContent")}:
                yield from walk(child)

    yield from walk(document.element.body)


@dataclass(frozen=True)
class UtpTableCandidate:
    study_year: int | None
    sections: tuple[Section, ...]
    topics: tuple[Topic, ...]
    table_totals: Hours | None


def collect_utp_table_candidates(source) -> tuple[UtpTableCandidate, ...]:
    """Все валидные таблицы УТП в порядке документа, с годом из ближайшего заголовка."""

    document = (
        source
        if hasattr(source, "element") and hasattr(source, "tables")
        else _open_utp_document(source)
    )
    last_year: int | None = None
    found: list[UtpTableCandidate] = []
    for kind, block in _iter_document_blocks(document):
        if kind == "p":
            year = heading_study_year(block.text)
            if year is not None:
                last_year = year
            continue
        if _utp_table_score(block) < 0:
            continue
        try:
            sections, topics, table_totals = _parse_table_structure(block)
        except (ValueError, IndexError):
            continue
        if not _looks_like_valid_utp(sections, topics, table_totals):
            continue
        found.append(
            UtpTableCandidate(
                last_year,
                tuple(sections),
                tuple(topics),
                table_totals,
            )
        )
    return tuple(found)


def _select_utp_candidate(
    candidates: tuple[UtpTableCandidate, ...],
    study_year: int | None,
) -> UtpTableCandidate:
    """Выбрать таблицу только по study_year, не по похожести часов или тем."""

    if study_year is not None:
        matched = [item for item in candidates if item.study_year == study_year]
        if not matched:
            raise UtpYearSelectionError(
                f"В программе нет учебно-тематического плана {study_year}-го "
                "года обучения."
            )
        return matched[0]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise UtpYearSelectionError(
            "В программе несколько учебно-тематических планов по годам "
            "обучения, год не выбран."
        )
    raise ValueError("В документе не найдена таблица УТП с темами и часами.")


def _store_selected_study_year(
    result: UtpParseResult,
    year: int | None,
) -> UtpParseResult:
    if year is None:
        return result
    from calendar_pedagoga.program_parsing import infer_study_year_number

    current = infer_study_year_number(result.metadata.study_year)
    if current == year:
        return result
    return UtpParseResult(
        metadata=replace(result.metadata, study_year=f"{year} год обучения"),
        sections=result.sections,
        topics=result.topics,
        table_totals=result.table_totals,
        warnings=result.warnings,
    )


def _finalize_utp_parse(
    document,
    sections: list[Section] | tuple[Section, ...],
    topics: list[Topic] | tuple[Topic, ...],
    table_totals: Hours | None,
    *,
    study_year: int | None = None,
) -> UtpParseResult:
    section_list = list(sections)
    topic_list = list(topics)
    for section in section_list:
        if not any(topic.parent_section == section.title for topic in topic_list):
            topic_list.append(
                Topic(
                    section.number,
                    section.title,
                    section.hours,
                    parent_section=section.title,
                    is_standalone_section=True,
                )
            )
    result = UtpParseResult(
        metadata=_metadata([paragraph.text for paragraph in document.paragraphs]),
        sections=tuple(section_list),
        topics=tuple(topic_list),
        table_totals=table_totals,
    )
    from calendar_pedagoga.validation import validate_utp

    finalized = UtpParseResult(
        metadata=result.metadata,
        sections=result.sections,
        topics=result.topics,
        table_totals=result.table_totals,
        warnings=tuple(validate_utp(result)),
    )
    return _store_selected_study_year(finalized, study_year)


_DOC_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def _open_utp_document(source: str | Path | bytes | BinaryIO):
    if isinstance(source, bytes):
        data = source
        if data.startswith(_DOC_MAGIC):
            from calendar_pedagoga.program_parsing import convert_legacy_doc

            data = convert_legacy_doc(data)
        return Document(BytesIO(data))
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.suffix.lower() == ".doc":
            from calendar_pedagoga.program_parsing import convert_legacy_doc

            return Document(BytesIO(convert_legacy_doc(path.read_bytes())))
        return Document(path)
    return Document(source)


def parse_utp(
    source: str | Path | bytes | BinaryIO,
    study_year: int | None = None,
) -> UtpParseResult:
    """Разобрать УТП DOCX. При нескольких годовых таблицах выбрать по study_year."""

    document = _open_utp_document(source)
    if not document.tables:
        raise ValueError("В УТП не найдена таблица с темами.")
    candidates = collect_utp_table_candidates(document)
    if candidates:
        selected = _select_utp_candidate(candidates, study_year)
        chosen_year = study_year if study_year is not None else selected.study_year
        return _finalize_utp_parse(
            document,
            selected.sections,
            selected.topics,
            selected.table_totals,
            study_year=chosen_year,
        )
    last_error: Exception | None = None
    compact_error: CompactTableParseError | None = None
    for table in document.tables:
        if _utp_table_score(table) < 0:
            continue
        try:
            _parse_table_structure(table)
        except CompactTableParseError as error:
            compact_error = error
            last_error = error
        except (ValueError, IndexError) as error:
            last_error = error
    if compact_error is not None:
        raise compact_error
    if last_error is not None:
        raise ValueError("Не удалось распознать структуру таблицы УТП.") from last_error
    raise ValueError("В документе не найдена таблица УТП с темами и часами.")
