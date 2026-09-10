"""Content Engine 2.0: детерминированные поля занятия без ИИ.

Параллельный модуль. Content Engine 1.0 не меняет.
Подключается к pipeline только через внутренний флаг USE_CONTENT_ENGINE_V2.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import re
from difflib import SequenceMatcher

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
    finalize_lesson_type,
)
from calendar_pedagoga.practice_slots import (
    SLOT_CONTINUE_WARNING,
    SLOT_PACK_WARNING,
    assign_distributed_practice_slots,
    practice_units_from_text,
)


logger = logging.getLogger(__name__)


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
    "уход": "ухаживает",
    "участие": "участвует",
    "отыскание": "находит",
    "фасовка": "фасует",
    "чтение": "читает",
    "закупка": "закупает",
    "ремонт": "ремонтирует",
    "ведение": "ведёт",
    "выступление": "выступает",
    "формирование": "формирует",
    "оценка": "оценивает",
    "отбор": "отбирает",
    "заслушивание": "заслушивает",
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

# Knowledge heads that already satisfy the characterize-object case heuristic
# or name a closed pedagogical object, never a topic/programme label.
_THEORY_KNOWLEDGE_HEADS = _KNOWLEDGE_NOUNS | {
    "составляющие",
    "устройство",
    "назначение",
    "требования",
    "правила",
    "применение",
    "строение",
}

_INTERROGATIVE_START_RE = re.compile(
    r"(?i)^(что|кто|как|почему|зачем|когда|где|куда|откуда|"
    r"какой|какая|какое|какие|чем)\b"
)

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
    "обо",
    "между",
    "перед",
    "над",
    "под",
    "без",
    "до",
    "за",
    "про",
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
    if re.search(r"(?i)(?:ение|ание|яние|ствие|ений|аний|яний|ствий|ций)$", core):
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
    if word.casefold() == "мест":
        return "места"
    if word.casefold() == "костра":
        return "костёр"
    if word.casefold() == "обуви":
        return "обувь"
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
    if low.endswith("ций") and len(word) > 5:
        return word[:-1] + "и"
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
    if low.endswith("ия") and len(word) > 5:
        return word[:-1] + "е"
    if low.endswith("ости") and len(word) > 5:
        return word[:-1] + "ь"
    if low.endswith("а") and len(word) > 3:
        stem = word[:-1]
        if stem.casefold().endswith("к") and len(stem) >= 2:
            before_k = stem[-2].casefold()
            if before_k not in "аеёиоуыэюя":
                return stem[:-1] + "ок"
        # Neuter gen.sg / nom.pl «места» is 2 syllables; stripping -а yields
        # gen.pl «мест». Masc. gen.sg «стола» / «натюрморта» stay convertible.
        vowels = re.findall(r"(?i)[аеёиоуыэюя]", low)
        if len(vowels) == 2 and re.search(r"(?i)[^аеёиоуыэюя][^аеёиоуыэюя]$", stem):
            return word
        return stem
    if low.endswith("я") and len(word) > 3:
        stem = word[:-1]
        # Short neuter gen.sg «поля» / «моря» would become «поль»; do not guess.
        # Longer masc. ь-stems «лагеря» → «лагерь» remain regular.
        if len(stem) <= 3:
            return word
        return stem + "ь"
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
    if "-" in word:
        parts = word.split("-")
        if all(_participation_lemma(part) is not None for part in parts):
            return "-".join(_noun_to_prepositional(part) for part in parts)
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


class _UncertainGrammar(ValueError):
    """Internal abstention, not a change to the CE2 output contract."""


def _require_simple_inflection(phrase: str) -> None:
    # No dependency parser: coordinated heads, lists and nested clauses are
    # outside the supported grammar. Prepositional tails are copied verbatim.
    head = re.split(r"(?i)\s+(?:по|для|при|на|в|с|со|к|от|из)\s+", phrase, maxsplit=1)[0]
    if re.search(r"[,;:()«»\"]|\b(?:и|или|а|как|котор\w*)\b", head, re.I):
        raise _UncertainGrammar("coordinated_or_nested_phrase")


def _inflect_object_phrase(phrase: str, *, case: str) -> str:
    tokens = re.findall(r"\s+|[^\s]+", phrase)
    out: list[str] = []
    seen_noun = False
    has_post_head = False
    colon_list = False
    in_pp = False
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
        if _is_preposition(core):
            in_pp = True
        if in_pp and case == "acc":
            if pending:
                apply_pending(False, "m")
            out.append(token)
            continue
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
    text = "".join(out)
    return text.replace("места, пригодных", "места, пригодные")


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


def _complements_after_finite(remainder: str) -> tuple[str, str]:
    """Direct object may be inflected; a leading governed PP is copied as-is."""

    text = _normalize_spaces(remainder)
    if not text:
        return "", ""
    first = _strip_punct_word(text.split()[0])[1]
    if _is_preposition(first):
        return "", text
    obj, cond = _split_object_and_conditions(text)
    obj_acc = _inflect_object_phrase(obj, case="acc") if obj else ""
    return obj_acc, cond


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
    # An ending is not evidence of a verb, its meaning or its valency.
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


def _leading_activity_token(clause: str) -> str:
    """Head of the selected activity, ignoring adjectives and hyphen tails."""

    tokens = _normalize_spaces(clause).split()
    _mods, rest = _leading_modifiers(tokens)
    if not rest:
        return ""
    raw = rest[0].strip(" «»\"'(),.;:")
    if not raw:
        return ""
    return re.split(r"[^\wёЁ]", raw, maxsplit=1)[0]


def _is_leading_game_form(token: str) -> bool:
    # Genitive remnant «игр» is not a leading game activity.
    return bool(re.match(r"(?i)^(игр(?:а|ы|у|е|ами|ах)|эстафет\w*)$", token))


def _is_leading_form_activity(token: str) -> bool:
    if not token:
        return False
    if _is_walk_word(token) or _is_exercise_word(token) or _is_leading_game_form(token):
        return True
    return bool(re.match(r"(?i)^викторин\w*$", token))


# Closed nominal-activity frames: the source names an observable activity NP,
# but that lemma has no proven finite conjugation. Map only onto verbs that
# are already in the proven predicate set. Never invent a verb from a suffix.
_NOMINAL_PERFORM_LEMMAS = frozenset({"висы", "закаливание", "катание", "лазание", "сдача", "тренировка"})
_ACTIVITY_GLOSS_RE = re.compile(r"\s+[–—−]\s+|\s+-\s+")


def _nominal_activity_lemma(word: str) -> str:
    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE).casefold()
    if re.fullmatch(r"помощ[ьи]", core):
        return "помощь"
    if re.fullmatch(r"поездк[аиуеы]|поездок", core):
        return "поездка"
    return _verbal_noun_lemma(word).casefold()


def _decap_lexical(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    if not core or re.match(r"^[А-ЯЁ]{2,}$", core):
        return word
    if core[:1].isupper():
        core = core[:1].lower() + core[1:]
    return f"{prefix}{core}{suffix}"


def _aid_activity_evidence(mods: list[str], remainder: str) -> bool:
    for mod in mods:
        stem = re.sub(r"[^\wёЁ]", "", mod, flags=re.IGNORECASE).casefold()
        if stem.startswith(("перв", "доврачебн")):
            return True
    return bool(re.search(r"(?i)(?:^|\s)при\s", remainder))


def _trip_activity_evidence(mods: list[str]) -> bool:
    return any(
        re.sub(r"[^\wёЁ]", "", mod, flags=re.IGNORECASE).casefold().startswith("экскурсионн")
        for mod in mods
    )


def _head_core_and_remainder(rest: list[str]) -> tuple[str, str]:
    _prefix, core, suffix = _strip_punct_word(rest[0])
    leftover: list[str] = []
    colon = "".join(char for char in suffix if char == ":")
    if colon:
        leftover.append(colon)
    leftover.extend(rest[1:])
    remainder = _normalize_spaces(" ".join(leftover))
    if remainder.startswith(":"):
        remainder = ": " + remainder[1:].lstrip()
    return core, remainder


def _append_remainder(phrase: str, remainder: str) -> str:
    if not remainder:
        return phrase
    if remainder[:1] in {":", ";", ","}:
        return _normalize_spaces(phrase + remainder)
    return _normalize_spaces(f"{phrase} {remainder}")


def _match_nominal_activity_np(text: str) -> tuple[str, str, str, str] | None:
    """Safe NP → finite RESULT. Returns None when the action cannot be proven."""

    tokens = _normalize_spaces(text).split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return None
    head, remainder = _head_core_and_remainder(rest)
    lemma = _nominal_activity_lemma(head)
    if lemma == "помощь":
        if not _aid_activity_evidence(mods, remainder):
            return None
        acc_mods = [
            _decap_lexical(_adj_to_acc(mod, plural=False, gender="f"))
            for mod in mods
        ]
        np_words = [*acc_mods, _decap_lexical(head)]
        phrase = _append_remainder("оказывает " + " ".join(np_words), remainder)
        obj, cond = _split_object_and_conditions(
            _normalize_spaces(" ".join([*acc_mods, "помощь", remainder]))
        )
        return phrase, "помощь", obj, cond
    if lemma in _NOMINAL_PERFORM_LEMMAS:
        if lemma == "сдача" and re.match(r"(?i)^норматив\w*", remainder):
            obj_acc, cond = _complements_after_finite(remainder)
            phrase = "выполняет"
            if obj_acc:
                phrase += f" {obj_acc}"
            if cond:
                phrase += f" {cond}"
            obj, _split_cond = _split_object_and_conditions(remainder)
            return _normalize_spaces(phrase), "выполнение", obj, cond
        if lemma == "тренировка":
            obj_acc, cond = _complements_after_finite(remainder)
            phrase = "отрабатывает"
            if obj_acc:
                phrase += f" {obj_acc}"
            if cond:
                phrase += f" {cond}"
            obj, _split_cond = _split_object_and_conditions(remainder)
            return _normalize_spaces(phrase), "отработка", obj, cond
        np_words = [_decap_lexical(mod) for mod in mods] + [
            _decap_lexical(_noun_nom_to_acc(head))
        ]
        phrase = _append_remainder("выполняет " + " ".join(np_words), remainder)
        obj, cond = _split_object_and_conditions(remainder)
        return phrase, lemma, obj, cond
    if lemma == "поездка":
        if not _trip_activity_evidence(mods):
            return None
        plural = head.casefold() in {"поездки", "поездок"}
        if plural:
            acc_mods = [_decap_lexical(_adj_to_acc(mod, plural=True, gender="f")) for mod in mods]
            acc_head = _decap_lexical(head)
        else:
            acc_mods = [_decap_lexical(_adj_to_acc(mod, plural=False, gender="f")) for mod in mods]
            acc_head = _decap_lexical(_noun_nom_to_acc(head))
        phrase = _append_remainder("совершает " + " ".join([*acc_mods, acc_head]), remainder)
        obj, cond = _split_object_and_conditions(remainder.lstrip(": ").strip())
        return phrase, "поездка", obj, cond
    return None


def _nominal_activity_result(text: str) -> tuple[str, str, str, str] | None:
    """Universal layer: nominal activity NP → observable finite RESULT."""

    direct = _match_nominal_activity_np(text)
    if direct:
        return direct
    label, separator, tail = text.partition(":")
    if (
        separator
        and tail.strip()
        and _line_form_scores(label).get("тестирование", 0) >= 2
    ):
        diagnosed = _match_nominal_activity_np(tail.strip())
        if diagnosed:
            return diagnosed
    gloss = _ACTIVITY_GLOSS_RE.search(text)
    if gloss:
        tail = text[gloss.end() :].strip(" ,")
        if tail:
            return _match_nominal_activity_np(tail)
    return None


# Closed form-nouns that TYPE already recognizes. RESULT must emit the same
# locative nouns that the quality gate accepts after «участвует в».
_PARTICIPATION_CASES = frozenset({
    "играх",
    "игре",
    "эстафетах",
    "эстафете",
    "занятиях",
    "занятии",
    "викторине",
    "викторинах",
    "конкурсе",
    "конкурсах",
    "соревнованиях",
    "соревновании",
    "походе",
    "походах",
})
_CREATIVE_HEAD_RE = re.compile(
    r"(?i)^(аппликаци|конструирован|рисован|рисунк|лепк)"
)


def _participation_lemma(token: str) -> str | None:
    core = re.sub(r"[^\wёЁ]", "", token, flags=re.IGNORECASE).casefold()
    if re.fullmatch(r"игр(?:а|ы|у|е|ами|ах)", core):
        return "игра"
    if core.startswith("эстафет"):
        return "эстафета"
    if core.startswith("викторин"):
        return "викторина"
    if core.startswith("заняти"):
        return "занятие"
    if core == "поход":
        return "поход"
    if re.fullmatch(r"походы|походов|походам|походами|походах", core):
        return "походы"
    return None


def _participation_locative(lemma: str) -> str:
    return {
        "игра": "играх",
        "эстафета": "эстафетах",
        "викторина": "викторине",
        "занятие": "занятиях",
        "поход": "походе",
        "походы": "походах",
    }[lemma]


def _adj_to_participation_locative(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith(("ых", "их")):
        changed = core
    elif low.endswith(("ые", "ая", "ое", "ый", "ой")):
        changed = core[:-2] + "ых"
    elif low.endswith(("ие", "яя", "ее", "ий")) and not low.endswith(("ние", "тие")):
        changed = core[:-2] + "их"
    else:
        changed = core
    return f"{prefix}{changed}{suffix}"


def _one_participation_object(part: str) -> str | None:
    tokens = _normalize_spaces(part).split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return None
    head = re.sub(r"^[«(\"]+|[»)\",;:]+$", "", rest[0])
    lemma = _participation_lemma(head)
    if lemma is None:
        return None
    remainder = " ".join(rest[1:]).strip()
    if lemma == "занятие":
        first = remainder.split()[0] if remainder.split() else ""
        if not first or not _is_preposition(first):
            return None
    loc = _participation_locative(lemma)
    adjs = [_decap_lexical(_adj_to_participation_locative(mod)) for mod in mods]
    words = [*adjs, loc]
    if remainder:
        words.append(remainder)
    return _normalize_spaces(" ".join(words))


def _participation_object_parts(clause: str) -> tuple[list[str], str]:
    text = _normalize_spaces(clause)
    head, _colon, tail = text.partition(":")
    pieces = [
        part.strip()
        for part in re.split(r"(?:,\s+|\s+и\s+)", head)
        if part.strip()
    ]
    objects = []
    for part in pieces:
        built = _one_participation_object(part)
        if built:
            objects.append(built)
    return objects, tail.strip()


def _participation_object_phrase(clause: str) -> str | None:
    objects, tail = _participation_object_parts(clause)
    if not objects:
        return None
    body = ", ".join(objects)
    if tail:
        return f"{body}: {tail}"
    return body


def _embedded_aid_result(text: str) -> tuple[str, str, str, str] | None:
    """Proven «оказание помощи» even when wrapped in приёмы/способы."""

    match = re.search(
        r"(?i)\bоказан\w*\s+((?:[а-яё-]+\s+)*)(помощ[ьи])\b(.*)$",
        _normalize_spaces(text),
    )
    if match is None:
        return None
    adj_span, _help, tail = match.group(1), match.group(2), match.group(3)
    mods = [token for token in adj_span.split() if token]
    if not _aid_activity_evidence(mods, tail):
        if not any(mod.casefold().startswith(("перв", "доврачебн")) for mod in mods):
            return None
    acc_mods = [
        _decap_lexical(_adj_to_acc(mod, plural=False, gender="f"))
        for mod in mods
    ]
    phrase = _append_remainder(
        "оказывает " + " ".join([*acc_mods, "помощь"]).strip(),
        tail.strip(),
    )
    obj, cond = _split_object_and_conditions(
        _normalize_spaces(" ".join([*acc_mods, "помощь", tail]))
    )
    return _normalize_spaces(phrase), "помощь", obj, cond


def _is_foreign_activity_np(part: str) -> bool:
    """Comma-part names a different form-activity, not a complement of this verb."""

    tokens = _normalize_spaces(part).split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return False
    head = re.sub(r"^[«(\"]+|[»)\",;:]+$", "", rest[0])
    if not head or _is_preposition(head):
        return False
    if _CREATIVE_HEAD_RE.match(head):
        return True
    return _participation_lemma(head) is not None


def _keep_proven_action_complements(remainder: str) -> str:
    """Keep only proven objects/PPs of this action; drop foreign NP conjuncts."""

    text = _normalize_spaces(remainder)
    if not text:
        return ""
    parts = re.split(r",\s+", text)
    kept: list[str] = []
    for part in parts:
        if not part:
            continue
        if _is_foreign_activity_np(part):
            break
        kept.append(part)
    return ", ".join(kept)


def _creative_activity_result(text: str) -> tuple[str, str, str, str] | None:
    cleaned = re.sub(
        r"(?i)\s*\(\s*творческая работа\s*\)\s*",
        " ",
        _normalize_spaces(text),
    ).strip(" ,")
    if not cleaned:
        return None
    tokens = cleaned.split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return None
    head = re.sub(r"^[«(\"]+|[»)\",;:]+$", "", rest[0])
    if head.casefold() == "работа" and any(
        mod.casefold().startswith("творческ") for mod in mods
    ):
        return None
    if not (
        _CREATIVE_HEAD_RE.match(head)
        or _line_form_scores(cleaned).get("творческая работа", 0) >= 2
    ):
        return None
    verb = _conjugate_verbal_noun(head)
    remainder = _keep_proven_action_complements(" ".join(rest[1:]).strip())
    if verb:
        obj_acc, cond = _complements_after_finite(remainder)
        phrase = verb
        if obj_acc:
            phrase += f" {obj_acc}"
        if cond:
            phrase += f" {cond}"
        obj, _cond = _split_object_and_conditions(remainder)
        return _normalize_spaces(phrase), head.casefold(), obj, cond
    np_words = [_decap_lexical(mod) for mod in mods] + [_decap_lexical(_noun_nom_to_acc(head))]
    phrase = _append_remainder("выполняет " + " ".join(np_words), remainder)
    obj, cond = _split_object_and_conditions(remainder)
    return phrase, "творческая работа", obj, cond


def _closed_form_activity_result(text: str) -> tuple[str, str, str, str] | None:
    """Form-noun that TYPE can label → proven finite RESULT, or None."""

    aid = _embedded_aid_result(text)
    if aid:
        return aid
    objects, tail = _participation_object_parts(text)
    if objects:
        body = ", ".join(objects)
        if tail:
            body = f"{body}: {tail}"
        lemma = _participation_lemma(
            _leading_activity_token(text)
        ) or _participation_lemma(objects[0].split()[-1])
        phrase = "участвует в " + body
        obj, cond = _split_object_and_conditions(body)
        return _normalize_spaces(phrase), lemma or "участие", obj, cond
    return _creative_activity_result(text)


_KNOWLEDGE_PP_STARTS = frozenset({"о", "об", "обо", "про"})


def _remainder_is_quoted_label(remainder: str) -> bool:
    return bool(re.search(r"[«»\"„“]", remainder or ""))


def _remainder_is_dependent_object(remainder: str) -> bool:
    """Verbal-noun object/complement: NP without a leading preposition."""

    text = _normalize_spaces(remainder)
    if not text or text.startswith(":") or _remainder_is_quoted_label(text):
        return False
    tokens = text.split()
    if _is_preposition(tokens[0]):
        return False
    _mods, rest = _leading_modifiers(tokens)
    return bool(rest)


def _remainder_is_knowledge_np(remainder: str) -> bool:
    first = remainder.split()[0].casefold() if remainder.split() else ""
    if first in _KNOWLEDGE_PP_STARTS:
        return True
    tokens = remainder.split()
    _mods, rest = _leading_modifiers(tokens)
    if not rest:
        return False
    head = re.sub(r"^[«(\"]+|[»)\",;:]+$", "", rest[0])
    low = head.casefold()
    if _is_theory_knowledge_token(head):
        return True
    if low.endswith(("ений", "аний", "яний")) and len(low) > 5:
        nom_pl = low[:-4] + ("ения" if low.endswith("ений") else "ания" if low.endswith("аний") else "яния")
        return nom_pl in _THEORY_KNOWLEDGE_HEADS or _verbal_noun_lemma(nom_pl).casefold() in _THEORY_KNOWLEDGE_HEADS
    return False


def _practice_activity_np_object(mods: list[str], head: str, remainder: str) -> str:
    acc = _proven_feminine_acc(head)
    noun = _decap_lexical(acc if acc is not None else head)
    words = [_decap_lexical(mod) for mod in mods] + [noun]
    return _append_remainder(" ".join(words), remainder)


def _unconjugated_practice_activity_result(
    text: str,
) -> tuple[str, str, str, str] | None:
    """Practice activity NP without a proven verb → выполняет + source NP."""

    tokens = _normalize_spaces(text).split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return None
    head, remainder = _head_core_and_remainder(rest)
    if not remainder.strip():
        return None
    if _remainder_is_quoted_label(remainder) or _remainder_is_knowledge_np(remainder):
        return None
    if not _remainder_is_dependent_object(remainder):
        return None
    if _conjugate_verbal_noun(head):
        return None
    if _participation_lemma(head) == "занятие":
        return None
    lemma = _verbal_noun_lemma(head).casefold()
    deverbal_ka = bool(re.search(r"(?i)(?:тка|дка|нка|вка|жка|зка)$", lemma))
    # A -ка suffix is only a candidate filter, not activity evidence.
    if not (_has_stem(head, _PERFORM_STEMS) or deverbal_ka):
        return None
    obj_np = _practice_activity_np_object(mods, head, remainder)
    obj, cond = _split_object_and_conditions(remainder)
    return (
        _normalize_spaces("выполняет " + obj_np),
        head.casefold(),
        obj,
        cond,
    )


def _care_and_repair_result(segment: str) -> tuple[str, str, str] | None:
    """«уход за X и ремонт» — два действия, без перечня видов ремонта."""

    match = re.match(
        r"(?i)^уход\s+за\s+(.+?)\s+и\s+ремонт\s*$",
        _normalize_spaces(segment),
    )
    if match is None:
        return None
    obj = match.group(1).strip()
    return (
        _normalize_spaces(f"ухаживает за {obj} и ремонтирует его"),
        "уход и ремонт",
        obj,
    )


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
    obj_acc, cond = _complements_after_finite(remainder)
    phrase = f"{verb1} и {verb2}"
    if obj_acc:
        phrase += f" {obj_acc}"
    if cond:
        phrase += f" {cond}"
    obj, _split_cond = _split_object_and_conditions(remainder)
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

    care = _care_and_repair_result(text)
    if care:
        phrase, action, obj = care
        return phrase, action, obj, ""

    if not theory_only:
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

    if re.match(r"(?i)^(?:игра-)?викторин\w*$", head):
        named_form = _normalize_spaces(" ".join(rest))
        inflected = _inflect_object_phrase(named_form, case="prep")
        if inflected:
            inflected = inflected[:1].lower() + inflected[1:]
            return (
                _normalize_spaces(f"участвует в {inflected}"),
                "участие в викторине",
                named_form,
                "",
            )

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
        if theory_only and (
            head.casefold() in _KNOWLEDGE_NOUNS or _coordinated_theory_activity(text)
        ):
            named = _name_kinds(text)
            if named:
                return named
            return _characterize(text)
        verb = _conjugate_verbal_noun(head)
        if verb:
            remainder = _keep_proven_action_complements(" ".join(rest[1:]).strip())
            if (
                not theory_only
                and head.casefold() == "изучение"
                and not re.search(r"(?i)\bи\s+отработ\w*\b", remainder)
                and re.search(
                    r"(?i)\b(?:комплекс\w*\s+упражнен|упражнен|техник|"
                    r"выполнен|при[её]м|страхов|самострах|лазани|движен)\w*",
                    remainder,
                )
            ):
                # A practical clause with this object proves rehearsal of an
                # observable technique/action, not only passive acquaintance.
                verb = "отрабатывает"
            if mods and mods[0].casefold().endswith("ое"):
                verb = "практически " + verb
            obj_acc, cond = _complements_after_finite(remainder)
            phrase = verb
            if obj_acc:
                phrase += f" {obj_acc}"
            if cond:
                phrase += f" {cond}"
            obj, _split_cond = _split_object_and_conditions(remainder)
            return _normalize_spaces(phrase), head.casefold(), obj, cond

    if not theory_only:
        nominal = _nominal_activity_result(text)
        if nominal:
            return nominal
        formed = _closed_form_activity_result(text)
        if formed:
            return formed
        unconjugated = _unconjugated_practice_activity_result(text)
        if unconjugated:
            return unconjugated

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


def _is_interrogative_clause(text: str) -> bool:
    cleaned = _normalize_spaces(text).strip(" .")
    if not cleaned:
        return False
    if "?" in cleaned:
        return True
    return bool(_INTERROGATIVE_START_RE.match(cleaned))


def _coordinated_theory_activity(text: str) -> bool:
    match = re.match(
        r"(?i)^([А-Яа-яЁё-]+)\s+и\s+([А-Яа-яЁё-]+)\b",
        _normalize_spaces(text),
    )
    if not match:
        return False
    return _looks_like_verbal_noun(match.group(1)) and _looks_like_verbal_noun(match.group(2))


def _heading_without_catalogue(text: str) -> str:
    heading, _sep, _tail = _normalize_spaces(text).partition(":")
    return heading.strip(" .")


def _substantivized_head_without_complement(tokens: list[str]) -> bool:
    """Bare adjectival head used as a truncated object, with no NP/PP complement."""

    if len(tokens) != 1:
        return False
    first = tokens[0]
    core = _strip_punct_word(first)[1].casefold()
    if not core or not _is_adjective(first):
        return False
    return bool(re.search(r"(?i)(?:ые|ие|ый|ой|ий|ая|яя|ое|ее)$", core))


def _is_substantivized_role_object(tokens: list[str]) -> bool:
    """Adjective used as a person/role NP head, not as a modifier of a noun."""

    if len(tokens) < 2 or not _is_adjective(tokens[0]):
        return False
    core = _strip_punct_word(tokens[0])[1].casefold()
    if not re.search(r"(?i)(?:ые|ие|ый|ой|ий|ая|яя|ых|их|ого|его|ую|юю)$", core):
        return False
    return _is_preposition(tokens[1])


def _adj_nom_to_animate_acc(word: str) -> str:
    """Nominative adjectival head → animate accusative (person/role object)."""

    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith("ые") and len(core) > 3:
        changed = core[:-2] + "ых"
    elif low.endswith("ие") and len(core) > 3 and not low.endswith(("ние", "тие")):
        changed = core[:-2] + "их"
    elif low.endswith(("ый", "ой")) and len(core) > 3:
        changed = core[:-2] + "ого"
    elif low.endswith("ий") and len(core) > 3:
        changed = core[:-2] + "его"
    elif low.endswith("ая") and len(core) > 3:
        changed = core[:-2] + "ую"
    elif low.endswith("яя") and len(core) > 3:
        changed = core[:-2] + "юю"
    else:
        return word
    return f"{prefix}{_match_caps(core, changed)}{suffix}"


def _accusative_characterize_role_object(obj: str) -> str:
    tokens = _normalize_spaces(obj).split()
    if not _is_substantivized_role_object(tokens):
        return obj
    return _normalize_spaces(
        " ".join((_adj_nom_to_animate_acc(tokens[0]), *tokens[1:]))
    )


def _heading_with_needed_catalogue(text: str) -> str:
    """Keep the colon-catalogue when the heading alone is a truncated object."""

    raw = _normalize_spaces(text)
    heading, sep, tail = raw.partition(":")
    heading = heading.strip(" .")
    catalogue = tail.strip(" .") if sep else ""
    if catalogue and _substantivized_head_without_complement(heading.split()):
        return _normalize_spaces(f"{heading} {catalogue}")
    return heading


def _characterize_head_ok(word: str) -> bool:
    core = _strip_punct_word(word)[1].casefold()
    if not core:
        return False
    if re.search(r"[ыиуюеь]$", core):
        return True
    lemma = _verbal_noun_lemma(core).casefold()
    # Closed knowledge heads whose nominative already equals accusative (neuter -о).
    # Do not open arbitrary nouns that merely end in -о.
    return (
        core.endswith("о")
        and (core in _THEORY_KNOWLEDGE_HEADS or lemma in _THEORY_KNOWLEDGE_HEADS)
    )


def _is_theory_knowledge_token(word: str) -> bool:
    core = _strip_punct_word(word)[1].casefold()
    if not core:
        return False
    lemma = _verbal_noun_lemma(core).casefold()
    return core in _THEORY_KNOWLEDGE_HEADS or lemma in _THEORY_KNOWLEDGE_HEADS


def _proven_feminine_acc(core: str) -> str | None:
    """Regular feminine accusative only when the nominative type is unambiguous."""

    low = core.casefold()
    lemma = _verbal_noun_lemma(core).casefold()
    if low.endswith("ция"):
        return _noun_nom_to_acc(core)
    if low in _KNOWLEDGE_NOUNS or lemma in _KNOWLEDGE_NOUNS:
        if low.endswith(("а", "я")):
            return _noun_nom_to_acc(core)
        return None
    if low.endswith("ия"):
        return None
    if low.endswith(("ения", "ания", "яния", "ена", "ёна")):
        return None
    if low.endswith(("а", "я")):
        return _noun_nom_to_acc(core)
    return None


def _theory_object_token(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    if not core:
        return word
    if _characterize_head_ok(core):
        changed = _decap_lexical(core)
    else:
        proven = _proven_feminine_acc(core)
        changed = _decap_lexical(proven if proven is not None else core)
    return f"{prefix}{changed}{suffix}"


def _coordinated_knowledge_object_span(tokens: list[str]) -> bool:
    """Allow a non-gated first conjunct when a later coordinated head is gated."""

    if not any(_strip_punct_word(token)[1].casefold() == "и" for token in tokens):
        return False
    return any(
        _is_theory_knowledge_token(token) and _characterize_head_ok(token)
        for token in tokens
    )


def _theory_object_span_ok(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if not _characterize_head_ok(tokens[0]):
        return _coordinated_knowledge_object_span(tokens)
    if _substantivized_head_without_complement(tokens):
        return False
    first_core = _strip_punct_word(tokens[0])[1].casefold()
    if (
        first_core.endswith(("ые", "ие"))
        and len(tokens) > 1
        and _is_adjective(tokens[0])
        and not _is_theory_knowledge_token(tokens[0])
    ):
        raw_next = tokens[1]
        if _is_preposition(raw_next):
            return True
        raw_next_core = _strip_punct_word(raw_next)[1]
        if not _characterize_head_ok(raw_next_core) and not _is_theory_knowledge_token(raw_next):
            return False
    return True


_POSSESSIVE_OR_DEICTIC = frozenset({"его", "ее", "её", "их", "этот", "эта", "это", "эти"})
_POSSESSIVE_ONLY = frozenset({"его", "ее", "её", "их"})
_CLAUSE_NP_STOP = frozenset(
    {
        "как",
        "когда",
        "что",
        "чем",
        "где",
        "куда",
        "зачем",
        "почему",
        "и",
        "или",
        "а",
        "но",
        "же",
        "ли",
        "бы",
        "это",
        "то",
    }
)


def _knowledge_owner_tokens(after_head: list[str]) -> list[str]:
    """Non-head, non-PP tokens that prove an owner NP after a knowledge head."""

    owners: list[str] = []
    late_knowledge_np = False
    for index, token in enumerate(after_head):
        if _is_preposition(token):
            break
        core = _strip_punct_word(token)[1].casefold()
        comma_break = "," in token
        if not core or core in {"и", "или", "а", "но"} | _POSSESSIVE_OR_DEICTIC:
            late_knowledge_np = late_knowledge_np or comma_break
            continue
        if _is_theory_knowledge_token(token):
            if late_knowledge_np:
                break
            late_knowledge_np = late_knowledge_np or comma_break
            continue
        if late_knowledge_np:
            break
        nxt = after_head[index + 1 :]
        if (
            nxt
            and _strip_punct_word(nxt[0])[1].casefold() == "и"
            and len(nxt) > 1
            and _is_theory_knowledge_token(nxt[1])
        ):
            continue
        owners.append(token)
        late_knowledge_np = late_knowledge_np or comma_break
    return owners


def _chunk_looks_like_np(words: list[str]) -> bool:
    for word in words:
        core = _strip_punct_word(word)[1].casefold()
        if len(core) < 3 or core in _CLAUSE_NP_STOP | _POSSESSIVE_OR_DEICTIC:
            continue
        if _is_preposition(word):
            continue
        return True
    return False


def _coordinated_owner_is_ambiguous(group: list[str]) -> bool:
    """«Компас и линейка» is two owners; «имена и фамилии учеников» is one NP."""

    parts: list[list[str]] = []
    buf: list[str] = []
    for token in group:
        if _strip_punct_word(token)[1].casefold() == "и" and buf:
            parts.append(buf)
            buf = []
            continue
        buf.append(token)
    if buf:
        parts.append(buf)
    if len(parts) < 2:
        return False
    return all(len(part) == 1 for part in parts)


def _unique_possessive_antecedent(tokens: list[str], head_index: int) -> list[str] | None:
    """Unique in-clause antecedent of его/ее/их immediately before the head."""

    if not any(
        _strip_punct_word(token)[1].casefold() in _POSSESSIVE_ONLY
        for token in tokens[:head_index]
    ):
        return None
    end = head_index - 1
    while end >= 0 and _strip_punct_word(tokens[end])[1].casefold() in (
        _POSSESSIVE_OR_DEICTIC | {"и", "или"}
    ):
        end -= 1
    if end < 0:
        return None
    left = tokens[: end + 1]
    chunks = [
        chunk.split()
        for chunk in re.split(r",", " ".join(left))
        if chunk.strip(" .,;")
    ]
    groups = [chunk for chunk in chunks if _chunk_looks_like_np(chunk)]
    if len(groups) != 1:
        return None
    if _coordinated_owner_is_ambiguous(groups[0]):
        return None
    return groups[0]


def _possessive_before(tokens: list[str], index: int) -> bool:
    return any(
        _strip_punct_word(token)[1].casefold() in _POSSESSIVE_ONLY
        for token in tokens[:index]
    )


def _is_possessive_coord_member(tokens: list[str], index: int) -> bool:
    if index < 0 or index >= len(tokens):
        return False
    if _is_theory_knowledge_token(tokens[index]):
        return True
    if index > 0 and _strip_punct_word(tokens[index - 1])[1].casefold() in _POSSESSIVE_ONLY:
        core = _strip_punct_word(tokens[index])[1].casefold()
        return bool(core) and core not in _CLAUSE_NP_STOP and not _is_preposition(tokens[index])
    return False


def _expand_possessive_knowledge_span(
    tokens: list[str], index: int
) -> tuple[int, list[str], list[str]] | None:
    """Expand «его Head и Head» into one span; leftover is not an owner."""

    if not _is_theory_knowledge_token(tokens[index]):
        return None
    start = index
    while start >= 2:
        if (
            _strip_punct_word(tokens[start - 1])[1].casefold() == "и"
            and _is_possessive_coord_member(tokens, start - 2)
        ):
            start -= 2
            continue
        break
    end = index
    cursor = index + 1
    while cursor + 1 < len(tokens):
        if (
            _strip_punct_word(tokens[cursor])[1].casefold() == "и"
            and _is_possessive_coord_member(tokens, cursor + 1)
        ):
            end = cursor + 1
            cursor += 2
            continue
        break
    heads: list[str] = []
    for pos in range(start, end + 1):
        core = _strip_punct_word(tokens[pos])[1]
        if core.casefold() in {"и", "или"}:
            heads.append("и")
        else:
            heads.append(_theory_object_token(core))
    return start, heads, list(tokens[end + 1 :])


def _possessive_owner_genitive_tokens(words: list[str]) -> list[str]:
    """Genitive of the in-clause owner via the existing phrase helper."""

    cleaned: list[str] = []
    for word in words:
        core = _strip_punct_word(word)[1]
        if core:
            cleaned.append(_decap_lexical(core))
    if not cleaned:
        return []
    converted = _phrase_to_genitive(" ".join(cleaned)).split()
    if len(cleaned) != 1:
        return converted
    current = converted[0] if converted else cleaned[0]
    if current.casefold() != cleaned[0].casefold():
        return converted
    if _is_adjective(cleaned[0]):
        return [_adj_to_genitive(cleaned[0])]
    low = cleaned[0].casefold()
    if low.endswith(("а", "я")) and not low.endswith("ия"):
        return [_head_noun_to_genitive(_noun_nom_to_acc(cleaned[0]))]
    return converted


def _knowledge_head_span(tokens: list[str], index: int) -> list[str] | None:
    """Knowledge-head NP with a proven same-clause owner, or None."""

    expanded = _expand_possessive_knowledge_span(tokens, index)
    if expanded is None:
        return None
    start, heads, leftover = expanded
    if _possessive_before(tokens, start):
        antecedent = _unique_possessive_antecedent(tokens, start)
        if not antecedent:
            return None
        genitive = _possessive_owner_genitive_tokens(antecedent)
        if not genitive:
            return None
        rest = [*heads, *genitive]
        if leftover and _is_preposition(leftover[0]):
            rest = [*rest, *leftover]
        return rest if _theory_object_span_ok(rest) else None
    if _knowledge_owner_tokens(leftover):
        rest = [*heads, *leftover]
        return rest if _theory_object_span_ok(rest) else None
    return None


def _knowledge_object_missing_owner(obj_text: str) -> bool:
    obj, _cond = _split_object_and_conditions(obj_text)
    tokens = obj.split()
    if not tokens:
        return False
    if not _is_theory_knowledge_token(tokens[0]) and not any(
        _is_theory_knowledge_token(token) for token in tokens
    ):
        return False
    return not _knowledge_owner_tokens(tokens[1:])


def _drop_tautological_characterize_head(obj: str) -> str | None:
    """Keep a proven NP tail after a derivationally tautological first noun."""

    tokens = _normalize_spaces(obj).split()
    if not tokens:
        return None
    first = _strip_punct_word(tokens[0])[1]
    if not _predicate_repeats_object("характеризует", first):
        return _normalize_spaces(obj)
    rest = list(tokens[1:])
    if rest and rest[0].casefold() in {"и", "или"}:
        rest = rest[1:]
    if not rest:
        return None
    rest[0] = _theory_object_token(rest[0])
    if not _theory_object_span_ok(rest):
        return None
    if _is_theory_knowledge_token(rest[0]) and not _knowledge_owner_tokens(rest[1:]):
        return None
    return _normalize_spaces(" ".join(rest))


def _salvage_tautological_characterize_result(result: str) -> str | None:
    match = re.match(r"(?i)^характеризует\s+(.+)$", _normalize_spaces(result).rstrip("."))
    if match is None:
        return None
    dropped = _drop_tautological_characterize_head(match.group(1))
    if not dropped or dropped.casefold() == match.group(1).casefold():
        return None
    dropped = _accusative_characterize_role_object(dropped)
    obj, cond = _split_object_and_conditions(dropped)
    if not obj or _knowledge_object_missing_owner(obj):
        return None
    phrase = _normalize_spaces("характеризует " + " ".join(part for part in (obj, cond) if part))
    return _cap_sentence(phrase)


def _proven_theory_object(heading: str) -> str | None:
    """Object NP whose first word already satisfies the characterize case gate."""

    text = _heading_with_needed_catalogue(heading)
    if not text or _is_interrogative_clause(text):
        return None
    tokens = text.split()
    if not tokens:
        return None
    for index, token in enumerate(tokens):
        core = _strip_punct_word(token)[1].casefold()
        if core in _POSSESSIVE_OR_DEICTIC:
            continue
        nxt = (
            _strip_punct_word(tokens[index + 1])[1].casefold()
            if index + 1 < len(tokens)
            else ""
        )
        if core in {"сведения", "сведение"} and nxt in {"о", "об", "обо"}:
            if index + 2 >= len(tokens):
                continue
            after = list(tokens[index + 2 :])
            after[0] = _theory_object_token(_prep_noun_to_nom(after[0]))
            if _theory_object_span_ok(after):
                return _normalize_spaces(" ".join(after))
            continue
        if _is_theory_knowledge_token(token):
            rest = _knowledge_head_span(tokens, index)
            if rest:
                return _normalize_spaces(" ".join(rest))
            continue
    if (
        any(
            _strip_punct_word(token)[1].casefold() in _POSSESSIVE_ONLY
            for token in tokens
        )
        and any(_is_theory_knowledge_token(token) for token in tokens)
        and _strip_punct_word(tokens[0])[1].casefold() not in _POSSESSIVE_ONLY
    ):
        return None
    if _is_theory_knowledge_token(tokens[0]):
        return None
    first_core = _strip_punct_word(tokens[0])[1]
    second_core = _strip_punct_word(tokens[1])[1] if len(tokens) > 1 else ""
    if first_core.casefold().endswith(("ая", "яя")) and second_core:
        noun_acc = _proven_feminine_acc(second_core)
        if noun_acc is not None:
            led = [
                _decap_lexical(
                    _adj_to_acc(first_core, plural=False, gender="f")
                ),
                _decap_lexical(noun_acc),
                *tokens[2:],
            ]
            if _theory_object_span_ok(led):
                return _normalize_spaces(" ".join(led))
    led = [_theory_object_token(tokens[0]), *tokens[1:]]
    if not _theory_object_span_ok(led):
        return None
    return _normalize_spaces(" ".join(led))


def _characterize(text: str) -> tuple[str, str, str, str]:
    obj = _proven_theory_object(text)
    if not obj:
        return "", "", "", ""
    salvaged = _drop_tautological_characterize_head(obj)
    if not salvaged:
        return "", "", "", ""
    salvaged = _accusative_characterize_role_object(salvaged)
    obj, cond = _split_object_and_conditions(salvaged)
    if not obj or _knowledge_object_missing_owner(obj):
        return "", "", "", ""
    phrase = _normalize_spaces("характеризует " + " ".join(part for part in (obj, cond) if part))
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
    text = (inner or "").strip()
    if re.fullmatch(r"(?i)творческая работа", text):
        return False
    if _ACTIVITY_START_RE.search(text):
        return True
    return any(_looks_like_verbal_noun(token) for token in re.findall(r"[А-Яа-яЁё]+", inner))


_CAPACITY_PREPOSITIONS = _PREPOSITIONS


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
_RESULT_FINITE_RE = re.compile(
    r"(?i)^[А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют|ает|яет)\b"
)
_FINITE_VERB_RE = _RESULT_FINITE_RE
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
    return bool(_RESULT_FINITE_RE.match(phrase.strip()))


def _merge_repeated_verbs(text: str, *, only: frozenset[str] | None = None) -> str:
    parts = re.split(r",\s+", text)
    if len(parts) < 2:
        return text
    verb_re = re.compile(r"(?i)^([А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют))\s+")
    merged: list[str] = []
    prev_verb = ""
    for part in parts:
        found = verb_re.match(part)
        verb = found.group(1).casefold() if found else ""
        if (
            found
            and prev_verb
            and verb == prev_verb
            and (only is None or verb in only)
        ):
            rest = part[found.end() :]
            if merged:
                merged[-1] = f"{merged[-1].rstrip(',')} и {rest}"
            continue
        prev_verb = verb
        merged.append(part)
    return ", ".join(merged)


def _trim_long_parentheticals(text: str) -> str:
    def drop(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.count(",") >= 2 or inner.count(";") >= 1:
            return ""
        return match.group(0)

    return _normalize_spaces(re.sub(r"\s*\(([^()]*)\)", drop, text))


_OBSERVABLE_OP_HEAD = re.compile(
    r"(?i)^(определению|определение|измерению|измерение|отбору|отбор|"
    r"отысканию|отыскание|оценке|глазомерную|инструментальное)\b"
)


def _is_observable_operation_part(part: str) -> bool:
    return bool(_OBSERVABLE_OP_HEAD.match((part or "").strip()))


def _is_parallel_observable_series(parts: list[str]) -> bool:
    """Равноправные операции одной клаузы: «по определению X, измерению Y»."""

    if len(parts) < 2:
        return False
    if not re.search(
        r"(?i)\b(?:по|на)\s+(?:определен|измерен|отбор|отыскан|оценк|глазомерн|инструментальн)",
        parts[0],
    ):
        return False
    return all(_is_observable_operation_part(part) for part in parts[1:])


def _drop_raw_list_tails(text: str) -> str:
    parts = re.split(r",\s+", text)
    if len(parts) <= 1:
        return text
    if _is_parallel_observable_series(parts):
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
        if first[:1].isupper() and not _RESULT_FINITE_RE.match(part):
            if not re.match(r"(?i)^(?:совершает|посещает)\s+", parts[0]):
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
    siblings = [
        item
        for item in pool
        if item != best and _is_finite_result_phrase(item)
    ]
    kept = {best, *siblings}
    ordered = [item for item in phrases if item in kept]
    if score(best)[0] >= 3 and sum(1 for item in pool if score(item)[0] >= 3) == 1:
        return ordered or [best]
    if len(phrases) < 3:
        return phrases
    return ordered or [best]


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


def _transform_clause_candidate(
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
        if theory_only and _is_interrogative_clause(unit):
            continue
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
    frame_clause = source_clause
    if theory_only:
        headings = [
            _heading_without_catalogue(unit)
            for unit in (_clause_units(source_clause) or [source_clause])
            if unit and not _is_interrogative_clause(unit)
        ]
        headings = [item for item in headings if item]
        if headings:
            frame_clause = ". ".join(headings)
    frame = ActionFrame(
        clause=frame_clause,
        action=", ".join(actions),
        object=", ".join(objects),
        conditions=", ".join(conditions),
    )
    return result, frame


def _safe_topic_fields(topic_title: str, *, practical: bool) -> tuple[str, str]:
    # Quote source text rather than guessing its case, number or verb valency.
    title = _normalize_spaces(topic_title).strip(" .")
    if not title:
        return (
            "Выполняет практическое задание." if practical else "Характеризует материал занятия.",
            "педагогическое наблюдение за выполнением задания" if practical else "устный опрос",
        )
    topic = f"по теме „{title}“"
    return (
        f"Выполняет практическое задание {topic}." if practical else f"Характеризует материал {topic}.",
        f"педагогическое наблюдение за выполнением задания {topic}" if practical else f"устный опрос {topic}",
    )


def transform_clause_to_result(
    clause: str, *, theory_only: bool, full_source: str, topic_title: str = "",
) -> tuple[str, ActionFrame]:
    try:
        return _transform_clause_candidate(
            clause, theory_only=theory_only, full_source=full_source, topic_title=topic_title,
        )
    except _UncertainGrammar:
        result, _ = _safe_topic_fields(topic_title or clause, practical=not theory_only)
        return result, ActionFrame(clause, "", "", "")


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
    head = _leading_activity_token(clause)
    if _is_leading_form_activity(head) or _has_stem(head, _PERFORM_STEMS):
        return 3
    if _has_stem(lead, _PERFORM_STEMS):
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


_AUX_UNIT_HEAD_RE = re.compile(
    r"(?i)^(упражнен|тренировочн|построен|заняти|изучен|знакомств|"
    r"диктант|викторин|игр|соревнова)"
)
_LOGISTICS_STEMS = ("закупк", "фасовк", "упаковк", "сдач")
_METHOD_CATALOG_STEMS = (
    "измерен",
    "оценк",
    "глазомер",
    "азимут",
    "засечк",
    "ориентир",
    "курвиметр",
    "масштаб",
    "легенд",
    "абрис",
)
_COMPLEMENT_STEMS = (
    "уход",
    "ремонт",
    "костр",
    "привал",
    "ночлег",
    "снаряжен",
    "одежд",
    "обув",
)


def _is_auxiliary_practice_unit(clause: str) -> bool:
    """Упражнение, форма, изучение или логистика — не обязательное действие темы."""

    if _practice_unit_kind(clause) in {"exercise", "game", "element"}:
        return True
    first = clause.split()[0] if clause.split() else ""
    if _AUX_UNIT_HEAD_RE.match(first):
        return True
    low = clause.casefold()
    return any(stem in low for stem in _LOGISTICS_STEMS)


def _is_method_catalog_neighbor(selected: str, neighbor: str) -> bool:
    selected_hits = {stem for stem in _METHOD_CATALOG_STEMS if stem in selected.casefold()}
    neighbor_hits = {stem for stem in _METHOD_CATALOG_STEMS if stem in neighbor.casefold()}
    return bool(selected_hits and neighbor_hits)


def _focus_complement_parts(neighbor: str) -> list[str]:
    """Для ухода/ремонта брать действие, не общую «работу со снаряжением»."""

    low = neighbor.casefold()
    if not re.match(r"(?i)^работа\s+со?\s", neighbor.strip()):
        return [neighbor]
    focused = [
        part.strip()
        for part in re.split(r",\s+", neighbor)
        if "уход" in part.casefold() or "ремонт" in part.casefold()
    ]
    return focused or [neighbor]


def _is_obligatory_neighbor(selected: str, neighbor: str) -> bool:
    """Отдельное обязательное действие темы, не упражнение и не каталог способов."""

    if not neighbor or neighbor == selected:
        return False
    if _is_kinds_clause(neighbor):
        return False
    if _is_auxiliary_practice_unit(neighbor) or _is_method_catalog_neighbor(
        selected, neighbor
    ):
        return False
    low = neighbor.casefold()
    selected_low = selected.casefold()
    if re.match(r"(?i)^(определение|выбор)\s+мест", neighbor.strip()):
        return any(stem in selected_low for stem in ("лагер", "бивак", "привал", "ночлег"))
    if not any(stem in low for stem in _COMPLEMENT_STEMS):
        return False
    if "снаряжен" in low:
        if any(stem in selected_low for stem in ("уклад", "рюкзак", "подгонк", "снаряжен")):
            return any(stem in low for stem in ("уход", "ремонт"))
        return "план" in selected_low or "составлен" in selected_low
    return True


def _enrich_with_neighbors(
    selected: str, units: list[str]
) -> tuple[str, list[str]]:
    """Добавить соседние клаузы той же темы: география, поле, второй объект практики."""

    if not selected or selected not in units:
        return selected, []
    index = units.index(selected)
    extras: list[tuple[int, str]] = []
    complementary: list[str] = []
    used = {selected}

    def add(nidx: int, neighbor: str, *, practice: bool = False) -> None:
        if neighbor in used or _is_non_student_process(neighbor):
            return
        extras.append((nidx, neighbor))
        used.add(neighbor)
        if practice:
            complementary.append(neighbor)

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
    elif "горизонт" in selected_low:
        wanted = ("потер", "местонахожд")
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
                focused = [
                    part.strip()
                    for part in re.split(r",\s+", neighbor)
                    if any(stem in part.casefold() for stem in wanted)
                ]
                for part in focused or [neighbor]:
                    add(nidx, part, practice=True)
                break
    for nidx, neighbor in enumerate(units):
        if neighbor in used:
            continue
        if not _is_obligatory_neighbor(selected, neighbor):
            continue
        for part in _focus_complement_parts(neighbor):
            add(nidx, part, practice=True)
    extra_texts = complementary
    if not extras:
        return selected, []
    parts = [(index, selected), *extras]
    parts.sort()
    return ". ".join(item for _, item in parts), extra_texts


def _clause_yields_proven_finite(clause: str, *, theory_only: bool) -> bool:
    """True when the clause already converts to a proven finite RESULT."""

    phrase, _frame = transform_clause_to_result(
        clause,
        theory_only=theory_only,
        full_source=clause,
        topic_title="",
    )
    phrase = _observable_result(phrase) if phrase else ""
    if not phrase or not _is_finite_result_phrase(phrase):
        return False
    if not theory_only and phrase.casefold().startswith(("характеризует", "называет")):
        return False
    return True


def select_source_clause(
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int = 0,
) -> tuple[str, bool, list[str]]:
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
        title = _normalize_spaces(topic_title)
        return title, theory_only, [title] if title else []
    units = _clause_units(source)
    extra_units = _clause_units(program_content or "") if program_content else []
    if not units and not extra_units:
        title = _normalize_spaces(topic_title)
        return title, theory_only, [title] if title else []

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
        pool = candidates
        if as_theory:
            declarative = [unit for unit in candidates if not _is_interrogative_clause(unit)]
            if declarative:
                pool = declarative
            grounded = [unit for unit in pool if _proven_theory_object(unit)]
            if grounded:
                pool = grounded
        classed = [
            (
                idx,
                unit,
                _action_class(unit, theory_only=as_theory),
                topic_score(unit),
            )
            for idx, unit in enumerate(pool)
        ]
        best_class = max(item[2] for item in classed)
        top = [item for item in classed if item[2] == best_class]
        if top and max(item[3] for item in top) == 0:
            aligned = [
                item
                for item in classed
                if item[2] >= 2
                and item[3] > 0
                and _clause_yields_proven_finite(item[1], theory_only=as_theory)
            ]
            if aligned:
                top = aligned
        top.sort(key=lambda item: rank_key(item[1], as_theory, item[0]))
        best = top[0][1]
        return best, topic_score(best)

    primary = pick(units, theory_only)
    aligned = pick(extra_units, True)
    chosen = primary[0] if primary else (aligned[0] if aligned else topic_title)
    pool = units or extra_units
    # Whole-program topic overlap must never replace the actual row source.
    if primary:
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
    return chosen, theory_only, pool


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
    "находит": "отыскания",
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
    "ухаживает": "ухода",
    "ремонтирует": "ремонта",
    "разжигает": "разжигания",
    "подбирает": "подбора",
}
_EXERCISE_SKILL_VERBS = {"определяет", "оценивает", "измеряет", "отбирает", "находит"}
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


_KNOWLEDGE_RESULT_VERBS = frozenset({"характеризует", "называет"})
_PROVEN_FINITE_VERBS = frozenset(_VERBAL_NOUN_TO_VERB.values()) | _KNOWLEDGE_RESULT_VERBS | frozenset(
    _FINITE_TO_NOUN
) | {
    "выбирает",
    "осваивает",
    "участвует",
    "совершает",
    "распознаёт",
    "исследует",
    "работает",
    "ориентируется",
}
_ACTION_FINITE_VERBS = _PROVEN_FINITE_VERBS - _KNOWLEDGE_RESULT_VERBS
_VERB_TO_VERBAL_NOUN = {
    verb: noun for noun, verb in _VERBAL_NOUN_TO_VERB.items()
}
_CONTROL_RESULT_VERB_RE = re.compile(
    r"(?i)\b("
    + "|".join(sorted(_PROVEN_FINITE_VERBS, key=len, reverse=True))
    + r")\b"
)


def _result_control_segments(result: str) -> list[tuple[str, str]]:
    """Split a finished RESULT into proven (verb, object) pairs."""

    text = _normalize_spaces(result).rstrip(".")
    matches = list(_CONTROL_RESULT_VERB_RE.finditer(text))
    if not matches:
        return []
    segments: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        obj = text[match.end() : end].strip(" ,.;")
        obj = re.sub(r"^(и|а|но)\s+", "", obj, flags=re.IGNORECASE)
        obj = re.sub(r"\s+(и|а|но)$", "", obj, flags=re.IGNORECASE)
        if obj.casefold() in {"и", "а", "но"}:
            obj = ""
        segments.append((match.group(1).casefold(), obj))
    return segments


def _finite_token(word: str) -> str:
    return _strip_punct_word(word)[1].casefold()


def _is_proven_finite_token(word: str) -> bool:
    return _finite_token(word) in _PROVEN_FINITE_VERBS


def _is_action_finite_token(word: str) -> bool:
    return _finite_token(word) in _ACTION_FINITE_VERBS


def _starts_with_action_finite(text: str) -> bool:
    first = _normalize_spaces(text).split()[:1]
    return bool(first) and _is_action_finite_token(first[0])


def _verbal_noun_to_instrumental(noun: str) -> str:
    prefix, core, suffix = _strip_punct_word(noun)
    low = core.casefold()
    if low.endswith(("ение", "ание", "яние", "тие")):
        changed = core + "м"
    elif low.endswith("ка") and len(core) > 3:
        changed = core[:-1] + "ой"
    elif low.endswith("а") and len(core) > 3:
        changed = core[:-1] + "ой"
    elif not re.search(r"(?i)[аеёиоуыэюя]$", low):
        changed = core + "ом"
    else:
        changed = core
    return f"{prefix}{_match_caps(core, changed)}{suffix}"


def _observation_for_action_verb(verb: str, obj: str) -> str:
    """Process CONTROL for a proven action finite. Never oral + dative of the verb."""

    noun = _VERB_TO_VERBAL_NOUN.get(verb)
    if not noun:
        return ""
    focus = _coordinated_phrase_to_genitive(obj) if obj else ""
    return _normalize_spaces(
        f"педагогическое наблюдение за {_verbal_noun_to_instrumental(noun)} {focus}"
    )


def _observation_from_action_segments(segments: list[tuple[str, str]]) -> str:
    parts = [
        _observation_for_action_verb(verb, obj)
        for verb, obj in segments
        if verb in _ACTION_FINITE_VERBS
    ]
    parts = [item for item in parts if item]
    if not parts:
        return ""
    prefix = "педагогическое наблюдение за "
    if all(item.startswith(prefix) for item in parts):
        tails = [item[len(prefix) :] for item in parts]
        return prefix + _join_and(tails)
    return "; ".join(parts)


def _oral_object_for_control(obj: str) -> str:
    """Dative of a proven RESULT object. Do not dative a leftover finite verb."""

    phrase = _normalize_spaces(obj).strip(" ,.;")
    if not phrase or _starts_with_action_finite(phrase):
        return ""
    topic = re.match(r"(?i)^материал по теме\s+[„\"«](.+?)[“\"»]$", phrase)
    if topic:
        return f"теме „{topic.group(1)}“"
    first = phrase.split()[0]
    if _is_proven_finite_token(first):
        phrase = _normalize_spaces(phrase[len(first) :]).strip(" ,.;")
        if not phrase or _starts_with_action_finite(phrase):
            return ""
    return _phrase_to_dative(phrase)


def _oral_from_knowledge_objects(objects: list[str]) -> str:
    converted = [
        _oral_object_for_control(item)
        for item in objects
        if _normalize_spaces(item).strip(" ,.;")
    ]
    converted = [item for item in converted if item]
    if not converted:
        return ""
    if len(converted) == 1:
        return "устный опрос по " + converted[0]
    return "устный опрос по " + _join_and(converted)


def _rebuild_skill_result(segments: list[tuple[str, str]]) -> str:
    parts = [
        _normalize_spaces(f"{verb} {obj}").strip(" ,")
        for verb, obj in segments
        if verb not in _KNOWLEDGE_RESULT_VERBS
    ]
    if not parts:
        return ""
    return _cap_sentence(", ".join(parts).rstrip("."))


def _control_from_proven_result(result: str, *, lesson_type: str = "") -> str:
    """CONTROL from already-proven RESULT actions only; never from title/source."""

    segments = _result_control_segments(result)
    knowledge = [
        obj
        for verb, obj in segments
        if verb in _KNOWLEDGE_RESULT_VERBS and obj
    ]
    oral = _oral_from_knowledge_objects(knowledge)
    skill_result = _rebuild_skill_result(segments)
    observed = ""
    if skill_result:
        observed = (
            _skill_control(skill_result)
            or _slot_control_from_result(skill_result)
            or _process_control(skill_result, lesson_type)
            or _observation_from_action_segments(segments)
        )
    if oral and observed:
        return f"{oral}; {observed}"
    return oral or observed


def _oral_object_grounded_in_result(control: str, result: str) -> bool:
    """Accept oral CONTROL whose object is already in RESULT, not a head whitelist."""

    oral = control.split(";")[0].strip()
    if not oral.startswith("устный опрос по ") or oral.startswith("устный опрос по теме"):
        return False
    complement = oral[len("устный опрос по ") :]
    core = re.sub(
        r"(?i)^(характеризует|называет)\s+",
        "",
        _normalize_spaces(result).rstrip("."),
    ).strip()
    if core:
        expected = _oral_object_for_control(core)
        if expected and expected.casefold() == complement.casefold():
            return True
    result_tokens = re.findall(r"[а-яё]{3,}", result.casefold())
    if not result_tokens:
        return False
    result_stems = {token[:4] if len(token) >= 4 else token for token in result_tokens}

    def _stem(token: str) -> str:
        return token[:4] if len(token) >= 4 else token

    extra = [
        token
        for token in re.findall(r"[а-яё]{3,}", complement.casefold())
        if not any(
            _stem(token)[:3] == stem[:3]
            or _stem(token).startswith(stem[:3])
            or stem.startswith(_stem(token)[:3])
            for stem in result_stems
        )
    ]
    return not extra


def _oral_quiz_control(frame: ActionFrame, planned_result: str) -> str:
    """Oral CONTROL from the accepted RESULT only; never a shortened source clause."""

    proven = _control_from_proven_result(planned_result)
    if proven:
        return proven
    if _starts_with_action_finite(planned_result):
        return ""
    blob = _normalize_spaces(planned_result or "").casefold()
    kinds = re.search(r"виды\s+([а-яё]+)", blob)
    if kinds:
        return f"устный опрос по видам {kinds.group(1)}"
    core = re.sub(
        r"(?i)^(характеризует|называет)\s+",
        "",
        _normalize_spaces(planned_result or "").rstrip("."),
    ).strip()
    if core and not _starts_with_action_finite(core):
        complement = _oral_object_for_control(core)
        if complement:
            return "устный опрос по " + complement
    return "устный опрос"


def _align_control_to_result(control: str, result: str) -> str:
    """Контроль проверяет те же объекты, что уже попали в результат."""

    control_text = _normalize_spaces(control)
    result_text = _normalize_spaces(result)
    if not control_text or not result_text:
        return control_text
    result_low = result_text.casefold()
    control_low = control_text.casefold()
    if control_low.startswith("устный опрос"):
        rebuilt = _control_from_proven_result(result_text)
        if rebuilt:
            control_text = rebuilt
            control_low = rebuilt.casefold()
        elif _starts_with_action_finite(result_text):
            observed = _observation_from_action_segments(
                _result_control_segments(result_text)
            )
            if observed:
                return observed
        else:
            core = re.sub(
                r"(?i)^(характеризует|называет)\s+",
                "",
                result_text.rstrip("."),
            ).strip()
            if core and not _starts_with_action_finite(core):
                complement = _oral_object_for_control(core)
                if complement:
                    return "устный опрос по " + complement
    if "самострахов" in result_low and "самострахов" not in control_low:
        if "препятств" in control_low:
            return control_text.rstrip(".") + " и самостраховкой"
    if re.search(r"(?i)потер[еия].{0,24}ориентир|восстановлен\w*\s+местонахожд", result_low):
        if "восстановлен" not in control_low and "потер" not in control_low:
            if control_low.startswith("практическое задание по "):
                return control_text.rstrip(".") + " и восстановлению ориентировки"
    return control_text


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
    elif low.endswith("ие") and len(core) > 3:
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
    elif low.endswith("ции"):
        changed = core[:-1] + "й"
    elif low.endswith("ию") and len(core) > 3:
        # Inverse of _noun_nom_to_acc «-ия» → «-ию»: accusative → genitive «-ии».
        changed = core[:-2] + "ии"
    elif low.endswith("ю") and len(core) > 3:
        # Inverse of _noun_nom_to_acc «-я» → «-ю»: accusative → genitive «-и».
        changed = core[:-1] + "и"
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
    leading_adjectives = 0
    for word in head:
        if _is_adjective(word) or word.casefold().endswith(("ую", "юю", "ая")):
            leading_adjectives += 1
            continue
        break
    if (
        leading_adjectives
        and leading_adjectives < len(head)
        and head[leading_adjectives].casefold() not in {"и", "или"}
    ):
        # One or more adjectives + direct noun + untouched dependent tail.
        head = [
            *[
                _adj_to_genitive(word)
                for word in head[:leading_adjectives]
            ],
            _head_noun_to_genitive(head[leading_adjectives]),
            *head[leading_adjectives + 1 :],
        ]
    elif (
        len(head) >= 3
        and _is_adjective(head[0])
        and any(word.casefold() == "и" for word in head[:-1])
    ):
        noun = _head_noun_to_genitive(head[-1])
        mids = []
        for word in head[:-1]:
            if word.casefold() == "и":
                mids.append(word.casefold())
            elif _is_adjective(word):
                mids.append(_adj_to_genitive(word))
            else:
                mids.append(word)
        head = [*mids, noun]
    else:
        head = [_head_noun_to_genitive(head[0]), *head[1:]]
    return _normalize_spaces(" ".join((*head, *tail)))


def _coordinated_phrase_to_genitive(phrase: str) -> str:
    parts = [
        part.strip()
        for part in re.split(r"\s+и\s+", _normalize_spaces(phrase))
        if part.strip()
    ]
    if len(parts) < 2:
        return _phrase_to_genitive(phrase)
    return " и ".join(_phrase_to_genitive(part) for part in parts)


def _phrase_to_dative_noun(noun: str) -> str:
    low = noun.casefold()
    if low.endswith("ия"):
        return noun[:-2] + "ию"
    if low.endswith("ие"):
        return noun[:-1] + "ю"
    if low.endswith("ки"):
        return noun[:-1] + "е"
    if low.endswith("ора"):
        return noun[:-1] + "у"
    if low.endswith("а"):
        return noun[:-1] + "е"
    return noun


def _looks_like_direct_case_start(word: str) -> bool:
    """True when a chunk after a comma still looks like a nominative/accusative NP."""

    _, core, _ = _strip_punct_word(word)
    low = core.casefold()
    if not low or _is_preposition(low) or low in {"и", "а", "но", "да"}:
        return False
    if _is_adjective(word):
        return bool(
            re.search(r"(?i)(?:ое|ее|ая|яя|ые|ие|ый|ой|ий|ую|юю)$", low)
        )
    if low.endswith("ию") and len(low) > 3:
        return True
    if re.search(
        r"(?i)(?:ах|ях|ами|ями|ам|ям|ов|ев|ёй|ей|ою|ею|ом|ем|ии)$",
        low,
    ):
        return False
    return True


def _split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
            buf.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            buf.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
    if buf:
        parts.append("".join(buf).strip())
    return [item for item in parts if item]


def _split_direct_case_commas(text: str) -> list[str]:
    raw = _split_top_level_commas(text)
    if len(raw) < 2:
        return raw
    merged = [raw[0]]
    for part in raw[1:]:
        first = part.split()[0] if part.split() else ""
        if _looks_like_direct_case_start(first):
            merged.append(part)
        else:
            merged[-1] = f"{merged[-1]}, {part}"
    return merged


def _has_noun_token(words: list[str]) -> bool:
    return any(
        word.casefold() not in {"и", "а", "но", "да"}
        and not _is_preposition(word)
        and not _is_adjective(word)
        for word in words
        if word
    )


def _split_first_coord_and(text: str) -> tuple[str, str] | None:
    """Split the first NP-level «и» whose left side already contains a noun."""

    lower = text.casefold()
    start = 0
    while True:
        index = lower.find(" и ", start)
        if index < 0:
            return None
        depth = 0
        for char in text[:index]:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
        if depth:
            start = index + 3
            continue
        left = text[:index].strip()
        right = text[index + 3 :].strip()
        if not left or not right:
            return None
        if _has_noun_token(left.split()) and not _is_preposition(right.split()[0]):
            last_left = left.split()[-1]
            first_right = right.split()[0]
            if _is_adjective(last_left) and _is_adjective(first_right):
                start = index + 3
                continue
            return left, right
        start = index + 3


def _detach_trailing_parens(text: str) -> tuple[str, str]:
    match = re.match(r"^(.*?)(\s*\([^()]+\))\s*$", text)
    if match:
        return match.group(1).strip(), match.group(2)
    return text, ""


def _regular_feminine_a_noun(low: str) -> bool:
    """Suffixal feminine -а, not the unmarked neuter/inanimate plural -а."""

    return bool(re.search(r"(?i)(?:[кгхжшщч]а|ота|ета|ина|ица)$", low))


def _adj_to_dative(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith("ую"):
        changed = core[:-2] + "ой"
    elif low.endswith("юю"):
        changed = core[:-2] + "ей"
    elif low.endswith("ые"):
        changed = core[:-2] + "ым"
    elif low.endswith("ых"):
        changed = core[:-2] + "ым"
    elif low.endswith("ие") and not low.endswith(("ние", "тие", "ание", "яние")):
        changed = core[:-2] + "им"
    elif low.endswith("их"):
        changed = core[:-2] + "им"
    elif low.endswith("ого") and len(core) > 4:
        changed = core[:-3] + "ому"
    elif low.endswith("его") and len(core) > 4:
        changed = core[:-3] + "ему"
    elif low.endswith("ая"):
        changed = core[:-2] + "ой"
    elif low.endswith("ое"):
        changed = core[:-2] + "ому"
    elif low.endswith("ее") and len(core) > 3:
        changed = core[:-2] + "ему"
    elif low.endswith("ий") and len(core) > 3:
        if low.endswith(("ский", "цкий", "ной", "ный")) or core[-3].casefold() in "кгхжшщч":
            changed = core[:-2] + "ому"
        else:
            changed = core[:-2] + "ему"
    elif low.endswith("ый") and len(core) > 3:
        changed = core[:-2] + "ому"
    else:
        changed = core
    return f"{prefix}{_match_caps(core, changed)}{suffix}"


def _noun_to_dative(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low in _PROVEN_FINITE_VERBS or low in _POSSESSIVE_ONLY:
        return f"{prefix}{core}{suffix}"
    if "-" in core and not _is_adjective(core):
        stems = [stem for stem in core.split("-") if stem]
        if len(stems) >= 2:
            inflected = "-".join(
                _adj_to_dative(stem) if _is_adjective(stem) else _noun_to_dative(stem)
                for stem in stems
            )
            return f"{prefix}{inflected}{suffix}"
    if low in {"меню", "кофе"}:
        changed = core
    elif re.search(r"(?i)(?:ам|ям|ами|ями|ах|ях|ов|ев|ём)$", low):
        changed = core
    elif low.endswith(("ений", "аний", "яний", "тий")):
        changed = core
    elif low.endswith(("ение", "ание", "яние", "тие")):
        changed = core[:-1] + "ю"
    elif low.endswith(("ения", "ания", "яния")):
        changed = core[:-1] + "ям"
    elif low.endswith("ию") and len(core) > 3:
        changed = core[:-2] + "ии"
    elif low.endswith("ю") and len(core) > 3:
        changed = core[:-2] + "ии" if low[-2] == "и" else core[:-1] + "е"
    elif low.endswith("ия") and len(core) > 3:
        changed = core[:-2] + "ии"
    elif low.endswith("у") and len(core) > 3:
        changed = core[:-1] + "е"
    elif low.endswith("о") and len(core) > 2:
        changed = core[:-1] + "у"
    elif low.endswith("ы") and len(core) > 3:
        changed = core[:-1] + "ам"
    elif low.endswith("ии"):
        changed = core
    elif low.endswith("и") and len(core) > 3:
        stem = core[:-1]
        changed = stem + (
            "ам" if stem[-1:].casefold() in "гкхжчшщц" else "ям"
        )
    elif low.endswith("а") and len(core) > 3:
        if _regular_feminine_a_noun(low):
            changed = core[:-1] + "е"
        else:
            changed = core[:-1] + "ам"
    elif low.endswith("я") and len(core) > 3:
        changed = core[:-1] + "е"
    elif low.endswith("ь") and len(core) > 2:
        if low.endswith(("арь", "ырь", "тель")):
            changed = core[:-1] + "ю"
        else:
            changed = core[:-1] + "и"
    elif not re.search(r"(?i)[аеёиоуыэюя]$", low):
        changed = core + "у"
    else:
        changed = core
    return f"{prefix}{_match_caps(core, changed)}{suffix}"


def _is_postposed_dative_adjective(word: str) -> bool:
    """Agreeing modifier after the head, not a genitive noun like «занятий»."""

    if _looks_like_verbal_noun(word) or not _is_adjective(word):
        return False
    core = _strip_punct_word(word)[1].casefold()
    if core.endswith(("ние", "тие", "ание", "яние")):
        return False
    return bool(re.search(r"(?i)(?:ое|ее|ая|яя|ые|ие|ую|юю)$", core))


def _dative_np(phrase: str) -> str:
    words = _normalize_spaces(phrase).split()
    if not words:
        return phrase
    head, tail = _split_prep_tail(words)
    if not head:
        return _normalize_spaces(phrase)
    converted: list[str] = []
    seen_noun = False
    leftover: list[str] = []
    for index, word in enumerate(head):
        low = word.casefold()
        if low in {"и", "а", "но", "да"}:
            converted.append("и" if low == "и" else word)
            continue
        # его/её/их as determiners are indeclinable; the following NP head takes the case.
        if not seen_noun and _strip_punct_word(word)[1].casefold() in _POSSESSIVE_ONLY:
            converted.append(word)
            continue
        if not seen_noun and _is_theory_knowledge_token(word) and _is_adjective(word):
            # A substantivized knowledge head (for example, «составляющие»)
            # is the NP head.  Its following genitive owner must stay intact.
            converted.append(_adj_to_dative(word))
            seen_noun = True
            continue
        if not seen_noun and _is_adjective(word) and not _looks_like_verbal_noun(word):
            converted.append(_adj_to_dative(word))
            continue
        if not seen_noun:
            converted.append(_noun_to_dative(word))
            seen_noun = True
            continue
        if _is_postposed_dative_adjective(word):
            converted.append(_adj_to_dative(word))
            continue
        leftover = list(head[index:])
        break
    converted.extend(leftover)
    text = _normalize_spaces(" ".join((*converted, *tail)))
    if text[:1].isupper():
        text = text[:1].lower() + text[1:]
    return text


def _phrase_to_dative(text: str) -> str:
    """Put a RESULT knowledge-object into the dative required by «опрос по»."""

    phrase = _normalize_spaces(text).strip(" .")
    if not phrase:
        return text
    body, parens = _detach_trailing_parens(phrase)
    comma_parts = _split_direct_case_commas(body)
    if len(comma_parts) > 1:
        converted = ", ".join(_phrase_to_dative(part) for part in comma_parts)
        return converted + parens
    dash_parts = re.split(r"\s+[–—]\s+", body, maxsplit=1)
    if len(dash_parts) == 2:
        converted = (
            _phrase_to_dative(dash_parts[0])
            + " – "
            + _phrase_to_dative(dash_parts[1])
        )
        return converted + parens
    coord = _split_first_coord_and(body)
    if coord:
        converted = _phrase_to_dative(coord[0]) + " и " + _phrase_to_dative(coord[1])
        return converted + parens
    return _dative_np(body) + parens


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
    result_low = result.casefold()
    if "диктант" in frame.clause.casefold():
        if "топограф" in frame.clause.casefold() or "знак" in result_low:
            return "топографический диктант"
        return "диктант"
    if "викторин" in selected:
        if "краевед" in selected:
            return "краеведческая викторина"
        return "викторина"
    # Event-as-check uses the selected RESULT only, so neighbouring mentions
    # of соревнования cannot replace a different control already in the clause.
    if "соревнован" in result_low and any(
        stem in result_low for stem in ("выступа", "участник", "участв")
    ):
        if "туристск" in result_low:
            return "выступление в туристских соревнованиях"
        return "выступление в соревнованиях"
    return ""


def _selected_activity(result: str, clause: str) -> str:
    return _normalize_spaces(f"{result} {clause}").casefold()


_EVENT_PARTICIPATION_RE = re.compile(
    r"(?i)(?:участв\w*|выступл\w*|участник\w*)\s+(?:в|во)\b"
)
_EVENT_KIND_STEMS = (
    "соревнован",
    "конкурс",
    "слёт",
    "слет",
    "праздник",
    "мероприяти",
)


def _has_event_participation(text: str) -> bool:
    return bool(_EVENT_PARTICIPATION_RE.search(text or ""))


def _activity_event_type(result: str, clause: str) -> str:
    """Lesson events from selected activity. Control methods never become TYPE."""
    result_low = result.casefold()
    clause_low = clause.casefold()
    blob = _normalize_spaces(f"{result} {clause}")
    if not _has_event_participation(blob):
        return ""
    event_src = result_low if any(
        stem in result_low for stem in _EVENT_KIND_STEMS
    ) else clause_low
    if "соревнован" in event_src:
        if "туристск" in event_src:
            return "туристские соревнования"
        return "соревнования"
    if "конкурс" in event_src:
        return "конкурс"
    if "слёт" in event_src or "слет" in event_src:
        return "туристский слёт" if "туристск" in event_src else "слёт"
    if "праздник" in event_src:
        return "праздник"
    if "мероприяти" in event_src:
        return "мероприятие"
    return ""


def _practice_activity_type(result: str, clause: str) -> str:
    """Leading practical activity, not a copied CONTROL label."""
    result_low = result.casefold()
    selected = _selected_activity(result, clause)
    clause_low = clause.casefold().strip()
    if "норматив" in selected and result_low.startswith("выполняет норматив"):
        return "тестирование"
    if "игровой форме" in selected:
        return "игровое занятие"
    if re.search(
        r"(?i)(?:^|\bи\s+)(?:отработка|выполнение|применение|изучение)\s+"
        r"(?:\w+\s+){0,3}(?:страховк|страховщик|страховочн|самострах)\w*",
        clause_low,
    ) or re.match(
        r"(?i)^(?:надевание|использование)\s+"
        r"(?:\w+\s+){0,3}страховочн\w*",
        clause_low,
    ):
        return "практикум по страховке"
    if re.search(r"(?i)\b(?:узл|вязани)\w*", selected):
        return "практикум по вязанию узлов"
    if result_low.startswith("составляет и ведёт дневник"):
        return "проектно-практическое занятие"
    if re.search(r"(?i)\b(?:лазани|трасс|вис|тактик)\w*", selected):
        return "учебно-тренировочное занятие"
    if "развертывает" in result_low and any(stem in selected for stem in ("лагер", "бивак")):
        return "практикум по организации бивака"
    if "обязанност" in result_low and "должност" in selected:
        return "практикум по исполнению должностей"
    if re.search(r"(?i)\bзнак[аиуов]?\b", result_low):
        if "топограф" in selected:
            return "практикум по работе с топографическими знаками"
        return "практикум по работе со знаками"
    if "азимут" in result_low:
        return "измерительный практикум"
    if "масштаб" in result_low:
        return "практикум по работе с картой"
    if (
        result_low.startswith("ориентирует")
        or "стороны горизонта" in result_low
        or ("ориентир" in result_low and any(token in result_low for token in ("карт", "маршрут")))
        or ("компас" in result_low and re.search(r"карт", result_low))
    ):
        return "практикум по ориентированию"
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
    return _normalize_spaces(" ".join((*content, *prep_phrase))).strip(" ,;:")


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
        obj = re.sub(r"\s+(и|а|но)$", "", obj, flags=re.IGNORECASE)
        actions.append((match.group(0).casefold(), obj))
    return actions


def _product_control(result: str) -> str:
    text = result.rstrip(".")
    low = text.casefold()
    if re.match(r"(?i)^(характеризует|называет)\b", text):
        return ""
    report = re.fullmatch(r"(?i)составляет отч[её]т (.+)", text)
    if report:
        return f"проверка отчёта {report.group(1)}"
    if "меню" in low:
        product = "проверка меню"
        if "список продуктов" in low:
            product += " и списка продуктов"
        cooking = next((obj for verb, obj in _result_actions(result) if verb == "готовит"), "")
        if cooking:
            product += "; педагогическое наблюдение за приготовлением " + _phrase_to_genitive(cooking)
        return product
    if "план-график" in low or "плана-график" in low:
        parts = [
            _phrase_to_genitive(match.group(0).strip(" ,;"))
            for match in re.finditer(
                r"(?i)план-график(?:\s+(?!и\b)\S+)?|план\s+(?!график)[а-яё]+(?:\s+(?!и\b)[а-яё]+)?",
                text,
            )
        ]
        if parts:
            check = "проверка " + " и ".join(parts)
        else:
            check = "проверка плана-графика"
        gear = next(
            (
                obj
                for verb, obj in _result_actions(result)
                if verb == "подготавливает" and "снаряжен" in obj.casefold()
            ),
            "",
        )
        if gear:
            check += "; педагогическое наблюдение за подготовкой " + _phrase_to_genitive(
                gear
            )
        return check
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
        return "педагогическое наблюдение за проведением " + _phrase_to_genitive(phrase)
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
        remainder = _normalize_spaces(
            re.sub(r"(?i)^выполняет упражнения\s*", "", result)
        ).rstrip(".")
        conjunct = re.search(r"(?i)\sи\s+([а-яё-]+)(.*)$", remainder)
        if conjunct and _looks_like_verbal_noun(conjunct.group(1)):
            tail = _phrase_to_genitive(
                _normalize_spaces(conjunct.group(1) + conjunct.group(2))
            )
            remainder = remainder[: conjunct.start(1)] + tail
            return "проверка выполнения упражнений " + remainder
        if remainder:
            return "педагогическое наблюдение за выполнением упражнений " + remainder
        return "педагогическое наблюдение за выполнением упражнений"
    wrapped_nominal = re.match(r"(?i)^выполняет\s+(закаливание|катание)\b", result)
    if wrapped_nominal:
        focus = re.sub(r"(?i)^выполняет\s+", "", result).rstrip(".")
        return "педагогическое наблюдение за выполнением " + _phrase_to_genitive(focus)
    if re.match(r"(?i)^оказывает\b", result) and "помощ" in low:
        return "педагогическое наблюдение за оказанием первой помощи"
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
        cycle = []
        if "определяет" in low and "мест" in low:
            cycle.append("выбором места для привалов и ночлегов")
        cycle.append("развертыванием и свертыванием лагеря")
        if "разжигает" in low:
            cycle.append("разжиганием костра")
        if len(cycle) == 1:
            return "педагогическое наблюдение при развертывании и свертывании лагеря"
        return "педагогическое наблюдение за " + _join_and(cycle)
    if re.match(r"(?i)^участвует\s+в\b", result):
        body = re.sub(r"(?i)^участвует\s+в\s*", "", result).rstrip(".")
        return "педагогическое наблюдение за участием в " + body
    if re.match(r"(?i)^выполняет\s+", result) and any(
        stem in low for stem in ("аппликац", "конструир", "рисован", "рисунк")
    ):
        chunks: list[str] = []
        for part in re.split(r",\s+", result.rstrip(".")):
            if _is_finite_result_phrase(part):
                chunks.append(part)
            elif chunks:
                chunks[-1] = f"{chunks[-1]}, {part}"
        pieces: list[str] = []
        for chunk in chunks:
            verb = _leading_finite_verb(chunk)
            obj = _normalize_spaces(chunk[len(verb) :]).strip(" ,")
            verb_low = verb.casefold()
            if verb_low == "выполняет":
                focus = _phrase_to_genitive(obj) if obj else ""
                pieces.append(_normalize_spaces(f"выполнением {focus}"))
            elif verb_low == "конструирует":
                pieces.append(_normalize_spaces(f"конструированием {obj}"))
            elif verb_low == "рисует":
                focus = _phrase_to_genitive(obj) if obj else ""
                pieces.append(_normalize_spaces(f"рисованием {focus}"))
        if pieces:
            return "педагогическое наблюдение за " + _join_and(pieces)
        focus = re.sub(r"(?i)^выполняет\s+", "", result).rstrip(".")
        return "педагогическое наблюдение за выполнением " + _phrase_to_genitive(focus)
    if "экскурси" in type_low or low.startswith("совершает прогул") or low.startswith("совершает экскурси"):
        return "педагогическое наблюдение на экскурсии"
    return ""


def _skill_control(result: str) -> str:
    actions = _result_actions(result)
    if not actions:
        return ""
    # Observable performance is not a submitted product. Keep the selected
    # result's objects/conditions; never borrow a method from a neighbouring clause.
    _equip_observe = {
        "укладывает": "укладкой",
        "подгоняет": "подгонкой",
        "ухаживает": "уходом",
        "ремонтирует": "ремонтом",
    }
    if actions and all(verb in _equip_observe for verb, _ in actions):
        parts = []
        for verb, obj in actions:
            if verb == "ухаживает" and obj.casefold().startswith("за "):
                parts.append(_equip_observe[verb] + " " + obj)
            elif verb == "ремонтирует" and obj.casefold() in {"его", "её", "ее", "их"}:
                parts.append(_equip_observe[verb])
            else:
                parts.append(_equip_observe[verb] + " " + _phrase_to_genitive(obj))
        return "педагогическое наблюдение за " + _join_and(parts)
    _hygiene_observe = {
        "применяет": "применением",
        "подбирает": "подбором",
        "ухаживает": "уходом",
    }
    if (
        actions
        and all(verb in _hygiene_observe for verb, _ in actions)
        and any(verb == "применяет" for verb, _ in actions)
    ):
        parts = []
        for verb, obj in actions:
            if verb == "ухаживает" and obj.casefold().startswith("за "):
                parts.append(_hygiene_observe[verb] + " " + obj)
            elif verb == "подбирает" and "одежд" in obj.casefold() and "обув" in obj.casefold():
                parts.append("подбором одежды и обуви")
            else:
                parts.append(
                    _hygiene_observe[verb]
                    + " "
                    + _phrase_to_genitive(_short_object(obj) if verb != "ухаживает" else obj)
                )
        return "педагогическое наблюдение за " + _join_and(parts)
    if len(actions) == 1:
        verb, obj = actions[0]
        if verb == "выполняет" and "норматив" in obj.casefold():
            return "тестирование"
        leading = _nominal_activity_lemma(obj.split()[0]) if obj.split() else ""
        if verb == "выполняет" and leading in _NOMINAL_PERFORM_LEMMAS:
            return (
                "педагогическое наблюдение за выполнением "
                + _coordinated_phrase_to_genitive(obj)
            )
        low = obj.casefold()
        if verb == "выполняет" and "обязанност" in low:
            return "педагогическое наблюдение за выполнением " + _phrase_to_genitive(obj)
        if verb == "выступает" and "соревнован" in low:
            return "педагогическое наблюдение за участием " + re.sub(r"\s+в качестве\b.*", "", obj)
        if verb == "применяет":
            return "педагогическое наблюдение за применением " + _phrase_to_genitive(_short_object(obj))
        if verb in {"ориентирует", "отбирает"}:
            noun = {"ориентирует": "ориентированию", "отбирает": "отбору"}[verb]
            return "практическое задание по " + noun + " " + _phrase_to_genitive(obj)
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
        elif objects and len(set(objects)) == 1:
            body = _normalize_spaces(f"{' и '.join(nouns)} {object_text}")
        else:
            full_objects = [
                _phrase_to_genitive(
                    re.sub(r"\s*\([^)]*\)", "", obj.split(",")[0]).strip()
                )
                for _verb, obj in actions
            ]
            body = _join_and(
                f"{noun} {obj}".strip()
                for noun, obj in zip(nouns, full_objects)
            )
        return f"практическое задание по {body}".rstrip()
    if any(verb == "изготавливает" for verb, _obj in actions) and any(
        verb == "разучивает" and "транспортир" in obj.casefold() for verb, obj in actions
    ):
        made = []
        observed = []
        for verb, obj in actions:
            if verb == "изготавливает":
                made.extend(
                    _phrase_to_genitive(part.strip())
                    for part in obj.split(",")
                    if part.strip()
                )
            elif verb == "разучивает" and "транспортир" in obj.casefold():
                transport = re.search(r"(?i)способ\w*\s+транспортиров\w*", obj)
                if transport:
                    observed.append("педагогическое наблюдение при разучивании " + _phrase_to_genitive(transport.group(0)))
        if made:
            if len(made) == 1:
                joined = made[0]
            else:
                joined = ", ".join(made[:-1]) + " и " + made[-1]
            return "; ".join(["проверка изготовленных " + joined, *observed])
    if any(verb == "строит" and "график" in obj for verb, obj in actions):
        checks = []
        for verb, obj in actions:
            if verb == "измеряет":
                checks.append("практическое задание по измерению " + _phrase_to_genitive(_short_object(obj)))
            elif verb == "строит":
                checks.append("проверка " + _phrase_to_genitive(obj))
        if len(checks) == len(actions):
            return "; ".join(checks)
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
            "выполняет",
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
        proven = _control_from_proven_result(
            planned_result, lesson_type=lesson_type
        )
        if proven:
            return proven
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


_DISCOURSE_HEAD_RE = re.compile(
    r"(?i)^(рассказ|бесед|сведен|лекци|истори|описан|сообщен|поняти|значение)"
)
_THEMATIC_FORM_ADJ_RE = re.compile(r"(?i)^(?:экскурсионн|соревновательн)\w*")
_EXPLICIT_ACTIVITY_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:совершает|посещает|проводит)\s+(?:прогулк|экскурси)"
    r"|^(?:экскурси[яиею])\s+(?:по|в|на|к|во)\b"
    r"|^(?:посещени[ея])\s+\w"
    r"|^(?:проведени[ея])\s+(?:экскурси|занят|соревнован|мероприяти|праздник)"
    r"|^(?:участи[ея])\s+(?:в|во)\b"
    r"|^(?:отработк[аеи])\s+\w"
    r"|^(?:заняти[яе])\s+на\s+\w"
    r"|\b(?:отрабатывает|выполняет\s+упражнен)"
    r")"
)


def _explicit_activity_evidence(clause: str) -> bool:
    """True only for an action-clause, not a topic/form heading or object list."""

    lead = _normalize_spaces(clause)
    if not lead:
        return False
    low = lead.casefold()
    if _DISCOURSE_HEAD_RE.match(low) or _THEMATIC_FORM_ADJ_RE.match(low):
        return False
    if re.match(r"(?i)^[а-яё\s,-]+:\s+\S", low) and not _EXPLICIT_ACTIVITY_RE.search(low):
        return False
    return bool(_EXPLICIT_ACTIVITY_RE.search(low))


def _normalize_source_form(label: str, *, clause: str) -> str:
    """Map scorer labels onto CE2 TYPE names; drop generic practical overlay."""

    blob = clause.casefold()
    if label == "тренировочное занятие":
        return "учебно-тренировочное занятие"
    if label == "занятие на местности":
        return "учебно-тренировочное занятие на местности"
    if label == "соревнования" and "туристск" in blob and "участ" in blob:
        return "туристские соревнования"
    if label in {"практическое занятие", "теоретическое занятие"}:
        return ""
    return label


def _clause_occupation_form(clause: str) -> str:
    """Explicit activity form of the selected source clause, or empty."""

    lead = _normalize_spaces(clause)
    if not lead:
        return ""
    low = lead.casefold()
    if _DISCOURSE_HEAD_RE.match(low):
        return ""
    if re.match(r"(?i)^посещени[ея]\s+\w", low):
        return "экскурсия"
    if re.match(r"(?i)^проведени[ея]\s+экскурси", low):
        return "экскурсия"
    if re.search(r"(?i)\b(?:совершает|посещает|проводит)\s+экскурси", low):
        return "экскурсия"
    dominant = _dominant_label(_line_form_scores(lead), min_score=2)
    if dominant in {"игра", "викторина"} and not re.match(r"(?i)^(игр|викторин)", lead):
        dominant = None
    if dominant in {"беседа", "исследовательское занятие", "ситуационное занятие"}:
        dominant = None
    if not dominant:
        return ""
    return _normalize_source_form(dominant, clause=lead)


def _allow_specialized_type(
    clause: str, *, theory_hours: int, practice_hours: int
) -> bool:
    if practice_hours:
        return True
    if theory_hours and not practice_hours:
        return _explicit_activity_evidence(clause)
    return False


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
        selected = _selected_activity(planned_result, frame.clause)
        if "викторин" in result or re.search(
            r"(?i)(?:проведен|провод).{0,40}викторин", clause
        ):
            return "викторина"
        if "экскурси" in result and any(x in result for x in ("совершает", "посещает")):
            return "экскурсия"
        if result.startswith("исследует") and any(x in clause for x in ("гипотез", "сравнен", "измерен", "наблюден")):
            return "исследовательское занятие"
        if "имитац" in selected and "ситуаци" in selected:
            return "ситуационный тренинг"
        event_type = _activity_event_type(planned_result, frame.clause)
        if event_type:
            return event_type
        if re.match(r"(?i)^участвует\s+в\b", planned_result):
            if "дидактическ" in result and re.search(r"(?i)\bигр", planned_result):
                return "дидактическое занятие"
            if re.search(r"(?i)\b(?:играх|игре|эстафет)", planned_result):
                return "игра"
            if "викторин" in result:
                return "викторина"
            if re.search(r"(?i)\bпоход", planned_result):
                return "поход"
            if re.search(r"(?i)занятиях\s+в\s+бассейне", planned_result):
                return "учебно-тренировочное занятие"
            if re.search(r"(?i)занятиях\s+на\s+скалодроме", planned_result):
                return "учебно-тренировочное занятие"
        if result.startswith("выполняет практическое задание по теме"):
            # The generic safe RESULT intentionally carries no activity form.
            # Recover TYPE only from the row-local practical source and
            # only when the existing taxonomy has one unambiguous strong cue.
            source = practice_text.strip() or frame.clause
            occupation = _clause_occupation_form(source)
            if occupation:
                return occupation
            special_scores = {
                label: score
                for label, score in _line_form_scores(source).items()
                if label
                not in {
                    "практическое занятие",
                    "беседа",
                    "исследовательское занятие",
                    "ситуационное занятие",
                }
            }
            grounded = _dominant_label(
                special_scores, min_score=2
            )
            if grounded:
                mapped = _normalize_source_form(grounded, clause=source)
                if mapped:
                    return mapped
                return grounded
            return "практическое занятие"
        if "составляет" in result and "план" in result and "план-график" in result:
            return "проектно-практическое занятие"
        if result.startswith(("проводит наблюдения", "проводит краеведческие наблюдения", "наблюдает")):
            return "занятие-наблюдение"
        if result.startswith(("выполняет упражнения", "отрабатывает", "разучивает")):
            activity_type = _practice_activity_type(planned_result, frame.clause)
            if activity_type:
                return activity_type
            if "движени" in result and "местности" in result:
                return "учебно-тренировочное занятие на местности"
            return "учебно-тренировочное занятие"
        if result.startswith("изучает") and re.search(
            r"(?i)\b(?:упражнен|техник|при[её]м)\w*",
            f"{planned_result} {frame.clause}",
        ):
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
            activity_type = _practice_activity_type(planned_result, frame.clause)
            if activity_type:
                return activity_type
            if "измеряет" in result and "шаг" in result and "измерен" in clause:
                return "измерительный практикум"
            if "доклад" in result and "район" in result and "поход" in clause:
                return "краеведческий практикум"
            if "аптечк" in result and "формирован" in clause:
                return "практикум по комплектованию аптечки"
            if (
                "оказывает" in result
                and "помощ" in result
                and (
                    "перв" in result
                    or "доврачебн" in result
                    or "перв" in clause
                    or "доврачебн" in clause
                )
            ):
                return "практикум по оказанию первой помощи"
            if "транспортировки пострадавшего" in result and "транспортировки пострадавшего" in clause:
                return "практикум по транспортировке пострадавшего"
            if "дневник самоконтроля" in result and "дневника самоконтроля" in clause:
                return "практикум по самоконтролю"
            lead_token = _leading_activity_token(frame.clause)
            if (
                planned_result.casefold().startswith("выполняет")
                and lead_token
                and _CREATIVE_HEAD_RE.match(lead_token)
                and _dominant_label(_line_form_scores(frame.clause), min_score=2)
                == "творческая работа"
            ):
                return "творческая работа"
            return "практикум"
    lead = _leading_clause(frame)
    occupation = _clause_occupation_form(lead)
    if occupation and _allow_specialized_type(
        lead, theory_hours=theory_hours, practice_hours=practice_hours
    ):
        return occupation
    scores = _line_form_scores(lead)
    # A practice-hour allocation is not evidence of a practical activity form.
    # Special TYPE inference is allowed only when this row has grounded
    # practical source text.
    if practice_hours and practice_text.strip() and lead:
        dominant = _dominant_label(scores, min_score=2)
        if dominant in {"игра", "викторина"} and not re.match(
            r"(?i)^(игр|викторин)", lead
        ):
            dominant = None
        if dominant and dominant not in {"беседа", "исследовательское занятие", "ситуационное занятие"}:
            mapped = _normalize_source_form(dominant, clause=lead)
            if mapped:
                return mapped
            if dominant not in {"практическое занятие", "практикум"}:
                return dominant
    if theory_hours and not practice_hours:
        theory_scores = _line_form_scores(lead or theory_text)
        if theory_scores.get("беседа", 0) >= 2:
            return "беседа"
        return "теоретическое занятие"
    if practice_hours:
        event_clause = " ".join(
            part for part in (frame.clause, practice_text, program_content) if part
        )
        event_type = _activity_event_type(planned_result, event_clause)
        if event_type:
            return event_type
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
    "ухаживает": "ухаживать", "ремонтирует": "ремонтировать",
    "разжигает": "разжечь", "подбирает": "подобрать",
    "находит": "найти",
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


_EXERCISE_OP_RE = re.compile(
    r"(?i)(?:по|на) (определению|определение|отбору|отбор|отысканию|"
    r"отыскание|измерению|измерение|запоминание|глазомерную оценку|"
    r"инструментальное измерение) (.+)"
)
_EXERCISE_OP_SPLIT_RE = re.compile(
    r",\s+(?=(?:определению|определение|измерению|измерение|отбору|отбор|"
    r"отысканию|отыскание|оценке|глазомерную|инструментальное)\b)"
)


def _exercise_operation_parts(body: str) -> list[str]:
    """Не резать хвост, если в одной клаузе несколько равноправных операций."""

    chunks = [item.strip() for item in _EXERCISE_OP_SPLIT_RE.split(body) if item.strip()]
    if len(chunks) < 2:
        return [body]
    lead = "по "
    if chunks[0].casefold().startswith("на "):
        lead = "на "
    elif chunks[0].casefold().startswith("по "):
        lead = "по "
    parts = [chunks[0]]
    for chunk in chunks[1:]:
        if chunk.casefold().startswith(("по ", "на ")):
            parts.append(chunk)
        else:
            parts.append(lead + chunk)
    if all(_EXERCISE_OP_RE.fullmatch(part) for part in parts):
        return parts
    return [body]


def _observable_result_candidate(result: str) -> str:
    """Remove exercise wrappers only for explicitly named observable operations."""
    if result.startswith("Выполняет упражнения"):
        body = result.removeprefix("Выполняет упражнения ").rstrip(".")
        parts = []
        for block in re.split(r" и упражнения ", body):
            parts.extend(_exercise_operation_parts(block))
        converted = []
        for part in parts:
            match = _EXERCISE_OP_RE.fullmatch(part)
            if match is None:
                return result
            operation, rest = match.groups()
            verbs = {
                "определению": "определяет", "определение": "определяет",
                "отбору": "отбирает", "отбор": "отбирает",
                "отысканию": "находит", "отыскание": "находит",
                "измерению": "измеряет", "измерение": "измеряет",
                "запоминание": "распознаёт", "глазомерную оценку": "оценивает",
                "инструментальное измерение": "измеряет",
            }
            obj, conditions = _split_object_and_conditions(rest)
            if not obj and conditions:
                locative = re.match(r"(?i)^((?:на|по|в)\s+\S+)\s+(.+)$", conditions)
                if locative:
                    obj, conditions = locative.group(2), ""
                elif _is_preposition(conditions.split()[0]):
                    pass
                else:
                    obj, conditions = conditions, ""
            phrase = f"{verbs[operation]} {_inflect_object_phrase(obj, case='acc') if obj else ''}".strip()
            if operation == "глазомерную оценку":
                phrase += " глазомерно"
            if conditions:
                phrase += f" {conditions}"
            converted.append(phrase)
        return _cap_sentence(_join_and(converted))
    # Parenthetical action lists are details, not extra outcomes for the lesson.
    result = re.sub(
        r"\s*\(([^()]*)\)",
        lambda m: "" if _paren_has_actions(m.group(1)) or _is_finite_result_phrase(m.group(1)) else m.group(0),
        result,
    )
    return result.replace("Проводит различные наблюдения", "Проводит наблюдения").replace("Проводит различные краеведческие наблюдения", "Проводит краеведческие наблюдения")


def _observable_result(result: str) -> str:
    try:
        return _observable_result_candidate(result)
    except _UncertainGrammar:
        # A source-grounded exercise is already observable. Do not rewrite
        # its coordinated object merely to remove the exercise wrapper.
        return result


def _lower_lead(text: str) -> str:
    stripped = _normalize_spaces(text)
    if not stripped:
        return stripped
    if stripped[0].isupper() and not stripped[:2].isupper():
        return stripped[0].lower() + stripped[1:]
    return stripped


def _practice_unit_kind(clause: str) -> str:
    text = _normalize_spaces(clause)
    low = text.casefold()
    if re.search(r"(?i)\bосвоен", text):
        return "master"
    head = _leading_activity_token(text)
    if _is_exercise_word(head):
        return "exercise"
    if re.match(r"(?i)^элемент", head):
        return "element"
    # A later «игра/игры» does not reclassify a different leading action.
    if _is_leading_game_form(head):
        return "game"
    if re.search(r"(?i)\bспорт\b", low) or re.search(r"(?i)атлетик", low):
        return "sport"
    return "other"


def _slot_group_kind(kind: str) -> str:
    if kind in {"exercise", "element"}:
        return "perform"
    if kind in {"game", "sport"}:
        return "participate"
    return kind


def _unit_has_action_head(clause: str) -> bool:
    """True when the clause names its own action or lesson form."""

    text = _normalize_spaces(clause)
    if not text:
        return False
    if _practice_unit_kind(text) != "other":
        return True
    if _is_auxiliary_practice_unit(text):
        return True
    head = _leading_activity_token(text)
    if _is_action_head(head) or _is_leading_form_activity(head):
        return True
    if head and _CREATIVE_HEAD_RE.match(head):
        return True
    if _participation_lemma(head):
        return True
    return _action_class(text, theory_only=False) >= 2


def _is_dependent_catalog_unit(clause: str) -> bool:
    """Name-list / gloss with no action head: context, not a new activity."""

    if _unit_has_action_head(clause):
        return False
    text = _normalize_spaces(clause)
    if re.search(r"[«»\"„“]", text):
        return True
    parts = [part.strip() for part in re.split(r",\s+", text) if part.strip()]
    return len(parts) >= 2 and not any(_unit_has_action_head(part) for part in parts)


def _coalesce_activity_units(units: list[str]) -> list[str]:
    """Attach dependent catalogs to the previous activity before slot packing."""

    coalesced: list[str] = []
    for unit in units:
        if coalesced and _is_dependent_catalog_unit(unit):
            prev = coalesced[-1].rstrip(" .")
            coalesced[-1] = _normalize_spaces(f"{prev}: {unit}")
            continue
        coalesced.append(unit)
    return coalesced


def _finite_other_slot_result(clause: str) -> str:
    phrase, _frame = transform_clause_to_result(
        clause,
        theory_only=False,
        full_source=clause,
        topic_title="",
    )
    phrase = _observable_result(phrase) if phrase else ""
    if not phrase or not _is_finite_result_phrase(phrase):
        return ""
    if phrase.casefold().startswith(("характеризует", "называет")):
        return ""
    return phrase


def _strongest_finite_phrase(phrases: list[str]) -> str:
    if not phrases:
        return ""
    if len(phrases) == 1:
        return phrases[0]

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

    return max(phrases, key=score)


def _slot_has_named_practice_kind(slot: tuple[str, ...]) -> bool:
    kinds = {_practice_unit_kind(item) for item in slot}
    return bool(kinds & {"exercise", "element", "sport", "master", "game"})


def _adj_to_locative(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith("ые"):
        core = core[:-2] + "ых"
    elif low.endswith("ие") and not low.endswith(("ние", "тие")):
        core = core[:-2] + "их"
    elif low.endswith("ая"):
        core = core[:-2] + "ой"
    return f"{prefix}{core}{suffix}"


def _noun_to_locative(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith(("ах", "ях")):
        changed = core
    elif low.endswith("ы"):
        changed = core[:-1] + "ах"
    elif low.endswith("и") and len(core) > 3 and not low.endswith(("ии", "ени")):
        changed = core[:-1] + "ах"
    elif low.endswith("а"):
        changed = core[:-1] + "е"
    elif low.endswith("я"):
        changed = core[:-1] + "е"
    elif not re.search(r"(?i)[аеёиоуыэюя]$", low):
        changed = core + "е"
    else:
        changed = core
    return f"{prefix}{changed}{suffix}"


def _phrase_to_locative(phrase: str) -> str:
    words = _normalize_spaces(phrase).split()
    if not words:
        return phrase
    if len(words) >= 2 and all(_is_adjective(word) for word in words[:-1]):
        return _normalize_spaces(
            " ".join([*(_adj_to_locative(word) for word in words[:-1]), _noun_to_locative(words[-1])])
        )
    return _noun_to_locative(words[0]) if len(words) == 1 else _normalize_spaces(
        " ".join([_noun_to_locative(words[0]), *words[1:]])
    )


def _adj_to_instrumental(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith("ый"):
        core = core[:-2] + "ым"
    elif low.endswith("ий"):
        core = core[:-2] + "им"
    elif low.endswith("ая"):
        core = core[:-2] + "ой"
    elif low.endswith("ое"):
        core = core[:-2] + "ым"
    return f"{prefix}{core}{suffix}"


def _noun_to_instrumental(word: str) -> str:
    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if low.endswith("а"):
        changed = core[:-1] + "ой"
    elif low.endswith("я"):
        changed = core[:-1] + "ей"
    elif low.endswith(("о", "е")):
        changed = core + "м"
    elif not re.search(r"(?i)[аеёиоуыэюя]$", low):
        changed = core + "ом"
    else:
        changed = core
    return f"{prefix}{changed}{suffix}"


def _phrase_to_instrumental(phrase: str) -> str:
    words = _normalize_spaces(phrase).split()
    if not words:
        return phrase
    if len(words) >= 2 and all(_is_adjective(word) for word in words[:-1]):
        return _normalize_spaces(
            " ".join(
                [
                    *(_adj_to_instrumental(word) for word in words[:-1]),
                    _noun_to_instrumental(words[-1]),
                ]
            )
        )
    return _noun_to_instrumental(words[0]) if len(words) == 1 else _normalize_spaces(
        " ".join([_noun_to_instrumental(words[0]), *words[1:]])
    )


def _exercise_remainder(clause: str) -> str:
    tokens = _normalize_spaces(clause).split()
    mods, rest = _leading_modifiers(tokens)
    if rest and _is_exercise_word(re.sub(r"[^\wёЁ]", "", rest[0])):
        remainder = " ".join(rest[1:]).strip()
        if mods:
            lead = _lower_lead(" ".join(mods))
            return _normalize_spaces(f"{lead} упражнения {remainder}").strip()
        return remainder
    return _lower_lead(clause)


def _flatten_prep_parts(item: str, prep: str) -> list[str]:
    parts = re.split(rf"(?i),\s*{re.escape(prep)}\s+", _normalize_spaces(item))
    cleaned: list[str] = []
    for part in parts:
        text = part.strip()
        if text.casefold().startswith(prep + " "):
            text = text[len(prep) + 1 :]
        if text:
            cleaned.append(text)
    return cleaned


def _compress_exercise_remainders(remainders: list[str]) -> str:
    dlya: list[str] = []
    other: list[str] = []
    for item in remainders:
        if item.casefold().startswith("для "):
            dlya.extend(_flatten_prep_parts(item, "для"))
        else:
            other.append(item)
    chunks: list[str] = []
    if dlya:
        chunks.append("для " + _join_and(dlya))
    for item in other:
        text = item
        if re.search(r"(?i)^(со?)\s+", text) and "," in text and not re.search(
            r"(?i),\s*(для|со?|на)\s+", text
        ):
            match = re.match(r"(?i)^(со?)\s+(.+)$", text)
            if match:
                nouns = [part.strip() for part in match.group(2).split(",") if part.strip()]
                text = match.group(1) + " " + _join_and(nouns)
        chunks.append(text)
    if len(chunks) >= 2 and any(
        not item.casefold().startswith("для ") for item in other
    ) and dlya:
        *head, last = chunks
        return ", ".join(head) + ", а также упражнения " + last
    return ", ".join(chunks)


def _game_participate_object(clause: str, *, more_follow: bool) -> str:
    objects, tail = _participation_object_parts(clause)
    if objects:
        if more_follow and len(objects) > 1:
            body = ", ".join(objects)
        elif len(objects) > 1:
            body = ", ".join(objects)
        else:
            body = objects[0]
        if tail:
            return f"{body}: {tail}" if not tail.startswith(" ") else f"{body}:{tail}"
        return body
    text = _normalize_spaces(clause)
    head, colon, tail = text.partition(":")
    pieces = [part.strip() for part in re.split(r"\s+и\s+", head) if part.strip()]
    locatives = [_phrase_to_locative(_lower_lead(part)) for part in pieces]
    if more_follow and len(locatives) > 1:
        body = ", ".join(locatives)
    else:
        body = _join_and(locatives)
    if colon:
        return f"{body}:{tail}" if tail.startswith(" ") else f"{body}: {tail.strip()}"
    return body


def _sport_participate_object(clause: str) -> str:
    return "занятиях " + _phrase_to_instrumental(_lower_lead(clause))


def _master_result(clause: str) -> str:
    match = re.match(
        r"(?i)^(.+?)\s*\(\s*освоение\s+(.+)\s*\)\s*$",
        _normalize_spaces(clause),
    )
    if not match:
        return _lower_lead(clause)
    domain = _lower_lead(match.group(1).strip())
    obj = match.group(2).strip()
    obj_acc = re.sub(r"(?i)^одного\b", "один", obj)
    return _normalize_spaces(
        f"осваивает {obj_acc} {_head_noun_to_genitive(domain)}"
    )


def _perform_result(units: list[tuple[str, str]]) -> str:
    remainders: list[str] = []
    for kind, clause in units:
        if kind == "exercise":
            remainder = _exercise_remainder(clause)
            if remainder.casefold().startswith("гимнастическ"):
                remainders.append(_lower_lead(clause))
            else:
                remainders.append(remainder)
        else:
            remainders.append(_lower_lead(clause))
    body = _compress_exercise_remainders(remainders)
    if any(
        kind == "exercise" and not _exercise_remainder(clause).casefold().startswith("гимнастическ")
        for kind, clause in units
    ):
        return _normalize_spaces(f"выполняет упражнения {body}".strip())
    return _normalize_spaces(f"выполняет {body}".strip())


def _participate_result(units: list[tuple[str, str]]) -> str:
    objects: list[str] = []
    for index, (kind, clause) in enumerate(units):
        more = index < len(units) - 1
        if kind == "game":
            objects.append(_game_participate_object(clause, more_follow=more or any(
                item[0] == "sport" for item in units[index + 1 :]
            )))
        else:
            objects.append(_sport_participate_object(clause))
    return "участвует в " + _join_and(objects)


def _aggregate_slot_result(slot: tuple[str, ...]) -> str:
    if not slot:
        return ""
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    for clause in slot:
        kind = _practice_unit_kind(clause)
        group = _slot_group_kind(kind)
        if groups and groups[-1][0] == group and group in {"perform", "participate"}:
            groups[-1][1].append((kind, clause))
        else:
            groups.append((group, [(kind, clause)]))
    predicates: list[str] = []
    for group, units in groups:
        if group == "perform":
            predicates.append(_perform_result(units))
        elif group == "participate":
            predicates.append(_participate_result(units))
        elif group == "master":
            predicates.append(_master_result(units[0][1]))
        else:
            for _kind, clause in units:
                phrase = _finite_other_slot_result(clause)
                if phrase:
                    predicates.append(phrase)
    finite = [
        item.rstrip(" .")
        for item in predicates
        if item and _is_finite_result_phrase(item)
    ]
    if not finite:
        return ""
    if not _slot_has_named_practice_kind(slot) and len(finite) <= 1:
        return _cap_sentence(_strongest_finite_phrase(finite))
    joined = [finite[0], *(_lower_lead(item) for item in finite[1:])]
    return _cap_sentence(
        _merge_repeated_verbs(", ".join(joined), only=frozenset({"выполняет"}))
    )


def _slot_selected_activity(slot: tuple[str, ...]) -> tuple[str, str]:
    """One grounded activity frame for the slot: (clause, RESULT)."""

    joined = _normalize_spaces(". ".join(slot))
    result = _aggregate_slot_result(slot)
    if not result:
        return joined, ""
    finite_units = [clause for clause in slot if _finite_other_slot_result(clause)]
    if _slot_has_named_practice_kind(slot) or len(finite_units) > 1:
        return joined, result
    target = result.rstrip(".")
    for clause in slot:
        phrase = _finite_other_slot_result(clause)
        if phrase and _cap_sentence(phrase).rstrip(".") == target:
            return clause, result
    return (slot[0] if slot else ""), result


def _slot_control_from_result(result: str) -> str:
    text = _normalize_spaces(result).rstrip(".")
    matches = list(
        re.finditer(
            r"(?i)\b(выполняет|осваивает|участвует\s+в|выбирает)\b",
            text,
        )
    )
    if not matches:
        return ""
    parts: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        verb = re.sub(r"\s+", " ", match.group(1).casefold())
        body = text[match.end() : end].strip(" ,")
        if verb == "выполняет":
            if body.casefold().startswith("упражнения"):
                remainder = body[len("упражнения") :].strip()
                if "а также упражнения" in remainder:
                    left, right = remainder.split("а также упражнения", 1)
                    obj = (
                        "упражнений "
                        + left.strip(" ,")
                        + ", а также упражнений "
                        + right.strip()
                    )
                else:
                    obj = "упражнений " + remainder if remainder else "упражнений"
                    obj = obj.replace(", элементы ", ", элементов ").replace(
                        ", элементы", ", элементов"
                    )
                    if obj.endswith(" элементы акробатики"):
                        obj = obj[: -len(" элементы акробатики")] + " элементов акробатики"
                parts.append("выполнением " + _normalize_spaces(obj))
            else:
                parts.append("выполнением " + _coordinated_phrase_to_genitive(body))
        elif verb.startswith("участвует"):
            parts.append("участием в " + body)
        elif verb == "выбирает":
            parts.append("выбором " + _phrase_to_genitive(body))
        else:
            obj = re.sub(r"(?i)^один\b", "одного", body)
            parts.append("освоением " + obj)
    return "педагогическое наблюдение за " + _join_and(parts)


def _derive_fields_candidate(
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str = "",
    theory_hours: int = 0,
    practice_hours: int = 0,
    occurrence_index: int = 0,
    practice_appearance_count: int = 0,
) -> ContentEngineV2Result:
    """Цепочка: source → action/object/conditions → RESULT → CONTROL → TYPE."""

    warnings: list[str] = []
    units = _coalesce_activity_units(practice_units_from_text(practice_text))
    if (
        practice_hours
        and practice_appearance_count > 1
        and units
    ):
        slots, flags = assign_distributed_practice_slots(
            units, practice_appearance_count
        )
        index = min(occurrence_index, len(slots) - 1)
        slot = slots[index]
        continuation = flags[index] if index < len(flags) else False
        if len(units) > practice_appearance_count:
            warnings.append(SLOT_PACK_WARNING)
        if continuation:
            warnings.append(SLOT_CONTINUE_WARNING)
        selected_clause, planned_result = _slot_selected_activity(slot)
        assigned_clause = _normalize_spaces(". ".join(slot))
        type_clause = selected_clause if planned_result else assigned_clause
        selected_frame = ActionFrame(
            clause=type_clause,
            action="",
            object="",
            conditions="",
        )
        kinds = {_practice_unit_kind(item) for item in slot}
        if kinds & {"exercise", "element", "sport", "master"}:
            lesson_type = "учебно-тренировочное занятие"
        else:
            lesson_type = type_from_frame(
                selected_frame,
                theory_hours=theory_hours,
                practice_hours=practice_hours,
                theory_text=theory_text,
                practice_text=type_clause,
                program_content=type_clause,
                planned_result=planned_result,
            )
        assessment = _slot_control_from_result(planned_result)
        if not assessment:
            assessment = control_from_frame(
                selected_frame,
                lesson_type=lesson_type,
                theory_hours=theory_hours,
                practice_hours=practice_hours,
                planned_result=planned_result,
            )
        assessment = _align_control_to_result(assessment, planned_result)
        return ContentEngineV2Result(
            frame=ActionFrame(
                clause=assigned_clause,
                action="",
                object="",
                conditions="",
            ),
            lesson_type=lesson_type,
            planned_result=planned_result,
            assessment_method=assessment,
            theory_text=theory_text,
            practice_text=practice_text,
            warnings=tuple(warnings),
        )
    clause, theory_only, source_pool = select_source_clause(
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
        source_pool = source_pool or [clause]
    result_clause, neighbor_extras = _enrich_with_neighbors(
        clause, source_pool or [clause]
    )
    planned_result, frame = transform_clause_to_result(
        result_clause,
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
    follow_results: list[str] = []
    for unit in neighbor_extras:
        extra, _extra_frame = transform_clause_to_result(
            unit,
            theory_only=theory_only,
            full_source=_normalize_spaces(
                f"{theory_text} {practice_text} {program_content} {unit}"
            ),
            topic_title=topic_title,
        )
        extra = _observable_result(extra)
        extra_core = _drop_leading_verb(extra).rstrip(" .")
        if not extra:
            continue
        if extra.casefold().rstrip(".") in planned_result.casefold():
            continue
        if extra_core and extra_core.casefold() in planned_result.casefold():
            continue
        follow_results.append(extra)
    if follow_results:
        planned_result = _merge_part_results([planned_result, *follow_results])
    type_frame = ActionFrame(
        clause,
        frame.action,
        frame.object,
        frame.conditions,
    )
    lesson_type = type_from_frame(
        type_frame,
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
    assessment = _align_control_to_result(assessment, planned_result)
    return ContentEngineV2Result(
        frame=frame,
        lesson_type=lesson_type,
        planned_result=planned_result,
        assessment_method=assessment,
        theory_text=theory_text,
        practice_text=practice_text,
        warnings=tuple(warnings),
    )


def _proven_finite_predicates() -> set[str]:
    return set(_PROVEN_FINITE_VERBS)


def _quality_issue(
    result: str, control: str, *, source: str = "", clause: str = "",
) -> str:
    """Validate a finished candidate; syntax complexity alone is not a defect."""
    if not result or not control:
        return "empty_triad"
    if source and clause:
        source_words = {word[:4] for word in _word_tokens(source.casefold()) if len(word) >= 4}
        clause_words = {word[:4] for word in _word_tokens(clause.casefold()) if len(word) >= 4}
        if clause_words - source_words:
            return "source_leakage"
    allowed = _proven_finite_predicates()
    first = result.split()[0].casefold()
    if first not in allowed:
        return "unproven_predicate"
    broken_genitive_plural = re.match(
        r"(?i)^(?:изучает|отрабатывает|выполняет|применяет)\s+([а-яё-]+ий)\b",
        result.rstrip("."),
    )
    if broken_genitive_plural and re.search(
        rf"(?i)\b(?:изучение|отработка|выполнение|применение)\s+{re.escape(broken_genitive_plural.group(1))}\b",
        clause,
    ):
        return "unproven_object_case"
    broken_genitive_object = re.match(
        r"(?i)^изучает\s+([а-яё-]+(?:ых|их))\s+([а-яё-]+)",
        result.rstrip("."),
    )
    if broken_genitive_object and re.search(
        rf"(?i)\bизучение\s+{re.escape(broken_genitive_object.group(1))}\s+"
        rf"{re.escape(broken_genitive_object.group(2))}\b",
        clause,
    ):
        return "unproven_object_case"
    for left, right in re.findall(r"(?i)\b(\w+)\s+и\s+(\w+)", result):
        if left.casefold() in allowed and right.casefold() not in allowed:
            return "unproven_coordinated_predicate"
    for match in re.finditer(r"(?i)\bучаствует\s+в\s+([^.,;:]+)", result):
        words = match.group(1).casefold().split()
        while words and _is_adjective(words[0]):
            words.pop(0)
        compound_parts = (
            re.sub(r"[^а-яё-]", "", words[0]).split("-") if words else []
        )
        compound_case = bool(compound_parts) and all(
            _participation_lemma(part) is not None for part in compound_parts
        )
        if not words or (
            words[0] not in _PARTICIPATION_CASES and not compound_case
        ):
            loc_np = match.group(1).casefold()
            if clause and loc_np in clause.casefold():
                continue
            return "unproven_participation_case"
    if re.search(r"(?i)\bориентирует\s+(?:на|по|в)\b", result):
        return "unproven_verb_valency"
    prep_alt = "|".join(sorted((re.escape(item) for item in _PREPOSITIONS), key=len, reverse=True))
    finite_alt = "|".join(
        sorted((re.escape(item) for item in _proven_finite_predicates()), key=len, reverse=True)
    )
    for match in re.finditer(
        rf"(?i)\b({finite_alt})\s+({prep_alt})\s+([^.]*)",
        result.rstrip("."),
    ):
        complement = match.group(3)
        if not re.search(r"(?i)[а-яё-]*(?:ую|юю)\b", complement):
            continue
        pp = _normalize_spaces(f"{match.group(2)} {complement}").casefold()
        grounded = f"{clause} {source}".casefold()
        if pp.rstrip(" .,") in grounded:
            continue
        return "unproven_verb_valency"
    for text in (result, control):
        if text.count("(") != text.count(")") or text.count("«") != text.count("»") or text.count("„") != text.count("“"):
            return "unbalanced_delimiters"
        if re.search(r"[.!?]\s*[,;]|[,;]\s*[,;]", text):
            return "broken_clause_join"
    result_words = [
        word.strip(".,;:!?()[]{}«»„“\"")
        for word in _word_tokens(result.casefold())
        if word.strip() and word.strip()[0].isalpha()
    ]
    grounded = f"{clause} {source}".casefold()
    for first_word, second_word in zip(result_words, result_words[1:]):
        if (
            first_word.endswith("ий")
            and not re.search(
                rf"(?i)\b{re.escape(first_word)}\s+"
                rf"{re.escape(second_word)}\b",
                grounded,
            )
            and re.search(
                rf"(?i)\b{re.escape(first_word)}\s+"
                rf"{re.escape(second_word)}[ая]\b",
                grounded,
            )
        ):
            return "unproven_object_case"
    # Structural damage has priority over uncertain case diagnostics.
    nominal = re.match(r"(?i)^характеризует\s+(.+)$", result.rstrip("."))
    if nominal:
        obj_text = nominal.group(1)
        first_match = re.match(r"(?i)^[«\"(]*([а-яё-]+)", obj_text)
        first_word = first_match.group(1) if first_match else ""
        if first_word and _predicate_repeats_object("характеризует", first_word):
            return "tautological_predicate_object"
        if _knowledge_object_missing_owner(obj_text):
            return "missing_knowledge_owner"
        if first_word and _unproven_raw_colon_subject(first_word, clause):
            return "unproven_object_case"
        if first_word and not _characterize_head_ok(first_word):
            obj_tokens = obj_text.split()
            if not _is_substantivized_role_object(obj_tokens) and not (
                _coordinated_knowledge_object_span(obj_tokens)
            ):
                return "unproven_object_case"
    # A surviving genitive modifier after these transitive predicates is not
    # evidence of a successfully converted direct object. Do not guess a repair.
    # A span copied from the selected clause is already source-grounded.
    genitive_after_verb = re.search(
        r"(?i)\b(?:проводит|выполняет|подготавливает)\s+([а-яё]+(?:ых|их)\b.*)$",
        result.rstrip("."),
    )
    if genitive_after_verb:
        span = genitive_after_verb.group(1)
        if not (clause and span.casefold() in clause.casefold()):
            return "unproven_object_case"
    if re.search(r"(?i)\b(?:подготовки|выполнения)\s+[а-яё]+(?:ое|ая|ые)\b", control):
        return "unsafe_control_case"
    if control.startswith("устный опрос") and not control.startswith("устный опрос по теме „"):
        oral_part = control.split(";")[0].strip()
        if re.match(
            r"(?i)^устный опрос по (?:истори[ия]|биографи[ия]|рол[иь]|строени[юе]|"
            r"видам|значени[юе]|поняти[юе]|назначени[юе]|устройств[уео]|"
            r"правил[ам]|требованиям|характеристик[еа])$",
            oral_part,
        ):
            return "unsafe_oral_control"
        if _oral_object_grounded_in_result(oral_part, result):
            return ""
        # Closed, already-supported knowledge heads; no arbitrary tail gets
        # certified solely because the generator put 'по' in front of it.
        if not re.match(r"устный опрос по (?:истории|биографии|роли|строению|видам|значению|понятию)\b", oral_part):
            return "unsafe_oral_control"
        # A valid first head does not certify a raw object appended after a
        # comma (for example, nominative instead of the case required by 'по').
        for tail in oral_part.split(",")[1:]:
            if not re.match(r"\s*(?:истории|биографии|роли|строению|видам|значению|понятию)\b", tail):
                return "unsafe_oral_control"
    return ""


def _predicate_repeats_object(predicate: str, object_head: str) -> bool:
    """Detect a derivational tautology without a topic-specific vocabulary."""

    left = re.sub(r"[^а-яё]", "", predicate.casefold())
    right = re.sub(r"[^а-яё]", "", object_head.casefold())
    if min(len(left), len(right)) < 7:
        return False
    common_prefix = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        common_prefix += 1
    return common_prefix >= 7 and SequenceMatcher(None, left, right).ratio() >= 0.72


def _unproven_raw_colon_subject(object_head: str, clause: str) -> bool:
    """Reject an unchanged nominal heading before ':' as an unproven object."""

    if ":" not in clause:
        return False
    source_head = re.match(r"(?i)^\s*([а-яё-]+)", clause)
    return bool(source_head and source_head.group(1).casefold() == object_head.casefold())


def _salvage_drop_dependent_pp(rest: str) -> str:
    """A PP after the dropped conjunct is its complement, not the left verb's."""

    text = _normalize_spaces(rest)
    if not text:
        return ""
    first = text.split()[0]
    if _is_preposition(first):
        return ""
    return rest


