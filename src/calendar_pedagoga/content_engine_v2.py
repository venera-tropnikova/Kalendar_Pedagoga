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
from calendar_pedagoga.matching import MatchStatus
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
    refine_selected_activity_type,
)
from calendar_pedagoga.practice_slots import (
    SLOT_CONTINUE_WARNING,
    SLOT_PACK_WARNING,
    ambiguous_colon_object_catalog,
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


PROVENANCE_GENERIC_ONLY = "GENERIC_ONLY"
PROVENANCE_SENTENCE_FRAME_CLOSED = "SENTENCE_FRAME_CLOSED"
PROVENANCE_UTP_TOPIC_DERIVED = "UTP_TOPIC_DERIVED"
PROVENANCE_UNINFORMATIVE_TOPIC_TITLE = "UNINFORMATIVE_TOPIC_TITLE"


def _stamp_generic_only(codes: tuple[str, ...] | list[str] = ()) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*codes, PROVENANCE_GENERIC_ONLY)))


def _drop_generic_only(codes: tuple[str, ...] | list[str] = ()) -> tuple[str, ...]:
    return tuple(code for code in codes if code != PROVENANCE_GENERIC_ONLY)


def _stamp_sentence_frame_closed(
    codes: tuple[str, ...] | list[str] = (),
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*codes, PROVENANCE_SENTENCE_FRAME_CLOSED)))


def _stamp_utp_topic_derived(
    codes: tuple[str, ...] | list[str] = (),
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*codes, PROVENANCE_UTP_TOPIC_DERIVED)))


def _stamp_uninformative_topic_title(
    codes: tuple[str, ...] | list[str] = (),
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*codes, PROVENANCE_UNINFORMATIVE_TOPIC_TITLE)))


def is_sentence_frame_closed_row(row: "LessonContentV2Row") -> bool:
    return PROVENANCE_SENTENCE_FRAME_CLOSED in row.provenance_codes


def is_utp_topic_derived_row(row: "LessonContentV2Row") -> bool:
    return PROVENANCE_UTP_TOPIC_DERIVED in row.provenance_codes


_GENERIC_FALLBACK_PROVEN_MATCH = frozenset(
    {
        MatchStatus.EXACT,
        MatchStatus.NORMALIZED,
        MatchStatus.TEXT_MATCH,
        MatchStatus.USER_CONFIRMED,
    }
)
_GENERIC_MODE_THEORY = "theory"
_GENERIC_MODE_PRACTICE = "practice"
_GENERIC_MODE_MIXED = "mixed"


@dataclass(frozen=True)
class GenericLessonFrame:
    """Closed RESULT/CONTROL frame from topic + hours, not from finished strings."""

    topic: str
    mode: str

    @property
    def action(self) -> str:
        if self.mode == _GENERIC_MODE_PRACTICE:
            return "выполняет"
        if self.mode == _GENERIC_MODE_MIXED:
            return "характеризует и выполняет"
        return "характеризует"

    @property
    def object(self) -> str:
        topic = self.topic
        if self.mode == _GENERIC_MODE_PRACTICE:
            return f"практическое задание по теме «{topic}»"
        if self.mode == _GENERIC_MODE_MIXED:
            return f"содержание темы «{topic}» и практическое задание"
        return f"содержание темы «{topic}»"


def _positive_workload(value: object) -> bool:
    if value is None:
        return False
    try:
        return value > 0
    except TypeError:
        return False


def generic_lesson_mode(*, theory_hours: object, practice_hours: object) -> str:
    theory = _positive_workload(theory_hours)
    practice = _positive_workload(practice_hours)
    if theory and practice:
        return _GENERIC_MODE_MIXED
    if practice:
        return _GENERIC_MODE_PRACTICE
    return _GENERIC_MODE_THEORY


def generic_lesson_fallback_frame(
    topic_title: str,
    *,
    theory_hours: object,
    practice_hours: object,
) -> GenericLessonFrame:
    topic = _normalize_spaces(topic_title).strip(" .")
    return GenericLessonFrame(
        topic=topic,
        mode=generic_lesson_mode(
            theory_hours=theory_hours, practice_hours=practice_hours
        ),
    )


def generic_lesson_fields_from_frame(frame: GenericLessonFrame) -> tuple[str, str]:
    topic = frame.topic
    if frame.mode == _GENERIC_MODE_PRACTICE:
        return (
            f"Выполняет практическое задание по теме «{topic}».",
            (
                "Педагогическое наблюдение за выполнением практического "
                f"задания по теме «{topic}»."
            ),
        )
    if frame.mode == _GENERIC_MODE_MIXED:
        return (
            (
                f"Характеризует содержание темы «{topic}» и выполняет "
                "практическое задание по этой теме."
            ),
            (
                "Устный опрос и педагогическое наблюдение за выполнением "
                f"практического задания по теме «{topic}»."
            ),
        )
    return (
        f"Характеризует содержание темы «{topic}».",
        f"Устный опрос по теме «{topic}».",
    )


def is_generic_lesson_fallback_pair(
    topic_title: str,
    *,
    theory_hours: object,
    practice_hours: object,
    planned_result: str,
    assessment_method: str,
) -> bool:
    expected_result, expected_control = generic_lesson_fields_from_frame(
        generic_lesson_fallback_frame(
            topic_title, theory_hours=theory_hours, practice_hours=practice_hours
        )
    )
    return (
        (planned_result or "").strip() == expected_result
        and (assessment_method or "").strip() == expected_control
    )


def is_generic_lesson_fallback_row(row: "LessonContentV2Row") -> bool:
    if PROVENANCE_GENERIC_ONLY not in row.provenance_codes:
        return False
    topic = _weekly_source_topic(row.source)
    return is_generic_lesson_fallback_pair(
        topic,
        theory_hours=row.source.theory_hours,
        practice_hours=row.source.practice_hours,
        planned_result=row.planned_result,
        assessment_method=row.assessment_method,
    )


def _generic_fallback_source_present(
    theory_text: str, practice_text: str, program_content: str
) -> bool:
    return bool(
        (theory_text or "").strip()
        or (practice_text or "").strip()
        or (program_content or "").strip()
    )


def _part_has_confirmed_source(part: WeekTopicPart) -> bool:
    if part.match_status not in _GENERIC_FALLBACK_PROVEN_MATCH:
        return False
    if (part.program_content_full or "").strip():
        return True
    return bool(part.weekly_content_assigned)


def calendar_row_has_confirmed_source(
    row: CalendarContentRow,
    *,
    theory_text: str = "",
    practice_text: str = "",
) -> bool:
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
            program_content_full=row.program_content_full or "",
            weekly_content_assigned=False,
        ),
    )
    bearing = tuple(
        part
        for part in parts
        if _positive_workload(part.theory_hours)
        or _positive_workload(part.practice_hours)
    )
    if not bearing:
        bearing = parts
    if not bearing or not all(_part_has_confirmed_source(part) for part in bearing):
        return False
    return _generic_fallback_source_present(
        theory_text,
        practice_text,
        row.program_content_full or "",
    ) or any(
        (part.program_content_full or "").strip() or part.weekly_content_assigned
        for part in bearing
    )


def _ce2_pair_is_proven(candidate: ContentEngineV2Result) -> bool:
    if not (candidate.planned_result or "").strip():
        return False
    if not (candidate.assessment_method or "").strip():
        return False
    if PROVENANCE_GENERIC_ONLY in candidate.provenance_codes:
        return False
    if any(
        warning.startswith("Безопасный шаблон CE2:") for warning in candidate.warnings
    ):
        return False
    return True


def _maybe_apply_generic_lesson_fallback(
    candidate: ContentEngineV2Result,
    *,
    topic_title: str,
    theory_hours: object,
    practice_hours: object,
    source_confirmed: bool,
) -> ContentEngineV2Result:
    if not source_confirmed:
        return candidate
    if not (
        _positive_workload(theory_hours) or _positive_workload(practice_hours)
    ):
        return candidate
    if _ce2_pair_is_proven(candidate):
        return candidate
    result_empty = not (candidate.planned_result or "").strip()
    control_empty = not (candidate.assessment_method or "").strip()
    if not result_empty and not control_empty:
        return candidate
    if not result_empty and PROVENANCE_GENERIC_ONLY not in candidate.provenance_codes:
        if not any(
            warning.startswith("Безопасный шаблон CE2:")
            for warning in candidate.warnings
        ):
            return candidate
    frame = generic_lesson_fallback_frame(
        topic_title, theory_hours=theory_hours, practice_hours=practice_hours
    )
    planned_result, assessment_method = generic_lesson_fields_from_frame(frame)
    return replace(
        candidate,
        frame=ActionFrame(frame.topic, frame.action, frame.object, ""),
        planned_result=planned_result,
        assessment_method=assessment_method,
        provenance_codes=_stamp_generic_only(candidate.provenance_codes),
        # Generic text is not SOURCE-backed coverage; keep P0 GENERIC_ONLY active.
        clause_coverage=(),
        clause_roles=(),
    )


def _apply_unresolved_confirmed_slot_generic(
    candidate: ContentEngineV2Result,
    part: WeekTopicPart,
) -> ContentEngineV2Result:
    """Keep GenericLessonFrame on an overlay slot that received no SOURCE units."""

    if part.match_status is not MatchStatus.USER_CONFIRMED:
        return candidate
    if not part.weekly_content_assigned:
        return candidate
    if (part.program_content_full or "").strip():
        return candidate
    frame = generic_lesson_fallback_frame(
        _weekly_source_topic(part),
        theory_hours=part.theory_hours,
        practice_hours=part.practice_hours,
    )
    planned_result, assessment_method = generic_lesson_fields_from_frame(frame)
    return replace(
        candidate,
        frame=ActionFrame(frame.topic, frame.action, frame.object, ""),
        planned_result=planned_result,
        assessment_method=assessment_method,
        provenance_codes=_stamp_generic_only(candidate.provenance_codes),
        clause_coverage=(),
        clause_roles=(),
    )


def _apply_user_confirmed_sentence_frames(
    candidate: ContentEngineV2Result,
    row: CalendarContentRow,
    parts: tuple[WeekTopicPart, ...],
) -> ContentEngineV2Result:
    """Replace overlay RESULT/CONTROL with one SentenceFrame render. Auto-path unchanged."""

    from calendar_pedagoga.sentence_frame import (
        frames_by_confirmed_parts,
        part_lacks_source_units,
        render_confirmed_parts,
        row_uses_sentence_frame,
        title_is_informative,
        weekly_source_topic,
        _bearing_parts,
    )

    if not row_uses_sentence_frame(row, parts):
        return candidate
    groups = frames_by_confirmed_parts(row, parts)
    planned_result, assessment_method, week_fallback = render_confirmed_parts(groups)
    if not planned_result.strip() or not assessment_method.strip():
        return candidate
    first = next((frame for group in groups for frame in group), None)
    title_derived = any(
        frame.title_derived for group in groups for frame in group
    )
    uninformative = any(
        part_lacks_source_units(part)
        and not title_is_informative(
            weekly_source_topic(part) or weekly_source_topic(row)
        )
        for part in _bearing_parts(parts)
    )
    codes = candidate.provenance_codes
    if week_fallback and not title_derived and not uninformative:
        codes = _stamp_generic_only(codes)
    else:
        codes = _drop_generic_only(codes)
    if title_derived and not uninformative:
        codes = _stamp_utp_topic_derived(codes)
    if uninformative:
        codes = _stamp_uninformative_topic_title(codes)
    return replace(
        candidate,
        frame=ActionFrame(
            first.source_span if first else candidate.frame.clause,
            first.action if first else candidate.frame.action,
            first.object if first else candidate.frame.object,
            "",
        ),
        planned_result=planned_result,
        assessment_method=assessment_method,
        provenance_codes=_stamp_sentence_frame_closed(codes),
    )


def _row_is_unresolved_confirmed_slot(
    row: CalendarContentRow,
    parts: tuple[WeekTopicPart, ...],
) -> bool:
    bearing = tuple(
        part
        for part in parts
        if _positive_workload(part.theory_hours)
        or _positive_workload(part.practice_hours)
    ) or parts
    if not bearing:
        return False
    return all(
        part.match_status is MatchStatus.USER_CONFIRMED
        and part.weekly_content_assigned
        and not (part.program_content_full or "").strip()
        for part in bearing
    )


def generic_fallback_fields_for_row(row: "LessonContentV2Row") -> tuple[str, str] | None:
    if is_sentence_frame_closed_row(row):
        return None
    if row.source.match_status is not MatchStatus.USER_CONFIRMED:
        parts = row.source.week_parts
        bearing = tuple(
            part
            for part in (parts or ())
            if _positive_workload(part.theory_hours)
            or _positive_workload(part.practice_hours)
        ) or (parts or ())
        if not bearing or any(
            part.match_status is not MatchStatus.USER_CONFIRMED for part in bearing
        ):
            return None
    if not calendar_row_has_confirmed_source(
        row.source, theory_text=row.theory_text, practice_text=row.practice_text
    ):
        return None
    if not (
        _positive_workload(row.source.theory_hours)
        or _positive_workload(row.source.practice_hours)
        or _positive_workload(row.source.total_hours)
    ):
        return None
    frame = generic_lesson_fallback_frame(
        _weekly_source_topic(row.source),
        theory_hours=row.source.theory_hours,
        practice_hours=row.source.practice_hours,
    )
    return generic_lesson_fields_from_frame(frame)


@dataclass(frozen=True)
class ContentEngineV2Result:
    frame: ActionFrame
    lesson_type: str
    planned_result: str
    assessment_method: str
    theory_text: str
    practice_text: str
    warnings: tuple[str, ...] = ()
    type_result: str | None = None
    clause_coverage: tuple[tuple[str, str], ...] = ()
    clause_roles: tuple[tuple[str, str], ...] = ()
    provenance_codes: tuple[str, ...] = ()


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
    clause_coverage: tuple[tuple[str, str], ...] = ()
    clause_roles: tuple[tuple[str, str], ...] = ()
    provenance_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManualContentValidation:
    """Read-only verdict for teacher-entered RESULT/CONTROL."""

    accepted: bool
    row: LessonContentV2Row
    issues: tuple[str, ...] = ()


REQUIRED_ACTION = "REQUIRED_ACTION"
REQUIRED_KNOWLEDGE = "REQUIRED_KNOWLEDGE"
REQUIRED_OBJECT = "REQUIRED_OBJECT"
REQUIRED_CONDITION = "REQUIRED_CONDITION"
CONTEXT = "CONTEXT"
EXAMPLE = "EXAMPLE"
CATALOG = "CATALOG"
METADATA = "METADATA"

_REQUIRED_SOURCE_ROLES = frozenset(
    {
        REQUIRED_ACTION,
        REQUIRED_KNOWLEDGE,
        REQUIRED_OBJECT,
        REQUIRED_CONDITION,
    }
)
_OPTIONAL_SOURCE_ROLES = frozenset({CONTEXT, EXAMPLE, CATALOG, METADATA})
_OPTIONAL_COVERAGE_STATUS = "OPTIONAL"
_VERBAL_NOUN_TO_VERB: dict[str, str] = {
    "анализ": "анализирует",
    "выбор": "выбирает",
    "выполнение": "выполняет",
    "знакомство": "знакомится",
    "изготовление": "изготавливает",
    "измерение": "измеряет",
    "изучение": "изучает",
    "использование": "использует",
    "конструирование": "конструирует",
    "копирование": "копирует",
    "наблюдение": "наблюдает",
    "оказание": "оказывает",
    "определение": "определяет",
    "организация": "организует",
    "ориентирование": "ориентирует",
    "отработка": "отрабатывает",
    "подбор": "подбирает",
    "подготовка": "подготавливает",
    "подгонка": "подгоняет",
    "поддержание": "поддерживает",
    "посещение": "посещает",
    "постановка": "ставит",
    "построение": "строит",
    "приготовление": "готовит",
    "применение": "применяет",
    "проверка": "проверяет",
    "проведение": "проводит",
    "разведение": "разводит",
    "развертывание": "развертывает",
    "разжигание": "разжигает",
    "разработка": "разрабатывает",
    "разучивание": "разучивает",
    "расчет": "рассчитывает",
    "расчёт": "рассчитывает",
    "решение": "решает",
    "рисование": "рисует",
    "создание": "создаёт",
    "сборка": "собирает",
    "свертывание": "свертывает",
    "смешивание": "смешивает",
    "соблюдение": "соблюдает",
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
    "прохождение": "проходит",
    "сбор": "собирает",
    "огибание": "огибает",
    "переноска": "переносит",
    "перенос": "переносит",
    "надевание": "надевает",
    "выпрыгивание": "выпрыгивает",
    "преодоление": "преодолевает",
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
    r"влияние\s+(?:[а-яё-]+\s+){1,6}на\b|"
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
    if _motion_process_lemma(word):
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
    if low.endswith(("ений", "яний", "аний")) and len(word) > 5:
        return word[:-2] + "ия"
    if low.endswith("ствий") and len(word) > 6:
        return word[:-2] + "ия"
    if low.endswith("ций") and len(word) > 5:
        return word[:-1] + "и"
    # Soft gen.pl «линий» → «линии» (not hard «-ей»).
    if low.endswith("ий") and len(word) > 4 and word[-3].casefold() not in "аеёиоуыэюя":
        return word[:-2] + "ии"
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


def _parenthetical_apposition_acc(word: str, *, suffix: str = "") -> str | None:
    """Accusative for a gen.sg paren apposition; None keeps the token as written.

    Single appositions follow the head: «шага (пары шагов)» → «шаг (пару шагов)»,
    «лагеря (бивака)» → «лагерь (бивак)». Comma-separated exemplar lists
    («треугольники, „бабочки“ и т.п.») stay in direct case.
    """

    core = _strip_punct_word(word)[1]
    if not core:
        return None
    # Exemplar catalogues name forms in direct case; do not re-inflect them.
    if "," in suffix or "и т.п" in suffix.casefold() or "и т.д" in suffix.casefold():
        return None
    acc = _noun_gen_to_acc(core)
    if acc == core:
        return None
    return acc


def _noun_nom_to_acc(word: str) -> str:
    low = word.casefold()
    if low.endswith("ия") and len(word) > 3:
        return word[:-2] + "ию"
    if low.endswith("я") and not low.endswith("ия"):
        return word[:-1] + "ю"
    if low.endswith("а"):
        # Neuter/inanimate plural -а (правила, средства, места): accusative
        # equals nominative. Do not invent feminine-looking -у.
        if _neuter_plural_nom_a(low):
            return word
        return word[:-1] + "у"
    return word


def _neuter_plural_nom_a(low: str) -> bool:
    """Nominative plural of a neuter -о/-е noun: accusative repeats nominative."""

    if not low.endswith("а") or len(low) < 5:
        return False
    stem = low[:-1]
    # правило→правила, средство→средства, место→места / дело→дела.
    if stem.endswith(("ств", "ил")):
        return True
    vowels = re.findall(r"(?i)[аеёиоуыэюя]", low)
    if len(vowels) == 2 and re.search(r"(?i)[^аеёиоуыэюя][^аеёиоуыэюя]$", stem):
        return True
    return False


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
    plural = src.endswith(("ов", "ев", "ёв", "ей", "ений", "яний", "аний", "ий", "ок")) or bool(
        re.search(r"(?i)[аеёиоуыэюя][шж]$", src)
    )
    if out.endswith(("ые", "ие", "ки", "ши", "жи", "ии")):
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
            # Soft gen.pl nouns («линий») share the adjective -ий ending. After a
            # gen.pl adjective they are the noun head, not another modifier.
            pending_adjs = pending_cores()
            if (
                pending_adjs
                and re.search(r"(?i)(?:ых|их)$", pending_adjs[-1][2])
                and core.casefold().endswith(("ий", "ений", "яний", "аний"))
                and _noun_gen_to_acc(core) != core
            ):
                pass
            else:
                pending.append(token)
                continue
        if case == "acc" and not colon_list and (not seen_noun or prefix.startswith("(")):
            # Parenthetical exemplar lists («треугольники, …») stay in direct case.
            # A gen.sg apposition («лагеря (бивака)», «шага (пары шагов)») follows
            # the head into the accusative.
            if prefix.startswith("(") and seen_noun:
                if pending:
                    apply_pending(False, "m")
                acc_paren = _parenthetical_apposition_acc(core, suffix=suffix)
                if acc_paren is not None:
                    out.append(f"{prefix}{acc_paren}{suffix}")
                else:
                    out.append(token)
                has_post_head = True
                continue
            acc_core = _noun_gen_to_acc(core)
            pending_adjs = pending_cores()
            # A singular genitive feminine adjective proves that an ambiguous
            # -и head is gen.sg, not nom.pl: «учебной модели» → «учебную модель».
            singular_feminine = bool(
                pending_adjs
                and re.search(r"(?i)(?:ой|ей)$", pending_adjs[-1][2])
            )
            if singular_feminine and core.casefold().endswith("и"):
                if core.casefold().endswith("ии"):
                    acc_core = core[:-2] + "ию"
                elif re.search(r"(?i)[бвгджзйклмнпрстфхцчшщ]и$", core):
                    acc_core = core[:-1] + "ь"
            # Zero-ending gen.pl heads are ambiguous in isolation. A preceding
            # gen.pl adjective proves the plural object frame:
            # «голосовых команд» -> «голосовые команды».
            if (
                acc_core == core
                and pending_adjs
                and re.search(r"(?i)(?:ых|их)$", pending_adjs[-1][2])
                and re.search(r"(?i)[бвгджзйклмнпрстфхцчшщ]$", core)
            ):
                acc_core = core + ("и" if core[-1].casefold() in "гкхжчшщ" else "ы")
            plural, gender = _noun_acc_features(core, acc_core)
            if singular_feminine:
                plural, gender = False, "f"
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
    """Direct object may be inflected; a leading governed PP is copied as-is.

    When a short PP is followed by the verbal-noun patient still in the
    genitive («на кальку участка карты»), that patient becomes accusative
    after the finite verb («на кальку участок карты»).
    """

    text = _normalize_spaces(remainder)
    if not text:
        return "", ""
    tokens = text.split()
    first = _strip_punct_word(tokens[0])[1]
    if _is_preposition(first):
        repaired = _short_pp_genitive_patient_to_acc(tokens)
        return "", repaired if repaired else text
    obj, cond = _split_object_and_conditions(text)
    obj_acc = _inflect_object_phrase(obj, case="acc") if obj else ""
    return obj_acc, cond


def _short_pp_genitive_patient_to_acc(tokens: list[str]) -> str | None:
    """«на кальку участка …» → «на кальку участок …» for a finite RESULT."""

    if len(tokens) < 3:
        return None
    prep = _strip_punct_word(tokens[0])[1]
    gov = _strip_punct_word(tokens[1])[1]
    if not _is_preposition(prep) or not gov:
        return None
    if _is_preposition(gov) or gov.casefold() in {"и", "или"}:
        return None
    patient = " ".join(tokens[2:])
    head = _strip_punct_word(tokens[2])[1]
    if not head or _is_preposition(head):
        return None
    # Proven genitive patient of the source verbal noun, not another PP.
    if not re.search(r"(?i)(?:а|я|ов|ев|ёв|ей|ий)$", head):
        return None
    patient_acc = _inflect_object_phrase(patient, case="acc")
    if not patient_acc or patient_acc.casefold() == patient.casefold():
        return None
    return _normalize_spaces(f"{tokens[0]} {tokens[1]} {patient_acc}")


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


def _is_travel_word(word: str) -> bool:
    """Named travel activity («путешествия»), not the agent «путешественник»."""

    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE).casefold()
    return bool(re.match(r"путешеств(?:ие|ия|ию|ий|ием|иями)?$", core))


def _token_core(token: str) -> str:
    return re.sub(r"^[«(\"]+|[»)\",;:]+$", "", token)


def _is_action_head(word: str) -> bool:
    low = word.casefold()
    lemma = _verbal_noun_lemma(word).casefold()
    nominative = lemma == re.sub(r"[^\wёЁ]", "", word).casefold()
    return bool(
        word
        and (
            _looks_like_verbal_noun(word)
            or _is_exercise_word(word)
            or _is_walk_word(word)
            or _is_leading_form_activity(word)
            or low.startswith("викторин")
            or low.startswith("диктант")
            or low.startswith("соревнован")
            # Bare process nouns («движение по …») start their own segment.
            or (nominative and _is_unconjugated_process_noun(word))
            # Bare setting / directed-action NPs start their own segment so they
            # are not glued as objects of a previous finite verb.
            or low.startswith("имитац")
            or low.startswith("действ")
        )
    )


def _bare_directed_actions_np(text: str) -> str | None:
    """«действия по X» names pupil actions, not a finished RESULT phrase."""

    match = re.match(
        r"(?i)^(?:действия|действие)\s+по\s+(.+)$",
        _normalize_spaces(text).rstrip(" ."),
    )
    return match.group(1).strip() if match else None


def _bare_simulation_setting(text: str) -> str | None:
    """«имитация X» is a practice setting, never a standalone RESULT."""

    match = re.match(
        r"(?i)^имитаци[яию]\s+(.+)$",
        _normalize_spaces(text).rstrip(" ."),
    )
    return match.group(1).strip() if match else None


def _attach_simulation_circumstance(phrase: str, setting: str) -> str:
    """Keep the setting as «при имитации …» on a directed-action RESULT."""

    body = phrase.rstrip(" .")
    if "при имитац" in body.casefold():
        return body
    return _normalize_spaces(f"{body} при имитации {setting}")


def _fuse_simulation_action_neighbors(units: list[str]) -> list[str]:
    """Join bare simulation + directed-action neighbors into one transform unit."""

    settings: list[str] = []
    actions: list[str] = []
    other: list[str] = []
    for unit in units:
        if _bare_simulation_setting(unit) is not None:
            settings.append(unit)
        elif _bare_directed_actions_np(unit) is not None:
            actions.append(unit)
        else:
            other.append(unit)
    if not actions or not settings:
        return units
    return [*other, f"{actions[0]}, {settings[0]}"]


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
        low = core.casefold()
        if low == "мини" or (
            _is_adjective(core) and not _looks_like_verbal_noun(core)
        ):
            look += 1
            continue
        return False
    return False


def _has_explicit_action_catalogue(text: str) -> bool:
    """Colon marks accepted parallel objects, not new independent actions."""
    head, separator, tail = text.partition(":")
    if not separator:
        return False
    head_low = head.casefold().strip()
    catalogue_head = bool(
        re.search(
            r"(?i)\b(?:при[её]м|элемент|техник|способ|действ|упражнен|препятств|офп)\w*\s*$",
            head,
        )
        or re.search(r"(?i)\b(?:круговое\s+)?офп\b", head_low)
        or re.search(
            r"(?i)\b(?:тренировка|преодоление|отработка|выполнение|изучение)\b",
            head_low,
        )
    )
    if not catalogue_head:
        return False
    members = [item.strip() for item in tail.rstrip(".").split(",")]
    if not members or not all(members):
        return False
    # Quoted exemplars after an action head stay in one segment.
    if all(re.fullmatch(r"[«\"].+[»\"]", member) for member in members):
        return True
    return all(
        len(member.split()) <= 8
        and not re.search(r"[.;!?]", member)
        and not _FINITE_VERB_RE.match(member)
        for member in members
    )


def _split_action_segments(text: str) -> list[str]:
    """Режет клаузу только перед новым действием, не внутри объекта."""

    parts: list[str] = []
    stripped = text.strip(" ,")
    if _has_explicit_action_catalogue(stripped):
        return [stripped]
    # Keep «A, B и C + shared object» as one explicit coordinated action.
    if re.match(
        r"(?i)^(?:[А-Яа-яЁё\-]+(?:\s*,\s*[А-Яа-яЁё\-]+)*)\s+и\s+"
        r"[А-Яа-яЁё\-]+\s+\S+",
        stripped,
    ):
        heads_match = re.match(
            r"(?i)^((?:[А-Яа-яЁё\-]+(?:\s*,\s*[А-Яа-яЁё\-]+)*))\s+и\s+"
            r"([А-Яа-яЁё\-]+)\s+",
            stripped,
        )
        if heads_match:
            heads = [item.strip() for item in heads_match.group(1).split(",")] + [
                heads_match.group(2)
            ]
            if len(heads) >= 2 and all(_is_explicit_action_head_token(head) for head in heads):
                return [stripped]
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
            nominative_action = (
                _is_exercise_word(core)
                or _is_leading_form_activity(core)
                or (
                    (
                        _looks_like_verbal_noun(core)
                        or _is_unconjugated_process_noun(core)
                    )
                    and _verbal_noun_lemma(core).casefold()
                    == re.sub(r"[^\wёЁ]", "", core).casefold()
                )
            )
            low_core = core.casefold()
            # Bare simulation / directed-action NPs are new activities even after
            # a PP («…на карте, имитация…, действия по…»).
            setting_or_directed = low_core.startswith(("имитац", "действ"))
            if token.startswith("("):
                pass
            elif in_prepositional and not (
                prev.endswith(",")
                and _starts_new_action(tokens, index)
                and (nominative_action or setting_or_directed)
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
    while rest and not _looks_like_verbal_noun(rest[0]):
        core = re.sub(r"[^\wёЁ]", "", rest[0], flags=re.IGNORECASE).casefold()
        if core == "мини" or (_is_adjective(rest[0]) and not _is_leading_form_activity(rest[0])):
            mods.append(rest.pop(0))
            continue
        break
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
    core = re.sub(r"[^\wёЁ]", "", token, flags=re.IGNORECASE).casefold()
    if core.startswith(("викторин", "диктант", "соревнован", "конкурс")):
        return True
    return False

# Closed nominal-activity frames: the source names an observable activity NP,
# but that lemma has no proven finite conjugation. Map only onto verbs that
# are already in the proven predicate set. Never invent a verb from a suffix.
_NOMINAL_PERFORM_LEMMAS = frozenset({
    "вис",
    "висы",
    "закаливание",
    "катание",
    "лазание",
    "подъем",
    "сдача",
    "спуск",
    "торможение",
    "тренировка",
})
_ACTIVITY_GLOSS_RE = re.compile(r"\s+[–—−]\s+|\s+-\s+")
_PERFORMANCE_QUANTITY_RE = re.compile(
    r"(?i)\(\s*\d+\s*(?:раз(?:а)?|подход(?:а|ов)?|круг(?:а|ов)?)\s*\)"
    r"|\b\d+\s*(?:раз(?:а)?|подход(?:а|ов)?|круг(?:а|ов)?)\b"
)


def _nominal_activity_lemma(word: str) -> str:
    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE).casefold()
    if re.fullmatch(r"помощ[ьи]", core):
        return "помощь"
    if re.fullmatch(r"поездк[аиуеы]|поездок", core):
        return "поездка"
    # Orthographic variants of the same perform lemma (ь/ие).
    if re.fullmatch(r"лазань[еяюи]", core):
        return "лазание"
    if re.fullmatch(r"вис(?:ы|а|у|ом|ах)?", core):
        return "вис"
    motion = _motion_process_lemma(word)
    if motion:
        return motion
    return _verbal_noun_lemma(word).casefold()


