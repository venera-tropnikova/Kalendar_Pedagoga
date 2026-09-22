"""Confirm an incompletely recognized program structure before generation.

Reuses ConfirmedStudyPlan, the manual topic table and USER_CONFIRMED.
Does not add a second pipeline and does not bypass the P0 readiness gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re

from calendar_pedagoga.confirmed_study_plan import (
    ConfirmedStudyPlan,
    ConfirmedStudyPlanError,
    confirmed_plan_from_manual_rows,
    hour_value_from_input,
)
from calendar_pedagoga.match_review import (
    ProgramItemRef,
    is_disputed_match,
    topic_key,
)
from calendar_pedagoga.matching import (
    ContentMatch,
    MatchStatus,
    bound_program_item,
    match_utp_to_program,
)
from calendar_pedagoga.parsing import (
    Topic,
    UtpTableCandidate,
    collect_utp_table_candidates,
)
from calendar_pedagoga.program_parsing import (
    ProgramContentItem,
    ProgramData,
    convert_legacy_doc,
)


STRUCTURE_CONFIRM_BUTTON = "Подтвердить структуру"
STRUCTURE_THEORY_CONTENT_LABEL = "Содержание теории"
STRUCTURE_PRACTICE_CONTENT_LABEL = "Содержание практики"
STRUCTURE_ORIGIN_MANUAL_LABEL = "Ввести вручную"
STRUCTURE_ORIGIN_EXCERPT_LABEL = "Фрагмент SOURCE"
STRUCTURE_SOURCE_ITEM_LABEL = "SOURCE-item"
STRUCTURE_EXCERPT_LABEL = "Excerpt"
SOURCE_USER = "user"
SOURCE_PROGRAM = "program"
SOURCE_EXTERNAL_UTP = "external_utp"
PROVEN_MATCH_STATUSES = frozenset(
    {
        MatchStatus.EXACT.value,
        MatchStatus.NORMALIZED.value,
        MatchStatus.TEXT_MATCH.value,
        MatchStatus.USER_CONFIRMED.value,
    }
)

DISPOSITION_MAPPED = "mapped"
DISPOSITION_EXCLUDED = "excluded"
DISPOSITION_UNRESOLVED = "unresolved"
SOURCE_DISPOSITIONS = frozenset(
    {DISPOSITION_MAPPED, DISPOSITION_EXCLUDED, DISPOSITION_UNRESOLVED}
)
TOPIC_STATUS_DRAFT_READY = "DRAFT_READY"
TOPIC_STATUS_UNRESOLVED = "UNRESOLVED"
TOPIC_STATUS_USER_CONFIRMED = "USER_CONFIRMED"
CONTENT_ORIGIN_EXCERPT = "excerpt"
CONTENT_ORIGIN_MANUAL = "manual"

EXCLUSION_HEADING = "heading"
EXCLUSION_DUPLICATE = "duplicate"
EXCLUSION_SERVICE = "service_section"
EXCLUSION_OTHER_YEAR = "other_year"
EXCLUSION_REASON_LABELS = {
    EXCLUSION_HEADING: "заголовок",
    EXCLUSION_DUPLICATE: "дубль",
    EXCLUSION_SERVICE: "служебный раздел",
    EXCLUSION_OTHER_YEAR: "не входит в выбранный год",
}
EXCLUSION_REASONS = frozenset(EXCLUSION_REASON_LABELS)

_ROW_KEYS = (
    "schedule_id",
    "number",
    "topic",
    "theory_hours",
    "practice_hours",
    "theory_content",
    "practice_content",
    "source_item_id",
    "excerpt",
    "content_origin",
    "topic_status",
    "source",
    "match_status",
)
_BROADCAST_SPLIT = re.compile(r"[;\n|]+")
_LEDGER_KEYS = (
    "item_id",
    "title",
    "number",
    "section",
    "study_year",
    "content",
    "disposition",
    "exclusion_reason",
    "mapped_topic",
)


class ProgramStructureConfirmationError(ConfirmedStudyPlanError):
    """The teacher cannot confirm the recognized structure yet."""


@dataclass(frozen=True)
class StructureConfirmation:
    """Confirmed plan plus USER_CONFIRMED provenance for matching."""

    plan: ConfirmedStudyPlan
    program_items: tuple[ProgramContentItem, ...]
    match_reviews: dict[tuple[str | None, str, str | None], dict]
    scope: str
    source_ledger: tuple["SourceItemRecord", ...] = ()
    topic_statuses: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceItemRecord:
    """One SOURCE-item and its confirmation fate."""

    item_id: str
    title: str
    number: str | None = None
    section: str | None = None
    study_year: int | None = None
    content: str = ""
    disposition: str = DISPOSITION_UNRESOLVED
    exclusion_reason: str | None = None
    mapped_topic: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "number": self.number or "",
            "section": self.section or "",
            "study_year": "" if self.study_year is None else str(self.study_year),
            "content": self.content,
            "disposition": self.disposition,
            "exclusion_reason": self.exclusion_reason or "",
            "mapped_topic": self.mapped_topic or "",
        }


def source_item_id(index: int) -> str:
    return f"src-{index:04d}"


def schedule_topic_id(index: int) -> str:
    return f"sch-{index:04d}"


def split_mapped_topics(value: object) -> tuple[str, ...]:
    """Split a broadcast mapping string. More than one title is forbidden."""

    return tuple(
        token.strip()
        for token in _BROADCAST_SPLIT.split(_cell(value))
        if token.strip()
    )


@dataclass(frozen=True)
class TopicSourceMapping:
    """Per-topic SOURCE binding. Never shares a full block across topics."""

    schedule_id: str
    topic: str
    source_item_id: str | None
    excerpt: str
    content: str
    origin: str
    status: str


def _reject_broadcast_mapping(value: object, *, label: str) -> str:
    token = _cell(value)
    parts = split_mapped_topics(token)
    if len(parts) > 1:
        raise ProgramStructureConfirmationError(
            f"Broadcast mapping запрещён для {label}: "
            "каждая тема УТП должна иметь отдельную привязку."
        )
    return token


def _is_full_source_block(excerpt: str, source_content: str) -> bool:
    left = excerpt.strip()
    right = (source_content or "").strip()
    return bool(left) and left == right


_ROMAN_STUDY_YEARS = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
}
_YEAR_TOKEN = (
    r"(?P<roman>viii|vii|vi|iv|iii|ii|i|v)|"
    r"(?P<arabic>[1-8])|"
    r"(?P<word>перв\w*|втор\w*|трет\w*|четв\w*)"
)
_YEAR_TAIL = r"(?:\s*[-–—]?\s*(?:й|ый|ой|ий|го|ого))?\s+год(?:а)?(?:\s+обучен\w*)?"
_YEAR_BOUNDARY_RE = re.compile(
    rf"(?ix)^\s*(?:содержание\s+программы\s+)?(?:{_YEAR_TOKEN}){_YEAR_TAIL}\s*[.:]?\s*$"
)
_YEAR_MENTION_RE = re.compile(rf"(?ix)(?<!\w)(?:{_YEAR_TOKEN}){_YEAR_TAIL}")
_TOPIC_NUM_RE = re.compile(r"(?i)тема\s*№\s*(\d+)\b")
_TOPIC_RANGE_RE = re.compile(r"(?i)тема\s*№\s*\d+\s*[-–—]\s*\d+")
_SERVICE_HEADING_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"учебно[- ]тематическ\w*\s+план"
    r"|список\s+литератур"
    r"|^литература\b"
    r"|описани[ея]\s+условий\s+реализации"
    r"|условий?\s+реализации\s+программ"
    r"|методы?\s+(?:обучен|воспитан)"
    r"|диагностик"
    r"|механизм\s+оценки"
    r"|(?:ожидаемые|планируемые|получаемые)\s+результат"
    r"|в\s+результате\s+прохождени"
    r"|по\s+окончани[юя]"
    r"|должны?\s+(?:знать|уметь|иметь\s+представление)"
    r"|дидактическ\w*\s+материал"
    r"|оборудование\s+и\s+материалы"
    r"|форма\s+учета\s+знаний"
    r")"
)


def catalog_source_items(
    items: Sequence[ProgramContentItem],
) -> tuple[SourceItemRecord, ...]:
    """Stable ledger of every parsed SOURCE-item. Nothing is dropped."""

    years = _structural_item_years(items)
    return tuple(
        SourceItemRecord(
            item_id=source_item_id(index),
            title=item.title,
            number=item.number,
            section=item.parent_section,
            study_year=years[index],
            content=item.content or "",
        )
        for index, item in enumerate(items)
    )


def _year_from_match(match: re.Match[str] | None) -> int | None:
    if match is None:
        return None
    roman = match.group("roman")
    if roman:
        return _ROMAN_STUDY_YEARS.get(roman.casefold())
    arabic = match.group("arabic")
    if arabic:
        value = int(arabic)
        return value if 1 <= value <= 8 else None
    word = match.group("word")
    if not word:
        return None
    folded = word.casefold()
    for stem, number in (("перв", 1), ("втор", 2), ("трет", 3), ("четв", 4)):
        if folded.startswith(stem):
            return number
    return None


def structural_year_heading(text: str | None) -> int | None:
    """Year only when the heading itself is a year boundary."""

    return _year_from_match(_YEAR_BOUNDARY_RE.match(_cell(text)))


def mentioned_study_year(text: str | None) -> int | None:
    """Year mentioned next to «год», without using the selected year."""

    return _year_from_match(_YEAR_MENTION_RE.search(_cell(text)))


def is_proven_service_heading(text: str | None) -> bool:
    token = _cell(text)
    if not token:
        return False
    return bool(_SERVICE_HEADING_RE.search(token))


def _structural_item_years(
    items: Sequence[ProgramContentItem],
) -> tuple[int | None, ...]:
    years: list[int | None] = []
    current_year: int | None = None
    in_service = False
    for item in items:
        title = item.title or ""
        section = item.parent_section or ""
        title_boundary = structural_year_heading(title)
        service_here = is_proven_service_heading(title) or is_proven_service_heading(
            section
        )
        if title_boundary is not None:
            current_year = title_boundary
            in_service = False
            years.append(title_boundary)
            continue
        if service_here:
            in_service = True
            current_year = None
            years.append(mentioned_study_year(title) or mentioned_study_year(section))
            continue
        if in_service:
            years.append(mentioned_study_year(title) or mentioned_study_year(section))
            continue
        years.append(
            mentioned_study_year(title)
            or structural_year_heading(section)
            or mentioned_study_year(section)
            or current_year
        )
    return tuple(years)


def _topic_ordinal(number: object) -> int | None:
    token = _cell(number)
    if not re.fullmatch(r"[1-9]\d*", token):
        return None
    value = int(token)
    return value if 1 <= value <= 99 else None


def topic_number_blocks(text: str | None) -> tuple[tuple[int, str], ...]:
    """Exact continuous `Тема №N` excerpts. Ranges and empty tails are skipped."""

    source = text or ""
    if not source or _TOPIC_RANGE_RE.search(source):
        return ()
    matches = list(_TOPIC_NUM_RE.finditer(source))
    if not matches:
        return ()
    blocks: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        excerpt = source[start:end].rstrip()
        if not excerpt or excerpt == match.group(0):
            continue
        blocks.append((int(match.group(1)), excerpt))
    return tuple(blocks)


def _title_topic_number(title: str | None) -> int | None:
    match = _TOPIC_NUM_RE.match(_cell(title))
    if match is None or _TOPIC_RANGE_RE.search(_cell(title)):
        return None
    return int(match.group(1))


def structure_row_is_unresolved(row: Mapping[str, object]) -> bool:
    """True when a schedule topic still has no own SOURCE or manual content."""

    if _row_is_proven({key: _cell(row.get(key)) for key in _ROW_KEYS}):
        return False
    if _cell(row.get("source_item_id")) and _cell(row.get("excerpt")):
        return False
    origin = _cell(row.get("content_origin")).casefold()
    manual = _content_for_row({key: _cell(row.get(key)) for key in _ROW_KEYS})
    if origin == CONTENT_ORIGIN_MANUAL and manual:
        return False
    if manual and not _cell(row.get("excerpt")):
        return False
    return True


def _item_identity(item: ProgramContentItem) -> tuple:
    return (
        item.number,
        item.title,
        item.parent_section,
        item.content or "",
        item.study_year,
    )


def exclusion_reason_label(reason: str | None) -> str:
    token = _normalize_exclusion_reason(reason)
    if token is None:
        return _cell(reason)
    return EXCLUSION_REASON_LABELS[token]


def _normalize_disposition(value: object) -> str:
    token = _cell(value).casefold()
    aliases = {
        "mapped": DISPOSITION_MAPPED,
        "привязан": DISPOSITION_MAPPED,
        "excluded": DISPOSITION_EXCLUDED,
        "исключён": DISPOSITION_EXCLUDED,
        "исключен": DISPOSITION_EXCLUDED,
        "unresolved": DISPOSITION_UNRESOLVED,
        "необработан": DISPOSITION_UNRESOLVED,
    }
    return aliases.get(token, DISPOSITION_UNRESOLVED)


def _normalize_exclusion_reason(value: object) -> str | None:
    token = _cell(value)
    if not token:
        return None
    folded = token.casefold()
    for code, label in EXCLUSION_REASON_LABELS.items():
        if folded == code or folded == label.casefold():
            return code
    return None


def source_disposition_counts(
    rows: Sequence[Mapping[str, object] | SourceItemRecord],
) -> dict[str, int]:
    mapped = excluded = unresolved = 0
    for row in rows:
        if isinstance(row, SourceItemRecord):
            disposition = row.disposition
        else:
            disposition = _normalize_disposition(row.get("disposition"))
        if disposition == DISPOSITION_MAPPED:
            mapped += 1
        elif disposition == DISPOSITION_EXCLUDED:
            excluded += 1
        else:
            unresolved += 1
    return {
        DISPOSITION_MAPPED: mapped,
        DISPOSITION_EXCLUDED: excluded,
        DISPOSITION_UNRESOLVED: unresolved,
        "total": mapped + excluded + unresolved,
    }


def classify_source_structure(
    program: ProgramData | None,
    *,
    matches: Sequence[ContentMatch] = (),
    study_year: int | None = None,
    topics: Sequence[Topic] = (),
) -> tuple[tuple[SourceItemRecord, ...], dict[int, tuple[str, str]]]:
    """Year-scope SOURCE, exclude proven service, map unique `Тема №N` one-to-one."""

    items = program.content_items if program is not None else ()
    catalog = catalog_source_items(items)
    bound_topics: dict[tuple, str] = {}
    for match in matches:
        item = bound_program_item(match)
        if item is None:
            continue
        identity = _item_identity(item)
        if identity not in bound_topics:
            bound_topics[identity] = match.utp_position.title
    used_identities: set[tuple] = set()
    classified: list[SourceItemRecord] = []
    for record, item in zip(catalog, items, strict=True):
        identity = _item_identity(item)
        topic = bound_topics.get(identity)
        if topic is not None and identity not in used_identities:
            used_identities.add(identity)
            classified.append(
                replace(
                    record,
                    disposition=DISPOSITION_MAPPED,
                    exclusion_reason=None,
                    mapped_topic=topic,
                )
            )
            continue
        year = record.study_year
        title_boundary = structural_year_heading(record.title)
        service_here = is_proven_service_heading(
            record.title
        ) or is_proven_service_heading(record.section)
        if (
            year is not None
            and study_year is not None
            and year != study_year
        ):
            classified.append(
                replace(
                    record,
                    disposition=DISPOSITION_EXCLUDED,
                    exclusion_reason=EXCLUSION_OTHER_YEAR,
                    mapped_topic=None,
                )
            )
            continue
        mentioned = mentioned_study_year(record.title) or mentioned_study_year(
            record.section
        )
        if service_here:
            reason = (
                EXCLUSION_OTHER_YEAR
                if (
                    mentioned is not None
                    and study_year is not None
                    and mentioned != study_year
                )
                else EXCLUSION_SERVICE
            )
            classified.append(
                replace(
                    record,
                    disposition=DISPOSITION_EXCLUDED,
                    exclusion_reason=reason,
                    mapped_topic=None,
                )
            )
            continue
        blocks = topic_number_blocks(record.content)
        title_number = _title_topic_number(record.title)
        if (
            title_boundary is not None
            and not (record.content or "").strip()
            and not blocks
            and title_number is None
        ):
            classified.append(
                replace(
                    record,
                    disposition=DISPOSITION_EXCLUDED,
                    exclusion_reason=EXCLUSION_HEADING,
                    mapped_topic=None,
                )
            )
            continue
        classified.append(record)

    ordinal_topics: dict[int, str] = {}
    ordinal_counts: dict[int, int] = {}
    for topic in topics:
        ordinal = _topic_ordinal(topic.number)
        if ordinal is None:
            continue
        ordinal_counts[ordinal] = ordinal_counts.get(ordinal, 0) + 1
        ordinal_topics.setdefault(ordinal, topic.title)
    unique_topic_ordinals = {
        ordinal: title
        for ordinal, title in ordinal_topics.items()
        if ordinal_counts.get(ordinal) == 1
    }

    candidates: dict[int, list[tuple[str, str]]] = {}
    for record in classified:
        if record.disposition == DISPOSITION_EXCLUDED:
            continue
        if study_year is not None and record.study_year not in (None, study_year):
            continue
        seen_in_item: list[tuple[int, str]] = list(topic_number_blocks(record.content))
        title_number = _title_topic_number(record.title)
        if title_number is not None and not any(
            number == title_number for number, _excerpt in seen_in_item
        ):
            excerpt = (record.content or "").strip()
            if excerpt:
                seen_in_item.append((title_number, excerpt))
        for number, excerpt in seen_in_item:
            candidates.setdefault(number, []).append((record.item_id, excerpt))

    excerpts: dict[int, tuple[str, str]] = {}
    for number, found in candidates.items():
        if number not in unique_topic_ordinals:
            continue
        unique_pairs = {(item_id, excerpt) for item_id, excerpt in found}
        if len(found) != 1 or len(unique_pairs) != 1:
            continue
        item_id, excerpt = found[0]
        record = next(item for item in classified if item.item_id == item_id)
        if _is_full_source_block(excerpt, record.content) and any(
            other != number
            and other in unique_topic_ordinals
            and any(pair[0] == item_id for pair in candidates.get(other, ()))
            for other in candidates
        ):
            continue
        excerpts[number] = (item_id, excerpt)

    used_items = {item_id for item_id, _excerpt in excerpts.values()}
    mapped_by_item: dict[str, str] = {}
    for number, (item_id, _excerpt) in excerpts.items():
        title = unique_topic_ordinals[number]
        previous = mapped_by_item.get(item_id)
        if previous is None:
            mapped_by_item[item_id] = title
        elif previous != title:
            mapped_by_item[item_id] = ""

    finalized: list[SourceItemRecord] = []
    for record in classified:
        if record.disposition == DISPOSITION_EXCLUDED:
            finalized.append(record)
            continue
        if record.item_id in used_items:
            finalized.append(
                replace(
                    record,
                    disposition=DISPOSITION_MAPPED,
                    exclusion_reason=None,
                    mapped_topic=mapped_by_item.get(record.item_id) or None,
                )
            )
            continue
        if record.disposition == DISPOSITION_MAPPED:
            finalized.append(record)
            continue
        finalized.append(
            replace(
                record,
                disposition=DISPOSITION_UNRESOLVED,
                exclusion_reason=None,
                mapped_topic=None,
            )
        )
    return tuple(finalized), excerpts


def draft_source_ledger(
    program: ProgramData | None,
    *,
    matches: Sequence[ContentMatch] = (),
    study_year: int | None = None,
    topics: Sequence[Topic] = (),
) -> tuple[dict[str, str], ...]:
    """One ledger row per SOURCE-item after year/service/`Тема №N` classification."""

    records, _excerpts = classify_source_structure(
        program,
        matches=matches,
        study_year=study_year,
        topics=topics,
    )
    return tuple(record.as_dict() for record in records)


def merge_source_ledger(
    catalog: Sequence[SourceItemRecord],
    edited: Sequence[Mapping[str, object]] | None = None,
) -> tuple[dict[str, str], ...]:
    """Keep every catalog SOURCE-item. Overlay edits by item_id only."""

    overlays: dict[str, Mapping[str, object]] = {}
    for row in edited or ():
        item_id = _cell(row.get("item_id"))
        if item_id:
            overlays[item_id] = row
    merged: list[dict[str, str]] = []
    for record in catalog:
        overlay = overlays.get(record.item_id)
        if overlay is None:
            merged.append(record.as_dict())
            continue
        merged.append(
            {
                **record.as_dict(),
                "disposition": _normalize_disposition(
                    overlay.get("disposition") or record.disposition
                ),
                "exclusion_reason": _cell(overlay.get("exclusion_reason"))
                or (record.exclusion_reason or ""),
                "mapped_topic": _cell(overlay.get("mapped_topic"))
                or (record.mapped_topic or ""),
            }
        )
    return tuple(merged)


def resolve_source_ledger(
    catalog: Sequence[SourceItemRecord],
    edited: Sequence[Mapping[str, object]] | None = None,
) -> tuple[SourceItemRecord, ...]:
    """Apply mapping/exclusion without dropping catalog items."""

    merged = merge_source_ledger(catalog, edited)
    resolved: list[SourceItemRecord] = []
    for record, row in zip(catalog, merged, strict=True):
        disposition = _normalize_disposition(row.get("disposition"))
        reason = _normalize_exclusion_reason(row.get("exclusion_reason"))
        mapped_topic = _reject_broadcast_mapping(
            row.get("mapped_topic"),
            label=f"SOURCE-item «{record.title}»",
        )
        if disposition == DISPOSITION_EXCLUDED:
            if reason not in EXCLUSION_REASONS:
                raise ProgramStructureConfirmationError(
                    f"Укажите причину исключения для SOURCE-item «{record.title}»: "
                    "заголовок / дубль / служебный раздел / не входит в выбранный год."
                )
            resolved.append(
                replace(
                    record,
                    disposition=DISPOSITION_EXCLUDED,
                    exclusion_reason=reason,
                    mapped_topic=None,
                )
            )
            continue
        if disposition == DISPOSITION_MAPPED:
            topic = mapped_topic or record.title
            if not topic:
                raise ProgramStructureConfirmationError(
                    f"Привяжите SOURCE-item «{record.title}» к подтверждённой теме."
                )
            resolved.append(
                replace(
                    record,
                    disposition=DISPOSITION_MAPPED,
                    exclusion_reason=None,
                    mapped_topic=topic,
                )
            )
            continue
        resolved.append(
            replace(
                record,
                disposition=DISPOSITION_UNRESOLVED,
                exclusion_reason=None,
                mapped_topic=None,
            )
        )
    return tuple(resolved)


def unresolved_source_items(
    records: Sequence[SourceItemRecord],
) -> tuple[SourceItemRecord, ...]:
    return tuple(
        record
        for record in records
        if record.disposition == DISPOSITION_UNRESOLVED
    )


def _require_source_inventory(
    catalog: Sequence[SourceItemRecord],
    records: Sequence[SourceItemRecord],
) -> None:
    catalog_ids = [record.item_id for record in catalog]
    record_ids = [record.item_id for record in records]
    if catalog_ids != record_ids:
        raise ProgramStructureConfirmationError(
            "SOURCE-item нельзя потерять: состав журнала должен совпадать "
            "с распознанными элементами программы."
        )


def _require_resolved_source_items(records: Sequence[SourceItemRecord]) -> None:
    leftover = unresolved_source_items(records)
    if leftover:
        raise ProgramStructureConfirmationError(
            "Подтверждение запрещено, пока есть необработанные SOURCE-items "
            f"({len(leftover)})."
        )


def _hours_positive(value: object) -> bool:
    token = _cell(value).replace(",", ".")
    if not token:
        return False
    try:
        return hour_value_from_input(token, field_name="Часы") > 0
    except ConfirmedStudyPlanError:
        return False


def _row_has_required_slot_content(
    row: Mapping[str, str],
    mapping: TopicSourceMapping | None,
) -> bool:
    """SOURCE mapping is optional. Title + hours are enough for title-based frames."""

    del mapping
    if not _cell(row.get("topic")):
        return False
    return _hours_positive(row.get("theory_hours")) or _hours_positive(
        row.get("practice_hours")
    )


def unresolved_schedule_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    return tuple(
        {key: _cell(row.get(key)) for key in _ROW_KEYS}
        for row in rows
        if structure_row_is_unresolved(row)
    )


_IDENTITY_KEYS = (
    "schedule_id",
    "number",
    "topic",
    "theory_hours",
    "practice_hours",
    "source",
    "match_status",
)
_CONTENT_EDIT_KEYS = (
    "theory_content",
    "practice_content",
    "source_item_id",
    "excerpt",
    "content_origin",
)


def overlay_unresolved_topic_edits(
    draft: Sequence[Mapping[str, object]],
    edited: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    """Keep UTP identity/hours from draft; accept only per-topic content edits."""

    overlays = {
        _cell(row.get("schedule_id")): row
        for row in edited
        if _cell(row.get("schedule_id"))
    }
    merged: list[dict[str, str]] = []
    for original in draft:
        row = {key: _cell(original.get(key)) for key in _ROW_KEYS}
        overlay = overlays.get(row["schedule_id"])
        if overlay is None:
            merged.append(row)
            continue
        for key in _CONTENT_EDIT_KEYS:
            row[key] = _cell(overlay.get(key))
        for key in _IDENTITY_KEYS:
            row[key] = _cell(original.get(key))
        merged.append(row)
    return tuple(merged)


def _require_topics_resolved(
    rows: Sequence[Mapping[str, str]],
    mappings: Sequence[TopicSourceMapping | None],
) -> None:
    leftover: list[str] = []
    for row, mapping in zip(rows, mappings, strict=True):
        if _row_is_proven(row) or _row_has_required_slot_content(row, mapping):
            continue
        leftover.append(_cell(row.get("topic")) or "без названия")
    if leftover:
        raise ProgramStructureConfirmationError(
            "Подтверждение запрещено: укажите название и часы для тем "
            f"({len(leftover)})."
        )


def structure_confirmation_scope(
    *,
    program_name: str | None,
    program_digest: str | None,
    utp_name: str | None,
    utp_digest: str | None,
    study_year: int | None,
    rows: Sequence[Mapping[str, object]] | None = None,
    ledger: Sequence[Mapping[str, object]] | None = None,
) -> str:
    """Fingerprint of the documents, year, table and SOURCE dispositions."""

    payload = [
        program_name,
        program_digest,
        utp_name,
        utp_digest,
        study_year,
        [dict(row) for row in (rows or ())],
        [dict(row) for row in (ledger or ())],
    ]
    return sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def file_digest(payload: bytes | None) -> str | None:
    if payload is None:
        return None
    return sha256(payload).hexdigest()


def embedded_utp_candidates(
    program_bytes: bytes,
    filename: str = "",
) -> tuple[UtpTableCandidate, ...]:
    data = program_bytes
    if filename.lower().endswith(".doc"):
        data = convert_legacy_doc(program_bytes)
    return collect_utp_table_candidates(data)


def select_embedded_utp(
    embedded: Sequence[UtpTableCandidate],
    study_year: int | None,
) -> UtpTableCandidate | None:
    """Pick exactly one embedded UTP for the requested year."""

    if not embedded:
        return None
    if study_year is not None:
        matched = [item for item in embedded if item.study_year == study_year]
        if len(matched) == 1:
            return matched[0]
        return None
    if len(embedded) == 1:
        return embedded[0]
    return None


def schedule_topics_from_candidate(
    candidate: UtpTableCandidate,
) -> tuple[Topic, ...]:
    """Hour-bearing UTP rows become schedule topics. Content-items do not."""

    from_topics = tuple(
        topic for topic in candidate.topics if _topic_has_hours(topic)
    )
    if from_topics:
        return from_topics
    promoted: list[Topic] = []
    for section in candidate.sections:
        hours = section.hours
        if min(hours.total, hours.theory + hours.practice) <= 0:
            continue
        promoted.append(
            Topic(
                number=section.number,
                title=section.title,
                hours=hours,
                parent_section=section.title,
                is_standalone_section=True,
            )
        )
    return tuple(promoted)


def _topic_has_hours(topic: Topic) -> bool:
    return min(topic.hours.total, topic.hours.theory + topic.hours.practice) > 0


def topic_lacks_proven_source(topic: Topic, match: ContentMatch | None) -> bool:
    """Hour-bearing topic without a proven SOURCE binding.

    Disputed matches that already have review candidates stay on the
    existing match-review cards and do not open this table.
    """

    if not _topic_has_hours(topic):
        return False
    item = bound_program_item(match)
    if item is not None and (item.content or "").strip():
        return False
    if match is not None and is_disputed_match(match):
        return False
    return True


def unmatched_source_topics(
    plan: ConfirmedStudyPlan,
    matches: Sequence[ContentMatch] = (),
) -> tuple[Topic, ...]:
    by_topic = _match_by_topic(matches)
    return tuple(
        topic
        for topic in plan.topics
        if topic_lacks_proven_source(topic, by_topic.get(topic_key(topic)))
    )


def needs_structure_confirmation(
    *,
    plan: ConfirmedStudyPlan | None,
    program: ProgramData | None,
    embedded_utp_count: int = 0,
    has_external_utp: bool = False,
    study_year: int | None = None,
    matches: Sequence[ContentMatch] = (),
    has_unique_embedded_utp: bool = False,
) -> bool:
    """True only for structural errors. Missing SOURCE does not open the screen."""

    del study_year, matches, program, has_external_utp
    if plan is not None:
        return not bool(plan.topics)
    if has_unique_embedded_utp:
        return False
    if embedded_utp_count > 1:
        return True
    if embedded_utp_count == 1:
        return False
    return True


def _cell(value: object) -> str:
    if value is None:
        return ""
    token = str(value).strip()
    if token.lower() == "nan":
        return ""
    return token


def _blank_row() -> dict[str, str]:
    return {key: "" for key in _ROW_KEYS}


def normalize_structure_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        item = {key: _cell(row.get(key)) for key in _ROW_KEYS}
        if any(item.values()):
            normalized.append(item)
    return tuple(normalized)


def _is_fully_blank(row: Mapping[str, str]) -> bool:
    return not any(_cell(row.get(key)) for key in _ROW_KEYS)


def _match_by_topic(
    matches: Sequence[ContentMatch],
) -> dict[tuple[str | None, str, str | None], ContentMatch]:
    return {topic_key(match.utp_position): match for match in matches}


def _row_from_topic(
    topic: Topic,
    *,
    match: ContentMatch | None,
    source: str,
    schedule_id: str = "",
) -> dict[str, str]:
    proven = match is not None and match.status.value in PROVEN_MATCH_STATUSES
    item = bound_program_item(match) if proven else None
    content = (item.content if item is not None else "").strip()
    theory_hours = topic.hours.theory
    practice_hours = topic.hours.practice
    theory_content = content if theory_hours else ""
    practice_content = content if practice_hours else ""
    if content and not theory_content and not practice_content:
        theory_content = content
    status = match.status.value if match is not None else MatchStatus.NOT_MATCHED.value
    return {
        "schedule_id": schedule_id,
        "number": topic.number or "",
        "topic": topic.title,
        "theory_hours": str(theory_hours),
        "practice_hours": str(practice_hours),
        "theory_content": theory_content,
        "practice_content": practice_content,
        "source_item_id": "",
        "excerpt": "",
        "content_origin": "",
        "topic_status": TOPIC_STATUS_DRAFT_READY,
        "source": source,
        "match_status": status,
    }


def draft_structure_rows(
    *,
    plan: ConfirmedStudyPlan | None = None,
    program: ProgramData | None = None,
    matches: Sequence[ContentMatch] = (),
    embedded: Sequence[UtpTableCandidate] = (),
    study_year: int | None = None,
) -> tuple[dict[str, str], ...]:
    """Prefill schedule topics from a plan or the selected UTP only.

    Program headings and content-items never become schedule topics.
    Unique `Тема №N` excerpts of the selected year are attached one-to-one.
    Without a plan or a single year-matching UTP the teacher creates
    manual schedule rows; the content ledger stays separate.
    """

    by_topic = _match_by_topic(matches)
    topics: tuple[Topic, ...]
    source: str
    year = study_year
    if plan is not None:
        topics = plan.topics
        year = plan.study_year if year is None else year
        source = (
            SOURCE_EXTERNAL_UTP if plan.source == "external_utp" else SOURCE_USER
        )
    else:
        selected = select_embedded_utp(embedded, study_year)
        if selected is None:
            return (_blank_row(),)
        topics = schedule_topics_from_candidate(selected)
        if not topics:
            return (_blank_row(),)
        source = SOURCE_PROGRAM
    rows = tuple(
        _row_from_topic(
            topic,
            match=by_topic.get(topic_key(topic)),
            source=source,
            schedule_id=schedule_topic_id(index),
        )
        for index, topic in enumerate(topics)
    )
    _records, excerpts = classify_source_structure(
        program,
        matches=matches,
        study_year=year,
        topics=topics,
    )
    filled: list[dict[str, str]] = []
    for row in rows:
        if not structure_row_is_unresolved(row):
            filled.append(row)
            continue
        ordinal = _topic_ordinal(row.get("number"))
        binding = excerpts.get(ordinal) if ordinal is not None else None
        if binding is None:
            filled.append({**row, "topic_status": TOPIC_STATUS_UNRESOLVED})
            continue
        source_item_id, excerpt = binding
        filled.append(
            {
                **row,
                "source_item_id": source_item_id,
                "excerpt": excerpt,
                "content_origin": CONTENT_ORIGIN_EXCERPT,
                "theory_content": "",
                "practice_content": "",
                "topic_status": TOPIC_STATUS_DRAFT_READY,
            }
        )
    return tuple(filled)


def matches_for_draft(
    plan: ConfirmedStudyPlan | None,
    program: ProgramData | None,
    embedded: Sequence[UtpTableCandidate] = (),
    study_year: int | None = None,
) -> tuple[ContentMatch, ...]:
    topics: tuple[Topic, ...]
    year = study_year
    if plan is not None:
        topics = plan.topics
        year = plan.study_year if year is None else year
    else:
        selected = select_embedded_utp(embedded, study_year)
        if selected is None:
            return ()
        topics = schedule_topics_from_candidate(selected)
        if not topics:
            return ()
    if program is None or not program.content_items:
        return ()
    return match_utp_to_program(
        topics,
        program.content_items,
        study_year=year,
    )


def _content_for_row(row: Mapping[str, str]) -> str:
    theory = _cell(row.get("theory_content"))
    practice = _cell(row.get("practice_content"))
    if theory and practice and theory != practice:
        return f"{theory}\n{practice}"
    return theory or practice


def _validate_structure_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[dict[str, str], ...]:
    kept = normalize_structure_rows(rows)
    if not kept:
        raise ProgramStructureConfirmationError(
            "Добавьте хотя бы одну тему учебного плана."
        )
    validated: list[dict[str, str]] = []
    for index, row in enumerate(kept, start=1):
        if _is_fully_blank(row):
            continue
        topic = _cell(row.get("topic"))
        theory = _cell(row.get("theory_hours"))
        practice = _cell(row.get("practice_hours"))
        if not topic:
            raise ProgramStructureConfirmationError(
                f"Укажите тему в строке {index} учебного плана."
            )
        if not theory or not practice:
            raise ProgramStructureConfirmationError(
                f"Укажите часы теории и практики в строке {index}."
            )
        theory_value = hour_value_from_input(theory, field_name="Теория")
        practice_value = hour_value_from_input(practice, field_name="Практика")
        if theory_value + practice_value <= 0:
            raise ProgramStructureConfirmationError(
                f"В строке {index} сумма часов теории и практики должна быть больше нуля."
            )
        validated.append(
            {
                **row,
                "topic": topic,
                "theory_hours": str(theory_value),
                "practice_hours": str(practice_value),
            }
        )
    if not validated:
        raise ProgramStructureConfirmationError(
            "Добавьте хотя бы одну тему учебного плана."
        )
    return tuple(validated)


def _manual_rows_from_structure(
    rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    return [
        {
            "topic": row["topic"],
            "total": str(
                hour_value_from_input(row["theory_hours"], field_name="Теория")
                + hour_value_from_input(row["practice_hours"], field_name="Практика")
            ),
            "theory": row["theory_hours"],
            "practice": row["practice_hours"],
        }
        for row in rows
    ]


def _plan_matches_rows(
    plan: ConfirmedStudyPlan,
    rows: Sequence[Mapping[str, str]],
    *,
    study_weeks: int,
    hours_per_week: object,
) -> bool:
    weekly = hour_value_from_input(
        hours_per_week,
        field_name="Количество часов в неделю",
    )
    if plan.study_weeks != study_weeks or plan.hours_per_week != weekly:
        return False
    if len(plan.topics) != len(rows):
        return False
    for topic, row in zip(plan.topics, rows, strict=True):
        theory = hour_value_from_input(row["theory_hours"], field_name="Теория")
        practice = hour_value_from_input(row["practice_hours"], field_name="Практика")
        if topic.title != row["topic"]:
            return False
        if topic.hours.theory != theory or topic.hours.practice != practice:
            return False
    return True


def _row_is_proven(row: Mapping[str, str]) -> bool:
    return (row.get("match_status") or "") in PROVEN_MATCH_STATUSES


def _catalog_by_id(
    records: Sequence[SourceItemRecord],
) -> dict[str, SourceItemRecord]:
    return {record.item_id: record for record in records}


def resolve_topic_source_mapping(
    row: Mapping[str, str],
    catalog: Mapping[str, SourceItemRecord],
) -> TopicSourceMapping | None:
    """Return this topic's own mapping, or None if it stays unresolved."""

    schedule_id = _cell(row.get("schedule_id"))
    topic = _cell(row.get("topic"))
    source_item_id = _cell(row.get("source_item_id"))
    excerpt = _cell(row.get("excerpt"))
    origin = _cell(row.get("content_origin")).casefold()
    manual = _content_for_row(row)
    _reject_broadcast_mapping(row.get("mapped_topic"), label=f"темы «{topic}»")
    if origin == CONTENT_ORIGIN_MANUAL or (manual and not excerpt and not source_item_id):
        if not manual:
            return None
        return TopicSourceMapping(
            schedule_id=schedule_id,
            topic=topic,
            source_item_id=None,
            excerpt="",
            content=manual,
            origin=CONTENT_ORIGIN_MANUAL,
            status=TOPIC_STATUS_USER_CONFIRMED,
        )
    if source_item_id or excerpt or origin == CONTENT_ORIGIN_EXCERPT:
        if not source_item_id or not excerpt:
            return None
        record = catalog.get(source_item_id)
        if record is None:
            raise ProgramStructureConfirmationError(
                f"Тема «{topic}» ссылается на неизвестный SOURCE-item {source_item_id}."
            )
        if excerpt not in (record.content or ""):
            raise ProgramStructureConfirmationError(
                f"Excerpt темы «{topic}» должен быть подстрокой SOURCE-item "
                f"«{record.title}»."
            )
        return TopicSourceMapping(
            schedule_id=schedule_id,
            topic=topic,
            source_item_id=source_item_id,
            excerpt=excerpt,
            content=excerpt,
            origin=CONTENT_ORIGIN_EXCERPT,
            status=TOPIC_STATUS_USER_CONFIRMED,
        )
    return None


