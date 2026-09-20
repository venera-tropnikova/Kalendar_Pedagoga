"""Shadow builders for proven explicit actions. No new predicates."""

from __future__ import annotations

from dataclasses import replace
import re

from calendar_pedagoga import content_engine_v2 as _ce2
from calendar_pedagoga.morphology import (
    _nominal_parses,
    _object_head_indices,
    _restore,
    parse_head,
)
from calendar_pedagoga.semantic_atom.canonicalize import canonicalize_text
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    NO_STRUCTURAL_MATCH,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import _c5_candidate, catalog_selector
from calendar_pedagoga.semantic_atom.lexical import shadow_lexical_violations
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    CoverageBinding,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceAtom,
    StructuralEvidence,
)

MISSING_OBJECT = "unconfirmed_action_object"
OPEN_ACTION_CATALOG = "open_action_catalog"
AMBIGUOUS_CATALOG_ASSIGNMENT = "ambiguous_catalog_assignment"
OPEN_TAIL_UNRESOLVED = "open_tail_unresolved"
_OPEN_CATALOG_RE = re.compile(
    r"(?i)(?:\b(?:и\s+другие|и\s+прочее|и\s+прочие|и\s+др\.?|"
    r"и\s+т\.?\s*д\.?|и\s+т\.?\s*п\.?|и\s+пр\.?)\b|\.\.\.|…)"
)
_DASH_RE = re.compile(r"[‐‑‒–—−\-]+")
_EVENT_NAME_QUOTE_RE = re.compile(r"«[^»]+»|\"[^\"]+\"|„[^“]+“")


def _eligible_action_atom(text: str) -> bool:
    if not text or not str(text).strip():
        return False
    if _ce2._is_interrogative_clause(text):
        return False
    if _open_colon_catalog(text):
        return False
    if _catalog_owned_atom(text):
        return False
    if _generic_head_blocks_other_builders(text):
        return False
    if _selector_narrows_catalog(text):
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


_QUOTED_ONLY_RE = re.compile(r'^[«"„“][^«»"]+[»"“”]$')


def _quoted_only_conjunct(part: str) -> bool:
    cleaned = _ce2._normalize_spaces(part).strip(" .")
    return bool(_QUOTED_ONLY_RE.fullmatch(cleaned))


def _open_list_member(part: str) -> bool:
    cleaned = _ce2._normalize_spaces(part).strip(" .,;:")
    if not cleaned:
        return True
    if _OPEN_CATALOG_RE.search(part):
        return True
    folded = cleaned.casefold()
    if folded in {
        "другие",
        "прочее",
        "прочие",
        "др",
        "др.",
        "т.д",
        "т.д.",
        "т.п",
        "т.п.",
        "...",
        "…",
    }:
        return True
    return "..." in part or "…" in part


def _split_series_parts(text: str) -> list[str]:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned:
        return []
    if ";" in cleaned and "(" not in cleaned:
        return [part.strip(" .") for part in cleaned.split(";") if part.strip(" .")]
    parts: list[str] = []
    buf: list[str] = []
    quote_depth = 0
    paren_depth = 0
    index = 0
    while index < len(cleaned):
        ch = cleaned[index]
        if ch == "(":
            paren_depth += 1
            buf.append(ch)
            index += 1
            continue
        if ch == ")" and paren_depth:
            paren_depth -= 1
            buf.append(ch)
            index += 1
            continue
        if ch in {"«", '"'} and quote_depth == 0:
            quote_depth += 1
            buf.append(ch)
            index += 1
            continue
        if ch in {"»", '"'} and quote_depth:
            quote_depth = max(0, quote_depth - 1)
            buf.append(ch)
            index += 1
            continue
        if paren_depth == 0 and quote_depth == 0 and cleaned[index : index + 2] == ", ":
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
            index += 2
            continue
        if paren_depth == 0 and quote_depth == 0 and cleaned[index : index + 3].casefold() == " и ":
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
            index += 3
            continue
        buf.append(ch)
        index += 1
    piece = "".join(buf).strip()
    if piece:
        parts.append(piece)
    return parts


