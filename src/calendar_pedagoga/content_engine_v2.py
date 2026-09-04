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


def _has_all_stems(text: str, stems: tuple[str, ...]) -> bool:
    low = text.casefold()
    return all(stem in low for stem in stems)


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
    if word.casefold() == "пищи":
        return "пищу"
    if word.casefold() == "сторон":
        return "стороны"
    if "-" in word:
        if word.casefold().startswith("плана-график"):
            return "план-график" + word[len("плана-графика") :]
        return word
    if re.search(r"(?i)\d", word) or word.endswith("."):
        return word

    low = word.casefold()
    if low.endswith("ств") and len(word) > 5:
        return word + "а"
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
        named = _name_kinds(text)
        if named:
            return named
        return _characterize(text)
    return text, "", "", ""


def _name_kinds(text: str) -> tuple[str, str, str, str] | None:
    match = re.match(r"(?i)^виды\s+([^:.,]+)", _normalize_spaces(text))
    if match is None:
        return None
    obj = _normalize_spaces(match.group(1))
    if not obj:
        return None
    phrase = f"называет виды {obj[:1].lower() + obj[1:]}"
    return phrase, "называет", f"виды {obj}", ""


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


_DANGLING_PRONOUN_RE = re.compile(r"(?i)^(ее|её|его|их)\s+(.+)$")
_FINITE_VERB_RE = re.compile(
    r"(?i)^[А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют|ает|яет)\b"
)
_KNOWLEDGE_WRAPPER_RE = re.compile(
    r"(?i)^(характеризует)\s+(?:краткие|общие|основные)\s+сведения\s+(?:о|об)\s+"
)
_GENERIC_TOPIC_STEMS = (
    "техник",
    "занят",
    "проведен",
    "поход",
    "турист",
    "основ",
    "правил",
    "занятий",
    "физическ",
    "специальн",
    "сведен",
    "влияни",
)


def _resolve_dangling_pronoun(clause: str, topic_title: str) -> str:
    match = _DANGLING_PRONOUN_RE.match(clause.strip())
    if match is None:
        return clause
    rest = match.group(2)
    head = rest.split()[0] if rest.split() else ""
    topic_words = re.findall(r"[А-Яа-яЁё]{4,}", topic_title)
    replacement = ""
    for word in reversed(topic_words):
        if word.casefold() == head.casefold():
            continue
        if len(word) >= 5 and word.casefold()[:5] == head.casefold()[:5]:
            continue
        replacement = word
        break
    tail = rest[len(head) :].lstrip() if head else rest
    if replacement:
        return _normalize_spaces(f"{head} {replacement.casefold()} {tail}")
    return _normalize_spaces(rest)


def _is_finite_result_phrase(phrase: str) -> bool:
    return bool(_FINITE_VERB_RE.match(phrase.strip()))


def _merge_repeated_verbs(text: str) -> str:
    parts = re.split(r",\s+", text)
    if len(parts) < 2:
        return text
    verb_re = re.compile(r"(?i)^([А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют))\s+")
    merged: list[str] = []
    prev_verb = ""
    for part in parts:
        found = verb_re.match(part)
        if found and prev_verb and found.group(1).casefold() == prev_verb:
            rest = part[found.end() :]
            if merged:
                merged[-1] = f"{merged[-1].rstrip(',')} и {rest}"
            continue
        prev_verb = found.group(1).casefold() if found else ""
        merged.append(part)
    return ", ".join(merged)