def _salvage_left_is_complete(left: str, remainder: str) -> bool:
    """Bare finite verb without an object/complement is not a finished RESULT."""

    body = _normalize_spaces(remainder)
    if not body:
        return False
    first = body.split()[0]
    return not _is_preposition(first)


def _salvage_proven_finite_result(result: str) -> str | None:
    """Keep a proven finite predicate; drop one unproven coordinated neighbour."""

    allowed = _proven_finite_predicates()
    text = _normalize_spaces(result).rstrip(".")
    match = re.match(r"(?i)^([А-Яа-яЁё]+)\s+и\s+(\w+)(\s+.*)?$", text)
    if not match:
        return None
    left, right, rest = match.group(1), match.group(2), match.group(3) or ""
    if left.casefold() not in allowed or right.casefold() in allowed:
        return None
    kept_rest = _salvage_drop_dependent_pp(rest)
    if not _salvage_left_is_complete(left, kept_rest):
        return None
    return _cap_sentence(_normalize_spaces(f"{left} {kept_rest}"))


def _participatory_result_from_clause(clause: str) -> str | None:
    """Finite RESULT from a source «участие + PP», without guessing locative."""

    text = _normalize_spaces(clause).rstrip(".")
    match = re.search(r"(?i)\bучастие\s+((?:в|во|на|по|при)\s+.+)$", text)
    if not match:
        return None
    body = match.group(1).strip()
    if not body or not _is_preposition(body.split()[0]):
        return None
    phrase = _normalize_spaces(f"участвует {body}")
    if not _is_finite_result_phrase(phrase):
        return None
    return _cap_sentence(phrase)


