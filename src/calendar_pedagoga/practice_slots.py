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


_CATALOG_HEAD_RE = re.compile(
    r"(?i)^(?:практика\.\s*)?(?P<head>"
    r"экскурсии|экскурсия|экскурсионные поездки|поездки|"
    r"прогулки(?:\s+и\s+экскурсии)?|посещения"
    r")(?P<sep>\s*:)?\s+(?P<body>.+)$"
)
_PLACE_PREP_RE = re.compile(
    r"(?i)^(по|к|ко|в|во|на|у|около|через|от|до|из|с|со)\s+"
)
_STREET_PREFIX_RE = re.compile(r"(?i)^(по\s+улицам?)\s*[—–-]?\s*(.+)$")
_PURPOSE_AFTER_NA_RE = re.compile(
    r"(?i)^на\s+(развитие|растягивание|расслабление|постановку|"
    r"выполнение|закрепление)\b"
)
_FINITE_IN_ITEM_RE = re.compile(
    r"(?i)\b(посещает|совершает|выполняет|участвует|проводит|"
    r"готовит|составляет|изучает|отрабатывает)\b"
)
_NAME_ITEM_RE = re.compile(
    r"(?i)^(?:[«\"„].+|[A-ZА-ЯЁ][\w.\-]*(?:\s+[A-ZА-ЯЁ\w.\-]*)*)"
)


def _normalize_catalog_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_coordinated_places(item: str) -> list[str]:
    """Split 'на A и в B' when both sides are prepositional places."""

    parts = re.split(r"\s+и\s+", item)
    if len(parts) < 2:
        return [item]
    if all(_PLACE_PREP_RE.match(part.strip()) for part in parts):
        return [part.strip() for part in parts if part.strip()]
    return [item]


def _inherit_street_prefix(items: list[str]) -> list[str]:
    qualified: list[str] = []
    street_prefix = ""
    for raw in items:
        item = raw.strip(" .;")
        if not item:
            continue
        street = _STREET_PREFIX_RE.match(item)
        if street:
            street_prefix = street.group(1)
            rest = street.group(2).strip(" -—–")
            qualified.append(_normalize_catalog_spaces(f"{street_prefix} {rest}"))
            continue
        if street_prefix and not _PLACE_PREP_RE.match(item):
            qualified.append(_normalize_catalog_spaces(f"{street_prefix} {item}"))
            continue
        if _PLACE_PREP_RE.match(item):
            street_prefix = ""
        qualified.append(item)
    return qualified


def _parse_catalog_items(body: str) -> list[str]:
    chunks = [part.strip(" .;") for part in body.split(",") if part.strip(" .;")]
    items: list[str] = []
    for chunk in chunks:
        if chunk.casefold() in {"и другие", "другие"}:
            if items:
                items[-1] = _normalize_catalog_spaces(f"{items[-1]} и другие")
            continue
        items.extend(_split_coordinated_places(chunk))
    return _inherit_street_prefix(items)


def _item_is_place_or_name(item: str, *, allow_names: bool) -> bool:
    text = item.strip()
    if not text or _FINITE_IN_ITEM_RE.search(text) or _PURPOSE_AFTER_NA_RE.match(text):
        return False
    if _PLACE_PREP_RE.match(text):
        return True
    return allow_names and bool(_NAME_ITEM_RE.match(text))


def parse_splittable_catalog(text: str) -> tuple[str, str, tuple[str, ...]] | None:
    """Activity head + catalog items, or None if the list is not safely splittable."""

    source = _normalize_catalog_spaces(text).rstrip(" .")
    if not source:
        return None
    match = _CATALOG_HEAD_RE.match(source)
    if not match:
        return None
    head = _normalize_catalog_spaces(match.group("head"))
    separator = ": " if match.group("sep") else " "
    items = _parse_catalog_items(match.group("body"))
    if len(items) < 2:
        return None
    allow_names = bool(match.group("sep"))
    if not all(_item_is_place_or_name(item, allow_names=allow_names) for item in items):
        return None
    return head, separator, tuple(items)


