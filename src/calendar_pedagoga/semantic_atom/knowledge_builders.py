"""Shadow builders for proven explicit knowledge. No new predicates."""

from __future__ import annotations

import re
from dataclasses import replace

from calendar_pedagoga import content_engine_v2 as _ce2
from calendar_pedagoga.semantic_atom.action_builders import (
    AMBIGUOUS_CATALOG_ASSIGNMENT,
    OPEN_TAIL_UNRESOLVED,
    _action_catalog_head_ok,
)
from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    NO_STRUCTURAL_MATCH,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import _c5_candidate, catalog_selector
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    SourceAtom,
    SourceSpan,
    StructuralEvidence,
)

_OPEN_CATALOG_RE = re.compile(
    r"(?i)(?:\b(?:и\s+другие|и\s+прочее|и\s+прочие|и\s+др\.?|"
    r"и\s+т\.?\s*д\.?|и\s+т\.?\s*п\.?|и\s+пр\.?)\b|\.\.\.|…)"
)
_KEM_START_RE = re.compile(r"(?i)^кем\b")
_NUMERIC_SIDE_RE = re.compile(r"^[\d\s./:-]+$")
_DASH_RE = re.compile(r"[‐‑‒–—−\-]+")


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


def _definition_shape(text: str) -> bool:
    return len(_dash_outside_quotes(text)) == 1


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


def _open_colon_catalog(text: str) -> bool:
    cleaned = _ce2._normalize_spaces(text)
    if ":" not in cleaned:
        return False
    _head, tail = cleaned.split(":", 1)
    return bool(_OPEN_CATALOG_RE.search(tail))


def _strip_open_catalog_marker(tail: str) -> str:
    cleaned = _OPEN_CATALOG_RE.sub("", _ce2._normalize_spaces(tail))
    return cleaned.strip(" ,.;…")


def _split_catalog_members(tail: str) -> list[str] | None:
    cleaned = _ce2._normalize_spaces(tail).strip()
    if not cleaned:
        return []
    if cleaned.endswith((",", ";", ":", "—", "–", "-")):
        return None
    if ";" in cleaned:
        members = [part.strip(" .") for part in cleaned.split(";")]
        if any(not part for part in members):
            return None
    else:
        members = [
            part.strip(" .")
            for part in _ce2._split_coordinating_и_outside_quotes(cleaned)
            if part.strip(" .")
        ]
        if len(members) < 2:
            members = _ce2._split_source_list(cleaned)
    if any(_ce2._FINITE_VERB_RE.search(part) or ":" in part for part in members):
        return None
    return members


def _catalog_members(tail: str) -> list[str] | None:
    cleaned = _ce2._normalize_spaces(tail).strip()
    if not cleaned or _OPEN_CATALOG_RE.search(cleaned):
        return None
    if cleaned.endswith("..."):
        return None
    members = _split_catalog_members(cleaned)
    if members is None or len(members) < 2:
        return None
    return members


def _named_members_from_tail(tail: str) -> list[str]:
    stripped = _strip_open_catalog_marker(tail)
    members = _split_catalog_members(stripped) if stripped else []
    return members or []


def _fold_span(text: str) -> str:
    return canonicalize_text(_DASH_RE.sub("-", text)).casefold()


def _is_generic_head_selector(head: str, selector: str) -> bool:
    if not selector.strip():
        return False
    folded = _fold_span(selector)
    return folded == _fold_span(head) or folded == _fold_span(f"{head}:")


def _select_catalog_members(
    head: str, members: list[str], selector: str
) -> list[str] | None:
    if not selector.strip():
        return members
    folded = _fold_span(selector)
    hits = [
        member
        for member in members
        if _fold_span(member) == folded or _fold_span(f"{head}: {member}") == folded
    ]
    if len(hits) == 1:
        return hits
    if len(hits) > 1:
        return None
    if _is_generic_head_selector(head, selector):
        return None
    return members


def _proven_knowledge_label(head: str) -> bool:
    tokens = [_ce2._token_core(part) for part in head.split() if _ce2._token_core(part)]
    if not tokens:
        return False
    return any(_ce2._is_theory_knowledge_token(token) for token in tokens)


def _role_head(head: str, tail: str = "") -> bool:
    words = [part for part in head.split() if part.strip()]
    if not words:
        return False
    if _ce2._is_substantivized_role_object(words):
        return True
    tail_words = tail.split()
    return bool(
        len(words) == 1
        and _ce2._is_adjective(words[0])
        and tail_words
        and _ce2._is_preposition(tail_words[0])
    )


