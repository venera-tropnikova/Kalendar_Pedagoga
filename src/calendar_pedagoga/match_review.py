"""Ручные решения по спорному matching. Эвристики названий не меняет."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json

from calendar_pedagoga.matching import ContentMatch, MatchStatus
from calendar_pedagoga.parsing import Topic
from calendar_pedagoga.program_parsing import ProgramContentItem


ReviewMap = dict[tuple[str | None, str, str | None], dict]

MISSING_PROGRAM_CONTENT_NOTICE = (
    "Не найден отдельный блок содержания программы для темы"
)


@dataclass(frozen=True)
class ProgramItemRef:
    number: str | None
    title: str
    section: str | None
    study_year: int | None

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "title": self.title,
            "section": self.section,
            "study_year": self.study_year,
        }

    @classmethod
    def from_item(cls, item: ProgramContentItem) -> ProgramItemRef:
        return cls(item.number, item.title, item.parent_section, item.study_year)

    @classmethod
    def from_dict(cls, data: Mapping) -> ProgramItemRef:
        return cls(
            data.get("number"),
            str(data["title"]),
            data.get("section"),
            data.get("study_year"),
        )


def topic_key(topic: Topic) -> tuple[str | None, str, str | None]:
    return (topic.number, topic.title, topic.parent_section)


def is_disputed_match(match: ContentMatch) -> bool:
    if match.status is MatchStatus.UNCONFIRMED:
        return True
    return match.status is MatchStatus.NOT_MATCHED and bool(match.ambiguous_candidates)


def is_missing_program_content(match: ContentMatch) -> bool:
    return (
        match.status is MatchStatus.NOT_MATCHED
        and not match.ambiguous_candidates
        and match.program_item is None
    )


def review_scope(
    utp_name: str,
    utp_content: bytes,
    program_name: str | None,
    program_content: bytes | None,
    study_year: int | None,
) -> str:
    parts = [
        [utp_name, hashlib.sha256(utp_content).hexdigest()],
        [
            program_name,
            hashlib.sha256(program_content).hexdigest() if program_content is not None else None,
        ],
        study_year,
    ]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def resolve_item_ref(
    items: tuple[ProgramContentItem, ...],
    ref: ProgramItemRef,
) -> ProgramContentItem | None:
    found = [
        item
        for item in items
        if item.number == ref.number
        and item.title == ref.title
        and item.parent_section == ref.section
        and item.study_year == ref.study_year
    ]
    if len(found) != 1:
        return None
    return found[0]


def candidate_items(
    match: ContentMatch,
    items: tuple[ProgramContentItem, ...],
) -> tuple[ProgramContentItem, ...]:
    titles = set(match.ambiguous_candidates)
    return tuple(item for item in items if item.title in titles)


def apply_match_reviews(
    matches: tuple[ContentMatch, ...],
    items: tuple[ProgramContentItem, ...],
    reviews: Mapping | None,
) -> tuple[ContentMatch, ...]:
    if not reviews:
        return matches
    applied: list[ContentMatch] = []
    for match in matches:
        if not is_disputed_match(match):
            applied.append(match)
            continue
        review = reviews.get(topic_key(match.utp_position))
        if not isinstance(review, Mapping):
            applied.append(match)
            continue
        decision = review.get("decision")
        if decision == "USER_REJECTED":
            applied.append(
                ContentMatch(
                    match.utp_position,
                    None,
                    match.status,
                    0.0,
                    match.ambiguous_candidates,
                )
            )
            continue
        if decision != "USER_CONFIRMED":
            applied.append(match)
            continue
        raw_ref = review.get("item_ref")
        if not isinstance(raw_ref, Mapping):
            applied.append(match)
            continue
        item = resolve_item_ref(items, ProgramItemRef.from_dict(raw_ref))
        if item is None:
            applied.append(match)
            continue
        applied.append(
            ContentMatch(
                match.utp_position,
                item,
                MatchStatus.USER_CONFIRMED,
                1.0,
            )
        )
    return tuple(applied)


def unresolved_disputed(
    matches: tuple[ContentMatch, ...],
    reviews: Mapping | None,
) -> tuple[ContentMatch, ...]:
    pending: list[ContentMatch] = []
    for match in matches:
        if not is_disputed_match(match):
            continue
        review = (reviews or {}).get(topic_key(match.utp_position))
        if not isinstance(review, Mapping) or review.get("decision") not in {
            "USER_CONFIRMED",
            "USER_REJECTED",
        }:
            pending.append(match)
    return tuple(pending)


def rejected_topic_count(reviews: Mapping | None) -> int:
    if not reviews:
        return 0
    return sum(
        1
        for review in reviews.values()
        if isinstance(review, Mapping) and review.get("decision") == "USER_REJECTED"
    )