def _token_is_pupil_activity(word: str) -> bool:
    """True when a token itself names a pupil perform/process activity."""

    if not word:
        return False
    core = _token_core(word).strip("«»\"„“")
    if not core:
        return False
    lemma = _nominal_activity_lemma(core)
    if lemma in _NOMINAL_PERFORM_LEMMAS:
        return True
    if _is_travel_word(core) or _is_walk_word(core) or _is_exercise_word(core):
        return True
    if _is_leading_form_activity(core) or _participation_lemma(core):
        return True
    if _is_action_head(core):
        return True
    # Physical drill nouns: приседания, отжимания, выпрыгивания…
    if re.match(
        r"(?i)^(?:приседан|отжиман|выпрыг|подтягиван|прыжк|плаваен)\w*$",
        re.sub(r"[^\wёЁ]", "", core),
    ):
        return True
    return False


def _clause_has_performance_quantity(text: str) -> bool:
    return bool(_PERFORMANCE_QUANTITY_RE.search(text or ""))


def _theory_channel_only(*, theory_hours: int, practice_hours: int) -> bool:
    return bool(theory_hours) and not bool(practice_hours)


def _practice_channel_only(*, theory_hours: int, practice_hours: int) -> bool:
    return bool(practice_hours) and not bool(theory_hours)


def _explicit_pupil_perform_clause(text: str) -> bool:
    """Perform evidence stronger than a bare theory verbal noun (-ование/-ение)."""

    cleaned = _normalize_spaces(text)
    if not cleaned:
        return False
    if _CONTROL_RESULT_VERB_RE.search(cleaned):
        return True
    if _clause_has_performance_quantity(cleaned):
        return True
    tokens = [_token_core(token).strip("«»\"„“") for token in cleaned.split()]
    for token in tokens[:8]:
        if not token:
            continue
        lemma = _nominal_activity_lemma(token)
        if lemma in _NOMINAL_PERFORM_LEMMAS:
            return True
        if (
            _is_travel_word(token)
            or _is_walk_word(token)
            or _is_exercise_word(token)
            or _is_leading_form_activity(token)
            or _participation_lemma(token)
        ):
            return True
        if re.match(
            r"(?i)^(?:приседан|отжиман|выпрыг|подтягиван|прыжк)\w*$",
            re.sub(r"[^\wёЁ]", "", token),
        ):
            return True
    if _ACTIVITY_START_RE.match(cleaned):
        return True
    if _locative_drawing_object(cleaned) is not None:
        return True
    if _semiotic_object_phrase(cleaned) is not None:
        return True
    return False


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
    if _remainder_contains_finite_action(remainder):
        return None
    if ":" in remainder and lemma in _NOMINAL_PERFORM_LEMMAS | {"преодоление"}:
        return None
    if _technique_process_lemma(head):
        remainder = _drop_ways_catalogue_tail(remainder)
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
    "диктанте",
    "диктантах",
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
    if core.startswith("диктант"):
        return "диктант"
    if core.startswith("соревнован"):
        return "соревнование"
    if core.startswith("конкурс"):
        return "конкурс"
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
        "диктант": "диктантах",
        "соревнование": "соревнованиях",
        "конкурс": "конкурсах",
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


def _split_coordinating_и_outside_quotes(text: str) -> list[str]:
    """Split on comma / « и » that are not inside «…» or \"…\" quotes."""

    parts: list[str] = []
    buf: list[str] = []
    quote_depth = 0
    index = 0
    while index < len(text):
        ch = text[index]
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
        if quote_depth == 0 and text[index : index + 2] == ", ":
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
            index += 2
            continue
        if quote_depth == 0 and text[index : index + 3].casefold() == " и ":
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


def _participation_object_parts(clause: str) -> tuple[list[str], str]:
    text = _normalize_spaces(clause)
    head, _colon, tail = text.partition(":")
    pieces = _split_coordinating_и_outside_quotes(head)
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

    if _bare_simulation_setting(part) is not None or _bare_directed_actions_np(part) is not None:
        return True
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


_LOCATIVE_DRAWING_RE = re.compile(
    r"(?i)^(?P<object>.+?)\s+в\s+рисунках\s+"
    r"(?:детей|учащихся|учеников)\s*\.?$"
)
_SEMIOTIC_HEAD_RE = re.compile(
    r"(?i)^(знаки?|символы?|эмблем(?:а|ы)?|пиктограмм(?:а|ы)?)$"
)
_PRODUCTIVE_FINITE_RE = re.compile(
    r"(?i)^(рисуют|рисует|изготавлива\w*|созда[её]т|создают|"
    r"разрабатыва\w*)\b"
)
_PRODUCTIVE_LEMMAS = frozenset(
    {"рисование", "изготовление", "создание", "разработка"}
)


def _decap_phrase(text: str) -> str:
    words = _normalize_spaces(text).split()
    if not words:
        return ""
    words[0] = _decap_lexical(words[0])
    return " ".join(words)


def _object_phrase_to_acc(obj: str) -> str:
    cleaned = _normalize_spaces(obj).rstrip(" .")
    if not cleaned:
        return ""
    from calendar_pedagoga.morphology import inflect_heads_only

    inflected = inflect_heads_only(cleaned)
    return _decap_phrase(inflected or cleaned)


def _has_clause_initial_productive_head(text: str) -> bool:
    cleaned = _normalize_spaces(text)
    if not cleaned:
        return False
    if _PRODUCTIVE_FINITE_RE.match(cleaned):
        return True
    _mods, rest = _leading_modifiers(cleaned.split())
    if not rest:
        return False
    head = re.sub(r"^[«(\"]+|[»)\",;:]+$", "", rest[0])
    lemma = _verbal_noun_lemma(head).casefold()
    if lemma in _PRODUCTIVE_LEMMAS:
        return True
    return bool(_CREATIVE_HEAD_RE.match(head) and _conjugate_verbal_noun(head))


def _locative_drawing_object(text: str) -> str | None:
    match = _LOCATIVE_DRAWING_RE.match(_normalize_spaces(text))
    if match is None:
        return None
    prefix = match.group("object").strip(" ,.;")
    if not prefix or _has_clause_initial_productive_head(prefix):
        return None
    return prefix


def _is_semiotic_head(word: str) -> bool:
    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE)
    return bool(_SEMIOTIC_HEAD_RE.match(core))


def _semiotic_head_plural(word: str) -> bool:
    core = re.sub(r"[^\wёЁ]", "", word, flags=re.IGNORECASE).casefold()
    return bool(re.fullmatch(r"(знаки|символы|эмблемы|пиктограммы)", core))


def _semiotic_object_to_genitive(obj: str) -> str:
    """Genitive of a SOURCE semiotic NP; dependents and quotes stay."""

    words = _normalize_spaces(obj).rstrip(" .").split()
    if not words:
        return obj
    from calendar_pedagoga.morphology import parse_head, _restore

    out: list[str] = []
    for word in words:
        _prefix, core, _suffix = _strip_punct_word(word)
        if _is_semiotic_head(core):
            parsed = parse_head(core)
            gram = {"gent"}
            if parsed is not None and parsed.tag.number:
                gram.add(parsed.tag.number)
            inflected = parsed.inflect(gram) if parsed is not None else None
            out.append(_restore(word, inflected.word) if inflected else word)
            continue
        if _is_adjective(core):
            out.append(_adj_to_genitive(word))
            continue
        out.append(word)
    return _normalize_spaces(" ".join(out))


def _semiotic_object_phrase(text: str) -> str | None:
    cleaned = _normalize_spaces(text).rstrip(" .")
    if not cleaned or _has_clause_initial_productive_head(cleaned):
        return None
    if _locative_drawing_object(cleaned) is not None:
        return None
    mods, rest = _leading_modifiers(cleaned.split())
    if not rest:
        return None
    head = re.sub(r"^[«(\"]+|[»)\",;:]+$", "", rest[0])
    if not _is_semiotic_head(head):
        return None
    return _normalize_spaces(" ".join([*mods, *rest]))


def _locative_drawing_result(text: str) -> tuple[str, str, str, str] | None:
    obj = _locative_drawing_object(text)
    if obj is None:
        return None
    acc = _object_phrase_to_acc(obj)
    phrase = _normalize_spaces(f"рисует {acc}")
    return phrase, "рисование", acc, ""


def _semiotic_object_result(text: str) -> tuple[str, str, str, str] | None:
    obj = _semiotic_object_phrase(text)
    if obj is None:
        return None
    acc = _object_phrase_to_acc(obj)
    tokens = acc.split()
    head = next(
        (token for token in reversed(tokens) if _is_semiotic_head(token)),
        tokens[-1] if tokens else "",
    )
    pronoun = "их" if _semiotic_head_plural(head) else "его"
    phrase = _normalize_spaces(f"распознаёт {acc} и объясняет {pronoun} значение")
    return phrase, "распознавание", acc, ""


def _finite_produce_result(text: str) -> tuple[str, str, str, str] | None:
    match = _PRODUCTIVE_FINITE_RE.match(_normalize_spaces(text))
    if match is None:
        return None
    raw = match.group(1).casefold()
    remainder = _normalize_spaces(text)[match.end() :].strip(" ,.")
    if raw.startswith("рису"):
        verb, action = "рисует", "рисование"
    elif raw.startswith("изготавлива"):
        verb, action = "изготавливает", "изготовление"
    elif raw.startswith("созда"):
        verb, action = "создаёт", "создание"
    elif raw.startswith("разрабатыва"):
        verb, action = "разрабатывает", "разработка"
    else:
        return None
    acc = _object_phrase_to_acc(remainder) if remainder else ""
    phrase = _normalize_spaces(f"{verb} {acc}")
    return phrase, action, acc, ""


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


# Path/manner PPs specify how a process is performed, not a knowledge field.
_PATH_MANNER_PREPOSITIONS = frozenset({"по", "вдоль", "через", "вокруг", "на"})


def _remainder_is_path_or_manner_complement(remainder: str) -> bool:
    """Restrictive path/manner PP: «по маршруту», «на тему …», not «о X»."""

    text = _normalize_spaces(remainder)
    if not text or text.startswith(":") or _remainder_is_quoted_label(text):
        return False
    tokens = text.split()
    first = tokens[0].casefold()
    if first not in _PATH_MANNER_PREPOSITIONS:
        return False
    # «на» is productive only for topic/theme complements of a product activity.
    if first == "на" and not re.match(r"(?i)^на\s+тему\b", text):
        return False
    return len(tokens) >= 2 and not _remainder_is_knowledge_np(text)


def _is_unconjugated_process_noun(head: str) -> bool:
    """Deverbal process that can be performed, not a document or knowledge NP."""

    lemma = _verbal_noun_lemma(head).casefold()
    if lemma in _STATE_OR_KNOWLEDGE_LEMMAS or lemma in {"произведение", "заключение"}:
        return False
    if lemma.endswith("ведение") and lemma != "ведение":
        return False
    if _is_exercise_word(head):
        return False
    # Short process/product nouns without a mapped finite verb.
    if lemma in {"смена", "рисунок", "поделка", "аппликация"}:
        return True
    return bool(re.search(r"(?:ание|ение|яние|тие)$", lemma))


def _motion_process_lemma(head: str) -> str:
    """Motion process without a proven finite verb: подъём, спуск."""

    core = re.sub(r"[^\wёЁ]", "", head, flags=re.IGNORECASE).casefold()
    if re.fullmatch(r"подъ?[её]м(?:ы|ов|а|у|е)?", core):
        return "подъем"
    if re.fullmatch(r"спуски?|спуска|спусков|спуске|спуску", core):
        return "спуск"
    return ""


def _technique_catalogue_head(head: str) -> bool:
    core = re.sub(r"[^\wёЁ]", "", head, flags=re.IGNORECASE).casefold()
    return bool(re.fullmatch(r"способ(?:ы|а|ов)?", core))


def _leading_ways_catalogue(member: str) -> bool:
    tokens = _normalize_spaces(member).split()
    return bool(tokens) and _technique_catalogue_head(_strip_punct_word(tokens[0])[1])


def _drop_ways_catalogue_tail(remainder: str) -> str:
    """A catalogue of ways is named, not performed, so it cannot be an object."""

    pieces = re.split(r"(,\s*|\s+и\s+)", _normalize_spaces(remainder))
    if _leading_ways_catalogue(pieces[0]):
        return ""
    kept = pieces[:1]
    for separator, member in zip(pieces[1::2], pieces[2::2]):
        if _leading_ways_catalogue(member):
            break
        kept.extend((separator, member))
    return "".join(kept).strip()


def _remainder_contains_finite_action(remainder: str) -> bool:
    """A later proven finite verb is its own action, not a process complement."""

    if not remainder:
        return False
    if _CONTROL_RESULT_VERB_RE.search(remainder):
        return True
    return any(
        _starts_with_action_finite(part)
        for part in re.split(r"[,:;]", remainder)
        if _normalize_spaces(part)
    )


def _technique_process_lemma(head: str) -> str:
    """Closed motion/technique processes without a proven finite verb."""

    motion = _motion_process_lemma(head)
    if motion:
        return motion
    lemma = _verbal_noun_lemma(head).casefold()
    if lemma in {"торможение", "преодоление"}:
        return lemma
    return ""


def _performed_process_conjuncts(obj: str) -> list[str]:
    """Coordinated members of an object that name a closed-class process."""

    conjuncts = []
    for member in re.split(r",\s*|\s+и\s+", _normalize_spaces(obj)):
        tokens = member.split()
        if tokens and _technique_process_lemma(_strip_punct_word(tokens[0])[1]):
            conjuncts.append(member.strip())
    return conjuncts


def _remainder_is_knowledge_np(remainder: str) -> bool:
    first = remainder.split()[0].casefold() if remainder.split() else ""
    if first in _KNOWLEDGE_PP_STARTS:
        return True
    tokens = remainder.split()
    _mods, rest = _leading_modifiers(tokens)
    if not rest:
        return False
    head = _strip_punct_word(rest[0])[1]
    low = head.casefold()
    if _is_theory_knowledge_token(head):
        return True
    if low.endswith(("ений", "аний", "яний")) and len(low) > 5:
        nom_pl = low[:-4] + ("ения" if low.endswith("ений") else "ания" if low.endswith("аний") else "яния")
        return nom_pl in _THEORY_KNOWLEDGE_HEADS or _verbal_noun_lemma(nom_pl).casefold() in _THEORY_KNOWLEDGE_HEADS
    return False


def _practice_activity_np_object(mods: list[str], head: str, remainder: str) -> str:
    acc = _proven_feminine_acc(head)
    if acc is None and head.casefold().endswith("а") and not _neuter_plural_nom_a(
        head.casefold()
    ):
        acc = _noun_nom_to_acc(head)
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
    if _conjugate_verbal_noun(head):
        return None
    if _participation_lemma(head) == "занятие":
        return None
    if ":" in remainder or _remainder_contains_finite_action(remainder):
        return None
    if _technique_process_lemma(head):
        remainder = _drop_ways_catalogue_tail(remainder)
    overcoming = (
        _technique_process_lemma(head) == "преодоление"
        and _remainder_is_dependent_object(remainder)
        and not _remainder_is_knowledge_np(remainder)
    )
    if not remainder.strip():
        return None
    if (
        not overcoming
        and (_remainder_is_quoted_label(remainder) or _remainder_is_knowledge_np(remainder))
    ):
        return None
    has_object = _remainder_is_dependent_object(remainder)
    has_path = _remainder_is_path_or_manner_complement(remainder)
    if not overcoming and not has_object and not has_path:
        return None
    lemma = _verbal_noun_lemma(head).casefold()
    deverbal_ka = bool(re.search(r"(?i)(?:тка|дка|нка|вка|жка|зка)$", lemma))
    # A -ка suffix is only a candidate filter, not activity evidence.
    # A path PP is activity evidence for an unconjugated process noun;
    # a genitive object after an unknown -ение noun is not, except the
    # closed technique process «преодоление + object».
    if overcoming:
        pass
    elif _has_stem(head, _PERFORM_STEMS) or deverbal_ka:
        if not has_object:
            return None
    elif not (has_path and _is_unconjugated_process_noun(head)):
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


def _r13_must_abstain_action_reconstruction(text: str) -> bool:
    """R13 safety BEFORE positive action reconstruction.

    Prohibition, action-scoped negation, alternative OR, bare condition, or an
    unclear non-student executor must abstain rather than invent a positive RESULT.
    """

    cleaned = _normalize_spaces(text)
    if not cleaned:
        return False
    if _prohibition_only_source(cleaned):
        return True
    low = cleaned.casefold()
    # Predicate/modal forms prohibit an action.  Attributive participles such
    # as «запрещающий знак» merely name an object/category and must not trigger
    # the R13 abstention guard.
    if re.search(
        r"(?i)\b(?:запрещ(?:ается|[её]н(?:а|о|ы)?)|нельзя|"
        r"не\s+допускается|не\s+разрешается)\b",
        low,
    ):
        return True
    if re.search(
        r"(?i)\bне\s+(?:выполнять|выполняет|проводить|использовать|применять|"
        r"начинать|открывать|трогать|лазать)\b",
        low,
    ):
        return True
    if re.search(r"(?i)\bне\s+[а-яё]+йте(?:сь)?\b", low):
        return True
    # Alternative between explicit actions / verbal nouns.
    if re.search(
        r"(?i)\b(?:выполнение|изучение|отработка|тренировка|использование|"
        r"фасовка|упаковка|переноска)\b"
        r".{0,80}\bили\b.{0,80}\b"
        r"(?:выполнение|изучение|отработка|тренировка|использование|"
        r"фасовка|упаковка|переноска|[а-яё]+ни[ея]|[а-яё]+ка)\b",
        low,
    ):
        return True
    # Condition-only fragment (no governing explicit action head).
    if re.match(
        r"(?i)^(?:если|при\s+условии|в\s+случае|без\b(?!\s+ошибок))",
        low,
    ) and not _VERBAL_NOUN_FIND_RE.search(cleaned):
        return True
    # Unclear non-student executor as the clause subject.
    if re.match(r"(?i)^(?:педагог|учитель|инструктор|тренер)\b", low) and not re.search(
        r"(?i)\b(?:ученик|ученица|учащ|обуча|реб[её]нок|дети)\b",
        low,
    ):
        return True
    return False


_EXPLICIT_ACTION_HEADS = frozenset(
    {
        "выполнение",
        "изучение",
        "знакомство",
        "отработка",
        "тренировка",
        "преодоление",
        "подготовка",
        "использование",
        "надевание",
        "фасовка",
        "упаковка",
        "переноска",
        "перенос",
        "составление",
        "измерение",
        "оценка",
        "сбор",
        "разработка",
        "проведение",
        "формирование",
        "огибание",
        "постановка",
    }
)
_DRILL_ACTIVITY_RE = re.compile(
    r"(?i)^(?:приседан|отжиман|выпрыг|подтягиван|прыжк|плаван|"
    r"вис(?:ы|а|у|ом|ах)?|планка|складочк|стульчик)\w*$"
)


def _is_explicit_action_head_token(word: str) -> bool:
    core = _token_core(word)
    if not core:
        return False
    lemma = _verbal_noun_lemma(core).casefold()
    if lemma in _EXPLICIT_ACTION_HEADS or core.casefold() in _EXPLICIT_ACTION_HEADS:
        return True
    if _conjugate_verbal_noun(core):
        return True
    if _nominal_activity_lemma(core) in _NOMINAL_PERFORM_LEMMAS:
        return True
    return False


def _is_drill_activity_token(word: str) -> bool:
    core = re.sub(r"[^\wёЁ]", "", _token_core(word), flags=re.IGNORECASE).casefold()
    return bool(_DRILL_ACTIVITY_RE.fullmatch(core))


def _strip_parenthetical_examples(text: str) -> tuple[str, str]:
    """Keep governing action text; return (main, example_tail)."""

    cleaned = _normalize_spaces(text)
    if ":" not in cleaned:
        return cleaned, ""
    head, sep, tail = cleaned.partition(":")
    members = [item.strip() for item in re.split(r"\s*,\s*", tail.strip(" .")) if item.strip()]
    if members and all(
        re.fullmatch(r"[«\"].+[»\"]", member) or member.startswith("«") for member in members
    ):
        return head.strip(), tail.strip()
    return cleaned, ""


def _conjugate_explicit_action_head(head: str) -> str | None:
    verb = _conjugate_verbal_noun(head)
    if verb:
        return verb
    lemma = _verbal_noun_lemma(head).casefold()
    if lemma == "тренировка" or _nominal_activity_lemma(head) == "тренировка":
        return "отрабатывает"
    if lemma in _NOMINAL_PERFORM_LEMMAS:
        return None
    return None


def _reconstruct_coordinated_explicit_actions(
    text: str,
) -> tuple[str, str, str, str] | None:
    """A, B и C + shared object → finite verbs for each explicit action head."""

    cleaned = _normalize_spaces(text).strip(" ,")
    if not cleaned or re.search(r"(?i)\s+или\s+", cleaned):
        return None
    match = re.match(
        r"(?i)^((?:[А-Яа-яЁё\-]+(?:\s*,\s*[А-Яа-яЁё\-]+)*))\s+и\s+"
        r"([А-Яа-яЁё\-]+)\s+(.+)$",
        cleaned,
    )
    if not match:
        return None
    left_blob, last_head, remainder = match.group(1), match.group(2), match.group(3)
    heads = [item.strip() for item in left_blob.split(",")] + [last_head]
    if len(heads) < 2 or not all(_is_explicit_action_head_token(head) for head in heads):
        return None
    verbs: list[str] = []
    for head in heads:
        verb = _conjugate_explicit_action_head(head)
        if not verb:
            return None
        verbs.append(verb)
    obj_acc, cond = _complements_after_finite(remainder)
    phrase = ", ".join(verbs[:-1]) + " и " + verbs[-1] if len(verbs) > 2 else " и ".join(verbs)
    if obj_acc:
        phrase += f" {obj_acc}"
    if cond:
        phrase += f" {cond}"
    obj, split_cond = _split_object_and_conditions(remainder)
    return (
        _normalize_spaces(phrase),
        " и ".join(heads),
        obj,
        split_cond or cond,
    )


def _reconstruct_action_object_list(text: str) -> tuple[str, str, str, str] | None:
    """Explicit action head + object / object list (optional colon catalogue)."""

    cleaned = _normalize_spaces(text).strip(" ,")
    if not cleaned:
        return None
    main, example_tail = _strip_parenthetical_examples(cleaned)
    tokens = main.split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return None
    head, remainder = _head_core_and_remainder(rest)
    lemma = _verbal_noun_lemma(head).casefold()
    nom = _nominal_activity_lemma(head)
    ofp = bool(re.fullmatch(r"(?i)офп", head)) or "офп" in main.casefold() and ":" in cleaned

    if ofp or (re.match(r"(?i)^(?:круговое\s+)?офп\b", cleaned) and ":" in cleaned):
        # Circuit / ОФП catalogue: keep the labelled activity and dosage,
        # never expand colon members into RESULT.
        label, _, tail = cleaned.partition(":")
        np_words = [_decap_lexical(tok) for tok in label.split()]
        dosages = _dosage_markers(tail)
        unique_dosages = list(
            dict.fromkeys(_normalize_dosage_marker(item) for item in dosages)
        )
        if len(unique_dosages) > 1:
            phrase = _append_remainder(
                "выполняет " + " ".join(np_words), ": " + tail.strip()
            )
            obj, cond = _split_object_and_conditions(tail.strip())
            return phrase, "офп", obj, cond
        phrase = "выполняет " + " ".join(np_words)
        if unique_dosages:
            phrase = _normalize_spaces(phrase + " " + unique_dosages[0])
        return phrase, "офп", "", unique_dosages[0] if unique_dosages else ""

    if not (
        lemma in _EXPLICIT_ACTION_HEADS
        or nom in _NOMINAL_PERFORM_LEMMAS
        or _conjugate_verbal_noun(head)
    ):
        return None

    # Drop colon only when it introduces exemplars; keep object catalogues.
    work_remainder = remainder
    if example_tail and ":" in cleaned:
        work_remainder = remainder.split(":", 1)[0].strip() if ":" in remainder else remainder

    verb = _conjugate_explicit_action_head(head)
    if verb is None and nom in _NOMINAL_PERFORM_LEMMAS and nom != "тренировка":
        # Fall through to nominal layer.
        return None
    if verb is None:
        return None

    if lemma == "изучение" and re.search(
        r"(?i)\b(?:комплекс\w*\s+упражнен|упражнен|техник|выполнен|при[её]м|"
        r"страхов|самострах|лазани|движен)\w*",
        work_remainder,
    ):
        verb = "отрабатывает"

    if mods and mods[0].casefold().endswith("ое"):
        verb = "практически " + verb

    obj_acc, cond = _complements_after_finite(work_remainder)
    phrase = verb
    if obj_acc:
        phrase += f" {obj_acc}"
    if cond:
        phrase += f" {cond}"
    # Retain non-example object catalogue after colon (преодоление: A, B).
    if ":" in remainder and not example_tail:
        after = remainder.split(":", 1)[1].strip()
        if after and after.casefold() not in phrase.casefold():
            phrase = _append_remainder(phrase, ": " + after)
    obj, split_cond = _split_object_and_conditions(work_remainder)
    return _normalize_spaces(phrase), lemma or head.casefold(), obj, split_cond or cond


def _reconstruct_explicit_drill_np(text: str) -> tuple[str, str, str, str] | None:
    """Adjective* + drill noun (+ quantity) → выполняет + source NP."""

    cleaned = _normalize_spaces(text).strip(" ,")
    if not cleaned or ":" in cleaned:
        return None
    tokens = cleaned.split()
    if not tokens:
        return None
    # Collect leading adjectives, then a drill head.
    mods: list[str] = []
    rest = list(tokens)
    while rest and _is_adjective(rest[0]) and not _is_drill_activity_token(rest[0]):
        mods.append(rest.pop(0))
    if not rest or not _is_drill_activity_token(rest[0]):
        return None
    head = rest[0]
    remainder = " ".join(rest[1:]).strip()
    # Require quantity or a short drill NP — never bare topic nouns.
    if remainder and not (
        _clause_has_performance_quantity(cleaned)
        or re.match(r"(?i)^\(", remainder)
        or _is_preposition(remainder.split()[0])
    ):
        # Object/PP after drill is allowed; reject long foreign content.
        if len(remainder.split()) > 6:
            return None
    np_words = [_decap_lexical(mod) for mod in mods] + [
        _decap_lexical(_noun_nom_to_acc(head) if not head.casefold().endswith(("я", "и", "ы")) else head)
    ]
    phrase = _append_remainder("выполняет " + " ".join(np_words), remainder)
    obj, cond = _split_object_and_conditions(remainder)
    return phrase, head.casefold(), obj, cond


def _reconstruct_unconjugated_process_with_object(
    text: str,
) -> tuple[str, str, str, str] | None:
    """Explicit process VN + object/path that lacks a mapped finite verb."""

    cleaned = _normalize_spaces(text).strip(" ,")
    if not cleaned or ":" in cleaned:
        return None
    tokens = cleaned.split()
    mods, rest = _leading_modifiers(tokens)
    if not rest:
        return None
    head, remainder = _head_core_and_remainder(rest)
    if not remainder.strip():
        return None
    if _conjugate_verbal_noun(head):
        return None
    lemma = _verbal_noun_lemma(head).casefold()
    if (
        _is_theory_knowledge_token(head)
        or lemma in _THEORY_KNOWLEDGE_HEADS
        or lemma in _STATE_OR_KNOWLEDGE_LEMMAS
        or _clause_is_knowledge_content(cleaned)
    ):
        return None
    if not _is_unconjugated_process_noun(head) and not _is_explicit_action_head_token(head):
        return None
    if not (
        _remainder_is_dependent_object(remainder)
        or _remainder_is_path_or_manner_complement(remainder)
    ):
        return None
    if _remainder_is_knowledge_np(remainder) or _remainder_is_quoted_label(remainder):
        return None
    obj_np = _practice_activity_np_object(mods, head, remainder)
    obj, cond = _split_object_and_conditions(remainder)
    return (
        _normalize_spaces("выполняет " + obj_np),
        head.casefold(),
        obj,
        cond,
    )


def _explicit_action_reconstruction(
    text: str,
    *,
    theory_only: bool,
) -> tuple[str, str, str, str] | None:
    """Universal EXPLICIT ACTION RECONSTRUCTION for practice SOURCE clauses.

    Covers: verbal noun → finite, coordinated actions, shared head + objects,
    action + object list. Does not invent actions for bare topic nouns or theory.
    """

    if theory_only:
        return None
    cleaned = _normalize_spaces(text).strip(" ,")
    if not cleaned:
        return None
    if _r13_must_abstain_action_reconstruction(cleaned):
        return None

    coordinated = _reconstruct_coordinated_explicit_actions(cleaned)
    if coordinated:
        return coordinated
    listed = _reconstruct_action_object_list(cleaned)
    if listed:
        return listed
    drill = _reconstruct_explicit_drill_np(cleaned)
    if drill:
        return drill
    process = _reconstruct_unconjugated_process_with_object(cleaned)
    if process:
        return process
    return None


def _is_shared_object_action_head(word: str) -> bool:
    """True when the token names a proven pupil action, finite or nominal."""

    if _looks_like_verbal_noun(word):
        return True
    return _nominal_activity_lemma(word) in _NOMINAL_PERFORM_LEMMAS


def _legacy_paired_verbal_shape(word: str) -> bool:
    """Former paired-verb morphology: deverbal -ние / -ка only."""

    return bool(re.fullmatch(r"(?i)(?:[А-Яа-яЁё]+ние|[А-Яа-яЁё]+ка)", word))