def _triad_from_selected_frame(
    candidate: ContentEngineV2Result,
    *,
    planned_result: str,
    theory_hours: int,
    practice_hours: int,
) -> ContentEngineV2Result:
    selected = _normalize_spaces(candidate.frame.clause)
    lesson_type = type_from_frame(
        candidate.frame,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        theory_text="",
        practice_text=selected,
        program_content=selected,
        planned_result=planned_result,
    )
    control = _control_from_proven_result(planned_result, lesson_type=lesson_type)
    if not control:
        control = control_from_frame(
        candidate.frame,
        lesson_type=lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        planned_result=planned_result,
    )
    control = _align_control_to_result(control, planned_result)
    return replace(
        candidate,
        lesson_type=lesson_type,
        planned_result=planned_result,
        assessment_method=control,
    )


def _closed_candidate(
    candidate: ContentEngineV2Result, *, issue: str, topic_title: str, practical: bool,
) -> ContentEngineV2Result | None:
    """Small source-backed repairs, never a second unrestricted generator."""
    if issue == "unsafe_oral_control":
        rebuilt = _control_from_proven_result(
            candidate.planned_result, lesson_type=candidate.lesson_type
        )
        if rebuilt:
            return replace(candidate, assessment_method=rebuilt)
        _, control = _safe_topic_fields(topic_title, practical=practical)
        return replace(candidate, assessment_method=control)
    if issue == "unsafe_control_case":
        _, control = _safe_topic_fields(topic_title, practical=practical)
        return replace(candidate, assessment_method=control)
    if issue == "unproven_predicate" and re.match(r"(?i)^работа\s+(?:в|на|с)\s", candidate.frame.clause):
        result = re.sub(r"(?i)^работа\b", "Работает", candidate.planned_result)
        # Repair the dependent control as well; it was built from a nominal
        # fragment and cannot certify the newly completed predicate.
        _, control = _safe_topic_fields(topic_title, practical=practical)
        return replace(candidate, planned_result=result, assessment_method=control)
    if issue == "unproven_verb_valency" and re.match(r"(?i)^ориентирование\s+(?:на|по|в)\s", candidate.frame.clause):
        return replace(candidate, planned_result=re.sub(r"(?i)\bориентирует\b", "Ориентируется", candidate.planned_result))
    if issue == "unproven_object_case":
        grounded_action = re.search(
            r"(?i)\bи\s+((?:отработка|выполнение|применение)\s+.+)$",
            candidate.frame.clause,
        )
        if grounded_action:
            phrase, _action, _object, _conditions = _transform_segment(
                grounded_action.group(1),
                theory_only=False,
                full_source=candidate.frame.clause,
            )
            if phrase and _is_finite_result_phrase(phrase):
                return replace(candidate, planned_result=_cap_sentence(phrase))
    if issue == "unproven_coordinated_predicate":
        salvaged = _salvage_proven_finite_result(candidate.planned_result)
        if salvaged:
            return replace(candidate, planned_result=salvaged)
        participatory = _participatory_result_from_clause(candidate.frame.clause)
        if participatory:
            return replace(candidate, planned_result=participatory)
    if issue == "tautological_predicate_object":
        salvaged = _salvage_tautological_characterize_result(candidate.planned_result)
        if salvaged:
            return replace(candidate, planned_result=salvaged)
    return None


