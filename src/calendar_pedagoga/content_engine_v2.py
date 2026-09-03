"""Content Engine 2.0: детерминированные поля занятия без ИИ.

Параллельный модуль. Content Engine 1.0 не меняет.
Подключается к pipeline только через внутренний флаг USE_CONTENT_ENGINE_V2.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
from calendar_pedagoga.lesson_content import (
    _cap_sentence,
    _clean_source_phrase,
    _clause_units,
    _dominant_label,
    _line_form_scores,
    _normalize_spaces,
    _shorten_clause,
    _split_explicit_practice,
    _week_result_source,
    derive_lesson_type,
)


@dataclass(frozen=True)
class ActionFrame:
    clause: str
    action: str
    object: str
    conditions: str


@dataclass(frozen=True)
class ContentEngineV2Result:
    frame: ActionFrame
    lesson_type: str
    planned_result: str
    assessment_method: str
    theory_text: str
    practice_text: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LessonContentV2Row:
    source: CalendarContentRow
    theory_text: str
    practice_text: str
    lesson_type: str
    planned_result: str
    assessment_method: str
    action: str
    object: str
    conditions: str
    warnings: tuple[str, ...]


# Универсальные отглагольные пары (морфология, не предмет).
_VERBAL_NOUN_TO_VERB: dict[str, str] = {
    "анализ": "анализирует",
    "выбор": "выбирает",
    "выполнение": "выполняет",
    "знакомство": "знакомится",
    "изготовление": "изготавливает",
    "измерение": "измеряет",
    "изучение": "изучает",
    "конструирование": "конструирует",
    "наблюдение": "наблюдает",
    "оказание": "оказывает",
    "определение": "определяет",
    "организация": "организует",
    "ориентирование": "ориентирует",
    "отработка": "отрабатывает",
    "подбор": "подбирает",
    "подготовка": "подготавливает",
    "подгонка": "подгоняет",
    "посещение": "посещает",
    "постановка": "ставит",
    "построение": "строит",
    "приготовление": "готовит",
    "применение": "применяет",
    "проверка": "проверяет",
    "проведение": "проводит",
    "развертывание": "развертывает",
    "разжигание": "разжигает",
    "разработка": "разрабатывает",
    "разучивание": "разучивает",
    "расчет": "рассчитывает",
    "расчёт": "рассчитывает",
    "решение": "решает",
    "рисование": "рисует",
    "свертывание": "свертывает",
    "смешивание": "смешивает",
    "составление": "составляет",
    "укладка": "укладывает",
    "упаковка": "упаковывает",
    "фасовка": "фасует",
    "чтение": "читает",
    "закупка": "закупает",
}

_KNOWLEDGE_NOUNS = {
    "история",
    "организация",
    "понятие",
    "значение",
    "роль",
    "сведения",
    "характеристика",
    "виды",
    "биография",
}

# Состояние/знание, не действие учащегося (морфология, не предмет).
_STATE_OR_KNOWLEDGE_LEMMAS = _KNOWLEDGE_NOUNS | {
    "значение",
    "понятие",
    "сведение",
    "сведения",
    "умение",
    "мнение",
    "состояние",
    "влияние",
    "явление",
    "положение",
}

_EFFECT_FRAME_RE = re.compile(
    r"(?i)под воздействием|под влиянием|в результате\s+|"
    r"влияние\s+\w+\s+на|"
    r"^(совершенствование|укрепление|улучшение)\s+"
    r"(функций|функции|здоровья|организма|работоспособности)"
)

_POSSESSIVE_GEN_TO_ACC = {
    "своего": "свой",
    "своей": "свою",
    "своих": "свои",
    "моего": "мой",
    "твоего": "твой",
    "нашего": "наш",
    "вашего": "ваш",
}

_PREPOSITIONS = {
    "в",
    "во",
    "на",
    "по",
    "при",
    "с",
    "со",
    "для",
    "через",
    "к",
    "ко",
    "от",
    "из",
    "у",
    "о",
    "об",
    "между",
    "перед",
    "над",
    "под",
}

_PERFORM_STEMS = (
    "укладк",
    "разжиган",
    "развертыван",
    "свертыван",
    "оказан",
    "установ",
    "отработк",
    "упражнен",
)

_PRODUCE_STEMS = (
    "составлен",
    "изготовлен",
    "разработ",
    "рисован",
    "разучиван",
    "решен",
    "подготовк",
)

_FORM_STEMS = (
    "викторин",
    "экскурси",
    "прогул",
    "игр",
)

_STUDENT_CONDUCTS_QUIZ_RE = re.compile(
    r"(?i)(?:учащ\w+|обучаем\w+|дет\w+).{0,24}провод|"
    r"проводят\s+викторин|"
    r"проведен\w+\s+учащ"
)

_VERBAL_NOUN_FIND_RE = re.compile(
    r"(?i)(?<![А-Яа-яЁё])("
    + "|".join(sorted((re.escape(k) for k in _VERBAL_NOUN_TO_VERB), key=len, reverse=True))
    + r")(?![А-Яа-яЁё])"
)

_ACTIVITY_START_RE = re.compile(
    r"(?i)^(упражнен\w*|прогул\w*|экскурси\w*|викторин\w*)"
)


def _has_stem(text: str, stems: tuple[str, ...]) -> bool:
    low = text.casefold()
    return any(stem in low for stem in stems)


def _word_tokens(text: str) -> list[str]:
    return [token for token in re.split(r"(\s+)", text) if token]


def _is_preposition(word: str) -> bool:
    return word.casefold().strip(".,;:()") in _PREPOSITIONS


def _strip_punct_word(word: str) -> tuple[str, str, str]:
    match = re.match(r"^(\()?(.*?)([).,;:]+)?$", word)
    if not match:
        return "", word, ""
    return match.group(1) or "", match.group(2), match.group(3) or ""


def _is_adjective(word: str) -> bool:
    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE)
    if re.search(r"(?i)(?:ение|ание|яние|ений|аний|яний|ций)$", core):
        return False
    if re.search(r"(?i)(?:ностей|телей|ателей)$", core):
        return False
    return bool(
        re.search(
            r"(?i)(?:ое|ее|ая|яя|ый|ой|ий|ые|ие|ых|их|ого|его|ому|ему|"
            r"ую|юю|ым|им|ом|ем|ей)$",
            core,
        )
    ) and len(core) > 3


def _noun_gen_to_acc(word: str) -> str:
    """Грамматический переход типичного род. → вин. без словарных объектов."""

    if not word or word.casefold() in {"меню", "кофе"}:
        return word
    if re.search(r"(?i)\d", word) or word.endswith("."):
        return word

    low = word.casefold()
    if low.endswith(("ую", "юю", "ию")):
        return word
    if low.endswith("ений") and len(word) > 5:
        return word[:-2] + "ия"
    if low.endswith(("ов", "ев", "ёв")) and len(word) > 4:
        stem = word[:-2]
        last = stem[-1:].casefold()
        if last in "аеёиоуыэюякгхжчшщц":
            return stem + "и"
        return stem + "ы"
    if low.endswith("ей") and len(word) > 4:
        stem = word[:-2]
        if stem.casefold().endswith("и"):
            return stem + "я"
        return stem + "и"
    if low.endswith("ения") and len(word) > 5:
        return word[:-1] + "е"
    if low.endswith("ния") and len(word) > 5:
        return word[:-1] + "е"
    if low.endswith("ости") and len(word) > 5:
        return word[:-1] + "ь"
    if low.endswith("а") and len(word) > 3:
        stem = word[:-1]
        if stem.casefold().endswith("к") and len(stem) >= 2:
            before_k = stem[-2].casefold()
            if before_k not in "аеёиоуыэюя":
                return stem[:-1] + "ок"
        return stem
    if low.endswith("я") and len(word) > 3:
        return word[:-1] + "ь"
    if low.endswith("ы") and len(word) > 3:
        return word[:-1] + "у"
    if low.endswith("и") and len(word) > 3:
        stem = word[:-1]
        last = stem[-1:].casefold()
        if last in "кгх":
            return stem + "у"
        if last in "жчшщ" or stem.casefold().endswith("омощ"):
            return stem + "ь"
        if stem.casefold().endswith("ост"):
            return stem + "ь"
        return stem + "у"
    if low.endswith("ок") and len(word) > 5 and word[-3].casefold() not in "аеёиоуыэюя":
        return word[:-2] + "ки"
    if re.search(r"(?i)[аеёиоуыэюя][шж]$", low) and len(word) > 4:
        return word + "и"
    return word


def _noun_nom_to_acc(word: str) -> str:
    low = word.casefold()
    if low.endswith("ия") and len(word) > 3:
        return word[:-2] + "ию"
    if low.endswith("я") and not low.endswith("ия"):
        return word[:-1] + "ю"
    if low.endswith("а"):
        return word[:-1] + "у"
    return word


def _match_caps(src: str, dst: str) -> str:
    if src[:1].isupper() and src[1:].islower():
        return dst[:1].upper() + dst[1:]
    if src.isupper():
        return dst.upper()
    return dst


def _adj_to_acc(word: str, *, plural: bool, gender: str = "m") -> str:
    low = word.casefold()
    if low in _POSSESSIVE_GEN_TO_ACC:
        return _match_caps(word, _POSSESSIVE_GEN_TO_ACC[low])
    if plural:
        if low.endswith("ых"):
            return word[:-2] + "ые"
        if low.endswith("их"):
            return word[:-2] + "ие"
        return word
    if gender == "n":
        if low.endswith("ого"):
            return word[:-3] + "ое"
        if low.endswith("его"):
            return word[:-3] + "ее"
        return word
    if gender == "f":
        if low.endswith(("ой", "ей")):
            return word[:-2] + "ую"
        if low.endswith("ая"):
            return word[:-2] + "ую"
        if low.endswith("яя"):
            return word[:-2] + "юю"
        return word
    if low.endswith("ого"):
        return word[:-3] + "ый"
    if low.endswith("его"):
        return word[:-3] + "ий"
    if low.endswith(("ой", "ей")):
        return word[:-2] + "ую"
    if low.endswith("ая"):
        return word[:-2] + "ую"
    if low.endswith("яя"):
        return word[:-2] + "юю"
    return word


def _noun_acc_features(original: str, acc: str) -> tuple[bool, str]:
    src = original.casefold()
    out = acc.casefold()
    if src.endswith(("ения", "ания", "яния")):
        return False, "n"
    plural = src.endswith(("ов", "ев", "ёв", "ей", "ений", "ок")) or bool(
        re.search(r"(?i)[аеёиоуыэюя][шж]$", src)
    )
    if out.endswith(("ые", "ки", "ши", "жи")):
        plural = True
    elif len(out) > 3 and out.endswith(("ы", "и")) and not out.endswith(("ие", "ние")):
        plural = True
    if plural:
        return True, "m"
    if out.endswith(("е", "о", "ие")):
        return False, "n"
    if out.endswith(("у", "ю")):
        return False, "f"
    return False, "m"


def _noun_to_prepositional(word: str) -> str:
    low = word.casefold()
    if low.endswith(("ах", "ях", "е", "и")):
        return word
    if low.endswith("ы"):
        return word[:-1] + "е"
    if low.endswith("а"):
        return word[:-1] + "е"
    if low.endswith("я"):
        return word[:-1] + "е"
    if not re.search(r"(?i)[аеёиоуыэюя]$", word):
        return word + "ах"
    return word


def _inflect_object_phrase(phrase: str, *, case: str) -> str:
    tokens = re.findall(r"\s+|[^\s]+", phrase)
    out: list[str] = []
    seen_noun = False
    has_post_head = False
    colon_list = False
    pending: list[str] = []

    def pending_cores() -> list[tuple[int, str, str, str]]:
        found: list[tuple[int, str, str, str]] = []
        for idx, item in enumerate(pending):
            if item.isspace():
                continue
            prefix, core, suffix = _strip_punct_word(item)
            if core and _is_adjective(core):
                found.append((idx, prefix, core, suffix))
        return found

    def apply_pending(plural: bool, gender: str) -> None:
        for idx, prefix, core, suffix in pending_cores():
            if case == "acc":
                core = _adj_to_acc(core, plural=plural, gender=gender)
            pending[idx] = f"{prefix}{core}{suffix}"
        out.extend(pending)
        pending.clear()

    for token in tokens:
        if token.isspace():
            (pending if (pending or not out) and not seen_noun else out).append(token)
            continue
        prefix, core, suffix = _strip_punct_word(token)
        if not core or _is_preposition(core) or core.casefold() in {"и", "или", "г"}:
            if core.casefold() in {"и", "или"} and not has_post_head and not colon_list:
                seen_noun = False
            if pending and _is_preposition(core):
                apply_pending(False, "m")
            target = pending if pending and core.casefold() in {"и", "или"} else out
            if pending and core.casefold() in {"и", "или"}:
                pending.append(token)
            else:
                if pending:
                    apply_pending(False, "m")
                out.append(token)
            continue
        if re.match(r"(?i)^г$", core) and suffix.startswith("."):
            if pending:
                apply_pending(False, "m")
            out.append(token)
            if seen_noun:
                has_post_head = True
            continue
        if not seen_noun and not colon_list and _is_adjective(core):
            pending.append(token)
            continue
        if case == "acc" and not colon_list and (not seen_noun or prefix.startswith("(")):
            acc_core = _noun_gen_to_acc(core)
            plural, gender = _noun_acc_features(core, acc_core)
            apply_pending(plural, gender)
            core = acc_core
            if not prefix.startswith("("):
                seen_noun = True
        elif case == "acc" and colon_list:
            if pending:
                apply_pending(True, "m")
        elif case == "acc" and seen_noun:
            has_post_head = True
            if pending:
                apply_pending(False, "m")
        elif case == "prep" and not seen_noun and not _is_adjective(core):
            if pending:
                apply_pending(False, "m")
            core = _noun_to_prepositional(core)
            seen_noun = True
        else:
            if pending:
                apply_pending(False, "m")
        out.append(f"{prefix}{core}{suffix}")
        if ":" in suffix:
            colon_list = True
        if "," in suffix and not colon_list:
            seen_noun = False
            has_post_head = False
    if pending:
        apply_pending(False, "m")
    return "".join(out)


def _split_object_and_conditions(remainder: str) -> tuple[str, str]:
    text = _normalize_spaces(remainder)
    if not text:
        return "", ""
    protected = re.sub(
        r"\b(г|ул|пр|пер|обл|р-н|с|п|д|пос|т|пгт)\.\s*",
        lambda match: match.group(0).replace(".", "\u0000"),
        text,
        flags=re.IGNORECASE,
    )
    tokens = protected.split()
    object_parts: list[str] = []
    condition_parts: list[str] = []
    in_conditions = False
    for token in tokens:
        raw = token.replace("\u0000", ".")
        if not in_conditions and _is_preposition(raw):
            in_conditions = True
        if in_conditions:
            condition_parts.append(raw)
        else:
            object_parts.append(raw)
    return _normalize_spaces(" ".join(object_parts)), _normalize_spaces(" ".join(condition_parts))


def _verbal_noun_lemma(word: str) -> str:
    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE)
    low = core.casefold()
    endings = (
        ("ениями", "ение"),
        ("аниями", "ание"),
        ("ением", "ение"),
        ("анием", "ание"),
        ("янием", "яние"),
        ("ениях", "ение"),
        ("аниях", "ание"),
        ("ению", "ение"),
        ("анию", "ание"),
        ("ения", "ение"),
        ("ания", "ание"),
        ("яния", "яние"),
        ("тием", "тие"),
        ("тия", "тие"),
    )
    for src, dst in endings:
        if low.endswith(src) and len(low) > len(src) + 2:
            return core[: -len(src)] + dst
    return core


def _looks_like_verbal_noun(word: str) -> bool:
    lemma = _verbal_noun_lemma(word).casefold()
    if lemma in _VERBAL_NOUN_TO_VERB:
        return True
    if lemma in _STATE_OR_KNOWLEDGE_LEMMAS:
        return False
    if lemma.endswith("ведение") and lemma != "ведение":
        return False
    if lemma in {"движение", "произведение", "введение", "заключение", "упражнение"}:
        return False
    return bool(
        re.search(
            r"(?:ование|евание|ывание|ивание|ание|ение|яние|тие|тка|дка|нка|вка|жка)$",
            lemma,
        )
    )


def _conjugate_verbal_noun(word: str) -> str | None:
    lemma = _verbal_noun_lemma(word)
    mapped = _VERBAL_NOUN_TO_VERB.get(lemma.casefold())
    if mapped:
        return mapped
    low = lemma.casefold()
    if low in _STATE_OR_KNOWLEDGE_LEMMAS:
        return None
    if low.endswith("ование") and len(lemma) > 7:
        return lemma[: -len("ование")] + "ует"
    if low.endswith("евание") and len(lemma) > 7:
        return lemma[: -len("евание")] + "юет"
    if low.endswith("ывание") and len(lemma) > 7:
        return lemma[: -len("ывание")] + "ывает"
    if low.endswith("ивание") and len(lemma) > 7:
        return lemma[: -len("ивание")] + "ивает"
    if low.endswith("товление") and len(lemma) > 9:
        return lemma[: -len("товление")] + "тавливает"
    if low.endswith("ание") and not low.endswith("ование") and len(lemma) > 5:
        return lemma[: -len("ание")] + "ает"
    if low.endswith("ение") and len(lemma) > 5:
        if low.endswith("ведение") and low != "ведение":
            return None
        stem = lemma[: -len("ение")]
        last = stem[-1:].casefold()
        if last == "л" and len(stem) >= 2 and stem[-2].casefold() in "пбвфм":
            return stem[:-1] + "ает"
        if last == "л":
            return stem + "яет"
        if last in "чщжш":
            return stem + "ает"
        if last in "дтб" and len(stem) <= 4:
            return stem + "ёт"
        return stem + "яет"
    if low.endswith("тие") and len(lemma) > 4:
        return lemma[: -len("тие")] + "вает"
    return None


def _is_non_student_process(clause: str) -> bool:
    text = _normalize_spaces(clause)
    return bool(_EFFECT_FRAME_RE.search(text))


def _student_conducts_quiz(source: str) -> bool:
    return bool(_STUDENT_CONDUCTS_QUIZ_RE.search(source or ""))


def _is_exercise_word(word: str) -> bool:
    return bool(re.match(r"(?i)^упражнен", re.sub(r"[^\wёЁ]", "", word)))


def _is_walk_word(word: str) -> bool:
    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE).casefold()
    return bool(re.match(r"(?:прогулк\w*|экскурси[яиею])$", core))


def _token_core(token: str) -> str:
    return re.sub(r"^[«(\"]+|[»)\",;:]+$", "", token)


def _is_action_head(word: str) -> bool:
    return bool(
        word
        and (
            _looks_like_verbal_noun(word)
            or _is_exercise_word(word)
            or _is_walk_word(word)
            or word.casefold().startswith("викторин")
        )
    )


def _starts_new_action(tokens: list[str], index: int) -> bool:
    """После запятой: действие или прилагательное + действие («практическое оказание»)."""

    look = index
    while look < len(tokens):
        core = _token_core(tokens[look])
        if not core:
            look += 1
            continue
        if _is_action_head(core):
            return True
        if _is_adjective(core) and not _looks_like_verbal_noun(core):
            look += 1
            continue
        return False
    return False


def _split_action_segments(text: str) -> list[str]:
    """Режет клаузу только перед новым действием, не внутри объекта."""

    parts: list[str] = []
    buf: list[str] = []
    tokens = text.split()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        core = _token_core(token)
        prev = buf[-1] if buf else ""
        is_break = False
        if buf and core:
            in_prepositional = any(_is_preposition(item) for item in buf)
            nominative_action = _is_exercise_word(core) or (
                _looks_like_verbal_noun(core)
                and _verbal_noun_lemma(core).casefold()
                == re.sub(r"[^\wёЁ]", "", core).casefold()
            )
            if token.startswith("("):
                pass
            elif in_prepositional and not (
                prev.endswith(",") and nominative_action and _starts_new_action(tokens, index)
            ):
                pass
            elif prev.endswith(",") and _starts_new_action(tokens, index):
                is_break = True
            elif prev.casefold() in {"и", "или"} and _looks_like_verbal_noun(core):
                # «Развертывание и свертывание лагеря» — одно сегментное действие.
                if len(buf) == 1 and _looks_like_verbal_noun(buf[0]):
                    pass
                elif any(_looks_like_verbal_noun(item) for item in buf[:-1]):
                    pass
        if is_break:
            parts.append(" ".join(buf).rstrip(","))
            buf = [token]
        else:
            buf.append(token)
        index += 1
    if buf:
        parts.append(" ".join(buf).rstrip(","))
    return [part.strip(" ,") for part in parts if part.strip(" ,")]


def _leading_modifiers(tokens: list[str]) -> tuple[list[str], list[str]]:
    mods: list[str] = []
    rest = list(tokens)
    while rest and _is_adjective(rest[0]) and not _looks_like_verbal_noun(rest[0]):
        mods.append(rest.pop(0))
    return mods, rest


def _shared_object_after_paired_verbs(segment: str) -> tuple[str, str, str] | None:
    match = re.match(
        r"(?i)^((?:[А-Яа-яЁё]+ние|[А-Яа-яЁё]+ка))\s+и\s+"
        r"((?:[А-Яа-яЁё]+ние|[А-Яа-яЁё]+ка))\s+(.+)$",
        segment.strip(),
    )
    if not match:
        return None
    first, second, remainder = match.group(1), match.group(2), match.group(3)
    if not (_looks_like_verbal_noun(first) and _looks_like_verbal_noun(second)):
        return None
    verb1 = _conjugate_verbal_noun(first)
    verb2 = _conjugate_verbal_noun(second)
    if not verb1 or not verb2:
        return None
    obj, cond = _split_object_and_conditions(remainder)
    obj_acc = _inflect_object_phrase(obj, case="acc") if obj else ""
    phrase = f"{verb1} и {verb2}"
    if obj_acc:
        phrase += f" {obj_acc}"
    if cond:
        phrase += f" {cond}"
    return _normalize_spaces(phrase), f"{first} и {second}", _normalize_spaces(f"{obj} {cond}")


def _transform_segment(
    segment: str,
    *,
    theory_only: bool,
    full_source: str,
) -> tuple[str, str, str, str]:
    """Вернуть (фраза, action, object, conditions)."""

    text = _normalize_spaces(segment).strip(" ,")
    if not text:
        return "", "", "", ""
    if _is_non_student_process(text):
        return _characterize(text)

    paired = _shared_object_after_paired_verbs(text)
    if paired:
        phrase, action, rest = paired
        obj, cond = _split_object_and_conditions(rest)
        return phrase, action, obj, cond

    tokens = text.split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return text, "", "", ""
    head = re.sub(r"^[«(\"]+|[»)\",;:]+$", "", rest[0])

    if _is_exercise_word(head):
        remainder = " ".join(rest[1:]).strip()
        phrase = "выполняет упражнения"
        if remainder:
            phrase += f" {remainder}"
        obj, cond = _split_object_and_conditions(remainder)
        return phrase, "упражнения", obj, cond

    if _has_stem(text, ("викторин",)) and re.search(
        r"(?i)(?:проведен|провод)", text
    ):
        remainder_match = re.search(r"(?i)(?:проведен\w*|провод\w+)\s+(.+)", text)
        remainder = remainder_match.group(1) if remainder_match else "викторине"
        if not _student_conducts_quiz(full_source):
            inflected = _inflect_object_phrase(remainder, case="prep")
            return (
                _normalize_spaces(f"участвует в {inflected}"),
                "участие в викторине",
                remainder,
                "",
            )
        inflected = _inflect_object_phrase(remainder, case="acc")
        return _normalize_spaces(f"проводит {inflected}"), "проведение", remainder, ""

    if _is_walk_word(head):
        walk_words: list[str] = []
        idx = 0
        while idx < len(rest) and (
            _is_walk_word(re.sub(r"[^\wёЁ]", "", rest[idx]))
            or rest[idx].casefold() in {"и", "или"}
        ):
            walk_words.append(rest[idx])
            idx += 1
        remainder = " ".join(rest[idx:]).strip()
        obj, cond = _split_object_and_conditions(remainder)
        lowered_walks = []
        for word in walk_words:
            if word.casefold() in {"и", "или"}:
                lowered_walks.append(word.casefold())
            elif word[:1].isupper() and not re.match(r"^[А-ЯЁ]{2,}$", word):
                lowered_walks.append(word[:1].lower() + word[1:])
            else:
                lowered_walks.append(word)
        phrase = "совершает " + " ".join(lowered_walks)
        if remainder:
            phrase += f" {remainder}"
        return _normalize_spaces(phrase), " ".join(walk_words), obj, cond

    if _looks_like_verbal_noun(head):
        if theory_only and head.casefold() in _KNOWLEDGE_NOUNS:
            return _characterize(text)
        verb = _conjugate_verbal_noun(head)
        if verb:
            remainder = " ".join(rest[1:]).strip()
            if mods and mods[0].casefold().endswith("ое"):
                verb = "практически " + verb
            obj, cond = _split_object_and_conditions(remainder)
            obj_acc = _inflect_object_phrase(obj, case="acc") if obj else ""
            phrase = verb
            if obj_acc:
                phrase += f" {obj_acc}"
            if cond:
                phrase += f" {cond}"
            return _normalize_spaces(phrase), head.casefold(), obj, cond

    if theory_only or head.casefold() in _KNOWLEDGE_NOUNS:
        return _characterize(text)
    return text, "", "", ""


def _characterize(text: str) -> tuple[str, str, str, str]:
    tokens = _normalize_spaces(text).split()
    if not tokens:
        return "", "", "", ""
    first = tokens[0]
    prefix, core, suffix = _strip_punct_word(first)
    acc = _noun_nom_to_acc(core)
    if acc and not re.match(r"^[А-ЯЁ]{2,}$", acc):
        acc = acc[:1].lower() + acc[1:]
    tokens[0] = f"{prefix}{acc}{suffix}"
    obj, cond = _split_object_and_conditions(" ".join(tokens))
    phrase = _normalize_spaces("характеризует " + " ".join(tokens))
    return phrase, "характеризует", obj, cond


def _transform_inner(text: str, *, theory_only: bool, full_source: str) -> str:
    pieces: list[str] = []
    for raw_part in re.split(r"\s+или\s+", text):
        or_parts = []
        for segment in _split_action_segments(raw_part):
            phrase, _, _, _ = _transform_segment(
                segment, theory_only=theory_only, full_source=full_source
            )
            if phrase:
                or_parts.append(phrase)
        if or_parts:
            pieces.append(", ".join(or_parts) if len(or_parts) > 1 else or_parts[0])
    return " или ".join(pieces) if pieces else text


def _paren_has_actions(inner: str) -> bool:
    if _ACTIVITY_START_RE.search(inner.strip()):
        return True
    return any(_looks_like_verbal_noun(token) for token in re.findall(r"[А-Яа-яЁё]+", inner))


_CAPACITY_PREPOSITIONS = {
    "в",
    "во",
    "на",
    "по",
    "с",
    "со",
    "для",
    "к",
    "ко",
    "от",
    "из",
    "у",
    "о",
    "об",
    "обо",
    "при",
    "над",
    "под",
    "между",
    "без",
    "до",
    "за",
    "через",
    "про",
}


def _role_noun_gen_pl_to_sg(word: str) -> tuple[str, str]:
    """Роль после «в качестве»: род. мн. → род. ед. по окончанию, без словаря тем."""

    low = word.casefold()
    if low.endswith("ниц") and len(word) > 4 and not low.endswith("ница"):
        return word + "ы", "f"
    if low.endswith("телей") and len(word) > 6:
        return word[:-2] + "я", "m"
    if low.endswith("арей") and len(word) > 5:
        return word[:-2] + "я", "m"
    if low.endswith("ов") and len(word) > 5 and len(word) - 2 >= 4:
        return word[:-2] + "а", "m"
    if low.endswith("ёв") and len(word) > 5 and len(word) - 2 >= 4:
        return word[:-2] + "я", "m"
    if low.endswith("ев") and len(word) > 5 and len(word) - 2 >= 4:
        stem = word[:-2]
        last = stem[-1:].casefold()
        if last in "цжшщч":
            return stem + "а", "m"
        return stem + "я", "m"
    return word, "m"


def _adj_gen_pl_to_sg(word: str, *, gender: str) -> str:
    low = word.casefold()
    if gender == "f":
        if low.endswith("ых"):
            return word[:-2] + "ой"
        if low.endswith("их"):
            return word[:-2] + "ей"
        return word
    if low.endswith("ых"):
        return word[:-2] + "ого"
    if low.endswith("их"):
        stem = word[:-2]
        last = stem[-1:].casefold()
        if last in "кгхжчшщц":
            return stem + "ого"
        return stem + "его"
    return word


def _singularize_capacity_tail(tail: str) -> str:
    tokens = re.findall(r"\s+|[^\s]+", tail)
    pending_adj: list[int] = []
    updated = list(tokens)
    noun_seen = False
    for index, token in enumerate(tokens):
        if token.isspace():
            continue
        prefix, core, suffix = _strip_punct_word(token)
        if not core:
            continue
        if core.casefold() in _CAPACITY_PREPOSITIONS:
            break
        if _is_adjective(core) and not noun_seen:
            pending_adj.append(index)
            continue
        converted, gender = _role_noun_gen_pl_to_sg(core)
        if converted != core:
            updated[index] = f"{prefix}{_match_caps(core, converted)}{suffix}"
            for adj_index in pending_adj:
                adj_prefix, adj_core, adj_suffix = _strip_punct_word(tokens[adj_index])
                adj = _adj_gen_pl_to_sg(adj_core, gender=gender)
                updated[adj_index] = f"{adj_prefix}{_match_caps(adj_core, adj)}{adj_suffix}"
        noun_seen = True
        break
    if not noun_seen and pending_adj:
        first = pending_adj[0]
        prefix, core, suffix = _strip_punct_word(tokens[first])
        if core.casefold().endswith(("ых", "их")):
            adj = _adj_gen_pl_to_sg(core, gender="m")
            updated[first] = f"{prefix}{_match_caps(core, adj)}{suffix}"
    return "".join(updated)


def _agree_capacity_role(text: str) -> str:
    """Согласовать роль после «в качестве» с 3-м лицом ед. ч. ученика."""

    parts = re.split(r"(?i)(\bв качестве\b)", text)
    if len(parts) < 3:
        return text
    out = [parts[0]]
    for index in range(1, len(parts), 2):
        out.append(parts[index])
        tail = parts[index + 1] if index + 1 < len(parts) else ""
        out.append(_singularize_capacity_tail(tail))
    return "".join(out)


def transform_clause_to_result(
    clause: str,
    *,
    theory_only: bool,
    full_source: str,
) -> tuple[str, ActionFrame]:
    """Наблюдаемый RESULT: настоящее время, 3-е лицо, только факты источника."""

    source_clause = _clean_source_phrase(clause)
    if not source_clause:
        return "", ActionFrame("", "", "", "")

    action_parens: list[str] = []

    def _paren(match: re.Match[str]) -> str:
        inner = match.group(1)
        if _paren_has_actions(inner):
            action_parens.append(
                _transform_inner(inner, theory_only=False, full_source=full_source)
            )
            return " "
        return match.group(0)

    phrases: list[str] = []
    actions: list[str] = []
    objects: list[str] = []
    conditions: list[str] = []
    for unit in _clause_units(source_clause) or [source_clause]:
        main = re.sub(r"\(([^()]*)\)", _paren, unit)
        for segment in _split_action_segments(_normalize_spaces(main)):
            phrase, action, obj, cond = _transform_segment(
                segment, theory_only=theory_only, full_source=full_source
            )
            if phrase:
                phrases.append(phrase.rstrip(","))
            if action:
                actions.append(action)
            if obj:
                objects.append(obj)
            if cond:
                conditions.append(cond)
    result = ", ".join(phrases)
    if action_parens:
        result = _normalize_spaces(
            result + " " + " ".join(f"({part})" for part in action_parens)
        )
    result = _agree_capacity_role(result)
    result = _cap_sentence(_shorten_clause(result))
    frame = ActionFrame(
        clause=source_clause,
        action=", ".join(actions),
        object=", ".join(objects),
        conditions=", ".join(conditions),
    )
    return result, frame


def _topic_hits(clause: str, topic_title: str) -> int:
    topic_words = re.findall(r"[А-Яа-яЁё]{4,}", topic_title.casefold())
    return sum(1 for word in topic_words if word in clause.casefold())


def _action_class(clause: str, *, theory_only: bool = False) -> int:
    tokens = clause.split()
    first = tokens[0] if tokens else ""
    lead = " ".join(tokens[:3])
    if _is_non_student_process(clause):
        return 0
    if theory_only and first.casefold() in _KNOWLEDGE_NOUNS:
        return 1
    if re.match(
        r"(?i)^(разрядн|понятие|значение|характеристика|роль|виды|требования|способ)",
        first,
    ):
        return 0
    if theory_only and _looks_like_verbal_noun(first):
        return 1
    if _has_stem(lead, _FORM_STEMS + _PERFORM_STEMS):
        return 3
    if _has_stem(lead, _PRODUCE_STEMS + ("ориентир", "измерен")):
        return 3
    if re.match(r"(?i)^(определен|изучен|знакомств|поняти|значен|соблюден)", first):
        return 0
    if _looks_like_verbal_noun(first):
        return 2
    return 1


def _condition_signal(clause: str) -> int:
    """Сильные условия (скобки, география, длинная клауза), без штрафа коротким первым фразам."""

    score = 0
    if "(" in clause:
        score += 2
    if re.search(r"\bг\.\s*[А-ЯЁA-Z]", clause):
        score += 2
    if len(clause) >= 90:
        score += 1
    return score


def _has_geography(text: str) -> bool:
    return bool(re.search(r"\bг\.\s*[А-ЯЁA-Z]", text))


def _has_field_marker(text: str) -> bool:
    return bool(re.search(r"(?i)на местности|на маршруте|в поле", text))


def _enrich_with_neighbors(selected: str, units: list[str]) -> str:
    """Добавить соседние клаузы с географией или полевыми условиями той же темы."""

    if not selected or selected not in units:
        return selected
    index = units.index(selected)
    extras: list[tuple[int, str]] = []
    start = max(0, index - 1)
    end = min(len(units), index + 3)
    for nidx in range(start, end):
        if nidx == index:
            continue
        neighbor = units[nidx]
        if _is_non_student_process(neighbor):
            continue
        geo = _has_geography(neighbor) and not _has_geography(selected)
        field = _has_field_marker(neighbor) and not _has_field_marker(selected)
        if geo or field:
            extras.append((nidx, neighbor))
    if not extras:
        return selected
    parts = [(index, selected), *extras]
    parts.sort()
    return ". ".join(item for _, item in parts)


def select_source_clause(
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int = 0,
) -> tuple[str, bool]:
    source = _week_result_source(
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
    )
    theory_only = bool(theory_hours and not practice_hours)
    if practice_hours and not (practice_text or "").strip():
        theory_only = True
    if not source:
        return _normalize_spaces(topic_title), theory_only
    units = _clause_units(source)
    if not units:
        return _normalize_spaces(topic_title), theory_only
    best_class = max(_action_class(unit, theory_only=theory_only) for unit in units)
    pool = [
        unit
        for unit in units
        if _action_class(unit, theory_only=theory_only) == best_class
    ] or units
    scored = sorted(
        enumerate(pool),
        key=lambda pair: (
            -_topic_hits(pair[1], topic_title),
            -_condition_signal(pair[1]),
            pair[0],
        ),
    )
    best = scored[0][1]
    ordered = [best] + [unit for unit in pool if unit != best]
    chosen = ordered[occurrence_index % len(ordered)]
    return _enrich_with_neighbors(chosen, units), theory_only


def _leading_clause(frame: ActionFrame) -> str:
    segments = _split_action_segments(frame.clause)
    return segments[0] if segments else frame.clause


def _leading_blob(frame: ActionFrame) -> str:
    action = frame.action.split(",")[0] if frame.action else ""
    return _normalize_spaces(f"{action} {_leading_clause(frame)}").casefold()


def control_from_frame(
    frame: ActionFrame,
    *,
    lesson_type: str,
    theory_hours: int,
    practice_hours: int,
) -> str:
    lead = _leading_blob(frame)
    type_low = lesson_type.casefold()
    if "викторин" in lead:
        return "викторина"
    if _has_stem(lead, ("наблюден",)):
        return "наблюдение"
    if any(marker in type_low for marker in ("экскурсия", "на местности")):
        return "наблюдение"
    if type_low == "игра" or type_low.startswith("игр"):
        return "наблюдение"
    if _has_stem(lead, ("прогул", "экскурси", "посещен")):
        return "наблюдение"
    if _has_stem(lead, ("отчёт", "отчет", "доклад", "заслушиван")):
        return "защита результата"
    if _has_stem(lead, _PERFORM_STEMS) or "трениров" in type_low:
        return "демонстрация навыка"
    if "теоретическ" in type_low or "беседа" in type_low or (
        theory_hours and not practice_hours
    ):
        return "устный опрос"
    if _has_stem(lead, _PRODUCE_STEMS) or practice_hours:
        return "практическое задание"
    return "устный опрос"


def type_from_frame(
    frame: ActionFrame,
    *,
    theory_hours: int,
    practice_hours: int,
    theory_text: str,
    practice_text: str,
    program_content: str,
) -> str:
    lead = _leading_clause(frame)
    scores = _line_form_scores(lead)
    if practice_hours and lead:
        dominant = _dominant_label(scores, min_score=2)
        if dominant in {"игра", "викторина"} and not re.match(
            r"(?i)^(игр|викторин)", lead
        ):
            dominant = None
        if dominant and dominant != "беседа":
            return dominant
    if theory_hours and not practice_hours:
        theory_scores = _line_form_scores(lead or theory_text)
        if theory_scores.get("беседа", 0) >= 2:
            return "беседа"
        return "теоретическое занятие"
    if practice_hours and not (practice_text or "").strip():
        return "теоретическое занятие"
    if practice_hours:
        return "практическое занятие"
    return derive_lesson_type(
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        topic_title="",
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
    )


def derive_fields_v2(
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str = "",
    theory_hours: int = 0,
    practice_hours: int = 0,
    occurrence_index: int = 0,
) -> ContentEngineV2Result:
    """Цепочка: source → action/object/conditions → RESULT → CONTROL → TYPE."""

    warnings: list[str] = []
    clause, theory_only = select_source_clause(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
    )
    if not (theory_text.strip() or practice_text.strip() or program_content.strip()):
        warnings.append("Недостаточно данных источника; использован безопасный fallback.")
        clause = clause or topic_title
        theory_only = bool(theory_hours and not practice_hours)
    planned_result, frame = transform_clause_to_result(
        clause,
        theory_only=theory_only,
        full_source=_normalize_spaces(
            f"{theory_text} {practice_text} {program_content} {clause}"
        ),
    )
    if not planned_result:
        planned_result, frame = transform_clause_to_result(
            topic_title,
            theory_only=True,
            full_source=topic_title,
        )
    lesson_type = type_from_frame(
        frame,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
    )
    assessment = control_from_frame(
        frame,
        lesson_type=lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    return ContentEngineV2Result(
        frame=frame,
        lesson_type=lesson_type,
        planned_result=planned_result,
        assessment_method=assessment,
        theory_text=theory_text,
        practice_text=practice_text,
        warnings=tuple(warnings),
    )


def fill_from_source(
    *,
    topic_title: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int = 0,
) -> ContentEngineV2Result:
    """Разделить содержание темы и заполнить поля 2.0."""

    theory_text = ""
    practice_text = ""
    content = program_content or ""
    explicit = _split_explicit_practice(content) if content else None
    if explicit:
        theory_source, practice_source = explicit
        if theory_hours:
            theory_text = theory_source
        if practice_hours:
            practice_text = practice_source
    elif theory_hours and not practice_hours:
        theory_text = content
    elif practice_hours and not theory_hours:
        practice_text = content
    elif theory_hours and practice_hours:
        theory_text = content
    return derive_fields_v2(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
    )


def _split_row_texts(row: CalendarContentRow) -> tuple[str, str, list[str]]:
    warnings: list[str] = list(row.warnings)
    theory_parts: list[str] = []
    practice_parts: list[str] = []
    parts = row.week_parts or (
        WeekTopicPart(
            topic_number=row.topic_number,
            topic_title=row.topic_title,
            section=row.section,
            theory_hours=row.theory_hours,
            practice_hours=row.practice_hours,
            match_status=row.match_status,
            program_section=row.program_section,
            program_topic=row.program_topic,
            program_content_full=row.program_content_full,
            warnings=row.warnings,
        ),
    )
    topic_totals: dict[tuple[str | None, str, str], tuple[int, int]] = {}
    for part in parts:
        key = (part.topic_number, part.topic_title, part.section)
        theory, practice = topic_totals.get(key, (0, 0))
        topic_totals[key] = (theory + part.theory_hours, practice + part.practice_hours)
    for part in parts:
        topic_theory, topic_practice = topic_totals[
            (part.topic_number, part.topic_title, part.section)
        ]
        theory_text = ""
        practice_text = ""
        content = part.program_content_full
        if content:
            explicit = _split_explicit_practice(content)
            if explicit:
                theory_source, practice_source = explicit
                if part.theory_hours:
                    theory_text = theory_source
                if part.practice_hours:
                    practice_text = practice_source
            elif topic_theory and not topic_practice and part.theory_hours:
                theory_text = content
            elif topic_practice and not topic_theory and part.practice_hours:
                practice_text = content
            elif topic_theory and topic_practice:
                theory_text = content
        if theory_text:
            theory_parts.append(theory_text)
        if practice_text:
            practice_parts.append(practice_text)
    return "\n".join(theory_parts), "\n".join(practice_parts), warnings


def build_lesson_content_v2(
    rows: tuple[CalendarContentRow, ...],
) -> tuple[LessonContentV2Row, ...]:
    """Построить поля 2.0 по календарным строкам.

    Pipeline вызывает только при внутреннем флаге USE_CONTENT_ENGINE_V2.
    """

    result: list[LessonContentV2Row] = []
    topic_occurrences: dict[tuple[str | None, str, str], int] = {}
    for row in rows:
        key = (row.topic_number, row.topic_title, row.section)
        occurrence_index = topic_occurrences.get(key, 0)
        topic_occurrences[key] = occurrence_index + 1
        theory_text, practice_text, warnings = _split_row_texts(row)
        derived = derive_fields_v2(
            topic_title=row.topic_title,
            theory_text=theory_text,
            practice_text=practice_text,
            program_content=row.program_content_full or "",
            theory_hours=row.theory_hours,
            practice_hours=row.practice_hours,
            occurrence_index=occurrence_index,
        )
        result.append(
            LessonContentV2Row(
                source=row,
                theory_text=theory_text,
                practice_text=practice_text,
                lesson_type=derived.lesson_type,
                planned_result=derived.planned_result,
                assessment_method=derived.assessment_method,
                action=derived.frame.action,
                object=derived.frame.object,
                conditions=derived.frame.conditions,
                warnings=tuple(dict.fromkeys((*warnings, *derived.warnings))),
            )
        )
    return tuple(result)
