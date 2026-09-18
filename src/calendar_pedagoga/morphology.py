"""Fail-closed Russian morphology for already-rejected knowledge objects."""

from __future__ import annotations

import re

import pymorphy3

_MORPH = pymorphy3.MorphAnalyzer()
_NOMINAL_POS = frozenset({"NOUN", "ADJF", "PRTF"})
_PREP = frozenset(
    "в во на за к ко с со по из от до у о об обо при для через над под без".split()
)
_TOPIC_FALLBACK_RE = re.compile(r"(?i)по теме|материал")


def _core(word: str) -> str:
    return re.sub(r"^[«(\"]+|[»)\",;:.]+$", "", word)


def _restore(token: str, replacement: str) -> str:
    match = re.match(r"^([«(\"]*)(.*?)([»)\",;:.]*)$", token)
    if not match:
        return replacement
    body = replacement
    if match.group(2)[:1].isupper() and body:
        body = body[:1].upper() + body[1:]
    return f"{match.group(1)}{body}{match.group(3)}"


def _nominal_parses(word: str):
    core = _core(word)
    if not core:
        return []
    return [item for item in _MORPH.parse(core) if item.tag.POS in _NOMINAL_POS]


def parse_nominal(word: str):
    parses = _nominal_parses(word)
    return parses[0] if parses else None


def parse_head(word: str):
    """Prefer a plural noun parse so coordinated heads keep number."""

    parses = _nominal_parses(word)
    if not parses:
        return None
    nouns = [item for item in parses if item.tag.POS == "NOUN"]
    pool = nouns or parses
    plural = [item for item in pool if item.tag.number == "plur"]
    if plural:
        for preferred in ("accs", "nomn"):
            hit = [item for item in plural if preferred in item.tag]
            if hit:
                return hit[0]
        return plural[0]
    return pool[0]


def is_proven_acc(word: str) -> bool:
    """True when the surface form is a morphological accusative, not animate nominative."""

    parses = _nominal_parses(word)
    if not parses:
        return False
    best = parses[0]
    if "nomn" in best.tag and best.tag.animacy == "anim":
        return False
    if "nomn" in best.tag and best.tag.POS in {"ADJF", "PRTF"} and "accs" not in best.tag:
        return False
    if "accs" in best.tag:
        return True
    if (
        best.tag.POS == "NOUN"
        and best.tag.animacy == "anim"
        and best.tag.number == "plur"
        and best.tag.case == "gent"
    ):
        return True
    if (
        best.tag.POS in {"ADJF", "PRTF"}
        and best.tag.number == "plur"
        and best.tag.case in {"gent", "loct"}
    ):
        return True
    floor = best.score * 0.4
    for parsed in parses:
        if parsed.score < floor:
            continue
        if "accs" in parsed.tag:
            return True
        if "nomn" in parsed.tag and parsed.tag.animacy == "inan":
            inflected = parsed.inflect({"accs"})
            if inflected is not None and inflected.word.casefold() == _core(word).casefold():
                return True
    return False


def _inflect_acc(parsed, *, animacy=None):
    gram = {"accs"}
    if parsed.tag.number:
        gram.add(parsed.tag.number)
    if animacy and parsed.tag.POS in {"ADJF", "PRTF"}:
        gram.add(animacy)
    return parsed.inflect(gram) or parsed.inflect({"accs"})


def _object_head_indices(tokens: list[str]) -> list[int] | None:
    """Noun or substantivized-adjective heads, including coordinated conjuncts."""

    heads: list[int] = []
    index = 0
    while index < len(tokens):
        if _core(tokens[index]).casefold() in {"и", "или"}:
            index += 1
            continue
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        current = parse_nominal(tokens[index])
        following = parse_nominal(nxt) if nxt else None
        if (
            current is not None
            and current.tag.POS in {"ADJF", "PRTF"}
            and following is not None
            and following.tag.POS == "NOUN"
        ):
            index += 1
            continue
        head = parse_head(tokens[index])
        if head is None or head.tag.POS not in _NOMINAL_POS:
            return None
        heads.append(index)
        had_comma = tokens[index].endswith(",")
        index += 1
        if index < len(tokens) and _core(tokens[index]).casefold() in _PREP:
            break
        if had_comma:
            continue
        while index < len(tokens) and _core(tokens[index]).casefold() not in {"и", "или"}:
            if tokens[index].endswith(","):
                index += 1
                break
            index += 1
    return heads


def inflect_heads_only(obj: str) -> str | None:
    """Inflect only object heads to accusative; modifiers and dependents stay."""

    tokens = obj.split()
    heads = _object_head_indices(tokens)
    if not heads:
        return None
    out = list(tokens)
    for index in heads:
        parsed = parse_head(tokens[index])
        if parsed is None:
            return None
        inflected = _inflect_acc(parsed, animacy=parsed.tag.animacy)
        if inflected is None:
            return None
        out[index] = _restore(tokens[index], inflected.word)
    return " ".join(out)


def repaired_heads_proven(sentence: str) -> bool:
    match = re.match(r"(?i)^(характеризует|раскрывает)\s+(.+)$", sentence.strip())
    if not match:
        return False
    obj = match.group(2).rstrip(".")
    tokens = obj.split()
    heads = _object_head_indices(tokens)
    return bool(heads) and all(is_proven_acc(tokens[index]) for index in heads)


def repair_rejected_knowledge_sentence(sentence: str) -> str | None:
    """Head-only accusative repair for one already-rejected knowledge sentence."""

    text = sentence.strip()
    if _TOPIC_FALLBACK_RE.search(text):
        return None
    match = re.match(r"(?i)^(характеризует|раскрывает)\s+(.+)$", text)
    if not match:
        return None
    verb, obj = match.group(1), match.group(2)
    ending = ""
    if obj.endswith("."):
        obj = obj[:-1]
        ending = "."
    repaired_obj = inflect_heads_only(obj)
    if not repaired_obj:
        return None
    repaired = f"{verb} {repaired_obj}{ending}"
    if not repaired_heads_proven(repaired):
        return None
    return repaired