def _require_distinct_excerpts(
    mappings: Sequence[TopicSourceMapping | None],
    catalog: Mapping[str, SourceItemRecord],
) -> None:
    seen_full: dict[tuple[str, str], str] = {}
    seen_excerpt: dict[tuple[str, str], str] = {}
    for mapping in mappings:
        if mapping is None or mapping.origin != CONTENT_ORIGIN_EXCERPT:
            continue
        if not mapping.source_item_id:
            continue
        record = catalog[mapping.source_item_id]
        if _is_full_source_block(mapping.excerpt, record.content):
            full_key = (mapping.source_item_id, mapping.excerpt.strip())
            previous = seen_full.get(full_key)
            if previous is not None and previous != mapping.topic:
                raise ProgramStructureConfirmationError(
                    "Одинаковый full-block SOURCE нельзя назначить "
                    "несвязанным темам."
                )
            seen_full[full_key] = mapping.topic
        excerpt_key = (mapping.source_item_id, mapping.excerpt)
        if mapping.topic != seen_excerpt.get(excerpt_key, mapping.topic):
            raise ProgramStructureConfirmationError(
                "Один и тот же excerpt нельзя назначить несвязанным темам; "
                "нужны отдельные явно подтверждённые фрагменты."
            )
        seen_excerpt[excerpt_key] = mapping.topic


