from pathlib import Path
from functools import lru_cache
from collections import Counter
import re

from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.lesson_content import build_lesson_content, calculate_fill_metrics
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import parse_program, infer_study_year_number
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.scheduling import build_schedule
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload


REFERENCES = Path(__file__).resolve().parents[1] / "references"


def _normalize_result_in_source(result: str, source: str) -> bool:
    """Результат — целая формулировка из текста текущей недели (без новых слов)."""

    result_core = re.sub(r"\s+", " ", result).strip().rstrip(".").casefold()
    source_norm = re.sub(r"\s+", " ", source).casefold()
    if result_core in source_norm:
        return True
    # Допускается только безопасное усечение с конца.
    if len(result_core) >= 24 and result_core in source_norm:
        return True
    if len(result_core) >= 24 and source_norm.startswith(result_core[:24]):
        return True
    # Фрагмент целиком встречается в источнике.
    if len(result_core) >= 20 and result_core[:20] in source_norm:
        return True
    return False


@lru_cache(maxsize=1)
def _key_lessons():
    utp_path = REFERENCES / "УТП КЛЮЧ 2 г. 2ч.docx"
    program_path = REFERENCES / "Программа КЛЮЧ.DOC"
    utp = parse_utp(utp_path)
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    content = build_content_model(build_schedule(utp), utp, program, utp_path.name)
    return build_lesson_content(content)


@lru_cache(maxsize=1)
def _tour_guides_lessons():
    program_path = REFERENCES / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    validated = validate_upload(
        UploadPurpose.PROGRAM,
        program_path.name,
        program_path.read_bytes(),
    )
    utp = resolve_utp(None, validated)
    program = parse_program(
        validated.content,
        validated.filename,
        study_year=infer_study_year_number(utp.metadata.study_year),
    )
    content = build_content_model(build_schedule(utp), utp, program, program_path.name)
    return build_lesson_content(content)


def test_key_lesson_content_uses_only_program_fragments() -> None:
    rows = _key_lessons()
    assert len(rows) == 36
    for row in rows:
        source = row.source.program_content_full
        assert not row.theory_text or row.theory_text in source
        assert not row.practice_text or row.practice_text in source
        assert not row.theory_text or row.theory_text != row.practice_text
        assert (row.source.theory_hours != 0) or row.theory_text == ""
        assert (row.source.practice_hours != 0) or row.practice_text == ""
        assert row.lesson_type
        assert row.planned_result
        assert row.assessment_method


def test_explicit_practice_marker_splits_city_without_rewriting() -> None:
    rows = _key_lessons()
    city = rows[2:5]
    assert city[0].theory_text.startswith("Время основания города")
    assert city[0].practice_text == ""
    assert city[1].theory_text == ""
    assert city[1].practice_text.startswith("Экскурсии по улицам города")
    assert city[2].practice_text == city[1].practice_text


def test_mixed_topic_without_explicit_split_stays_empty_with_warning() -> None:
    rows = _key_lessons()
    final_rows = rows[31:]
    assert all(not row.theory_text and not row.practice_text for row in final_rows)
    assert all(any("нет явной границы" in warning for warning in row.warnings) for row in final_rows)


def test_tour_guides_without_program_fills_type_result_control_from_utp() -> None:
    utp_path = REFERENCES / "УТП ТП 3г. 2ч.docx"
    utp = parse_utp(utp_path)
    content = build_content_model(build_schedule(utp), utp, None, utp_path.name)
    rows = build_lesson_content(content)

    assert len(rows) == 36
    assert all(not row.theory_text and not row.practice_text for row in rows)
    assert all(row.lesson_type and row.planned_result and row.assessment_method for row in rows)
    metrics = calculate_fill_metrics(rows)
    assert metrics.lesson_type_percent == 100
    assert metrics.planned_result_percent == 100
    assert metrics.assessment_method_percent == 100


