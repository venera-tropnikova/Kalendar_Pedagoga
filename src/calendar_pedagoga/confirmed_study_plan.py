"""Normalized, confirmed source of calendar topics and workload."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from calendar_pedagoga.parsing import (
    HourValue,
    Hours,
    Section,
    Topic,
    UtpMetadata,
    UtpParseResult,
)
from calendar_pedagoga.program_parsing import infer_study_year_number


StudyPlanSource = Literal["external_utp", "manual"]


class ConfirmedStudyPlanError(ValueError):
    """A candidate study plan cannot be confirmed without guessing."""


def _sum_hours(values: tuple[Topic, ...]) -> Hours:
    return Hours(
        sum((item.hours.total for item in values), 0),
        sum((item.hours.theory for item in values), 0),
        sum((item.hours.practice for item in values), 0),
    )


def _section_number(topic: Topic) -> str | None:
    number = (topic.number or "").strip().rstrip(".")
    if not number:
        return None
    return number.split(".", 1)[0]


def _sections_from_topics(topics: tuple[Topic, ...]) -> tuple[Section, ...]:
    ordered_names: list[str] = []
    grouped: dict[str, list[Topic]] = {}
    for topic in topics:
        name = (topic.parent_section or topic.title).strip()
        if name not in grouped:
            ordered_names.append(name)
            grouped[name] = []
        grouped[name].append(topic)
    return tuple(
        Section(
            _section_number(grouped[name][0]),
            name,
            _sum_hours(tuple(grouped[name])),
            is_standalone_position=(
                len(grouped[name]) == 1 and grouped[name][0].is_standalone_section
            ),
        )
        for name in ordered_names
    )


def _topics_in_teacher_order(utp: UtpParseResult) -> tuple[Topic, ...]:
    ordered: list[Topic] = []
    for section in utp.sections:
        ordered.extend(
            topic for topic in utp.topics if topic.parent_section == section.title
        )
    if len(ordered) != len(utp.topics) or set(map(id, ordered)) != set(
        map(id, utp.topics)
    ):
        raise ConfirmedStudyPlanError(
            "Не удалось однозначно восстановить порядок тем внешнего УТП."
        )
    return tuple(ordered)


@dataclass(frozen=True)
class ConfirmedStudyPlan:
    """The only authoritative topics and hours accepted by the calendar pipeline.

    ``_reference_metadata`` and ``_reference_warnings`` preserve non-authoritative
    display/provenance details from an external UTP. All workload values in the
    compatibility projection are always replaced by the normalized fields above.
    """

    study_year: int
    topics: tuple[Topic, ...]
    total_hours: HourValue
    theory_hours: HourValue
    practice_hours: HourValue
    study_weeks: int
    hours_per_week: HourValue
    source: StudyPlanSource
    _reference_metadata: UtpMetadata = field(
        default_factory=UtpMetadata,
        repr=False,
        compare=False,
    )
    _reference_warnings: tuple[str, ...] = field(
        default_factory=tuple,
        repr=False,
        compare=False,
    )

    @property
    def metadata(self) -> UtpMetadata:
        return self.as_utp_parse_result().metadata

    @property
    def sections(self) -> tuple[Section, ...]:
        return _sections_from_topics(self.topics)

    @property
    def table_totals(self) -> Hours:
        return Hours(self.total_hours, self.theory_hours, self.practice_hours)

    @property
    def warnings(self) -> tuple[str, ...]:
        return self._reference_warnings

    def as_utp_parse_result(self) -> UtpParseResult:
        """Project the normalized plan into the legacy downstream read contract."""

        metadata = replace(
            self._reference_metadata,
            study_year=f"{self.study_year} год обучения",
            hours_per_year=self.total_hours,
            hours_per_week=self.hours_per_week,
            study_weeks=self.study_weeks,
            workload_provenance=self.source,
        )
        return UtpParseResult(
            metadata=metadata,
            sections=self.sections,
            topics=self.topics,
            table_totals=self.table_totals,
            warnings=self.warnings,
        )


def _confirmed_plan(
    *,
    study_year: int,
    topics: tuple[Topic, ...],
    total_hours: HourValue,
    theory_hours: HourValue,
    practice_hours: HourValue,
    study_weeks: int,
    hours_per_week: HourValue,
    source: StudyPlanSource,
    reference_metadata: UtpMetadata | None = None,
    reference_warnings: tuple[str, ...] = (),
) -> ConfirmedStudyPlan:
    if study_year <= 0:
        raise ConfirmedStudyPlanError("Год обучения должен быть положительным.")
    if not topics:
        raise ConfirmedStudyPlanError("Подтверждённый учебный план не содержит тем.")
    if study_weeks <= 0 or hours_per_week <= 0:
        raise ConfirmedStudyPlanError(
            "Количество недель и часов в неделю должно быть положительным."
        )
    if theory_hours + practice_hours != total_hours:
        raise ConfirmedStudyPlanError(
            "Итоги теории и практики не совпадают с общим количеством часов."
        )
    for topic in topics:
        if topic.hours.theory + topic.hours.practice != topic.hours.total:
            raise ConfirmedStudyPlanError(
                f"Часы темы «{topic.title}» не согласованы."
            )
    topic_totals = _sum_hours(topics)
    expected = Hours(total_hours, theory_hours, practice_hours)
    if topic_totals != expected:
        raise ConfirmedStudyPlanError(
            "Сумма часов тем не совпадает с итогами учебного плана."
        )
    if study_weeks * hours_per_week != total_hours:
        raise ConfirmedStudyPlanError(
            "Количество недель и недельная нагрузка не совпадают с итогом часов."
        )
    return ConfirmedStudyPlan(
        study_year=study_year,
        topics=topics,
        total_hours=total_hours,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        study_weeks=study_weeks,
        hours_per_week=hours_per_week,
        source=source,
        _reference_metadata=reference_metadata or UtpMetadata(),
        _reference_warnings=reference_warnings,
    )


def confirmed_plan_from_external_utp(
    utp: UtpParseResult,
    *,
    study_year: int | None = None,
    source_name: str | None = None,
) -> ConfirmedStudyPlan:
    """Confirm a fully resolved external UTP without consulting PROGRAM data."""

    resolved_year = (
        study_year
        or infer_study_year_number(utp.metadata.study_year)
        or infer_study_year_number(source_name)
    )
    if resolved_year is None:
        raise ConfirmedStudyPlanError("Укажите год обучения для учебного плана.")
    totals = utp.table_totals
    if totals is None:
        raise ConfirmedStudyPlanError("В УТП не найдена итоговая строка часов.")
    if utp.metadata.study_weeks is None:
        raise ConfirmedStudyPlanError("Укажите количество учебных недель.")
    if utp.metadata.hours_per_week is None:
        raise ConfirmedStudyPlanError("Укажите количество часов в неделю.")
    return _confirmed_plan(
        study_year=resolved_year,
        topics=_topics_in_teacher_order(utp),
        total_hours=totals.total,
        theory_hours=totals.theory,
        practice_hours=totals.practice,
        study_weeks=utp.metadata.study_weeks,
        hours_per_week=utp.metadata.hours_per_week,
        source="external_utp",
        reference_metadata=utp.metadata,
        reference_warnings=utp.warnings,
    )


def confirmed_plan_from_manual(
    *,
    study_year: int,
    topics: tuple[Topic, ...],
    total_hours: HourValue,
    theory_hours: HourValue,
    practice_hours: HourValue,
    study_weeks: int,
    hours_per_week: HourValue,
) -> ConfirmedStudyPlan:
    """Pure manual-input adapter; a UI for collecting these values comes later."""

    return _confirmed_plan(
        study_year=study_year,
        topics=topics,
        total_hours=total_hours,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        study_weeks=study_weeks,
        hours_per_week=hours_per_week,
        source="manual",
    )