def _ends_with_prepositional_object(part: str) -> bool:
    tokens = part.split()
    last_prep = max(
        (index for index, token in enumerate(tokens) if _ce2._is_preposition(token)),
        default=-1,
    )
    return last_prep >= 0 and last_prep >= len(tokens) - 3


def _independent_activity_conjuncts(text: str) -> list[str] | None:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned or ":" in cleaned:
        return None
    parts = _split_series_parts(cleaned)
    if len(parts) < 2:
        return None
    heads = []
    for part in parts:
        tokens = part.split()
        if not tokens or _ce2._is_preposition(tokens[0]) or _quoted_only_conjunct(part):
            continue
        if _eligible_action_atom(part):
            heads.append(part)
    if len(heads) < 2:
        return None
    if " и " in cleaned and (
        _ends_with_prepositional_object(heads[0])
        or _coordinates_trailing_object(heads[0], heads[1])
    ):
        return None
    return heads


def _has_own_complement(part: str) -> bool:
    tokens = part.split()
    _mods, rest = _ce2._leading_modifiers(tokens)
    if not rest:
        return False
    _head, remainder = _ce2._head_core_and_remainder(rest)
    return bool(remainder.strip())


def _coordinates_trailing_object(first: str, second: str) -> bool:
    if not _has_own_complement(first):
        return False
    tokens = second.split()
    return 1 <= len(tokens) <= 2 and not _ce2._is_preposition(tokens[0])


def _bare_head_remainder_glue(helper, text: str) -> bool:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned or ":" in cleaned:
        return False
    parts = _split_series_parts(cleaned)
    if len(parts) < 2:
        return False
    first, *rest = parts
    if not _eligible_action_atom(first):
        return False
    tokens = first.split()
    _mods, after_mods = _ce2._leading_modifiers(tokens)
    if after_mods:
        _head, remainder = _ce2._head_core_and_remainder(after_mods)
        if remainder.strip():
            return False
    leftovers = [
        part
        for part in rest
        if part.split()
        and not _ce2._is_preposition(part.split()[0])
        and not _quoted_only_conjunct(part)
        and not _eligible_action_atom(part)
    ]
    if not leftovers:
        return False
    built_full = helper(text)
    if not built_full or _finite_verb_count(built_full[0]) >= 2:
        return False
    folded = built_full[0].casefold()
    return any(part.casefold() in folded for part in leftovers)


def _leading_built_predicate(built: tuple[str, str, str, str]) -> str:
    verb = _ce2._leading_finite_verb(built[0])
    return (verb or "").casefold()


def _finite_verb_count(phrase: str) -> int:
    found = _ce2._FINITE_VERB_RE.findall(phrase or "")
    pieces = _ce2._split_coordinating_и_outside_quotes(phrase or "")
    leading = sum(1 for piece in pieces if _ce2._leading_finite_verb(piece))
    return max(len(found), leading)


def _merge_same_predicate_builts(
    builts: list[tuple[str, str, str, str]],
) -> tuple[str, str, str, str]:
    verb = _leading_built_predicate(builts[0])
    remnants: list[str] = []
    objects: list[str] = []
    conds: list[str] = []
    action = builts[0][1]
    for phrase, _action, obj, cond in builts:
        rest = _ce2._drop_leading_verb(phrase).strip(" .")
        if rest:
            remnants.append(rest)
        if obj.strip():
            objects.append(obj.strip())
        if cond.strip():
            conds.append(cond.strip())
    obj_joined = ", ".join(objects) if objects else ", ".join(remnants)
    cond_joined = ", ".join(dict.fromkeys(conds))
    phrase = _ce2._normalize_spaces(f"{verb} {', '.join(remnants)}".strip())
    return phrase, action, obj_joined, cond_joined