def _event_head(head: str, tail: str = "") -> bool:
    blob = f"{head}: {tail}" if tail else head
    if _ce2._has_event_participation(blob):
        return True
    if _ce2._conducted_event_result(blob):
        return True
    head_tokens = head.split()
    return any(
        any(stem in _ce2._strip_punct_word(token)[1].casefold() for stem in _ce2._EVENT_KIND_STEMS)
        for token in head_tokens
    )


def _head_has_finite_verb(head: str) -> bool:
    for token in head.split():
        core = _ce2._token_core(token)
        if not core:
            continue
        if _ce2._is_proven_finite_token(core):
            return True
        if _ce2._RESULT_FINITE_RE.match(core):
            return True
    return False


def _ce2_knowledge_helper_proves(text: str) -> bool:
    if _owned_by_c5_knowledge(text):
        return True
    if _ce2._theory_knowledge_reconstruction(text) is not None:
        return True
    if _ce2._classification_clause_result(text, theory_only=True) is not None:
        return True
    return False


def _catalog_blocked_head(head: str, tail: str = "", full: str = "") -> bool:
    if _ce2._has_explicit_action_catalogue(f"{head}: x, y"):
        return True
    if _form_or_productive_head(head):
        return True
    if _ce2._has_clause_initial_productive_head(head):
        return True
    if _action_catalog_head_ok(head):
        return True
    if _role_head(head, tail):
        return True
    if _event_head(head, tail):
        return True
    if _head_has_finite_verb(head) and not _ce2_knowledge_helper_proves(full or f"{head}: {tail}"):
        return True
    return False


def _blocked_knowledge_atom(text: str) -> bool:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned:
        return True
    if _ce2._starts_with_action_finite(cleaned):
        return True
    if any(_ce2._is_proven_finite_token(token) for token in cleaned.split()):
        return True
    if _form_or_productive_head(cleaned):
        return True
    if _ce2._has_clause_initial_productive_head(cleaned):
        return True
    if _role_head(cleaned):
        return True
    if _event_head(cleaned):
        return True
    return False


def _accusative_catalog_head(head: str) -> str | None:
    proven = _ce2._proven_theory_object(head)
    if proven:
        return proven
    tokens = [part for part in head.split() if part.strip()]
    if not tokens:
        return None
    cores = [(_ce2._token_core(token) or token) for token in tokens]
    if any(_ce2._is_theory_knowledge_token(token) for token in tokens) and all(
        _ce2._characterize_head_ok(core)
        or _ce2._is_preposition(token)
        or core.casefold() in {"и", "или"}
        for token, core in zip(tokens, cores)
    ):
        return _decap_body(head)
    first = tokens[0]
    first_core = cores[0]
    if _ce2._characterize_head_ok(first_core) or _ce2._proven_feminine_acc(first_core):
        led = [_ce2._theory_object_token(first), *tokens[1:]]
        if _ce2._theory_object_span_ok(led):
            return _ce2._normalize_spaces(" ".join(led))
    return None


def _covers_full_atom(atom: SourceAtom, part: str) -> bool:
    return _fold_span(atom.text.strip(" .")) == _fold_span(part.strip(" ."))


def _source_part_for_object(atom_text: str, obj: str) -> str | None:
    cleaned = _ce2._normalize_spaces(atom_text)
    needle = _ce2._normalize_spaces(obj).strip(" .")
    if not needle:
        return None
    if _fold_span(cleaned.strip(" .")) == _fold_span(needle):
        return cleaned.strip(" .")
    index = cleaned.find(needle)
    if index < 0:
        index = cleaned.casefold().find(needle.casefold())
        if index >= 0:
            return cleaned[index : index + len(needle)]
    elif index >= 0:
        return needle
    members = _ce2._split_source_list(cleaned.strip(" .")) or []
    hits = [
        member
        for member in members
        if _fold_span(member) == _fold_span(needle)
        or _fold_span(needle) in _fold_span(member)
        or _fold_span(member) in _fold_span(needle)
    ]
    if len(hits) == 1:
        return hits[0]
    return None


def _span_for_part(atom: SourceAtom, part: str) -> SourceSpan | None:
    hay = atom.text
    needle = _ce2._normalize_spaces(part).strip(" .")
    if not needle:
        return None
    index = hay.find(needle)
    if index < 0:
        index = hay.casefold().find(needle.casefold())
        if index < 0:
            return None
        if hay[index : index + len(needle)].casefold() != needle.casefold():
            return None
    start = atom.span.start + index
    end = start + len(needle)
    stripped_end = len(hay.rstrip(" ."))
    if index == 0 and index + len(needle) >= stripped_end:
        return atom.span
    return replace(atom.span, start=start, end=end)


