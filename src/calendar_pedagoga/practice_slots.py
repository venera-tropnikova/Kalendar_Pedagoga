"""Назначение практических единиц темы по календарным появлениям (W > 1)."""

from __future__ import annotations

import re

from calendar_pedagoga.lesson_content import _clause_units, _split_explicit_practice

SLOT_PACK_WARNING = (
    "На одну календарную неделю приходится несколько исходных практических единиц."
)
SLOT_CONTINUE_WARNING = (
    "Дополнительная неделя темы — продолжение уже представленного "
    "практического содержания, не новая единица источника."
)

_CONTINUATION_MARK = "Продолжение."


def practice_units_from_text(text: str) -> list[str]:
    """Клаузы уже выделенного практического текста или полного блока с маркером."""

    if not (text or "").strip():
        return []
    explicit = _split_explicit_practice(text)
    source = explicit[1] if explicit else text
    return _clause_units(source) if source.strip() else []


def practice_units_from_content(
    content: str,
    *,
    theory_hours: int,
    practice_hours: int,
) -> list[str]:
    """Практические клаузы источника; без маркера при смешанных часах — пусто."""

    if practice_hours <= 0 or not (content or "").strip():
        return []
    explicit = _split_explicit_practice(content)
    if explicit:
        return practice_units_from_text(explicit[1])
    if theory_hours:
        return []
    return practice_units_from_text(content)


def assign_practice_slots(
    units: list[str], w: int
) -> tuple[tuple[str, ...], ...]:
    """Разложить C единиц на W слотов: непрерывные куски или растяжение хвоста."""

    if w <= 0:
        return ()
    if not units:
        return tuple(() for _ in range(w))
    count = len(units)
    if count > w:
        return tuple(
            tuple(units[index * count // w : (index + 1) * count // w])
            for index in range(w)
        )
    if count == w:
        return tuple((unit,) for unit in units)
    base, remainder = divmod(w, count)
    sizes = [base + (1 if index >= count - remainder else 0) for index in range(count)]
    slots: list[tuple[str, ...]] = []
    for unit, size in zip(units, sizes):
        slots.extend([(unit,)] * size)
    return tuple(slots)


def slot_is_continuation(
    slots: tuple[tuple[str, ...], ...], index: int
) -> bool:
    return (
        0 < index < len(slots)
        and slots[index] == slots[index - 1]
        and bool(slots[index])
    )


def format_slot_practice_text(
    slot: tuple[str, ...], *, continuation: bool = False
) -> str:
    """Исходные формулировки слота; маркер продолжения без новых фактов."""

    body = ". ".join(
        re.sub(r"\s+", " ", item).strip().rstrip(" .") for item in slot if item.strip()
    )
    if not body:
        return ""
    if continuation:
        return f"{_CONTINUATION_MARK} {body}"
    return body