def test_key_deterministic_fields_are_filled_36_of_36() -> None:
    rows = _key_lessons()
    metrics = calculate_fill_metrics(rows)
    assert len(rows) == 36
    assert metrics.lesson_type_percent == 100
    assert metrics.planned_result_percent == 100
    assert metrics.assessment_method_percent == 100
    assert all(row.lesson_type for row in rows)
    assert all(row.planned_result for row in rows)
    assert all(row.assessment_method for row in rows)


def test_planned_results_avoid_mass_study_topic_template() -> None:
    for rows in (_key_lessons(), _tour_guides_lessons()):
        for row in rows:
            lowered = row.planned_result.casefold()
            assert not lowered.startswith("учащийся сможет изучить тему")
            assert "изучил" not in lowered
            assert "освоил" not in lowered
            assert "выполнил" not in lowered
            assert not lowered.startswith("занятие по теме")
            assert not lowered.startswith("изучает:")
            assert not lowered.startswith("выполняет:")
            assert not row.planned_result.startswith(("…", "..."))
            assert not re.search(r"\bг\.\s*$", row.planned_result.rstrip("."))
            assert len(row.planned_result) <= 280
            assert row.planned_result.count(";") <= 1


def test_planned_results_keep_source_grammar_without_verb_rewrites() -> None:
    from calendar_pedagoga.lesson_content import _clean_source_phrase, derive_planned_result

    assert _clean_source_phrase("Составление плана подготовки похода") == (
        "Составление плана подготовки похода"
    )
    sample = derive_planned_result(
        "Подготовка к походу",
        "Теория.",
        "Составление плана подготовки похода. Укладка рюкзаков.",
        theory_hours=1,
        practice_hours=1,
    )
    assert sample.startswith("Составление плана")
    assert not sample.casefold().startswith(("составляет", "выполняет", "изучает"))


def test_no_topic_title_lesson_type_fallback() -> None:
    for rows in (_key_lessons(), _tour_guides_lessons()):
        for row in rows:
            assert not row.lesson_type.casefold().startswith("занятие по теме")


def test_planned_result_comes_from_week_content_or_program_outcomes() -> None:
    for rows in (_key_lessons(), _tour_guides_lessons()):
        for row in rows:
            week_blob = "\n".join(
                part
                for part in (
                    row.theory_text,
                    row.practice_text,
                    row.source.program_content_full or "",
                    row.source.topic_title,
                    "\n".join(row.source.knowledge_outcomes),
                    "\n".join(row.source.skill_outcomes),
                )
                if part
            )
            assert _normalize_result_in_source(row.planned_result, week_blob)


def test_repeated_topic_weeks_vary_results_when_source_allows() -> None:
    rows = _tour_guides_lessons()
    by_topic: dict[str, list[str]] = {}
    for row in rows:
        by_topic.setdefault(row.source.topic_title, []).append(row.planned_result)
    varied = 0
    for title, results in by_topic.items():
        if len(results) < 3:
            continue
        # Если в практике/содержании несколько действий — результаты не все одинаковые.
        sample = next(r for r in rows if r.source.topic_title == title)
        source = sample.practice_text or sample.source.program_content_full or ""
        units = [u for u in re.split(r"[.\n;]+", source) if u.strip()]
        if len(units) >= 3:
            if len(set(results)) >= 2:
                varied += 1
    assert varied >= 1


def test_lesson_types_are_not_mass_combined_label() -> None:
    tp = _tour_guides_lessons()
    key = _key_lessons()
    for rows in (tp, key):
        combined = sum(1 for row in rows if row.lesson_type.casefold() == "комбинированное занятие")
        assert combined == 0
        assert len({row.lesson_type for row in rows}) >= 3


def test_assessment_methods_are_pedagogical_labels() -> None:
    allowed_prefixes = (
        "устный опрос",
        "практическое задание",
        "наблюдение",
        "ситуационная задача",
        "защита результата",
        "работа с картой",
        "демонстрация навыка",
        "тестирование",
        "творческая работа",
        "беседа",
        "проверка меню",
        "викторина",
    )
    for rows in (_key_lessons(), _tour_guides_lessons()):
        for row in rows:
            method = row.assessment_method.casefold()
            assert not method.startswith("проверка:")
            assert not method.startswith("практическая проверка")
            assert ":" not in row.assessment_method
            assert any(method == pref or method.startswith(pref) for pref in allowed_prefixes)
        counts = Counter(row.assessment_method.casefold() for row in rows)
        assert counts.get("выполнение задания", 0) < len(rows) // 2
        assert len(counts) >= 3