def derive_fields_v2(
    *, topic_title: str, theory_text: str, practice_text: str,
    program_content: str = "", theory_hours: int = 0, practice_hours: int = 0,
    occurrence_index: int = 0, practice_appearance_count: int = 0,
) -> ContentEngineV2Result:
    local = _normalize_spaces(f"{theory_text} {practice_text}")
    context = local or program_content
    practical = bool(practice_hours and practice_text.strip())
    candidate = _derive_fields_candidate(
        topic_title=topic_title, theory_text=theory_text, practice_text=practice_text,
        program_content=context, theory_hours=theory_hours, practice_hours=practice_hours,
        occurrence_index=occurrence_index, practice_appearance_count=practice_appearance_count,
    )
    candidate = replace(
        candidate,
        planned_result=re.sub(r"(?<=\d)(?=[А-Яа-яЁё])", " ", candidate.planned_result),
        assessment_method=re.sub(r"(?<=\d)(?=[А-Яа-яЁё])", " ", candidate.assessment_method),
    )
    grounded_source = f"{context} {topic_title}"
    issue = _quality_issue(candidate.planned_result, candidate.assessment_method,
                           source=grounded_source, clause=candidate.frame.clause)
    if not issue:
        return candidate
    repaired = _closed_candidate(candidate, issue=issue, topic_title=topic_title, practical=practical)
    if repaired is not None:
        if issue in {
            "unproven_coordinated_predicate",
            "unproven_object_case",
            "tautological_predicate_object",
        }:
            repaired = _triad_from_selected_frame(
                repaired,
                planned_result=repaired.planned_result,
                theory_hours=theory_hours,
                practice_hours=practice_hours,
            )
        repair_issue = _quality_issue(repaired.planned_result, repaired.assessment_method,
                                      source=grounded_source, clause=repaired.frame.clause)
        if not repair_issue:
            return repaired
    result, control = _safe_topic_fields(topic_title, practical=practical)
    lesson_type = candidate.lesson_type
    # Fallback may reduce specificity, but must never widen the source scope.
    # If TYPE needs one last grounded specialisation, use only the already
    # selected activity frame, never the full topic/practice block.
    if (
        lesson_type in _GENERIC_LESSON_TYPES
        and practical
        and candidate.frame.clause.strip()
    ):
        selected_activity = _normalize_spaces(candidate.frame.clause)
        lesson_type = type_from_frame(
            candidate.frame,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
            theory_text="",
            practice_text=selected_activity,
            program_content=selected_activity,
            planned_result=result,
        )
    return replace(
        candidate, frame=ActionFrame(candidate.frame.clause, "", "", ""),
        lesson_type=lesson_type, planned_result=result, assessment_method=control,
        warnings=(*candidate.warnings, f"Безопасный шаблон CE2: {issue}."),
    )


