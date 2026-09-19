"""Shadow builders for proven explicit knowledge. No new predicates."""

from __future__ import annotations

import re

from calendar_pedagoga import content_engine_v2 as _ce2
from calendar_pedagoga.semantic_atom.dispatcher import (
    NO_STRUCTURAL_MATCH,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import _c5_candidate
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    SourceAtom,
    StructuralEvidence,
)

_OPEN_CATALOG_RE = re.compile(
    r"(?i)(?:\b(?:и\s+другие|и\s+прочее|и\s+прочие|и\s+др\.?|"
    r"и\s+т\.?\s*д\.?|и\s+т\.?\s*п\.?|и\s+пр\.?)\b|\.\.\.|…)"
)
_KEM_START_RE = re.compile(r"(?i)^кем\b")
_NUMERIC_SIDE_RE = re.compile(r"^[\d\s./:-]+$")


def _is_knowledge_question(text: str) -> bool:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned:
        return False
    if _ce2._is_interrogative_clause(cleaned):
        return True
    return bool(_KEM_START_RE.match(cleaned))


def _owned_by_c5_knowledge(text: str) -> bool:
    return _ce2._knowledge_clause_result(text, theory_only=True) is not None


def _what_is_helper(text: str) -> tuple[str, str, str, str] | None:
    built = _ce2._theory_knowledge_reconstruction(text)
    if built is None:
        return None
    phrase, action, obj, cond = built
    if action.casefold() == "объясняет" and "что такое" in phrase.casefold():
        return phrase, action, obj, cond
    return None


def _decap_body(text: str) -> str:
    return _ce2._decap_phrase(_ce2._normalize_spaces(text).strip(" ."))


