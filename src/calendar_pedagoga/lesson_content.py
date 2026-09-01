"""Строгое формирование полей занятия без ИИ и новых формулировок."""

from __future__ import annotations

from dataclasses import dataclass
import re

from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart


@dataclass(frozen=True)
class LessonContentRow:
    source: CalendarContentRow
    theory_text: str
    practice_text: str
    lesson_type: str
    planned_result: str
    assessment_method: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class FillMetrics:
    theory_percent: float
    practice_percent: float
    lesson_type_percent: float
    planned_result_percent: float
    assessment_method_percent: float
    overall_percent: float


def _split_explicit_practice(text: str) -> tuple[str, str] | None:
    match = re.search(r"(?:^|\n)Практика\.\s*", text, re.IGNORECASE)
    if not match:
        return None
    theory = text[: match.start()].strip()
    practice = text[match.end() :].strip()
    return theory, practice


_EXCURSION_HEAD_RE = re.compile(
    r"^\s*экскурси(?:я|и|ю|ей|онн)",
    re.IGNORECASE,
)
_GAME_HEAD_RE = re.compile(
    r"^\s*(?:дидактическ\w*\s+)?игр(?:а|ы)\b",
    re.IGNORECASE,
)
_PRACTICUM_HEAD_RE = re.compile(r"^\s*практикум\b", re.IGNORECASE)
_DISCUSSION_RE = re.compile(r"обсужден", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(r"\bзадани", re.IGNORECASE)

_CONTROL_BY_TYPE = {
    "теоретическое занятие": "устный опрос",
    "практическое занятие": "практическая работа",
    "комбинированное занятие": "выполнение задания",
    "экскурсия": "наблюдение",
    "игра": "наблюдение",
    "практикум": "практическая работа",
}


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _looks_like(pattern: re.Pattern[str], *texts: str) -> bool:
    return any(pattern.search(text.strip()) for text in texts if text.strip())


def derive_lesson_type(
    *,
    theory_hours: int,
    practice_hours: int,
    topic_title: str,
    theory_text: str,
    practice_text: str,
) -> str:
    """Тип занятия только из часов и явных признаков источника."""
    if theory_hours and practice_hours:
        return "комбинированное занятие"

    primary = practice_text if practice_hours and not theory_hours else theory_text
    markers = (primary, topic_title)
    if _looks_like(_EXCURSION_HEAD_RE, *markers):
        return "экскурсия"
    if _looks_like(_GAME_HEAD_RE, *markers):
        return "игра"
    if _looks_like(_PRACTICUM_HEAD_RE, *markers):
        return "практикум"
    if theory_hours and not practice_hours:
        return "теоретическое занятие"
    if practice_hours and not theory_hours:
        return "практическое занятие"
    return "комбинированное занятие"


def derive_planned_result(
    topic_title: str,
    theory_text: str,
    practice_text: str,
    lesson_type: str = "",
) -> str:
    """Ожидаемый результат учащегося только из темы; без дублирования полного текста."""
    title = _normalize_spaces(topic_title).rstrip(" .")
    quoted = f"«{title}»" if title else "темы занятия"
    if lesson_type == "экскурсия":
        return f"Учащийся сможет участвовать в экскурсии по теме {quoted}."
    if lesson_type == "игра":
        return f"Учащийся сможет выполнить игровые задания по теме {quoted}."
    if lesson_type == "практикум":
        return f"Учащийся сможет выполнить практикум по теме {quoted}."
    if lesson_type == "практическое занятие":
        return f"Учащийся сможет выполнить практическую работу по теме {quoted}."
    if lesson_type == "комбинированное занятие":
        return (
            f"Учащийся сможет изучить тему {quoted} "
            "на теоретическом и практическом занятии."
        )
    return f"Учащийся сможет изучить тему {quoted}."


def derive_assessment_method(
    lesson_type: str,
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
) -> str:
    blob = " ".join(part for part in (theory_text, practice_text, topic_title) if part)
    if _DISCUSSION_RE.search(blob):
        return "обсуждение"
    if _ASSIGNMENT_RE.search(blob):
        return "выполнение задания"
    return _CONTROL_BY_TYPE.get(lesson_type, "устный опрос")


def build_lesson_content(
    rows: tuple[CalendarContentRow, ...],
) -> tuple[LessonContentRow, ...]:
    result: list[LessonContentRow] = []
    topic_totals: dict[tuple[str | None, str, str], tuple[int, int]] = {}
    for row in rows:
        for part in row.week_parts or ():
            key = (part.topic_number, part.topic_title, part.section)
            theory, practice = topic_totals.get(key, (0, 0))
            topic_totals[key] = (theory + part.theory_hours, practice + part.practice_hours)

    for row in rows:
        warnings = list(row.warnings)
        theory_parts: list[str] = []
        practice_parts: list[str] = []
        parts = row.week_parts or (
            WeekTopicPart(
                topic_number=row.topic_number,
                topic_title=row.topic_title,
                section=row.section,
                theory_hours=row.theory_hours,
                practice_hours=row.practice_hours,
                match_status=row.match_status,
                program_section=row.program_section,
                program_topic=row.program_topic,
                program_content_full=row.program_content_full,
                warnings=row.warnings,
            ),
        )
        for part in parts:
            topic_theory, topic_practice = topic_totals[
                (part.topic_number, part.topic_title, part.section)
            ]
            part_warnings: list[str] = list(part.warnings)
            theory_text = ""
            practice_text = ""
            content = part.program_content_full
            if content:
                explicit = _split_explicit_practice(content)
                if explicit:
                    theory_source, practice_source = explicit
                    if part.theory_hours:
                        theory_text = theory_source
                    if part.practice_hours:
                        practice_text = practice_source
                elif topic_theory and not topic_practice and part.theory_hours:
                    theory_text = content
                elif topic_practice and not topic_theory and part.practice_hours:
                    practice_text = content
                elif part.theory_hours or part.practice_hours:
                    part_warnings.append(
                        "В программе нет явной границы теории и практики; текст не разделён."
                    )
            elif part.theory_hours or part.practice_hours:
                part_warnings.append("Нет программного содержания для заполнения занятия.")
            if part.theory_hours and not theory_text:
                part_warnings.append(
                    "Теоретическое занятие не заполнено: недостаточно данных источника."
                )
            if part.practice_hours and not practice_text:
                part_warnings.append(
                    "Практическое занятие не заполнено: недостаточно данных источника."
                )
            warnings.extend(part_warnings)
            if theory_text:
                theory_parts.append(theory_text)
            if practice_text:
                practice_parts.append(practice_text)
        theory_text = "\n".join(theory_parts)
        practice_text = "\n".join(practice_parts)

        lesson_type = derive_lesson_type(
            theory_hours=row.theory_hours,
            practice_hours=row.practice_hours,
            topic_title=row.topic_title,
            theory_text=theory_text,
            practice_text=practice_text,
        )
        planned_result = derive_planned_result(
            row.topic_title,
            theory_text,
            practice_text,
            lesson_type,
        )
        assessment_method = derive_assessment_method(
            lesson_type,
            topic_title=row.topic_title,
            theory_text=theory_text,
            practice_text=practice_text,
        )
        result.append(
            LessonContentRow(
                source=row,
                theory_text=theory_text,
                practice_text=practice_text,
                lesson_type=lesson_type,
                planned_result=planned_result,
                assessment_method=assessment_method,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        )
    return tuple(result)


def calculate_fill_metrics(rows: tuple[LessonContentRow, ...]) -> FillMetrics:
    def percent(filled: int, applicable: int) -> float:
        return 100.0 if applicable == 0 else filled * 100.0 / applicable

    theory_applicable = [row for row in rows if row.source.theory_hours]
    practice_applicable = [row for row in rows if row.source.practice_hours]
    theory_filled = sum(bool(row.theory_text) for row in theory_applicable)
    practice_filled = sum(bool(row.practice_text) for row in practice_applicable)
    total_slots = len(theory_applicable) + len(practice_applicable) + 3 * len(rows)
    total_filled = theory_filled + practice_filled + sum(
        bool(value)
        for row in rows
        for value in (row.lesson_type, row.planned_result, row.assessment_method)
    )
    return FillMetrics(
        theory_percent=percent(theory_filled, len(theory_applicable)),
        practice_percent=percent(practice_filled, len(practice_applicable)),
        lesson_type_percent=percent(sum(bool(r.lesson_type) for r in rows), len(rows)),
        planned_result_percent=percent(sum(bool(r.planned_result) for r in rows), len(rows)),
        assessment_method_percent=percent(sum(bool(r.assessment_method) for r in rows), len(rows)),
        overall_percent=percent(total_filled, total_slots),
    )