def _apply_topic_usage_to_ledger(
    records: Sequence[SourceItemRecord],
    mappings: Sequence[TopicSourceMapping | None],
) -> tuple[SourceItemRecord, ...]:
    used = {
        mapping.source_item_id
        for mapping in mappings
        if mapping is not None and mapping.source_item_id
    }
    updated: list[SourceItemRecord] = []
    for record in records:
        if record.disposition == DISPOSITION_EXCLUDED:
            if record.item_id in used:
                raise ProgramStructureConfirmationError(
                    f"SOURCE-item «{record.title}» нельзя одновременно "
                    "исключить и привязать к теме."
                )
            updated.append(replace(record, mapped_topic=None))
            continue
        if record.item_id in used:
            updated.append(
                replace(
                    record,
                    disposition=DISPOSITION_MAPPED,
                    exclusion_reason=None,
                    mapped_topic=None,
                )
            )
            continue
        updated.append(
            replace(
                record,
                disposition=DISPOSITION_UNRESOLVED,
                exclusion_reason=None,
                mapped_topic=None,
            )
        )
    return tuple(updated)


def _item_from_record(
    record: SourceItemRecord,
    source_items: Sequence[ProgramContentItem],
) -> ProgramContentItem | None:
    try:
        index = int(record.item_id.split("-", 1)[1])
    except (IndexError, ValueError):
        index = -1
    if 0 <= index < len(source_items):
        item = source_items[index]
        if item.title == record.title:
            return item
    for item in source_items:
        if (
            item.title == record.title
            and (item.content or "") == record.content
            and item.parent_section == record.section
        ):
            return item
    return None