def _collapse_shared_prefix(items: tuple[str, ...]) -> str:
    """Join items, writing a shared 'по улицам' prefix once for consecutive streets."""

    pieces: list[str] = []
    street_run: list[str] = []
    street_prefix = ""

    def flush() -> None:
        nonlocal street_run, street_prefix
        if not street_run:
            return
        pieces.append(f"{street_prefix} " + ", ".join(street_run))
        street_run = []
        street_prefix = ""

    for item in items:
        street = _STREET_PREFIX_RE.match(item)
        if street is None:
            flush()
            pieces.append(item)
            continue
        prefix = street.group(1)
        tail = street.group(2).strip()
        if street_run and prefix.casefold() != street_prefix.casefold():
            flush()
        street_prefix = prefix
        street_run.append(tail)
    flush()
    return ", ".join(pieces)


def format_catalog_part(head: str, separator: str, items: tuple[str, ...]) -> str:
    body = _collapse_shared_prefix(items)
    if separator == ": ":
        return _normalize_catalog_spaces(f"{head}: {body}")
    return _normalize_catalog_spaces(f"{head} {body}")


def partition_items_by_volume(
    items: tuple[str, ...], parts: int
) -> tuple[tuple[str, ...], ...] | None:
    """Deterministic near-even split by character volume; None if not enough items."""

    if parts <= 0 or len(items) < parts:
        return None
    weights = [max(len(item), 1) for item in items]
    total = sum(weights)
    groups: list[tuple[str, ...]] = []
    start = 0
    consumed = 0
    for index in range(parts):
        remaining_parts = parts - index
        if remaining_parts == 1:
            groups.append(tuple(items[start:]))
            break
        target = (total - consumed) / remaining_parts
        take = 1
        acc = weights[start]
        max_take = len(items) - start - (remaining_parts - 1)
        while take < max_take:
            nxt = acc + weights[start + take]
            if abs(nxt - target) < abs(acc - target):
                acc = nxt
                take += 1
            else:
                break
        groups.append(tuple(items[start : start + take]))
        start += take
        consumed += acc
    if sum(len(group) for group in groups) != len(items):
        return None
    if any(not group for group in groups):
        return None
    return tuple(groups)


def split_catalog_across_weeks(text: str, weeks: int) -> tuple[str, ...] | None:
    """Split a place/object catalog across weeks, or None to keep the current copy."""

    parsed = parse_splittable_catalog(text)
    if parsed is None:
        return None
    head, separator, items = parsed
    groups = partition_items_by_volume(items, weeks)
    if groups is None:
        return None
    return tuple(format_catalog_part(head, separator, group) for group in groups)


def continuation_flags(
    slots: tuple[tuple[str, ...], ...],
) -> tuple[bool, ...]:
    return tuple(slot_is_continuation(slots, index) for index in range(len(slots)))


def distribute_splittable_slot_catalogs(
    slots: tuple[tuple[str, ...], ...],
) -> tuple[tuple[str, ...], ...]:
    """Inside a stretched continuation run, share list items instead of copying all."""

    if not slots:
        return slots
    updated = list(slots)
    index = 0
    while index < len(updated):
        cursor = index + 1
        while cursor < len(updated) and updated[cursor] == updated[index] and updated[index]:
            cursor += 1
        run = cursor - index
        unit = updated[index]
        if run > 1 and len(unit) == 1:
            parts = split_catalog_across_weeks(unit[0], run)
            if parts is not None:
                for offset, part in enumerate(parts):
                    updated[index + offset] = (part,)
        index = cursor
    return tuple(updated)


def assign_distributed_practice_slots(
    units: list[str], w: int
) -> tuple[tuple[tuple[str, ...], ...], tuple[bool, ...]]:
    """Assign units, remember continuation, then split safe catalogs inside runs."""

    slots = assign_practice_slots(units, w)
    flags = continuation_flags(slots)
    return distribute_splittable_slot_catalogs(slots), flags


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