def _question(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _ce2._normalize_spaces(text).strip()
    if not _is_knowledge_question(cleaned):
        return None
    if _owned_by_c5_knowledge(cleaned):
        return None
    what = _what_is_helper(cleaned)
    if what is not None:
        return what
    body = cleaned.strip(" .").rstrip("?").strip()
    if not body or not _ce2._balanced_fold_object(body):
        return None
    tokens = body.split()
    if len(tokens) < 2:
        return None
    if not (
        _ce2._INTERROGATIVE_START_RE.match(body) or _KEM_START_RE.match(body)
    ):
        return None
    complement = " ".join(tokens[1:]).strip()
    if not complement:
        return None
    phrase = _ce2._normalize_spaces(f"объясняет, {_decap_body(body)}")
    return phrase, "объясняет", complement, body


def _dash_outside_quotes(text: str) -> list[int]:
    depth = 0
    found: list[int] = []
    for index, char in enumerate(text):
        if char in {"«", "„", '"'}:
            depth += 1
        elif char in {"»", "“", '"'}:
            depth = max(0, depth - 1)
        elif char in {"—", "–"} and depth == 0:
            found.append(index)
    return found


def _looks_numeric_side(side: str) -> bool:
    cleaned = side.strip(" .")
    if not cleaned:
        return True
    if _NUMERIC_SIDE_RE.fullmatch(cleaned):
        return True
    return bool(re.search(r"\d", cleaned) and not re.search(r"[А-Яа-яЁёA-Za-z]", cleaned))


def _action_helper_proves(text: str) -> bool:
    probes = (
        _ce2._closed_form_activity_result,
        lambda item: _ce2._explicit_action_reconstruction(item, theory_only=False),
        _ce2._nominal_activity_result,
        _ce2._unconjugated_practice_activity_result,
        _ce2._finite_produce_result,
    )
    for probe in probes:
        built = probe(text)
        if built and built[0]:
            return True
    return False


def _form_or_productive_head(head: str) -> bool:
    if _ce2._has_clause_initial_productive_head(head):
        return True
    token = _ce2._leading_activity_token(head)
    return bool(
        token
        and (
            _ce2._is_leading_form_activity(token)
            or _ce2._is_explicit_action_head_token(token)
        )
    )


def _definition(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned or _owned_by_c5_knowledge(cleaned):
        return None
    if _ce2._symbol_clause_result(cleaned) is not None:
        return None
    dashes = _dash_outside_quotes(cleaned)
    if len(dashes) != 1:
        return None
    index = dashes[0]
    head = cleaned[:index].strip()
    tail = cleaned[index + 1 :].strip()
    tail = re.sub(r"(?i)^это\s+", "", tail).strip()
    if not head or not tail:
        return None
    if _looks_numeric_side(head) or _looks_numeric_side(tail):
        return None
    if not _ce2._balanced_fold_object(head) or not _ce2._balanced_fold_object(tail):
        return None
    if _form_or_productive_head(head) or _action_helper_proves(cleaned):
        return None
    if _form_or_productive_head(tail) or _action_helper_proves(tail):
        return None
    if _ce2._has_clause_initial_productive_head(tail):
        return None
    phrase = _ce2._normalize_spaces(f"объясняет {_decap_body(cleaned)}")
    return phrase, "объясняет", head, tail


def _catalog_members(tail: str) -> list[str] | None:
    cleaned = _ce2._normalize_spaces(tail).strip()
    if not cleaned or _OPEN_CATALOG_RE.search(cleaned):
        return None
    if cleaned.endswith((",", ";", ":", "—", "–", "-", "…")):
        return None
    if cleaned.endswith("..."):
        return None
    if ";" in cleaned:
        members = [part.strip(" .") for part in cleaned.split(";")]
        if any(not part for part in members):
            return None
    else:
        members = _ce2._split_source_list(cleaned)
    if len(members) < 2:
        return None
    if any(_ce2._FINITE_VERB_RE.search(part) or ":" in part for part in members):
        return None
    return members


def _proven_knowledge_label(head: str) -> bool:
    tokens = [_ce2._token_core(part) for part in head.split() if _ce2._token_core(part)]
    if not tokens:
        return False
    return any(_ce2._is_theory_knowledge_token(token) for token in tokens)


def _catalog_blocked_head(head: str) -> bool:
    if _ce2._has_explicit_action_catalogue(f"{head}: x, y"):
        return True
    if _form_or_productive_head(head):
        return True
    if _ce2._has_clause_initial_productive_head(head):
        return True
    return False


def _catalog(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if ":" not in cleaned:
        return None
    if _owned_by_c5_knowledge(cleaned):
        return None
    if _ce2._concept_values_clause_result(cleaned) is not None:
        return None
    if _ce2._classification_clause_result(cleaned, theory_only=True) is not None:
        return None
    if _ce2._has_explicit_action_catalogue(cleaned):
        return None
    head, tail = cleaned.split(":", 1)
    head, tail = head.strip(), tail.strip()
    if not head or not tail:
        return None
    if not _proven_knowledge_label(head) or _catalog_blocked_head(head):
        return None
    members = _catalog_members(tail)
    if members is None:
        return None
    phrase = _ce2._normalize_spaces(f"называет {_decap_body(cleaned)}")
    return phrase, "называет", tail, head


def _no_match(builder_id: str, atom: SourceAtom) -> FrameCandidate:
    return FrameCandidate(
        builder_id=builder_id,
        atom_id=atom.id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=FrameKind.PROJECTED,
        proposed_predicate="",
        proposed_object="",
        proposed_complement="",
        proposed_result="",
        proposed_control="",
        structural_evidence=StructuralEvidence(notes=(NO_STRUCTURAL_MATCH,)),
        lexical_check=LexicalCheckResult(passed=True),
        confidence=CandidateConfidence.REJECTED,
        rejection_reason=NO_STRUCTURAL_MATCH,
    )


def _propose(builder_id: str, kind: FrameKind, helper, atom: SourceAtom) -> FrameCandidate:
    built = helper(atom.text)
    if not built:
        return _no_match(builder_id, atom)
    return _c5_candidate(builder_id, kind, lambda _text: built, atom)


def _register(builder_id: str, helper_name: str, kind: FrameKind) -> RegisteredBuilder:
    def propose(atom: SourceAtom) -> FrameCandidate:
        helper = globals()[helper_name]
        return _propose(builder_id, kind, helper, atom)

    return RegisteredBuilder(builder_id=builder_id, propose=propose)


KNOWLEDGE_REGISTRY: tuple[RegisteredBuilder, ...] = (
    _register("knowledge_question", "_question", FrameKind.KNOWLEDGE),
    _register("knowledge_definition", "_definition", FrameKind.DEFINITION),
    _register("knowledge_catalog", "_catalog", FrameKind.KNOWLEDGE),
)
