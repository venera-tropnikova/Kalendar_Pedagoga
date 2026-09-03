"""Строгое формирование полей занятия без ИИ и новых фактов сверх источника."""

from __future__ import annotations

from dataclasses import dataclass
import re

from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart


@dataclass(frozen=True)
class LessonContentRow:
    source: CalendarContentRow
    theory_text: str
    practice_text: str
    lesson_type: str
    planned_result: str
    assessment_method: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class FillMetrics:
    theory_percent: float
    practice_percent: float
    lesson_type_percent: float
    planned_result_percent: float
    assessment_method_percent: float
    overall_percent: float


def _split_explicit_practice(text: str) -> tuple[str, str] | None:
    match = re.search(
        r"(?:^|\n)\s*(?:Практика\.|Практические занятия\.?)\s*",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    theory = text[: match.start()].strip()
    practice = text[match.end() :].strip()
    return theory, practice


def _normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_loose(text: str) -> str:
    return _normalize_spaces(text).casefold()


def _source_blob(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(needle.casefold() in lowered for needle in needles)


def _has_token(blob: str, token: str, *, prefix: bool = False) -> bool:
    r"""Подстрока как отдельный токен, без ложных вхождений внутри слов."""

    boundary = r"" if prefix else r"(?![0-9A-Za-zА-Яа-яЁё])"
    return (
        re.search(
            rf"(?i)(?<![0-9A-Za-zА-Яа-яЁё]){re.escape(token)}{boundary}",
            blob,
        )
        is not None
    )


def _clause_units(text: str) -> list[str]:
    """Короткие смысловые единицы текущего текста (не соседние темы)."""

    # Не рвать предложения на сокращениях «г.», «ул.» и т.п.
    protected = re.sub(
        r"\b(г|ул|пр|пер|обл|р-н|с|п|д|пос|т|пгт)\.\s*",
        lambda m: m.group(0).replace(".", "\u0000"),
        text,
        flags=re.IGNORECASE,
    )
    parts = re.split(r"(?<=[.!;?])\s+|\n+|;\s*", protected)
    units: list[str] = []
    for part in parts:
        cleaned = _normalize_spaces(part.replace("\u0000", ".")).strip(" .;:")
        cleaned = re.sub(r"^:\s*", "", cleaned)
        if cleaned:
            units.append(cleaned)
    return units


_ACTION_HINT_RE = re.compile(
    r"(?i)\b("
    r"выбор|подбор|подготовк|составлен|составля|разработ|определен|"
    r"организац|распредел|провер|изучен|анализ|работа|измерен|"
    r"ориентир|укладк|развертыван|свертыван|преодолен|наблюден|"
    r"отработк|рассчит|представлен|изготовлен|применен|выполнен|"
    r"аппликац|конструир|рисунк|рассказ|заслушиван|разжиган|"
    r"построен|упражнен|знакомств|прогул|экскурси|викторин"
    r")"
)


_ABSTRACT_START_RE = re.compile(
    r"(?i)^(понятие|значение|характеристика|роль государства|"
    r"виды туризма|требования к|разрядн|основная задача|роль и значение)"
)


def _line_form_scores(text: str) -> dict[str, int]:
    """Очки форм занятия по фразам текста; одно слово не даёт победу."""

    scores: dict[str, int] = {}
    if not text.strip():
        return scores

    def add(label: str, points: int) -> None:
        scores[label] = scores.get(label, 0) + points

    for unit in _clause_units(text):
        low = unit.casefold()
        # Одна фраза — одна ведущая форма (иначе «игры на местности» даёт ничью).
        if re.match(r"(?:дидактическ\w*\s+)?игр(?:а|ы)\b", low) or low.startswith(
            "дидактические игры"
        ):
            add("игра", 2)
            continue
        if re.match(r"экскурси(?:я|и|ю|ей)\b", low):
            add("экскурсия", 2)
            continue
        if re.match(r"прогул\w*\s+и\s+экскурси", low) or (
            "экскурси" in low and ("прогул" in low or "посещен" in low)
        ):
            add("экскурсия", 2)
            continue
        if low.startswith("мероприятие") or low.startswith("праздник"):
            add("практическое занятие", 2)
            continue
        if "практикум" in low:
            add("практикум", 2)
            continue
        if "ситуационн" in low:
            add("ситуационное занятие", 2)
            continue
        if low.startswith("отработка") or low.startswith("упражнен"):
            add("тренировочное занятие", 2)
            continue
        # «тренировочного процесса» в бытовой фразе не делает занятие тренировочным.
        if re.search(r"\bтренировочн(?:ое|ые|ая)\s+занят", low) or re.match(
            r"тренировк(?:а|и)\b", low
        ):
            add("тренировочное занятие", 2)
            continue
        if "исследован" in low or "изучение района" in low:
            add("исследовательское занятие", 2)
            continue
        if "на местности" in low or "на маршруте" in low:
            add("занятие на местности", 2)
            continue

        if "экскурси" in low:
            add("экскурсия", 1)
        if _has_token(unit, "игра") or _has_token(unit, "игры") or "игровые" in low:
            add("игра", 1)
        if "мероприятие" in low or "праздник" in low:
            add("практическое занятие", 1)
        if low.startswith("отработк"):
            add("тренировочное занятие", 1)
        if re.search(r"\bв походе\b", low):
            add("занятие на местности", 1)
        if re.search(r"\bпроект", low):
            add("проектное занятие", 1)
        if "ситуационн" in low:
            add("ситуационное занятие", 1)
        if "бесед" in low:
            add("беседа", 1)

    return scores


def _dominant_label(scores: dict[str, int], *, min_score: int = 2) -> str | None:
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_label, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if best_score < min_score:
        return None
    if best_score <= second:
        return None
    return best_label


def _general_lesson_type(
    *,
    theory_hours: int,
    practice_hours: int,
    theory_text: str,
    practice_text: str,
    program_content: str,
) -> str:
    """Общий тип без шаблона «Занятие по теме …»."""

    if theory_hours and not practice_hours:
        return "теоретическое занятие"
    if practice_hours and not theory_hours:
        return "практическое занятие"
    if practice_hours and theory_hours:
        if practice_text.strip() and (
            _ACTION_HINT_RE.search(practice_text)
            or _contains_any(practice_text, ("практическ", "мероприятие", "занятия", "упражнен"))
        ):
            return "практическое занятие"
        if theory_text.strip() and not practice_text.strip():
            return "теоретическое занятие"
        # Часы смешанные, явного разделения нет — по соотношению часов.
        if not practice_text.strip() and not theory_text.strip():
            return (
                "практическое занятие"
                if practice_hours > theory_hours
                else "теоретическое занятие"
            )
        if practice_hours > theory_hours:
            return "практическое занятие"
        if theory_hours > practice_hours:
            return "теоретическое занятие"
        return "практическое занятие"
    return "теоретическое занятие"


def derive_lesson_type(
    *,
    theory_hours: int,
    practice_hours: int,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str = "",
) -> str:
    """Тип занятия по доминирующему смыслу источника, не по одному слову."""

    del topic_title  # тип не строится из названия темы

    practice_scores = _line_form_scores(practice_text)
    if practice_hours and practice_text.strip():
        dominant = _dominant_label(practice_scores, min_score=2)
        if dominant and dominant != "беседа":
            return dominant

    if theory_hours and not practice_hours:
        theory_scores = _line_form_scores(theory_text)
        if theory_scores.get("беседа", 0) >= 2 and theory_scores.get("беседа", 0) > max(
            (v for k, v in theory_scores.items() if k != "беседа"),
            default=0,
        ):
            return "беседа"
        return "теоретическое занятие"

    if practice_hours and not theory_hours:
        if practice_text.strip():
            dominant = _dominant_label(practice_scores, min_score=2)
            if dominant:
                return dominant
        week_scores = _line_form_scores(practice_text or program_content)
        dominant = _dominant_label(week_scores, min_score=2)
        if dominant:
            return dominant
        return "практическое занятие"

    return _general_lesson_type(
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
    )


def _topic_overlap_score(fragment: str, topic_title: str) -> int:
    stop = {
        "и",
        "или",
        "для",
        "при",
        "по",
        "на",
        "в",
        "во",
        "с",
        "со",
        "о",
        "об",
        "его",
        "их",
        "the",
        "основы",
        "основн",
        "правила",
        "общие",
        "сведен",
    }
    topic_words = {
        w
        for w in re.findall(r"[А-Яа-яЁё]{4,}", topic_title.casefold())
        if w not in stop
    }
    if not topic_words:
        return 0
    frag = fragment.casefold()
    return sum(1 for word in topic_words if word in frag)


_GENERIC_ROOTS = (
    "турист",
    "поход",
    "занят",
    "обучен",
    "програм",
    "правил",
    "основ",
    "общие",
    "сведен",
    "личн",
    "групп",
    "детск",
    "воспита",
    "умения",
    "знани",
    "навык",
    "местност",
    "места",
    "место",
    "движе",
    "выбор",
    "требо",
    "основн",
    "подго",
    "истор",
)


def _significant_tokens(text: str) -> set[str]:
    stop = {
        "и",
        "или",
        "для",
        "при",
        "по",
        "на",
        "в",
        "во",
        "с",
        "со",
        "о",
        "об",
        "его",
        "их",
        "это",
        "как",
        "что",
        "все",
        "основы",
        "основн",
        "правила",
        "общие",
        "сведен",
        "основные",
        "должны",
        "знать",
        "уметь",
    }
    return {
        token
        for token in re.findall(r"[А-Яа-яЁё]{4,}", text.casefold())
        if token not in stop
    }


def _token_root(token: str) -> str:
    # Не схлопывать «организм» и «организация».
    if token.startswith("организм"):
        return "организм"
    if token.startswith("организа"):
        return "организац"
    if token.startswith("лыж"):
        return "лыж"
    return token[:6] if len(token) >= 6 else token


def _is_generic_root(root: str) -> bool:
    return any(root.startswith(stem[:5]) or stem.startswith(root) for stem in _GENERIC_ROOTS)


def _distinctive_roots(text: str) -> set[str]:
    return {
        _token_root(token)
        for token in _significant_tokens(text)
        if not _is_generic_root(_token_root(token))
    }


def _prefix_hits_in_blob(tokens: set[str], blob: str) -> int:
    low = blob.casefold()
    hits = 0
    for token in tokens:
        needle = token[:4] if len(token) >= 4 else token
        if len(needle) >= 4 and needle in low:
            hits += 1
    return hits


def _outcome_match_score(
    outcome: str,
    *,
    topic_title: str,
    content_blob: str,
    activity: str,
    theory_hours: int = 0,
    practice_hours: int = 0,
) -> int:
    """Насколько исход соответствует теме и содержанию текущей недели."""

    outcome_tokens = _significant_tokens(outcome)
    if not outcome_tokens:
        return -100

    topic_roots = _distinctive_roots(topic_title)
    outcome_roots = _distinctive_roots(outcome)
    activity_roots = _distinctive_roots(activity)
    content_roots = _distinctive_roots(content_blob)

    topic_hits = len(topic_roots & outcome_roots)
    activity_hits = len(activity_roots & outcome_roots)
    content_hits = len(content_roots & outcome_roots)
    content_prefix_hits = _prefix_hits_in_blob(outcome_tokens, content_blob)

    # Без связи с темой нужно убедительное пересечение с содержанием недели.
    if topic_hits == 0 and content_hits + activity_hits < 2 and content_prefix_hits < 3:
        return -50
    if topic_hits == 0 and content_hits == 0 and activity_hits == 0:
        return -50

    score = (
        topic_hits * 12
        + content_hits * 11
        + activity_hits * 7
        + content_prefix_hits
    )
    if topic_hits >= 2:
        score += 45
    elif topic_hits == 1:
        score += 12
    score += _topic_overlap_score(outcome, topic_title) * 2
    if re.search(r"\bг\.\s*\w+", outcome, re.I) and re.search(
        r"\bг\.\s*\w+", _source_blob(topic_title, content_blob), re.I
    ):
        score += 5
    if topic_hits == 0:
        score -= 8
        if theory_hours and not practice_hours:
            score -= 16
    if len(outcome) > 180:
        score -= 2
    return score


def _outcome_pools(
    *,
    theory_hours: int,
    practice_hours: int,
    knowledge_outcomes: tuple[str, ...],
    skill_outcomes: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Первичный пул: умения для практики, знания для теории."""

    if practice_hours and not theory_hours:
        return skill_outcomes, knowledge_outcomes
    if theory_hours and not practice_hours:
        return knowledge_outcomes, skill_outcomes
    if practice_hours >= theory_hours:
        return skill_outcomes, knowledge_outcomes
    return knowledge_outcomes, skill_outcomes


def _select_matched_outcome(
    *,
    topic_title: str,
    content_blob: str,
    activity: str,
    theory_hours: int,
    practice_hours: int,
    knowledge_outcomes: tuple[str, ...],
    skill_outcomes: tuple[str, ...],
    occurrence_index: int,
) -> str | None:
    """Выбрать знание/умение, согласованное с темой недели."""

    primary, _secondary = _outcome_pools(
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        knowledge_outcomes=knowledge_outcomes,
        skill_outcomes=skill_outcomes,
    )
    min_score = 16
    primary_set = set(primary)

    scored: list[tuple[int, int, str]] = []
    for item in (*knowledge_outcomes, *skill_outcomes):
        if not item.strip():
            continue
        score = _outcome_match_score(
            item,
            topic_title=topic_title,
            content_blob=content_blob,
            activity=activity,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
        )
        if item in primary_set:
            score += 3
        title_hits = len(_distinctive_roots(topic_title) & _distinctive_roots(item))
        if score >= min_score:
            scored.append((score, title_hits, item))

    if not scored:
        return None

    titled = [row for row in scored if row[1] >= 1]
    pool = titled or scored
    if titled:
        knowledge_set = set(knowledge_outcomes)
        skill_set = set(skill_outcomes)
        if theory_hours and not practice_hours:
            preferred = [row for row in titled if row[2] in knowledge_set]
            pool = preferred or titled
        elif practice_hours and not theory_hours:
            preferred = [row for row in titled if row[2] in skill_set]
            pool = preferred or titled
    pool.sort(key=lambda row: (-row[0], -row[1], row[2]))
    top_score = pool[0][0]
    top_hits = pool[0][1]
    top = [
        item
        for score, hits, item in pool
        if score >= top_score - 3 and hits >= max(0, top_hits - 1)
    ]
    return top[occurrence_index % len(top)]


def _fragment_action_score(fragment: str, topic_title: str = "") -> int:
    score = 0
    if _ACTION_HINT_RE.search(fragment):
        score += 6
    if _ABSTRACT_START_RE.search(fragment):
        score -= 6
    if fragment.count("?") >= 1:
        score -= 3
    if fragment.count("?") >= 2:
        score -= 4
    if fragment.casefold().startswith("мероприятие") or "«" in fragment:
        score += 5
    if fragment.casefold().startswith("экскурси"):
        score -= 1
    if re.search(r"\bг\.\s*\w+", fragment, re.IGNORECASE):
        score += 3
    score += _topic_overlap_score(fragment, topic_title) * 3
    length = len(fragment)
    if 25 <= length <= 140:
        score += 4
    elif 141 <= length <= 200:
        score += 1
    elif length > 200:
        score -= 2
    if length < 18:
        score -= 2
    return score


def _week_result_source(
    *,
    theory_hours: int,
    practice_hours: int,
    theory_text: str,
    practice_text: str,
    program_content: str,
) -> str:
    """Только содержание текущей недели: практика → теория → текст темы недели."""

    if practice_hours and practice_text.strip():
        return practice_text.strip()
    if theory_hours and theory_text.strip():
        return theory_text.strip()
    if practice_text.strip():
        return practice_text.strip()
    if theory_text.strip():
        return theory_text.strip()
    return (program_content or "").strip()


def _action_candidates(source: str, topic_title: str) -> list[str]:
    units = _clause_units(source)
    if not units:
        return []
    scored = sorted(
        units,
        key=lambda unit: _fragment_action_score(unit, topic_title),
        reverse=True,
    )
    positive = [unit for unit in scored if _fragment_action_score(unit, topic_title) >= 1]
    ordered = positive or scored
    # Для последовательного выбора по неделям сохраняем порядок появления в тексте.
    by_source_order = [unit for unit in units if unit in set(ordered)]
    return by_source_order if len(by_source_order) >= 2 else ordered


def _ends_with_incomplete_entity(text: str) -> bool:
    low = text.rstrip()
    if re.search(r"\bг\.\s*$", low, re.IGNORECASE):
        return True
    if re.search(r"\b(?:ул|пр|пер|обл|р-н|респ)\.\s*$", low, re.IGNORECASE):
        return True
    if low.endswith(("«", "(", "-", "—")):
        return True
    return False


def _clean_source_phrase(phrase: str) -> str:
    """Нормализация пробелов/хвоста без смены слов и падежей."""

    text = _normalize_spaces(phrase).strip(" .;:")
    text = re.sub(r"^[…\.]+", "", text).strip()
    text = re.sub(r"^:\s*", "", text)
    return text


def _shorten_clause(text: str, *, max_len: int = 220) -> str:
    """Если фраза слишком длинная — обрезать только с конца по безопасной границе."""

    text = _clean_source_phrase(text)
    if len(text) <= max_len:
        return text

    city = re.search(r"\bг\.\s*[А-ЯЁA-Z][\w\-]*(?:\s+[А-ЯЁA-Z][\w\-]*)*", text)
    if city and city.end() <= max_len + 40:
        end = city.end()
        tail = text[end:]
        extra = re.match(r"(?:\s+и\s+[А-ЯЁа-яёA-Za-z\-]+)+", tail)
        if extra:
            end += extra.end()
        if end <= len(text) and end <= max_len + 60:
            candidate = text[:end].strip(" .;")
            if not _ends_with_incomplete_entity(candidate) and not candidate.startswith(
                ("…", "...")
            ):
                return candidate

    cut = text[: max_len + 1]
    for sep in (";", ", ", " — ", " - "):
        if sep in cut:
            candidate = cut.rsplit(sep, 1)[0].strip(" .;")
            if (
                len(candidate) >= 24
                and not _ends_with_incomplete_entity(candidate)
                and not candidate.startswith(("…", "..."))
            ):
                return candidate
    # Лучше вернуть целую исходную фразу, чем обрезанный обрубок.
    return text


def _cap_sentence(text: str) -> str:
    text = _clean_source_phrase(text)
    if not text:
        return "."
    if re.search(r"\b(?:г|ул|пр|пер|обл|р-н)\.$", text, re.IGNORECASE):
        text += "."
    elif text[-1] not in ".!?":
        text += "."
    return text[:1].upper() + text[1:]


def _select_activity_focus(
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str,
    theory_hours: int,
    practice_hours: int,
    occurrence_index: int,
) -> str:
    """Выбрать формулировку деятельности текущей недели из источника."""

    source = _week_result_source(
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
    )
    if not source:
        return _normalize_spaces(topic_title).rstrip(" .")
    candidates = _action_candidates(source, topic_title)
    if not candidates:
        units = _clause_units(source)
        return units[0] if units else _normalize_spaces(topic_title).rstrip(" .")
    if occurrence_index == 0:
        return max(candidates, key=lambda unit: _fragment_action_score(unit, topic_title))
    return candidates[occurrence_index % len(candidates)]


def _type_from_activity(
    activity: str,
    *,
    theory_hours: int,
    practice_hours: int,
    theory_text: str,
    practice_text: str,
    program_content: str,
) -> str:
    """Тип по характеру выбранной деятельности и контекста недели."""

    activity_scores = _line_form_scores(activity)
    # Усилить сигналы самой выбранной формулировки деятельности.
    boosted = {key: value * 2 for key, value in activity_scores.items()}
    practice_scores = _line_form_scores(practice_text)
    merged: dict[str, int] = dict(practice_scores)
    for key, value in boosted.items():
        merged[key] = merged.get(key, 0) + value

    if practice_hours and (practice_text.strip() or activity.strip()):
        dominant = _dominant_label(merged, min_score=2)
        if dominant and dominant != "беседа":
            return dominant

    return derive_lesson_type(
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        topic_title="",
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
    )


def _control_for_activity(
    *,
    activity: str,
    lesson_type: str,
    theory_hours: int,
    practice_hours: int,
) -> str:
    """Контроль, согласованный с выбранной деятельностью и типом занятия."""

    focus = activity.casefold()
    type_low = lesson_type.casefold()

    if any(marker in type_low for marker in ("экскурсия", "игр", "на местности")):
        return "наблюдение"

    if _contains_any(focus, ("доклад", "отчёт", "отчет")) or _contains_any(
        focus, ("заслушиван",)
    ):
        return "защита результата"

    if "викторин" in focus:
        return "викторина"

    if _has_token(focus, "ситуационн", prefix=True) or "ситуационное" in type_low:
        return "ситуационная задача"

    if _has_token(focus, "тест") or _has_token(focus, "тесты") or _has_token(
        focus, "тестир", prefix=True
    ):
        return "тестирование"

    if _contains_any(focus, ("аппликац", "рисунк", "творческ", "конструир", "мероприятие", "праздник")):
        return "творческая работа"

    if _contains_any(focus, ("меню",)) or (
        _contains_any(focus, ("продукт",)) and _contains_any(focus, ("составлен", "список", "расчёт", "расчет"))
    ):
        return "проверка меню"

    # Маркеры читаются из формулировки деятельности источника, не из названия программы.
    if _contains_any(
        focus, ("карт", "компас", "абрис", "азимут", "масштаб", "топограф", "транспортир", "курвиметр")
    ):
        return "работа с картой"

    if _contains_any(focus, ("отработк", "упражнен", "техник", "приём", "прием", "тренировк")):
        return "демонстрация навыка"

    if "теоретическ" in type_low or "беседа" in type_low:
        return "устный опрос"

    if "практикум" in type_low or "проектн" in type_low or "исследовател" in type_low:
        return "практическое задание"

    if "трениров" in type_low:
        return "демонстрация навыка"

    if "практическ" in type_low or (practice_hours and _ACTION_HINT_RE.search(activity)):
        return "практическое задание"

    if theory_hours and not practice_hours:
        return "устный опрос"
    if practice_hours:
        return "практическое задание"
    return "устный опрос"


def derive_pedagogical_fields(
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str = "",
    theory_hours: int = 0,
    practice_hours: int = 0,
    occurrence_index: int = 0,
    knowledge_outcomes: tuple[str, ...] = (),
    skill_outcomes: tuple[str, ...] = (),
) -> tuple[str, str, str]:
    """Связка: тема/содержание → исход → тип → контроль."""

    activity = _select_activity_focus(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
    )
    content_blob = _source_blob(theory_text, practice_text, program_content)
    matched_outcome = _select_matched_outcome(
        topic_title=topic_title,
        content_blob=content_blob,
        activity=activity,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        knowledge_outcomes=knowledge_outcomes,
        skill_outcomes=skill_outcomes,
        occurrence_index=occurrence_index,
    )

    # Разнообразие по неделям одной темы: не подменять сильный тематический исход.
    phrase_source = matched_outcome or activity
    title_hits = (
        len(_distinctive_roots(topic_title) & _distinctive_roots(matched_outcome))
        if matched_outcome
        else 0
    )
    if matched_outcome and occurrence_index > 0 and content_blob.strip() and title_hits < 2:
        activity_options = _action_candidates(content_blob, topic_title)
        if len(activity_options) >= 2:
            alt = activity_options[occurrence_index % len(activity_options)]
            if _normalize_loose(alt) != _normalize_loose(matched_outcome):
                phrase_source = alt if occurrence_index % 2 else matched_outcome

    focus_for_type = phrase_source or activity
    if matched_outcome and activity and phrase_source == matched_outcome:
        focus_for_type = f"{activity}. {matched_outcome}"

    lesson_type = _type_from_activity(
        focus_for_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
    )

    phrase = _clean_source_phrase(phrase_source)
    planned_result = _cap_sentence(_shorten_clause(phrase)) if phrase else _cap_sentence(topic_title)
    if planned_result.casefold().startswith(
        ("учащийся сможет изучить тему", "изучает:", "выполняет:")
    ) or planned_result.startswith(("…", "...")):
        planned_result = _cap_sentence(topic_title) if topic_title.strip() else planned_result

    assessment_method = _control_for_activity(
        activity=_clean_source_phrase(phrase_source or activity) or planned_result,
        lesson_type=lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )
    return lesson_type, planned_result, assessment_method


def derive_planned_result(
    topic_title: str,
    theory_text: str,
    practice_text: str,
    lesson_type: str = "",
    program_content: str = "",
    *,
    theory_hours: int = 0,
    practice_hours: int = 0,
    occurrence_index: int = 0,
    knowledge_outcomes: tuple[str, ...] = (),
    skill_outcomes: tuple[str, ...] = (),
) -> str:
    """Планируемый результат: знание/умение темы или формулировка из содержания недели."""

    del lesson_type
    _, planned_result, _ = derive_pedagogical_fields(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=occurrence_index,
        knowledge_outcomes=knowledge_outcomes,
        skill_outcomes=skill_outcomes,
    )
    return planned_result


def derive_assessment_method(
    lesson_type: str,
    *,
    topic_title: str,
    theory_text: str,
    practice_text: str,
    program_content: str = "",
    theory_hours: int = 0,
    practice_hours: int = 0,
    planned_result: str = "",
) -> str:
    activity = planned_result or _select_activity_focus(
        topic_title=topic_title,
        theory_text=theory_text,
        practice_text=practice_text,
        program_content=program_content,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        occurrence_index=0,
    )
    return _control_for_activity(
        activity=activity,
        lesson_type=lesson_type,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
    )


def build_lesson_content(
    rows: tuple[CalendarContentRow, ...],
) -> tuple[LessonContentRow, ...]:
    result: list[LessonContentRow] = []
    topic_totals: dict[tuple[str | None, str, str], tuple[int, int]] = {}
    topic_occurrences: dict[tuple[str | None, str, str], int] = {}
    for row in rows:
        for part in row.week_parts or ():
            key = (part.topic_number, part.topic_title, part.section)
            theory, practice = topic_totals.get(key, (0, 0))
            topic_totals[key] = (theory + part.theory_hours, practice + part.practice_hours)

    for row in rows:
        warnings = list(row.warnings)
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
        primary_key = (row.topic_number, row.topic_title, row.section)
        occurrence_index = topic_occurrences.get(primary_key, 0)
        topic_occurrences[primary_key] = occurrence_index + 1

        for part in parts:
            topic_theory, topic_practice = topic_totals[
                (part.topic_number, part.topic_title, part.section)
            ]
            part_warnings: list[str] = list(part.warnings)
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
                elif part.theory_hours or part.practice_hours:
                    part_warnings.append(
                        "В программе нет явной границы теории и практики; текст не разделён."
                    )
            elif part.theory_hours or part.practice_hours:
                part_warnings.append("Нет программного содержания для заполнения занятия.")
            if part.theory_hours and not theory_text:
                part_warnings.append(
                    "Теоретическое занятие не заполнено: недостаточно данных источника."
                )
            if part.practice_hours and not practice_text:
                part_warnings.append(
                    "Практическое занятие не заполнено: недостаточно данных источника."
                )
            warnings.extend(part_warnings)
            if theory_text:
                theory_parts.append(theory_text)
            if practice_text:
                practice_parts.append(practice_text)
        theory_text = "\n".join(theory_parts)
        practice_text = "\n".join(practice_parts)
        program_content = row.program_content_full or ""

        lesson_type, planned_result, assessment_method = derive_pedagogical_fields(
            topic_title=row.topic_title,
            theory_text=theory_text,
            practice_text=practice_text,
            program_content=program_content,
            theory_hours=row.theory_hours,
            practice_hours=row.practice_hours,
            occurrence_index=occurrence_index,
            knowledge_outcomes=row.knowledge_outcomes,
            skill_outcomes=row.skill_outcomes,
        )
        result.append(
            LessonContentRow(
                source=row,
                theory_text=theory_text,
                practice_text=practice_text,
                lesson_type=lesson_type,
                planned_result=planned_result,
                assessment_method=assessment_method,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        )
    return tuple(result)


def calculate_fill_metrics(rows: tuple[LessonContentRow, ...]) -> FillMetrics:
    def percent(filled: int, applicable: int) -> float:
        return 100.0 if applicable == 0 else filled * 100.0 / applicable

    theory_applicable = [row for row in rows if row.source.theory_hours]
    practice_applicable = [row for row in rows if row.source.practice_hours]
    theory_filled = sum(bool(row.theory_text) for row in theory_applicable)
    practice_filled = sum(bool(row.practice_text) for row in practice_applicable)
    total_slots = len(theory_applicable) + len(practice_applicable) + 3 * len(rows)
    total_filled = theory_filled + practice_filled + sum(
        bool(value)
        for row in rows
        for value in (row.lesson_type, row.planned_result, row.assessment_method)
    )
    return FillMetrics(
        theory_percent=percent(theory_filled, len(theory_applicable)),
        practice_percent=percent(practice_filled, len(practice_applicable)),
        lesson_type_percent=percent(sum(bool(r.lesson_type) for r in rows), len(rows)),
        planned_result_percent=percent(sum(bool(r.planned_result) for r in rows), len(rows)),
        assessment_method_percent=percent(sum(bool(r.assessment_method) for r in rows), len(rows)),
        overall_percent=percent(total_filled, total_slots),
    )