def _shared_object_after_paired_verbs(segment: str) -> tuple[str, str, str] | None:
    """Two coordinated actions that share one trailing object stay both kept.

    Finite -ние/-ка pairs («изучение и отработка X») conjugate as before. When
    either side is only a proven nominal activity (for example «ремонт и сдача
    инвентаря»), both nouns are kept under «выполняет» so the middle action of
    a week chain cannot silently disappear.
    """

    match = re.match(
        r"(?i)^([А-Яа-яЁё\-]+)\s+и\s+([А-Яа-яЁё\-]+)\s+(.+)$",
        _normalize_spaces(segment).strip(),
    )
    if not match:
        return None
    first, second, remainder = match.group(1), match.group(2), match.group(3)
    verb1 = _conjugate_verbal_noun(first)
    verb2 = _conjugate_verbal_noun(second)
    if (
        verb1
        and verb2
        and _legacy_paired_verbal_shape(first)
        and _legacy_paired_verbal_shape(second)
    ):
        obj_acc, cond = _complements_after_finite(remainder)
        phrase = f"{verb1} и {verb2}"
        if obj_acc:
            phrase += f" {obj_acc}"
        if cond:
            phrase += f" {cond}"
        obj, _split_cond = _split_object_and_conditions(remainder)
        return (
            _normalize_spaces(phrase),
            f"{first} и {second}",
            _normalize_spaces(f"{obj} {cond}"),
        )

    nom1 = _nominal_activity_lemma(first) in _NOMINAL_PERFORM_LEMMAS
    nom2 = _nominal_activity_lemma(second) in _NOMINAL_PERFORM_LEMMAS
    if not (nom1 or nom2):
        return None
    if not (
        _is_shared_object_action_head(first) and _is_shared_object_action_head(second)
    ):
        return None
    # Keep the source complement (often genitive under the nouns) verbatim.
    acc1 = _decap_lexical(_noun_nom_to_acc(first))
    acc2 = _decap_lexical(_noun_nom_to_acc(second))
    phrase = _normalize_spaces(f"выполняет {acc1} и {acc2} {remainder}")
    obj, cond = _split_object_and_conditions(remainder)
    return (
        phrase,
        f"{first} и {second}",
        _normalize_spaces(f"{obj} {cond}".strip()),
    )


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
    if _r13_must_abstain_action_reconstruction(text):
        return "", "", "", ""
    if _is_non_student_process(text):
        return _characterize(text)

    care = _care_and_repair_result(text)
    if care:
        phrase, action, obj = care
        return phrase, action, obj, ""

    knowledge = _knowledge_clause_result(text, theory_only=theory_only)
    if knowledge:
        return knowledge

    if not theory_only:
        reconstructed = _explicit_action_reconstruction(text, theory_only=theory_only)
        if reconstructed:
            return reconstructed
        produced = _finite_produce_result(text)
        if produced:
            return produced
        drawing = _locative_drawing_result(text)
        if drawing:
            return drawing
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

    travel_head = _is_travel_word(head) and not theory_only
    if _is_walk_word(head) or travel_head:
        walk_words: list[str] = []
        idx = 0
        while idx < len(rest) and (
            _is_walk_word(re.sub(r"[^\wёЁ]", "", rest[idx]))
            or _is_travel_word(re.sub(r"[^\wёЁ]", "", rest[idx]))
            or rest[idx].casefold() in {"и", "или"}
        ):
            walk_words.append(rest[idx])
            idx += 1
        remainder = " ".join(rest[idx:]).strip()
        # Bare «путешествия» in theory/lists is knowledge, not a pupil trip.
        # A path PP («по карте») proves the map-travel practice activity.
        if travel_head and not _is_walk_word(head) and not re.match(
            r"(?i)^по\b", remainder
        ):
            pass
        else:
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
            head.casefold() in _KNOWLEDGE_NOUNS
            or _coordinated_theory_activity(text)
            or _verbal_noun_lemma(head).casefold() == "выбор"
        ):
            named = _name_kinds(text)
            if named:
                return named
            return _characterize(text)
        verb = _conjugate_verbal_noun(head)
        if verb:
            remainder = _keep_proven_action_complements(" ".join(rest[1:]).strip())
            if verb == "соблюдает" and re.match(
                r"(?i)^(?:правил|норм|требован|положен)\w*",
                remainder,
            ):
                # Observance of rules/norms is knowledge content, not a drill.
                named = _name_kinds(text)
                if named:
                    return named
                return _characterize(text)
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
        semiotic = _semiotic_object_result(text)
        if semiotic:
            return semiotic
        unconjugated = _unconjugated_practice_activity_result(text)
        if unconjugated:
            return unconjugated

    if not theory_only:
        directed = _bare_directed_actions_np(text)
        if directed is not None:
            phrase = _normalize_spaces(f"выполняет действия по {directed}")
            return phrase, "действия", directed, ""
        if _bare_simulation_setting(text) is not None:
            # A simulation is the setting of another action, not an outcome.
            return "", "", "", ""

    if theory_only or head.casefold() in _KNOWLEDGE_NOUNS:
        if _knowledge_label_over_catalogue(text):
            # BARE LIST: a label above an enumeration is not an action; keep
            # the source untouched and let the clause stay for review.
            return text, "", "", ""
        named = _name_kinds(text)
        if named:
            return named
        return _characterize(text)
    # Practice «способы + process NP with a dependent object» (methods of
    # disinfecting water) is knowledge of methods. A ways label whose process
    # only takes a path PP («способы передвижения на лыжах») stays unconverted
    # so neighbouring technique drills remain the performed RESULT.
    if not theory_only and _leading_ways_catalogue(text) and ":" not in text:
        tokens = _normalize_spaces(text).split()
        if (
            len(tokens) >= 3
            and _looks_like_verbal_noun(tokens[1])
            and not _is_preposition(tokens[2])
        ):
            return _characterize(text)
    return text, "", "", ""


def _knowledge_label_over_catalogue(text: str) -> bool:
    """Knowledge label plus a colon enumeration: no action of the pupil."""

    head, separator, tail = _normalize_spaces(text).partition(":")
    if not separator:
        return False
    tokens = head.split()
    if not tokens or _token_core(tokens[0]).casefold() not in _KNOWLEDGE_NOUNS:
        return False
    members = [item.strip() for item in tail.rstrip(" .").split(",")]
    return len(members) > 1 and all(
        member
        and len(member.split()) <= 5
        and not re.search(r"[.;:!?]", member)
        and not _FINITE_VERB_RE.match(member)
        for member in members
    )


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
    if _neuter_plural_nom_a(core):
        return True
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
    leading = tokens[:start]
    safe_leading_modifiers = bool(leading) and all(
        _is_adjective(token)
        or _strip_punct_word(token)[1].casefold() in {"и", "или"}
        for token in leading
    )
    if safe_leading_modifiers:
        heads = [*(_decap_lexical(token) for token in leading), *heads]
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


def _knowledge_head_has_only_leading_modifiers(text: str) -> bool:
    """A knowledge head preceded only by its agreeing modifier series."""

    tokens = _normalize_spaces(text).strip(" .").split()
    index = next(
        (position for position, token in enumerate(tokens) if _is_theory_knowledge_token(token)),
        None,
    )
    if index is None or index == 0:
        return False
    return all(
        _is_adjective(token)
        or _strip_punct_word(token)[1].casefold() in {"и", "или"}
        for token in tokens[:index]
    )


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
    # Frame nouns («выбор / определение X»): characterize the content X.
    first_lemma = _verbal_noun_lemma(tokens[0]).casefold()
    if first_lemma in {"выбор", "определение", "анализ", "описание"} and len(tokens) >= 2:
        rest = " ".join(tokens[1:])
        obj_acc, cond = _complements_after_finite(rest)
        if obj_acc:
            return _normalize_spaces(f"{obj_acc} {cond}".strip())
        nested = _proven_theory_object(rest)
        if nested:
            return nested
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


def _nominal_knowledge_subject_number(text: str) -> str:
    """Return a proven agreement number for a copied theory subject.

    The reconstruction deliberately keeps the SOURCE NP in the nominative and
    proves only the copular number.  Unknown plural heads are accepted only in
    the structural ``plural head + genitive owner`` shape; otherwise CE2
    abstains instead of guessing case, animacy, or agreement.
    """

    subject = _normalize_spaces(text).strip(" .")
    if (
        not subject
        or any(mark in subject for mark in ":;?!")
        or not _balanced_fold_object(subject)
        or _r13_must_abstain_action_reconstruction(subject)
    ):
        return ""
    tokens = subject.split()
    if not tokens or any(_is_proven_finite_token(token) for token in tokens):
        return ""

    knowledge_index = next(
        (index for index, token in enumerate(tokens) if _is_theory_knowledge_token(token)),
        None,
    )
    if knowledge_index is not None:
        head = _strip_punct_word(tokens[knowledge_index])[1].casefold()
        modifiers = tokens[:knowledge_index]
        if modifiers and not all(
            _is_adjective(token)
            or _strip_punct_word(token)[1].casefold() in {"и", "или"}
            for token in modifiers
        ):
            return ""
        agreeing = [
            _strip_punct_word(token)[1].casefold()
            for token in modifiers
            if _is_adjective(token)
        ]
        if agreeing:
            last = agreeing[-1]
            if last.endswith(("ые", "ие")):
                return "plural"
            if last.endswith(("ое", "ее", "ая", "яя")):
                return "singular"
        if _neuter_plural_nom_a(head) or head.endswith("ы"):
            return "plural"
        if head.endswith(("ющие", "щие")):
            return "plural"
        if head.endswith(("а", "я", "и")):
            # Without an agreeing modifier these forms can be plural
            # nominative or singular genitive; do not guess the copular number.
            return ""
        return "singular"

    # Generic nominal knowledge shape, e.g. a plural abstract heading with a
    # genitive dependent.  Requiring the dependent keeps bare labels and
    # arbitrary noun lists outside the reconstruction.
    head = _strip_punct_word(tokens[0])[1].casefold()
    owner = _strip_punct_word(tokens[1])[1].casefold() if len(tokens) > 1 else ""
    plural_head = bool(head) and head.endswith(("ы", "и")) and not head.endswith(
        ("ие", "ии")
    )
    genitive_owner = bool(
        re.search(
            r"(?i)(?:ов|ев|ёв|ей|ий|ствий|ений|аний|яний|ок)$",
            owner,
        )
    )
    if plural_head and genitive_owner and not _looks_like_verbal_noun(head):
        return "plural"
    return ""


def _split_source_list(text: str) -> list[str]:
    return [
        part.strip(" .")
        for part in re.split(r",\s*|\s+и\s+", _normalize_spaces(text))
        if part.strip(" .")
    ]


def _quote_source_term(term: str) -> str:
    return f"«{term.strip(' .«»\"„“')}»"


def _join_quoted_source_terms(terms: list[str], source_tail: str) -> str:
    quoted = [_quote_source_term(term) for term in terms]
    if re.search(r"(?i)\s+и\s+", source_tail):
        return _join_and(quoted)
    return ", ".join(quoted)


def _np_to_genitive(text: str) -> str:
    """Genitive of a SOURCE NP via proven morphology only; otherwise abstain."""

    tokens = _normalize_spaces(text).split()
    if not tokens:
        return ""
    from calendar_pedagoga.morphology import parse_head, parse_nominal, _restore

    out: list[str] = []
    for token in tokens:
        core = _strip_punct_word(token)[1]
        if not core:
            return ""
        if core.casefold() in {"и", "или"}:
            out.append(token)
            continue
        parsed = parse_head(core) or parse_nominal(core)
        if parsed is None:
            return ""
        gram = {"gent"}
        if parsed.tag.number:
            gram.add(parsed.tag.number)
        inflected = parsed.inflect(gram)
        if inflected is None:
            return ""
        word = _restore(token, inflected.word)
        # «цветы» is the -ы plural; pymorphy maps it onto the -ки lexeme
        # (цветков). Keep the SOURCE plural type: -ы → -ов.
        src_core = core.casefold()
        inf_core = _strip_punct_word(word)[1].casefold()
        if src_core.endswith("ы") and inf_core.endswith("ков") and inf_core[:-3] == src_core[:-1]:
            word = _restore(token, core[:-1] + "ов")
        out.append(word)
    return _normalize_spaces(" ".join(out))


def _source_list_to_genitive(text: str) -> str:
    items = _split_source_list(text)
    if len(items) < 1:
        return ""
    gens = [_np_to_genitive(item) for item in items]
    if not all(gens):
        return ""
    return _join_and(gens)


def _noun_to_instrumental(word: str) -> str:
    from calendar_pedagoga.morphology import parse_head, _restore

    core = _strip_punct_word(word)[1]
    if not core:
        return ""
    parsed = parse_head(core)
    if parsed is None:
        return ""
    gram = {"ablt"}
    if parsed.tag.number:
        gram.add(parsed.tag.number)
    inflected = parsed.inflect(gram)
    if inflected is None:
        return ""
    return _restore(word, inflected.word)