def _is_proven_loct(word: str) -> bool:
    nouns = [item for item in _nominal_parses(word) if item.tag.POS == "NOUN"]
    if not nouns:
        return False
    best = max(nouns, key=lambda item: item.score)
    if "loct" in best.tag:
        return True
    core = _ce2._token_core(word).casefold()
    for item in nouns:
        gram = {"loct"}
        if item.tag.number:
            gram.add(item.tag.number)
        inflected = item.inflect(gram) or item.inflect({"loct"})
        if inflected is not None and inflected.word.casefold() == core:
            return True
    return False


def _inflect_loct_phrase(phrase: str) -> str | None:
    tokens = _ce2._normalize_spaces(phrase).split()
    if not tokens:
        return None
    heads = _object_head_indices(tokens)
    if not heads:
        return None
    out = list(tokens)
    for index in heads:
        parsed = parse_head(tokens[index])
        if parsed is None:
            return None
        gram = {"loct"}
        if parsed.tag.number:
            gram.add(parsed.tag.number)
        inflected = parsed.inflect(gram) or parsed.inflect({"loct"})
        if inflected is None:
            return None
        out[index] = _restore(tokens[index], inflected.word)
    head = out[heads[0]]
    if not _is_proven_loct(head):
        return None
    return _ce2._normalize_spaces(" ".join(out))


def _governing_prep(built: tuple[str, str, str, str]) -> str:
    rest = _ce2._drop_leading_verb(built[0]).strip(" .")
    tokens = rest.split()
    if tokens and _ce2._is_preposition(tokens[0]):
        return tokens[0].casefold()
    return ""


def _other_activity_conjunct(part: str) -> bool:
    if _eligible_action_atom(part):
        return True
    if _ce2._is_foreign_activity_np(part):
        return True
    if _ce2._leading_ways_catalogue(part):
        return True
    token = _ce2._leading_activity_token(part)
    if token and _ce2._token_is_pupil_activity(token):
        return True
    tokens = _ce2._normalize_spaces(part).split()
    if not tokens:
        return True
    head = token or tokens[0]
    if _ce2._technique_catalogue_head(head):
        return True
    return bool(_ce2._conjugate_verbal_noun(head))


def _part_used_in_built(part: str, built: tuple[str, str, str, str]) -> bool:
    blob = _ce2._normalize_spaces(" ".join(built)).casefold()
    cleaned = _ce2._normalize_spaces(part).strip(" .").casefold()
    if cleaned and cleaned in blob:
        return True
    for token in cleaned.split():
        core = _ce2._token_core(token).casefold()
        if len(core) < 4 or core in {"при", "для", "без"}:
            continue
        if core in blob or core[:5] in blob:
            return True
    return False


def _case_proven_in_built(built: tuple[str, str, str, str]) -> bool:
    prep = _governing_prep(built)
    rest = _ce2._drop_leading_verb(built[0]).strip(" .")
    tokens = rest.split()
    if prep:
        if not tokens or tokens[0].casefold() != prep:
            return False
        obj_tokens = tokens[1:]
        heads = _object_head_indices(obj_tokens)
        if not heads:
            return False
        return _is_proven_loct(obj_tokens[heads[0]])
    obj = (built[2] or rest).strip()
    obj_tokens = obj.split()
    heads = _object_head_indices(obj_tokens)
    if not heads:
        return False
    from calendar_pedagoga.morphology import is_proven_acc

    return is_proven_acc(obj_tokens[heads[0]])


def _reconstruct_conjunct_in_context(
    part: str, anchor: tuple[str, str, str, str]
) -> str | None:
    cleaned = _ce2._normalize_spaces(part).strip(" .")
    if not cleaned:
        return None
    verb = _leading_built_predicate(anchor)
    if not verb:
        return None
    noun = _ce2._VERB_TO_VERBAL_NOUN.get(verb, "")
    prep = _governing_prep(anchor)
    if prep:
        governed = _inflect_loct_phrase(cleaned)
        if not governed:
            return None
        if noun:
            return f"{noun} {prep} {governed}"
        return f"{verb} {prep} {governed}"
    from calendar_pedagoga.morphology import inflect_heads_only

    acc = inflect_heads_only(cleaned) or _ce2._inflect_object_phrase(cleaned, case="acc")
    if not acc:
        return None
    if noun:
        return f"{noun} {acc}"
    return f"{verb} {acc}"


