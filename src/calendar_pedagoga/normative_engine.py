"""Нормативные и методические проверки ДОП. Не изменяет расписание, часы, CE2 и DOCX."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
import re

from calendar_pedagoga.normative_registry import (
    NormativeDocument,
    NormativeRegistry,
    get_builtin_normative_registry,
)
from calendar_pedagoga.parsing import UtpParseResult
from calendar_pedagoga.program_parsing import ProgramData, infer_study_year_number
from calendar_pedagoga.scheduling import ScheduleResult, build_academic_weeks


class NormativeVerdict(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    NOT_CHECKED = "not_checked"


class NormativeLayer(StrEnum):
    FEDERAL = "federal"
    LOCAL = "local"
    METHODICAL = "methodical"


@dataclass(frozen=True)
class NormativeCheck:
    check_id: str
    verdict: NormativeVerdict
    teacher_text: str
    layer: NormativeLayer


@dataclass(frozen=True)
class NormativeLessonView:
    """Только чтение полей строки CE2. Движок ничего не пересчитывает."""

    theory_hours: int
    practice_hours: int
    lesson_type: str
    assessment_method: str = ""
    topic_title: str = ""


@dataclass(frozen=True)
class NormativeReport:
    checks: tuple[NormativeCheck, ...]

    @property
    def passed(self) -> tuple[NormativeCheck, ...]:
        return tuple(item for item in self.checks if item.verdict is NormativeVerdict.PASS)

    @property
    def warnings(self) -> tuple[NormativeCheck, ...]:
        return tuple(
            item for item in self.checks if item.verdict is NormativeVerdict.WARNING
        )

    @property
    def unchecked(self) -> tuple[NormativeCheck, ...]:
        return tuple(
            item for item in self.checks if item.verdict is NormativeVerdict.NOT_CHECKED
        )

    def for_layer(self, layer: NormativeLayer) -> tuple[NormativeCheck, ...]:
        return tuple(item for item in self.checks if item.layer is layer)


APPROVED_ACADEMIC_YEAR = "2026–2027"
APPROVED_WEEK_COUNT = 36
VACATION_GAP_START = date(2026, 12, 31)
VACATION_GAP_END = date(2027, 1, 10)

_THEORY_TYPE_MARKERS = ("теоретическ", "беседа")
_PRACTICE_TYPE_MARKERS = (
    "практическ",
    "практикум",
    "трениров",
    "экскурси",
    "викторин",
    "ситуационн",
    "проектно-практич",
    "наблюден",
)


def normalize_academic_year(value: str | None) -> str | None:
    if not value:
        return None
    found = re.search(r"(\d{4})\s*[-–]\s*(\d{4})", value)
    if found is None:
        return None
    return f"{found.group(1)}–{found.group(2)}"


def academic_year_period(academic_year: str | None) -> tuple[date, date] | None:
    normalized = normalize_academic_year(academic_year)
    if normalized is None:
        return None
    start_year = int(normalized[:4])
    end_year = int(normalized[5:])
    if end_year != start_year + 1:
        return None
    return date(start_year, 9, 1), date(end_year, 8, 31)


def _document_covers_period(document: NormativeDocument, start: date, end: date) -> bool:
    if document.effective_from is not None and document.effective_from > end:
        return False
    if document.effective_until is not None and document.effective_until < start:
        return False
    return True


def _yearly_hour_values(utp: UtpParseResult) -> tuple[int, ...]:
    metadata = utp.metadata
    values: list[int] = []
    if metadata.hours_per_year is not None:
        values.append(metadata.hours_per_year)
    if metadata.study_weeks is not None and metadata.hours_per_week is not None:
        values.append(metadata.study_weeks * metadata.hours_per_week)
    if metadata.stated_schedule_hours is not None:
        values.append(metadata.stated_schedule_hours)
    if utp.table_totals is not None:
        values.append(utp.table_totals.total)
    return tuple(values)


def _age_found(utp: UtpParseResult, program: ProgramData | None) -> bool:
    if (utp.metadata.student_age or "").strip():
        return True
    return bool(program and (program.student_age or "").strip())


def _study_year_number(
    utp: UtpParseResult,
    study_year_hints: tuple[str | None, ...],
) -> int | None:
    for raw in (utp.metadata.study_year, *study_year_hints):
        number = infer_study_year_number(raw)
        if number is not None:
            return number
    return None


def _document_academic_year(utp: UtpParseResult) -> str | None:
    return normalize_academic_year(utp.metadata.academic_year)


def _week_length_days(week) -> int:
    return (week.end - week.start).days + 1


def _week_overlaps_gap(week) -> bool:
    return week.start <= VACATION_GAP_END and week.end >= VACATION_GAP_START


def _hours_for_week(schedule: ScheduleResult, week_number: int) -> int:
    return sum(element.hours for element in schedule.elements if element.week.number == week_number)


def _schedule_hour_totals(schedule: ScheduleResult) -> tuple[int, int, int]:
    theory = sum(element.hours for element in schedule.elements if element.part_type == "theory")
    practice = sum(
        element.hours for element in schedule.elements if element.part_type == "practice"
    )
    return theory, practice, theory + practice


def _type_is_theory(lesson_type: str) -> bool:
    low = lesson_type.casefold()
    return any(marker in low for marker in _THEORY_TYPE_MARKERS)


def _type_is_practice(lesson_type: str) -> bool:
    low = lesson_type.casefold()
    return any(marker in low for marker in _PRACTICE_TYPE_MARKERS)


def _attestation_trace(
    utp: UtpParseResult,
    lessons: tuple[NormativeLessonView, ...],
    program: ProgramData | None,
) -> bool:
    parts = [topic.title for topic in utp.topics]
    parts.extend(topic.parent_section or "" for topic in utp.topics)
    if program is not None:
        parts.extend(item.title for item in program.content_items)
    for lesson in lessons:
        parts.extend((lesson.lesson_type, lesson.assessment_method, lesson.topic_title))
    return "аттестац" in " ".join(parts).casefold()


def _check_registry(
    *,
    academic_year: str,
    registry: NormativeRegistry,
) -> NormativeCheck:
    period = academic_year_period(academic_year)
    if period is None:
        return NormativeCheck(
            "registry_in_force",
            NormativeVerdict.NOT_CHECKED,
            "Не удалось сопоставить учебный год со сроками нормативных документов.",
            NormativeLayer.FEDERAL,
        )
    start, end = period
    missing = [
        document.number
        for document in registry.current.documents
        if not _document_covers_period(document, start, end)
    ]
    year_label = normalize_academic_year(academic_year) or academic_year
    if missing:
        return NormativeCheck(
            "registry_in_force",
            NormativeVerdict.WARNING,
            "Не все нормативные документы реестра действуют в учебном году "
            f"{year_label}.",
            NormativeLayer.FEDERAL,
        )
    return NormativeCheck(
        "registry_in_force",
        NormativeVerdict.PASS,
        "Нормативные документы (273-ФЗ, приказ 629, СП 2.4.2.4283-26) "
        f"действуют в учебном году {year_label}.",
        NormativeLayer.FEDERAL,
    )


def _check_age(utp: UtpParseResult, program: ProgramData | None) -> NormativeCheck:
    if _age_found(utp, program):
        return NormativeCheck(
            "age_found",
            NormativeVerdict.PASS,
            "Возраст обучающихся указан в документах.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "age_found",
        NormativeVerdict.WARNING,
        "Возраст обучающихся в программе и УТП не найден.",
        NormativeLayer.METHODICAL,
    )


def _check_duration(program: ProgramData | None) -> NormativeCheck:
    if program and (program.duration or "").strip():
        return NormativeCheck(
            "duration_found",
            NormativeVerdict.PASS,
            "Срок реализации программы указан.",
            NormativeLayer.METHODICAL,
        )
    if program is None:
        return NormativeCheck(
            "duration_found",
            NormativeVerdict.NOT_CHECKED,
            "Срок реализации не проверялся: программа не загружена.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "duration_found",
        NormativeVerdict.WARNING,
        "Срок реализации программы не найден.",
        NormativeLayer.METHODICAL,
    )


def _check_study_year(
    utp: UtpParseResult,
    study_year_hints: tuple[str | None, ...],
) -> NormativeCheck:
    if _study_year_number(utp, study_year_hints) is not None:
        return NormativeCheck(
            "study_year_found",
            NormativeVerdict.PASS,
            "Год обучения определён.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "study_year_found",
        NormativeVerdict.WARNING,
        "Год обучения в документах не найден.",
        NormativeLayer.METHODICAL,
    )


def _check_year_within_duration(
    utp: UtpParseResult,
    program: ProgramData | None,
    study_year_hints: tuple[str | None, ...],
) -> NormativeCheck:
    year_number = _study_year_number(utp, study_year_hints)
    duration_years = program.duration_years if program is not None else None
    if year_number is None or duration_years is None:
        return NormativeCheck(
            "year_within_duration",
            NormativeVerdict.NOT_CHECKED,
            "Нельзя сравнить год обучения со сроком программы: нет обоих чисел.",
            NormativeLayer.METHODICAL,
        )
    if year_number <= duration_years:
        return NormativeCheck(
            "year_within_duration",
            NormativeVerdict.PASS,
            "Год обучения не превышает срок программы.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "year_within_duration",
        NormativeVerdict.WARNING,
        "Год обучения больше указанного срока программы.",
        NormativeLayer.METHODICAL,
    )


def _check_yearly_hours(utp: UtpParseResult) -> NormativeCheck:
    values = _yearly_hour_values(utp)
    if not values:
        return NormativeCheck(
            "yearly_hours",
            NormativeVerdict.NOT_CHECKED,
            "Недостаточно данных, чтобы сверить годовые часы.",
            NormativeLayer.METHODICAL,
        )
    if len(set(values)) == 1:
        return NormativeCheck(
            "yearly_hours",
            NormativeVerdict.PASS,
            "Годовые часы в документах согласованы.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "yearly_hours",
        NormativeVerdict.WARNING,
        "Годовые часы в разных местах УТП не совпадают.",
        NormativeLayer.METHODICAL,
    )


def _check_weekly_load(utp: UtpParseResult) -> NormativeCheck:
    weekly = utp.metadata.hours_per_week
    if weekly is None:
        return NormativeCheck(
            "weekly_load",
            NormativeVerdict.NOT_CHECKED,
            "Недельная нагрузка не найдена.",
            NormativeLayer.METHODICAL,
        )
    provenance = utp.metadata.workload_provenance or "document"
    if provenance.startswith("derived"):
        return NormativeCheck(
            "weekly_load",
            NormativeVerdict.WARNING,
            "Недельная нагрузка вычислена автоматически, в УТП она не указана явно.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "weekly_load",
        NormativeVerdict.PASS,
        "Недельная нагрузка указана в УТП.",
        NormativeLayer.METHODICAL,
    )


def _check_expected_results(program: ProgramData | None) -> NormativeCheck:
    if program is None:
        return NormativeCheck(
            "expected_results",
            NormativeVerdict.NOT_CHECKED,
            "Ожидаемые результаты не проверялись: программа не загружена.",
            NormativeLayer.METHODICAL,
        )
    if program.expected_results or program.knowledge_outcomes or program.skill_outcomes:
        return NormativeCheck(
            "expected_results",
            NormativeVerdict.PASS,
            "В программе есть ожидаемые результаты.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "expected_results",
        NormativeVerdict.WARNING,
        "В программе не найдены ожидаемые результаты.",
        NormativeLayer.METHODICAL,
    )


def _check_academic_year(utp: UtpParseResult, selected_year: str) -> NormativeCheck:
    selected = normalize_academic_year(selected_year)
    documented = _document_academic_year(utp)
    if documented is None or selected is None:
        return NormativeCheck(
            "academic_year_match",
            NormativeVerdict.NOT_CHECKED,
            "Учебный год в программе и УТП не указан — сверка с выбранным годом не выполнялась.",
            NormativeLayer.LOCAL,
        )
    if documented == selected:
        return NormativeCheck(
            "academic_year_match",
            NormativeVerdict.PASS,
            "Учебный год в документах совпадает с выбранным.",
            NormativeLayer.LOCAL,
        )
    return NormativeCheck(
        "academic_year_match",
        NormativeVerdict.WARNING,
        "Учебный год в УТП отличается от выбранного.",
        NormativeLayer.LOCAL,
    )


def _check_topic_hours(utp: UtpParseResult) -> NormativeCheck:
    if not utp.topics:
        return NormativeCheck(
            "topic_hours",
            NormativeVerdict.NOT_CHECKED,
            "Часы по темам не сверялись: в УТП нет тем.",
            NormativeLayer.METHODICAL,
        )
    broken = [
        topic
        for topic in utp.topics
        if topic.hours.total != topic.hours.theory + topic.hours.practice
    ]
    if broken:
        return NormativeCheck(
            "topic_hours",
            NormativeVerdict.WARNING,
            "В части тем УТП сумма теории и практики не равна итогу темы.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "topic_hours",
        NormativeVerdict.PASS,
        "Часы тем УТП согласованы: всего равно теории и практике.",
        NormativeLayer.METHODICAL,
    )


def _check_theory_practice_hours(
    utp: UtpParseResult,
    schedule: ScheduleResult | None,
) -> NormativeCheck:
    if schedule is None or utp.table_totals is None:
        return NormativeCheck(
            "theory_practice_hours",
            NormativeVerdict.NOT_CHECKED,
            "Теория и практика плана не сверялись с УТП: нет расписания или итога.",
            NormativeLayer.METHODICAL,
        )
    theory, practice, _total = _schedule_hour_totals(schedule)
    if theory == utp.table_totals.theory and practice == utp.table_totals.practice:
        return NormativeCheck(
            "theory_practice_hours",
            NormativeVerdict.PASS,
            "Теория и практика календарного плана совпадают с УТП.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "theory_practice_hours",
        NormativeVerdict.WARNING,
        "Теория или практика календарного плана не совпадают с итогом УТП.",
        NormativeLayer.METHODICAL,
    )


def _check_plan_matches_utp(
    utp: UtpParseResult,
    schedule: ScheduleResult | None,
) -> NormativeCheck:
    if schedule is None or utp.table_totals is None:
        return NormativeCheck(
            "plan_matches_utp",
            NormativeVerdict.NOT_CHECKED,
            "План не сверялся с УТП: нет расписания или итога часов.",
            NormativeLayer.METHODICAL,
        )
    theory, practice, total = _schedule_hour_totals(schedule)
    expected = utp.table_totals
    scheduled_topics = {
        (element.topic_number, element.topic, element.section)
        for element in schedule.elements
    }
    source_topics = {
        (topic.number, topic.title, topic.parent_section or topic.title)
        for topic in utp.topics
    }
    if (
        theory == expected.theory
        and practice == expected.practice
        and total == expected.total
        and scheduled_topics == source_topics
    ):
        return NormativeCheck(
            "plan_matches_utp",
            NormativeVerdict.PASS,
            "Часы календарного плана совпадают с итогом УТП, все темы распределены.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "plan_matches_utp",
        NormativeVerdict.WARNING,
        "Календарный план расходится с УТП по часам или составу тем.",
        NormativeLayer.METHODICAL,
    )


def _check_type_vs_hours(
    lessons: tuple[NormativeLessonView, ...],
) -> NormativeCheck:
    if not lessons:
        return NormativeCheck(
            "type_vs_hours",
            NormativeVerdict.NOT_CHECKED,
            "Форма занятия не сверялась с часами: нет строк содержания.",
            NormativeLayer.METHODICAL,
        )
    conflicts = 0
    typed = 0
    for lesson in lessons:
        lesson_type = (lesson.lesson_type or "").strip()
        if not lesson_type:
            continue
        typed += 1
        theory_only = bool(lesson.theory_hours and not lesson.practice_hours)
        practice_only = bool(lesson.practice_hours and not lesson.theory_hours)
        if theory_only and _type_is_practice(lesson_type):
            conflicts += 1
        elif practice_only and _type_is_theory(lesson_type):
            conflicts += 1
    if typed == 0:
        return NormativeCheck(
            "type_vs_hours",
            NormativeVerdict.NOT_CHECKED,
            "Форма занятия не сверялась с часами: тип в строках не указан.",
            NormativeLayer.METHODICAL,
        )
    if conflicts:
        return NormativeCheck(
            "type_vs_hours",
            NormativeVerdict.WARNING,
            "Форма занятия не совпадает с часами теории или практики "
            f"в {conflicts} строках плана.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "type_vs_hours",
        NormativeVerdict.PASS,
        "Форма занятия не противоречит часам теории и практики.",
        NormativeLayer.METHODICAL,
    )


def _check_academic_grid(
    schedule: ScheduleResult | None,
    academic_year: str,
) -> NormativeCheck:
    if schedule is None:
        return NormativeCheck(
            "academic_grid",
            NormativeVerdict.NOT_CHECKED,
            "Сетка не сверялась: расписание не передано.",
            NormativeLayer.LOCAL,
        )
    selected = normalize_academic_year(academic_year)
    if selected != APPROVED_ACADEMIC_YEAR:
        return NormativeCheck(
            "academic_grid",
            NormativeVerdict.WARNING,
            "Проверяется только утверждённая сетка 2026–2027 на 36 недель.",
            NormativeLayer.LOCAL,
        )
    try:
        expected = build_academic_weeks(APPROVED_ACADEMIC_YEAR, APPROVED_WEEK_COUNT)
    except ValueError:
        return NormativeCheck(
            "academic_grid",
            NormativeVerdict.NOT_CHECKED,
            "Эталонную сетку 2026–2027 не удалось прочитать.",
            NormativeLayer.LOCAL,
        )
    actual = tuple((week.start, week.end) for week in schedule.weeks)
    wanted = tuple((week.start, week.end) for week in expected)
    if actual == wanted:
        return NormativeCheck(
            "academic_grid",
            NormativeVerdict.PASS,
            "Календарная сетка совпадает с утверждённой: 2026–2027, 36 недель.",
            NormativeLayer.LOCAL,
        )
    return NormativeCheck(
        "academic_grid",
        NormativeVerdict.WARNING,
        "Календарная сетка отличается от утверждённой сетки 2026–2027 / 36 недель.",
        NormativeLayer.LOCAL,
    )


def _check_vacation_gap(schedule: ScheduleResult | None) -> NormativeCheck:
    if schedule is None:
        return NormativeCheck(
            "vacation_gap",
            NormativeVerdict.NOT_CHECKED,
            "Разрыв 31.12–10.01 не проверялся: расписание не передано.",
            NormativeLayer.LOCAL,
        )
    overlapping = [week for week in schedule.weeks if _week_overlaps_gap(week)]
    if overlapping:
        return NormativeCheck(
            "vacation_gap",
            NormativeVerdict.WARNING,
            "Учебная неделя пересекает каникулярный разрыв 31.12–10.01. "
            "Перенос не выполнялся.",
            NormativeLayer.LOCAL,
        )
    return NormativeCheck(
        "vacation_gap",
        NormativeVerdict.PASS,
        "Занятия не стоят в каникулярном разрыве 31.12–10.01.",
        NormativeLayer.LOCAL,
    )


def _check_short_week_full_load(
    utp: UtpParseResult,
    schedule: ScheduleResult | None,
) -> NormativeCheck:
    if schedule is None or utp.metadata.hours_per_week is None:
        return NormativeCheck(
            "short_week_full_load",
            NormativeVerdict.NOT_CHECKED,
            "Короткие недели не проверялись: нет расписания или недельной нагрузки.",
            NormativeLayer.LOCAL,
        )
    weekly = utp.metadata.hours_per_week
    conflict_labels: list[str] = []
    for week in schedule.weeks:
        if _week_length_days(week) >= 7:
            continue
        if _hours_for_week(schedule, week.number) == weekly and weekly > 0:
            conflict_labels.append(week.date_range)
    if conflict_labels:
        return NormativeCheck(
            "short_week_full_load",
            NormativeVerdict.WARNING,
            "Короткая учебная неделя получила полную нагрузку: "
            + ", ".join(conflict_labels)
            + ". Перенос не выполнялся.",
            NormativeLayer.LOCAL,
        )
    return NormativeCheck(
        "short_week_full_load",
        NormativeVerdict.PASS,
        "Короткие недели не содержат полной недельной нагрузки.",
        NormativeLayer.LOCAL,
    )


def _check_attestation(
    utp: UtpParseResult,
    program: ProgramData | None,
    lessons: tuple[NormativeLessonView, ...],
) -> NormativeCheck:
    if program is None:
        return NormativeCheck(
            "attestation",
            NormativeVerdict.NOT_CHECKED,
            "Аттестация не проверялась: программа не загружена.",
            NormativeLayer.METHODICAL,
        )
    statements = program.attestation_statements
    if not statements:
        return NormativeCheck(
            "attestation",
            NormativeVerdict.NOT_CHECKED,
            "Промежуточная и итоговая аттестация не проверялись: "
            "в программе не указаны.",
            NormativeLayer.METHODICAL,
        )
    if _attestation_trace(utp, lessons, program):
        return NormativeCheck(
            "attestation",
            NormativeVerdict.PASS,
            "В программе указана аттестация, и в плане или УТП есть "
            "соответствующая тема либо вид контроля.",
            NormativeLayer.METHODICAL,
        )
    return NormativeCheck(
        "attestation",
        NormativeVerdict.WARNING,
        "В программе указана аттестация, но в календарном плане и УТП "
        "она не найдена.",
        NormativeLayer.METHODICAL,
    )


def evaluate_normative_mvp(
    utp: UtpParseResult,
    program: ProgramData | None,
    *,
    academic_year: str,
    study_year_hints: tuple[str | None, ...] = (),
    registry: NormativeRegistry | None = None,
    schedule: ScheduleResult | None = None,
    lessons: tuple[NormativeLessonView, ...] = (),
) -> NormativeReport:
    """Собрать проверки MVP. Не меняет входные данные и не бросает ошибок."""

    source = registry or get_builtin_normative_registry()
    checks = (
        _check_registry(academic_year=academic_year, registry=source),
        _check_academic_year(utp, academic_year),
        _check_academic_grid(schedule, academic_year),
        _check_vacation_gap(schedule),
        _check_short_week_full_load(utp, schedule),
        _check_age(utp, program),
        _check_duration(program),
        _check_study_year(utp, study_year_hints),
        _check_year_within_duration(utp, program, study_year_hints),
        _check_topic_hours(utp),
        _check_yearly_hours(utp),
        _check_weekly_load(utp),
        _check_theory_practice_hours(utp, schedule),
        _check_plan_matches_utp(utp, schedule),
        _check_type_vs_hours(lessons),
        _check_expected_results(program),
        _check_attestation(utp, program, lessons),
    )
    return NormativeReport(checks)