def _trim_long_parentheticals(text: str) -> str:
    def drop(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.count(",") >= 2 or inner.count(";") >= 1:
            return ""
        return match.group(0)

    return _normalize_spaces(re.sub(r"\s*\(([^()]*)\)", drop, text))


def _drop_raw_list_tails(text: str) -> str:
    parts = re.split(r",\s+", text)
    if len(parts) <= 1:
        return text
    kept = [parts[0]]
    for part in parts[1:]:
        first = part.split()[0] if part.split() else ""
        if _FINITE_VERB_RE.match(part):
            kept.append(part)
            continue
        if _looks_like_verbal_noun(first) or re.search(
            r"(?i)(?:нию|тию|анию|ению)$", first
        ):
            continue
        if first[:1].isupper() and not _FINITE_VERB_RE.match(part):
            continue
        if re.match(r"(?i)^(игры|игра|соревнования|диктанты|занятия|мини)\b", part):
            continue
        kept.append(part)
    return ", ".join(kept)


def _keep_strongest_phrase(phrases: list[str]) -> list[str]:
    if len(phrases) < 2:
        return phrases
    finite = [item for item in phrases if _is_finite_result_phrase(item)]
    pool = finite or phrases

    def score(item: str) -> tuple[int, int]:
        low = item.casefold()
        strength = 0
        if low.startswith("выполняет упражнения"):
            strength = 3
        elif _has_stem(low, _PRODUCE_STEMS + _PERFORM_STEMS):
            strength = 2
        elif _is_finite_result_phrase(item):
            strength = 1
        return (strength, -len(item))

    best = max(pool, key=score)
    if score(best)[0] >= 3 and sum(1 for item in pool if score(item)[0] >= 3) == 1:
        return [best]
    if len(phrases) < 3:
        return phrases
    return [best]


def _prep_noun_to_nom(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith("ии") and len(core) > 3:
        core = core[:-2] + "ие"
    elif low.endswith("иях") and len(core) > 4:
        core = core[:-3] + "ия"
    return f"{prefix}{core}{suffix}"


def _drop_knowledge_wrappers(text: str) -> str:
    stripped = _KNOWLEDGE_WRAPPER_RE.sub(r"\1 ", text)
    if stripped == text:
        return text
    words = stripped.split()
    if len(words) >= 2:
        words[1] = _prep_noun_to_nom(words[1])
    return _normalize_spaces(" ".join(words))


def transform_clause_to_result(
    clause: str,
    *,
    theory_only: bool,
    full_source: str,
    topic_title: str = "",
) -> tuple[str, ActionFrame]:
    """Наблюдаемый RESULT: настоящее время, 3-е лицо, только факты источника."""

    source_clause = _resolve_dangling_pronoun(
        _clean_source_phrase(clause), topic_title
    )
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
            if phrase and (
                _is_finite_result_phrase(phrase) or theory_only or not phrases
            ):
                if not _is_finite_result_phrase(phrase) and phrases:
                    continue
                phrases.append(phrase.rstrip(","))
            if action:
                actions.append(action)
            if obj:
                objects.append(obj)
            if cond:
                conditions.append(cond)
    phrases = _keep_strongest_phrase(phrases)
    result = ", ".join(phrases)
    if action_parens and len(phrases) < 2:
        result = _normalize_spaces(
            result + " " + " ".join(f"({part})" for part in action_parens)
        )
    result = _merge_repeated_verbs(result)
    result = _drop_raw_list_tails(result)
    result = _trim_long_parentheticals(result)
    result = _drop_knowledge_wrappers(result)
    result = _agree_capacity_role(result)
    result = _cap_sentence(_shorten_clause(result))
    frame = ActionFrame(
        clause=source_clause,
        action=", ".join(actions),
        object=", ".join(objects),
        conditions=", ".join(conditions),
    )
    return result, frame


def _is_generic_topic_word(word: str) -> bool:
    low = word.casefold()
    return any(low.startswith(stem) for stem in _GENERIC_TOPIC_STEMS)


def _topic_hits(clause: str, topic_title: str) -> int:
    topic_words = re.findall(r"[А-Яа-яЁё]{4,}", topic_title.casefold())
    clause_low = clause.casefold()
    clause_words = re.findall(r"[А-Яа-яЁё]{4,}", clause_low)
    hits = 0
    for word in topic_words:
        if _is_generic_topic_word(word):
            continue
        matched = word in clause_low
        if not matched:
            stem_len = 8 if len(word) >= 8 else 5
            stem = word[:stem_len]
            matched = len(stem) >= 5 and any(
                token.startswith(stem) or stem.startswith(token[:stem_len])
                for token in clause_words
            )
        if matched:
            hits += 2 if len(word) >= 8 else 1
    return hits


_SPECIFICITY_WEIGHTS = (
    ("план-график", 3),
    ("плана-график", 3),
    ("меню", 2),
    ("рюкзак", 2),
    ("костр", 2),
    ("бивак", 2),
    ("аптечк", 2),
    ("маршрут", 1),
)


def _specificity_signal(clause: str) -> int:
    low = clause.casefold()
    return sum(weight for stem, weight in _SPECIFICITY_WEIGHTS if stem in low)


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
        if theory_only and first.casefold() in {"виды", "понятие"}:
            return 1
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


def _is_kinds_clause(text: str) -> bool:
    return bool(re.match(r"(?i)^виды\b", text.strip()))


def _enrich_with_neighbors(selected: str, units: list[str]) -> str:
    """Добавить соседние клаузы той же темы: география, поле, виды, второй объект."""

    if not selected or selected not in units:
        return selected
    index = units.index(selected)
    extras: list[tuple[int, str]] = []
    used = {selected}

    def add(nidx: int, neighbor: str) -> None:
        if neighbor in used or _is_non_student_process(neighbor):
            return
        extras.append((nidx, neighbor))
        used.add(neighbor)

    start = max(0, index - 1)
    end = min(len(units), index + 3)
    for nidx in range(start, end):
        if nidx == index:
            continue
        neighbor = units[nidx]
        geo = _has_geography(neighbor) and not _has_geography(selected)
        field = _has_field_marker(neighbor) and not _has_field_marker(selected)
        if field and "упражнен" in selected.casefold():
            field = False
        if geo or field:
            add(nidx, neighbor)
    if not any(_is_kinds_clause(item) for item in used):
        for nidx, neighbor in enumerate(units):
            if _is_kinds_clause(neighbor):
                add(nidx, neighbor)
                break
    selected_low = selected.casefold()
    wanted: tuple[str, ...] = ()
    if "меню" in selected_low:
        wanted = ("костр", "приготов")
    elif "план подготовки" in selected_low or (
        "план" in selected_low
        and "составлен" in selected_low
        and "снаряжен" not in selected_low
    ) or (
        "подготовк" in selected_low and "поход" in selected_low and "снаряжен" not in selected_low
    ):
        wanted = ("план-график", "плана-график")
    elif "преодолен" in selected_low or "препятств" in selected_low:
        wanted = ("самострахов", "альпеншток")
    elif "лагер" in selected_low or "бивак" in selected_low:
        for nidx, neighbor in enumerate(units):
            if neighbor in used:
                continue
            if re.match(r"(?i)^выбор места", neighbor.strip()):
                add(nidx, neighbor)
                break
        wanted = ()
    if wanted:
        for nidx, neighbor in enumerate(units):
            if neighbor in used:
                continue
            if any(stem in neighbor.casefold() for stem in wanted):
                add(nidx, neighbor)
                break
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
    extra_units = _clause_units(program_content or "") if program_content else []
    if not units and not extra_units:
        return _normalize_spaces(topic_title), theory_only

    def topic_score(clause: str) -> int:
        return _topic_hits(clause, topic_title)

    def theory_overlap(clause: str) -> int:
        if not (theory_text or "").strip():
            return 0
        if topic_score(clause):
            return 0
        return _topic_hits(clause, theory_text)

    def rank_key(clause: str, as_theory: bool, index: int) -> tuple:
        return (
            -_action_class(clause, theory_only=as_theory),
            -topic_score(clause),
            -theory_overlap(clause),
            -_specificity_signal(clause),
            -_condition_signal(clause),
            index,
        )

    def pick(candidates: list[str], as_theory: bool) -> tuple[str, int] | None:
        if not candidates:
            return None
        classed = [
            (
                idx,
                unit,
                _action_class(unit, theory_only=as_theory),
                topic_score(unit),
            )
            for idx, unit in enumerate(candidates)
        ]
        best_class = max(item[2] for item in classed)
        top = [item for item in classed if item[2] == best_class]
        if top and max(item[3] for item in top) == 0:
            aligned = [item for item in classed if item[2] >= 2 and item[3] > 0]
            if aligned:
                top = aligned
        top.sort(key=lambda item: rank_key(item[1], as_theory, item[0]))
        best = top[0][1]
        return best, topic_score(best)

    primary = pick(units, theory_only)
    aligned = pick(extra_units, True)
    chosen = primary[0] if primary else (aligned[0] if aligned else topic_title)
    pool = units or extra_units
    if (
        primary
        and aligned
        and primary[1] == 0
        and aligned[1] >= 2
        and _action_class(primary[0], theory_only=False) <= 1
    ):
        chosen = aligned[0]
        pool = extra_units
        if _action_class(chosen, theory_only=False) <= 1:
            theory_only = True
    elif primary:
        same = [
            unit
            for unit in units
            if _action_class(unit, theory_only=theory_only)
            == _action_class(chosen, theory_only=theory_only)
        ]
        if same:
            ordered = [chosen] + [unit for unit in same if unit != chosen]
            chosen = ordered[occurrence_index % len(ordered)]
            pool = units
    return _enrich_with_neighbors(chosen, pool), theory_only


def _leading_clause(frame: ActionFrame) -> str:
    segments = _split_action_segments(frame.clause)
    return segments[0] if segments else frame.clause


def _leading_blob(frame: ActionFrame) -> str:
    action = frame.action.split(",")[0] if frame.action else ""
    return _normalize_spaces(f"{action} {_leading_clause(frame)}").casefold()


_NAMED_FORMS = ("викторин", "диктант")
_PLACE_TITLE_STEMS = ("край", "город", "район", "област", "республик", "стран")
_PRODUCT_HEADS = (
    "отчёт",
    "отчет",
    "меню",
    "план-график",
    "плана-график",
    "дневник",
    "аптечк",
    "доклад",
)
_FINITE_TO_NOUN = {
    "укладывает": "укладки",
    "подгоняет": "подгонки",
    "составляет": "составления",
    "готовит": "приготовления",
    "ориентирует": "ориентирования",
    "определяет": "определения",
    "оценивает": "оценки",
    "измеряет": "измерения",
    "отбирает": "отбора",
    "применяет": "применения",
    "изготавливает": "изготовления",
    "разучивает": "разучивания",
    "формирует": "формирования",
    "оказывает": "оказания",
    "выполняет": "выполнения",
    "выступает": "участия",
    "ведёт": "ведения",
    "ведет": "ведения",
    "рисует": "рисования",
    "строит": "построения",
    "подготавливает": "подготовки",
    "заслушивает": "заслушивания",
}
_EXERCISE_SKILL_VERBS = {"определяет", "оценивает", "измеряет"}
_FINITE_VERB_RE = re.compile(
    r"(?i)\b(" + "|".join(sorted(_FINITE_TO_NOUN, key=len, reverse=True)) + r")\b"
)


def _drop_leading_verb(text: str) -> str:
    return _normalize_spaces(
        re.sub(
            r"(?i)^[А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют|ает|яет|ает)\s+",
            "",
            text.strip(),
            count=1,
        )
    )


def _first_word_prepositional(text: str) -> str:
    words = _normalize_spaces(text).split()
    if not words:
        return text
    first = words[0]
    low = first.casefold()
    if low.endswith("ия") and len(first) > 3:
        words[0] = first[:-2] + "ии"
    elif low.endswith("ие") and len(first) > 3:
        words[0] = first[:-1] + "ю"
    elif low.endswith("а") and len(first) > 3:
        words[0] = first[:-1] + "е"
    elif low.endswith("ь") and len(first) > 3:
        words[0] = first[:-1] + "и"
    else:
        words[0] = low
    if words[0][:1].isupper():
        words[0] = words[0][:1].lower() + words[0][1:]
    return " ".join(words)


def _oral_quiz_control(frame: ActionFrame, planned_result: str) -> str:
    blob = _normalize_spaces(f"{planned_result} {frame.clause} {frame.object}").casefold()
    kinds = re.search(r"виды\s+([а-яё]+)", blob)
    if kinds:
        return f"устный опрос по видам {kinds.group(1)}"
    raw_clause = (frame.clause or "").split(".")[0]
    raw_result = (planned_result or "").split(".")[0]
    clause_core = re.sub(r"(?i)^(характеризует|называет)\s+", "", raw_clause).strip()
    result_core = re.sub(r"(?i)^(характеризует|называет)\s+", "", raw_result).strip()
    clause_first = clause_core.split()[0] if clause_core.split() else ""
    if (
        clause_first.casefold() in {"ее", "её", "его", "их", "эта", "это", "эти"}
        or _is_adjective(clause_first)
        or clause_first.casefold() in {"краткие", "общие", "основные", "сведения"}
    ):
        source = result_core
    else:
        source = clause_core or result_core
    first = source.split()[0] if source.split() else ""
    if first.casefold() in {"ее", "её", "его", "их", "эта", "это", "эти"}:
        return "устный опрос"
    if source and first and not _is_adjective(first):
        return "устный опрос по " + _shorten_clause(
            _first_word_prepositional(source), max_len=48
        )
    return "устный опрос"


def _result_restates_named_form(result: str) -> bool:
    return bool(re.match(r"(?i)участвует в .*(викторин|диктант)", result or ""))


def _title_has_knowledge_beyond_form(title: str) -> bool:
    stripped = re.sub(r"(?i)викторин\w*|диктант\w*|игр\w*", " ", title or "")
    return len(re.findall(r"[А-Яа-яЁё]{4,}", stripped)) >= 2


def _is_place_title_part(part: str) -> bool:
    words = [word for word in re.findall(r"[А-Яа-яЁё]+", part) if word]
    if not words or len(words) > 3:
        return False
    return any(
        any(word.casefold().startswith(stem) for stem in _PLACE_TITLE_STEMS)
        for word in words
    )


def _drop_geo_tail(part: str) -> str:
    text = re.sub(r"\s+г\.\s+\S+\.?$", "", part.strip().rstrip("."))
    words = text.split()
    if len(words) >= 2 and words[-1][:1].isupper() and not _is_adjective(words[-1]):
        words = words[:-1]
    return _normalize_spaces(" ".join(words))


def _adj_to_genitive(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith("ую"):
        core = core[:-2] + "ой"
    elif low.endswith("юю"):
        core = core[:-2] + "ей"
    elif low.endswith("ые"):
        core = core[:-2] + "ых"
    elif low.endswith("ие") and not low.endswith(("ние", "тие")):
        core = core[:-2] + "их"
    elif low.endswith("ая"):
        core = core[:-2] + "ой"
    elif low.endswith(("ый", "ой")) and len(core) > 3:
        core = core[:-2] + "ого"
    elif low.endswith("ое"):
        core = core[:-2] + "ого"
    return f"{prefix}{core}{suffix}"


def _head_noun_to_genitive(word: str) -> str:
    if "-" in word:
        left, right = word.split("-", 1)
        return f"{_head_noun_to_genitive(left)}-{_head_noun_to_genitive(right)}"
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low in {"меню", "кофе"}:
        changed = core
    elif low.endswith("ения") or low.endswith("ания") or low.endswith("яния"):
        changed = core[:-1] + "й"
    elif low.endswith("ение") or low.endswith("ание") or low.endswith("яние"):
        changed = core[:-1] + "я"
    elif low.endswith("ства"):
        changed = core[:-1]
    elif low.endswith("лки"):
        changed = core[:-2] + "ок"
    elif low.endswith("ши"):
        changed = core[:-1]
    elif low.endswith("ки") and len(core) > 3:
        changed = core[:-1] + "а"
    elif low.endswith("ку"):
        changed = core[:-1] + "и"
    elif low.endswith("ту"):
        changed = core[:-1] + "ы"
    elif low.endswith("у") and len(core) > 3:
        changed = core[:-1] + ("и" if core[-2].casefold() in "кгхжчшщ" else "ы")
    elif low.endswith("ны"):
        changed = core[:-1]
    elif low.endswith("ы") and len(core) > 3:
        changed = core[:-1] + "ов"
    elif low.endswith("ости"):
        changed = core[:-1] + "ей"
    elif low.endswith("ь"):
        changed = core[:-1] + "и"
    elif low.endswith("й") and len(core) > 2 and core[-2].casefold() in "аеёиоуыэюя":
        changed = core[:-1] + "я"
    elif not re.search(r"(?i)[аеёиоуыэюя]$", low):
        changed = core + "а"
    else:
        changed = core
    return f"{prefix}{changed}{suffix}"


def _split_prep_tail(words: list[str]) -> tuple[list[str], list[str]]:
    for index, word in enumerate(words):
        if _is_preposition(word):
            return words[:index], words[index:]
    return words, []


def _phrase_to_genitive(phrase: str) -> str:
    words = _normalize_spaces(phrase).split()
    if not words:
        return phrase
    head, tail = _split_prep_tail(words)
    if not head:
        return _normalize_spaces(phrase)
    if len(head) >= 2 and all(_is_adjective(word) or word.casefold().endswith(("ую", "юю", "ая")) for word in head[:-1]):
        head = [_adj_to_genitive(word) for word in head[:-1]] + [_head_noun_to_genitive(head[-1])]
    else:
        head = [_head_noun_to_genitive(head[0]), *head[1:]]
    return _normalize_spaces(" ".join((*head, *tail)))


def _phrase_to_dative_noun(noun: str) -> str:
    low = noun.casefold()
    if low.endswith("ия"):
        return noun[:-2] + "ию"
    if low.endswith("ие"):
        return noun[:-1] + "ю"
    if low.endswith("ки"):
        return noun[:-1] + "е"
    if low.endswith("а"):
        return noun[:-1] + "е"
    return noun


def _title_part_to_acc(part: str) -> str:
    words = _normalize_spaces(part).split()
    if not words:
        return part
    last = words[-1]
    low = last.casefold()
    if low.endswith("ия"):
        words[-1] = last[:-2] + "ию"
    elif low == "земляки":
        words[-1] = "земляков"
        words = [
            (word[:-2] + "ых" if word.casefold().endswith("ые") else word)
            for word in words[:-1]
        ] + [words[-1]]
    return _normalize_spaces(" ".join(words))


def _place_to_genitive(part: str) -> str:
    words = []
    for index, word in enumerate(_normalize_spaces(part).split()):
        low = word.casefold()
        if low.endswith(("ой", "ый")) and len(word) > 3:
            word = word[:-2] + "ого"
        elif low.endswith("й") and len(word) > 2 and word[-2].casefold() in "аеёиоуыэюя":
            word = word[:-1] + "я"
        if index == 0 and word[:1].isupper():
            word = word[:1].lower() + word[1:]
        words.append(word)
    return _normalize_spaces(" ".join(words))


def _knowledge_result_from_title(topic_title: str) -> str:
    parts = [
        re.sub(r"(?i)^(его|её|ее|их)\s+", "", part.strip().rstrip("."))
        for part in (topic_title or "").split(",")
        if part.strip()
    ]
    if not parts:
        return ""
    context = ""
    if _is_place_title_part(parts[0]):
        context = _place_to_genitive(parts[0])
        parts = parts[1:]
    objects = []
    for part in parts:
        cleaned = _drop_geo_tail(part)
        if cleaned:
            objects.append(_title_part_to_acc(cleaned))
    if not objects:
        return ""
    if context and len(objects) >= 2:
        objects[-2] = _normalize_spaces(f"{objects[-2]} {context}")
    elif context:
        objects[-1] = _normalize_spaces(f"{objects[-1]} {context}")
    if len(objects) == 1:
        joined = objects[0]
    else:
        joined = ", ".join(objects[:-1]) + " и " + objects[-1]
    return _cap_sentence(_normalize_spaces(f"характеризует {joined}"))


def _named_form_control(result: str, frame: ActionFrame, lesson_type: str) -> str:
    selected = _normalize_spaces(f"{result} {frame.clause} {lesson_type}").casefold()
    if "диктант" in frame.clause.casefold():
        if "топограф" in frame.clause.casefold() or "знак" in result.casefold():
            return "топографический диктант"
        return "диктант"
    if "викторин" in selected:
        if "краевед" in selected:
            return "краеведческая викторина"
        return "викторина"
    return ""


def _short_object(text: str, *, keep_first_prep: bool = True) -> str:
    focus = re.sub(r"\s*\([^)]*\)", "", _normalize_spaces(text)).strip(" .;:")
    if not focus:
        return ""
    tokens = focus.split()
    content: list[str] = []
    prep_phrase: list[str] = []
    in_prep = False
    for token in tokens:
        if token in {":", ",", ";"}:
            break
        if token.casefold() in {"и", "или"} and not in_prep:
            break
        if not in_prep and _is_preposition(token):
            if not keep_first_prep or prep_phrase or token.casefold() not in {"по", "на", "с"}:
                break
            in_prep = True
        if in_prep:
            if prep_phrase and (_is_preposition(token) or token.casefold() in {"и", "или"}):
                break
            prep_phrase.append(token)
            if len(prep_phrase) >= 3:
                break
            continue
        if token.casefold().endswith("о") and content and not _is_adjective(token):
            continue
        content.append(token)
    if len(content) >= 3 and all(
        _is_adjective(word) or word.casefold() in {"свой", "своя", "свое", "свои"}
        for word in content[:-1]
    ):
        content = content[-1:]
    return _normalize_spaces(" ".join((*content, *prep_phrase)))


def _result_actions(result: str) -> list[tuple[str, str]]:
    text = result.rstrip(".")
    matches = list(_FINITE_VERB_RE.finditer(text))
    if not matches:
        return []
    actions: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        obj = text[match.end() : end].strip(" ,.;")
        obj = re.sub(r"^(и|а|но)\s+", "", obj, flags=re.IGNORECASE)
        actions.append((match.group(0).casefold(), obj))
    return actions


def _product_control(result: str) -> str:
    text = result.rstrip(".")
    low = text.casefold()
    report = re.fullmatch(r"(?i)составляет отч[её]т (.+)", text)
    if report:
        return f"проверка отчёта {report.group(1)}"
    if "меню" in low:
        if any(stem in low for stem in ("костр", "готов", "приготов")):
            return "проверка меню и приготовления пищи на костре"
        return "проверка меню"
    if "план-график" in low or "плана-график" in low:
        parts = [
            _phrase_to_genitive(match.group(0))
            for match in re.finditer(
                r"(?i)план-график(?:\s+(?!и\b)\S+)?|план\s+(?!график)[а-яё]+(?:\s+(?!и\b)[а-яё]+)?",
                text,
            )
        ]
        if parts:
            return "проверка " + " и ".join(parts)
        return "проверка плана-графика"
    if "дневник" in low:
        focus = re.search(r"(?i)дневник(?:\s+\S+)?", text)
        raw = focus.group(0) if focus else "дневник"
        return "проверка " + _phrase_to_genitive(raw.casefold())
    if "аптечк" in low:
        match = re.search(r"(?i)((?:[а-яё]+ ){0,2}аптечк[а-яё]*)", low)
        phrase = match.group(1).strip() if match else "аптечки"
        return "проверка состава " + _phrase_to_genitive(phrase)
    if "доклад" in low:
        match = re.search(r"(?i)доклад\w*(?:\s+по\s+.+)?", text)
        raw = match.group(0).rstrip(" .") if match else "доклады"
        raw = re.sub(r"(?i)^доклад[а-яё]*", "докладов", raw, count=1)
        return "проверка " + raw
    if re.search(r"(?i)проводит(?:\s+\S+)?\s+наблюден", low):
        rest = re.sub(r"(?i)^проводит\s+", "", text)
        focus = re.search(r"(?i)((?:[а-яё]+ )?наблюден\w*(?:\s+за\s+[^.,;]+)?)", rest)
        phrase = focus.group(1) if focus else "наблюдений"
        return "проверка " + _phrase_to_genitive(phrase)
    if not any(head in low for head in _PRODUCT_HEADS):
        return ""
    return ""


def _imitation_or_route_control(result: str) -> str:
    low = result.casefold()
    if "мини-маршрут" in low or "мини маршрут" in low or "движен" in low and "легенд" in low:
        return "маршрутное задание"
    if "условно" in low or "имитац" in low:
        actions = _result_actions(result)
        if actions:
            verb, obj = actions[0]
            noun = _FINITE_TO_NOUN.get(verb, "выполнения")
            core = re.sub(r"(?i)\s+условно\b.*", "", obj)
            focus = _phrase_to_genitive(_short_object(core, keep_first_prep=False))
            dative = _phrase_to_dative_noun(noun)
            body = _normalize_spaces(f"{dative} {focus}").strip()
            if "условно" in low:
                rest = re.search(r"(?i)условно\s+\S+", result)
                if rest:
                    body = _normalize_spaces(f"{body} {rest.group(0)}")
            return f"практическое задание по {body}".rstrip(" .")
    return ""


def _process_control(result: str, lesson_type: str) -> str:
    low = result.casefold()
    type_low = lesson_type.casefold()
    if low.startswith("выполняет упражнения"):
        return "педагогическое наблюдение за выполнением упражнений"
    if low.startswith("отрабатывает технику") or "отрабатывает технику" in low:
        rest = re.sub(r"(?i)^отрабатывает технику\s*", "", result).rstrip(".")
        rest = rest.split(":")[0]
        rest = re.split(r"(?i),\s+организ", rest)[0]
        words = [word for word in _short_object(rest, keep_first_prep=False).split() if not _is_adjective(word)]
        if any(word.startswith("преодолен") or word.startswith("препятств") for word in words):
            return "педагогическое наблюдение за техникой преодоления препятствий"
        if any(word.startswith("движен") for word in words):
            return "педагогическое наблюдение за техникой движения"
        if words:
            return "педагогическое наблюдение за техникой " + " ".join(words[:2])
        return "педагогическое наблюдение за техникой"
    if "развертывает" in low and any(stem in low for stem in ("лагер", "бивак")):
        return "педагогическое наблюдение при развертывании и свертывании лагеря"
    if "экскурси" in type_low or low.startswith("совершает прогул") or low.startswith("совершает экскурси"):
        return "педагогическое наблюдение на экскурсии"
    return ""


def _skill_control(result: str) -> str:
    actions = _result_actions(result)
    if not actions:
        return ""
    verbs = [verb for verb, _obj in actions]
    if (
        all(verb in _EXERCISE_SKILL_VERBS for verb in verbs)
        and not any(word in result.casefold() for word in ("график", "меню", "план", "отчёт", "отчет"))
    ):
        nouns = []
        objects = []
        for verb, obj in actions:
            nouns.append(_phrase_to_dative_noun(_FINITE_TO_NOUN[verb]))
            short = _short_object(obj, keep_first_prep=False)
            if short:
                objects.append(_phrase_to_genitive(short.split(",")[0]))
        object_text = objects[0] if objects else ""
        if len(nouns) == 1:
            body = _normalize_spaces(f"{nouns[0]} {object_text}")
        else:
            body = _normalize_spaces(f"{' и '.join(nouns)} {object_text}")
        return f"практическое задание по {body}".rstrip()
    if any(verb == "изготавливает" for verb, _obj in actions) and any(
        "транспортир" in obj.casefold() or verb == "разучивает" for verb, obj in actions
    ):
        made = []
        for verb, obj in actions:
            if verb == "изготавливает":
                made.extend(
                    _phrase_to_genitive(part.strip())
                    for part in obj.split(",")
                    if part.strip()
                )
            elif "транспортир" in obj.casefold():
                transport = re.search(r"(?i)способ\w*\s+транспортиров\w*", obj)
                if transport:
                    made.append(_phrase_to_genitive(transport.group(0)))
        if made:
            if len(made) == 1:
                joined = made[0]
            else:
                joined = ", ".join(made[:-1]) + " и " + made[-1]
            return "проверка изготовления " + joined
    pieces: list[str] = []
    for verb, obj in actions:
        noun = _FINITE_TO_NOUN[verb]
        short = _short_object(obj)
        if verb == "выполняет" and "обязанност" in obj.casefold():
            noun = "исполнения"
            short = "обязанностей по должностям"
        elif verb == "выступает" and "соревнован" in obj.casefold():
            noun = "участия"
            short = "в соревнованиях"
        elif verb == "строит" and "график" in obj.casefold():
            noun = ""
            graph = re.search(r"(?i)график\w*(?:\s+перевода пар шагов в метры)?", obj)
            short = _phrase_to_genitive(graph.group(0) if graph else "график")
        elif verb in {
            "укладывает",
            "подгоняет",
            "ориентирует",
            "отбирает",
            "применяет",
            "изготавливает",
            "разучивает",
            "измеряет",
            "рисует",
            "составляет",
        }:
            short = _phrase_to_genitive(short)
        if noun and short:
            pieces.append(_normalize_spaces(f"{noun} {short}"))
        elif short:
            pieces.append(short)
        elif noun:
            pieces.append(noun)
    if not pieces:
        return ""
    return "проверка " + " и ".join(pieces)


def control_from_frame(
    frame: ActionFrame,
    *,
    lesson_type: str,
    theory_hours: int,
    practice_hours: int,
    planned_result: str = "",
) -> str:
    # Control is a verification method for the selected result, never its infinitive clone.
    type_low = lesson_type.casefold()
    if "теоретическ" in type_low or "беседа" in type_low or (
        theory_hours and not practice_hours
    ):
        return _oral_quiz_control(frame, planned_result)
    named = _named_form_control(planned_result, frame, lesson_type)
    if named:
        return named
    product = _product_control(planned_result)
    if product:
        return product
    imitation = _imitation_or_route_control(planned_result)
    if imitation:
        return imitation
    process = _process_control(planned_result, lesson_type)
    if process:
        return process
    skill = _skill_control(planned_result)
    if skill:
        return skill
    return _oral_quiz_control(frame, planned_result)


def type_from_frame(
    frame: ActionFrame,
    *,
    theory_hours: int,
    practice_hours: int,
    theory_text: str,
    practice_text: str,
    program_content: str,
    planned_result: str = "",
) -> str:
    # A form names an evidenced activity; it never invents a lesson scenario.
    if practice_hours and practice_text.strip() and planned_result:
        result = planned_result.casefold()
        clause = frame.clause.casefold()
        if "викторин" in result or re.search(
            r"(?i)(?:проведен|провод).{0,40}викторин", clause
        ):
            return "викторина"
        if "экскурси" in result and any(x in result for x in ("совершает", "посещает")):
            return "экскурсия"
        if result.startswith("исследует") and any(x in clause for x in ("гипотез", "сравнен", "измерен", "наблюден")):
            return "исследовательское занятие"
        if "имитац" in clause and "ситуаци" in clause and "действ" in clause:
            return "ситуационный тренинг"
        if "составляет" in result and "план" in result and "план-график" in result:
            return "проектно-практическое занятие"
        if result.startswith(("проводит наблюдения", "проводит краеведческие наблюдения", "наблюдает")):
            return "занятие-наблюдение"
        if result.startswith(("выполняет упражнения", "отрабатывает", "разучивает")):
            if "движени" in result and "местности" in result:
                return "учебно-тренировочное занятие на местности"
            return "учебно-тренировочное занятие"
        if _result_as_task(planned_result):
            if "снаряжени" in result or "рюкзак" in result:
                return "практикум по работе со снаряжением"
            if "меню" in result and "продукт" in result:
                return "практикум по организации питания"
            if "отчёт" in result or "отчет" in result:
                return "практикум по подготовке отчёта"
            if "гигиен" in result:
                return "практикум по личной гигиене"
            if "азимут" in result or (
                re.search(r"\bкарт(?:у|е|ы|ой)\b", result)
                and any(x in result for x in ("компас", "ориентир", "маршрут"))
            ):
                return "топографический практикум"
            return "практикум"
    lead = _leading_clause(frame)
    scores = _line_form_scores(lead)
    if practice_hours and lead:
        dominant = _dominant_label(scores, min_score=2)
        if dominant in {"игра", "викторина"} and not re.match(
            r"(?i)^(игр|викторин)", lead
        ):
            dominant = None
        if dominant and dominant not in {"беседа", "исследовательское занятие", "ситуационное занятие"}:
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


_TASK_VERBS = {
    "укладывает": "уложить", "подгоняет": "подогнать",
    "составляет": "составить", "готовит": "приготовить",
    "развертывает": "развернуть", "свертывает": "свернуть",
    "выполняет": "выполнить", "отрабатывает": "отработать",
    "организует": "организовать", "выступает": "выступить",
    "оценивает": "оценить", "измеряет": "измерить",
    "определяет": "определить", "отбирает": "отобрать",
    "распознаёт": "распознать", "ориентирует": "ориентировать",
    "строит": "построить", "совершает": "совершить",
    "посещает": "посетить", "подготавливает": "подготовить",
    "заслушивает": "заслушать", "проводит": "провести",
    "применяет": "применить", "формирует": "сформировать",
    "оказывает": "оказать", "изготавливает": "изготовить",
    "разучивает": "разучить", "ведёт": "вести",
    "рисует": "нарисовать", "сравнивает": "сравнить",
    "решает": "решить", "исследует": "исследовать",
}


def _result_as_task(result: str) -> str:
    """Closed grammatical conversion; objects and conditions stay verbatim."""
    text = result.rstrip(".")
    if not text or text.split()[0].casefold() not in _TASK_VERBS:
        return ""
    return re.sub(
        r"\b(" + "|".join(_TASK_VERBS) + r")\b",
        lambda match: _TASK_VERBS[match.group().casefold()],
        text, flags=re.IGNORECASE,
    )


def _observable_result(result: str) -> str:
    """Remove exercise wrappers only for explicitly named observable operations."""
    if result.startswith("Выполняет упражнения"):
        parts = re.split(r" и упражнения ", result.removeprefix("Выполняет упражнения ").rstrip("."))
        converted = []
        for part in parts:
            match = re.fullmatch(
                r"(?:по|на) (определению|определение|отбору|отбор|измерению|измерение|запоминание|глазомерную оценку|инструментальное измерение) (.+)", part,
            )
            if match is None:
                return result
            operation, rest = match.groups()
            verbs = {
                "определению": "определяет", "определение": "определяет",
                "отбору": "отбирает", "отбор": "отбирает",
                "измерению": "измеряет", "измерение": "измеряет",
                "запоминание": "распознаёт", "глазомерную оценку": "оценивает",
                "инструментальное измерение": "измеряет",
            }
            obj, conditions = _split_object_and_conditions(rest)
            phrase = f"{verbs[operation]} {_inflect_object_phrase(obj, case='acc')}"
            if operation == "глазомерную оценку":
                phrase += " глазомерно"
            if conditions:
                phrase += f" {conditions}"
            converted.append(phrase)
        return _cap_sentence(" и ".join(converted))
    # Parenthetical action lists are details, not extra outcomes for the lesson.
    result = re.sub(
        r"\s*\(([^()]*)\)",
        lambda m: "" if _paren_has_actions(m.group(1)) or _is_finite_result_phrase(m.group(1)) else m.group(0),
        result,
    )
    return result.replace("Проводит различные наблюдения", "Проводит наблюдения").replace("Проводит различные краеведческие наблюдения", "Проводит краеведческие наблюдения")


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
        topic_title=topic_title,
    )
    if not planned_result:
        planned_result, frame = transform_clause_to_result(
            topic_title,
            theory_only=True,
            full_source=topic_title,
            topic_title=topic_title,
        )
    planned_result = _observable_result(planned_result)
    if (
        _result_restates_named_form(planned_result)
        and theory_text.strip()
        and _title_has_knowledge_beyond_form(topic_title)
    ):
        knowledge = _knowledge_result_from_title(topic_title)
        if knowledge and not _result_restates_named_form(knowledge):
            planned_result = knowledge
    lesson_type = type_from_frame(
        frame,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        planned_result=planned_result,
    )
    assessment = control_from_frame(
        frame,
        lesson_type=lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        planned_result=planned_result,
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


def _row_week_parts(row: CalendarContentRow) -> tuple[WeekTopicPart, ...]:
    if row.week_parts:
        return row.week_parts
    return (
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


def _topic_hour_totals(
    parts: tuple[WeekTopicPart, ...],
) -> dict[tuple[str | None, str, str], tuple[int, int]]:
    totals: dict[tuple[str | None, str, str], tuple[int, int]] = {}
    for part in parts:
        key = (part.topic_number, part.topic_title, part.section)
        theory, practice = totals.get(key, (0, 0))
        totals[key] = (theory + part.theory_hours, practice + part.practice_hours)
    return totals


def _part_texts(
    part: WeekTopicPart,
    topic_totals: dict[tuple[str | None, str, str], tuple[int, int]],
) -> tuple[str, str]:
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
    return theory_text, practice_text


def _split_row_texts(row: CalendarContentRow) -> tuple[str, str, list[str]]:
    warnings: list[str] = list(row.warnings)
    parts = _row_week_parts(row)
    topic_totals = _topic_hour_totals(parts)
    theory_parts: list[str] = []
    practice_parts: list[str] = []
    for part in parts:
        theory_text, practice_text = _part_texts(part, topic_totals)
        if theory_text:
            theory_parts.append(theory_text)
        if practice_text:
            practice_parts.append(practice_text)
    return "\n".join(theory_parts), "\n".join(practice_parts), warnings


def _unique_phrases(phrases: list[str]) -> list[str]:
    unique: list[str] = []
    for phrase in phrases:
        normalized = _normalize_spaces(phrase).rstrip(" .")
        if not normalized:
            continue
        folded = normalized.casefold()
        if any(folded == _normalize_spaces(item).rstrip(" .").casefold() for item in unique):
            continue
        superseded = False
        for index, item in enumerate(unique):
            item_fold = _normalize_spaces(item).rstrip(" .").casefold()
            if folded in item_fold:
                superseded = True
                break
            if item_fold in folded:
                unique[index] = _normalize_spaces(phrase)
                superseded = True
                break
        if not superseded:
            unique.append(_normalize_spaces(phrase))
    return unique


def _join_and(parts: list[str]) -> str:
    cleaned = [_normalize_spaces(part).rstrip(" .") for part in parts if _normalize_spaces(part)]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + " и " + cleaned[-1]


def _leading_finite_verb(text: str) -> str:
    match = re.match(r"(?i)^([А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют|ает|яет))\b", text.strip())
    return match.group(1) if match else ""


_SHARED_CONTROL_PREFIXES = (
    "устный опрос по ",
    "практическое задание по ",
    "педагогическое наблюдение за ",
    "педагогическое наблюдение ",
    "проверка ",
)


def _merge_part_results(results: list[str]) -> str:
    unique = _unique_phrases(results)
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0] if unique[0].endswith(".") else unique[0] + "."
    verbs = [_leading_finite_verb(item) for item in unique]
    if all(verbs) and len({verb.casefold() for verb in verbs}) == 1:
        objects = [_drop_leading_verb(item).rstrip(" .") for item in unique]
        return _cap_sentence(f"{verbs[0]} {_join_and(objects)}")
    sentences = [item if item.endswith(".") else f"{item}." for item in unique]
    return _normalize_spaces(" ".join(sentences))


def _merge_part_controls(controls: list[str]) -> str:
    unique = _unique_phrases(controls)
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    folded = [item.casefold() for item in unique]
    for prefix in _SHARED_CONTROL_PREFIXES:
        if all(item.startswith(prefix) for item in folded):
            tails = [item[len(prefix) :].strip() for item in unique]
            return prefix + _join_and(_unique_phrases(tails))
    return "; ".join(unique)


def _merge_week_part_fields(
    derived_parts: list[ContentEngineV2Result],
) -> tuple[str, str, str]:
    types = list(dict.fromkeys(item.lesson_type for item in derived_parts if item.lesson_type))
    lesson_type = types[0] if types else ""
    planned_result = _merge_part_results([item.planned_result for item in derived_parts])
    assessment = _merge_part_controls([item.assessment_method for item in derived_parts])
    return lesson_type, planned_result, assessment


def _derive_week_part(
    part: WeekTopicPart,
    topic_totals: dict[tuple[str | None, str, str], tuple[int, int]],
    occurrence_index: int,
) -> ContentEngineV2Result:
    theory_text, practice_text = _part_texts(part, topic_totals)
    return derive_fields_v2(
        topic_title=part.topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=part.program_content_full or "",
        theory_hours=part.theory_hours,
        practice_hours=part.practice_hours,
        occurrence_index=occurrence_index,
    )


def build_lesson_content_v2(
    rows: tuple[CalendarContentRow, ...],
) -> tuple[LessonContentV2Row, ...]:
    """Построить поля 2.0 по календарным строкам.

    Pipeline вызывает только при внутреннем флаге USE_CONTENT_ENGINE_V2.
    """

    result: list[LessonContentV2Row] = []
    topic_occurrences: dict[tuple[str | None, str, str], int] = {}
    for row in rows:
        parts = _row_week_parts(row)
        theory_text, practice_text, warnings = _split_row_texts(row)
        if len(parts) > 1:
            derived_parts: list[ContentEngineV2Result] = []
            for part in parts:
                key = (part.topic_number, part.topic_title, part.section)
                occurrence_index = topic_occurrences.get(key, 0)
                topic_occurrences[key] = occurrence_index + 1
                derived_parts.append(
                    _derive_week_part(part, _topic_hour_totals(parts), occurrence_index)
                )
            lesson_type, planned_result, assessment = _merge_week_part_fields(derived_parts)
            derived = derived_parts[0]
            extra_warnings = tuple(
                warning for item in derived_parts for warning in item.warnings
            )
        else:
            key = (row.topic_number, row.topic_title, row.section)
            occurrence_index = topic_occurrences.get(key, 0)
            topic_occurrences[key] = occurrence_index + 1
            derived = derive_fields_v2(
                topic_title=row.topic_title,
                theory_text=theory_text,
                practice_text=practice_text,
                program_content=row.program_content_full or "",
                theory_hours=row.theory_hours,
                practice_hours=row.practice_hours,
                occurrence_index=occurrence_index,
            )
            lesson_type = derived.lesson_type
            planned_result = derived.planned_result
            assessment = derived.assessment_method
            extra_warnings = derived.warnings
        result.append(
            LessonContentV2Row(
                source=row,
                theory_text=theory_text,
                practice_text=practice_text,
                lesson_type=lesson_type,
                planned_result=planned_result,
                assessment_method=assessment,
                action=derived.frame.action,
                object=derived.frame.object,
                conditions=derived.frame.conditions,
                warnings=tuple(dict.fromkeys((*warnings, *extra_warnings))),
            )
        )
    return tuple(result)
