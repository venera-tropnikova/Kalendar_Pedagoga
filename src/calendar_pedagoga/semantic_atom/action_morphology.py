"""Shadow-only proven ACTION-object accusative. Production must not import this."""

from __future__ import annotations

from calendar_pedagoga.morphology import (
    _MORPH,
    _PREP,
    _core,
    _inflect_acc,
    _nominal_parses,
    _object_head_indices,
    _restore,
    is_proven_acc,
    parse_head,
)

_VELAR = frozenset("кгх")
_CONJ = frozenset({"и", "или"})
_ADJF_POS = frozenset({"ADJF", "PRTF"})


def _lemmas_of(word: str) -> frozenset[str]:
    core = _core(word).casefold()
    if not core:
        return frozenset()
    return frozenset(item.normal_form for item in _MORPH.parse(core)) | {core}


def lemma_bound_to_source(form: str, source_token: str) -> bool:
    """True when the produced form shares a pymorphy lemma with the SOURCE token."""

    return bool(_lemmas_of(form) & _lemmas_of(source_token))


def _noun_parses(word: str):
    core = _core(word)
    if not core:
        return []
    return [item for item in _MORPH.parse(core) if item.tag.POS == "NOUN"]


def _competitive_nouns(word: str):
    nouns = _noun_parses(word)
    if not nouns:
        return []
    floor = max(item.score for item in nouns) * 0.4
    return [item for item in nouns if item.score >= floor]


def _competing_number_or_case(word: str) -> bool:
    """True when sing/plur or genitive vs direct-case readings both exist."""

    nouns = _noun_parses(word)
    if not nouns:
        return False
    numbers = {item.tag.number for item in nouns if item.tag.number}
    cases = {item.tag.case for item in nouns if item.tag.case}
    if len(numbers) > 1:
        return True
    return "gent" in cases and bool(cases & {"nomn", "accs"})


def _unique_gen_sg_parse(word: str):
    hits = [
        item
        for item in _noun_parses(word)
        if item.tag.number == "sing" and item.tag.case == "gent"
    ]
    lemmas = {item.normal_form for item in hits}
    if len(lemmas) != 1:
        return None
    return max(hits, key=lambda item: item.score)


def _velar_gen_i_to_u(word: str) -> str | None:
    """Proven 1st-declension gen.sg after velar: книги→книгу, страховки→страховку."""

    core = _core(word)
    low = core.casefold()
    if len(core) <= 3 or not low.endswith("и"):
        return None
    stem = core[:-1]
    if stem[-1:].casefold() not in _VELAR:
        return None
    return stem + "у"


def _is_fem_gen_sg_modifier(word: str) -> bool:
    core = _core(word)
    low = core.casefold()
    if not low.endswith(("ой", "ей")):
        return False
    for parsed in _nominal_parses(core):
        if parsed.tag.POS not in _ADJF_POS:
            continue
        if parsed.tag.number != "sing" or parsed.tag.gender != "femn":
            continue
        if parsed.tag.case in {"gent", "datv", "ablt", "loct"}:
            return True
    return False


def _surface_is_sing_gen_noun(word: str) -> bool:
    """True for a non-ambiguous singular genitive, never an -и nom/acc-plural form."""

    core = _core(word)
    if core.casefold().endswith("и"):
        return False
    competitive = _competitive_nouns(word)
    gent_sg = [
        item
        for item in competitive
        if item.tag.number == "sing" and item.tag.case == "gent"
    ]
    if not gent_sg:
        return False
    return not any(item.tag.number == "plur" for item in competitive)


def _inflect_adj_acc(word: str, *, number: str | None, gender: str | None) -> str | None:
    parses = [item for item in _nominal_parses(word) if item.tag.POS in _ADJF_POS]
    if not parses:
        return None
    gram = {"accs"}
    if number:
        gram.add(number)
    if gender:
        gram.add(gender)
    for parsed in parses:
        inflected = parsed.inflect(gram) or parsed.inflect({"accs"})
        if inflected is None:
            continue
        if lemma_bound_to_source(inflected.word, word) and is_proven_acc(inflected.word):
            return _restore(word, inflected.word)
    return None


def proven_noun_acc(
    word: str,
    *,
    fem_gen_sg_modifier: bool = False,
    parallel_sing_gen: bool = False,
    governed_genitive: bool = False,
) -> str | None:
    """Accusative of one SOURCE noun, or None when number/case is not proven."""

    core = _core(word)
    if not core:
        return None
    proves_sing_gen = fem_gen_sg_modifier or parallel_sing_gen or governed_genitive
    competing = _competing_number_or_case(core)
    if competing and not proves_sing_gen:
        return None

    velar = _velar_gen_i_to_u(core)
    gen_sg = _unique_gen_sg_parse(core)

    if velar is not None and proves_sing_gen:
        chosen = None
        if gen_sg is not None:
            inflected = _inflect_acc(gen_sg, animacy=gen_sg.tag.animacy)
            if inflected is not None and lemma_bound_to_source(inflected.word, core):
                chosen = inflected.word
        if chosen is None and lemma_bound_to_source(velar, core):
            chosen = velar
        if chosen is None:
            return None
        return _restore(word, chosen)

    if gen_sg is not None and (proves_sing_gen or not competing):
        inflected = _inflect_acc(gen_sg, animacy=gen_sg.tag.animacy)
        if inflected is not None and lemma_bound_to_source(inflected.word, core):
            return _restore(word, inflected.word)
        return None
    if not competing and is_proven_acc(core):
        return word
    return None


def proven_action_object_acc(obj: str, *, governed_genitive: bool = False) -> str | None:
    """Inflect ACTION-object heads to a SOURCE-lemma accusative, or None."""

    text = (obj or "").strip()
    if not text or ":" in text or ";" in text:
        return None
    tokens = text.split()
    heads = _object_head_indices(tokens)
    if not heads:
        return None
    parallel = any(_surface_is_sing_gen_noun(tokens[index]) for index in heads)
    out = list(tokens)
    for head in heads:
        pending = []
        index = head - 1
        while index >= 0 and index not in heads:
            token = tokens[index]
            folded = _core(token).casefold()
            if folded in _CONJ or folded in _PREP:
                break
            pending.append(index)
            index -= 1
        pending.reverse()
        fem_mod = any(_is_fem_gen_sg_modifier(tokens[index]) for index in pending)
        acc = proven_noun_acc(
            tokens[head],
            fem_gen_sg_modifier=fem_mod,
            parallel_sing_gen=parallel,
            governed_genitive=governed_genitive,
        )
        if acc is None:
            return None
        out[head] = acc
        parsed = parse_head(acc)
        number = parsed.tag.number if parsed is not None else None
        gender = parsed.tag.gender if parsed is not None else None
        for index in pending:
            if not any(
                item.tag.POS in _ADJF_POS for item in _nominal_parses(tokens[index])
            ):
                continue
            inflected = _inflect_adj_acc(tokens[index], number=number, gender=gender)
            if inflected is None:
                return None
            out[index] = inflected
    if not all(lemma_bound_to_source(out[index], tokens[index]) for index in range(len(tokens))):
        return None
    return " ".join(out)
