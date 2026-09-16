"""Normalized, confirmed source of calendar topics and workload."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
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


@dataclass(frozen=True)
class ManualStudyPlanRow:
    """One teacher-entered row before it becomes a normalized Topic."""

    section: str | None
    topic: str
    total: HourValue
    theory: HourValue
    practice: HourValue


def hour_value_from_input(value: object, *, field_name: str) -> HourValue:
    """Parse an exact non-negative hour value from a UI cell."""

    if isinstance(value, bool) or value is None:
        raise ConfirmedStudyPlanError(f"Поле «{field_name}» обязательно.")
    token = str(value).strip().replace(",", ".")
    if not token:
        raise ConfirmedStudyPlanError(f"Поле «{field_name}» обязательно.")
    try:
        parsed = Decimal(token)
    except InvalidOperation as error:
        raise ConfirmedStudyPlanError(
            f"Поле «{field_name}» должно содержать число часов."
        ) from error
    if not parsed.is_finite() or parsed < 0:
        raise ConfirmedStudyPlanError(
            f"Поле «{field_name}» должно быть неотрицательным числом."
        )
    if parsed == parsed.to_integral_value():
        return int(parsed)
    return parsed.normalize()


def _manual_row(value: ManualStudyPlanRow | Mapping[str, object]) -> ManualStudyPlanRow:
    if isinstance(value, ManualStudyPlanRow):
        return value

    def first(*keys: str) -> object | None:
        for key in keys:
            if key in value:
                return value[key]
        return None

    topic_value = first("topic", "Тема")
    section_value = first("section", "Раздел")
    topic = "" if topic_value is None else str(topic_value).strip()
    section = (
        "" if section_value is None else str(section_value).strip()
    ) or None
    return ManualStudyPlanRow(
        section=section,
        topic=topic,
        total=hour_value_from_input(
            first("total", "Всего"), field_name="Всего"
        ),
        theory=hour_value_from_input(
            first("theory", "Теория"), field_name="Теория"
        ),
        practice=hour_value_from_input(
            first("practice", "Практика"), field_name="Практика"
        ),
    )


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
        if min(topic.hours.total, topic.hours.theory, topic.hours.practice) < 0:
            raise ConfirmedStudyPlanError(
                f"Часы темы «{topic.title}» должны быть неотрицательными."
            )
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
    study_weeks: int | None = None,
    hours_per_week: HourValue | None = None,
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
    if (
        utp.metadata.hours_per_year is not None
        and utp.metadata.hours_per_year != totals.total
    ):
        raise ConfirmedStudyPlanError(
            "Годовой итог внешнего УТП не совпадает с итогом таблицы тем и часов."
        )
    source_weeks = utp.metadata.study_weeks
    source_weekly = utp.metadata.hours_per_week
    if source_weeks is not None and study_weeks not in {None, source_weeks}:
        raise ConfirmedStudyPlanError(
            "Введённое количество недель противоречит значению внешнего УТП."
        )
    if source_weekly is not None and hours_per_week not in {None, source_weekly}:
        raise ConfirmedStudyPlanError(
            "Введённое количество часов в неделю противоречит внешнему УТП."
        )
    resolved_weeks = source_weeks if source_weeks is not None else study_weeks
    resolved_weekly = source_weekly if source_weekly is not None else hours_per_week
    if resolved_weeks is None:
        raise ConfirmedStudyPlanError("Укажите количество учебных недель.")
    if resolved_weekly is None:
        raise ConfirmedStudyPlanError("Укажите количество часов в неделю.")
    return _confirmed_plan(
        study_year=resolved_year,
        topics=_topics_in_teacher_order(utp),
        total_hours=totals.total,
        theory_hours=totals.theory,
        practice_hours=totals.practice,
        study_weeks=resolved_weeks,
        hours_per_week=resolved_weekly,
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


def confirmed_plan_from_manual_rows(
    *,
    study_year: int,
    rows: Sequence[ManualStudyPlanRow | Mapping[str, object]],
    study_weeks: int,
    hours_per_week: object,
) -> ConfirmedStudyPlan:
    """Normalize the dynamic UI table without losing decimals or row order."""

    normalized_rows = tuple(_manual_row(row) for row in rows)
    if not normalized_rows:
        raise ConfirmedStudyPlanError("Добавьте хотя бы одну тему учебного плана.")
    topics: list[Topic] = []
    for index, row in enumerate(normalized_rows, start=1):
        if not row.topic:
            raise ConfirmedStudyPlanError(
                f"Укажите тему в строке {index} учебного плана."
            )
        if row.theory + row.practice != row.total:
            raise ConfirmedStudyPlanError(
                f"В строке {index} теория и практика не совпадают с итогом часов."
            )
        topics.append(
            Topic(
                number=str(index),
                title=row.topic,
                hours=Hours(row.total, row.theory, row.practice),
                parent_section=row.section or row.topic,
                is_standalone_section=not bool(row.section),
            )
        )
    totals = _sum_hours(tuple(topics))
    weekly = hour_value_from_input(
        hours_per_week,
        field_name="Количество часов в неделю",
    )
    return confirmed_plan_from_manual(
        study_year=study_year,
        topics=tuple(topics),
        total_hours=totals.total,
        theory_hours=totals.theory,
        practice_hours=totals.practice,
        study_weeks=study_weeks,
        hours_per_week=weekly,
    )