def _concept_values_clause_result(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _normalize_spaces(text).strip(" .")
    match = re.fullmatch(r"(?i)поняти[ея]\s*:\s*(.+)", cleaned)
    if match is None:
        return None
    raw = match.group(1).strip(" .")
    terms = _split_source_list(raw)
    if len(terms) < 2:
        return None
    if any(_FINITE_VERB_RE.search(term) or ":" in term for term in terms):
        return None
    joined = _join_quoted_source_terms(terms, raw)
    obj = f"значения понятий {joined}"
    return f"объясняет {obj}", "объясняет", obj, ""


def _purpose_clause_result(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _normalize_spaces(text).strip()
    paren = re.search(r"(?i)\(\s*для\s+чего\s+нужн[аоые]+\s+(.+?)\s*\)", cleaned)
    standalone = re.fullmatch(
        r"(?i)для\s+чего\s+нужн[аоые]+\s+(.+?)\s*\??",
        cleaned.strip(" ."),
    )
    body = paren.group(1) if paren is not None else standalone.group(1) if standalone else ""
    body = _normalize_spaces(body).strip(" .")
    if not body:
        return None
    if _FINITE_VERB_RE.search(body) or ":" in body:
        return None
    gen = _source_list_to_genitive(body)
    if not gen:
        return None
    obj = f"функции {gen}"
    return f"объясняет {obj}", "объясняет", obj, ""


def _classification_head_blocked(head: str) -> bool:
    first = _token_core(head.split()[0]).casefold() if head.split() else ""
    if first in _KNOWLEDGE_NOUNS or first in {"понятия", "значения", "сведения"}:
        return True
    if _has_clause_initial_productive_head(head):
        return True
    for token in head.split():
        if token.casefold() in {"и", "или"}:
            continue
        if (
            _is_leading_form_activity(token)
            or _looks_like_verbal_noun(token)
            or _is_exercise_word(token)
            or _is_walk_word(token)
            or _is_travel_word(token)
        ):
            return True
    return False


def _classification_clause_result(
    text: str,
    *,
    theory_only: bool,
) -> tuple[str, str, str, str] | None:
    cleaned = _normalize_spaces(text).strip(" .")
    if ":" not in cleaned:
        return None
    head, tail = cleaned.split(":", 1)
    head, tail = head.strip(), tail.strip()
    if not head or not tail or _classification_head_blocked(head):
        return None
    cats = _split_source_list(tail)
    if len(cats) != 2:
        return None
    if "," in tail or not re.search(r"(?i)\s+и\s+", tail):
        return None
    if re.search(r"(?i)\b(?:другие|прочие|др\.|т\.?\s*п\.?)\b", tail):
        return None
    if any(_FINITE_VERB_RE.search(cat) or ":" in cat for cat in cats):
        return None
    all_adj = all(
        all(
            _is_adjective(token) or token.casefold() in {"и", "или"}
            for token in cat.split()
        )
        for cat in cats
    )
    if not all_adj:
        return None
    objects = _decap_phrase(head)
    obj = f"{tail} {objects}"
    return f"различает {obj}", "различает", obj, ""


def _symbol_clause_result(text: str) -> tuple[str, str, str, str] | None:
    cleaned = _normalize_spaces(text).strip(" .")
    match = re.fullmatch(
        r"(?i)(.+?)\s*[—–−-]\s*символ(?:ом)?\s+(.+)",
        cleaned,
    )
    if match is None:
        return None
    subject = _decap_phrase(match.group(1).strip())
    owner = _normalize_spaces(match.group(2)).strip(" .")
    if (
        not subject
        or not owner
        or ":" in subject
        or len(subject.split()) > 6
        or _has_clause_initial_productive_head(subject)
        or any(_is_proven_finite_token(token) for token in owner.split())
    ):
        return None
    inst = _noun_to_instrumental("символ")
    if inst.casefold() != "символом":
        return None
    obj = f"{subject} {inst} {owner}"
    return f"называет {obj}", "называет", obj, ""


def _knowledge_clause_result(
    text: str,
    *,
    theory_only: bool,
) -> tuple[str, str, str, str] | None:
    """Clause-level knowledge frames. Each SOURCE clause yields at most one."""

    cleaned = _normalize_spaces(text)
    if not cleaned:
        return None
    for builder in (
        _concept_values_clause_result,
        _purpose_clause_result,
        _symbol_clause_result,
    ):
        built = builder(cleaned)
        if built:
            return built
    return _classification_clause_result(cleaned, theory_only=theory_only)


def _knowledge_result_shape(result: str) -> str:
    obj = _drop_leading_verb(_normalize_spaces(result)).rstrip(" .").casefold()
    if obj.startswith("значения понятий"):
        return "concept_values"
    if obj.startswith("функции "):
        return "functions"
    if obj.startswith("что такое"):
        return "what_is"
    if re.match(r"(?i)в\s+ч[её]м\s+состоя", obj):
        return "consists"
    return _leading_finite_verb(result).casefold() or obj[:24]


def _knowledge_control_label(verb: str, obj: str) -> str:
    phrase = _normalize_spaces(obj).strip(" .")
    concepts = re.fullmatch(r"(?i)значения\s+понятий\s+(.+)", phrase)
    if concepts:
        return "понятия " + concepts.group(1)
    functions = re.fullmatch(r"(?i)функции\s+(.+)", phrase)
    if functions:
        return "функции " + functions.group(1)
    symbol = re.fullmatch(r"(?i)(.+?)\s+символом\s+(.+)", phrase)
    if verb == "называет" and symbol:
        return f"{symbol.group(1)} как символ {symbol.group(2)}"
    if verb == "различает" and phrase:
        return phrase
    return ""


def _declared_knowledge_control(result: str) -> str:
    """Oral CONTROL labels taken only from confirmed knowledge RESULT frames."""

    labels: list[str] = []
    for sentence in _result_sentences(result):
        verb = _leading_finite_verb(sentence).casefold()
        if verb not in _KNOWLEDGE_RESULT_VERBS:
            continue
        obj = _drop_leading_verb(sentence).rstrip(".")
        label = _knowledge_control_label(verb, obj)
        if not label:
            return ""
        labels.append(label)
    if not labels:
        return ""
    return _cap_sentence("устный опрос: " + "; ".join(labels))


def _theory_knowledge_reconstruction(
    text: str,
) -> tuple[str, str, str, str] | None:
    """Build a finite theory RESULT without changing the SOURCE object's case."""

    source = _normalize_spaces(text).strip()
    structured = _knowledge_clause_result(source, theory_only=True)
    if structured is not None:
        return structured
    question = re.fullmatch(r"(?i)что\s+такое\s+([^?!:;]+?)\s*\?", source)
    if question is not None:
        subject = _normalize_spaces(question.group(1)).strip(" .")
        if (
            subject
            and _balanced_fold_object(subject)
            and not _r13_must_abstain_action_reconstruction(subject)
            and not any(_is_proven_finite_token(token) for token in subject.split())
        ):
            subject = _decap_lexical(subject)
            phrase = _normalize_spaces(f"объясняет, что такое {subject}")
            return phrase, "объясняет", subject, ""
        return None

    tokens = source.strip(" .").split()
    if any(_is_theory_knowledge_token(token) for token in tokens):
        # Existing closed knowledge heads keep their established characterize
        # path.  This constructor is only for the interrogative frame above and
        # for nominal subjects that have no proven direct-object realization.
        return None

    existing, _action, _obj, _conditions = _characterize(source)
    if existing and not _result_grammar_issue(_cap_sentence(existing)):
        return None

    subject = source.strip(" .")
    number = _nominal_knowledge_subject_number(subject)
    if not number:
        return None
    subject = _decap_lexical(subject)
    copula = "состоят" if number == "plural" else "состоит"
    phrase = _normalize_spaces(f"объясняет, в чём {copula} {subject}")
    return phrase, "объясняет", subject, ""


def _explained_knowledge_subject(text: str) -> str:
    """Extract the SOURCE NP copied by the supported ``объясняет`` frames."""

    normalized = _normalize_spaces(text).strip().rstrip(".")
    normalized = re.sub(r"(?i)^объясняет\s*,?\s*", "", normalized, count=1)
    question = re.fullmatch(r"(?i)что\s+такое\s+(.+)", normalized)
    if question is not None:
        return _normalize_spaces(question.group(1)).strip(" .")
    nominal = re.fullmatch(r"(?i)в\s+ч[её]м\s+(состоит|состоят)\s+(.+)", normalized)
    if nominal is not None:
        subject = _normalize_spaces(nominal.group(2)).strip(" .")
        number = _nominal_knowledge_subject_number(subject)
        expected = "состоят" if number == "plural" else "состоит" if number == "singular" else ""
        return subject if expected and nominal.group(1).casefold() == expected else ""
    values = re.fullmatch(r"(?i)значения\s+понятий\s+(.+)", normalized)
    if values is not None:
        return _normalize_spaces(values.group(1)).strip(" .")
    functions = re.fullmatch(r"(?i)функции\s+(.+)", normalized)
    if functions is not None:
        return _normalize_spaces(functions.group(1)).strip(" .")
    return ""


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
    r"(?i)^[А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют|ает|яет)(?:ся|сь)?\b"
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
    verb_re = re.compile(r"(?i)^([А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют)(?:ся|сь)?)\s+")
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


# Endings that alone prove which cases a form can realise. A tail is judged
# against the member it would continue, so an ending outside this table proves
# nothing and the tail stays a separate list item.
_FORM_CASES: tuple[tuple[str, frozenset[str]], ...] = (
    (r"(?:ах|ях)$", frozenset({"prep.pl"})),
    (r"(?:ами|ями)$", frozenset({"ins.pl"})),
    (r"(?:ам|ям)$", frozenset({"dat.pl"})),
    (r"(?:ом|ем|ём|ью)$", frozenset({"ins.sg"})),
    (r"(?:ов|ев|ёв)$", frozenset({"gen.pl"})),
    (r"(?:ния|тия|ствия)$", frozenset({"gen.sg", "nom.pl", "acc.pl"})),
    (
        r"(?:[бвгджзклмнпрстфхцчшщ]и|ы)$",
        frozenset({"gen.sg", "nom.pl", "acc.pl"}),
    ),
    (r"(?:о|ё|е)$", frozenset({"nom.sg", "acc.sg"})),
)
_OBLIQUE_CASES = frozenset({"gen.sg", "gen.pl", "dat.pl", "ins.pl", "ins.sg", "prep.pl"})


def _proven_form_cases(word: str) -> frozenset[str]:
    """Cases the ending can realise; empty when the ending proves none."""

    low = _strip_punct_word(word)[1].casefold()
    if len(low) < 4 or _is_preposition(low):
        return frozenset()
    for pattern, cases in _FORM_CASES:
        if re.search(pattern, low):
            return cases
    return frozenset()


def _continues_prepositional_group(kept: str, tail: str) -> bool:
    """Tail is a further member of a prepositional group still open before it.

    A named activity after a comma is its own list item, so a tail is only a
    continuation when it starts no clause and no group of its own and its form
    can still be an oblique case that the open group governs. A form that can
    only be nominative names a new activity, and an unproven form proves
    nothing, so both leave the tail dropped.
    """

    tokens = _normalize_spaces(kept).split()
    tail_tokens = _normalize_spaces(tail).split()
    if not tokens or not tail_tokens:
        return False
    if _is_preposition(tail_tokens[0]) or _FINITE_VERB_RE.search(tail):
        return False
    opened = max(
        (index for index, token in enumerate(tokens) if _is_preposition(token)),
        default=-1,
    )
    if opened < 0 or opened == len(tokens) - 1:
        return False
    if _is_adjective(tokens[-1]):
        return False
    tail_cases = _proven_form_cases(tail_tokens[0]) & _OBLIQUE_CASES
    if not tail_cases:
        return False
    # Compare with any governed member after the preposition, not only the last
    # token: «с описанием ориентиров, составлением …» keeps the instrumental
    # series even when a genitive complement sits between the heads.
    for member in reversed(tokens[opened + 1 :]):
        if _is_preposition(member):
            continue
        member_oblique = _proven_form_cases(member) & _OBLIQUE_CASES
        # «описанием» shares the adjective -ем ending; oblique proof wins.
        if _is_adjective(member) and not member_oblique:
            continue
        if member_oblique & tail_cases:
            return True
    return False


def _drop_raw_list_tails(text: str) -> str:
    if _has_explicit_action_catalogue(text):
        return text
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
        if _bare_simulation_setting(part) is not None or _bare_directed_actions_np(part) is not None:
            # Settings and directed-action NPs are other activities, not tails.
            continue
        if _looks_like_verbal_noun(first) or re.search(
            r"(?i)(?:нию|тию|анию|ению)$", first
        ):
            if _continues_prepositional_group(kept[-1], part):
                kept.append(part)
            continue
        if first[:1].isupper() and not _RESULT_FINITE_RE.match(part):
            if not re.match(r"(?i)^(?:совершает|посещает)\s+", parts[0]):
                prev = kept[-1]
                # Capitalized mid-list members of an open «по …» series
                # («по Солнцу, Луне, Полярной звезде») are still complements.
                in_po_series = bool(re.match(r"(?i)^по\s+\S", prev)) or (
                    prev[:1].isupper()
                    and any(re.match(r"(?i)^по\s+\S", item) for item in kept)
                )
                if (
                    in_po_series
                    and not _is_action_head(first)
                    and not _looks_like_verbal_noun(first)
                    and len(part.split()) <= 3
                ):
                    kept.append(part)
                    continue
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
    # One exercise wrapper must not erase other independently proven actions
    # («знакомится…», «участвует в играх», «проходит…»).
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


def _join_finite_result_phrases(phrases: list[str]) -> str:
    """Same finite verb stays comma-joined; a new verb starts a new sentence
    only when an object-listing verb would otherwise absorb another action.
    """

    if not phrases:
        return ""
    # Verbs whose objects are long enumerations: a following different finite
    # must not be comma-glued into that list («определяет …, выполняет …»).
    object_list_verbs = frozenset(
        {
            "определяет",
            "измеряет",
            "характеризует",
            "называет",
            "раскрывает",
        }
    )
    sentences: list[str] = []
    current_verb = ""
    bucket: list[str] = []

    def flush() -> None:
        nonlocal bucket, current_verb
        if not bucket:
            return
        body = ", ".join(item.rstrip(" .") for item in bucket)
        sentences.append(_cap_sentence(body))
        bucket, current_verb = [], ""

    for phrase in phrases:
        cleaned = _normalize_spaces(phrase).rstrip(" .")
        if not cleaned:
            continue
        verb = _leading_finite_verb(cleaned).casefold()
        if bucket and verb and current_verb and verb != current_verb:
            if current_verb in object_list_verbs or verb in object_list_verbs:
                flush()
        if not bucket:
            current_verb = verb
        bucket.append(cleaned)
        if verb:
            current_verb = verb
    flush()
    return " ".join(sentences)


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
    if _r13_must_abstain_action_reconstruction(source_clause):
        return "", ActionFrame(source_clause, "", "", "")

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
    simulation_settings: list[str] = []
    for unit in _clause_units(source_clause) or [source_clause]:
        if theory_only and _is_interrogative_clause(unit):
            continue
        main = re.sub(r"\(([^()]*)\)", _paren, unit)
        for segment in _split_action_segments(_normalize_spaces(main)):
            setting = _bare_simulation_setting(segment)
            if setting is not None:
                simulation_settings.append(setting)
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
    if simulation_settings:
        for index, phrase in enumerate(phrases):
            if phrase.casefold().startswith("выполняет действия по"):
                phrases[index] = _attach_simulation_circumstance(
                    phrase, simulation_settings[0]
                )
                break
    phrases = _keep_strongest_phrase(phrases)
    result = _join_finite_result_phrases(phrases)
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
    # Agglutinated «Играна …» is the game form «Игра на …» (spacing OCR artifact).
    normalized = re.sub(
        r"(?i)^играна\b",
        "Игра на",
        _normalize_spaces(clause),
        count=1,
    )
    if theory_only:
        reconstructed = _theory_knowledge_reconstruction(normalized)
        if reconstructed is not None:
            phrase, action, obj, conditions = reconstructed
            return _cap_sentence(phrase), ActionFrame(
                normalized,
                action,
                obj,
                conditions,
            )
    try:
        return _transform_clause_candidate(
            normalized, theory_only=theory_only, full_source=full_source, topic_title=topic_title,
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
    if _locative_drawing_object(clause) is not None:
        return 3
    if _semiotic_object_phrase(clause) is not None:
        return 2
    if _has_stem(lead, _PRODUCE_STEMS + ("ориентир", "измерен")):
        return 3
    if re.match(r"(?i)^(определен|изучен|знакомств|поняти|значен)", first):
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
    # Clothing/footwear complements only attach to clothing/equipment selected.
    if "одежд" in low or "обув" in low:
        return any(
            stem in selected_low
            for stem in ("одежд", "обув", "экипир", "снаряжен", "уклад", "рюкзак")
        )
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

    def add(nidx: int, neighbor: str, *, practice: bool = False, join: bool = True) -> None:
        if neighbor in used or _is_non_student_process(neighbor):
            return
        if join:
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
                    # Bare simulation / directed-action NPs follow as their own
                    # RESULT units; joining them into the selected clause would
                    # glue a second finite verb into the first verb's objects.
                    bare = (
                        _bare_simulation_setting(part) is not None
                        or _bare_directed_actions_np(part) is not None
                    )
                    add(nidx, part, practice=True, join=not bare)
                break
    for nidx, neighbor in enumerate(units):
        if neighbor in used:
            continue
        if not _is_obligatory_neighbor(selected, neighbor):
            continue
        for part in _focus_complement_parts(neighbor):
            add(nidx, part, practice=True)
    extra_texts = complementary
    if not extras and not complementary:
        return selected, []
    if not extras:
        return selected, extra_texts
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
    "создаёт": "создания",
    "создает": "создания",
    "разрабатывает": "разработки",
    "собирает": "сборки",
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


_KNOWLEDGE_RESULT_VERBS = frozenset({"характеризует", "называет", "объясняет", "различает"})
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
    explained = _explained_knowledge_subject(phrase)
    if explained:
        # The subordinate RESULT deliberately preserves SOURCE nominative.
        # Keep that exact accepted object under a fixed grammatical control
        # frame instead of guessing dative government inside its dependants.
        return f"вопросу «{explained}»"
    topic = re.match(r"(?i)^материал по теме\s+[„\"«](.+?)[“\"»]$", phrase)
    if topic:
        return f"теме „{topic.group(1)}“"
    first = phrase.split()[0]
    if _is_proven_finite_token(first):
        phrase = _normalize_spaces(phrase[len(first) :]).strip(" ,.;")
        if not phrase or _starts_with_action_finite(phrase):
            return ""
    phrase_tokens = phrase.split()
    knowledge_index = next(
        (
            index
            for index, token in enumerate(phrase_tokens)
            if _is_theory_knowledge_token(token)
        ),
        None,
    )
    if (
        knowledge_index is not None
        and _knowledge_head_has_only_leading_modifiers(phrase)
        and _knowledge_owner_tokens(phrase_tokens[knowledge_index + 1 :])
    ):
        # The conjunction belongs to a genitive owner (``значение A и B``),
        # not to two parallel direct objects.  Keep the accepted NP verbatim
        # under a fixed grammatical frame instead of re-inflecting its owner.
        return f"вопросу «{phrase}»"
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
    skill_result = _rebuild_skill_result(segments)
    declared = _declared_knowledge_control(result)
    if declared and not skill_result:
        return declared
    oral = declared.rstrip(".") if declared else _oral_from_knowledge_objects(knowledge)
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
    text = result or ""
    if not re.match(r"(?i)участвует в .*(викторин|диктант)", text):
        return False
    # A RESULT that also keeps independent drills is not a mere form restatement.
    return not re.search(
        r"(?i)\b(?:выполняет|изучает|знакомится|определяет|измеряет|"
        r"собирает|проходит|оценивает|разрабатывает|составляет)\b",
        text,
    )


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


_PLURAL_ADJ_ENDINGS = ("ые", "ие", "ых", "их")
_MASCULINE_AGENT_SUFFIX_RE = re.compile(
    r"(?i)(?:ник|тель|ист|чик|щик|ец|ор|ёр|ер|ант|ент|лог|ик)$"
)


def _is_plural_adjective_run(words: list[str]) -> bool:
    """True when the modifiers before a noun are plural, so the noun is plural too."""

    for word in words:
        low = _strip_punct_word(word)[1].casefold()
        if low.endswith(("ние", "тие")):
            continue
        if low.endswith(_PLURAL_ADJ_ENDINGS):
            return True
    return False


def _noun_to_genitive_plural(word: str) -> str:
    """Genitive plural of a nominative plural noun; empty when the form is unproven."""

    prefix, core, suffix = _strip_punct_word(word)
    low = core.casefold()
    if len(core) < 4:
        return ""
    if low.endswith("ия"):
        changed = core[:-2] + "ий"
    elif low.endswith(("жи", "чи", "ши", "щи")):
        # A hushing stem takes «-ей» in either gender: «вещи» → «вещей».
        changed = core[:-1] + "ей"
    elif low.endswith(("и", "ы")) and _MASCULINE_AGENT_SUFFIX_RE.search(low[:-1]):
        # An agent noun is masculine: «путешественники» → «путешественников».
        changed = core[:-1] + "ов"
    elif low.endswith("а") and low[-2] not in "аеёиоуыэюя":
        # Neuter plural drops its ending: «качества» → «качеств».
        changed = core[:-1]
    else:
        # Gender is undecidable here, so the singular rules stay in charge.
        return ""
    return f"{prefix}{changed}{suffix}"


def _agreeing_noun_to_genitive(noun: str, modifiers: list[str]) -> str:
    """Genitive of a noun that must keep the number of its own modifiers."""

    if _is_plural_adjective_run(modifiers):
        plural = _noun_to_genitive_plural(noun)
        if plural:
            return plural
    return _head_noun_to_genitive(noun)


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
            _agreeing_noun_to_genitive(
                head[leading_adjectives], head[:leading_adjectives]
            ),
            *head[leading_adjectives + 1 :],
        ]
    elif (
        len(head) >= 3
        and _is_adjective(head[0])
        and any(word.casefold() == "и" for word in head[:-1])
    ):
        noun = _agreeing_noun_to_genitive(head[-1], head[:-1])
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


def _process_enumeration_to_genitive(phrase: str) -> str:
    """Genitive of every coordinated process head; dependents stay verbatim.

    A member that does not open a process of the closed class continues the
    previous head, so inflecting it would break the source wording.
    """

    pieces = re.split(r"(,\s*|\s+и\s+)", _normalize_spaces(phrase))
    rendered = []
    for index, piece in enumerate(pieces):
        if index % 2 or not _performed_process_conjuncts(piece):
            rendered.append(piece)
            continue
        rendered.append(_phrase_to_genitive(piece))
    return "".join(rendered)


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

    return bool(re.search(r"(?i)(?:[кгхжшщч]а|ота|ета|ина|ица|жда|ена|ема)$", low))


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
    result_low = (result or "").casefold().strip()
    clause_low = clause.casefold()
    # Only a leading event predicate makes the lesson an event. Trailing
    # «участвует в … соревнованиях» after map/drill actions must not reclassify.
    if not re.match(r"(?i)^(?:выступает\b|участвует\s+в\b)", result_low):
        return ""
    blob = _normalize_spaces(f"{result} {clause}")
    if not _has_event_participation(blob):
        return ""
    event_src = result_low if any(
        stem in result_low for stem in _EVENT_KIND_STEMS
    ) else clause_low
    if "соревнован" in event_src:
        # Pedagogical mini-competitions beside drills/dictations are lesson
        # forms, not a competition-week TYPE.
        if re.search(
            r"(?i)\b(?:мини\s+соревнован|диктант|упражнен|изучает|знакомится|"
            r"выполняет|определяет|измеряет)\b",
            result_low,
        ):
            return ""
        if "туристск" in event_src:
            return "туристские соревнования"
        return "соревнования"
    if "конкурс" in event_src:
        return "конкурс"
    if "слёт" in event_src or "слет" in event_src:
        return "туристский слёт" if "туристск" in event_src else "слёт"
    if "праздник" in event_src:
        return "праздничное мероприятие"
    if "мероприяти" in event_src:
        modifier = re.search(r"\b((?:[а-яё-]+(?:ых|их)\s+){1,3})мероприяти", clause_low)
        if modifier:
            adjectives = [
                _adj_to_acc(_adj_gen_pl_to_sg(word, gender="n"), plural=False, gender="n")
                for word in modifier.group(1).split()
                if word not in {"массовых", "различных", "разных", "общих"}
            ]
            if adjectives:
                return " ".join([*adjectives, "мероприятие"])
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
    if re.search(r"(?i)\bзнак\w*", result_low):
        if "топограф" in selected:
            return "практикум по работе с топографическими знаками"
        return "практикум по работе со знаками"
    if "азимут" in result_low:
        return "измерительный практикум"
    if "масштаб" in result_low:
        return "практикум по работе с картой"
    if (
        re.search(r"(?i)\b(?:составля|разрабатыва)", result_low)
        and re.search(r"(?i)\bалгоритм", selected)
    ):
        return "практикум по разработке алгоритма"
    if (
        re.search(r"(?i)\b(?:составля|разрабатыва|программиру)", result_low)
        and re.search(r"(?i)\bпрограмм", selected)
    ):
        return "практикум программирования"
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


def _declared_product_control(result: str) -> str:
    """CONTROL labels bound to proven produce/semiotic RESULT verbs only."""

    pieces: list[str] = []
    for sentence in _result_sentences(result):
        verb = _leading_finite_verb(sentence).casefold()
        if verb == "рисует":
            pieces.append("просмотр рисунков")
            continue
        if verb == "распознаёт":
            obj = _drop_leading_verb(sentence)
            obj = re.sub(
                r"(?i)\s+и\s+объясняет\s+\S+\s+значение\.?$",
                "",
                obj,
            ).strip(" .")
            if obj:
                pieces.append(
                    "устный опрос по значению " + _semiotic_object_to_genitive(obj)
                )
            continue
        if verb in {"создаёт", "создает", "изготавливает"}:
            pieces.append("просмотр и оценка готовой работы")
    if not pieces:
        return ""
    return _cap_sentence("; ".join(dict.fromkeys(pieces)))


def _covers_declared_product_control(control: str, verb: str, obj: str) -> bool:
    low = control.casefold()
    verb_low = verb.casefold()
    if verb_low == "рисует":
        return bool(re.search(r"(?i)просмотр\s+рисунк", low))
    if verb_low == "распознаёт":
        stems = _meaning_stems(obj) or _meaning_stems(verb)
        return "опрос" in low and any(stem[:4] in low for stem in stems)
    if verb_low == "объясняет":
        return "опрос" in low and "значен" in low
    if verb_low in {"создаёт", "создает", "изготавливает"}:
        return "просмотр" in low and "оценк" in low
    return False


def _product_control(result: str) -> str:
    declared = _declared_product_control(result)
    if declared:
        return declared
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
    if re.match(r"(?i)^совершает\b", result):
        # Map-travel and other совершает activities keep the finite wording in
        # CONTROL; a genitive paraphrase of a quoted label is not proven.
        return (
            "Педагогическое наблюдение: проверяется действие "
            + _format_control_action_quote(result)
        )
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


def _selected_control_from_frame(
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


def _control_result_obligations(result: str) -> list[tuple[str, str]]:
    """Read finite operations without changing RESULT or adding source actions."""
    verbs = "|".join(map(re.escape, _PROVEN_FINITE_VERBS))
    pattern = rf"(?i)(?<![а-яё])({verbs}|[а-яё]+(?:ивает|ывает|ает|яет))\b"
    matches = list(re.finditer(pattern, result))
    operations = []
    for i, match in enumerate(matches):
        # Unknown regular finites must be at a clause/coordination boundary.
        prefix = result[:match.start()].rstrip()
        if match.group(1).casefold() not in _PROVEN_FINITE_VERBS and prefix and not re.search(r"(?i)(?:[.;]|\bи|\bа)$", prefix):
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(result)
        obj = result[match.end():end].strip(" ,.;")
        obj = re.sub(r"(?i)\s+(?:и|а|но)$", "", obj)
        operations.append((match.group(1), "" if obj.casefold() in {"и", "а", "но"} else obj))
    # «Изучает и отрабатывает X»: X belongs to both coordinated verbs.
    for i in range(len(operations) - 2, -1, -1):
        if not operations[i][1]:
            operations[i] = (operations[i][0], operations[i + 1][1])
    operations = [(v, o) for v, o in operations if o]
    return operations


def _control_covers_operation(control: str, verb: str, obj: str) -> bool:
    """Require an operation anchor with its object, not an object alone."""
    if _covers_declared_product_control(control, verb, obj):
        return True
    noun = _FINITE_TO_NOUN.get(verb.casefold()) or _VERB_TO_VERBAL_NOUN.get(verb.casefold())
    anchor_word = (noun or verb).casefold()
    if len(anchor_word) <= 6 and anchor_word.endswith(("а", "я", "и")):
        anchor_word = anchor_word[:-1]
    anchor = anchor_word[:5]
    object_words = [w for w in re.findall(r"[а-яё]+", obj.casefold())
                    if len(w) >= 3 and not _is_adjective(w) and w not in _PREPOSITIONS
                    and w not in {"свой", "свою", "свои", "него", "ним", "его"}]
    # Keep anchors inside one control clause. Coordinated nominal operations
    # can share the following object («изучением и отработкой приёмов»).
    for part in re.split(r"[;.]", control.casefold()):
        # Inspecting a created product may verify its creation, but never a
        # second operation such as placing, transporting or using it.
        if object_words and verb.casefold() in {"изготавливает", "составляет", "строит", "подготавливает"} and "проверка" in part:
            if re.search(r"\b" + re.escape(object_words[0][:4]) + r"[а-яё]*\b", part):
                return True
        match = re.search(r"\b" + re.escape(anchor) + r"[а-яё]*\b", part)
        if not match:
            continue
        tail = part[match.end():]
        if not object_words or re.search(r"\b" + re.escape(object_words[0][:3]) + r"[а-яё]*\b", tail):
            return True
    return False


def _dosage_markers(text: str) -> list[str]:
    return [
        item
        for item in re.findall(r"\([^)]+\)", text or "")
        if re.search(r"(?i)(?<![а-яё])(?:круг|раз|подход|мин|сек)\w*\b", item)
    ]


def _normalize_dosage_marker(text: str) -> str:
    return re.sub(
        r"(?<=\d)(?=[а-яё])",
        " ",
        _normalize_spaces(text).casefold(),
        flags=re.IGNORECASE,
    )


def _homogeneous_exercise_catalogue(
    text: str,
) -> tuple[str, str, tuple[str, ...]] | None:
    """Return LABEL, full SOURCE and unique dosages for a proven catalogue."""

    cleaned = _normalize_spaces(text).rstrip(".")
    matches = list(
        re.finditer(
            r"(?i)((?:круговое\s+)?офп|(?:комплекс\s+)?упражнен\w*)\s*:\s*(.+)$",
            cleaned,
        )
    )
    if not matches:
        return None
    match = matches[-1]
    prefix = cleaned[: match.start()].rstrip()
    if prefix and not prefix.endswith(")"):
        return None
    catalogue_text = _normalize_spaces(f"{match.group(1)}: {match.group(2)}")
    if not _has_explicit_action_catalogue(catalogue_text):
        return None
    members = [item.strip() for item in match.group(2).split(",") if item.strip()]
    if len(members) < 2:
        return None
    dosages = tuple(
        dict.fromkeys(
            _normalize_dosage_marker(item)
            for item in _dosage_markers(match.group(2))
        )
    )
    return _normalize_spaces(match.group(1)), cleaned, dosages


def _catalogue_label_dosage_preserved(clause: str, result: str) -> bool:
    """A homogeneous catalogue may compress only to one preserved dosage."""

    catalogue = _homogeneous_exercise_catalogue(clause)
    if catalogue is None:
        return False
    label, _full_source, dosages = catalogue
    folded = _normalize_spaces(result).casefold()
    label_index = folded.find(label.casefold())
    result_dosages = (
        [_normalize_dosage_marker(item) for item in _dosage_markers(folded[label_index:])]
        if label_index >= 0
        else []
    )
    return bool(
        len(dosages) == 1
        and result_dosages
        and result_dosages[0] == dosages[0]
    )


def _compress_exercise_catalogues_in_text(text: str) -> str:
    """Drop colon member lists for ОФП / exercise catalogues; keep label + dosage."""

    if not text:
        return text

    def _ofp_repl(match: re.Match[str]) -> str:
        label = match.group(1)
        catalogue = _homogeneous_exercise_catalogue(match.group(0))
        if catalogue is None:
            return match.group(0)
        _, _, dosages = catalogue
        if len(dosages) > 1:
            return match.group(0)
        if not dosages:
            return label
        return _normalize_spaces(f"{label} {dosages[0]}")

    compressed = re.sub(
        r"(?i)((?:круговое\s+)?офп)\s*:\s*([^.;]+)",
        _ofp_repl,
        text,
    )

    def _exercise_repl(match: re.Match[str]) -> str:
        label = match.group(1)
        catalogue = _homogeneous_exercise_catalogue(match.group(0))
        if catalogue is None:
            return match.group(0)
        _, _, dosages = catalogue
        if len(dosages) > 1:
            return match.group(0)
        if not dosages:
            return label
        return _normalize_spaces(f"{label} {dosages[0]}")

    compressed = re.sub(
        r"(?i)\b((?:комплекс\s+)?упражнен\w*)\s*:\s*([^.;]+)",
        _exercise_repl,
        compressed,
    )
    return _normalize_spaces(compressed)


def _token_overlap_ratio(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[а-яёa-z0-9]{4,}", (left or "").casefold()))
    right_tokens = set(re.findall(r"[а-яёa-z0-9]{4,}", (right or "").casefold()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(
        1, min(len(left_tokens), len(right_tokens))
    )


def _has_expanded_ofp_catalogue(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:круговое\s+)?офп\s*:\s*[^.]{0,60},\s*[^.]{0,60},",
            text or "",
        )
    )


def _control_has_long_result_quotes(control: str) -> bool:
    text = control or ""
    if re.search(r"(?i)проверя(?:ется|ются)\s+действи", text):
        return True
    return bool(re.search(r"«[^»]{72,}»", text))


def _control_quotes_result_action(control: str) -> bool:
    """True only for quoted finite RESULT wording, not quoted game names."""

    for quoted in re.findall(r"«([^»]+)»", control or ""):
        if _leading_finite_verb(quoted):
            return True
    return False


def _control_nearly_duplicates_result(result: str, control: str) -> bool:
    control_text = _normalize_spaces(control)
    result_text = _normalize_spaces(result)
    low = control_text.casefold()
    if re.search(r"(?i)проверя(?:ется|ются)\s+действи", low):
        return True
    body = result_text.rstrip(".").casefold()
    if body and len(body) >= 80 and body in low:
        return True
    return False


def _rc_verbosity_block_reasons(result: str, control: str) -> tuple[str, ...]:
    """FINAL-gate blockers for RESULT/CONTROL catalogue and quote inflation."""

    reasons: list[str] = []
    if _control_nearly_duplicates_result(result, control):
        reasons.append("CONTROL почти дублирует RESULT")
    if _has_expanded_ofp_catalogue(result) or _has_expanded_ofp_catalogue(control):
        reasons.append("RESULT/CONTROL содержат повторяющийся каталог ОФП")
    if _control_has_long_result_quotes(control):
        reasons.append("CONTROL содержит длинные цитаты RESULT")
    if _control_quotes_result_action(control):
        reasons.append("CONTROL цитирует RESULT вместо semantic labels")
    return tuple(reasons)


def _format_control_action_quote(phrase: str) -> str:
    """Quote a RESULT sentence for CONTROL without nesting identical guillemets."""

    capped = _cap_sentence(_normalize_spaces(phrase)).rstrip(".")
    if "«" in capped or "»" in capped:
        inner = capped.replace("«", "„").replace("»", "“")
        return f"«{inner}»"
    return f"«{capped}»"


def _shorten_control_action_phrase(phrase: str, *, limit: int = 70) -> str:
    """Shorten a RESULT action for CONTROL without dropping essential PPs/objects."""

    phrase = _normalize_spaces(phrase).rstrip(".")
    phrase = _compress_exercise_catalogues_in_text(phrase)
    if len(phrase) <= limit:
        return phrase
    verb = _leading_finite_verb(phrase)
    obj = _drop_leading_verb(phrase)
    if _has_expanded_ofp_catalogue(phrase) or re.search(r"(?i)\bофп\b", phrase):
        short_obj = _short_object(obj, keep_first_prep=False) or obj
        phrase = _normalize_spaces(f"{verb} {short_obj}".strip())
        if len(phrase) <= limit:
            return phrase
    # Prefer keeping a trailing exemplar parenthesis (треугольники, «бабочки»…).
    # Ignore simple role labels like «(прямая засечка)».
    paren_match = re.search(
        r"(\(([^)]*(?:и\s*т\.?\s*п\.?|«)[^)]*)\))\s*$",
        phrase,
    )
    paren = paren_match.group(1).strip() if paren_match else ""
    core = phrase[: paren_match.start()].rstrip(" ,;") if paren_match else phrase
    if paren:
        core_tokens = core.split()
        if verb and core_tokens and core_tokens[0].casefold() == verb.casefold():
            tail_tokens = core_tokens[1:]
        else:
            tail_tokens = core_tokens
        if len(tail_tokens) <= 5:
            keep_tail = tail_tokens
        else:
            # Keep the opening object chain and the NP attached to exemplars.
            head = tail_tokens[:3]
            attach = tail_tokens[-2:]
            keep_tail = list(dict.fromkeys([*head, *attach]))
        compact = _normalize_spaces(
            " ".join([tok for tok in [verb, *keep_tail] if tok]).rstrip(" ,;")
            + f" {paren}"
        )
        # Exemplar paren is semantic cargo — keep it even if slightly over soft limit.
        if len(compact) <= max(limit + 24, len(paren) + 36):
            return compact
    tokens = phrase.split()
    kept: list[str] = []
    for token in tokens:
        candidate = _normalize_spaces(" ".join([*kept, token]))
        if kept and len(candidate) > limit:
            break
        kept.append(token)
        balanced = (
            candidate.count("(") == candidate.count(")")
            and candidate.count("«") == candidate.count("»")
            and candidate.count("„") == candidate.count("“")
        )
        if len(candidate) >= limit and balanced:
            break
    shortened = _normalize_spaces(" ".join(kept)).rstrip(" ,;:")
    while shortened and (
        shortened.count("(") != shortened.count(")")
        or shortened.count("«") != shortened.count("»")
        or shortened.count("„") != shortened.count("“")
    ):
        shortened = shortened.rsplit(" ", 1)[0].rstrip(" ,;:")
    return shortened or (verb or phrase[:limit])


def _control_label_without_dosage(obj: str) -> str:
    """Keep the semantic label and conditions; dosage stays authoritative in RESULT."""

    label = _compress_exercise_catalogues_in_text(obj)
    for marker in _dosage_markers(label):
        label = label.replace(marker, "")
    label = re.sub(r"\(\s*\)", "", label)
    label = re.sub(r"(?i)\(?\s*приложение\s*№?\s*\d+\s*\)?", "", label)
    label = re.sub(r"\s+([,;:.])", r"\1", label)
    return _normalize_spaces(label).strip(" ,.;")


def _compact_control_skill_label(label: str) -> str:
    """Keep the assessed skill head while RESULT retains its full evidence."""

    compact = _normalize_spaces(label).strip(" ,.;")
    # Named examples remain mandatory in RESULT, but CONTROL assesses the
    # semantic game/exercise category before the explanatory colon.
    if ":" in compact:
        head, tail = compact.split(":", 1)
        if "«" in tail or "»" in tail:
            compact = head
    compact = re.sub(
        r"(?i)\b(игр\w+\s+на\s+(?:развитие\s+)?[^:;,.]+?)\s+"
        r"\1(?=\s*[:;,.]|$)",
        r"\1",
        compact,
    )
    return _normalize_spaces(compact).strip(" ,.;")


def _compact_control_labels(obj: str) -> list[str]:
    """Return ordered skill labels, not a restatement of the whole RESULT.

    Dosage-bearing coordinated items are independently recoverable, so each
    can contribute its own semantic head.  Otherwise fail closed to one
    shortened object that still retains its first checkable noun.
    """

    cleaned = _control_label_without_dosage(obj)
    if not cleaned:
        return []
    items = _split_week_action_items(obj)
    dosage_items = [_action_item_dosage(item) for item in items]
    if len(items) > 1 and all(item is not None for item in dosage_items):
        candidates = [
            _control_label_without_dosage(item[0])
            for item in dosage_items
            if item is not None
        ]
    else:
        candidates = [cleaned]

    labels: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        label = _compact_control_skill_label(candidate)
        key = label.casefold().replace("ё", "е")
        if label and key not in seen:
            seen.add(key)
            labels.append(label)
    return labels


_CONTROL_PIECE_PREP_RE = re.compile(
    r"(?i)^(?:в|во|на|за|к|ко|с|со|по|из|от|до|у|о|об|обо|при|для|через|над|под)\b"
)


def _control_keeps_no_finite_predicate(control: str) -> bool:
    """True when CONTROL names methods and objects without RESULT predicates."""

    return not any(
        _is_proven_finite_token(word) for word in _normalize_spaces(control).split()
    )


def _process_action_control_piece(verb: str, obj: str) -> str:
    """Non-quoting CONTROL label for one proven process action of the RESULT.

    The observed object keeps the action checkable without pasting the RESULT
    predicate into CONTROL. Only a compact label already proven for CONTROL is
    used, so anything less certain fails closed to an empty piece.
    """

    verb_low = verb.casefold()
    if verb_low in _KNOWLEDGE_RESULT_VERBS or verb_low not in _ACTION_FINITE_VERBS:
        return ""
    labels = [
        _normalize_spaces(label).strip(" ,.;") for label in _compact_control_labels(obj)
    ]
    if not labels or not all(labels):
        return ""
    if any(_CONTROL_PIECE_PREP_RE.match(label) for label in labels):
        return ""
    piece = "педагогическое наблюдение: " + _join_and(labels)
    if not _control_keeps_no_finite_predicate(piece):
        return ""
    return piece


def _control_coverage_piece(control: str, verb: str, obj: str) -> str:
    """CONTROL piece for an uncovered RESULT action; quoting is the last resort.

    A non-quoting label may only extend CONTROL that itself stays free of
    RESULT predicates; otherwise the quoted form keeps the week blocked.
    """

    if _control_keeps_no_finite_predicate(control):
        piece = _process_action_control_piece(verb, obj)
        if piece:
            return piece
    payload = _shorten_control_action_phrase(_normalize_spaces(f"{verb} {obj}"))
    return "практическая проверка " + _format_control_action_quote(payload)


def _control_requires_case_rebuild(control: str) -> bool:
    """Detect supported malformed labels that must be rebuilt from RESULT."""

    text = _normalize_spaces(control)
    broken_coord = re.search(
        r"(?i)\bи\s+[а-яё-]+(?:ов|ев|ёв|ей|ствий|ений|аний|яний|ок)\b",
        text,
    )
    # Declared knowledge CONTROL copies RESULT genitive lists after a colon
    # («функции корней … и плодов»). That is not a broken dative reconstruction.
    if broken_coord and re.search(r"(?i)устный опрос:", text):
        broken_coord = None
    return bool(
        re.search(
            r"(?i)\bна\s+[а-яё-]+(?:ых|их)\s+[а-яё-]+(?:ов|ев|ёв|ей|ий|ств)\b",
            text,
        )
        or broken_coord
        or re.search(r"(?i)(?:^|[:,;]\s*)играх\b", text)
    )


def _semantic_labels_control(result: str) -> str:
    """Build one CONTROL from compact RESULT labels without quoting predicates."""

    compact_result = _strip_result_provenance_context(
        _compress_exercise_catalogues_in_text(result)
    )
    segments = _result_control_segments(compact_result)
    if not segments:
        return ""

    knowledge = [
        obj for verb, obj in segments if verb in _KNOWLEDGE_RESULT_VERBS and obj
    ]
    declared_labels: list[str] = []
    declared_ok = bool(knowledge)
    for verb, obj in segments:
        if verb not in _KNOWLEDGE_RESULT_VERBS or not obj:
            continue
        label = _knowledge_control_label(verb, obj)
        if not label:
            declared_ok = False
            break
        declared_labels.append(label)
    # A colon introduces unchanged, already-proven RESULT objects without a
    # lossy suffix-based attempt to manufacture dative government for a long
    # coordinated list.
    if declared_ok and declared_labels:
        oral = "устный опрос: " + "; ".join(declared_labels)
    else:
        oral = "устный опрос: " + _join_and(knowledge) if knowledge else ""

    observed_labels: list[str] = []
    observed_seen: set[str] = set()
    for verb, obj in segments:
        if verb in _KNOWLEDGE_RESULT_VERBS or not obj:
            continue
        if not (_VERB_TO_VERBAL_NOUN.get(verb) or _FINITE_TO_NOUN.get(verb)):
            return ""
        compact_labels = _compact_control_labels(obj)
        if not compact_labels:
            return ""
        if verb == "участвует":
            compact_labels = [
                "участие " + label if re.match(r"(?i)^в\s+", label) else label
                for label in compact_labels
            ]
        for label in compact_labels:
            key = label.casefold().replace("ё", "е")
            if key not in observed_seen:
                observed_seen.add(key)
                observed_labels.append(label)

    observed = ""
    if observed_labels:
        observed = "Педагогическое наблюдение: " + _join_and(observed_labels)
    if oral and observed:
        return f"{oral}; {observed}"
    if oral and declared_ok and declared_labels and not observed:
        return _cap_sentence(oral)
    return oral or observed


def _quoted_actions_control(result: str) -> str:
    """Legacy entry point: compact semantic CONTROL without RESULT quotations."""

    semantic = _semantic_labels_control(result)
    if semantic:
        return semantic

    compact_result = _compress_exercise_catalogues_in_text(result)
    finite_sentences = [
        _normalize_spaces(sentence).rstrip(".")
        for sentence in _result_sentences(compact_result)
        if _leading_finite_verb(sentence)
    ]
    knowledge_only = bool(finite_sentences) and all(
        _leading_finite_verb(sentence).casefold() in _KNOWLEDGE_RESULT_VERBS
        for sentence in finite_sentences
    )
    if knowledge_only:
        oral = _oral_quiz_control(ActionFrame("", "", "", ""), compact_result)
        if oral:
            return oral
        rebuilt = _control_from_proven_result(compact_result)
        return rebuilt or "устный опрос"

    if re.search(r"(?i)\bили\b|\bпо выбору\b", compact_result):
        return "практическая проверка изделия по выбору"

    rebuilt = _control_from_proven_result(compact_result)
    if rebuilt and not _control_has_long_result_quotes(rebuilt):
        return _join_control_clauses([rebuilt])
    observed = _observation_from_action_segments(
        _result_control_segments(compact_result)
    )
    if observed and not _control_has_long_result_quotes(observed):
        return observed
    return "педагогическое наблюдение"


def _control_has_multisentence_quotes(control: str) -> bool:
    return bool(re.search(r"«[^»]*\.[^»]*»", control or ""))


def control_from_frame(
    frame: ActionFrame, *, lesson_type: str, theory_hours: int,
    practice_hours: int, planned_result: str = "",
) -> str:
    control = _selected_control_from_frame(
        frame, lesson_type=lesson_type, theory_hours=theory_hours,
        practice_hours=practice_hours, planned_result=planned_result,
    )
    if not planned_result.strip():
        return control
    if re.search(r"(?i)\bили\b|\bпо выбору\b", planned_result) and not re.search(
        r"(?i)\bили\b|\bпо выбору\b|\bвыбранн", control,
    ):
        # Keep the alternative visible without pasting a long RESULT quote.
        control = "практическая проверка изделия по выбору"
    operations = _control_result_obligations(planned_result)
    if len(operations) > 1:
        for verb, obj in operations:
            if not _control_covers_operation(control, verb, obj):
                piece = _control_coverage_piece(control, verb, obj)
                control = f"{control}; {piece}" if control else piece
    return control


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
            if re.search(r"(?i)занятиях\s+в\s+бассейне", planned_result):
                return "учебно-тренировочное занятие"
            if re.search(r"(?i)занятиях\s+на\s+скалодроме", planned_result):
                return "учебно-тренировочное занятие"
            signs_type = _practice_activity_type(planned_result, frame.clause)
            if signs_type:
                return signs_type
            if re.search(r"(?i)\b(?:играх|игре|эстафет)", planned_result) and not re.search(
                r"(?i)\b(?:диктант|упражнен|выполняет)\b",
                planned_result,
            ):
                return "игра"
            if "викторин" in result:
                return "викторина"
            if re.search(r"(?i)\bпоход", planned_result):
                return "поход"
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
            if _performed_process_conjuncts(_drop_leading_verb(planned_result)):
                # Rehearsing the named ways of a movement is a practical lesson;
                # «практикум» names a workshop built around one task product.
                return "практическое занятие"
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
    "разрабатывает": "разработать", "собирает": "собрать",
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
    if _locative_drawing_object(text) is not None:
        return True
    if _semiotic_object_phrase(text) is not None:
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


def _conducted_event_result(clause: str) -> str | None:
    """A pupil participates in source-named events; retain the complete list.

    Convert only attested event nouns with known locatives. An unknown list
    member rejects the entire conversion, never silently dropping that member.
    Quoted names after a colon are source text and are not inflected.
    """
    # Simple one-object conduct actions already have a closed repair below.
    # This path is only for catalogues whose members must survive intact.
    if ":" not in clause and "," not in clause:
        return None
    match = re.match(r"(?i)^проведение\s+(.+)$", _normalize_spaces(clause))
    if not match:
        return None
    body, colon, names = match.group(1).partition(":")
    locatives = {"игр": "играх", "праздников": "праздниках",
                 "соревнований": "соревнованиях", "викторин": "викторинах",
                 "эстафет": "эстафетах", "экскурсий": "экскурсиях"}
    converted = []
    for item in body.split(","):
        event = re.fullmatch(
            r"(?i)\s*((?:(?:[а-яё-]+(?:ых|их)|и)\s+)*)"
            r"(" + "|".join(locatives) + r")((?:\s+(?:с|со|для|по|на|в)\s+.+)?)\s*",
            item,
        )
        if not event:
            return None
        converted.append(event.group(1) + locatives[event.group(2).casefold()] + event.group(3))
    result = "участвует в " + ", ".join(converted)
    if colon:
        result += ": " + names.strip()
    return result


def _finite_other_slot_result(clause: str) -> str:
    event_result = _conducted_event_result(clause)
    if event_result:
        return event_result
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
    pieces = _split_coordinating_и_outside_quotes(head)
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
    provenance_codes: list[str] = []
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
        provenance_codes.append(PROVENANCE_GENERIC_ONLY)
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
    neighbor_units = _fuse_simulation_action_neighbors(list(neighbor_extras))
    for unit in neighbor_units:
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
        if not extra or not _is_finite_result_phrase(extra):
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
        provenance_codes=tuple(provenance_codes),
    )


def _proven_finite_predicates() -> set[str]:
    return set(_PROVEN_FINITE_VERBS)


def _nominal_governed_span(clause: str, span: str) -> bool:
    """The copied span follows a nominal action, which governs the genitive."""

    low = _normalize_spaces(clause).casefold()
    index = low.find(_normalize_spaces(span).casefold())
    if index <= 0:
        return False
    preceding = low[:index].split()
    return bool(preceding) and _looks_like_verbal_noun(preceding[-1])


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
    first = _finite_token(result.split()[0])
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
        if _finite_token(left) in allowed and _finite_token(right) not in allowed:
            return "unproven_coordinated_predicate"
    for match in re.finditer(r"(?i)\bучаствует\s+в\s+([^.,;:]+)", result):
        words = match.group(1).casefold().split()
        while words and _is_adjective(words[0]):
            words.pop(0)
            if len(words) > 1 and words[0] == "и" and _is_adjective(words[1]):
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
        # In the source the span may be governed by a nominal action, which
        # requires the genitive. Copying it after a finite verb keeps that
        # case and does not prove a direct object.
        if _nominal_governed_span(clause, span):
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


def _prohibition_only_source(source: str) -> bool:
    """Recognise direct prohibitions, not mentions, ordinary negation or a topic.

    This closed grammatical guard does not infer an opposite allowed action.
    Every source clause must be prohibitive: a separate positive activity must
    not become NEEDS_REVIEW merely because a neighbouring clause forbids one.
    """
    infinitives = set(_TASK_VERBS.values()) | {
        "выполнять", "использовать", "применять", "проводить", "начинать",
        "продолжать", "оставлять", "снимать", "надевать", "приближаться",
        "касаться", "входить", "выходить", "брать", "трогать", "открывать",
        "закрывать", "переходить", "подниматься", "спускаться", "лазать",
    }
    action = "(?:" + "|".join(sorted(infinitives, key=len, reverse=True)) + ")"
    nominal = "(?:" + "|".join(sorted(set(_VERBAL_NOUN_TO_VERB) | {"использование"})) + ")"
    clauses = re.split(r"[.!?;\n]+", source.casefold())
    meaningful = []
    for clause in clauses:
        clause = re.sub(r"^\s*(?:практика|теория)\s*:\s*", "", clause).strip()
        if not clause or clause in {"практика", "теория"}:
            continue
        meaningful.append(clause)
    if not meaningful:
        return False
    for clause in meaningful:
        # Prefix modality governs the following action, not an object adjective.
        prefix = re.fullmatch(
            rf"(?:запрещено|запрещается|нельзя|не допускается)\s+(?:{action}|{nominal})\b.+",
            clause,
        )
        imperative = re.fullmatch(rf"не\s+(?:{action}|[а-яё]+йте(?:сь)?)\b.+", clause)
        suffix = re.fullmatch(
            rf"(?:{action}|{nominal})\b.+\s+(?:запрещено|запрещается|не допускается)",
            clause,
        )
        # 'Не запрещено' and 'не только' are not prohibitions. Do not guess
        # scope in a coordinated clause containing a second finite action.
        if re.search(r"\b(?:не\s+(?:запрещено|запрещается|только)|но|однако)\b", clause):
            return False
        finite = "(?:" + "|".join(_PROVEN_FINITE_VERBS) + ")"
        if re.search(rf"(?:,\s*|\s+(?:а|и)\s+)(?:{action}|{finite})\b", clause):
            return False
        if not (prefix or imperative or suffix):
            return False
    return True


def _comma_continues_prepositional_complement(text: str) -> bool:
    """Trailing «на X, Y» with a bare final noun is one complement, not a list.

    Parallel modified NPs after a preposition («по спортивному туризму,
    спортивному ориентированию») stay ordinary enumerations.
    """

    return bool(
        re.search(
            r"(?i)\b(?:на|для|о|об|обо|про|по|при|за|через)\s+"
            r"[^,;]{2,100},\s*[а-яё-]{3,}\s*$",
            text,
        )
    )


def _bare_list_without_action(source: str) -> bool:
    """A standalone enumeration is not evidence of a pupil's action."""
    units = [u for u in _clause_units(source) if u.casefold() not in {"практика", "теория"}]
    if len(units) != 1:
        return False
    text = re.sub(r"(?i)^(?:практика|теория)\s*:\s*", "", units[0])
    text = re.sub(r"(?i)^перечень\s*:\s*", "", text)
    unquoted = re.sub(r'«[^»]*»|"[^"]*"|„[^“]*“', "название", text)
    members = re.split(r"\s*[,/]\s*", unquoted)
    if len(members) < 2 or not all(members):
        return False
    # Explicit verbal/nominal governing actions must keep the ordinary path.
    # Named techniques alone (unlike performing them) provide no predicate.
    if _VERBAL_NOUN_FIND_RE.search(text) or _CONTROL_RESULT_VERB_RE.search(text):
        return False
    first = text.split()[0] if text.split() else ""
    if _nominal_activity_lemma(first) in _NOMINAL_PERFORM_LEMMAS:
        return False
    if _is_leading_form_activity(first) or _participation_lemma(first):
        return False
    if _comma_continues_prepositional_complement(unquoted):
        return False
    if first.casefold() in _STATE_OR_KNOWLEDGE_LEMMAS and re.search(
        r"(?i)\b(?:на|для|о|об|обо|про|по|при)\b", unquoted
    ):
        # «Влияние … на X, Y» is a knowledge frame, not an item list.
        return False
    if re.search(r"(?i)\b(?:их|его|её|ее)\b", unquoted):
        return False  # Dependent description/anaphora is not a standalone list.
    if re.search(r"(?i)\b(?:не|педагог|учитель|ребенок|ребёнок|ученик)\b|[:!?]", text):
        return False
    return True


def _pupil_observes_demonstration(source: str) -> tuple[str, str] | None:
    """Resolve only an explicit adjacent demonstrator/observer relation."""
    units = [u for u in _clause_units(source) if u.casefold() not in {"практика", "теория"}]
    if len(units) != 2:
        return None
    show = re.fullmatch(
        r"(педагог|учитель|инструктор|тренер)\s+(?:демонстрирует|показывает)(?:\s+(.+))?",
        units[0], re.IGNORECASE,
    )
    watch = re.fullmatch(
        r"(?:ребёнок|ребенок|ученик|ученица|дети|ученики|учащиеся|обучающиеся)"
        r"\s+наблюда(?:ет|ют)", units[1], re.IGNORECASE,
    )
    if not show or not watch:
        return None
    obj = show.group(2) or ""
    if re.search(r"(?i)\b(?:не|но|а)\b|[,;]", obj):
        return None  # Do not guess scope or discard an additional action.
    teacher = {"педагог": "педагога", "учитель": "учителя",
               "инструктор": "инструктора", "тренер": "тренера"}[show.group(1).casefold()]
    # Quote the original object instead of inventing a case or a new action.
    detail = f": «{obj}»" if obj else ""
    return (
        f"Наблюдает за показом {teacher}{detail}.",
        f"педагогическое наблюдение за участием ребёнка в просмотре показа {teacher}{detail}",
    )


def _derive_selected_fields_v2(
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
    observation = _pupil_observes_demonstration(context)
    if observation:
        return replace(candidate, frame=ActionFrame(context, "наблюдает", "", ""),
                       planned_result=observation[0], assessment_method=observation[1])
    if _prohibition_only_source(context):
        # Keep source fields and TYPE untouched; a prohibition alone provides
        # no observable positive result and authorises no positive control.
        return replace(
            candidate,
            frame=ActionFrame(context, "", "", ""),
            planned_result="",
            assessment_method="",
            warnings=(*candidate.warnings,
                      "NEEDS_REVIEW: источник содержит только запрет; "
                      "положительное действие не задано."),
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
    if _bare_list_without_action(context):
        return replace(
            candidate, frame=ActionFrame(context, "", "", ""),
            planned_result="", assessment_method="",
            warnings=(*candidate.warnings,
                      "NEEDS_REVIEW: перечень не задаёт действия ученика."),
        )
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
        provenance_codes=_stamp_generic_only(candidate.provenance_codes),
    )


def _selected_proof_absent_from_result(phrase: str, result: str) -> bool:
    """True when the selected frame's proven finite activity is not in RESULT."""

    folded = _normalize_spaces(result).casefold()
    if not phrase or not folded:
        return True
    verb = _leading_finite_verb(phrase).casefold()
    if not verb:
        return True
    if verb == "участвует":
        return "участвует" not in folded and "викторин" not in folded
    if verb == "совершает":
        return "совершает" not in folded and "путешеств" not in folded
    if verb == "проводит":
        return "проводит" not in folded and "викторин" not in folded
    obj = _drop_leading_verb(phrase).casefold()
    stems = [
        token
        for token in re.findall(r"[а-яё]{4,}", obj)
        if token not in _PREPOSITIONS and not _is_adjective(token)
    ]
    if stems and any(stem[:4] in folded for stem in stems):
        return False
    return verb not in folded


def _nonempty_result_in(candidate: str, result: str) -> bool:
    candidate = _normalize_spaces(candidate).strip(" .").casefold()
    return bool(candidate and candidate in _normalize_spaces(result).casefold())


def _title_complement_segments(topic_title: str) -> list[str]:
    """Coordinated title tails after the first segment (topic head)."""

    parts = [
        part.strip().rstrip(".")
        for part in re.split(r",\s*", topic_title or "")
        if part.strip()
    ]
    return parts[1:] if len(parts) >= 2 else []


def _title_segment_is_actionable(segment: str) -> bool:
    """Keep only title tails with a proven verbal-noun → finite mapping."""

    tokens = segment.split()
    if not tokens:
        return False
    first = _strip_punct_word(tokens[0])[1]
    # Require a conjugated pupil verb: bare knowledge nouns in the title
    # («предупреждение…», «преодоление…») stay outside this restore path.
    return bool(_conjugate_verbal_noun(first))


def _enrich_result_with_title_complements(
    candidate: ContentEngineV2Result,
    *,
    topic_title: str,
    theory_hours: int,
    practice_hours: int,
) -> ContentEngineV2Result:
    """Bring actionable title tails into RESULT/CONTROL when still uncovered."""

    if not candidate.planned_result.strip() or not topic_title.strip():
        return candidate
    extras_result: list[str] = []
    extras_control: list[str] = []
    merged_so_far = candidate.planned_result
    for segment in _title_complement_segments(topic_title):
        if not _title_segment_is_actionable(segment):
            continue
        prefer_practice = bool(practice_hours)
        orders = ((False, True) if prefer_practice else (True, False))
        proof = None
        for theory_only in orders:
            local = _derive_selected_fields_v2(
                topic_title=topic_title,
                theory_text=segment if theory_only else "",
                practice_text="" if theory_only else segment,
                program_content=segment,
                theory_hours=max(1, theory_hours) if theory_only else 0,
                practice_hours=0 if theory_only else max(1, practice_hours),
            )
            if (
                not local.planned_result
                or not local.assessment_method
                or local.warnings
                or "по теме" in local.planned_result.casefold()
                or _quality_issue(
                    local.planned_result,
                    local.assessment_method,
                    source=segment,
                    clause=local.frame.clause,
                )
            ):
                continue
            if not _selected_proof_absent_from_result(local.planned_result, merged_so_far):
                continue
            if _nonempty_result_in(local.planned_result, merged_so_far):
                continue
            proof = local
            break
        if proof is None:
            continue
        extras_result.append(proof.planned_result)
        extras_control.append(proof.assessment_method)
        merged_so_far = _merge_independent_part_results(
            [merged_so_far, proof.planned_result]
        )
    if not extras_result:
        return candidate
    merged_result = _merge_independent_part_results(
        [candidate.planned_result, *extras_result]
    )
    assessment = _unified_process_performance_control(
        merged_result,
        _join_control_clauses([candidate.assessment_method, *extras_control]),
    )
    if _control_has_multisentence_quotes(assessment):
        rebuilt = _quoted_actions_control(_fold_week_result(merged_result))
        if rebuilt:
            assessment = rebuilt
    assessment = _prefer_quoted_control_if_incomplete(
        _fold_week_result(merged_result), assessment
    )
    return replace(
        candidate,
        planned_result=merged_result,
        assessment_method=assessment,
    )


def _retained_complement_covered(clause: str, original: ContentEngineV2Result) -> bool:
    """Recognise provenance already established by the complement mechanism."""
    parts = _focus_complement_parts(clause)
    if parts == [clause] or not original.planned_result.strip():
        return False
    for part in parts:
        if not _nonempty_result_in(part, original.frame.clause):
            return False
        phrase, _frame = transform_clause_to_result(
            part, theory_only=False, full_source=original.frame.clause, topic_title="",
        )
        if not _nonempty_result_in(phrase, original.planned_result):
            return False
    return True


def _coordinated_action_uncovered(clause: str, result: str) -> bool:
    """Coordinated nominal actions of one clause must all reach the result."""

    if " и " not in clause.casefold():
        return False
    folded = result.casefold()
    for head, verb in _VERBAL_NOUN_TO_VERB.items():
        if not re.search(r"(?i)\b" + re.escape(head) + r"\b", clause):
            continue
        if not (
            re.search(r"(?i)\s+и\s+" + re.escape(head) + r"\b", clause)
            or clause.casefold().startswith(head + " ")
        ):
            continue
        if verb not in folded and not _list_member_reached(head, folded):
            return True
    for head in _NOMINAL_PERFORM_LEMMAS:
        if not re.search(r"(?i)\b" + re.escape(head) + r"\b", clause):
            continue
        if not (
            re.search(r"(?i)\s+и\s+" + re.escape(head) + r"\b", clause)
            or clause.casefold().startswith(head + " ")
        ):
            continue
        if not _list_member_reached(head, folded):
            return True
    return False


def _finite_verbs_for_action_head(head: str) -> tuple[str, ...]:
    """Finite RESULT verbs that may realize a SOURCE action/process head.

    Keep in sync with transform/reconstruct: «тренировка» → «отрабатывает»,
    and practical «изучение» of a technique may also surface as «отрабатывает».
    """

    verbs: list[str] = []
    explicit = _conjugate_explicit_action_head(head)
    if explicit:
        verbs.append(explicit.casefold())
    mapped = _conjugate_verbal_noun(head)
    if mapped:
        low = mapped.casefold()
        if low not in verbs:
            verbs.append(low)
    lemma = _verbal_noun_lemma(head).casefold()
    if lemma == "изучение" and "отрабатывает" not in verbs:
        verbs.append("отрабатывает")
    return tuple(verbs)


def _list_member_reached(head: str, folded_result: str) -> bool:
    """The activity named by a list head is present in the result."""

    if any(verb in folded_result for verb in _finite_verbs_for_action_head(head)):
        return True
    lemma = _verbal_noun_lemma(head).casefold()
    if lemma and lemma in folded_result:
        return True
    acc = _noun_nom_to_acc(head).casefold()
    if acc and acc in folded_result:
        return True
    stem = lemma[:-2] if len(lemma) > 5 else lemma
    return bool(stem) and stem in folded_result


def _list_member_uncovered(clause: str, result: str) -> bool:
    """A comma member of the clause names an activity left out of the result."""

    folded = result.casefold()
    members = re.split(r",\s+", _normalize_spaces(clause))
    for member in members[1:]:
        tokens = member.split()
        head = _strip_punct_word(tokens[0])[1] if tokens else ""
        if not head or not _is_shared_object_action_head(head):
            continue
        if _list_member_reached(head, folded):
            continue
        return True
    return False


def _ways_catalogue_uncovered(clause: str, result: str) -> bool:
    """A named catalogue of ways stayed outside the performed action."""

    folded = result.casefold()
    for member in re.split(r"[,;]\s*|\s+и\s+", _normalize_spaces(clause)):
        if not _leading_ways_catalogue(member):
            continue
        if member.strip(" .").casefold() not in folded:
            return True
    return False


def _derive_week_fields_v2(
    *, topic_title: str, theory_text: str, practice_text: str,
    program_content: str = "", theory_hours: int = 0, practice_hours: int = 0,
    occurrence_index: int = 0, practice_appearance_count: int = 0,
) -> ContentEngineV2Result:
    """Retain independently proven clauses of the already assigned week."""
    original = _derive_selected_fields_v2(
        topic_title=topic_title, theory_text=theory_text, practice_text=practice_text,
        program_content=program_content, theory_hours=theory_hours,
        practice_hours=practice_hours, occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )
    source = _week_result_source(
        theory_hours=theory_hours, practice_hours=practice_hours,
        theory_text=theory_text, practice_text=practice_text,
        program_content=program_content,
    )
    if _pupil_observes_demonstration(source) or _prohibition_only_source(source):
        status = "COVERED" if original.planned_result.strip() else "NEEDS_REVIEW"
        return replace(original, clause_coverage=tuple(
            (c, status) for c in _clause_units(source)
            if c.casefold().strip(" .:") not in {"теория", "практика"}
        ))
    if practice_hours and practice_appearance_count > 1:
        units = _coalesce_activity_units(practice_units_from_text(practice_text))
        slots, _flags = assign_distributed_practice_slots(units, practice_appearance_count)
        if not slots:
            return original
        clauses = list(slots[min(occurrence_index, len(slots) - 1)])
    else:
        clauses = _clause_units(source)
    clauses = [c for c in clauses if c.casefold().strip(" .:") not in {"теория", "практика"}]
    if len(clauses) < 2:
        status = (
            "COVERED" if original.planned_result.strip()
            and "по теме" not in original.planned_result.casefold()
            and not any("NEEDS_REVIEW" in w or w.startswith("Безопасный шаблон CE2:") for w in original.warnings)
            else "NEEDS_REVIEW"
        )
        # One clause may assign several coordinated actions: keeping just one
        # of them is partial coverage, not a proven clause.
        partial = [
            clause for clause in clauses
            if status == "COVERED"
            and _coordinated_action_uncovered(clause, original.planned_result)
        ]
        coverage = tuple(
            (c, "NEEDS_REVIEW" if c in partial else status) for c in clauses
        )
        # A clause whose object form could not be proven is reported: the safe
        # template must not hide an unconverted source case. A clause without
        # any proven predicate keeps the established silent fallback.
        unproven_case = any(
            w.startswith("Безопасный шаблон CE2:") and w.rstrip(".").endswith("_case")
            for w in original.warnings
        )
        warnings = original.warnings + tuple(
            "NEEDS_REVIEW: не подтверждено полное покрытие клаузы: " + clause
            for clause, clause_status in coverage
            if clause_status == "NEEDS_REVIEW" and (clause in partial or unproven_case)
        )
        return replace(
            original,
            warnings=tuple(dict.fromkeys(warnings)),
            clause_coverage=coverage,
        )
    results: list[str] = []
    controls: list[str] = []
    uncovered: list[str] = []
    retained = (
        bool(original.planned_result and original.assessment_method)
        and not any(w.startswith("Безопасный шаблон CE2:") for w in original.warnings)
        and "по теме" not in original.planned_result.casefold()
    )
    retained_added = False
    for clause in clauses:
        if retained and _normalize_spaces(clause).casefold() in _normalize_spaces(original.frame.clause).casefold():
            if not retained_added:
                results.append(original.planned_result)
                controls.append(original.assessment_method)
                retained_added = True
            proof = _derive_selected_fields_v2(
                topic_title=topic_title,
                theory_text=clause if not practice_hours else "",
                practice_text=clause if practice_hours else "",
                program_content=clause,
                theory_hours=theory_hours if not practice_hours else 0,
                practice_hours=practice_hours,
            )
            phrase = proof.planned_result
            reconstructed_knowledge = (
                _theory_knowledge_reconstruction(clause)
                if not practice_hours
                else None
            )
            exact = _nonempty_result_in(phrase, original.planned_result)
            if reconstructed_knowledge is not None:
                exact = any(
                    _knowledge_result_cites_clause(sentence, clause)
                    for sentence in _result_sentences(original.planned_result)
                )
            catalogue = _homogeneous_exercise_catalogue(clause)
            compact_catalogue_covered = _catalogue_label_dosage_preserved(
                clause, phrase
            )
            incomplete = (
                _coordinated_action_uncovered(clause, original.planned_result)
                or _ways_catalogue_uncovered(clause, original.planned_result)
                or (
                    not compact_catalogue_covered
                    if catalogue is not None
                    else _list_member_uncovered(clause, original.planned_result)
                )
            )
            absent = (
                not exact
                if reconstructed_knowledge is not None
                else _selected_proof_absent_from_result(phrase, original.planned_result)
            )
            # TYPE may keep this frame while RESULT still came from another
            # unit: restore only when the activity itself is missing.
            if absent and not exact:
                if (
                    phrase
                    and proof.assessment_method
                    and not proof.warnings
                    and "по теме" not in phrase.casefold()
                    and not _quality_issue(
                        phrase,
                        proof.assessment_method,
                        source=clause,
                        clause=proof.frame.clause,
                    )
                ):
                    if phrase not in results:
                        results.append(phrase)
                    quoted = (
                        ("Устный опрос" if not practice_hours else "Педагогическое наблюдение")
                        + ": проверяется действие "
                        + _format_control_action_quote(phrase)
                    )
                    control = quoted
                    if not practice_hours:
                        areas = [
                            _oral_object_for_control(obj)
                            for _verb, obj in _result_control_segments(phrase)
                            if obj
                        ]
                        if areas and all(areas):
                            control = "Устный опрос по: " + _join_and(areas)
                    if control not in controls:
                        controls.append(control)
                else:
                    uncovered.append(clause)
            elif incomplete:
                uncovered.append(clause)
            continue
        reconstructed_knowledge = (
            _theory_knowledge_reconstruction(clause)
            if not practice_hours
            else None
        )
        if (
            _prohibition_only_source(clause)
            or (
                _bare_list_without_action(clause)
                and reconstructed_knowledge is None
                and not (
                    not practice_hours
                    and _knowledge_head_has_only_leading_modifiers(clause)
                )
            )
            or re.search(r"(?i)\b(?:педагог|учитель|инструктор|тренер)\b", clause)
        ):
            uncovered.append(clause)
            continue
        local = _derive_selected_fields_v2(
            topic_title=topic_title,
            theory_text=clause if not practice_hours else "",
            practice_text=clause if practice_hours else "",
            program_content=clause,
            theory_hours=theory_hours if not practice_hours else 0,
            practice_hours=practice_hours,
        )
        if retained and _nonempty_result_in(local.planned_result, original.planned_result):
            reconstructed_knowledge = (
                _theory_knowledge_reconstruction(clause)
                if not practice_hours
                else None
            )
            if reconstructed_knowledge is None or any(
                _knowledge_result_cites_clause(sentence, clause)
                for sentence in _result_sentences(original.planned_result)
            ):
                continue
        # Topic fallback and its warnings cannot certify the clause.
        if (not local.planned_result or not local.assessment_method
                or local.warnings
                or "по теме" in local.planned_result.casefold()
                or "по теме" in local.assessment_method.casefold()
                or _quality_issue(local.planned_result, local.assessment_method,
                                  source=clause, clause=local.frame.clause)):
            uncovered.append(clause)
            continue
        # New additions have not passed the protected, contextual path.
        # Abstain on unsupported object grammar rather than exposing raw
        # genitives or nominal fragments as completed pupil actions.
        if (
            not practice_hours
            and not _proven_theory_object(clause)
            and _theory_knowledge_reconstruction(clause) is None
        ):
            uncovered.append(clause)
            continue
        if re.match(r"(?i)^(?:измеряет|строит|ремонтирует|оценивает|изготавливает)\s+"
                    r"(?:[а-яё-]+(?:ых|их)\b|[а-яё-]+(?:ушек|аря)\b)", local.planned_result):
            uncovered.append(clause)
            continue
        if local.planned_result not in results:
            results.append(local.planned_result)
        # Retain the proven finite wording: re-inflecting the concatenated
        # objects can lose conditions or attach them to a different action.
        quoted = (
            ("Устный опрос" if not practice_hours else "Педагогическое наблюдение")
            + ": проверяется действие "
            + _format_control_action_quote(local.planned_result)
        )
        # An oral check names the area it asks about. The quoted RESULT stays
        # only where the object case of that area is not proven.
        control = quoted
        if not practice_hours:
            areas = [
                _oral_object_for_control(obj)
                for _verb, obj in _result_control_segments(local.planned_result)
                if obj
            ]
            if areas and all(areas):
                control = "Устный опрос по: " + _join_and(areas)
        if control not in controls:
            controls.append(control)
        # A successful local repair may keep only one coordinated operation.
        # Do not certify the original clause as fully covered in that case.
        if (
            _coordinated_action_uncovered(clause, local.planned_result)
            or _ways_catalogue_uncovered(clause, local.planned_result)
            or (
                not _catalogue_label_dosage_preserved(clause, local.planned_result)
                if _homogeneous_exercise_catalogue(clause) is not None
                else _list_member_uncovered(clause, local.planned_result)
            )
        ):
            uncovered.append(clause)
    if retained and not retained_added:
        results.insert(0, original.planned_result)
        controls.insert(0, original.assessment_method)
    if retained:
        uncovered = [c for c in uncovered if not _retained_complement_covered(c, original)]
    warnings = tuple(w for w in original.warnings if not w.startswith("Безопасный шаблон CE2:"))
    provenance_codes = _drop_generic_only(original.provenance_codes)
    warnings += tuple("NEEDS_REVIEW: не подтверждено полное покрытие клаузы: " + c for c in uncovered)
    merged_result = _merge_independent_part_results(results)
    assessment = _unified_process_performance_control(
        merged_result, _join_control_clauses(controls)
    )
    if _control_has_multisentence_quotes(assessment):
        rebuilt = _quoted_actions_control(_fold_week_result(merged_result))
        if rebuilt:
            assessment = rebuilt
    assessment = _prefer_quoted_control_if_incomplete(
        _fold_week_result(merged_result), assessment
    )
    return replace(
        original,
        planned_result=merged_result,
        assessment_method=assessment,
        warnings=tuple(dict.fromkeys(warnings)),
        provenance_codes=provenance_codes,
        type_result=original.planned_result,
        clause_coverage=tuple(
            (c, "NEEDS_REVIEW" if c in uncovered else "COVERED") for c in clauses
        ),
    )


# Accusative proven by the form itself: neuter -ние/-тие/-ствие/-ье repeats the
# nominative, feminine -а/-я is already realised as -у/-ю. Animacy stays unknown
# for bare masculine and plural heads, so those remain unproven.
_PROVEN_NEUTER_ACC_RE = re.compile(r"(?i)(?:ние|тие|ствие|ье|ьё)$")
_NEUTER_DATIVE_RE = re.compile(r"(?i)(?:нию|тию|стию)$")
_NOMINATIVE_FEMININE_ADJ_RE = re.compile(r"(?i)(?:ая|яя)$")
_ABSTRACT_QUALITY_RE = re.compile(r"(?i)(?:ость|ости)$")


def _proven_feminine_acc_form(low: str) -> bool:
    """-у/-ю that can only be a feminine accusative, not a neuter dative."""

    if low.endswith("ию"):
        return not _NEUTER_DATIVE_RE.search(low)
    if low.endswith("у"):
        return _regular_feminine_a_noun(low[:-1] + "а")
    return False


def _proven_accusative_noun(word: str) -> bool:
    core = _strip_punct_word(word)[1].casefold()
    if len(core) < 4:
        return False
    if _is_theory_knowledge_token(word):
        return True
    if _PROVEN_NEUTER_ACC_RE.search(core):
        return True
    if _ABSTRACT_QUALITY_RE.search(core):
        # The -ость suffix builds abstract nouns: inanimate, so the accusative
        # repeats the nominative in both numbers.
        return True
    # Neuter plural -а keeps nominative form in the accusative (правила, средства).
    if _neuter_plural_nom_a(core):
        return True
    return _proven_feminine_acc_form(core)


def _knowledge_coordinated_head(tokens: list[str], head: int) -> bool:
    """An unproven head coordinated with a proven knowledge noun of one group."""

    core = _strip_punct_word(tokens[head])[1]
    if _NOMINATIVE_FEMININE_ADJ_RE.search(core) or _regular_feminine_a_noun(core.casefold()):
        # A nominative form cannot head an accusative object group.
        return False
    for position in range(head + 1, len(tokens) - 1):
        if _is_preposition(tokens[position]):
            return False
        if _strip_punct_word(tokens[position])[1].casefold() != "и":
            continue
        following = tokens[position + 1]
        if _is_theory_knowledge_token(following) and _proven_accusative_noun(following):
            return True
    return False


def _proven_characterize_object(sentence: str) -> bool:
    """Object case proven by its own form, not by a closed list of lemmas."""

    tokens = _normalize_spaces(sentence).split()[1:]
    index = 0
    seen_modifier = False
    while index < len(tokens):
        if _is_adjective(tokens[index]) and not _proven_accusative_noun(tokens[index]):
            # A nominative feminine modifier cannot agree with an accusative head.
            if _NOMINATIVE_FEMININE_ADJ_RE.search(_strip_punct_word(tokens[index])[1]):
                return False
            seen_modifier = True
            index += 1
            continue
        if (
            seen_modifier
            and _strip_punct_word(tokens[index])[1].casefold() in {"и", "или"}
            and index + 1 < len(tokens)
            and _is_adjective(tokens[index + 1])
        ):
            index += 1
            continue
        break
    if index >= len(tokens):
        return False
    if not _proven_accusative_noun(tokens[index]) and not _knowledge_coordinated_head(
        tokens, index
    ):
        return False
    return not _unproven_object_conjunct(tokens, index)


def _unproven_object_conjunct(tokens: list[str], head: int) -> bool:
    """An «и» conjunct that cannot carry the case of the object group."""

    head_core = _strip_punct_word(tokens[head])[1].casefold()
    head_fem_acc = head_core.endswith(("у", "ю"))
    for position in range(head + 1, len(tokens) - 1):
        if _is_preposition(tokens[position]):
            # A prepositional complement ends the object group.
            return False
        if _strip_punct_word(tokens[position])[1].casefold() != "и":
            continue
        following = tokens[position + 1]
        if _is_adjective(following) or _proven_accusative_noun(following):
            continue
        # Soft inanimate feminine after a feminine accusative head
        # («одежду и обувь»): accusative equals nominative.
        follow_core = _strip_punct_word(following)[1].casefold()
        if head_fem_acc and follow_core.endswith("ь") and len(follow_core) >= 4:
            continue
        if position == head + 1:
            # Directly coordinated with the object: the case must be proven.
            return True
        if _regular_feminine_a_noun(_strip_punct_word(following)[1].casefold()):
            # A nominative -а cannot continue the accusative object group.
            return True
    return False


def _oblique_source_conjunct(source: str, tail: str) -> bool:
    """The tail repeats a source conjunct still governed by a preposition."""

    words = _normalize_spaces(source).split()
    for index, word in enumerate(words):
        if _strip_punct_word(word)[1].casefold() != tail:
            continue
        for earlier in reversed(words[:index]):
            prefix, _core, suffix = _strip_punct_word(earlier)
            if prefix or suffix:
                # Punctuation closes the coordinated group.
                break
            if _is_preposition(earlier):
                return True
    return False


def _proven_object_prefix(sentence: str, source: str) -> str:
    """Proven part of an object group whose coordinated tail stayed oblique."""

    tokens = _normalize_spaces(sentence).split()
    for position in range(2, len(tokens) - 1):
        if _strip_punct_word(tokens[position])[1].casefold() != "и":
            continue
        tail = _strip_punct_word(tokens[position + 1])[1].casefold()
        if not _oblique_source_conjunct(source, tail):
            return ""
        prefix = " ".join(tokens[:position]).rstrip(" ,;") + "."
        return "" if _result_grammar_issue(prefix) else prefix
    return ""


def _result_grammar_issue(sentence: str) -> str:
    """Conservative case evidence, not a suffix-based grammar repair."""
    words = re.findall(r"[а-яё-]+", sentence.casefold())
    if not words:
        return ""
    if words[0] == "объясняет":
        if not _explained_knowledge_subject(sentence):
            return "unproven_knowledge_explanation"
    if words[0] in {"характеризует", "раскрывает"}:
        # A nominal topic is not proof of accusative case or animacy, but a
        # morphologically proven object form does not need a lemma list.
        if not _proven_characterize_object(sentence):
            return "unproven_knowledge_object_case"
    # Plural genitive adjectives cannot agree with a singular -а/-я noun.
    # Do not try to guess an animate plural or repair it by endings.
    if re.search(r"(?i)\b[а-яё-]+(?:ых|их)\s+[а-яё-]{3,}[ая]\b", sentence):
        return "unproven_number_agreement"
    # A prepositional adjunct does not license a genitive direct object.
    if re.match(
        r"(?i)^(?:строит|измеряет|изготавливает|ремонтирует|оценивает)\s+"
        r"(?:(?:на|в|по)\s+[а-яё-]+\s+)?[а-яё-]+(?:ых|их)\s+[а-яё-]+(?:ов|ев|ей)\b",
        sentence,
    ):
        return "unproven_direct_object_case"
    # Mixed cases in a coordinated object are outside the supported grammar.
    if re.match(r"(?i)^(?:изготавливает|строит|составляет)\b", sentence) and re.search(
        r"(?i),\s+[а-яё-]+(?:ок|ов|ев)\s*[,;]", sentence,
    ):
        return "unproven_coordinated_object_case"
    return ""


def _knowledge_result_object(sentence: str) -> str:
    text = _normalize_spaces(sentence).strip()
    explained = _explained_knowledge_subject(text)
    if explained:
        return explained
    return re.sub(r"(?i)^(?:характеризует|раскрывает)\s+", "", text).rstrip(".")


def _knowledge_cite_key(text: str) -> str:
    return _normalize_spaces(text).strip(" .:;").casefold().replace("ё", "е")


def _normalize_governed_verbal_noun(text: str) -> str:
    """Repair a verbal noun in -нии after an adjacent direct nominal head."""

    return re.sub(
        r"(?i)\b([а-яё-]+а)\s+([а-яё-]+(?:ании|янии|ении))\b",
        lambda match: f"{match.group(1)} {match.group(2)[:-1]}я",
        text or "",
    )


def _feminine_acc_citation(text: str) -> str:
    """Regular feminine nom -а/-я → acc -у/-ю on the first word only."""

    tokens = _normalize_spaces(text).split()
    if not tokens:
        return ""
    prefix, core, suffix = _strip_punct_word(tokens[0])
    if not _regular_feminine_a_noun(core.casefold()):
        return ""
    tokens[0] = f"{prefix}{_noun_nom_to_acc(core)}{suffix}"
    return _normalize_spaces(" ".join(tokens))


def _knowledge_result_cites_clause(sentence: str, clause: str) -> bool:
    """True when RESULT restates the whole covered NP, not a newly cased object.

    Animacy of masculine/plural heads stays unknown in the form-only gate.
    Restating the entire assigned clause (or its regular feminine accusative)
    adds no case of our own. A heading taken from above a colon catalogue is
    not such a citation: it silently drops the enumeration it governs.
    """

    if re.search(r"(?i)по теме", sentence):
        return False
    explained = _explained_knowledge_subject(sentence)
    if explained:
        source = _normalize_spaces(clause).strip(" .?")
        question = re.fullmatch(r"(?i)что\s+такое\s+(.+)", source)
        expected = question.group(1) if question is not None else source
        return _knowledge_cite_key(explained) == _knowledge_cite_key(expected)
    obj = _knowledge_cite_key(_knowledge_result_object(sentence))
    if len(obj) < 4:
        return False
    head = _normalize_governed_verbal_noun(
        _normalize_spaces(clause).strip(" .")
    )
    cite = _knowledge_cite_key(head)
    if not cite:
        return False
    variants = [cite]
    acc = _knowledge_cite_key(_feminine_acc_citation(head))
    proven = _proven_theory_object(head)
    if proven:
        variants.append(_knowledge_cite_key(proven))
    for variant in dict.fromkeys(item for item in variants if item):
        if obj == variant:
            return True
        # A compact knowledge sentence keeps each independently covered clause
        # as a complete ordered list item under one shared predicate.
        if re.search(
            rf"(?:^|,\s+|\s+и\s+){re.escape(variant)}(?:$|,\s+|\s+и\s+)",
            obj,
        ):
            return True
        tokens = variant.split()
        if len(tokens) > 1 and _common_semantic_head(variant):
            shared_head = tokens[0]
            factored_tail = " ".join(tokens[1:])
            head_at = re.search(rf"(?:^|\s){re.escape(shared_head)}(?:\s|$)", obj)
            tail_at = re.search(
                rf"(?:^|,\s+|\s+и\s+){re.escape(factored_tail)}(?:$|,\s+|\s+и\s+)",
                obj,
            )
            if head_at and tail_at and head_at.start() < tail_at.start():
                return True
    if len(cite) >= 8 and (
        obj.startswith(cite + ",")
        or obj.startswith(cite + " ")
        or f"характеризует {cite}" in obj
    ):
        return True
    if bool(acc) and (
        obj == acc or obj.startswith(acc + ",") or obj.startswith(acc + " ")
    ):
        return True
    # A grammar-only ending repair may stop being a literal SOURCE citation.
    # It is still the same object only when every non-adjectival meaning stem
    # from that clause remains present in the accepted knowledge object.
    stems = _meaning_stems(head)
    return bool(stems) and all(stem[:4] in obj for stem in stems)


# Instrumental is the case of an activity complement, not of a knowledge field.
_INSTRUMENTAL_COMPLEMENT_RE = re.compile(r"(?i)(?:ью|ами|ями)$")


def _admissible_knowledge_object(obj: str) -> bool:
    """Knowledge object without government CE2 has not converted into an action.

    An instrumental dependent names what the activity is carried out with;
    quoting it under «характеризует» would state a relation, not a field of
    knowledge, so such an object is not admissible for a citation.
    """

    for token in _normalize_spaces(obj).split():
        core = _strip_punct_word(token)[1].casefold()
        if len(core) > 4 and _INSTRUMENTAL_COMPLEMENT_RE.search(core):
            return False
    return True


def _safe_operation_result(clause: str, *, practical: bool) -> str:
    """Keep the source's nominal government for two approved operations."""
    text = _normalize_spaces(clause).strip(" .")
    if not practical or not re.match(r"(?i)^(?:изготовление|построение)\s+\S", text):
        return ""
    if re.match(r"(?i)^(?:изготовление|построение)\s+(?:и|или)\b", text):
        return ""
    # A bare action noun does not override a prohibition, another actor,
    # hypothetical description, or coordinated/embedded independent clause.
    if re.search(
        r"(?i)\b(?:не|нельзя|запрещ\w*|недопуст\w*|педагог\w*|учител\w*|"
        r"инструктор\w*|тренер\w*|родител\w*|демонстр\w*|показ\w*|"
        r"наблюд\w*|теори\w*|если|возможно|может|допуска\w*)\b|[;!?]", text,
    ):
        return ""
    if re.search(r"(?i)\bи\s+(?:" + "|".join(map(re.escape, _VERBAL_NOUN_TO_VERB)) + r")\b", text):
        return ""
    return "Выполняет " + text[0].lower() + text[1:] + "."


def _sync_control_after_result_gate(
    control: str,
    replacements: dict[str, str],
) -> str:
    """Quoted CONTROL must cite the gated RESULT wording, not the rejected form.

    The grammar gate may replace «строит на бумаге заданных …» with
    «выполняет построение на бумаге заданных …»; CONTROL quotes built before
    the gate still named the unsafe finite form.
    """

    text = _normalize_spaces(control)
    if not text or not replacements:
        return text
    for old, new in replacements.items():
        old_core = _normalize_spaces(old).rstrip(".")
        new_core = _normalize_spaces(new).rstrip(".")
        if not old_core or old_core.casefold() == new_core.casefold():
            continue
        text = re.sub(re.escape(old_core), new_core, text, flags=re.IGNORECASE)
    return text


def _apply_result_grammar_gate(
    original: ContentEngineV2Result,
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
) -> ContentEngineV2Result:
    """Drop or repair RESULT sentences that fail the grammar gate."""

    predicates = "|".join(re.escape(v) for v in _proven_finite_predicates())
    sentences = re.split(
        rf"(?i)(?<=[.!?])\s+(?=(?:{predicates})\b)",
        original.planned_result,
    )
    rejected = [s for s in sentences if _result_grammar_issue(s)]
    if not rejected:
        return original
    replacements: dict[str, str] = {}
    restored: set[str] = set()
    for clause, status in original.clause_coverage:
        safe = _safe_operation_result(
            clause,
            practical=bool(practice_hours and _nonempty_result_in(clause, practice_text)),
        )
        if status != "COVERED" or not safe:
            continue
        proof = _derive_selected_fields_v2(
            topic_title=topic_title,
            theory_text="",
            practice_text=clause,
            program_content=clause,
            practice_hours=practice_hours,
        )
        for sentence in rejected:
            if _normalize_spaces(proof.planned_result).casefold() == _normalize_spaces(
                sentence
            ).casefold():
                replacements[sentence] = safe
                restored.add(clause)
    for sentence in rejected:
        if sentence in replacements:
            continue
        if _result_grammar_issue(sentence) != "unproven_knowledge_object_case":
            continue
        verb = _leading_finite_verb(sentence)
        cited = [
            clause
            for clause, status in original.clause_coverage
            if status == "COVERED"
            and _knowledge_result_cites_clause(sentence, clause)
            and (
                (obj := (
                    _proven_theory_object(clause)
                    or _feminine_acc_citation(clause)
                    or _normalize_spaces(clause).strip(" .")
                ))
                and _knowledge_object_safe_to_fold(
                    f"{verb} {obj}.", verb, obj
                )
            )
        ]
        if cited:
            replacements[sentence] = sentence
            restored.update(cited)
    source_text = _normalize_spaces(f"{theory_text} {program_content}")
    knowledge_source = any(_is_theory_knowledge_token(word) for word in source_text.split())
    for sentence in rejected if knowledge_source else ():
        if sentence in replacements:
            continue
        if _result_grammar_issue(sentence) != "unproven_knowledge_object_case":
            continue
        trimmed = _proven_object_prefix(sentence, source_text)
        if trimmed:
            replacements[sentence] = trimmed
    retained = " ".join(
        replacements.get(s, s) for s in sentences if s not in rejected or s in replacements
    )
    coverage = []
    for clause, status in original.clause_coverage:
        if status == "COVERED" and clause not in restored:
            proof = _derive_selected_fields_v2(
                topic_title=topic_title,
                theory_text=clause if not practice_hours else "",
                practice_text=clause if practice_hours else "",
                program_content=clause,
                theory_hours=theory_hours,
                practice_hours=practice_hours,
            )
            proof_text = proof.planned_result.casefold().replace("раскрывает", "характеризует")
            retained_text = retained.casefold().replace("раскрывает", "характеризует")
            if not _nonempty_result_in(proof_text, retained_text):
                status = "NEEDS_REVIEW"
        coverage.append((clause, status))
    warnings = list(original.warnings)
    if any(s not in replacements for s in rejected):
        warnings.append(
            "NEEDS_REVIEW: грамматическая безопасность результата не доказана; SOURCE: "
            + original.frame.clause
        )
    for clause, status in coverage:
        if status == "NEEDS_REVIEW":
            warnings.append(
                "NEEDS_REVIEW: грамматическая безопасность результата не доказана; SOURCE: "
                + clause
            )
    assessment = _sync_control_after_result_gate(original.assessment_method, replacements)
    return replace(
        original,
        planned_result=retained,
        assessment_method=assessment,
        type_result=original.type_result
        if original.type_result is not None
        else original.planned_result,
        clause_coverage=tuple(coverage),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _meaning_stems(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[а-яё]{4,}", _normalize_spaces(text).casefold())
        if token not in _PREPOSITIONS and not _is_adjective(token)
    ]


def _clause_meaning_preserved_in_result(
    clause: str,
    result: str,
    *,
    topic_title: str,
    theory_hours: int,
    practice_hours: int,
) -> bool:
    """COVERED requires accepted RESULT to keep the clause's distinctive meaning."""

    folded = _normalize_spaces(result).casefold()
    if not folded:
        return False
    catalogue = _homogeneous_exercise_catalogue(clause)
    if catalogue is not None:
        _label, full_source, _dosages = catalogue
        if _catalogue_label_dosage_preserved(clause, result):
            return True
        # No LABEL+DOSAGE shortcut for missing or conflicting dosages. A fully
        # retained catalogue still preserves meaning through the ordinary path.
        return full_source.casefold() in folded
    for sentence in _result_sentences(result):
        if _knowledge_result_cites_clause(sentence, clause):
            return True
    proof = _derive_selected_fields_v2(
        topic_title=topic_title,
        theory_text=clause if not practice_hours else "",
        practice_text=clause if practice_hours else "",
        program_content=clause,
        theory_hours=theory_hours if not practice_hours else 0,
        practice_hours=practice_hours,
    ).planned_result
    if proof.strip():
        obj = _drop_leading_verb(proof)
        stems = _meaning_stems(obj) or _meaning_stems(proof)
        if stems and any(stem[:4] in folded for stem in stems):
            return True
        verb = _leading_finite_verb(proof).casefold()
        if verb and not stems:
            return verb in folded
    # Fall back to source stems when local proof is empty but RESULT still cites them.
    stems = _meaning_stems(clause)
    return bool(stems) and any(stem[:4] in folded for stem in stems)


def _control_covers_all_result_items(result: str, control: str) -> bool:
    """Every accepted RESULT obligation must be checkable from CONTROL."""

    result = _normalize_spaces(result)
    control = _normalize_spaces(control)
    if not result.strip():
        return True
    if not control.strip():
        return False
    control_low = control.casefold()
    # Compact named-form controls already selected from RESULT (викторина, диктант).
    sentences = [s for s in _result_sentences(result) if _leading_finite_verb(s)]
    if len(sentences) <= 1:
        named = _named_form_control(result, ActionFrame("", "", "", ""), "")
        if named and named.casefold() == control_low:
            return True
    obligations = _control_result_obligations(result)
    if not obligations:
        obligations = [(verb, obj) for verb, obj in _result_control_segments(result) if obj]
    if not obligations:
        return True
    for verb, obj in obligations:
        if verb.casefold() in _KNOWLEDGE_RESULT_VERBS:
            if _oral_object_grounded_in_result(control, result):
                continue
            stems = _meaning_stems(obj)
            if stems and any(stem[:4] in control_low for stem in stems):
                continue
            if _control_covers_operation(control, verb, obj):
                continue
            return False
        if _control_covers_operation(control, verb, obj):
            continue
        # Process CONTROL may name the activity without repeating the finite verb.
        activity_tokens = re.findall(r"[а-яё]{4,}", obj.casefold()) or re.findall(
            r"[а-яё]{4,}", verb.casefold()
        )
        if activity_tokens and any(token[:4] in control_low for token in activity_tokens):
            continue
        return False
    return True


def _rebuild_control_from_accepted_result(
    result: str,
    control: str,
    *,
    lesson_type: str,
    theory_hours: int,
    practice_hours: int,
) -> str:
    """CONTROL may only cite accepted RESULT items; keep valid oral/named forms."""

    if not _normalize_spaces(result).strip():
        return _normalize_spaces(control)
    repeated_oral = len(
        re.findall(r"(?i)\bустный опрос(?:\s+по|:)", control)
    ) > 1
    if _control_covers_all_result_items(result, control) and not repeated_oral:
        return control
    product = _product_control(result)
    if product and _control_covers_all_result_items(result, product):
        return product
    # Named-form CONTROL is valid only for a single named activity RESULT.
    sentences = [s for s in _result_sentences(result) if _leading_finite_verb(s)]
    if len(sentences) <= 1:
        named = _named_form_control(result, ActionFrame("", "", "", ""), lesson_type)
        if named and (
            not control.strip()
            or named.casefold() == control.casefold()
            or named.casefold() in control.casefold()
        ):
            return named
    rebuilt = _control_from_proven_result(result, lesson_type=lesson_type)
    if rebuilt and _control_covers_all_result_items(result, rebuilt):
        return rebuilt
    aligned = _align_control_to_result(control or rebuilt, result)
    if aligned and _control_covers_all_result_items(result, aligned):
        return aligned
    # Keep a non-empty process CONTROL that already names the RESULT activity.
    if control.strip():
        result_stems = _meaning_stems(_drop_leading_verb(result)) or _meaning_stems(result)
        if result_stems and any(stem[:4] in control.casefold() for stem in result_stems):
            return control
    if practice_hours and not theory_hours:
        quoted = _quoted_actions_control(result)
        if quoted and _control_covers_all_result_items(result, quoted):
            return quoted
    # Append only missing action obligations; leave oral heads intact.
    text = _normalize_spaces(aligned or rebuilt or control)
    for verb, obj in _control_result_obligations(result):
        if verb.casefold() in _KNOWLEDGE_RESULT_VERBS:
            continue
        if _control_covers_operation(text, verb, obj):
            continue
        piece = _control_coverage_piece(text, verb, obj)
        text = f"{text}; {piece}" if text else piece
    return text


def _try_recover_clause_result(
    clause: str,
    *,
    topic_title: str,
    theory_hours: int,
    practice_hours: int,
) -> ContentEngineV2Result | None:
    """Return a quality-safe local RESULT for a SOURCE clause, or None."""

    if _bare_list_without_action(clause) or _prohibition_only_source(clause):
        return None
    if re.search(r"(?i)\b(?:педагог|учитель|инструктор|тренер)\b", clause):
        return None
    # Prefer the lane suggested by hours; try the other lane as fallback.
    orders: tuple[tuple[bool, bool], ...]
    if practice_hours and not theory_hours:
        orders = ((False, True),)
    elif theory_hours and not practice_hours:
        orders = ((True, False),)
    else:
        orders = ((False, True), (True, False))
    for theory_only, practical in orders:
        local = _derive_selected_fields_v2(
            topic_title=topic_title,
            theory_text=clause if theory_only else "",
            practice_text=clause if practical else "",
            program_content=clause,
            theory_hours=max(1, theory_hours) if theory_only else 0,
            practice_hours=max(1, practice_hours) if practical else 0,
        )
        if (
            not local.planned_result
            or not local.assessment_method
            or local.warnings
            or "по теме" in local.planned_result.casefold()
            or "по теме" in local.assessment_method.casefold()
            or _quality_issue(
                local.planned_result,
                local.assessment_method,
                source=clause,
                clause=local.frame.clause,
            )
        ):
            continue
        if (
            not practice_hours
            and theory_only
            and not _proven_theory_object(clause)
            and _theory_knowledge_reconstruction(clause) is None
        ):
            continue
        return local
    return None


def _clause_has_pupil_action(text: str) -> bool:
    """Positive evidence that the clause names a pupil action/process."""

    cleaned = _normalize_spaces(text)
    if not cleaned:
        return False
    if _CONTROL_RESULT_VERB_RE.search(cleaned):
        return True
    if _VERBAL_NOUN_FIND_RE.search(cleaned):
        return True
    tokens = cleaned.split()
    # Scan the clause: modifiers before the activity noun must not hide it
    # («Коллективные приседания», «Круговое ОФП: … выпрыгивание …»).
    if any(_token_is_pupil_activity(token) for token in tokens[:8]):
        return True
    # Quantity of repetitions/approaches marks a pupil perform unit.
    if _clause_has_performance_quantity(cleaned) and any(
        len(_token_core(token)) >= 4 for token in tokens
    ):
        return True
    if _locative_drawing_object(cleaned) is not None:
        return True
    if _semiotic_object_phrase(cleaned) is not None:
        return True
    return False


def _clause_is_knowledge_content(text: str) -> bool:
    """Independent knowledge/study content (not a bare example or list)."""

    cleaned = _normalize_spaces(text)
    if not cleaned or _bare_list_without_action(cleaned):
        return False
    if _knowledge_clause_result(cleaned, theory_only=True):
        return True
    if _knowledge_label_over_catalogue(cleaned):
        return True
    tokens = cleaned.split()
    if any(_is_theory_knowledge_token(token) for token in tokens[:4]):
        return True
    first = tokens[0] if tokens else ""
    lemma = _verbal_noun_lemma(first).casefold()
    core = _strip_punct_word(first)[1].casefold()
    if lemma in {
        "изучение",
        "знакомство",
        "характеристика",
        "анализ",
        "описание",
        "функция",
        "свойство",
        "особенность",
        "различие",
        "принцип",
        "правило",
        "закон",
        "роль",
        "вид",
        "тип",
        "назначение",
        "применение",
        "состав",
        "структура",
        "механизм",
        "процесс",
        "явление",
    } or core in {
        "функции",
        "свойства",
        "особенности",
        "различия",
        "принципы",
        "правила",
        "законы",
        "роли",
        "виды",
        "типы",
    }:
        return True
    return False


def _sibling_has_pupil_action(siblings: tuple[str, ...], clause: str) -> bool:
    """Action sibling for object/condition attachment — knowledge heads do not count."""

    for item in siblings:
        if item == clause:
            continue
        if _clause_has_pupil_action(item) and not _clause_is_knowledge_content(item):
            return True
    return False


def _is_metadata_source_clause(text: str) -> bool:
    low = _normalize_spaces(text).casefold().strip(" .:")
    if low in {"теория", "практика", "содержание", "тема"}:
        return True
    if re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:ч|час|часа|часов)?", low):
        return True
    # Organizational lesson-phase / assessment wrappers (not pupil content).
    if re.fullmatch(r"(?:вводн\w*\s+)?инструктаж", low):
        return True
    if re.match(r"(?:промежуточн\w*\s+|итоговая\s+)?аттестация\b", low):
        return True
    if re.match(r"зач[её]т\b", low):
        return True
    if re.match(r"итоговое\s+занятие\b", low):
        return True
    if re.match(r"подведение\s+итогов\b", low):
        return True
    if re.match(r"перспективы\s+(?:дальнейшего\s+)?обучен", low):
        return True
    return False


def _is_example_source_clause(text: str) -> bool:
    cleaned = _normalize_spaces(text)
    low = cleaned.casefold()
    if low.startswith("например") or low.startswith("к примеру"):
        return True
    # Whole clause is one quoted exemplar / name list.
    if re.fullmatch(r"[«\"].+[»\"]", cleaned):
        return True
    # Parenthetical exemplar payload with no governing action outside.
    if cleaned.startswith("(") and cleaned.endswith(")") and not _clause_has_pupil_action(
        cleaned.strip("()")
    ):
        return True
    return False


def _is_anaphoric_context_clause(text: str, previous: str | None) -> bool:
    """Positive CONTEXT signals: gloss/anaphora of already stated content."""

    cleaned = _normalize_spaces(text)
    low = cleaned.casefold()
    if not cleaned:
        return False
    if re.match(r"(?i)^(?:их|его|её|ее|такой|такая|такое|такие|данного|данной)\b", low):
        return True
    if re.match(r"(?i)^(?:то есть|т\.е\.|включая|в том числе|а именно)\b", low):
        return True
    # Short possessive gloss like «их смысл» after a knowledge clause.
    if previous and re.fullmatch(r"(?i)их\s+\w{3,20}", low):
        return True
    if previous and _clause_is_knowledge_content(previous) and re.fullmatch(
        r"(?i)(?:смысл|значение|содержание|назначение)\b.*", low
    ):
        # Standalone gloss head elaborating the prior knowledge NP.
        if not _clause_has_pupil_action(cleaned) and len(cleaned.split()) <= 4:
            return True
    return False


def _is_condition_fragment(text: str) -> bool:
    low = _normalize_spaces(text).casefold()
    if re.match(
        r"(?i)^(?:при|при помощи|с помощью|в зависимости|если|когда|без|не\b)",
        low,
    ):
        return True
    if re.fullmatch(r"\(?\d+\s*раз(?:а)?\)?", low):
        return True
    if re.match(r"(?i)^(?:педагог|учитель|инструктор|тренер|спортсмен|страховщик)\b", low):
        # Role of executor without its own finite pupil action.
        if not _clause_has_pupil_action(text):
            return True
    return False


def _is_object_fragment(text: str, *, has_sibling_action: bool) -> bool:
    if not has_sibling_action:
        return False
    cleaned = _normalize_spaces(text)
    # Never demote a standalone pupil action to OBJECT merely because a
    # sibling also has an action (rule: pupil action → REQUIRED_ACTION).
    if _clause_has_pupil_action(cleaned) or _clause_is_knowledge_content(cleaned):
        return False
    if _clause_has_performance_quantity(cleaned):
        return False
    if _is_example_source_clause(cleaned) or _bare_list_without_action(cleaned):
        return False
    tokens = cleaned.split()
    if not tokens or len(tokens) > 8:
        return False
    # Dependent NP / named object without its own predicate.
    first = tokens[0]
    if _is_preposition(first):
        return False
    return not bool(_CONTROL_RESULT_VERB_RE.search(cleaned))

def classify_source_clause(
    clause: str,
    *,
    previous: str | None = None,
    siblings: tuple[str, ...] = (),
    theory_hours: int = 0,
    practice_hours: int = 0,
) -> str:
    """Classify one SOURCE clause before derive. Never marks CONTEXT for inability."""

    text = _normalize_spaces(clause)
    if _is_metadata_source_clause(text):
        return METADATA
    if _is_example_source_clause(text):
        return EXAMPLE
    # Knowledge-label catalogues stay optional CATALOG.
    if _knowledge_label_over_catalogue(text) and not _explicit_pupil_perform_clause(text):
        return CATALOG
    # Bare practice enumerations without a governing perform stay CATALOG.
    # The same shape on the theory channel is REQUIRED_KNOWLEDGE (topic list).
    if _bare_list_without_action(text) and not _explicit_pupil_perform_clause(text):
        if _practice_channel_only(theory_hours=theory_hours, practice_hours=practice_hours):
            return CATALOG
        return REQUIRED_KNOWLEDGE
    if _is_anaphoric_context_clause(text, previous):
        return CONTEXT

    sibling_action = _sibling_has_pupil_action(siblings, clause)
    if _is_condition_fragment(text) and sibling_action:
        return REQUIRED_CONDITION
    if _is_object_fragment(text, has_sibling_action=sibling_action):
        return REQUIRED_OBJECT

    # Knowledge heads (строение, функции, значение…) are REQUIRED_KNOWLEDGE even
    # when morphology overlaps with verbal nouns; pupil perform lemmas stay actions.
    if _clause_is_knowledge_content(text):
        first = text.split()[0] if text.split() else ""
        perform = _nominal_activity_lemma(first) in _NOMINAL_PERFORM_LEMMAS
        study = _verbal_noun_lemma(first).casefold() in {
            "изучение",
            "знакомство",
            "характеристика",
            "анализ",
            "описание",
        }
        if _is_theory_knowledge_token(first) or (study and not perform):
            return REQUIRED_KNOWLEDGE
        if not _explicit_pupil_perform_clause(text):
            return REQUIRED_KNOWLEDGE
        if _theory_channel_only(theory_hours=theory_hours, practice_hours=practice_hours):
            return REQUIRED_KNOWLEDGE

    if _explicit_pupil_perform_clause(text) or _clause_has_pupil_action(text):
        if _theory_channel_only(
            theory_hours=theory_hours, practice_hours=practice_hours
        ) and not _explicit_pupil_perform_clause(text):
            return REQUIRED_KNOWLEDGE
        return REQUIRED_ACTION
    if _clause_is_knowledge_content(text):
        return REQUIRED_KNOWLEDGE

    # Fail closed for independent unknown content: required by hour channel.
    if practice_hours and not theory_hours:
        return REQUIRED_ACTION
    if theory_hours and not practice_hours:
        return REQUIRED_KNOWLEDGE
    if practice_hours:
        return REQUIRED_ACTION
    return REQUIRED_KNOWLEDGE


def classify_source_clauses(
    clauses: list[str] | tuple[str, ...],
    *,
    theory_hours: int = 0,
    practice_hours: int = 0,
) -> tuple[tuple[str, str], ...]:
    """Role map for the week's selected SOURCE clauses."""

    items = tuple(_normalize_spaces(clause) for clause in clauses if _normalize_spaces(clause))
    roles: list[tuple[str, str]] = []
    for index, clause in enumerate(items):
        previous = items[index - 1] if index else None
        role = classify_source_clause(
            clause,
            previous=previous,
            siblings=items,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        )
        roles.append((clause, role))
    return tuple(roles)


def _role_is_required(role: str) -> bool:
    return role in _REQUIRED_SOURCE_ROLES


def _selected_source_clauses(
    *,
    theory_text: str,
    practice_text: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int = 0,
    practice_appearance_count: int = 0,
) -> list[str]:
    """Independent SOURCE clauses of the assigned week/slot only."""

    if practice_hours and practice_appearance_count > 1 and practice_text.strip():
        units = _coalesce_activity_units(practice_units_from_text(practice_text))
        slots, _flags = assign_distributed_practice_slots(units, practice_appearance_count)
        if slots:
            selected = list(slots[min(occurrence_index, len(slots) - 1)])
            return [
                clause
                for clause in selected
                if clause.casefold().strip(" .:") not in {"теория", "практика"}
            ]
    if theory_hours and practice_hours:
        pooled = f"{theory_text}\n{practice_text}".strip()
    else:
        pooled = _week_result_source(
            theory_hours=theory_hours,
            practice_hours=practice_hours,
            theory_text=theory_text,
            practice_text=practice_text,
            program_content=program_content,
        )
    return [
        clause
        for clause in _clause_units(pooled)
        if clause.casefold().strip(" .:") not in {"теория", "практика"}
    ]


def _apply_semantic_completeness_gate(
    candidate: ContentEngineV2Result,
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int = 0,
    practice_appearance_count: int = 0,
) -> ContentEngineV2Result:
    """Universal FINAL gate: only REQUIRED_* SOURCE meaning may force BLOCK."""

    source_clauses = _selected_source_clauses(
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )
    clause_roles = classify_source_clauses(
        source_clauses,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    role_map = dict(clause_roles)
    prior_status = {
        clause: status
        for clause, status in candidate.clause_coverage
        if status in {"COVERED", "NEEDS_REVIEW", _OPTIONAL_COVERAGE_STATUS}
    }
    coverage_map = dict(prior_status)
    for clause in source_clauses:
        role = role_map.get(clause, REQUIRED_ACTION)
        if not _role_is_required(role):
            coverage_map[clause] = _OPTIONAL_COVERAGE_STATUS
        else:
            coverage_map.setdefault(clause, "NEEDS_REVIEW")

    result = candidate.planned_result
    control = candidate.assessment_method
    warnings = list(candidate.warnings)
    recovered_controls: list[str] = []

    for clause in source_clauses:
        role = role_map.get(clause, REQUIRED_ACTION)
        if not _role_is_required(role):
            # CONTEXT/EXAMPLE/CATALOG/METADATA stay in SOURCE; no RESULT obligation.
            coverage_map[clause] = _OPTIONAL_COVERAGE_STATUS
            continue
        prior = prior_status.get(clause)
        # Intentional NEEDS_REVIEW stays review: do not invent a RESULT.
        if prior == "NEEDS_REVIEW":
            coverage_map[clause] = "NEEDS_REVIEW"
            continue
        preserved = _clause_meaning_preserved_in_result(
            clause,
            result,
            topic_title=topic_title,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        )
        if prior == "COVERED" and preserved:
            coverage_map[clause] = "COVERED"
            continue
        if prior == "COVERED" and not preserved:
            coverage_map[clause] = "NEEDS_REVIEW"
            warnings.append(
                "NEEDS_REVIEW: обязательный смысл SOURCE не сохранён в принятом RESULT; SOURCE: "
                + clause
            )
            continue
        # prior is None: clause was absent from accounting — try one safe recovery.
        local = _try_recover_clause_result(
            clause,
            topic_title=topic_title,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        )
        if local is None:
            coverage_map[clause] = "NEEDS_REVIEW"
            continue
        if _nonempty_result_in(local.planned_result, result) or not _selected_proof_absent_from_result(
            local.planned_result, result
        ):
            coverage_map[clause] = "COVERED"
            continue
        recovered_controls.append(local.assessment_method)
        coverage_map[clause] = "COVERED"
        result = _merge_independent_part_results([result, local.planned_result])

    if recovered_controls:
        control = _join_control_clauses(
            [control, *recovered_controls] if control.strip() else recovered_controls
        )
        control = _unified_process_performance_control(result, control)
        if _control_has_multisentence_quotes(control):
            rebuilt = _quoted_actions_control(_fold_week_result(result))
            if rebuilt:
                control = rebuilt
        control = _prefer_quoted_control_if_incomplete(_fold_week_result(result), control)

    reconciled: list[tuple[str, str]] = []
    seen: set[str] = set()
    for clause in source_clauses:
        role = role_map.get(clause, REQUIRED_ACTION)
        if not _role_is_required(role):
            reconciled.append((clause, _OPTIONAL_COVERAGE_STATUS))
            seen.add(clause)
            continue
        status = coverage_map.get(clause, "NEEDS_REVIEW")
        if status == "COVERED" and not _clause_meaning_preserved_in_result(
            clause,
            result,
            topic_title=topic_title,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        ):
            status = "NEEDS_REVIEW"
            warnings.append(
                "NEEDS_REVIEW: обязательный смысл SOURCE не сохранён в принятом RESULT; SOURCE: "
                + clause
            )
        if status not in {"COVERED", "NEEDS_REVIEW"}:
            status = "NEEDS_REVIEW"
        reconciled.append((clause, status))
        seen.add(clause)
    for clause, status in candidate.clause_coverage:
        if clause in seen:
            continue
        role = role_map.get(clause)
        if role is not None and not _role_is_required(role):
            reconciled.append((clause, _OPTIONAL_COVERAGE_STATUS))
            continue
        status = status if status in {"COVERED", "NEEDS_REVIEW"} else "NEEDS_REVIEW"
        if status == "COVERED" and not _clause_meaning_preserved_in_result(
            clause,
            result,
            topic_title=topic_title,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        ):
            status = "NEEDS_REVIEW"
            warnings.append(
                "NEEDS_REVIEW: обязательный смысл SOURCE не сохранён в принятом RESULT; SOURCE: "
                + clause
            )
        reconciled.append((clause, status))

    if not reconciled and source_clauses:
        reconciled = []
        for clause in source_clauses:
            role = role_map.get(clause, REQUIRED_ACTION)
            reconciled.append(
                (
                    clause,
                    _OPTIONAL_COVERAGE_STATUS
                    if not _role_is_required(role)
                    else "NEEDS_REVIEW",
                )
            )

    control = _rebuild_control_from_accepted_result(
        result,
        control,
        lesson_type=candidate.lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    if result.strip() and not _control_covers_all_result_items(result, control):
        warnings.append("NEEDS_REVIEW: CONTROL не покрывает все принятые RESULT-items")
        control = _rebuild_control_from_accepted_result(
            result,
            "",
            lesson_type=candidate.lesson_type,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        )

    return replace(
        candidate,
        planned_result=result,
        assessment_method=control,
        clause_coverage=tuple(reconciled),
        clause_roles=clause_roles,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _derive_fields_before_final_gate(
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
    """SOURCE → derive → enrich → grammar. No fold and no final gate yet."""

    original = _derive_week_fields_v2(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )
    original = _enrich_result_with_title_complements(
        original,
        topic_title=topic_title,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    return _apply_result_grammar_gate(
        original,
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )


def _rebuild_control_before_final_gate(
    result: str,
    control: str,
    *,
    lesson_type: str,
    theory_hours: int,
    practice_hours: int,
) -> str:
    """Fold-aware CONTROL sync that still runs before the FINAL gate."""

    if _control_has_multisentence_quotes(control):
        rebuilt = _quoted_actions_control(result)
        if rebuilt:
            control = rebuilt
    control = _prefer_quoted_control_if_incomplete(result, control)
    control = _unified_process_performance_control(result, control)
    control = _rebuild_control_from_accepted_result(
        result,
        control,
        lesson_type=lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    compact = _semantic_labels_control(result)
    if (
        compact
        and _control_covers_all_result_items(result, compact)
        and (
            len(compact) < len(control)
            or _control_quotes_result_action(control)
            or "приложение" in control.casefold()
            or _control_requires_case_rebuild(control)
        )
    ):
        control = compact
    return control


def _source_spelling_restorations(text: str, source_context: str) -> str:
    """Restore a unique SOURCE case form changed by an unsafe final-letter guess."""

    source_words = re.findall(r"[А-Яа-яЁё-]+", source_context or "")
    by_stem: dict[str, list[str]] = {}
    for word in source_words:
        key = word.casefold().replace("ё", "е")[:-1]
        if len(key) >= 5:
            by_stem.setdefault(key, []).append(word)

    def restore(match: re.Match[str]) -> str:
        word = match.group(0)
        if not word.casefold().endswith("ии"):
            return word
        key = word.casefold().replace("ё", "е")[:-1]
        candidates = {
            candidate
            for candidate in by_stem.get(key, [])
            if candidate.casefold().replace("ё", "е") != word.casefold().replace("ё", "е")
            and candidate.casefold().endswith(("ия", "ий"))
        }
        preferred = {candidate for candidate in candidates if candidate.casefold().endswith("ия")}
        if len(preferred) == 1:
            candidates = preferred
        if len(candidates) != 1:
            return word
        candidate = next(iter(candidates))
        return _match_caps(word, candidate.casefold())

    return re.sub(r"[А-Яа-яЁё-]+", restore, text or "")


def _object_starts_in_proven_genitive(obj: str) -> bool:
    words = re.findall(r"[А-Яа-яЁё-]+", _normalize_spaces(obj))
    if not words:
        return False
    first = words[0].casefold()
    if first.endswith(("ых", "их")) and len(words) > 1:
        return True
    return first.endswith(("ов", "ев", "ёв", "ей", "ствий", "ений", "аний", "яний", "ок"))


def _normalize_factored_result_grammar(result: str, source_context: str) -> str:
    """Normalize only grammar proven by SOURCE or a direct-object frame.

    This deliberately runs after semantic factoring. Conditions, dosage tails,
    predicate groups and sentence order are retained verbatim.
    """

    if not _normalize_spaces(result).strip(" ."):
        return ""
    restored = _source_spelling_restorations(result, source_context)
    # A direct nominal head followed immediately by a verbal noun cannot govern
    # a prepositional -нии form. Keep the lexical stem and restore genitive -ния.
    restored = _normalize_governed_verbal_noun(restored)

    def fold_participation_heading(match: re.Match[str]) -> str:
        first_tail = _normalize_spaces(match.group(2))
        heading_tail = _normalize_spaces(match.group(3))
        first_stems = {stem[:4] for stem in _meaning_stems(first_tail)}
        heading_stems = {stem[:4] for stem in _meaning_stems(heading_tail)}
        if not first_stems or not first_stems.issubset(heading_stems):
            return match.group(0)
        return f"{match.group(1)}{heading_tail}:"

    restored = re.sub(
        r"(?i)(участвует\s+в\s+играх\s+на\s+)([^.!?:]{3,80})\s+"
        r"игры\s+на\s+([^:.;!?]{3,80}):",
        fold_participation_heading,
        restored,
    )
    sentences = _result_sentences(restored)
    normalized: list[str] = []
    for sentence in sentences:
        verb = _leading_finite_verb(sentence).casefold()
        if not verb or verb in _KNOWLEDGE_RESULT_VERBS or verb == "участвует":
            normalized.append(_cap_sentence(sentence.rstrip(".")))
            continue
        match = re.match(r"(?i)^([А-Яа-яЁё-]+)\s+(.+?)\.?$", sentence.strip())
        if match is None:
            normalized.append(_cap_sentence(sentence.rstrip(".")))
            continue
        source_obj = match.group(2)
        target_obj = source_obj
        if not _object_starts_in_proven_genitive(target_obj):
            conjunct = re.search(
                r"(?i)\s+и\s+(?=[а-яё-]+(?:ов|ев|ёв|ей|ствий|ений|аний|яний|ок)\b)",
                target_obj,
            )
            if conjunct is not None:
                tail = target_obj[conjunct.end() :]
                try:
                    target_obj = (
                        target_obj[: conjunct.end()]
                        + _inflect_object_phrase(tail, case="acc")
                    )
                except _UncertainGrammar:
                    pass
        if not _object_starts_in_proven_genitive(target_obj):
            phrase_obj = target_obj
            if verb == "рисует":
                phrase_obj = _object_phrase_to_acc(phrase_obj) or phrase_obj
            normalized.append(_cap_sentence(f"{match.group(1)} {phrase_obj}".rstrip(".")))
            continue
        try:
            obj = _inflect_object_phrase(target_obj, case="acc")
        except _UncertainGrammar:
            phrase_obj = target_obj
            if verb == "рисует":
                phrase_obj = _object_phrase_to_acc(phrase_obj) or phrase_obj
            normalized.append(_cap_sentence(f"{match.group(1)} {phrase_obj}".rstrip(".")))
            continue
        if verb == "рисует":
            obj = _object_phrase_to_acc(obj) or obj
        normalized.append(_cap_sentence(f"{match.group(1)} {obj}".rstrip(".")))
    return _normalize_spaces(" ".join(normalized))


def _blocking_grammar_issues(result: str, control: str, source_context: str) -> tuple[str, ...]:
    """Grammar errors that must not cross the immutable DOCX boundary."""

    issues: list[str] = []
    if _normalize_factored_result_grammar(result, source_context) != _normalize_spaces(result):
        issues.append("RESULT требует доказанной грамматической нормализации")
    if _control_requires_case_rebuild(control):
        issues.append("CONTROL содержит неподдерживаемое падежное управление")
    if _control_quotes_result_action(control):
        issues.append("CONTROL механически цитирует RESULT")
    return tuple(dict.fromkeys(issues))


def _apply_blocking_grammar_gate(
    candidate: ContentEngineV2Result,
    *,
    source_context: str,
) -> ContentEngineV2Result:
    """Demote required coverage when final RESULT/CONTROL grammar is unsafe."""

    issues = _blocking_grammar_issues(
        candidate.planned_result,
        candidate.assessment_method,
        source_context,
    )
    if not issues:
        return candidate
    role_map = dict(candidate.clause_roles)
    already_blocked = any(
        status == "NEEDS_REVIEW"
        and _role_is_required(role_map.get(clause, REQUIRED_ACTION))
        for clause, status in candidate.clause_coverage
    )
    demoted = False
    coverage_items: list[tuple[str, str]] = []
    for clause, status in candidate.clause_coverage:
        if (
            not already_blocked
            and not demoted
            and status == "COVERED"
            and _role_is_required(role_map.get(clause, REQUIRED_ACTION))
        ):
            status = "NEEDS_REVIEW"
            demoted = True
        coverage_items.append((clause, status))
    coverage = tuple(coverage_items)
    warnings = tuple(
        dict.fromkeys(
            (*candidate.warnings, *(f"NEEDS_REVIEW: blocking grammar gate: {issue}" for issue in issues))
        )
    )
    return replace(candidate, clause_coverage=coverage, warnings=warnings)


def _finalize_content_fields(
    candidate: ContentEngineV2Result,
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int = 0,
    practice_appearance_count: int = 0,
) -> ContentEngineV2Result:
    """Fold → rebuild CONTROL → FINAL semantic + grammar + CONTROL coverage gate.

    After this returns, TYPE/RESULT/CONTROL are immutable for the DOCX path.
    """

    folded_result = _fold_week_result(candidate.planned_result)
    result_for_gate = folded_result
    # Fold only compresses already-proven sentences. If compression makes the
    # object chain grammar-unsafe and demotes a previously COVERED clause, keep
    # the unfolded wording — fold must not invent NEEDS_REVIEW.
    if folded_result != candidate.planned_result:
        fold_probe = _apply_result_grammar_gate(
            replace(candidate, planned_result=folded_result),
            topic_title=topic_title,
            theory_text=theory_text,
            practice_text=practice_text,
            program_content=program_content,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        )
        prior = dict(candidate.clause_coverage)
        if any(
            status == "NEEDS_REVIEW" and prior.get(clause) == "COVERED"
            for clause, status in fold_probe.clause_coverage
        ):
            # Keep meaning, but still strip expanded ОФП catalogues.
            result_for_gate = _compress_exercise_catalogues_in_text(
                candidate.planned_result
            )
    else:
        result_for_gate = _compress_exercise_catalogues_in_text(result_for_gate)
    source_context = _normalize_spaces(
        f"{theory_text} {practice_text} {program_content}"
    )
    result_for_gate = _normalize_factored_result_grammar(
        result_for_gate,
        source_context,
    )
    control = _rebuild_control_before_final_gate(
        result_for_gate,
        candidate.assessment_method,
        lesson_type=candidate.lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    if _rc_verbosity_block_reasons(result_for_gate, control):
        # One safe repair pass before FINAL verbosity BLOCK.
        control = _quoted_actions_control(result_for_gate)
        control = _join_control_clauses([control]) if control else control
    prepared = replace(
        candidate,
        planned_result=result_for_gate,
        assessment_method=control,
    )
    # Grammar after fold: a fold that breaks proof demotes clauses to NEEDS_REVIEW.
    after_grammar = _apply_result_grammar_gate(
        prepared,
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    after_semantic = _apply_semantic_completeness_gate(
        after_grammar,
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )
    if (
        practice_hours
        and practice_appearance_count > 1
        and any(
            ambiguous_colon_object_catalog(unit, practice_appearance_count)
            for unit in practice_units_from_text(practice_text)
        )
    ):
        clauses = tuple(
            unit for unit in practice_units_from_text(practice_text)
            if unit.casefold().strip(" .:") not in {"теория", "практика"}
        )
        after_semantic = replace(
            after_semantic,
            clause_coverage=tuple((clause, "NEEDS_REVIEW") for clause in clauses),
            warnings=tuple(dict.fromkeys((
                *after_semantic.warnings,
                "NEEDS_REVIEW: неоднозначный каталог объектов не распределён по неделям.",
            ))),
        )

    # Semantic recovery can reintroduce a pre-factoring clause form. Normalize
    # that accepted RESULT once more, then rebuild CONTROL from the exact final
    # wording before the immutable blocking gate.
    final_result = _normalize_factored_result_grammar(
        after_semantic.planned_result,
        source_context,
    )
    final_control = _rebuild_control_before_final_gate(
        final_result,
        after_semantic.assessment_method,
        lesson_type=after_semantic.lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    after_semantic = replace(
        after_semantic,
        planned_result=final_result,
        assessment_method=final_control,
    )

    return _apply_blocking_grammar_gate(
        after_semantic,
        source_context=source_context,
    )


def derive_fields_v2(
    *, topic_title: str, theory_text: str, practice_text: str,
    program_content: str = "", theory_hours: int = 0, practice_hours: int = 0,
    occurrence_index: int = 0, practice_appearance_count: int = 0,
) -> ContentEngineV2Result:
    """Full CE2 path ending at the FINAL immutable content gate."""

    prepared = _derive_fields_before_final_gate(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )
    final = _finalize_content_fields(
        prepared,
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )
    return replace(
        final,
        lesson_type=_complete_week_lesson_type(
            final.lesson_type,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
            theory_text=theory_text,
            practice_text=practice_text,
            part_types=(prepared.lesson_type,),
        ),
    )


def week_has_unresolved_mandatory_review(row: LessonContentV2Row) -> bool:
    """True when a REQUIRED_* SOURCE-clause stayed NEEDS_REVIEW after FINAL gate."""

    role_map = dict(row.clause_roles)
    for clause, status in row.clause_coverage:
        if status != "NEEDS_REVIEW":
            continue
        role = role_map.get(clause, REQUIRED_ACTION)
        if _role_is_required(role):
            return True
    if (row.planned_result or "").strip() and not is_sentence_frame_closed_row(row):
        if not _control_covers_all_result_items(
            row.planned_result, row.assessment_method
        ):
            return True
    if _rc_verbosity_block_reasons(row.planned_result or "", row.assessment_method or ""):
        return True
    return False


def unresolved_mandatory_review_blocks(
    rows: tuple[LessonContentV2Row, ...],
) -> tuple[tuple[int, tuple[str, ...]], ...]:
    """Weeks that must block ready DOCX: unresolved REQUIRED_* NEEDS_REVIEW."""

    blocks: list[tuple[int, tuple[str, ...]]] = []
    for row in rows:
        if not week_has_unresolved_mandatory_review(row):
            continue
        role_map = dict(row.clause_roles)
        clauses = tuple(
            clause
            for clause, status in row.clause_coverage
            if status == "NEEDS_REVIEW"
            and _role_is_required(role_map.get(clause, REQUIRED_ACTION))
        )
        if (
            not clauses
            and (row.planned_result or "").strip()
            and not is_sentence_frame_closed_row(row)
            and not _control_covers_all_result_items(
                row.planned_result, row.assessment_method
            )
        ):
            clauses = ("CONTROL не покрывает финальный RESULT",)
        verbosity = _rc_verbosity_block_reasons(
            row.planned_result or "", row.assessment_method or ""
        )
        if verbosity:
            clauses = tuple(dict.fromkeys((*clauses, *verbosity)))
        if clauses or not (row.planned_result or "").strip():
            blocks.append((row.source.week_number, clauses))
    return tuple(blocks)


def format_unresolved_review_block_message(
    blocks: tuple[tuple[int, tuple[str, ...]], ...],
) -> str:
    """User-facing reason: which weeks remain incomplete after FINAL gate."""

    if not blocks:
        return ""
    parts: list[str] = []
    for week_number, clauses in blocks:
        sample = "; ".join(clauses[:3])
        if len(clauses) > 3:
            sample += f" … (+{len(clauses) - 3})"
        if sample:
            parts.append(f"неделя {week_number}: {sample}")
        else:
            parts.append(f"неделя {week_number}")
    return (
        "Календарный план не готов: после финального semantic gate остались "
        "незакрытые обязательные SOURCE-фрагменты (NEEDS_REVIEW). "
        "DOCX как готовый документ не выдаётся. "
        + " | ".join(parts)
    )


def validate_manual_lesson_content(
    row: LessonContentV2Row,
    *,
    planned_result: str,
    assessment_method: str,
) -> ManualContentValidation:
    """Apply existing immutable gates without deriving or rewriting text.

    A previous ``NEEDS_REVIEW`` is not copied into the manual candidate:
    every REQUIRED source clause is proved again against the exact entered
    RESULT.  Acceptance is evidence-based, never a boolean override.
    """

    result = (planned_result or "").strip()
    control = (assessment_method or "").strip()
    issues: list[str] = []
    if not result:
        issues.append("RESULT не заполнен")
    if not control:
        issues.append("CONTROL не заполнен")

    role_map = dict(row.clause_roles)
    coverage: list[tuple[str, str]] = []
    for clause, _prior_status in row.clause_coverage:
        role = role_map.get(clause, REQUIRED_ACTION)
        if not _role_is_required(role):
            coverage.append((clause, _OPTIONAL_COVERAGE_STATUS))
            continue
        preserved = bool(result) and _clause_meaning_preserved_in_result(
            clause,
            result,
            topic_title=row.source.topic_title,
            theory_hours=row.source.theory_hours,
            practice_hours=row.source.practice_hours,
        )
        coverage.append((clause, "COVERED" if preserved else "NEEDS_REVIEW"))
        if not preserved:
            issues.append(f"RESULT не сохраняет обязательный SOURCE: {clause}")
        if _r13_must_abstain_action_reconstruction(clause):
            issues.append(
                "R13: SOURCE требует fail-closed проверки и не допускает "
                "ручного подтверждения как положительного действия ученика"
            )

    candidate = ContentEngineV2Result(
        frame=ActionFrame(
            row.source.program_content_full,
            row.action,
            row.object,
            row.conditions,
        ),
        lesson_type=row.lesson_type,
        planned_result=result,
        assessment_method=control,
        theory_text=row.theory_text,
        practice_text=row.practice_text,
        warnings=(),
        clause_coverage=tuple(coverage),
        clause_roles=row.clause_roles,
    )
    if result:
        grammar_probe = _apply_result_grammar_gate(
            candidate,
            topic_title=row.source.topic_title,
            theory_text=row.theory_text,
            practice_text=row.practice_text,
            program_content=row.source.program_content_full,
            theory_hours=row.source.theory_hours,
            practice_hours=row.source.practice_hours,
        )
        if grammar_probe.planned_result != result or any(
            status == "NEEDS_REVIEW"
            and _role_is_required(role_map.get(clause, REQUIRED_ACTION))
            for clause, status in grammar_probe.clause_coverage
        ):
            issues.append("RESULT не прошёл существующий grammar gate")

    source_context = _normalize_spaces(
        f"{row.theory_text} {row.practice_text} {row.source.program_content_full}"
    )
    issues.extend(_blocking_grammar_issues(result, control, source_context))
    if result and control and not _control_covers_all_result_items(result, control):
        issues.append("CONTROL не покрывает все RESULT-items")
    issues.extend(_rc_verbosity_block_reasons(result, control))
    unique_issues = tuple(dict.fromkeys(issues))
    validated_row = replace(
        row,
        planned_result=result,
        assessment_method=control,
        clause_coverage=tuple(coverage),
        warnings=tuple(
            warning for warning in row.warnings if "NEEDS_REVIEW" not in warning
        ),
    )
    return ManualContentValidation(
        accepted=not unique_issues,
        row=validated_row,
        issues=unique_issues,
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
    match = re.match(
        r"(?i)^([А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют|ает|яет)(?:ся|сь)?)\b",
        text.strip(),
    )
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
    shapes = {_knowledge_result_shape(item) for item in unique}
    if all(verbs) and len({verb.casefold() for verb in verbs}) == 1 and len(shapes) <= 1:
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


_COMPACT_KNOWLEDGE_PREDICATES = frozenset({"характеризует", "называет"})


def _balanced_fold_object(text: str) -> bool:
    """Only self-contained objects may enter a shared predicate list."""

    pairs = (("(", ")"), ("[", "]"), ("{", "}"), ("«", "»"), ("„", "“"))
    for opening, closing in pairs:
        depth = 0
        for char in text:
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth < 0:
                    return False
        if depth:
            return False
    return text.count('"') % 2 == 0


_COMMON_SEMANTIC_HEAD_STEMS = (
    "упражнен",
    "игр",
    "техник",
    "лазан",
    "страхов",
)


def _strip_result_provenance_context(result: str) -> str:
    """Remove appendix references that are SOURCE provenance, not outcomes."""

    cleaned = re.sub(
        r"(?i)\(\s*(?:выполняет\s+)?приложение\s*№?\s*\d+\s*\)",
        "",
        result or "",
    )
    cleaned = re.sub(
        r"(?i)(?:^|(?<=[.!?])\s+)выполняет\s+приложение\s*№?\s*\d+\s*\.?",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"\s+([.!?])", r"\1", cleaned)
    cleaned = re.sub(r"(?:\.\s*){2,}", ". ", cleaned)
    return _normalize_spaces(cleaned).strip()


def _common_semantic_head(text: str) -> str:
    """Return only a grammar-backed head family allowed for factoring."""

    first = _normalize_spaces(text).split()[:1]
    if not first:
        return ""
    token = _strip_punct_word(first[0])[1].casefold().replace("ё", "е")
    return next((stem for stem in _COMMON_SEMANTIC_HEAD_STEMS if token.startswith(stem)), "")


def _unwrap_redundant_action_wrapper(verb: str, obj: str) -> str:
    """Drop only a proven action wrapper whose finite predicate already carries it."""

    normalized = _normalize_spaces(obj).strip(" ,.;")
    if verb.casefold() != "отрабатывает":
        return normalized
    match = re.fullmatch(
        r"(?i)техник[ауи]\s+выполнения\s+(упражнен\w+)(\s+.+)",
        normalized,
    )
    if match is None:
        return normalized
    # Under «Отрабатывает» the repeated nominal wrapper adds no independent
    # meaning. The exercise relation, object, condition and dosage stay intact.
    return _normalize_spaces("упражнения" + match.group(2))


def _safe_common_head_factor(verb: str, objects: list[str]) -> str | None:
    """Factor one homogeneous object run while retaining every ordered tail."""

    unique: list[str] = []
    seen: set[str] = set()
    for obj in objects:
        normalized = _unwrap_redundant_action_wrapper(verb, obj)
        key = normalized.casefold().replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
    if len(unique) < 2:
        return unique[0] if unique else None
    if any(
        not _balanced_fold_object(obj)
        or any(mark in obj for mark in ";:!?")
        or any(
            _is_action_finite_token(token)
            for token in re.findall(r"[А-Яа-яЁё]+", obj)
        )
        for obj in unique
    ):
        return None
    heads = [_common_semantic_head(obj) for obj in unique]
    if not heads[0] or len(set(heads)) != 1:
        return None
    dosed = [_action_item_dosage(obj) for obj in unique]
    if all(item is not None for item in dosed):
        # Dosage-bearing frames stay whole here. The dedicated frame compactor
        # below may merge exact labels, while different conditions/difficulties
        # retain an explicit action object next to every dosage.
        return None

    tokenized = [obj.split() for obj in unique]
    prefix_len = 0
    while all(len(tokens) > prefix_len for tokens in tokenized):
        if any("(" in tokens[prefix_len] or ")" in tokens[prefix_len] for tokens in tokenized):
            break
        values = {
            tokens[prefix_len].casefold().replace("ё", "е")
            for tokens in tokenized
        }
        if len(values) != 1:
            break
        prefix_len += 1
    if not prefix_len:
        return None
    # A shared preposition alone does not prove a shared relation:
    # «упражнения на развитие ... / на равновесие» keeps both full tails.
    while prefix_len > 1 and tokenized[0][prefix_len - 1].casefold() in _PREPOSITIONS:
        prefix_len -= 1

    suffix_len = 0
    while all(len(tokens) - prefix_len > suffix_len for tokens in tokenized):
        if any("(" in tokens[-1 - suffix_len] or ")" in tokens[-1 - suffix_len] for tokens in tokenized):
            break
        values = {
            tokens[-1 - suffix_len].casefold().replace("ё", "е")
            for tokens in tokenized
        }
        if len(values) != 1:
            break
        suffix_len += 1
    middles = [
        tokens[prefix_len : len(tokens) - suffix_len if suffix_len else None]
        for tokens in tokenized
    ]
    if any(not middle for middle in middles):
        return None

    prefix = " ".join(tokenized[0][:prefix_len])
    middle_text = _join_and([" ".join(middle) for middle in middles])
    suffix = " ".join(tokenized[0][-suffix_len:]) if suffix_len else ""
    if prefix_len == 1 and all(
        middle[0].casefold() in _PREPOSITIONS for middle in middles
    ):
        return _normalize_spaces(f"{prefix}: {middle_text} {suffix}")
    return _normalize_spaces(f"{prefix} {middle_text} {suffix}")


def _factor_common_head_object_runs(verb: str, objects: list[str]) -> list[str]:
    """Factor only consecutive objects sharing one proven semantic head."""

    factored: list[str] = []
    index = 0
    while index < len(objects):
        head = _common_semantic_head(
            _unwrap_redundant_action_wrapper(verb, objects[index])
        )
        end = index + 1
        while head and end < len(objects):
            following = _common_semantic_head(
                _unwrap_redundant_action_wrapper(verb, objects[end])
            )
            if following != head:
                break
            end += 1
        run = objects[index:end]
        compact = _safe_common_head_factor(verb, run) if len(run) > 1 else None
        if compact is not None:
            factored.append(compact)
        else:
            factored.extend(run)
        index = end
    return factored


def _render_predicate_objects(verb: str, objects: list[str]) -> str:
    compact = _factor_common_head_object_runs(verb, objects)
    if not compact:
        return ""
    if len(compact) == 1:
        return compact[0]
    return (
        ", ".join(compact)
        if any(" и " in item or "," in item for item in compact)
        else _join_and(compact)
    )


def _practice_object_safe_to_fold(obj: str) -> bool:
    """Reject punctuation whose scope cannot be proved after sentence joining."""

    return bool(obj) and _balanced_fold_object(obj) and not any(
        mark in obj for mark in ";:!?"
    )


def _knowledge_object_safe_to_fold(sentence: str, verb: str, obj: str) -> bool:
    """Prove one object independently before it shares a knowledge predicate."""

    normalized = _normalize_spaces(sentence).strip()
    if not obj or _leading_finite_verb(normalized).casefold() != verb.casefold():
        return False
    if "по теме" in normalized.casefold() or any(mark in obj for mark in ";:!?"):
        return False
    if not _balanced_fold_object(obj):
        return False
    if any(
        _is_action_finite_token(token)
        for token in re.findall(r"[А-Яа-яЁё]+", obj)
    ):
        return False
    issue = _result_grammar_issue(
        normalized if normalized.endswith(".") else normalized + "."
    )
    if not issue:
        return True
    if issue != "unproven_knowledge_object_case" or verb.casefold() != "характеризует":
        return False
    if not _admissible_knowledge_object(obj):
        return False
    # A SOURCE citation may legitimately have an inanimate nominative-looking
    # head whose accusative form is identical. Do not extend that allowance to
    # a clearly oblique head (e.g. «правилу»): the final SOURCE-aware grammar
    # gate will prove every retained list item separately.
    tokens = _normalize_spaces(obj).split()
    head = next((token for token in tokens if not _is_adjective(token)), "")
    core = _strip_punct_word(head)[1]
    if not core or _proven_feminine_acc_form(core):
        return bool(core)
    return not core.casefold().endswith(("ому", "ему", "ым", "им", "у", "ю"))


def _fold_safe_knowledge_predicate_runs(sentences: list[str]) -> list[str]:
    """Fold consecutive equal knowledge predicates, or keep the whole run."""

    folded: list[str] = []
    index = 0
    while index < len(sentences):
        first = sentences[index]
        verb = _leading_finite_verb(first)
        key = verb.casefold()
        if key not in _COMPACT_KNOWLEDGE_PREDICATES:
            folded.append(first)
            index += 1
            continue

        end = index + 1
        while end < len(sentences):
            following = _leading_finite_verb(sentences[end]).casefold()
            if following != key:
                break
            end += 1
        run = sentences[index:end]
        if len(run) < 2:
            folded.extend(run)
            index = end
            continue

        objects = [_drop_leading_verb(item).rstrip(" .") for item in run]
        if not all(
            _knowledge_object_safe_to_fold(item, verb, obj)
            for item, obj in zip(run, objects)
        ):
            folded.extend(run)
            index = end
            continue

        unique: list[str] = []
        seen: set[str] = set()
        for obj in objects:
            normalized = _normalize_spaces(obj)
            object_key = normalized.casefold().replace("ё", "е")
            if object_key in seen:
                continue
            seen.add(object_key)
            unique.append(normalized)
        listed = _render_predicate_objects(verb, unique)
        compact = _cap_sentence(f"{verb} {listed}") if listed else ""
        # The combined object chain must also pass; otherwise no sentence in
        # the run is changed, including exact duplicates.
        combined_issue = _result_grammar_issue(compact) if compact else "empty"
        if combined_issue not in {"", "unproven_knowledge_object_case"}:
            folded.extend(run)
        else:
            folded.append(compact)
        index = end
    return folded


def _fold_repeated_predicates(sentences: list[str]) -> list[str]:
    """One predicate per run of actions that share it; every object is kept.

    Practice verbs that list parallel objects may resume after another finite
    action («определяет … . выполняет … . определяет точку …»): those later
    objects join the first occurrence. Knowledge verbs such as «характеризует»
    stay consecutive-only so independent theory sentences are not reordered.
    """

    sentences = _fold_safe_knowledge_predicate_runs(sentences)
    resumable = frozenset(
        {
            "определяет",
            "измеряет",
            "составляет",
            "подготавливает",
            "выполняет",
            "отрабатывает",
            "изучает",
        }
    )
    # Knowledge predicates stay one sentence each: folding «характеризует A» +
    # «характеризует B» into «характеризует A и B» can break object-case proof.
    knowledge_separate = frozenset(
        {
            "характеризует",
            "раскрывает",
            "называет",
            "описывает",
            "объясняет",
            "перечисляет",
        }
    )
    folded: list[str] = []
    verb: str = ""
    objects: list[str] = []
    slots: dict[str, list[str]] = {}

    def flush() -> None:
        nonlocal verb, objects
        if objects:
            listed = _render_predicate_objects(verb, objects)
            folded.append(_cap_sentence(f"{verb} {listed}"))
            if verb:
                slots[verb.casefold()] = objects
        verb, objects = "", []

    for sentence in sentences:
        current = _leading_finite_verb(sentence)
        obj = _drop_leading_verb(sentence).rstrip(" .") if current else ""
        if not current or not obj or "по теме" in sentence.casefold():
            flush()
            folded.append(sentence)
            continue
        key = current.casefold()
        if key in knowledge_separate:
            flush()
            folded.append(
                sentence if sentence.endswith((".", "!", "?")) else f"{sentence}."
            )
            continue
        # A remnant that still hosts a finite pupil verb is its own sentence,
        # not an object list glued under the previous predicate.
        if any(
            _is_action_finite_token(token)
            for token in re.findall(r"[А-Яа-яЁё]+", obj)
        ):
            flush()
            folded.append(sentence if sentence.endswith((".", "!", "?")) else f"{sentence}.")
            continue
        if verb and verb.casefold() == key and (
            not _practice_object_safe_to_fold(obj)
            or any(not _practice_object_safe_to_fold(item) for item in objects)
        ):
            flush()
        # A finished colon enumeration must not absorb a later independent action.
        if (
            key in resumable
            and key in slots
            and _practice_object_safe_to_fold(obj)
            and not any(
                ":" in item or "," in item or " и " in item for item in slots[key]
            )
        ):
            prior = slots[key]
            if obj.casefold() not in {item.casefold() for item in prior}:
                prior.append(obj)
            # Rewrite the already folded sentence that owns this verb.
            for index, item in enumerate(folded):
                if _leading_finite_verb(item).casefold() == key:
                    listed = _render_predicate_objects(current, prior)
                    folded[index] = _cap_sentence(f"{current} {listed}")
                    break
            continue
        if verb and verb.casefold() != key:
            flush()
        verb = verb or current
        if obj.casefold() not in {item.casefold() for item in objects}:
            objects.append(obj)
    flush()
    return folded


def _result_sentences(result: str) -> list[str]:
    """Sentences that open a new action; an abbreviated word keeps its sentence."""

    sentences: list[str] = []
    for piece in re.split(r"(?<=[.!?])\s+", _normalize_spaces(result)):
        if sentences and not _leading_finite_verb(piece):
            sentences[-1] = f"{sentences[-1]} {piece}"
            continue
        sentences.append(piece)
    return sentences


def _split_week_action_items(text: str) -> list[str]:
    """Split a finite action object list without entering quotes or dosage parens."""

    parts: list[str] = []
    buf: list[str] = []
    paren_depth = 0
    quote_depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char in {"«", "„", '"'} and quote_depth == 0:
            quote_depth = 1
        elif char in {"»", "“", '"'} and quote_depth:
            quote_depth = 0
        if not quote_depth:
            if char == "(":
                paren_depth += 1
            elif char == ")":
                paren_depth = max(0, paren_depth - 1)
        if char == "," and not quote_depth and not paren_depth:
            item = _normalize_spaces("".join(buf)).strip(" ,")
            if item:
                parts.append(item)
            buf = []
            index += 1
            continue
        buf.append(char)
        index += 1
    tail = _normalize_spaces("".join(buf)).strip(" ,")
    if tail:
        parts.append(tail)

    expanded: list[str] = []
    for part in parts:
        split_at: int | None = None
        paren_depth = 0
        quote_depth = 0
        scanned_to = 0
        for match in re.finditer(r"(?i)\s+и\s+", part):
            for char in part[scanned_to : match.start()]:
                if char in {"«", "„", '"'} and quote_depth == 0:
                    quote_depth = 1
                elif char in {"»", "“", '"'} and quote_depth:
                    quote_depth = 0
                elif not quote_depth and char == "(":
                    paren_depth += 1
                elif not quote_depth and char == ")":
                    paren_depth = max(0, paren_depth - 1)
            left = part[: match.start()].strip()
            right = part[match.end() :].strip()
            if (
                not quote_depth
                and not paren_depth
                and _action_item_dosage(left) is not None
                and _action_item_dosage(right) is not None
            ):
                split_at = match.start()
            scanned_to = match.end()
        if split_at is None:
            expanded.append(part)
            continue
        conjunction = re.search(r"(?i)\s+и\s+", part[split_at:])
        assert conjunction is not None
        boundary = split_at + conjunction.end()
        expanded.extend((part[:split_at].strip(), part[boundary:].strip()))
    return [item for item in expanded if item]


def _action_item_dosage(item: str) -> tuple[str, str] | None:
    """Return the semantic label and one trailing dosage marker, or fail closed."""

    label, marker = _detach_trailing_parens(_normalize_spaces(item).strip(" ,.;"))
    marker = marker.strip()
    if not label or not marker:
        return None
    dosages = _dosage_markers(marker)
    if len(dosages) != 1 or _normalize_spaces(dosages[0]) != marker:
        return None
    return label, marker


def _homogeneous_action_label(text: str) -> str:
    """Canonical semantic label; conditions and difficulty remain part of it."""

    normalized = _normalize_spaces(text).strip(" ,.;").casefold().replace("ё", "е")
    return re.sub(
        r"[а-яе]+",
        lambda match: _nominal_activity_lemma(match.group(0)).replace("ё", "е"),
        normalized,
    )


def _dosage_unit_family(unit: str) -> str:
    folded = re.sub(r"[^а-яёa-z]", "", unit.casefold())
    for family in ("круг", "раз", "подход", "мин", "сек"):
        if folded.startswith(family):
            return family
    return ""


def _ordered_dosage_formula(markers: list[str]) -> str:
    """Keep every ordered unique dosage; share prefix/unit only when identical."""

    unique: list[str] = []
    seen: set[str] = set()
    for marker in markers:
        key = _normalize_dosage_marker(marker)
        if key in seen:
            continue
        seen.add(key)
        unique.append(_normalize_spaces(marker))
    if len(unique) <= 1:
        return unique[0] if unique else ""

    parsed: list[tuple[str, str, str, str]] = []
    for marker in unique:
        match = re.fullmatch(
            r"(?i)\(\s*(?:(по)\s+)?(\d+)\s+([^()]+?)\s*\)", marker
        )
        if match is None:
            parsed = []
            break
        prefix = "по" if match.group(1) else ""
        unit = match.group(3).strip()
        family = _dosage_unit_family(unit)
        if not family:
            parsed = []
            break
        parsed.append((prefix, match.group(2), unit, family))
    if parsed and len({(prefix, family) for prefix, _, _, family in parsed}) == 1:
        values = [value for _, value, _, _ in parsed]
        listed = ", ".join(values[:-1]) + " и " + values[-1]
        prefix = f"{parsed[0][0]} " if parsed[0][0] else ""
        return f"({prefix}{listed} {parsed[-1][2]})"

    inner = [marker[1:-1].strip() for marker in unique]
    return "(" + "; ".join(inner) + ")"


def _compact_repeated_homogeneous_action_frames(result: str) -> str:
    """Merge exact action+label duplicates and retain all ordered dosages."""

    compacted: list[str] = []
    for sentence in _result_sentences(result):
        verb = _leading_finite_verb(sentence)
        if not verb or verb.casefold() in _KNOWLEDGE_RESULT_VERBS:
            compacted.append(sentence)
            continue
        body = _drop_leading_verb(sentence).rstrip(" .")
        items = _split_week_action_items(body)
        parsed = [_action_item_dosage(item) for item in items]
        groups: dict[str, list[int]] = {}
        for index, dosage in enumerate(parsed):
            if dosage is None:
                continue
            label, _marker = dosage
            groups.setdefault(_homogeneous_action_label(label), []).append(index)
        merge_groups = {key: indexes for key, indexes in groups.items() if len(indexes) > 1}
        if not merge_groups:
            compacted.append(sentence)
            continue

        replacements: dict[int, str] = {}
        removed: set[int] = set()
        for indexes in merge_groups.values():
            first = indexes[0]
            assert parsed[first] is not None
            label = parsed[first][0]
            markers = [parsed[index][1] for index in indexes if parsed[index] is not None]
            replacements[first] = _normalize_spaces(
                f"{label} {_ordered_dosage_formula(markers)}"
            )
            removed.update(indexes[1:])
        rebuilt_items = [
            replacements.get(index, item)
            for index, item in enumerate(items)
            if index not in removed
        ]
        rebuilt_body = (
            rebuilt_items[0]
            if len(rebuilt_items) == 1
            else ", ".join(rebuilt_items[:-1]) + " и " + rebuilt_items[-1]
        )
        compacted.append(_cap_sentence(f"{verb} {rebuilt_body}"))
    return _normalize_spaces(" ".join(compacted))


def _fold_week_result(result: str) -> str:
    """Write a predicate once for all objects already proven for it.

    Runs after the grammar gate and the coverage accounting: the sentences are
    proven, so listing their objects adds nothing and removes nothing.
    """

    result_without_provenance = _strip_result_provenance_context(result)
    folded = _normalize_spaces(
        " ".join(
            _fold_repeated_predicates(_result_sentences(result_without_provenance))
        )
    )
    folded = _compact_repeated_homogeneous_action_frames(folded)
    return _compress_exercise_catalogues_in_text(folded)


def _prefer_quoted_control_if_incomplete(result: str, control: str) -> str:
    """Rebuild compact CONTROL when a shortened process fragment misses an action."""

    # Never rewrite an oral theory CONTROL into observation quotes.
    if re.search(r"(?i)устный опрос", control or ""):
        return control
    if (
        re.search(r"(?i)проверя(?:ется|ются)\s+действи", control or "")
        or _control_has_long_result_quotes(control or "")
    ):
        rebuilt = _quoted_actions_control(result)
        if rebuilt:
            return rebuilt
    return control


def _unified_process_performance_control(result: str, control: str) -> str:
    """One observation for a week folded into a single performance of processes.

    Quoting each clause separately would repeat the finite verb and nest the
    quotes the source already uses for the named ways of a movement.
    """

    actions = _result_actions(_fold_week_result(result))
    if len(actions) != 1:
        return control
    verb, obj = actions[0]
    if verb.casefold() != "выполняет" or not obj:
        return control
    if len(_performed_process_conjuncts(obj)) < 2:
        return control
    return _normalize_spaces(
        "Педагогическое наблюдение за выполнением "
        + _process_enumeration_to_genitive(obj)
    )


def _merge_independent_part_results(results: list[str]) -> str:
    """Keep topic order while safely folding equal knowledge predicates."""

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
    sentences = _fold_safe_knowledge_predicate_runs(sentences)
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


_CONTROL_LEAD_PLURALS = {"проверяется действие": "проверяются действия"}


def _fold_control_operations(tails: list[str]) -> str:
    """One wording per group of operations checked the same way; all are listed."""

    leads: list[str] = []
    operations: list[list[str]] = []
    index_by_lead: dict[str, int] = {}
    last_lead: int | None = None
    for tail in tails:
        lead, quote, rest = tail.partition("«")
        lead = lead.strip()
        if quote and not lead and last_lead is not None:
            # A bare quoted operation continues the wording stated before it.
            operations[last_lead].append(tail)
            continue
        if not quote or not lead:
            leads.append(tail)
            operations.append([])
            continue
        index = index_by_lead.get(lead.casefold())
        if index is None:
            index_by_lead[lead.casefold()] = len(leads)
            leads.append(lead)
            operations.append([])
            index = len(leads) - 1
        operations[index].append(quote + rest)
        last_lead = index
    rendered = []
    for lead, items in zip(leads, operations):
        if not items:
            rendered.append(lead)
            continue
        if len(items) > 1:
            lead = _CONTROL_LEAD_PLURALS.get(lead.casefold(), lead)
        rendered.append(f"{lead} " + ", ".join(items))
    return "; ".join(item for item in rendered if item)


def _split_control_clauses(control: str) -> list[str]:
    """Clauses of an assembled CONTROL; separators inside quotes stay in place."""

    clauses: list[str] = []
    current: list[str] = []
    depth = 0
    for char in control:
        if char in "«„":
            depth += 1
        elif char in "»“":
            depth = max(depth - 1, 0)
        if char == ";" and depth == 0:
            clauses.append("".join(current))
            current = []
            continue
        current.append(char)
    clauses.append("".join(current))
    return [item for item in (clause.strip() for clause in clauses) if item]


def _unlabelled_method_group(
    item: str, index_by_label: dict[str, int]
) -> tuple[int, str] | None:
    """Group and remainder of a clause that repeats an already labelled method."""

    folded = item.casefold()
    for label, index in index_by_label.items():
        if folded.startswith(f"{label} "):
            remainder = item[len(label) :].strip()
            # «педагогическое наблюдение за техникой …» is a complete method
            # phrase, not a tail of «Педагогическое наблюдение: проверяется …».
            if remainder and not re.match(r"(?i)^(за|при)\b", remainder):
                return index, remainder
    return None


def _join_control_clauses(controls: list[str]) -> str:
    """Group the week's operations by control method: one label, no repeated wording."""

    groups: list[tuple[str, list[str], set[str]]] = []
    index_by_label: dict[str, int] = {}
    last_label_index: int | None = None
    for control in controls:
        # Already assembled controls are regrouped clause by clause, so a method
        # that appears in several parts of the week keeps a single label.
        for item in _split_control_clauses(_normalize_spaces(control)):
            head, separator, tail = item.partition(": ")
            labelled = bool(separator) and "«" not in head and bool(tail.strip())
            # The same method spelled without a label joins its own group
            # instead of repeating the wording of the check.
            unlabelled_method = _unlabelled_method_group(item, index_by_label)
            if labelled:
                index = index_by_label.get(head.casefold())
                if index is None:
                    index_by_label[head.casefold()] = len(groups)
                    groups.append((head, [], set()))
                    index = len(groups) - 1
                operation = tail.strip()
            elif item.startswith("«") and last_label_index is not None:
                # A clause that is only a quoted operation continues the method
                # whose label was already stated.
                index, operation = last_label_index, item
            elif unlabelled_method is not None:
                index, operation = unlabelled_method
            else:
                # A colon inside a quoted operation belongs to the operation
                # itself, and a method without a label keeps its own clause.
                groups.append(("", [item], set()))
                continue
            _, tails, seen = groups[index]
            if operation.casefold() in seen:
                continue
            seen.add(operation.casefold())
            tails.append(operation)
            last_label_index = index
    rendered = [
        "; ".join(tails) if not label
        else f"{label}: " + _fold_control_operations(tails)
        for label, tails, _seen in groups
    ]
    return "; ".join(item for item in rendered if item)


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
    return _join_control_clauses(unique)


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
    return _join_control_clauses(unique)


_GENERIC_LESSON_TYPES = {
    "теоретическое занятие",
    "практическое занятие",
    "комбинированное занятие",
}

_WEEK_SCOPE_OVERLAY_TYPES = {
    "теоретическое занятие",
    "практическое занятие",
    "комбинированное занятие",
    "теоретико-практическое занятие",
    "учебно-тренировочное занятие",
    "контрольно-тренировочное занятие",
    "итоговое комбинированное занятие",
    "практикум",
    "беседа",
    "лекция",
    "игра",
    "игровое занятие",
    "спортивно-игровое занятие",
    "викторина",
    "тестирование",
    "диагностика",
}
_WEEK_CONTROL_EVIDENCE_RE = re.compile(
    r"(?i)(?:\bаттестац\w*|\bзач[её]т\w*|\bтестирован\w*|"
    r"\bдиагностик\w*|\bсдач\w*.{0,40}\bнорматив\w*|"
    r"\bконтрольн\w*\s+(?:работ\w*|занят\w*|испытан\w*))"
)
_WEEK_FINAL_EVIDENCE_RE = re.compile(
    r"(?i)(?:\bитогов\w*\s+занят\w*|\bподведен\w*\s+итог\w*)"
)
_WEEK_TRAINING_EVIDENCE_RE = re.compile(
    r"(?i)(?:\bтрениров\w*|\bотработ\w*|\bразучив\w*|"
    r"\bлазан(?:ь|и)\w*|\bОФП\b|\bприседан\w*)"
)
_WEEK_LOCAL_ACTIVITY_TYPES = {
    "игра",
    "игровое занятие",
    "спортивно-игровое занятие",
    "викторина",
    "тестирование",
    "диагностика",
}


def _complete_week_lesson_type(
    candidate: str,
    *,
    theory_hours: int,
    practice_hours: int,
    theory_text: str,
    practice_text: str,
    part_types: tuple[str, ...] = (),
) -> str:
    """Resolve TYPE once, from the complete week, after RESULT/CONTROL."""

    normalized = _normalize_spaces(candidate)
    theory = _normalize_spaces(theory_text)
    practice = _normalize_spaces(practice_text)
    full_week = _normalize_spaces(f"{theory} {practice}")
    unique_part_types = tuple(
        dict.fromkeys(_normalize_spaces(value) for value in part_types if value)
    )
    scope_candidate = normalized
    if (
        normalized in _GENERIC_LESSON_TYPES
        and len(unique_part_types) == 1
        and unique_part_types[0] not in _GENERIC_LESSON_TYPES
    ):
        scope_candidate = unique_part_types[0]
    grounded_whole_week_special = (
        scope_candidate
        and scope_candidate not in _WEEK_SCOPE_OVERLAY_TYPES
        and (not unique_part_types or unique_part_types == (scope_candidate,))
    )

    if theory_hours and not practice_hours:
        return "теоретическое занятие"
    if practice_hours and _WEEK_FINAL_EVIDENCE_RE.search(full_week):
        return "итоговое комбинированное занятие"
    if theory_hours and practice_hours and theory and practice:
        if grounded_whole_week_special:
            return normalized
        return "комбинированное занятие"
    if practice_hours:
        if _WEEK_CONTROL_EVIDENCE_RE.search(practice):
            return "контрольно-тренировочное занятие"
        if (
            scope_candidate in _WEEK_LOCAL_ACTIVITY_TYPES
            and _WEEK_TRAINING_EVIDENCE_RE.search(practice)
        ):
            return "учебно-тренировочное занятие"
    return scope_candidate


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

    # TYPE уточняется после RESULT/CONTROL: их прежний контракт неизменен.
    derived_parts = [
        replace(item, lesson_type=refine_selected_activity_type(
            item.lesson_type, item.frame.clause,
            item.type_result if item.type_result is not None else item.planned_result
        ))
        for item in derived_parts
    ]
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
        elif candidate and (
            candidate not in _GENERIC_LESSON_TYPES
            or candidate == "комбинированное занятие"
        ):
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
    *,
    finalize: bool = True,
) -> ContentEngineV2Result:
    theory_text, practice_text = _part_texts(part, topic_totals)
    kwargs = dict(
        topic_title=_weekly_source_topic(part),
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=part.program_content_full or "",
        theory_hours=part.theory_hours,
        practice_hours=part.practice_hours,
        occurrence_index=occurrence_index,
        practice_appearance_count=practice_appearance_count,
    )
    if finalize:
        derived = derive_fields_v2(**kwargs)
    else:
        derived = _derive_fields_before_final_gate(**kwargs)
    return _apply_unresolved_confirmed_slot_generic(derived, part)


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
    RESULT/CONTROL/TYPE выходят только из FINAL gate и дальше не меняются.
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
                        finalize=False,
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
            clause_coverage = tuple(
                entry for item in derived_parts for entry in item.clause_coverage
            )
            merged = replace(
                derived,
                lesson_type=lesson_type,
                planned_result=planned_result,
                assessment_method=assessment,
                theory_text=theory_text,
                practice_text=practice_text,
                warnings=tuple(dict.fromkeys((*warnings, *extra_warnings))),
                clause_coverage=clause_coverage,
            )
            final = _finalize_content_fields(
                merged,
                topic_title=_weekly_source_topic(row),
                theory_text=theory_text,
                practice_text=practice_text,
                program_content=row.program_content_full or "",
                theory_hours=row.theory_hours,
                practice_hours=row.practice_hours,
            )
            week_part_types = tuple(item.lesson_type for item in derived_parts)
        else:
            key = _content_occurrence_key(row)
            occurrence_index = 0
            count = practice_counts.get(key, 0)
            if row.practice_hours:
                occurrence_index = practice_occurrences.get(key, 0)
                practice_occurrences[key] = occurrence_index + 1
            prepared = _derive_week_part(
                parts[0],
                _topic_hour_totals(parts),
                occurrence_index,
                count if row.practice_hours else 0,
                finalize=False,
            )
            lesson_type = _aggregate_week_lesson_type(
                parts,
                [prepared],
                theory_text=theory_text,
                practice_text=practice_text,
            )
            prepared = replace(prepared, lesson_type=lesson_type)
            final = _finalize_content_fields(
                prepared,
                topic_title=_weekly_source_topic(row),
                theory_text=theory_text,
                practice_text=practice_text,
                program_content=row.program_content_full or "",
                theory_hours=row.theory_hours,
                practice_hours=row.practice_hours,
                occurrence_index=occurrence_index,
                practice_appearance_count=count if row.practice_hours else 0,
            )
            week_part_types = (prepared.lesson_type,)
        final = replace(
            final,
            lesson_type=_complete_week_lesson_type(
                final.lesson_type,
                theory_hours=row.theory_hours,
                practice_hours=row.practice_hours,
                theory_text=theory_text,
                practice_text=practice_text,
                part_types=week_part_types,
            ),
        )
        final = _maybe_apply_generic_lesson_fallback(
            final,
            topic_title=_weekly_source_topic(row),
            theory_hours=row.theory_hours,
            practice_hours=row.practice_hours,
            source_confirmed=calendar_row_has_confirmed_source(
                row, theory_text=theory_text, practice_text=practice_text
            ),
        )
        if _row_is_unresolved_confirmed_slot(row, parts):
            slot = replace(
                parts[0],
                theory_hours=row.theory_hours,
                practice_hours=row.practice_hours,
            )
            final = _apply_unresolved_confirmed_slot_generic(final, slot)
        final = _apply_user_confirmed_sentence_frames(final, row, parts)
        result.append(
            LessonContentV2Row(
                source=row,
                theory_text=theory_text,
                practice_text=practice_text,
                lesson_type=final.lesson_type,
                planned_result=final.planned_result,
                assessment_method=final.assessment_method,
                action=final.frame.action,
                object=final.frame.object,
                conditions=final.frame.conditions,
                warnings=tuple(dict.fromkeys((*warnings, *final.warnings))),
                clause_coverage=final.clause_coverage,
                clause_roles=final.clause_roles,
                provenance_codes=final.provenance_codes,
            )
        )
    return tuple(result)
