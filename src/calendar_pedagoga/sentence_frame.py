"""Deterministic SentenceFrame renderer for USER_CONFIRMED overlay slots.

RESULT and CONTROL are built from one frame. Morphology is applied only to
proven templates. Arbitrary strings are never inflected, and a finished
RESULT is never re-parsed into CONTROL.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Sequence

from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
from calendar_pedagoga.lesson_content import _cap_sentence, _clause_units, _normalize_spaces
from calendar_pedagoga.matching import MatchStatus


LESSON_MODE_THEORY = "theory"
LESSON_MODE_PRACTICE = "practice"
LESSON_MODE_MIXED = "mixed"
CONTROL_ORAL = "oral"
CONTROL_OBSERVATION = "observation"
CONTROL_CHECK = "check"
_GOVERNMENT_INST = "inst"
_GOVERNMENT_DAT = "dat"
_GOVERNMENT_GEN = "gen"
_CLOSED_VN_PERFORMANCE = "выполнение"

_PREPOSITIONS = frozenset(
    {
        "без",
        "в",
        "во",
        "для",
        "до",
        "за",
        "из",
        "изо",
        "к",
        "ко",
        "между",
        "на",
        "над",
        "о",
        "об",
        "обо",
        "от",
        "ото",
        "перед",
        "по",
        "под",
        "при",
        "про",
        "с",
        "со",
        "у",
        "через",
    }
)
_SKIP_UNIT_RE = re.compile(
    r"(?i)^(теория|практика|практические\s+работы|практические\s+занятия|"
    r"темы|содержание)(?:\s*[.:])?$"
)
_TOPIC_HEADER_RE = re.compile(r"(?i)^тема\s*№?\s*\d+")
_PRACTICE_SPLIT_RE = re.compile(r"(?i)(?:^|\n)\s*практика\.?\s*(?:\n|$)")
_QUOTED_TITLE_RE = re.compile(r"[«„\"](.+?)[»“\"]")
_WHOLE_QUOTE_RE = re.compile(r"^[«„\"](.+)[»“\"]$")
_IYA_CONSONANTS = "бвгджзклмнпрстфхцчшщ"
_FLEETING_OK_RE = re.compile(
    rf"(?i)^([а-яё]*[{_IYA_CONSONANTS}])ка$"
)
_OPEN_CLOSE_QUOTES = (("«", "»"), ("„", "“"))


@dataclass(frozen=True)
class SentenceFrame:
    action: str
    object: str
    topic: str
    activity: str
    source_span: str
    lesson_mode: str
    control_type: str
    proven: bool = False


def _positive_workload(value: object) -> bool:
    if value is None:
        return False
    try:
        return value > 0
    except TypeError:
        return False


def _bearing_parts(parts: Sequence[WeekTopicPart]) -> tuple[WeekTopicPart, ...]:
    bearing = tuple(
        part
        for part in parts
        if _positive_workload(part.theory_hours)
        or _positive_workload(part.practice_hours)
    )
    return bearing or tuple(parts)


def row_uses_sentence_frame(
    row: CalendarContentRow,
    parts: Sequence[WeekTopicPart],
) -> bool:
    """Overlay USER_CONFIRMED slots only; auto-path without weekly assignment stays on CE2."""

    bearing = _bearing_parts(parts)
    if not bearing:
        return False
    if any(part.match_status is not MatchStatus.USER_CONFIRMED for part in bearing):
        return False
    return any(part.weekly_content_assigned for part in bearing)


def weekly_source_topic(part: WeekTopicPart | CalendarContentRow) -> str:
    source_topic = (part.program_topic or "").strip()
    if (
        source_topic
        and part.topic_title.strip().casefold() == part.section.strip().casefold()
    ):
        return source_topic
    return _normalize_spaces(part.topic_title).strip(" .")


def _topic_quoted(topic: str) -> str:
    cleaned = _normalize_spaces(topic).strip().strip("«»\"„“").strip(" .")
    return f"«{cleaned}»"


def _lower_first(text: str) -> str:
    stripped = _normalize_spaces(text)
    if not stripped:
        return stripped
    return stripped[:1].casefold() + stripped[1:]


def _token_core(token: str) -> str:
    return re.sub(r"[^\wёЁ]", "", token, flags=re.IGNORECASE)


def text_is_damaged(text: str) -> bool:
    """Reject unclosed quotes, truncated endings, and proven-broken morphology."""

    value = (text or "").strip()
    if not value:
        return True
    for open_q, close_q in _OPEN_CLOSE_QUOTES:
        if value.count(open_q) != value.count(close_q):
            return True
    if value.count('"') % 2:
        return True
    if re.search(r"№у", value, flags=re.IGNORECASE):
        return True
    if re.search(r"(?i)\bпо тему\b", value):
        return True
    if re.search(r"(?i)циу\b", value):
        return True
    if not re.search(r"[.!?]$", value):
        return True
    if re.search(r"[«„\"]\s*$", value):
        return True
    if re.search(r"(?i)[а-яё]-$", value):
        return True
    return False


def safe_fallback(lesson_mode: str, topic: str) -> tuple[str, str]:
    quoted = _topic_quoted(topic)
    if lesson_mode == LESSON_MODE_PRACTICE:
        return (
            f"Выполняет практическую работу по теме {quoted}.",
            (
                "Педагогическое наблюдение за выполнением практической "
                f"работы по теме {quoted}."
            ),
        )
    if lesson_mode == LESSON_MODE_MIXED:
        theory_result, theory_control = safe_fallback(LESSON_MODE_THEORY, topic)
        practice_result, practice_control = safe_fallback(
            LESSON_MODE_PRACTICE, topic
        )
        return (
            f"{theory_result} {practice_result}",
            f"{theory_control} {practice_control}",
        )
    return (
        f"Характеризует содержание темы {quoted}.",
        f"Устный опрос по теме {quoted}.",
    )


def fallback_frame(lesson_mode: str, topic: str) -> SentenceFrame:
    if lesson_mode == LESSON_MODE_PRACTICE:
        quoted = _topic_quoted(topic)
        return SentenceFrame(
            action="выполняет",
            object=f"практическую работу по теме {quoted}",
            topic=topic,
            activity="",
            source_span="",
            lesson_mode=LESSON_MODE_PRACTICE,
            control_type=CONTROL_OBSERVATION,
            proven=False,
        )
    quoted = _topic_quoted(topic)
    return SentenceFrame(
        action="характеризует",
        object=f"содержание темы {quoted}",
        topic=topic,
        activity="",
        source_span="",
        lesson_mode=LESSON_MODE_THEORY,
        control_type=CONTROL_ORAL,
        proven=False,
    )


def _iya_forms(token: str) -> dict[str, str] | None:
    """Regular -ия noun: nom -ия, acc -ию, gen sg/nom-acc pl -ии, gen pl -ий."""

    core = _token_core(token)
    low = core.casefold()
    if len(low) < 5:
        return None
    for ending in ("ий", "ию", "ия", "ии"):
        if not low.endswith(ending):
            continue
        stem = core[:-2]
        if len(stem) < 2:
            continue
        if stem[-1].casefold() != "ц":
            continue
        if not re.search(r"[аеёиоуыэюя]", stem.casefold()):
            continue
        return {
            "nom": stem + "ия",
            "acc": stem + "ию",
            "gen": stem + "ии",
            "acc_pl": stem + "ии",
            "ending": ending,
        }
    return None


def _fleeting_ok_nominative(token: str) -> str | None:
    """Genitive ...Cка of inanimate masculine -ок."""

    core = _token_core(token)
    match = _FLEETING_OK_RE.fullmatch(core.casefold())
    if not match or len(core) < 5:
        return None
    restored = core[:-2] + "ок"
    if core[0].isupper():
        return restored[:1].upper() + restored[1:]
    return restored


def _verbal_noun_case_forms(lemma: str) -> dict[str, str] | None:
    """Proven case set of a verbal-noun lemma. Structural endings only."""

    core = _token_core(lemma)
    if len(core) < 4:
        return None
    low = core.casefold()
    patterns = (
        ("ение", "ения", "ению", "ением"),
        ("ание", "ания", "анию", "анием"),
        ("яние", "яния", "янию", "янием"),
        ("тие", "тия", "тию", "тием"),
        ("ство", "ства", "ству", "ством"),
    )
    for nom, gen, dat, inst in patterns:
        if low.endswith(nom) and len(low) > len(nom) + 1:
            stem = core[: -len(nom)]
            return {
                "nom": stem + nom,
                "gen": stem + gen,
                "dat": stem + dat,
                "inst": stem + inst,
            }
    if low.endswith("ие") and len(low) > 4:
        stem = core[:-2]
        return {
            "nom": stem + "ие",
            "gen": stem + "ия",
            "dat": stem + "ию",
            "inst": stem + "ием",
        }
    if low.endswith("ия") and len(core) >= 4 and core[-3].casefold() == "ц":
        stem = core[:-2]
        return {
            "nom": stem + "ия",
            "gen": stem + "ии",
            "dat": stem + "ии",
            "inst": stem + "ией",
        }
    if low.endswith("ка") and len(low) > 4:
        stem = core[:-1]
        return {
            "nom": core,
            "gen": stem + "и",
            "dat": stem + "е",
            "inst": stem + "ой",
        }
    if re.search(rf"[{_IYA_CONSONANTS}]$", low) and not low.endswith("ка"):
        return {
            "nom": core,
            "gen": core + "а",
            "dat": core + "у",
            "inst": core + "ом",
        }
    return None


def _span_head_forms(span: str) -> dict[str, str] | None:
    from calendar_pedagoga.content_engine_v2 import (
        _conjugate_verbal_noun,
        _verbal_noun_lemma,
    )

    tokens = _normalize_spaces(span).split()
    if not tokens:
        return None
    if not _conjugate_verbal_noun(tokens[0]):
        return None
    return _verbal_noun_case_forms(_verbal_noun_lemma(tokens[0]))


def _span_complement(span: str) -> str:
    tokens = _normalize_spaces(span).rstrip(" .;:").split()
    if len(tokens) < 2:
        return ""
    return " ".join(tokens[1:]).strip(" .,;:")


def _case_token(forms: dict[str, str] | None, case: str) -> str:
    if not forms:
        return ""
    return _lower_first(forms.get(case, "").strip())


def unproven_observation_control(topic: str) -> str:
    return (
        "Педагогическое наблюдение в ходе выполнения задания "
        f"по теме {_topic_quoted(topic)}."
    )


def _government_for_control(control_type: str) -> str:
    if control_type == CONTROL_ORAL:
        return _GOVERNMENT_DAT
    if control_type == CONTROL_CHECK:
        return _GOVERNMENT_GEN
    return _GOVERNMENT_INST


def _expected_governed_head(frame: SentenceFrame) -> str:
    government = _government_for_control(frame.control_type)
    if frame.activity:
        forms = _verbal_noun_case_forms(_CLOSED_VN_PERFORMANCE)
        return _case_token(forms, government)
    return _case_token(_span_head_forms(frame.source_span), government)


def _token_after_marker(text: str, marker: str) -> str:
    match = re.search(rf"(?i)\b{re.escape(marker)}\s+(\S+)", text)
    if not match:
        return ""
    return match.group(1).strip(".,;:«»\"„“")


def _proven_complement_acc(token: str) -> str | None:
    forms = _iya_forms(token)
    if forms is not None:
        if forms["ending"] == "ий":
            return forms["acc_pl"]
        return forms["acc"]
    fleeting = _fleeting_ok_nominative(token)
    if fleeting:
        return fleeting
    return None


def _proven_title_forms(title: str) -> tuple[str, str] | None:
    tokens = title.split()
    if not tokens:
        return None
    forms = _iya_forms(tokens[0])
    if forms is None:
        return None
    rest = tokens[1:]
    acc = " ".join([_lower_first(forms["acc"]), *rest]).strip()
    gen = " ".join([_lower_first(forms["gen"]), *rest]).strip()
    return acc, gen


def _skip_unit(unit: str) -> bool:
    text = _normalize_spaces(unit).strip(" .;:")
    if not text:
        return True
    if _SKIP_UNIT_RE.fullmatch(text):
        return True
    if _TOPIC_HEADER_RE.match(text):
        return True
    return False


def _quoted_work_title(unit: str) -> str | None:
    text = _normalize_spaces(unit).rstrip(" .;:")
    if not text:
        return None
    whole = _WHOLE_QUOTE_RE.fullmatch(text)
    if whole:
        title = whole.group(1).strip()
        return title or None
    match = _QUOTED_TITLE_RE.search(text)
    if not match:
        return None
    prefix = text[: match.start()].strip()
    title = match.group(1).strip()
    if not title:
        return None
    if prefix:
        from calendar_pedagoga.content_engine_v2 import _conjugate_verbal_noun

        head = prefix.split()[0]
        if _conjugate_verbal_noun(head):
            return None
    return title


def _process_frame(
    unit: str, *, lesson_mode: str, topic: str
) -> SentenceFrame | None:
    from calendar_pedagoga.content_engine_v2 import _conjugate_verbal_noun

    tokens = _normalize_spaces(unit).rstrip(" .;:").split()
    if len(tokens) < 2:
        return None
    verb = _conjugate_verbal_noun(tokens[0])
    if not verb:
        return None
    rest = tokens[1:]
    first = rest[0].strip(".,;:")
    if first.casefold() in _PREPOSITIONS:
        obj = " ".join(rest)
    else:
        acc = _proven_complement_acc(first)
        if acc is None:
            return None
        obj = " ".join([_lower_first(acc), *rest[1:]]).strip()
    obj = obj.strip(" .,;:")
    if not obj:
        return None
    return SentenceFrame(
        action=verb,
        object=obj,
        topic=topic,
        activity="",
        source_span=_normalize_spaces(unit).rstrip(" .;:"),
        lesson_mode=lesson_mode,
        control_type=CONTROL_OBSERVATION,
        proven=True,
    )


def _named_work_frame(title: str, *, topic: str) -> SentenceFrame:
    forms = _proven_title_forms(title)
    if forms is None:
        quoted = _topic_quoted(title)
        return SentenceFrame(
            action="выполняет",
            object=f"практическую работу {quoted}",
            topic=topic,
            activity=f"практической работы {quoted}",
            source_span=title,
            lesson_mode=LESSON_MODE_PRACTICE,
            control_type=CONTROL_OBSERVATION,
            proven=True,
        )
    acc, gen = forms
    return SentenceFrame(
        action="выполняет",
        object=acc,
        topic=topic,
        activity=gen,
        source_span=title,
        lesson_mode=LESSON_MODE_PRACTICE,
        control_type=CONTROL_OBSERVATION,
        proven=True,
    )


def _split_part_units(part: WeekTopicPart) -> tuple[tuple[str, ...], tuple[str, ...]]:
    content = part.program_content_full or ""
    theory_hours = _positive_workload(part.theory_hours)
    practice_hours = _positive_workload(part.practice_hours)
    if not content.strip():
        return (), ()
    theory_text = ""
    practice_text = ""
    if theory_hours and practice_hours and _PRACTICE_SPLIT_RE.search(content):
        theory_text, practice_text = _PRACTICE_SPLIT_RE.split(content, maxsplit=1)
    elif practice_hours and not theory_hours:
        practice_text = content
    elif theory_hours and not practice_hours:
        theory_text = content
    elif theory_hours and practice_hours:
        theory_text = content
    else:
        theory_text = content
    theory_units = tuple(
        unit for unit in _clause_units(theory_text) if not _skip_unit(unit)
    )
    practice_units = tuple(
        unit for unit in _clause_units(practice_text) if not _skip_unit(unit)
    )
    return theory_units, practice_units


def _frames_from_theory_units(
    units: Sequence[str], *, topic: str
) -> list[SentenceFrame]:
    frames: list[SentenceFrame] = []
    for unit in units:
        frame = _process_frame(unit, lesson_mode=LESSON_MODE_THEORY, topic=topic)
        if frame is not None:
            frames.append(frame)
    return frames


def _frames_from_practice_units(
    units: Sequence[str], *, topic: str
) -> list[SentenceFrame]:
    frames: list[SentenceFrame] = []
    for unit in units:
        title = _quoted_work_title(unit)
        if title:
            frames.append(_named_work_frame(title, topic=topic))
            continue
        frame = _process_frame(unit, lesson_mode=LESSON_MODE_PRACTICE, topic=topic)
        if frame is not None:
            frames.append(frame)
    return frames


def frames_for_confirmed_row(
    row: CalendarContentRow,
    parts: Sequence[WeekTopicPart],
) -> tuple[SentenceFrame, ...]:
    frames: list[SentenceFrame] = []
    for part in _bearing_parts(parts):
        topic = weekly_source_topic(part) or weekly_source_topic(row)
        theory_units, practice_units = _split_part_units(part)
        theory_frames = _frames_from_theory_units(theory_units, topic=topic)
        practice_frames = _frames_from_practice_units(practice_units, topic=topic)
        if _positive_workload(part.theory_hours):
            if theory_frames:
                frames.extend(theory_frames)
            else:
                frames.append(fallback_frame(LESSON_MODE_THEORY, topic))
        if _positive_workload(part.practice_hours):
            if practice_frames:
                frames.extend(practice_frames)
            else:
                frames.append(fallback_frame(LESSON_MODE_PRACTICE, topic))
    return tuple(frames)


def result_from_frame(frame: SentenceFrame) -> str:
    """RESULT from frame fields only. Never derived from CONTROL."""

    if not frame.proven:
        result, _control = safe_fallback(frame.lesson_mode, frame.topic)
        return result
    if not (frame.action or "").strip() or not (frame.object or "").strip():
        result, _control = safe_fallback(frame.lesson_mode, frame.topic)
        return result
    return _cap_sentence(f"{frame.action} {frame.object}".strip())


def _closed_topic_oral(topic: str) -> str:
    return _cap_sentence(f"Устный опрос по теме {_topic_quoted(topic)}")


def _closed_performance_control(government: str, complement: str, topic: str) -> str:
    forms = _verbal_noun_case_forms(_CLOSED_VN_PERFORMANCE)
    head = _case_token(forms, government)
    phrase = _normalize_spaces(complement).strip(" .,;:")
    if not head or not phrase:
        return unproven_observation_control(topic)
    if government == _GOVERNMENT_DAT:
        return _cap_sentence(f"Устный опрос по {head} {phrase}")
    if government == _GOVERNMENT_GEN:
        return _cap_sentence(f"Проверка {head} {phrase}")
    return _cap_sentence(f"Педагогическое наблюдение за {head} {phrase}")


def control_from_frame(frame: SentenceFrame) -> str:
    """CONTROL from frame fields only. Never re-parses RESULT."""

    topic = frame.topic
    government = _government_for_control(frame.control_type)
    if not frame.proven:
        if frame.control_type == CONTROL_OBSERVATION and frame.lesson_mode != LESSON_MODE_PRACTICE:
            return unproven_observation_control(topic)
        _result, control = safe_fallback(frame.lesson_mode, topic)
        return control
    if frame.activity:
        return _closed_performance_control(government, frame.activity, topic)
    complement = _span_complement(frame.source_span)
    head = _expected_governed_head(frame)
    if not head or not complement:
        if government == _GOVERNMENT_DAT:
            return _closed_topic_oral(topic)
        if government == _GOVERNMENT_GEN:
            return unproven_observation_control(topic)
        return unproven_observation_control(topic)
    if government == _GOVERNMENT_DAT:
        return _cap_sentence(f"Устный опрос по {head} {complement}")
    if government == _GOVERNMENT_GEN:
        return _cap_sentence(f"Проверка {head} {complement}")
    return _cap_sentence(f"Педагогическое наблюдение за {head} {complement}")


def _result_matches_template(frame: SentenceFrame, result: str) -> bool:
    expected = result_from_frame(frame)
    if _normalize_spaces(result) != _normalize_spaces(expected):
        fallback, _control = safe_fallback(frame.lesson_mode, frame.topic)
        return _normalize_spaces(result) == _normalize_spaces(fallback)
    return True


def _control_matches_government(frame: SentenceFrame, control: str) -> bool:
    government = _government_for_control(frame.control_type)
    text = _normalize_spaces(control)
    if text == unproven_observation_control(frame.topic):
        return True
    _result, fallback_control = safe_fallback(frame.lesson_mode, frame.topic)
    if text == _normalize_spaces(fallback_control):
        if government == _GOVERNMENT_DAT:
            return _token_after_marker(text, "по").casefold() == "теме"
        if government == _GOVERNMENT_INST:
            return _token_after_marker(text, "за").casefold() == "выполнением"
        return False
    if government == _GOVERNMENT_DAT:
        token = _token_after_marker(text, "по")
        expected = _expected_governed_head(frame) or "теме"
        return bool(token) and token.casefold() == expected.casefold()
    if government == _GOVERNMENT_GEN:
        token = _token_after_marker(text, "Проверка")
        expected = _expected_governed_head(frame)
        return bool(expected) and token.casefold() == expected.casefold()
    token = _token_after_marker(text, "за")
    expected = _expected_governed_head(frame)
    return bool(expected) and token.casefold() == expected.casefold()


def grammar_gate(frame: SentenceFrame, result: str, control: str) -> bool:
    """Accept only frame-legal RESULT/CONTROL. Does not patch arbitrary strings."""

    if text_is_damaged(result) or text_is_damaged(control):
        return False
    if frame.proven and not (frame.object or "").strip():
        return False
    if not (result or "").strip() or not (control or "").strip():
        return False
    if not _result_matches_template(frame, result):
        return False
    if not _control_matches_government(frame, control):
        return False
    return True


def _rebuild_from_frame(frame: SentenceFrame) -> tuple[str, str]:
    if frame.control_type == CONTROL_ORAL or frame.lesson_mode == LESSON_MODE_THEORY:
        return safe_fallback(LESSON_MODE_THEORY, frame.topic)
    if frame.lesson_mode == LESSON_MODE_PRACTICE:
        result, _control = safe_fallback(LESSON_MODE_PRACTICE, frame.topic)
        return result, unproven_observation_control(frame.topic)
    result, _control = safe_fallback(frame.lesson_mode, frame.topic)
    return result, unproven_observation_control(frame.topic)


def render_sentence_frame(frame: SentenceFrame) -> tuple[str, str, bool]:
    """Return RESULT, CONTROL, and whether the safe fallback was used."""

    result = result_from_frame(frame)
    control = control_from_frame(frame)
    if grammar_gate(frame, result, control):
        return result, control, not frame.proven
    rebuilt_result, rebuilt_control = _rebuild_from_frame(frame)
    return rebuilt_result, rebuilt_control, True


def _unique_texts(items: Sequence[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = _normalize_spaces(item).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def render_frames(frames: Sequence[SentenceFrame]) -> tuple[str, str, bool]:
    if not frames:
        return "", "", True
    results: list[str] = []
    controls: list[str] = []
    used_fallback = False
    any_proven = False
    for frame in frames:
        result, control, fell_back = render_sentence_frame(frame)
        results.append(result)
        controls.append(control)
        if fell_back:
            used_fallback = True
        elif frame.proven:
            any_proven = True
    week_fallback = used_fallback and not any_proven
    return (
        " ".join(_unique_texts(results)),
        " ".join(_unique_texts(controls)),
        week_fallback,
    )
