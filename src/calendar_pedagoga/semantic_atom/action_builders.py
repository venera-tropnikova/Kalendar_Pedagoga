"""Shadow builders for proven explicit actions. No new predicates."""

from __future__ import annotations

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

MISSING_OBJECT = "unconfirmed_action_object"


def _eligible_action_atom(text: str) -> bool:
    if not text or not str(text).strip():
        return False
    if _ce2._is_interrogative_clause(text):
        return False
    if _quoted_event_only(text):
        return False
    if _ce2._starts_with_action_finite(text):
        return True
    if _ce2._has_clause_initial_productive_head(text):
        return True
    token = _ce2._leading_activity_token(text)
    if not token:
        return False
    return bool(
        _ce2._is_explicit_action_head_token(token)
        or _ce2._is_action_head(token)
        or _ce2._is_action_finite_token(token)
        or _ce2._is_walk_word(token)
        or _ce2._is_travel_word(token)
        or _ce2._is_exercise_word(token)
        or _ce2._is_leading_form_activity(token)
        or _ce2._participation_lemma(token)
        or _ce2._nominal_activity_lemma(token)
        in _ce2._NOMINAL_PERFORM_LEMMAS
        or _ce2._nominal_activity_lemma(token) in {"помощь", "поездка"}
    )


def _quoted_event_only(text: str) -> bool:
    cleaned = _ce2._normalize_spaces(text)
    if ":" not in cleaned:
        return False
    head, tail = cleaned.split(":", 1)
    token = _ce2._leading_activity_token(head)
    if _ce2._is_explicit_action_head_token(token) or _ce2._has_clause_initial_productive_head(
        head
    ):
        return False
    return _ce2._remainder_is_quoted_label(tail)


def _confirmed_object(candidate: FrameCandidate) -> bool:
    if candidate.proposed_object.strip(" .") or candidate.proposed_complement.strip(" ."):
        return True
    result = candidate.proposed_result.strip()
    verb = _ce2._leading_finite_verb(result)
    if verb and result.casefold().startswith(verb.casefold()):
        rest = result[len(verb) :].strip(" .")
    else:
        rest = _ce2._drop_leading_verb(result).strip(" .")
    return bool(rest)


def _kind_for(text: str, action: str, phrase: str) -> FrameKind:
    if _ce2._creative_activity_result(text) or _ce2._finite_produce_result(text):
        return FrameKind.CREATIVE_PRODUCT
    verb = _ce2._leading_finite_verb(phrase).casefold()
    noun = _ce2._VERB_TO_VERBAL_NOUN.get(verb, action.casefold())
    if noun in {
        "рисование",
        "изготовление",
        "создание",
        "разработка",
        "конструирование",
    }:
        return FrameKind.CREATIVE_PRODUCT
    return FrameKind.ACTION


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


def _propose(builder_id: str, helper, atom: SourceAtom) -> FrameCandidate:
    if not _eligible_action_atom(atom.text):
        return _no_match(builder_id, atom)
    built = helper(atom.text)
    if not built:
        return _no_match(builder_id, atom)
    _phrase, action, _obj, _comp = built
    kind = _kind_for(atom.text, action, _phrase)
    candidate = _c5_candidate(builder_id, kind, lambda _text: built, atom)
    if candidate.is_valid and not _confirmed_object(candidate):
        return FrameCandidate(
            builder_id=builder_id,
            atom_id=atom.id,
            span=atom.span,
            source_fingerprint=atom.source_fingerprint,
            proposed_kind=kind,
            proposed_predicate=candidate.proposed_predicate,
            proposed_object=candidate.proposed_object,
            proposed_complement=candidate.proposed_complement,
            proposed_result=candidate.proposed_result,
            proposed_control=candidate.proposed_control,
            structural_evidence=StructuralEvidence(notes=(MISSING_OBJECT,)),
            lexical_check=candidate.lexical_check,
            confidence=CandidateConfidence.REJECTED,
            rejection_reason=MISSING_OBJECT,
        )
    return candidate