def _coverage_span_for_part(atom: SourceAtom, part: str) -> SourceSpan | None:
    if _covers_full_atom(atom, part):
        return atom.span
    located = _source_part_for_object(atom.text, part)
    if located is None:
        return None
    if _covers_full_atom(atom, located):
        return atom.span
    return _span_for_part(atom, located)


def _catalog_phrase(head: str, used_tail: str, original: str | None = None) -> tuple[str, str, str, str] | None:
    acc_head = _accusative_catalog_head(head)
    if not acc_head:
        return None
    body = f"{acc_head}: {used_tail}"
    phrase = _ce2._normalize_spaces(f"называет {_decap_body(body)}")
    return phrase, "называет", used_tail, head


def _catalog(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if ":" not in cleaned:
        return None
    if _owned_by_c5_knowledge(cleaned):
        return None
    if _ce2._semiotic_object_result(cleaned) is not None:
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
    if _catalog_blocked_head(head, tail, cleaned):
        return None
    members = _catalog_members(tail)
    if members is None:
        return None
    return _catalog_phrase(head, tail, cleaned)


def _nominal_explain(text: str) -> tuple[str, str, str, str] | None:
    built = _ce2._theory_knowledge_reconstruction(text)
    if built is None:
        return None
    phrase, action, obj, cond = built
    if action.casefold() != "объясняет":
        return None
    if "что такое" in phrase.casefold():
        return None
    if "в чём" not in phrase.casefold() and "в чем" not in phrase.casefold():
        return None
    return phrase, action, obj, cond


def _noun_phrase(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned or ":" in cleaned:
        return None
    if _is_knowledge_question(cleaned) or _definition_shape(cleaned):
        return None
    if _owned_by_c5_knowledge(cleaned):
        return None
    if _ce2._symbol_clause_result(cleaned) is not None:
        return None
    if _ce2._semiotic_object_result(cleaned) is not None:
        return None
    if _ce2._locative_drawing_result(cleaned) is not None:
        return None
    if _ce2._purpose_clause_result(cleaned) is not None:
        return None
    if _ce2._concept_values_clause_result(cleaned) is not None:
        return None
    if _ce2._classification_clause_result(cleaned, theory_only=True) is not None:
        return None
    if _blocked_knowledge_atom(cleaned):
        return None
    phrase, action, obj, cond = _ce2._characterize(cleaned)
    if phrase and _knowledge_structure_kept(cleaned, obj):
        return phrase, action, obj, cond
    return _nominal_explain(cleaned)


def _knowledge_structure_kept(source: str, obj: str) -> bool:
    source_tokens = source.split()
    object_tokens = obj.split()
    if not source_tokens or not object_tokens:
        return False
    if not any(_ce2._is_theory_knowledge_token(token) for token in source_tokens):
        return False
    return any(_ce2._is_theory_knowledge_token(token) for token in object_tokens)


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


def _reject_catalog(atom: SourceAtom, reason: str) -> FrameCandidate:
    return FrameCandidate(
        builder_id="knowledge_catalog",
        atom_id=atom.id,
        span=atom.span,
        source_fingerprint=atom.source_fingerprint,
        proposed_kind=FrameKind.PROJECTED,
        proposed_predicate="",
        proposed_object="",
        proposed_complement="",
        proposed_result="",
        proposed_control="",
        structural_evidence=StructuralEvidence(notes=(reason,)),
        lexical_check=LexicalCheckResult(passed=True),
        confidence=CandidateConfidence.REJECTED,
        rejection_reason=reason,
    )


def _emit_knowledge_catalog(
    atom: SourceAtom,
    built: tuple[str, str, str, str],
    notes: tuple[str, ...] = (),
    span: SourceSpan | None = None,
) -> FrameCandidate:
    candidate = _c5_candidate(
        "knowledge_catalog", FrameKind.KNOWLEDGE, lambda _text: built, atom
    )
    if not candidate.is_valid:
        return candidate
    if span is not None:
        candidate = replace(candidate, span=span)
    if notes:
        return replace(
            candidate,
            structural_evidence=StructuralEvidence(
                notes=(*candidate.structural_evidence.notes, *notes)
            ),
        )
    return candidate


def _open_knowledge_catalog(
    atom: SourceAtom,
    head: str,
    named: list[str],
    selector: str,
) -> FrameCandidate:
    selected = _select_catalog_members(head, named, selector) if named else None
    if named and selected is None:
        return _reject_catalog(atom, AMBIGUOUS_CATALOG_ASSIGNMENT)
    if named and selected and selected != named:
        used_tail = ", ".join(selected)
        built = _catalog_phrase(head, used_tail)
        if built is None:
            return _no_match("knowledge_catalog", atom)
        span = _coverage_span_for_part(atom, used_tail)
        if span is None:
            return _no_match("knowledge_catalog", atom)
        return _emit_knowledge_catalog(
            atom,
            built,
            notes=(OPEN_TAIL_UNRESOLVED,),
            span=span,
        )
    if named:
        used_tail = ", ".join(named)
        built = _catalog_phrase(head, used_tail)
        if built is None:
            return _no_match("knowledge_catalog", atom)
        span = _coverage_span_for_part(atom, used_tail) or atom.span
        return _emit_knowledge_catalog(
            atom,
            built,
            notes=(OPEN_TAIL_UNRESOLVED,),
            span=span,
        )
    return _no_match("knowledge_catalog", atom)


def _propose_knowledge_catalog(atom: SourceAtom) -> FrameCandidate:
    raw = _ce2._normalize_spaces(atom.text)
    cleaned = raw.strip(" .")
    if ":" not in cleaned:
        return _no_match("knowledge_catalog", atom)
    if _owned_by_c5_knowledge(cleaned):
        return _no_match("knowledge_catalog", atom)
    if _ce2._semiotic_object_result(cleaned) is not None:
        return _no_match("knowledge_catalog", atom)
    if _ce2._concept_values_clause_result(cleaned) is not None:
        return _no_match("knowledge_catalog", atom)
    if _ce2._classification_clause_result(cleaned, theory_only=True) is not None:
        return _no_match("knowledge_catalog", atom)
    if _ce2._has_explicit_action_catalogue(cleaned):
        return _no_match("knowledge_catalog", atom)
    head, tail = cleaned.split(":", 1)
    head, tail = head.strip(), tail.strip()
    if not head or not tail:
        return _no_match("knowledge_catalog", atom)
    if _catalog_blocked_head(head, tail, cleaned):
        return _no_match("knowledge_catalog", atom)
    selector = catalog_selector()
    opened = _open_colon_catalog(cleaned)
    members = _catalog_members(tail)
    named = members if members is not None else _named_members_from_tail(tail)
    if opened:
        return _open_knowledge_catalog(atom, head, named, selector)
    if members is None:
        return _no_match("knowledge_catalog", atom)
    selected = _select_catalog_members(head, members, selector)
    if selected is None:
        reason = (
            AMBIGUOUS_CATALOG_ASSIGNMENT
            if _is_generic_head_selector(head, selector)
            else AMBIGUOUS_FRAME_CANDIDATES
        )
        return _reject_catalog(atom, reason)
    if selected == members:
        built = _catalog_phrase(head, tail, cleaned)
        if built is None:
            return _no_match("knowledge_catalog", atom)
        return _emit_knowledge_catalog(atom, built)
    used_tail = ", ".join(selected)
    built = _catalog_phrase(head, used_tail)
    if built is None:
        return _no_match("knowledge_catalog", atom)
    span = _coverage_span_for_part(atom, used_tail)
    if span is None:
        return _no_match("knowledge_catalog", atom)
    return _emit_knowledge_catalog(atom, built, span=span)


def _propose(builder_id: str, kind: FrameKind, helper, atom: SourceAtom) -> FrameCandidate:
    built = helper(atom.text)
    if not built:
        return _no_match(builder_id, atom)
    return _c5_candidate(builder_id, kind, lambda _text: built, atom)


def _propose_knowledge_noun_phrase(atom: SourceAtom) -> FrameCandidate:
    built = _noun_phrase(atom.text)
    if not built:
        return _no_match("knowledge_noun_phrase", atom)
    candidate = _c5_candidate(
        "knowledge_noun_phrase", FrameKind.KNOWLEDGE, lambda _text: built, atom
    )
    if not candidate.is_valid:
        return candidate
    _phrase, _action, obj, _cond = built
    if _covers_full_atom(atom, obj):
        return candidate
    span = _coverage_span_for_part(atom, obj)
    if span is None:
        return _no_match("knowledge_noun_phrase", atom)
    return replace(candidate, span=span)


def _register(builder_id: str, helper_name: str, kind: FrameKind) -> RegisteredBuilder:
    def propose(atom: SourceAtom) -> FrameCandidate:
        helper = globals()[helper_name]
        return _propose(builder_id, kind, helper, atom)

    return RegisteredBuilder(builder_id=builder_id, propose=propose)


KNOWLEDGE_REGISTRY: tuple[RegisteredBuilder, ...] = (
    _register("knowledge_question", "_question", FrameKind.KNOWLEDGE),
    _register("knowledge_definition", "_definition", FrameKind.DEFINITION),
    RegisteredBuilder(builder_id="knowledge_catalog", propose=_propose_knowledge_catalog),
    RegisteredBuilder(builder_id="knowledge_noun_phrase", propose=_propose_knowledge_noun_phrase),
)
