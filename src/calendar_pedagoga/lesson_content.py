"""Строгое формирование полей занятия без ИИ и новых формулировок."""

from __future__ import annotations

from dataclasses import dataclass
import re

from calendar_pedagoga.content_generation import CalendarContentRow


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


def build_lesson_content(
    rows: tuple[CalendarContentRow, ...],
) -> tuple[LessonContentRow, ...]:
    result: list[LessonContentRow] = []
    topic_parts: dict[tuple[str | None, str, str], tuple[int, int]] = {}
    for row in rows:
        key = (row.topic_number, row.topic_title, row.section)
        theory, practice = topic_parts.get(key, (0, 0))
        topic_parts[key] = (theory + row.theory_hours, practice + row.practice_hours)
    for row in rows:
        warnings = list(row.warnings)
        theory_text = ""
        practice_text = ""
        content = row.program_content_full
        topic_theory, topic_practice = topic_parts[
            (row.topic_number, row.topic_title, row.section)
        ]
        if content:
            explicit = _split_explicit_practice(content)
            if explicit:
                theory_source, practice_source = explicit
                if row.theory_hours:
                    theory_text = theory_source
                if row.practice_hours:
                    practice_text = practice_source
            elif topic_theory and not topic_practice and row.theory_hours:
                theory_text = content
            elif topic_practice and not topic_theory and row.practice_hours:
                practice_text = content
            elif row.theory_hours or row.practice_hours:
                warnings.append(
                    "В программе нет явной границы теории и практики; текст не разделён."
                )
        elif row.theory_hours or row.practice_hours:
            warnings.append("Нет программного содержания для заполнения занятия.")

        if row.theory_hours and not theory_text:
            warnings.append("Теоретическое занятие не заполнено: недостаточно данных источника.")
        if row.practice_hours and not practice_text:
            warnings.append("Практическое занятие не заполнено: недостаточно данных источника.")
        warnings.append("Тип занятия не указан для этой темы в источниках.")
        warnings.append("Планируемый результат не указан для этой темы в источниках.")
        warnings.append("Вид контроля не указан для этой темы в источниках.")
        result.append(
            LessonContentRow(
                source=row,
                theory_text=theory_text,
                practice_text=practice_text,
                lesson_type="",
                planned_result="",
                assessment_method="",
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
