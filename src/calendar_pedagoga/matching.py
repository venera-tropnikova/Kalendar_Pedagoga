"""Детерминированное сопоставление позиций УТП и содержания программы."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
import re

from calendar_pedagoga.parsing import Topic
from calendar_pedagoga.program_parsing import ProgramContentItem


class MatchStatus(StrEnum):
    EXACT = "EXACT"
    NORMALIZED = "NORMALIZED"
    NUMBER_MATCH = "NUMBER_MATCH"
    TEXT_MATCH = "TEXT_MATCH"
    NOT_MATCHED = "NOT_MATCHED"


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


def match_position(
    topic: Topic,
    items: tuple[ProgramContentItem, ...],
) -> ContentMatch:
    exact = [item for item in items if item.title.strip() == topic.title.strip()]
    if exact:
        return _choose_unique(topic, exact, MatchStatus.EXACT, 1.0)

    normalized_topic = normalize_title(topic.title)
    normalized = [
        item for item in items if normalize_title(item.title) == normalized_topic
    ]
    if normalized:
        return _choose_unique(topic, normalized, MatchStatus.NORMALIZED, 0.95)

    if topic.number:
        numbered = [item for item in items if item.number == topic.number]
        if numbered:
            return _choose_unique(topic, numbered, MatchStatus.NUMBER_MATCH, 0.9)

    topic_tokens = set(normalized_topic.split())
    contained = [item for item in items if topic_tokens and topic_tokens < set(normalize_title(item.title).split())]
    if contained:
        return _choose_unique(topic, contained, MatchStatus.TEXT_MATCH, 0.85)

    scored = sorted(
        (
            (
                SequenceMatcher(
                    None,
                    normalized_topic,
                    normalize_title(item.title),
                ).ratio(),
                item,
            )
            for item in items
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if scored and scored[0][0] >= 0.82:
        best_score = scored[0][0]
        close = [item for score, item in scored if best_score - score < 0.08]
        if len(close) == 1:
            return ContentMatch(topic, scored[0][1], MatchStatus.TEXT_MATCH, best_score)
        return ContentMatch(
            topic,
            None,
            MatchStatus.NOT_MATCHED,
            0.0,
            tuple(item.title for item in close),
        )
    return ContentMatch(topic, None, MatchStatus.NOT_MATCHED, 0.0)


def match_utp_to_program(
    topics: tuple[Topic, ...],
    items: tuple[ProgramContentItem, ...],
) -> tuple[ContentMatch, ...]:
    return tuple(match_position(topic, items) for topic in topics)