def _require_utp_schedule_preserved(
    candidate: UtpTableCandidate,
    rows: Sequence[Mapping[str, str]],
) -> None:
    expected = schedule_topics_from_candidate(candidate)
    if not expected:
        raise ProgramStructureConfirmationError(
            "В выбранном УТП нет строк с часами."
        )
    if len(rows) != len(expected):
        raise ProgramStructureConfirmationError(
            "Все hour-bearing строки выбранного УТП должны быть сохранены."
        )
    for topic, row in zip(expected, rows, strict=True):
        if _cell(row.get("topic")) != topic.title:
            raise ProgramStructureConfirmationError(
                f"Тема УТП «{topic.title}» отсутствует в расписании."
            )
        theory = hour_value_from_input(row["theory_hours"], field_name="Теория")
        practice = hour_value_from_input(row["practice_hours"], field_name="Практика")
        if theory != topic.hours.theory or practice != topic.hours.practice:
            raise ProgramStructureConfirmationError(
                f"Часы темы «{topic.title}» выбранного УТП нельзя терять."
            )


def confirm_program_structure(
    *,
    rows: Sequence[Mapping[str, object]],
    study_year: int,
    study_weeks: int,
    hours_per_week: object,
    scope: str,
    existing_plan: ConfirmedStudyPlan | None = None,
    source_items: Sequence[ProgramContentItem] | None = None,
    ledger: Sequence[Mapping[str, object]] | None = None,
    selected_utp: UtpTableCandidate | None = None,
    embedded: Sequence[UtpTableCandidate] = (),
) -> StructureConfirmation:
    """Confirm schedule topics. SOURCE mapping is optional.

    Schedule topics come only from the selected UTP or explicit manual
    rows. Topics without SOURCE keep their UTP title and hours and get
    USER_CONFIRMED for title-based SentenceFrame.
    """

    if selected_utp is None:
        selected_utp = select_embedded_utp(embedded, study_year)
    if embedded and selected_utp is None:
        raise ProgramStructureConfirmationError(
            "Выберите один учебно-тематический план нужного года."
        )
    if (
        selected_utp is not None
        and selected_utp.study_year is not None
        and selected_utp.study_year != study_year
    ):
        raise ProgramStructureConfirmationError(
            "Выберите учебно-тематический план нужного года обучения."
        )

    catalog = catalog_source_items(source_items or ())
    records = resolve_source_ledger(catalog, ledger)
    if catalog:
        _require_source_inventory(catalog, records)

    for row in rows:
        topic = _cell(row.get("topic")) or "без названия"
        _reject_broadcast_mapping(
            row.get("mapped_topic"),
            label=f"темы «{topic}»",
        )

    validated = _validate_structure_rows(rows)
    if selected_utp is not None:
        _require_utp_schedule_preserved(selected_utp, validated)
    catalog_index = _catalog_by_id(records)
    mappings = tuple(
        resolve_topic_source_mapping(row, catalog_index) for row in validated
    )
    _require_distinct_excerpts(mappings, catalog_index)
    _require_topics_resolved(validated, mappings)
    records = _apply_topic_usage_to_ledger(records, mappings)
    try:
        rebuilt = confirmed_plan_from_manual_rows(
            study_year=study_year,
            rows=_manual_rows_from_structure(validated),
            study_weeks=study_weeks,
            hours_per_week=hours_per_week,
        )
    except ConfirmedStudyPlanError as error:
        raise ProgramStructureConfirmationError(str(error)) from error
    plan = (
        existing_plan
        if existing_plan is not None
        and _plan_matches_rows(
            existing_plan,
            validated,
            study_weeks=study_weeks,
            hours_per_week=hours_per_week,
        )
        else rebuilt
    )

    items: list[ProgramContentItem] = []
    reviews: dict[tuple[str | None, str, str | None], dict] = {}
    statuses: list[str] = []
    originals = source_items or ()
    for topic, row, mapping in zip(plan.topics, validated, mappings, strict=True):
        if _row_is_proven(row):
            statuses.append(row.get("match_status") or TOPIC_STATUS_DRAFT_READY)
            continue
        if mapping is None:
            item = ProgramContentItem(
                number=topic.number,
                title=topic.title,
                content="",
                parent_section=topic.parent_section,
                study_year=study_year,
            )
            items.append(item)
            reviews[topic_key(topic)] = {
                "decision": "USER_CONFIRMED",
                "item_ref": ProgramItemRef.from_item(item).as_dict(),
            }
            statuses.append(TOPIC_STATUS_USER_CONFIRMED)
            continue
        if mapping.origin == CONTENT_ORIGIN_EXCERPT and mapping.source_item_id:
            record = catalog_index[mapping.source_item_id]
            original = _item_from_record(record, originals)
            excerpt_item = ProgramContentItem(
                number=topic.number if original is None else original.number,
                title=topic.title,
                content=mapping.content,
                parent_section=topic.parent_section,
                study_year=study_year,
            )
            items.append(excerpt_item)
            reviews[topic_key(topic)] = {
                "decision": "USER_CONFIRMED",
                "item_ref": ProgramItemRef.from_item(excerpt_item).as_dict(),
            }
            statuses.append(TOPIC_STATUS_USER_CONFIRMED)
            continue
        item = ProgramContentItem(
            number=topic.number,
            title=topic.title,
            content=mapping.content,
            parent_section=topic.parent_section,
            study_year=study_year,
        )
        items.append(item)
        reviews[topic_key(topic)] = {
            "decision": "USER_CONFIRMED",
            "item_ref": ProgramItemRef.from_item(item).as_dict(),
        }
        statuses.append(TOPIC_STATUS_USER_CONFIRMED)
    return StructureConfirmation(
        plan=plan,
        program_items=tuple(items),
        match_reviews=reviews,
        scope=scope,
        source_ledger=records,
        topic_statuses=tuple(statuses),
    )