def _tuple_from_phrase(phrase: str) -> tuple[str, str, str, str] | None:
    cleaned = _ce2._normalize_spaces(phrase).strip(" .")
    if not cleaned:
        return None
    verb = _ce2._leading_finite_verb(cleaned)
    if not verb:
        return None
    rest = _ce2._drop_leading_verb(cleaned).strip(" .")
    obj, cond = _ce2._split_object_and_conditions(rest)
    action = _ce2._VERB_TO_VERBAL_NOUN.get(verb.casefold(), verb)
    return cleaned, action, obj, cond


def _same_construction(
    anchor: tuple[str, str, str, str],
    checked: tuple[str, str, str, str],
    source: str,
    reconstructed: str,
) -> bool:
    if _leading_built_predicate(anchor) != _leading_built_predicate(checked):
        return False
    if _governing_prep(anchor) != _governing_prep(checked):
        return False
    return _kind_for(source, anchor[1], anchor[0]) == _kind_for(
        reconstructed, checked[1], checked[0]
    )


def _helper_check_conjunct(
    helper, part: str, anchor: tuple[str, str, str, str], source: str
):
    reconstructed = _reconstruct_conjunct_in_context(part, anchor)
    if not reconstructed:
        return None
    probes = [
        helper(reconstructed),
        _ce2._explicit_action_reconstruction(reconstructed, theory_only=False),
        _ce2._closed_form_activity_result(reconstructed),
        _tuple_from_phrase(_ce2._participatory_result_from_clause(reconstructed) or ""),
    ]
    for checked in probes:
        if not checked or not checked[0].strip():
            continue
        if not _same_construction(anchor, checked, source, reconstructed):
            continue
        if not _case_proven_in_built(checked):
            continue
        return checked
    return None


def _can_anchor_list_member(part: str) -> bool:
    if not _eligible_action_atom(part):
        return False
    token = _ce2._leading_activity_token(part)
    return bool(
        _ce2._starts_with_action_finite(part)
        or _ce2._is_leading_form_activity(token)
        or _ce2._is_walk_word(token)
        or _ce2._is_travel_word(token)
        or _ce2._is_exercise_word(token)
        or _ce2._is_explicit_action_head_token(token)
        or _ce2._participation_lemma(token)
    )


def _list_has_eligible_member(text: str) -> bool:
    if _eligible_action_atom(text):
        return True
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned or ":" in cleaned:
        return False
    return any(
        _can_anchor_list_member(part)
        for part in _split_series_parts(cleaned)
        if part and not _open_list_member(part)
    )


def _expand_homogeneous_list_conjuncts(helper, text: str, built_full):
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned or ":" in cleaned:
        return built_full
    if ";" in cleaned and "(" not in cleaned:
        return built_full
    parts = _split_series_parts(cleaned)
    if len(parts) < 2:
        return built_full
    series = _independent_activity_conjuncts(text)
    proven: list[tuple[str, tuple[str, str, str, str]]] = []
    if series:
        for part in series:
            built = helper(part)
            if not built:
                continue
            if not _leading_built_predicate(built):
                continue
            proven.append((part, built))
    if built_full and _finite_verb_count(built_full[0]) >= 2:
        return built_full
    if proven:
        first_pred = _leading_built_predicate(proven[0][1])
        proven = [
            item for item in proven if _leading_built_predicate(item[1]) == first_pred
        ]
        anchor_part, anchor = proven[0]
    elif built_full:
        anchor = built_full
        anchor_part = next(
            (part for part in parts if _part_used_in_built(part, built_full)),
            None,
        )
        if anchor_part is None:
            return built_full
        proven = [(anchor_part, anchor)]
    else:
        return None

    used = {item[0] for item in proven}
    if _has_own_complement(proven[0][0]):
        chosen = [item[1] for item in proven]
        if not chosen:
            return built_full
        if len(chosen) == 1:
            return chosen[0]
        return _merge_same_predicate_builts(chosen)
    for part in parts:
        if part in used or _open_list_member(part) or _quoted_only_conjunct(part):
            continue
        tokens = part.split()
        if tokens and _ce2._is_preposition(tokens[0]):
            continue
        if _part_used_in_built(part, proven[0][1]):
            continue
        if _eligible_action_atom(part):
            continue
        if _other_activity_conjunct(part):
            continue
        checked = _helper_check_conjunct(helper, part, proven[0][1], text)
        if not checked:
            continue
        proven.append((part, checked))
        used.add(part)

    by_part = {item[0]: item[1] for item in proven}
    contiguous: list[tuple[str, str, str, str]] = []
    run_broken = False
    for part in parts:
        if _open_list_member(part):
            run_broken = True
            continue
        built = by_part.get(part)
        if built is None:
            run_broken = True
            continue
        if not run_broken:
            contiguous.append(built)
    chosen = contiguous
    if not chosen:
        return built_full
    if len(chosen) == 1:
        return chosen[0]
    return _merge_same_predicate_builts(chosen)