def _finite_produce(text: str):
    return _ce2._finite_produce_result(text)


def _explicit_action(text: str):
    return _ce2._explicit_action_reconstruction(text, theory_only=False)


def _nominal_activity(text: str):
    return _ce2._nominal_activity_result(text)


def _closed_form(text: str):
    return _ce2._closed_form_activity_result(text)


def _unconjugated_practice(text: str):
    return _ce2._unconjugated_practice_activity_result(text)


def _care_and_repair(text: str):
    hit = _ce2._care_and_repair_result(text)
    if hit is None:
        return None
    phrase, action, obj = hit
    return phrase, action, obj, ""


def _paired_shared_object(text: str):
    hit = _ce2._shared_object_after_paired_verbs(text)
    if hit is None:
        return None
    phrase, action, rest = hit
    obj, cond = _ce2._split_object_and_conditions(rest)
    return phrase, action, obj, cond


def _proven_finite(text: str):
    if _ce2._finite_produce_result(text):
        return None
    if not _ce2._starts_with_action_finite(text):
        return None
    verb = _ce2._leading_finite_verb(text)
    rest = _ce2._normalize_spaces(text)[len(verb) :].strip(" ,.")
    if not rest:
        return None
    obj_acc, cond = _ce2._complements_after_finite(rest)
    if not obj_acc.strip() and not cond.strip():
        return None
    phrase = _ce2._normalize_spaces(f"{verb} {obj_acc} {cond}".strip())
    action = _ce2._VERB_TO_VERBAL_NOUN.get(verb.casefold(), verb)
    obj, split = _ce2._split_object_and_conditions(rest)
    return phrase, action, obj or obj_acc, split or cond


def _walk_travel(text: str):
    cleaned = _ce2._normalize_spaces(text).strip(" ,.")
    _mods, rest = _ce2._leading_modifiers(cleaned.split())
    if not rest:
        return None
    head = _ce2._token_core(rest[0])
    remainder = " ".join(rest[1:]).strip()
    if not remainder:
        return None
    travel = _ce2._is_travel_word(head) and remainder.casefold().startswith("по")
    if not (_ce2._is_walk_word(head) or travel):
        return None
    phrase = _ce2._append_remainder(
        "совершает " + _ce2._decap_lexical(rest[0]), remainder
    )
    obj, cond = _ce2._split_object_and_conditions(remainder)
    return phrase, head.casefold(), obj, cond


def _exercise(text: str):
    cleaned = _ce2._normalize_spaces(text).strip(" ,.")
    _mods, rest = _ce2._leading_modifiers(cleaned.split())
    if not rest or not _ce2._is_exercise_word(_ce2._token_core(rest[0])):
        return None
    remainder = " ".join(rest[1:]).strip()
    if not remainder:
        return None
    phrase = _ce2._normalize_spaces(f"выполняет упражнения {remainder}")
    obj, cond = _ce2._split_object_and_conditions(remainder)
    return phrase, "упражнения", obj, cond


def _register(builder_id: str, helper) -> RegisteredBuilder:
    def propose(atom: SourceAtom) -> FrameCandidate:
        return _propose(builder_id, helper, atom)

    return RegisteredBuilder(builder_id=builder_id, propose=propose)


ACTION_REGISTRY: tuple[RegisteredBuilder, ...] = (
    _register("finite_produce", _finite_produce),
    _register("explicit_action", _explicit_action),
    _register("nominal_activity", _nominal_activity),
    _register("closed_form_activity", _closed_form),
    _register("unconjugated_practice", _unconjugated_practice),
    _register("care_and_repair", _care_and_repair),
    _register("paired_shared_object", _paired_shared_object),
    _register("proven_finite", _proven_finite),
    _register("walk_travel", _walk_travel),
    _register("exercise", _exercise),
)