def overlay_confirmed_program(
    program: ProgramData | None,
    items: Sequence[ProgramContentItem],
) -> ProgramData:
    """Merge confirmed SOURCE items; keep the rest of the parsed program."""

    confirmed = tuple(items)
    if program is None:
        return ProgramData(
            title=None,
            duration=None,
            student_age=None,
            goal=None,
            tasks=(),
            lesson_forms=(),
            teaching_methods=(),
            expected_results=(),
            knowledge_outcomes=(),
            skill_outcomes=(),
            content_items=confirmed,
        )
    confirmed_titles = {item.title for item in confirmed}
    kept = tuple(
        item
        for item in program.content_items
        if item.title not in confirmed_titles
    )
    return replace(program, content_items=confirmed + kept)


def confirm_embedded_utp_structure(
    *,
    program: ProgramData | None,
    embedded: Sequence[UtpTableCandidate],
    study_year: int,
    study_weeks: int,
    hours_per_week: object,
    scope: str,
    source_items: Sequence[ProgramContentItem] | None = None,
) -> StructureConfirmation:
    """Build USER_CONFIRMED plan from a uniquely selected embedded UTP."""

    selected = select_embedded_utp(embedded, study_year)
    if selected is None:
        raise ProgramStructureConfirmationError(
            "Выберите один учебно-тематический план нужного года."
        )
    topics = schedule_topics_from_candidate(selected)
    if not topics:
        raise ProgramStructureConfirmationError(
            "В выбранном учебно-тематическом плане нет тем с часами."
        )
    draft = draft_structure_rows(
        program=program,
        embedded=embedded,
        study_year=study_year,
    )
    ledger = draft_source_ledger(
        program,
        matches=matches_for_draft(None, program, embedded, study_year=study_year),
        study_year=study_year,
        topics=topics,
    )
    items = source_items
    if items is None and program is not None:
        items = program.content_items
    return confirm_program_structure(
        rows=draft,
        study_year=study_year,
        study_weeks=study_weeks,
        hours_per_week=hours_per_week,
        scope=scope,
        source_items=items or (),
        ledger=ledger,
        selected_utp=selected,
        embedded=embedded,
    )


def confirmation_is_current(
    confirmation: StructureConfirmation | None,
    scope: str,
) -> bool:
    return confirmation is not None and confirmation.scope == scope
