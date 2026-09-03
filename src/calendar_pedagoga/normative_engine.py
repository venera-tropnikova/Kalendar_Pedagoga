"""Нормативные проверки ДОП. Не изменяет расписание, часы, CE2 и DOCX."""

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


class NormativeVerdict(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True)
class NormativeCheck:
    check_id: str
    verdict: NormativeVerdict
    teacher_text: str


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
        )
    return NormativeCheck(
        "registry_in_force",
        NormativeVerdict.PASS,
        "Нормативные документы (273-ФЗ, приказ 629, СП 2.4.2.4283-26) "
        f"действуют в учебном году {year_label}.",
    )


def _check_age(utp: UtpParseResult, program: ProgramData | None) -> NormativeCheck:
    if _age_found(utp, program):
        return NormativeCheck(
            "age_found",
            NormativeVerdict.PASS,
            "Возраст обучающихся указан в документах.",
        )
    return NormativeCheck(
        "age_found",
        NormativeVerdict.WARNING,
        "Возраст обучающихся в программе и УТП не найден.",
    )


def _check_duration(program: ProgramData | None) -> NormativeCheck:
    if program and (program.duration or "").strip():
        return NormativeCheck(
            "duration_found",
            NormativeVerdict.PASS,
            "Срок реализации программы указан.",
        )
    if program is None:
        return NormativeCheck(
            "duration_found",
            NormativeVerdict.NOT_CHECKED,
            "Срок реализации не проверялся: программа не загружена.",
        )
    return NormativeCheck(
        "duration_found",
        NormativeVerdict.WARNING,
        "Срок реализации программы не найден.",
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
        )
    return NormativeCheck(
        "study_year_found",
        NormativeVerdict.WARNING,
        "Год обучения в документах не найден.",
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
        )
    if year_number <= duration_years:
        return NormativeCheck(
            "year_within_duration",
            NormativeVerdict.PASS,
            "Год обучения не превышает срок программы.",
        )
    return NormativeCheck(
        "year_within_duration",
        NormativeVerdict.WARNING,
        "Год обучения больше указанного срока программы.",
    )


def _check_yearly_hours(utp: UtpParseResult) -> NormativeCheck:
    values = _yearly_hour_values(utp)
    if not values:
        return NormativeCheck(
            "yearly_hours",
            NormativeVerdict.NOT_CHECKED,
            "Недостаточно данных, чтобы сверить годовые часы.",
        )
    if len(set(values)) == 1:
        return NormativeCheck(
            "yearly_hours",
            NormativeVerdict.PASS,
            "Годовые часы в документах согласованы.",
        )
    return NormativeCheck(
        "yearly_hours",
        NormativeVerdict.WARNING,
        "Годовые часы в разных местах УТП не совпадают.",
    )


def _check_weekly_load(utp: UtpParseResult) -> NormativeCheck:
    weekly = utp.metadata.hours_per_week
    if weekly is None:
        return NormativeCheck(
            "weekly_load",
            NormativeVerdict.NOT_CHECKED,
            "Недельная нагрузка не найдена.",
        )
    provenance = utp.metadata.workload_provenance or "document"
    if provenance.startswith("derived"):
        return NormativeCheck(
            "weekly_load",
            NormativeVerdict.WARNING,
            "Недельная нагрузка вычислена автоматически, в УТП она не указана явно.",
        )
    return NormativeCheck(
        "weekly_load",
        NormativeVerdict.PASS,
        "Недельная нагрузка указана в УТП.",
    )


def _check_expected_results(program: ProgramData | None) -> NormativeCheck:
    if program is None:
        return NormativeCheck(
            "expected_results",
            NormativeVerdict.NOT_CHECKED,
            "Ожидаемые результаты не проверялись: программа не загружена.",
        )
    if program.expected_results or program.knowledge_outcomes or program.skill_outcomes:
        return NormativeCheck(
            "expected_results",
            NormativeVerdict.PASS,
            "В программе есть ожидаемые результаты.",
        )
    return NormativeCheck(
        "expected_results",
        NormativeVerdict.WARNING,
        "В программе не найдены ожидаемые результаты.",
    )


def _check_academic_year(utp: UtpParseResult, selected_year: str) -> NormativeCheck:
    selected = normalize_academic_year(selected_year)
    documented = _document_academic_year(utp)
    if documented is None or selected is None:
        return NormativeCheck(
            "academic_year_match",
            NormativeVerdict.NOT_CHECKED,
            "Учебный год в программе и УТП не указан — сверка с выбранным годом не выполнялась.",
        )
    if documented == selected:
        return NormativeCheck(
            "academic_year_match",
            NormativeVerdict.PASS,
            "Учебный год в документах совпадает с выбранным.",
        )
    return NormativeCheck(
        "academic_year_match",
        NormativeVerdict.WARNING,
        "Учебный год в УТП отличается от выбранного.",
    )


def evaluate_normative_mvp(
    utp: UtpParseResult,
    program: ProgramData | None,
    *,
    academic_year: str,
    study_year_hints: tuple[str | None, ...] = (),
    registry: NormativeRegistry | None = None,
) -> NormativeReport:
    """Собрать проверки MVP. Не меняет входные данные и не бросает ошибок."""

    source = registry or get_builtin_normative_registry()
    checks = (
        _check_registry(academic_year=academic_year, registry=source),
        _check_age(utp, program),
        _check_duration(program),
        _check_study_year(utp, study_year_hints),
        _check_year_within_duration(utp, program, study_year_hints),
        _check_yearly_hours(utp),
        _check_weekly_load(utp),
        _check_expected_results(program),
        _check_academic_year(utp, academic_year),
    )
    return NormativeReport(checks)
