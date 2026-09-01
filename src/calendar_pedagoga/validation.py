"""Проверка согласованности извлечённых данных УТП."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from calendar_pedagoga.parsing import UtpParseResult


def validate_utp(result: "UtpParseResult") -> list[str]:
    warnings: list[str] = []
    metadata = result.metadata
    keys = ("total", "theory", "practice")

    for topic in result.topics:
        if topic.hours.total != topic.hours.theory + topic.hours.practice:
            warnings.append(
                f"Тема {topic.number or 'без номера'} «{topic.title}»: всего "
                f"{topic.hours.total}, но теория + практика = "
                f"{topic.hours.theory + topic.hours.practice}."
            )

    for section in result.sections:
        children = [
            topic for topic in result.topics if topic.parent_section == section.title
        ]
        if children:
            sums = tuple(
                sum(getattr(topic.hours, key) for topic in children) for key in keys
            )
            actual = tuple(getattr(section.hours, key) for key in keys)
            if actual != sums:
                warnings.append(
                    f"Раздел «{section.title}»: итоги раздела {actual} не совпадают "
                    f"с суммой тем {sums} (всего, теория, практика)."
                )

    topic_sums = tuple(
        sum(getattr(topic.hours, key) for topic in result.topics) for key in keys
    )
    if result.table_totals:
        table = tuple(getattr(result.table_totals, key) for key in keys)
        if result.table_totals.total != (
            result.table_totals.theory + result.table_totals.practice
        ):
            warnings.append(
                f"Итоговая строка УТП: всего {result.table_totals.total}, "
                f"но теория + практика = "
                f"{result.table_totals.theory + result.table_totals.practice}."
            )
        if table != topic_sums:
            warnings.append(
                f"Сумма тем {topic_sums} не совпадает с итоговой строкой УТП "
                f"{table} (всего, теория, практика)."
            )

    calculated = None
    if metadata.study_weeks is not None and metadata.hours_per_week is not None:
        calculated = metadata.study_weeks * metadata.hours_per_week
    values: list[tuple[str, int]] = []
    if metadata.hours_per_year is not None:
        values.append(("информационная справка", metadata.hours_per_year))
    if calculated is not None:
        values.append(
            (
                f"{metadata.study_weeks} недель × {metadata.hours_per_week} часа",
                calculated,
            )
        )
    if metadata.stated_schedule_hours is not None:
        values.append(("описание недельной нагрузки", metadata.stated_schedule_hours))
    if result.table_totals is not None:
        values.append(("итоговая строка УТП", result.table_totals.total))
    if len({value for _, value in values}) > 1:
        details = "; ".join(f"{source}: {value} ч." for source, value in values)
        warnings.append(f"Противоречие по годовому объёму часов: {details}")

    return warnings