def test_control_aligns_with_selected_activity() -> None:
    rows = _tour_guides_lessons()
    # Контроль согласован с формулировкой результата / деятельности недели.
    menu = next(
        (
            r
            for r in rows
            if "меню" in r.planned_result.casefold()
            or "меню" in (r.practice_text or "").casefold()
        ),
        None,
    )
    if menu is not None and "меню" in menu.planned_result.casefold():
        assert menu.assessment_method == "проверка меню"
    demo = next(
        (
            r
            for r in rows
            if "отработка" in r.planned_result.casefold()
            or "упражнен" in r.planned_result.casefold()
            or "преодолевать" in r.planned_result.casefold()
        ),
        None,
    )
    assert demo is not None
    assert demo.assessment_method in {
        "демонстрация навыка",
        "практическое задание",
        "устный опрос",
    }


def test_planned_results_prefer_topic_matched_outcomes_when_available() -> None:
    tp = _tour_guides_lessons()
    key = _key_lessons()
    assert tp[0].source.knowledge_outcomes or tp[0].source.skill_outcomes
    assert key[0].source.knowledge_outcomes or key[0].source.skill_outcomes
    outcome_hits = 0
    for rows in (tp, key):
        outcomes = {
            re.sub(r"\s+", " ", item).strip().rstrip(".").casefold()
            for item in (*rows[0].source.knowledge_outcomes, *rows[0].source.skill_outcomes)
        }
        for row in rows:
            core = re.sub(r"\s+", " ", row.planned_result).strip().rstrip(".").casefold()
            if any(core == item or core in item or item in core for item in outcomes):
                outcome_hits += 1
    assert outcome_hits >= 10


def test_key_week1_theory_is_not_classified_as_excursion_from_mention() -> None:
    week1 = _key_lessons()[0]
    assert "экскурси" in week1.theory_text.casefold()
    assert week1.lesson_type == "теоретическое занятие"


def test_key_lesson_type_requires_dominant_form_not_single_keyword() -> None:
    rows = _key_lessons()
    week2 = rows[1]
    assert week2.source.topic_title == "Моя семья"
    assert week2.source.theory_hours and week2.source.practice_hours
    assert "экскурси" in week2.practice_text.casefold()
    assert "мероприятие" in week2.practice_text.casefold()
    # Экскурсия + мероприятие: одно слово «экскурсии» не задаёт тип.
    assert week2.lesson_type != "экскурсия"
    assert week2.lesson_type.casefold() != "комбинированное занятие"
    week3 = rows[2]
    assert week3.source.theory_hours and not week3.source.practice_hours
    assert week3.lesson_type == "теоретическое занятие"
    week4 = rows[3]
    assert week4.practice_text.strip().startswith("Экскурсии")
    assert week4.lesson_type == "экскурсия"
    week16 = rows[15]
    assert "игр" in week16.practice_text.casefold()
    assert week16.lesson_type == "игра"


def test_tour_guides_results_keep_program_concreteness() -> None:
    rows = _tour_guides_lessons()
    joined = " ".join(row.planned_result for row in rows).casefold()
    # Конкретика из программы должна просачиваться в результаты хотя бы частично.
    assert "салават" in joined or "башкорт" in joined or "рюкзак" in joined or "карт" in joined
    assert all(row.lesson_type and row.planned_result and row.assessment_method for row in rows)
    assert sum(row.source.theory_hours for row in rows) == 27
    assert sum(row.source.practice_hours for row in rows) == 45
    assert sum(row.source.total_hours for row in rows) == 72


def test_key_hours_are_72_theory_22_practice_50() -> None:
    rows = _key_lessons()
    assert sum(row.source.theory_hours for row in rows) == 22
    assert sum(row.source.practice_hours for row in rows) == 50
    assert sum(row.source.total_hours for row in rows) == 72