def _compatible_series_built(helper, text: str):
    if _bare_head_remainder_glue(helper, text):
        return None
    series = _independent_activity_conjuncts(text)
    built_full = helper(text)
    if not series:
        return _expand_homogeneous_list_conjuncts(helper, text, built_full)
    proven: list[tuple[str, str, tuple[str, str, str, str]]] = []
    for part in series:
        built = helper(part)
        if not built:
            continue
        predicate = _leading_built_predicate(built)
        if not predicate:
            continue
        proven.append((part, predicate, built))
    if built_full and _finite_verb_count(built_full[0]) >= 2:
        return built_full
    if not proven:
        return _expand_homogeneous_list_conjuncts(helper, text, built_full)
    first_pred = proven[0][1]
    same = [item[2] for item in proven if item[1] == first_pred]
    merged = same[0] if len(same) == 1 else _merge_same_predicate_builts(same)
    return _expand_homogeneous_list_conjuncts(helper, text, merged)


def _propose(builder_id: str, helper, atom: SourceAtom) -> FrameCandidate:
    if not _list_has_eligible_member(atom.text):
        return _no_match(builder_id, atom)
    built = _compatible_series_built(helper, atom.text)
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


def _open_colon_catalog(text: str) -> bool:
    cleaned = _ce2._normalize_spaces(text)
    if ":" not in cleaned:
        return False
    _head, tail = cleaned.split(":", 1)
    return bool(_OPEN_CATALOG_RE.search(tail))


def _action_catalog_head_ok(head: str) -> bool:
    cleaned = _ce2._normalize_spaces(head).strip(" .")
    if not cleaned or _ce2._is_interrogative_clause(cleaned):
        return False
    if _ce2._knowledge_clause_result(cleaned, theory_only=True):
        return False
    if _ce2._has_explicit_action_catalogue(f"{cleaned}: x, y"):
        return True
    token = _ce2._leading_activity_token(cleaned)
    if not token:
        return False
    if _ce2._is_theory_knowledge_token(token) and not (
        _ce2._is_explicit_action_head_token(token)
        or _ce2._is_leading_form_activity(token)
        or _ce2._is_action_head(token)
    ):
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
        or _ce2._nominal_activity_lemma(token) in _ce2._NOMINAL_PERFORM_LEMMAS
        or _ce2._nominal_activity_lemma(token) in {"помощь", "поездка"}
        or _ce2._has_clause_initial_productive_head(cleaned)
        or _ce2._starts_with_action_finite(cleaned)
    )


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


def _is_generic_head_selector(head: str, selector: str) -> bool:
    if not selector.strip():
        return False
    folded = _fold_span(selector)
    return folded == _fold_span(head) or folded == _fold_span(f"{head}:")


def _generic_head_blocks_other_builders(text: str) -> bool:
    selector = catalog_selector()
    if not selector.strip():
        return False
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if ":" not in cleaned:
        return False
    head, tail = cleaned.split(":", 1)
    head, tail = head.strip(), tail.strip()
    if not _action_catalog_head_ok(head):
        return False
    if not _is_generic_head_selector(head, selector):
        return False
    members = _catalog_members(tail) or _named_members_from_tail(tail)
    return len(members) >= 2 or _open_colon_catalog(cleaned)