def fill_from_source(
    *,
    topic_title: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int = 0,
    practice_appearance_count: int = 0,
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
        practice_appearance_count=practice_appearance_count,
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


def _week_parts_are_independent_topics(parts: tuple[WeekTopicPart, ...]) -> bool:
    """True when the week holds more than one topic identity."""

    return len({(part.topic_number, part.topic_title) for part in parts}) > 1


def _merge_part_results(results: list[str]) -> str:
    if any("по теме „" in item for item in results):
        return " ".join(dict.fromkeys(item for item in results if item))
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


_REVEAL_SAFE_OBJECT_HEADS = ("роль", "значение", "назначение", "особенности")


def _characterize_object_allows_reveal(text: str) -> bool:
    """True when «Раскрывает» keeps the same knowledge sense as «Характеризует»."""

    folded = _drop_leading_verb(_normalize_spaces(text)).rstrip(" .").casefold()
    for head in _REVEAL_SAFE_OBJECT_HEADS:
        if folded == head or folded.startswith(f"{head} ") or folded.startswith(f"{head},"):
            return True
    return False


def _vary_repeated_independent_characterize(sentences: list[str]) -> list[str]:
    """Do not rejoin independent Характеризует phrases; vary only a safe second object."""

    characterize_at = [
        index
        for index, item in enumerate(sentences)
        if _leading_finite_verb(item).casefold() == "характеризует"
    ]
    if len(characterize_at) < 2:
        return sentences
    second = characterize_at[1]
    if not _characterize_object_allows_reveal(sentences[second]):
        return sentences
    rewritten = list(sentences)
    rewritten[second] = re.sub(
        r"(?i)^характеризует\b",
        "Раскрывает",
        rewritten[second],
        count=1,
    )
    return rewritten


def _merge_independent_part_results(results: list[str]) -> str:
    """Keep each topic's RESULT as its own sentence. Do not fold same verbs."""

    sentences: list[str] = []
    seen: set[str] = set()
    for item in results:
        text = _normalize_spaces(item).rstrip()
        if not text:
            continue
        if not text.endswith("."):
            text += "."
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        sentences.append(text)
    sentences = _vary_repeated_independent_characterize(sentences)
    return _normalize_spaces(" ".join(sentences))


def _oral_tail_has_finite_dative(tail: str) -> bool:
    first = _normalize_spaces(tail).split()[:1]
    if not first:
        return False
    token = _finite_token(first[0])
    if _is_action_finite_token(token):
        return True
    return bool(token.endswith("у") and _is_action_finite_token(token[:-1]))


def _sanitize_oral_control(control: str) -> str:
    """Drop oral conjuncts that dativized a leftover action finite."""

    prefix = "устный опрос по "
    if not control.startswith(prefix):
        return control
    rest = control[len(prefix) :]
    chunks = re.split(r"\s+и\s+", rest)
    kept = [chunk for chunk in chunks if not _oral_tail_has_finite_dative(chunk)]
    if not kept:
        return ""
    return prefix + " и ".join(kept)


def _merge_part_controls(controls: list[str]) -> str:
    if any("по теме „" in item for item in controls):
        return "; ".join(dict.fromkeys(item for item in controls if item))
    unique = [
        item
        for item in (_sanitize_oral_control(control) for control in _unique_phrases(controls))
        if item
    ]
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    folded = [item.casefold() for item in unique]
    for prefix in _SHARED_CONTROL_PREFIXES:
        if all(item.startswith(prefix) for item in folded):
            tails = [
                item[len(prefix) :].strip()
                for item in unique
                if prefix != "устный опрос по "
                or not _oral_tail_has_finite_dative(item[len(prefix) :].strip())
            ]
            if not tails:
                continue
            return prefix + _join_and(_unique_phrases(tails))
    return "; ".join(unique)


def _merge_independent_part_controls(controls: list[str]) -> str:
    """Keep each topic's CONTROL as its own clause. Do not fold shared prefixes."""

    unique: list[str] = []
    seen: set[str] = set()
    for control in controls:
        item = _sanitize_oral_control(_normalize_spaces(control))
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return "; ".join(unique)


_GENERIC_LESSON_TYPES = {
    "теоретическое занятие",
    "практическое занятие",
    "комбинированное занятие",
}


def _mixed_week_lesson_type(
    parts: tuple[WeekTopicPart, ...],
    derived_parts: list[ContentEngineV2Result],
) -> str:
    """Compose a mixed-week TYPE from aligned, already grounded part types."""

    part_types = [item.lesson_type for item in derived_parts if item.lesson_type]
    unique_types = list(dict.fromkeys(part_types))

    # A single evidenced special form can describe the whole integrated week.
    if len(unique_types) == 1 and unique_types[0] not in _GENERIC_LESSON_TYPES:
        return unique_types[0]

    practice_types = list(
        dict.fromkeys(
            item.lesson_type
            for part, item in zip(parts, derived_parts)
            if part.practice_hours and item.lesson_type
        )
    )
    if len(practice_types) != 1:
        return "теоретико-практическое занятие"

    practice_type = practice_types[0]
    if practice_type == "практическое занятие":
        return "теоретико-практическое занятие"
    if practice_type in {"теоретическое занятие", "комбинированное занятие"}:
        return "теоретико-практическое занятие"
    # A confirmed practical/special form is more useful than an artificial
    # compound label and still describes the integrated mixed-hours lesson.
    return practice_type


def _aggregate_week_lesson_type(
    parts: tuple[WeekTopicPart, ...],
    derived_parts: list[ContentEngineV2Result],
    *,
    theory_text: str,
    practice_text: str,
) -> str:
    """Classify the complete week instead of selecting one part by position."""

    types = list(dict.fromkeys(
        item.lesson_type for item in derived_parts if item.lesson_type
    ))
    candidate = types[0] if len(types) == 1 else ""
    theory_hours = sum(part.theory_hours for part in parts)
    practice_hours = sum(part.practice_hours for part in parts)
    independent = _week_parts_are_independent_topics(parts)
    differing_forms = independent and len(types) > 1

    if theory_hours and not practice_hours:
        candidate = "теоретическое занятие"

    elif practice_hours and not theory_hours:
        if differing_forms:
            candidate = "практическое занятие"
        elif candidate and candidate not in _GENERIC_LESSON_TYPES:
            pass
        else:
            candidate = "практическое занятие"

    elif theory_hours and practice_hours:
        if not (theory_text.strip() and practice_text.strip()):
            logger.info(
                "CE2 type ambiguity: mixed hours without both row-local sources"
            )
            if differing_forms:
                candidate = "теоретико-практическое занятие"
            elif (
                len(derived_parts) == 1
                and derived_parts[0].lesson_type != "комбинированное занятие"
            ):
                candidate = derived_parts[0].lesson_type
            else:
                candidate = "теоретико-практическое занятие"
        elif differing_forms:
            candidate = "теоретико-практическое занятие"
        else:
            candidate = _mixed_week_lesson_type(parts, derived_parts)

    else:
        candidate = candidate or (derived_parts[0].lesson_type if derived_parts else "")

    return finalize_lesson_type(
        candidate,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )


def _merge_week_part_fields(
    parts: tuple[WeekTopicPart, ...],
    derived_parts: list[ContentEngineV2Result],
    *,
    theory_text: str,
    practice_text: str,
) -> tuple[str, str, str]:
    lesson_type = _aggregate_week_lesson_type(
        parts,
        derived_parts,
        theory_text=theory_text,
        practice_text=practice_text,
    )
    results = [item.planned_result for item in derived_parts]
    controls = [item.assessment_method for item in derived_parts]
    if _week_parts_are_independent_topics(parts):
        planned_result = _merge_independent_part_results(results)
        assessment = _merge_independent_part_controls(controls)
    else:
        planned_result = _merge_part_results(results)
        assessment = _merge_part_controls(controls)
    return lesson_type, planned_result, assessment


def _derive_week_part(
    part: WeekTopicPart,
    topic_totals: dict[tuple[str | None, str, str], tuple[int, int]],
    occurrence_index: int,
    practice_appearance_count: int = 0,
) -> ContentEngineV2Result:
    theory_text, practice_text = _part_texts(part, topic_totals)
    return derive_fields_v2(
        topic_title=_weekly_source_topic(part),
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=part.program_content_full or "",
        theory_hours=part.theory_hours,
        practice_hours=part.practice_hours,
        occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )


def _weekly_source_topic(part: WeekTopicPart | CalendarContentRow) -> str:
    source_topic = (part.program_topic or "").strip()
    if (
        source_topic
        and part.topic_title.strip().casefold() == part.section.strip().casefold()
    ):
        return source_topic
    return part.topic_title


def _practice_appearance_counts(
    rows: tuple[CalendarContentRow, ...],
) -> dict[tuple[str | None, str, str, str], int]:
    counts: dict[tuple[str | None, str, str, str], int] = {}
    for row in rows:
        for part in _row_week_parts(row):
            if part.practice_hours <= 0:
                continue
            key = _content_occurrence_key(part)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _content_occurrence_key(
    part: WeekTopicPart | CalendarContentRow,
) -> tuple[str | None, str, str, str]:
    """Keep an already assigned weekly source block independent of its section."""

    return (
        part.topic_number,
        part.topic_title,
        part.section,
        (part.program_topic or "").strip(),
    )


def build_lesson_content_v2(
    rows: tuple[CalendarContentRow, ...],
) -> tuple[LessonContentV2Row, ...]:
    """Построить поля 2.0 по календарным строкам.

    Pipeline вызывает только при внутреннем флаге USE_CONTENT_ENGINE_V2.
    """

    result: list[LessonContentV2Row] = []
    practice_counts = _practice_appearance_counts(rows)
    practice_occurrences: dict[tuple[str | None, str, str, str], int] = {}
    for row in rows:
        parts = _row_week_parts(row)
        theory_text, practice_text, warnings = _split_row_texts(row)
        if len(parts) > 1:
            derived_parts: list[ContentEngineV2Result] = []
            for part in parts:
                key = _content_occurrence_key(part)
                occurrence_index = 0
                count = practice_counts.get(key, 0)
                if part.practice_hours:
                    occurrence_index = practice_occurrences.get(key, 0)
                    practice_occurrences[key] = occurrence_index + 1
                derived_parts.append(
                    _derive_week_part(
                        part,
                        _topic_hour_totals(parts),
                        occurrence_index,
                        count if part.practice_hours else 0,
                    )
                )
            lesson_type, planned_result, assessment = _merge_week_part_fields(
                parts,
                derived_parts,
                theory_text=theory_text,
                practice_text=practice_text,
            )
            derived = derived_parts[0]
            extra_warnings = tuple(
                warning for item in derived_parts for warning in item.warnings
            )
        else:
            key = _content_occurrence_key(row)
            occurrence_index = 0
            count = practice_counts.get(key, 0)
            if row.practice_hours:
                occurrence_index = practice_occurrences.get(key, 0)
                practice_occurrences[key] = occurrence_index + 1
            derived = derive_fields_v2(
                topic_title=_weekly_source_topic(row),
                theory_text=theory_text,
                practice_text=practice_text,
                program_content=row.program_content_full or "",
                theory_hours=row.theory_hours,
                practice_hours=row.practice_hours,
                occurrence_index=occurrence_index,
                practice_appearance_count=count if row.practice_hours else 0,
            )
            lesson_type = _aggregate_week_lesson_type(
                parts,
                [derived],
                theory_text=theory_text,
                practice_text=practice_text,
            )
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
