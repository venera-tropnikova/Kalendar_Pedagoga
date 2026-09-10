"""Детерминированное сопоставление позиций УТП и содержания программы."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from calendar_pedagoga.parsing import Topic
from calendar_pedagoga.program_parsing import ProgramContentItem


class MatchStatus(StrEnum):
    EXACT = "EXACT"
    NORMALIZED = "NORMALIZED"
    NUMBER_MATCH = "NUMBER_MATCH"
    TEXT_MATCH = "TEXT_MATCH"
    UNCONFIRMED = "UNCONFIRMED"
    USER_CONFIRMED = "USER_CONFIRMED"
    NOT_MATCHED = "NOT_MATCHED"


_GENERIC_TITLE_TOKENS = frozenset(
    {
        "занятие",
        "занятия",
        "тема",
        "темы",
        "основы",
        "раздел",
        "программа",
        "обучение",
        "курс",
    }
)
_FUNCTION_TOKENS = frozenset(
    {
        "с",
        "со",
        "без",
        "в",
        "во",
        "на",
        "по",
        "от",
        "до",
        "из",
        "к",
        "ко",
        "у",
        "за",
        "для",
        "при",
        "про",
        "не",
        "ни",
        "через",
        "между",
        "над",
        "под",
        "перед",
        "около",
    }
)
_PRONOUN_TOKENS = frozenset(
    {
        "его",
        "ее",
        "её",
        "их",
        "мой",
        "моя",
        "мое",
        "моё",
        "мои",
        "наш",
        "наша",
        "наше",
        "наши",
        "этот",
        "эта",
        "это",
        "эти",
        "тот",
        "та",
        "то",
        "те",
        "свой",
        "своя",
        "свое",
        "своё",
        "свои",
    }
)
_SECTION_HEADING_PREFIX = re.compile(
    r"(?i)^раздел\s+(\d+(?:\.\d+)*)\.?\s+"
)
_INFLECTION_ENDINGS = (
    "ями",
    "ами",
    "ого",
    "его",
    "ому",
    "ему",
    "ыми",
    "ими",
    "ах",
    "ях",
    "ой",
    "ей",
    "ом",
    "ем",
    "ам",
    "ям",
    "ый",
    "ий",
    "ая",
    "яя",
    "ое",
    "ее",
    "ие",
    "ые",
    "их",
    "ых",
    "им",
    "ым",
    "ую",
    "юю",
    "ою",
    "ею",
    "а",
    "я",
    "ы",
    "и",
    "о",
    "е",
    "у",
    "ю",
    "ь",
)


@dataclass(frozen=True)
class ContentMatch:
    utp_position: Topic
    program_item: ProgramContentItem | None
    status: MatchStatus
    confidence: float
    ambiguous_candidates: tuple[str, ...] = ()


def normalize_title(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _heading_core(value: str) -> str:
    """Название без префикса «Раздел N.» — номер программы не входит в ядро."""

    cleaned = value.strip()
    match = _SECTION_HEADING_PREFIX.match(cleaned)
    if match:
        return cleaned[match.end() :].strip().rstrip(".")
    return cleaned


def _looks_like_utp_section(topic: Topic) -> bool:
    if topic.is_standalone_section:
        return True
    parent = (topic.parent_section or "").strip()
    return bool(parent) and normalize_title(parent) == normalize_title(topic.title)


def _looks_like_section_heading(item: ProgramContentItem) -> bool:
    title = item.title.strip()
    if _SECTION_HEADING_PREFIX.match(title):
        return True
    return bool(item.number) and "." not in item.number


def _prefer_section_heading(
    topic: Topic,
    candidates: list[ProgramContentItem],
) -> list[ProgramContentItem]:
    """Для раздела УТП предпочесть заголовок раздела, а не упоминание в теме."""

    if len(candidates) < 2 or not _looks_like_utp_section(topic):
        return candidates
    headings = [item for item in candidates if _looks_like_section_heading(item)]
    return headings or candidates


def _is_embedded_title_mention(section_title: str, item_title: str) -> bool:
    """Короткое имя раздела упомянуто внутри другой (часто вводной) темы."""

    core = normalize_title(_heading_core(section_title))
    item_core = normalize_title(_heading_core(item_title))
    if not core or not item_core or core == item_core:
        return False
    if core not in item_core:
        return False
    quoted = re.findall(r"[«\"']([^»\"]+)[»\"']", item_title)
    if any(
        normalize_title(_heading_core(part)) == core or normalize_title(part) == core
        for part in quoted
    ):
        return True
    return len(core.split()) >= 2


def _ordered_tokens(value: str) -> tuple[str, ...]:
    return tuple(normalize_title(value).split())


def _ordered_distinctive(value: str) -> tuple[str, ...]:
    return tuple(token for token in _ordered_tokens(value) if token not in _GENERIC_TITLE_TOKENS)


def _title_tokens(value: str) -> frozenset[str]:
    return frozenset(_ordered_tokens(value))


def _distinctive_tokens(value: str) -> frozenset[str]:
    return frozenset(_ordered_distinctive(value))


def _lexical_stem(token: str) -> str:
    if len(token) < 5:
        return token
    for ending in _INFLECTION_ENDINGS:
        if token.endswith(ending) and len(token) - len(ending) >= 4:
            return token[: -len(ending)]
    return token


def _token_aligned(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) < 5 or len(right) < 5:
        return False
    return _lexical_stem(left) == _lexical_stem(right)


def _token_in(needle: str, haystack: frozenset[str]) -> bool:
    return any(_token_aligned(needle, token) for token in haystack)


def _tokens_covered(needles: frozenset[str], haystack: frozenset[str]) -> bool:
    if not needles:
        return False
    return all(_token_in(needle, haystack) for needle in needles)


def _overlap(left_tokens: frozenset[str], right_tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(token for token in left_tokens if _token_in(token, right_tokens))


def _residuals(tokens: frozenset[str], other: frozenset[str]) -> frozenset[str]:
    return frozenset(token for token in tokens if not _token_in(token, other))


def _heads_aligned(left: str, right: str) -> bool:
    left_head = _ordered_distinctive(left)
    right_head = _ordered_distinctive(right)
    if not left_head or not right_head:
        return False
    return _token_aligned(left_head[0], right_head[0])


def _function_conflict(left_tokens: frozenset[str], right_tokens: frozenset[str]) -> bool:
    left_fn = frozenset(token for token in _residuals(left_tokens, right_tokens) if token in _FUNCTION_TOKENS)
    right_fn = frozenset(token for token in _residuals(right_tokens, left_tokens) if token in _FUNCTION_TOKENS)
    return bool(left_fn and right_fn and left_fn != right_fn)


def _object_residuals(tokens: frozenset[str], other: frozenset[str]) -> frozenset[str]:
    ignored = _FUNCTION_TOKENS | _PRONOUN_TOKENS
    return frozenset(token for token in _residuals(tokens, other) if token not in ignored)


def _object_conflict(left_tokens: frozenset[str], right_tokens: frozenset[str]) -> bool:
    return bool(_object_residuals(left_tokens, right_tokens) and _object_residuals(right_tokens, left_tokens))


def _titles_similar(left: str, right: str) -> bool:
    """Shared wording worth keeping as a candidate, not enough to bind content."""

    if not left.strip() or not right.strip():
        return False
    if normalize_title(left) == normalize_title(right):
        return True
    left_tokens = _distinctive_tokens(left)
    right_tokens = _distinctive_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if _tokens_covered(left_tokens, right_tokens) or _tokens_covered(right_tokens, left_tokens):
        return True
    return len(_overlap(left_tokens, right_tokens)) >= 2


def _titles_confirmed(left: str, right: str) -> bool:
    """Same topic by title evidence, not by number and not by one shared word."""

    if not left.strip() or not right.strip():
        return False
    if normalize_title(left) == normalize_title(right):
        return True
    left_tokens = _distinctive_tokens(left)
    right_tokens = _distinctive_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = _overlap(left_tokens, right_tokens)
    if (
        (
            _tokens_covered(left_tokens, right_tokens)
            or _tokens_covered(right_tokens, left_tokens)
        )
        and len(overlap) >= 2
    ):
        return True
    if len(overlap) < 2:
        return False
    if not _heads_aligned(left, right):
        return False
    if _function_conflict(left_tokens, right_tokens):
        return False
    if _object_conflict(left_tokens, right_tokens):
        return False
    return True


def _sections_compatible(topic_section: str | None, item_section: str | None) -> bool:
    if not (topic_section or "").strip() or not (item_section or "").strip():
        return True
    if normalize_title(topic_section) == normalize_title(item_section):
        return True
    if normalize_title(_heading_core(topic_section)) == normalize_title(
        _heading_core(item_section)
    ):
        return True
    topic_tokens = _title_tokens(topic_section)
    item_tokens = _title_tokens(item_section)
    return bool(topic_tokens and item_tokens) and (
        topic_tokens <= item_tokens or item_tokens <= topic_tokens
    )


def _items_in_year(
    items: tuple[ProgramContentItem, ...],
    study_year: int | None,
) -> tuple[ProgramContentItem, ...]:
    if study_year is None:
        return items
    return tuple(
        item
        for item in items
        if item.study_year is None or item.study_year == study_year
    )


def _choose_unique(
    topic: Topic,
    candidates: list[ProgramContentItem],
    status: MatchStatus,
    confidence: float,
) -> ContentMatch:
    if len(candidates) == 1:
        return ContentMatch(topic, candidates[0], status, confidence)
    return ContentMatch(
        topic,
        None,
        MatchStatus.NOT_MATCHED,
        0.0,
        tuple(candidate.title for candidate in candidates),
    )


def _unconfirmed_number(
    topic: Topic,
    numbered: list[ProgramContentItem],
) -> ContentMatch:
    return ContentMatch(
        topic,
        None,
        MatchStatus.UNCONFIRMED,
        0.0,
        tuple(item.title for item in numbered),
    )


def bound_program_item(match: ContentMatch | None) -> ProgramContentItem | None:
    """Program content only after a confirmed title/section link."""

    if match is None or match.program_item is None:
        return None
    if match.status in {
        MatchStatus.NOT_MATCHED,
        MatchStatus.UNCONFIRMED,
        MatchStatus.NUMBER_MATCH,
    }:
        return None
    return match.program_item


def match_position(
    topic: Topic,
    items: tuple[ProgramContentItem, ...],
    *,
    study_year: int | None = None,
) -> ContentMatch:
    pool = _items_in_year(items, study_year)
    sectioned = [
        item
        for item in pool
        if _sections_compatible(topic.parent_section, item.parent_section)
    ]
    exact = [item for item in sectioned if item.title.strip() == topic.title.strip()]
    if not exact:
        core_raw = _heading_core(topic.title).strip()
        exact = [
            item
            for item in sectioned
            if core_raw and _heading_core(item.title).strip() == core_raw
        ]
    if exact:
        return _choose_unique(
            topic,
            _prefer_section_heading(topic, exact),
            MatchStatus.EXACT,
            1.0,
        )

    normalized_topic = normalize_title(topic.title)
    topic_core = normalize_title(_heading_core(topic.title))
    normalized = [
        item
        for item in sectioned
        if normalize_title(item.title) == normalized_topic
        or (topic_core and normalize_title(_heading_core(item.title)) == topic_core)
    ]
    if normalized:
        return _choose_unique(
            topic,
            _prefer_section_heading(topic, normalized),
            MatchStatus.NORMALIZED,
            0.95,
        )

    titled = [
        item
        for item in sectioned
        if _titles_confirmed(topic.title, item.title)
        or _titles_confirmed(_heading_core(topic.title), _heading_core(item.title))
    ]
    if titled:
        preferred = _prefer_section_heading(topic, titled)
        heading_preferred = [
            item for item in preferred if _looks_like_section_heading(item)
        ]
        if _looks_like_utp_section(topic) and heading_preferred:
            return _choose_unique(
                topic, heading_preferred, MatchStatus.TEXT_MATCH, 0.85
            )
        if topic.number:
            numbered_titled = [item for item in preferred if item.number == topic.number]
            if numbered_titled:
                return _choose_unique(
                    topic, numbered_titled, MatchStatus.TEXT_MATCH, 0.85
                )
        return _choose_unique(topic, preferred, MatchStatus.TEXT_MATCH, 0.85)

    if topic.number:
        numbered = [item for item in sectioned if item.number == topic.number]
        if numbered:
            return _unconfirmed_number(topic, numbered)

    similar = [
        item
        for item in sectioned
        if _titles_similar(topic.title, item.title)
        and not (
            _looks_like_utp_section(topic)
            and _is_embedded_title_mention(topic.title, item.title)
        )
    ]
    if similar:
        return ContentMatch(
            topic,
            None,
            MatchStatus.NOT_MATCHED,
            0.0,
            tuple(item.title for item in similar),
        )

    return ContentMatch(topic, None, MatchStatus.NOT_MATCHED, 0.0)


def _items_occupied_by_confirmed_matches(
    matches: tuple[ContentMatch, ...],
) -> dict[int | None, set[ProgramContentItem]]:
    occupied: dict[int | None, set[ProgramContentItem]] = {}
    for match in matches:
        item = bound_program_item(match)
        if item is None:
            continue
        occupied.setdefault(item.study_year, set()).add(item)
    return occupied


def _free_candidate_titles(
    titles: tuple[str, ...],
    items: tuple[ProgramContentItem, ...],
    occupied_by_year: dict[int | None, set[ProgramContentItem]],
) -> tuple[str, ...]:
    remaining: list[str] = []
    for title in titles:
        titled = [item for item in items if item.title == title]
        if not titled:
            remaining.append(title)
            continue
        if any(
            item not in occupied_by_year.get(item.study_year, set()) for item in titled
        ):
            remaining.append(title)
    return tuple(remaining)


def _year_compatible(item: ProgramContentItem, study_year: int | None) -> bool:
    if study_year is None:
        return True
    return item.study_year is None or item.study_year == study_year


def _release_occupied_candidates(
    match: ContentMatch,
    items: tuple[ProgramContentItem, ...],
    occupied_by_year: dict[int | None, set[ProgramContentItem]],
) -> ContentMatch:
    if bound_program_item(match) is not None or not match.ambiguous_candidates:
        return match
    remaining = _free_candidate_titles(
        match.ambiguous_candidates,
        items,
        occupied_by_year,
    )
    if remaining == match.ambiguous_candidates:
        return match
    if match.status is MatchStatus.UNCONFIRMED and not remaining:
        return ContentMatch(match.utp_position, None, MatchStatus.NOT_MATCHED, 0.0)
    return ContentMatch(
        match.utp_position,
        None,
        match.status if remaining else MatchStatus.NOT_MATCHED,
        match.confidence if remaining else 0.0,
        remaining,
    )


def _bind_unique_numbered_similar(
    match: ContentMatch,
    items: tuple[ProgramContentItem, ...],
    occupied_by_year: dict[int | None, set[ProgramContentItem]],
    study_year: int | None,
) -> ContentMatch | None:
    """Occupancy leftover: one same-number similar item is enough to bind."""

    if match.status is not MatchStatus.UNCONFIRMED:
        return None
    topic = match.utp_position
    if not topic.number or len(match.ambiguous_candidates) != 1:
        return None
    title = match.ambiguous_candidates[0]
    found: list[ProgramContentItem] = []
    for item in items:
        if item.title != title:
            continue
        if item.number != topic.number:
            continue
        if not _sections_compatible(topic.parent_section, item.parent_section):
            continue
        if not _year_compatible(item, study_year):
            continue
        if not _titles_similar(topic.title, item.title):
            continue
        if item in occupied_by_year.get(item.study_year, set()):
            continue
        found.append(item)
    if len(found) != 1:
        return None
    return ContentMatch(topic, found[0], MatchStatus.TEXT_MATCH, 0.85)


def match_utp_to_program(
    topics: tuple[Topic, ...],
    items: tuple[ProgramContentItem, ...],
    *,
    study_year: int | None = None,
) -> tuple[ContentMatch, ...]:
    matches = tuple(match_position(topic, items, study_year=study_year) for topic in topics)
    occupied_by_year = _items_occupied_by_confirmed_matches(matches)
    released: list[ContentMatch] = []
    for match in matches:
        current = _release_occupied_candidates(match, items, occupied_by_year)
        bound = _bind_unique_numbered_similar(
            current, items, occupied_by_year, study_year
        )
        if bound is not None and bound.program_item is not None:
            occupied_by_year.setdefault(bound.program_item.study_year, set()).add(
                bound.program_item
            )
            released.append(bound)
            continue
        released.append(current)
    return tuple(released)