def _fold_span(text: str) -> str:
    return canonicalize_text(_DASH_RE.sub("-", text)).casefold()


def _selector_narrows_catalog(text: str) -> bool:
    selector = catalog_selector()
    if not selector.strip() or ":" not in text:
        return False
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if ":" not in cleaned:
        return False
    head, tail = cleaned.split(":", 1)
    head, tail = head.strip(), tail.strip()
    if not _action_catalog_head_ok(head):
        return False
    members = _catalog_members(tail)
    if members is None:
        return False
    selected = _select_catalog_members(head, members, selector)
    return selected is not None and selected != members


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


def _reconstruct_catalog_text(text: str):
    for helper in (
        _finite_produce,
        _explicit_action,
        _nominal_activity,
        _closed_form,
        _unconjugated_practice,
        _care_and_repair,
        _paired_shared_object,
        _proven_finite,
        _walk_travel,
        _exercise,
    ):
        built = helper(text)
        if built and built[0]:
            return built
    return None


def _catalog_owned_atom(text: str) -> bool:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if ":" not in cleaned:
        return False
    head, tail = cleaned.split(":", 1)
    head, tail = head.strip(), tail.strip()
    if not _action_catalog_head_ok(head):
        return False
    if _open_colon_catalog(cleaned):
        return True
    members = _catalog_members(tail)
    return members is not None and len(members) >= 2


def _tail_items_kept(phrase: str, tail: str) -> bool:
    items = _split_catalog_members(tail) or _named_members_from_tail(tail)
    if not items:
        folded_tail = canonicalize_text(tail).casefold()
        return bool(folded_tail) and folded_tail in canonicalize_text(phrase).casefold()
    folded = canonicalize_text(phrase).casefold()
    return all(canonicalize_text(item).casefold() in folded for item in items)


def _with_catalog_tail(phrase: str, tail: str) -> str:
    cleaned_tail = _ce2._normalize_spaces(tail).strip(" .")
    if not cleaned_tail:
        return phrase
    if _tail_items_kept(phrase, cleaned_tail):
        return phrase
    return _ce2._append_remainder(phrase.rstrip(" ."), ": " + cleaned_tail)


def _reconstruct_action_catalog(head: str, tail: str, original: str):
    built = _reconstruct_catalog_text(head) or _reconstruct_catalog_text(original)
    if not built:
        return None
    phrase, action, obj, cond = built
    phrase = _with_catalog_tail(phrase, tail)
    if tail and not _tail_items_kept(obj or "", tail):
        obj = tail
    return phrase, action, obj, cond


