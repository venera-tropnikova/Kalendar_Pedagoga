from functools import lru_cache
from pathlib import Path

import pytest

from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2, derive_fields_v2
from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.parsing import parse_utp
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.resolve_utp import apply_workload_from_document
from calendar_pedagoga.scheduling import build_schedule


@lru_cache(maxsize=1)
def _key_y1_rows():
    local = Path(__file__).resolve().parents[1] / "references"
    frozen = Path(r"D:\Kalendar_Pedagoga\references")
    references = local if (local / "УТП КЛЮЧ 1 г. 2ч.docx").exists() else frozen
    utp_path = references / "УТП КЛЮЧ 1 г. 2ч.docx"
    program_path = references / "Программа КЛЮЧ.DOC"
    if not utp_path.exists() or not program_path.exists():
        pytest.skip("Нет исходников KEY Y1")
    utp = apply_workload_from_document(parse_utp(utp_path))
    study_year = infer_study_year_number(utp.metadata.study_year)
    program = parse_program(
        program_path.read_bytes(),
        program_path.name,
        study_year=study_year,
    )
    schedule = build_schedule(utp, "2026–2027")
    content = build_content_model(schedule, utp, program, utp_path.name)
    return build_lesson_content_v2(content)


def _theory(text: str, topic: str = "Теоретическая тема"):
    return derive_fields_v2(
        topic_title=topic,
        theory_text=text,
        practice_text="",
        program_content=text,
        theory_hours=2,
        practice_hours=0,
    )


def test_concept_colon_list_explains_quoted_values():
    actual = _theory("Понятия: ритм, темп, динамика.")
    assert actual.planned_result == (
        "Объясняет значения понятий «ритм», «темп», «динамика»."
    )
    assert actual.assessment_method == (
        "Устный опрос: понятия «ритм», «темп», «динамика»."
    )
    assert all(status == "COVERED" for _clause, status in actual.clause_coverage)


def test_purpose_standalone_and_parenthetical_explain_functions():
    standalone = _theory("Для чего нужны карта, компас и фонарь?")
    wrapped = _theory("Загадки прибора (для чего нужны шкала, стрелка, корпус).")
    assert standalone.planned_result.startswith("Объясняет функции ")
    assert "карты" in standalone.planned_result.casefold()
    assert "компаса" in standalone.planned_result.casefold()
    assert "фонаря" in standalone.planned_result.casefold()
    assert "тайны" not in standalone.planned_result.casefold()
    assert wrapped.planned_result.startswith("Объясняет функции ")
    assert "загадк" not in wrapped.planned_result.casefold()
    assert "тайны" not in wrapped.planned_result.casefold()
    assert "шкал" in wrapped.planned_result.casefold()
    assert standalone.assessment_method.casefold().startswith("устный опрос:")
    assert "функции" in standalone.assessment_method.casefold()


def test_classification_colon_distinguishes_source_categories():
    actual = _theory("Инструменты: духовые и ударные.")
    assert actual.planned_result == "Различает духовые и ударные инструменты."
    assert actual.assessment_method == (
        "Устный опрос: духовые и ударные инструменты."
    )


def test_dash_symbol_names_instrumental_symbol():
    actual = _theory("Флаг — символ государства.")
    assert actual.planned_result == "Называет флаг символом государства."
    assert actual.assessment_method == "Устный опрос: флаг как символ государства."


def test_knowledge_clauses_stay_separate_and_keep_proven_what_is():
    actual = _theory(
        "Что такое краеведение? "
        "Понятия: маршрут, ориентир. "
        "Для чего нужны карта и компас?"
    )
    assert "Объясняет, что такое краеведение." in actual.planned_result
    assert "Объясняет значения понятий «маршрут», «ориентир»." in actual.planned_result
    assert "Объясняет функции" in actual.planned_result
    assert actual.planned_result.count("Объясняет") == 3
    assert "тайны" not in actual.planned_result.casefold()
    assert all(status == "COVERED" for _clause, status in actual.clause_coverage)
    control = actual.assessment_method.casefold()
    assert "устный опрос" in control
    assert "маршрут" in control and "ориентир" in control
    assert "краеведение" in control
    assert "карт" in control and "компас" in control


def test_key_w18_plant_world_knowledge_frames():
    theory = (
        "Понятия: лес, поляна, луг, степь, болото. "
        "Тайны растений (для чего нужны корни, стебли, цветы, плоды). "
        "Деревья и кустарники: лиственные и хвойные. "
        "Курай – символ башкирского народа."
    )
    actual = _theory(theory, topic="Растительный мир Башкортостана")
    assert actual.planned_result == (
        "Объясняет значения понятий «лес», «поляна», «луг», «степь», «болото». "
        "Объясняет функции корней, стеблей, цветов и плодов. "
        "Различает лиственные и хвойные деревья и кустарники. "
        "Называет курай символом башкирского народа."
    )
    assert actual.assessment_method == (
        "Устный опрос: понятия «лес», «поляна», «луг», «степь», «болото»; "
        "функции корней, стеблей, цветов и плодов; "
        "лиственные и хвойные деревья и кустарники; "
        "курай как символ башкирского народа."
    )
    assert "тайны" not in actual.planned_result.casefold()
    assert "тайны" not in actual.assessment_method.casefold()
    assert all(status == "COVERED" for _clause, status in actual.clause_coverage)
    assert not any("NEEDS_REVIEW" in warning for warning in actual.warnings)


def test_key_w17_locative_and_signs_unchanged():
    practice = (
        "Животные и птицы в рисунках детей. "
        "Запрещающие знаки «Берегите природу»."
    )
    actual = derive_fields_v2(
        topic_title="Животный мир Башкортостана",
        theory_text="",
        practice_text=practice,
        program_content=(
            "В лесах нашего края живут звери: растительноядные, хищные, всеядные. "
            "Практика. " + practice
        ),
        theory_hours=0,
        practice_hours=2,
    )
    assert actual.planned_result == (
        "Рисует животных и птиц. Распознаёт запрещающие знаки «Берегите природу» "
        "и объясняет их значение."
    )
    assert actual.assessment_method == (
        "Просмотр рисунков; устный опрос по значению запрещающих знаков "
        "«Берегите природу»."
    )


def test_key_w4_family_games_and_drawings_unchanged():
    row = next(item for item in _key_y1_rows() if item.source.week_number == 4)
    assert row.planned_result == (
        "Участвует в подвижных играх, праздниках с участием родителей, "
        "выполняет рисунки. Проводит подвижные игры, праздники с участием родителей."
    )
    assert row.assessment_method == (
        "Педагогическое наблюдение: участие в подвижных играх, праздниках с "
        "участием родителей, рисунки и подвижные игры, праздники с участием родителей"
    )
