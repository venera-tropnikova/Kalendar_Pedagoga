"""Bind each calendar slot to one confirmed canonical source.

Fragments stay in packet order. Each fragment is given to at most one slot.
A slot with no fragment uses the confirmed UTP row title. An ambiguous
section blocks every slot of the topics that share it, before CE2.
"""
from __future__ import annotations

from dataclasses import dataclass

from calendar_pedagoga.confirmed_slot_allocation import allocate_contiguous
from calendar_pedagoga.ingestion_adapter.models import CanonicalRef
from calendar_pedagoga.lesson_content import _split_explicit_practice


@dataclass(frozen=True)
class SlotSource:
    week_number: int
    allocation_index: int
    topic_id: str
    year: int
    number: str | None
    title: str
    section: str
    theory: int
    training: int
    practice: int
    text: str
    refs: tuple[CanonicalRef, ...]
    spans: tuple
    fragment_ids: tuple[str, ...]
    title_fallback: bool
    blocked: str | None
    week: object


def bind_slots(packet, elements) -> tuple[SlotSource, ...]:
    year = packet.plan.study_year
    owners: dict[str, set[str]] = {}
    sections_by_topic: dict[str, tuple[str, ...]] = {}
    for binding in packet.bindings:
        if binding.year_scope != (year,):
            continue
        section_ids = tuple(ref.id for ref in binding.sections)
        sections_by_topic[binding.topic.id] = section_ids
        for section_id in section_ids:
            owners.setdefault(section_id, set()).add(binding.topic.id)
    fragments_by_section: dict[str, list] = {}
    for fragment in packet.fragments:
        if fragment.provenance.year_scope != (year,):
            continue
        fragments_by_section.setdefault(fragment.canonical.section_id, []).append(fragment)
    grouped: dict[tuple[int, str], dict] = {}
    order: list[tuple[int, str]] = []
    for channel, hours, week, number, title, section, topic in elements:
        key = (week.number, topic.canonical.id)
        if key not in grouped:
            grouped[key] = {
                "week": week,
                "number": number,
                "title": title,
                "section": section,
                "topic": topic,
                "theory": 0,
                "training": 0,
                "practice": 0,
            }
            order.append(key)
        grouped[key][channel] += hours
    by_topic: dict[str, list[tuple[int, str]]] = {}
    for key in order:
        by_topic.setdefault(key[1], []).append(key)
    assigned: dict[tuple[int, str], SlotSource] = {}
    for topic_id, keys in by_topic.items():
        topic = grouped[keys[0]]["topic"]
        section_ids = sections_by_topic.get(topic_id, ())
        ambiguous = any(len(owners.get(section_id, ())) != 1 for section_id in section_ids)
        fragments = [
            fragment
            for section_id in section_ids
            if len(owners.get(section_id, ())) == 1
            for fragment in fragments_by_section.get(section_id, ())
        ]
        shares = (
            tuple(() for _ in keys)
            if ambiguous
            else _shares_for_slots(fragments, [grouped[key] for key in keys])
        )
        for index, key in enumerate(keys):
            item = grouped[key]
            share = shares[index]
            if ambiguous:
                source = _blocked(item, index, year)
            elif share:
                source = _from_fragments(item, index, year, share)
            else:
                source = _from_title(item, index, year)
            assigned[key] = source
    return tuple(assigned[key] for key in order)


def _display_weights(items):
    theory = []
    practice = []
    for item in items:
        practice.append(item["practice"])
        if item["theory"]:
            theory.append(item["theory"])
        elif item["training"] and not item["practice"]:
            theory.append(item["training"])
        else:
            theory.append(0)
    return tuple(theory), tuple(practice)


def _fragment_channel(fragment) -> str:
    split = _split_explicit_practice(fragment.canonical.raw_text or "")
    if split is None:
        return "open"
    theory, practice = split
    if practice and not theory:
        return "practice"
    return "open"


def _shares_for_slots(fragments, items):
    theory_weights, practice_weights = _display_weights(items)
    grouped = {"practice": [], "open": []}
    for fragment in fragments:
        grouped[_fragment_channel(fragment)].append(fragment)
    shares = [[] for _ in items]
    _place(shares, grouped["practice"], practice_weights or theory_weights)
    open_weights = theory_weights if sum(theory_weights) else practice_weights
    _place(shares, grouped["open"], open_weights)
    return tuple(tuple(item) for item in shares)


def _place(shares, fragments, weights):
    if not fragments or not weights or not sum(weights):
        return
    for index, portion in enumerate(allocate_contiguous(tuple(fragments), weights)):
        shares[index].extend(portion)


def _blocked(item, index, year) -> SlotSource:
    return _source(item, index, year, "", (), (), (), False, "AMBIGUOUS_BINDING")


def _from_fragments(item, index, year, fragments) -> SlotSource:
    text = "\n".join(fragment.canonical.raw_text for fragment in fragments if fragment.canonical.raw_text)
    refs = tuple(fragment.provenance.ref for fragment in fragments)
    spans = tuple(span for fragment in fragments for span in fragment.provenance.spans)
    return _source(
        item, index, year, text, refs, spans,
        tuple(fragment.canonical.id for fragment in fragments),
        False, None,
    )


def _from_title(item, index, year) -> SlotSource:
    topic = item["topic"]
    return _source(
        item, index, year, topic.canonical.title,
        (topic.provenance.ref,), topic.provenance.spans, (), True, None,
    )


def _source(item, index, year, text, refs, spans, fragment_ids, title_fallback, blocked) -> SlotSource:
    topic = item["topic"]
    return SlotSource(
        item["week"].number,
        index,
        topic.canonical.id,
        year,
        item["number"],
        item["title"],
        item["section"],
        item["theory"],
        item["training"],
        item["practice"],
        text,
        refs,
        spans,
        fragment_ids,
        title_fallback,
        blocked,
        item["week"],
    )