def _reject_catalog(atom: SourceAtom, reason: str) -> FrameCandidate:
    return FrameCandidate(
        builder_id="action_catalog",
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


def _action_catalog(atom: SourceAtom) -> FrameCandidate:
    raw = _ce2._normalize_spaces(atom.text)
    cleaned = raw.strip(" .")
    if ":" not in cleaned:
        return _no_match("action_catalog", atom)
    if _ce2._is_interrogative_clause(cleaned):
        return _no_match("action_catalog", atom)
    if _ce2._knowledge_clause_result(cleaned, theory_only=True):
        return _no_match("action_catalog", atom)
    head, tail = cleaned.split(":", 1)
    head, tail = head.strip(), tail.strip()
    if not head or not tail:
        return _no_match("action_catalog", atom)
    if not _action_catalog_head_ok(head):
        return _no_match("action_catalog", atom)
    selector = catalog_selector()
    opened = _open_colon_catalog(cleaned)
    members = _catalog_members(tail)
    named = members if members is not None else _named_members_from_tail(tail)
    if opened:
        return _open_action_catalog(atom, head, named, selector)
    if members is None:
        return _no_match("action_catalog", atom)
    selected = _select_catalog_members(head, members, selector)
    if selected is None:
        reason = (
            AMBIGUOUS_CATALOG_ASSIGNMENT
            if _is_generic_head_selector(head, selector)
            else AMBIGUOUS_FRAME_CANDIDATES
        )
        return _reject_catalog(atom, reason)
    if selected == members:
        probe = raw
        used_tail = tail
    else:
        used_tail = ", ".join(selected)
        probe = _ce2._normalize_spaces(f"{head}: {used_tail}")
    return _emit_catalog_frame(atom, head, used_tail, probe)


def _open_action_catalog(
    atom: SourceAtom,
    head: str,
    named: list[str],
    selector: str,
) -> FrameCandidate:
    selected = _select_catalog_members(head, named, selector) if named else None
    if named and selected is None and _is_generic_head_selector(head, selector):
        built = _reconstruct_catalog_text(head)
        if not built:
            return _reject_catalog(atom, AMBIGUOUS_CATALOG_ASSIGNMENT)
        return _emit_built_catalog(atom, head, built, notes=(OPEN_TAIL_UNRESOLVED,))
    if named and selected is None:
        return _reject_catalog(atom, AMBIGUOUS_CATALOG_ASSIGNMENT)
    if named and selected and selected != named:
        used_tail = ", ".join(selected)
        probe = _ce2._normalize_spaces(f"{head}: {used_tail}")
        return _emit_catalog_frame(
            atom, head, used_tail, probe, notes=(OPEN_TAIL_UNRESOLVED,)
        )
    if named and not selector.strip():
        used_tail = ", ".join(named)
        probe = _ce2._normalize_spaces(f"{head}: {used_tail}")
        return _emit_catalog_frame(
            atom, head, used_tail, probe, notes=(OPEN_TAIL_UNRESOLVED,)
        )
    built = _reconstruct_catalog_text(head)
    if not built:
        return _reject_catalog(atom, OPEN_ACTION_CATALOG)
    return _emit_built_catalog(atom, head, built, notes=(OPEN_TAIL_UNRESOLVED,))


def _emit_catalog_frame(
    atom: SourceAtom,
    head: str,
    used_tail: str,
    probe: str,
    notes: tuple[str, ...] = (),
) -> FrameCandidate:
    built = _reconstruct_action_catalog(head, used_tail, probe)
    if not built:
        return _no_match("action_catalog", atom)
    return _emit_built_catalog(atom, probe, built, notes=notes)


def _emit_built_catalog(
    atom: SourceAtom,
    probe: str,
    built: tuple[str, str, str, str],
    notes: tuple[str, ...] = (),
) -> FrameCandidate:
    _phrase, action, _obj, _comp = built
    kind = _kind_for(probe, action, _phrase)
    candidate = _c5_candidate("action_catalog", kind, lambda _text: built, atom)
    extra = notes
    if candidate.is_valid and not _confirmed_object(candidate):
        return FrameCandidate(
            builder_id="action_catalog",
            atom_id=atom.id,
            span=atom.span,
            source_fingerprint=atom.source_fingerprint,
            proposed_kind=kind,
            proposed_predicate=candidate.proposed_predicate,
            proposed_object=candidate.proposed_object,
            proposed_complement=candidate.proposed_complement,
            proposed_result=candidate.proposed_result,
            proposed_control=candidate.proposed_control,
            structural_evidence=StructuralEvidence(notes=(MISSING_OBJECT, *extra)),
            lexical_check=candidate.lexical_check,
            confidence=CandidateConfidence.REJECTED,
            rejection_reason=MISSING_OBJECT,
        )
    if extra and candidate.is_valid:
        return FrameCandidate(
            builder_id=candidate.builder_id,
            atom_id=candidate.atom_id,
            span=candidate.span,
            source_fingerprint=candidate.source_fingerprint,
            proposed_kind=candidate.proposed_kind,
            proposed_predicate=candidate.proposed_predicate,
            proposed_object=candidate.proposed_object,
            proposed_complement=candidate.proposed_complement,
            proposed_result=candidate.proposed_result,
            proposed_control=candidate.proposed_control,
            structural_evidence=StructuralEvidence(
                notes=(*candidate.structural_evidence.notes, *extra)
            ),
            lexical_check=candidate.lexical_check,
            confidence=candidate.confidence,
            rejection_reason=candidate.rejection_reason,
        )
    return candidate


def _is_closed_event_name(member: str) -> bool:
    cleaned = _ce2._normalize_spaces(member).strip(" .")
    if not cleaned:
        return False
    if ":" in cleaned or ";" in cleaned:
        return False
    if _ce2._unit_has_action_head(cleaned) or _ce2._FINITE_VERB_RE.search(cleaned):
        return False
    quoted = list(_EVENT_NAME_QUOTE_RE.finditer(cleaned))
    leftover = _EVENT_NAME_QUOTE_RE.sub(" ", cleaned)
    leftover = _ce2._normalize_spaces(leftover).strip(" ,.;:—–-")
    if quoted:
        return not leftover
    tokens = [token for token in cleaned.split() if token]
    if not (1 <= len(tokens) <= 4):
        return False
    first = tokens[0]
    if _ce2._is_preposition(first):
        return False
    return first[:1].isupper()


def _closed_event_tail(text: str) -> str | None:
    cleaned = _ce2._normalize_spaces(text).strip(" .")
    if not cleaned or ":" in cleaned or ";" in cleaned:
        return None
    if _OPEN_CATALOG_RE.search(cleaned):
        return None
    if _ce2._unit_has_action_head(cleaned):
        return None
    if not _ce2._is_dependent_catalog_unit(cleaned):
        return None
    members = _split_catalog_members(cleaned)
    if not members:
        return None
    for member in members:
        if not member or member not in cleaned:
            return None
        if not _is_closed_event_name(member):
            return None
    return cleaned


def attach_event_tails(
    source: str,
    atoms: tuple[SourceAtom, ...],
    frames: list[SemanticFrame],
    bindings: list[CoverageBinding],
) -> tuple[list[SemanticFrame], list[CoverageBinding]]:
    """Join a following closed event-name list onto a proven activity-head."""

    if len(atoms) != len(frames) or len(frames) != len(bindings):
        return frames, bindings
    body = source or ""
    for index, atom in enumerate(atoms[:-1]):
        frame = frames[index]
        nxt = atoms[index + 1]
        if frame.status is not ObjectStatus.PROVEN:
            continue
        if frame.kind is not FrameKind.ACTION:
            continue
        if frames[index + 1].status is ObjectStatus.PROVEN:
            continue
        if ":" in atom.text or ";" in atom.text:
            continue
        if not canonicalize_text(atom.text).endswith("."):
            continue
        gap = body[atom.span.end : nxt.span.start]
        if ";" in gap or ":" in gap or (gap and not gap.isspace()):
            continue
        tail = _closed_event_tail(nxt.text)
        if not tail:
            continue
        phrase = (frame.projected_result or "").rstrip(" .")
        if not phrase:
            continue
        folded = phrase.casefold()
        members = _split_catalog_members(tail) or ()
        if members and all(member.casefold() in folded for member in members):
            continue
        result = _ce2._cap_sentence(_ce2._append_remainder(phrase, ": " + tail))
        violations = shadow_lexical_violations(
            source=body,
            result=result,
            control=frame.projected_control,
        )
        if violations:
            continue
        head_src = _ce2._normalize_spaces(atom.text).rstrip(" .")
        source_clause = f"{head_src}: {tail}"
        span = replace(frame.span, end=nxt.span.end)
        note = frame.provenance.note
        extra = "event_tail:" + tail
        frames[index] = replace(
            frame,
            span=span,
            projected_result=result,
            object=source_clause,
            provenance=Provenance(
                adapter=frame.provenance.adapter,
                role=frame.provenance.role,
                clause_index=frame.provenance.clause_index,
                note=f"{note};{extra}" if note else extra,
            ),
        )
        bindings[index] = replace(bindings[index], span=span)
    return frames, bindings


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
    RegisteredBuilder(builder_id="action_catalog", propose=_action_catalog),
)
