"""DOCX-time repair for RESULT already rejected as unproven_knowledge_object_case.

Morphology is never applied to a RESULT that already passed the grammar gate.
A FAIL in any post-repair gate leaves the week empty (REVIEW_REQUIRED).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import replace
from typing import Iterator

import calendar_pedagoga.content_engine_v2 as ce2
from calendar_pedagoga.content_engine_v2 import (
    LessonContentV2Row,
    REQUIRED_ACTION,
    _meaning_stems,
    _normalize_spaces,
    _proven_finite_predicates,
    _rc_verbosity_block_reasons,
    _rebuild_control_from_accepted_result,
    _result_grammar_issue,
    _role_is_required,
)
from calendar_pedagoga.morphology import repair_rejected_knowledge_sentence
from calendar_pedagoga.semantic_review import (
    _candidate_is_source_grounded,
    review_proposal_docx_issues,
)

_GRAMMAR_UNPROVEN = "unproven_knowledge_object_case"


def _normalize(text: str) -> str:
    return _normalize_spaces(text).casefold()


def _result_action_sentences(result: str) -> list[str]:
    predicates = "|".join(re.escape(verb) for verb in _proven_finite_predicates())
    return [
        part.strip()
        for part in re.split(
            rf"(?i)(?<=[.!?])\s+(?=(?:{predicates})\b)",
            result,
        )
        if part.strip()
    ]


@contextmanager
def _scoped_knowledge_case_grammar(allowed: frozenset[str]) -> Iterator[None]:
    original = ce2._result_grammar_issue

    def scoped(sentence: str) -> str:
        issue = original(sentence)
        if issue == _GRAMMAR_UNPROVEN and _normalize(sentence) in allowed:
            return ""
        return issue

    ce2._result_grammar_issue = scoped
    try:
        yield
    finally:
        ce2._result_grammar_issue = original


def try_repair_review_candidate(row: LessonContentV2Row) -> LessonContentV2Row | None:
    """Repair only knowledge phrases already rejected by the grammar gate.

    Returns a new row when every post-repair gate passes; otherwise None.
    """

    result = _normalize_spaces(row.planned_result or "")
    if not result:
        return None

    allowed: set[str] = set()
    rebuilt: list[str] = []
    for sentence in _result_action_sentences(result):
        if _result_grammar_issue(sentence) != _GRAMMAR_UNPROVEN:
            rebuilt.append(sentence)
            continue
        repaired = repair_rejected_knowledge_sentence(sentence)
        if repaired is None:
            return None
        rebuilt.append(repaired)
        allowed.add(_normalize(repaired))

    if not allowed:
        return None

    new_result = " ".join(rebuilt)
    control = _rebuild_control_from_accepted_result(
        new_result,
        row.assessment_method or "",
        lesson_type=row.lesson_type,
        theory_hours=row.source.theory_hours,
        practice_hours=row.source.practice_hours,
    )
    if not _normalize_spaces(control):
        return None
    if _rc_verbosity_block_reasons(new_result, control):
        return None

    source_text = " ".join(
        (
            row.source.topic_title,
            row.theory_text,
            row.practice_text,
            row.source.program_content_full,
        )
    )
    claimed = tuple(
        clause
        for clause, _status in row.clause_coverage
        if _role_is_required(dict(row.clause_roles).get(clause, REQUIRED_ACTION))
    )
    repaired_claimed = tuple(
        clause
        for clause in claimed
        if any(stem[:4] in _normalize(new_result) for stem in _meaning_stems(clause))
    )
    if repaired_claimed and not _candidate_is_source_grounded(
        new_result, repaired_claimed, source_text
    ):
        return None

    candidate = replace(row, planned_result=new_result, assessment_method=control)
    with _scoped_knowledge_case_grammar(frozenset(allowed)):
        if review_proposal_docx_issues(candidate):
            return None
    return candidate
