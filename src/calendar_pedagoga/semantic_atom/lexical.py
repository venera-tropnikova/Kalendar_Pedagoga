"""Lexical allow-list for shadow RESULT/CONTROL only.

Allowed tokens:
- SOURCE tokens;
- proven morphological forms of SOURCE heads (pymorphy3 lemmas);
- closed pedagogical predicate registry frozen from CE2 at the oracle pin;
- function words of approved CONTROL/RESULT templates.

Any other lexeme is a violation. Production CE2 is not checked by this
module except when a test feeds production text through a shadow row.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import re

import pymorphy3

from calendar_pedagoga import content_engine_v2 as _ce2

_MORPH = pymorphy3.MorphAnalyzer()
_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё-]+", re.UNICODE)

# Closed template lexicon pinned for C1. Expansion requires its own commit.
TEMPLATE_FUNCTION_WORDS = frozenset(
    {
        "а",
        "без",
        "в",
        "во",
        "выполнение",
        "выполнением",
        "выполнения",
        "за",
        "задания",
        "задание",
        "и",
        "из",
        "или",
        "их",
        "к",
        "как",
        "ко",
        "между",
        "на",
        "над",
        "наблюдение",
        "не",
        "ни",
        "о",
        "об",
        "обо",
        "от",
        "опрос",
        "педагогическое",
        "по",
        "под",
        "понятия",
        "понятий",
        "перед",
        "при",
        "про",
        "просмотр",
        "рисунков",
        "с",
        "со",
        "теме",
        "у",
        "устный",
        "функции",
        "через",
        "значение",
        "значения",
        "значению",
    }
)

# Existing closed CONTROL labels only. New template texts require their own commit.
APPROVED_CONTROL_TEMPLATES: dict[str, str] = {
    "product_review.drawings": "просмотр рисунков",
    "product_review.ready_work": "просмотр и оценка готовой работы",
}


@dataclass(frozen=True)
class ApprovedControlTemplate:
    template_id: str
    text: str
    provenance: str = ""
    bound: bool = False
    proven: bool = False


def pedagogical_predicates() -> frozenset[str]:
    """Live CE2 closed predicate registry (nouns + proven finites)."""

    return frozenset(_ce2._PROVEN_FINITE_VERBS) | frozenset(
        _ce2._VERBAL_NOUN_TO_VERB
    ) | frozenset(_ce2._VERBAL_NOUN_TO_VERB.values()) | frozenset(
        _ce2._KNOWLEDGE_RESULT_VERBS
    )


def tokenize(text: str | None) -> tuple[str, ...]:
    return tuple(match.group(0) for match in _TOKEN_RE.finditer(text or ""))


def _lemmas(token: str) -> frozenset[str]:
    core = token.strip("«»„“”\"'().,;:").casefold()
    if not core:
        return frozenset()
    return frozenset(item.normal_form for item in _MORPH.parse(core))


def _allowed_lemmas(source: str | None) -> frozenset[str]:
    lemmas: set[str] = set()
    for token in tokenize(source):
        lemmas.add(token.casefold())
        lemmas.update(_lemmas(token))
    return frozenset(lemmas)


def _fold_template(text: str | None) -> str:
    return " ".join(item.casefold() for item in tokenize(text))


def lookup_approved_control_template(text: str | None) -> str | None:
    folded = _fold_template(text)
    if not folded:
        return None
    for template_id, registered in APPROVED_CONTROL_TEMPLATES.items():
        if _fold_template(registered) == folded:
            return template_id
    return None


def _template_control_lemmas(
    control: str | None,
    approved_templates: Iterable[ApprovedControlTemplate],
) -> frozenset[str]:
    segments = {_fold_template(part) for part in (control or "").split(";")}
    extra: set[str] = set()
    for item in approved_templates:
        if not (item.proven and item.bound and item.provenance and item.template_id):
            continue
        registered = APPROVED_CONTROL_TEMPLATES.get(item.template_id)
        if registered is None:
            continue
        folded = _fold_template(registered)
        if _fold_template(item.text) != folded:
            continue
        if folded not in segments:
            continue
        extra.update(_allowed_lemmas(registered))
    return frozenset(extra)


def shadow_lexical_violations(
    *,
    source: str | None,
    result: str | None,
    control: str | None,
    extra_allowed: Iterable[str] = (),
    approved_templates: Iterable[ApprovedControlTemplate] = (),
) -> tuple[str, ...]:
    """Return disallowed lexemes found in shadow RESULT/CONTROL."""

    allowed = set(_allowed_lemmas(source))
    allowed.update(word.casefold() for word in pedagogical_predicates())
    allowed.update(TEMPLATE_FUNCTION_WORDS)
    allowed.update(token.casefold() for token in extra_allowed)
    template_lemmas = _template_control_lemmas(control, approved_templates)
    violations: list[str] = []
    seen: set[str] = set()
    for field, use_templates in ((result, False), (control, True)):
        field_allowed = allowed | template_lemmas if use_templates else allowed
        for token in tokenize(field):
            folded = token.casefold()
            if folded in field_allowed or folded in seen:
                continue
            if _lemmas(token) & field_allowed:
                continue
            seen.add(folded)
            violations.append(folded)
    return tuple(sorted(violations))
