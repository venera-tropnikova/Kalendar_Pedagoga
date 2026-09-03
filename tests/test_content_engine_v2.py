from functools import lru_cache
from pathlib import Path
import re

from calendar_pedagoga.content_engine_v2 import fill_from_source
from calendar_pedagoga.program_parsing import infer_study_year_number, parse_program
from calendar_pedagoga.resolve_utp import resolve_utp
from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload


REFERENCES = Path(__file__).resolve().parents[1] / "references"

APPROVED_TP_TOPICS = (
    ("1.1", "Характеризует историю развития туризма в г. Салават."),
    ("1.3", "Укладывает рюкзаки, подгоняет снаряжение."),
    ("1.4", "Развертывает и свертывает лагерь (бивак)."),
    ("1.6", "Составляет меню и список продуктов для похода."),
    ("1.8", "Отрабатывает технику движения по дорогам, тропам, по пересеченной местности (лес, заросли кустарников, завалы, заболоченная местность)."),
    ("2.4", "Ориентирует карту по компасу."),
    ("3.1", "Участвует в краеведческих викторинах."),
    ("3.2", "Совершает прогулки и экскурсии по ближайшим окрестностям, посещает музеи, экскурсионные объекты г. Салавата и Башкортостана."),
    ("4.3", "Оказывает первую помощь условно пострадавшему (определяет травму или ставит диагноз, практически оказывает помощь)."),
    ("5.4", "Выполняет упражнения на развитие выносливости."),
)

APPROVED_TP_CONTROL_TYPE = {
    "1.1": ("устный опрос", "теоретическое занятие"),
    "1.3": ("демонстрация навыка", "практическое занятие"),
    "1.4": ("демонстрация навыка", "практическое занятие"),
    "1.6": ("практическое задание", "практическое занятие"),
    "1.8": ("демонстрация навыка", "тренировочное занятие"),
    "2.4": ("практическое задание", "практическое занятие"),
    "3.1": ("викторина", "практическое занятие"),
    "3.2": ("наблюдение", "экскурсия"),
    "4.3": ("демонстрация навыка", "практическое занятие"),
    "5.4": ("демонстрация навыка", "тренировочное занятие"),
}


@lru_cache(maxsize=1)
def _tp_program_and_hours():
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
    hours = {
        topic.number: (topic.hours.theory, topic.hours.practice)
        for topic in utp.topics
        if topic.number
    }
    items = {item.number: item for item in program.content_items if item.number}
    return items, hours


def _fill_tp_topic(number: str):
    items, hours = _tp_program_and_hours()
    item = items[number]
    theory, practice = hours[number]
    return fill_from_source(
        topic_title=item.title,
        program_content=item.content,
        theory_hours=theory,
        practice_hours=practice,
    )


def test_approved_tour_guides_results() -> None:
    for number, expected in APPROVED_TP_TOPICS:
        derived = _fill_tp_topic(number)
        assert derived.planned_result == expected, (
            f"{number}: {derived.planned_result!r} != {expected!r}"
        )


def test_approved_tour_guides_control_and_type() -> None:
    for number, (control, lesson_type) in APPROVED_TP_CONTROL_TYPE.items():
        derived = _fill_tp_topic(number)
        assert derived.assessment_method == control, (
            f"{number}: control {derived.assessment_method!r} != {control!r}"
        )
        assert derived.lesson_type == lesson_type, (
            f"{number}: type {derived.lesson_type!r} != {lesson_type!r}"
        )


def test_results_are_present_tense_not_verbal_nouns() -> None:
    banned_starts = (
        "составление",
        "укладка",
        "оказание",
        "ориентирование",
        "отработка",
        "проведение",
        "упражнение",
    )
    for number, _expected in APPROVED_TP_TOPICS:
        derived = _fill_tp_topic(number)
        start = derived.planned_result.casefold()
        assert not start.startswith(banned_starts)
        assert re.match(
            r"(?i)(характеризует|укладывает|подгоняет|развертывает|составляет|"
            r"отрабатывает|ориентирует|участвует|совершает|посещает|оказывает|"
            r"выполняет)",
            derived.planned_result,
        )


def test_results_keep_source_entities_without_invented_numbers() -> None:
    one = _fill_tp_topic("1.1")
    assert "г. Салават" in one.planned_result
    eight = _fill_tp_topic("3.2")
    assert "г. Салавата" in eight.planned_result
    assert "Башкортостана" in eight.planned_result
    for number, _expected in APPROVED_TP_TOPICS:
        derived = _fill_tp_topic(number)
        assert not re.search(r"\d+\s*%", derived.planned_result)
        assert "секунд" not in derived.planned_result.casefold()
        assert "норматив" not in derived.planned_result.casefold()


def test_quiz_stays_participatory_unless_source_says_student_conducts() -> None:
    quiz = fill_from_source(
        topic_title="Литературная викторина",
        program_content="Проведение викторины по прочитанным главам.",
        theory_hours=0,
        practice_hours=1,
    )
    assert quiz.planned_result == "Участвует в викторине по прочитанным главам."
    assert quiz.assessment_method == "викторина"

    conducted = fill_from_source(
        topic_title="Литературная викторина",
        program_content="Учащиеся проводят викторину по прочитанным главам.",
        theory_hours=0,
        practice_hours=1,
    )
    assert conducted.planned_result.startswith("Проводит викторину")
    assert "прочитанным главам" in conducted.planned_result


def test_exercise_word_uses_performs_exercises() -> None:
    derived = fill_from_source(
        topic_title="Постановка руки",
        program_content="Практические занятия. Упражнение на постановку руки.",
        theory_hours=0,
        practice_hours=1,
    )
    assert derived.planned_result == "Выполняет упражнения на постановку руки."
    assert derived.assessment_method == "демонстрация навыка"
    assert derived.lesson_type == "тренировочное занятие"


def test_synthetic_chemistry_compose_equation() -> None:
    derived = fill_from_source(
        topic_title="Химические уравнения",
        program_content="Составление уравнений реакций.",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result == "Составляет уравнения реакций."
    assert derived.assessment_method == "практическое задание"
    assert derived.lesson_type == "практическое занятие"


def test_synthetic_music_and_art_keep_source_objects() -> None:
    music = fill_from_source(
        topic_title="Гамма до мажор",
        program_content="Практические занятия. Разучивание гаммы до мажор.",
        theory_hours=0,
        practice_hours=1,
    )
    assert music.planned_result == "Разучивает гамму до мажор."
    assert "до мажор" in music.planned_result

    art = fill_from_source(
        topic_title="Натюрморт",
        program_content="Практические занятия. Рисование натюрморта с натуры.",
        theory_hours=0,
        practice_hours=1,
    )
    assert art.planned_result == "Рисует натюрморт с натуры."
    assert art.assessment_method == "практическое задание"


def test_synthetic_theory_characterizes_without_inventing_action() -> None:
    derived = fill_from_source(
        topic_title="Биография писателя",
        program_content="Биография писателя. Основные этапы творчества.",
        theory_hours=1,
        practice_hours=0,
    )
    assert derived.planned_result == "Характеризует биографию писателя."
    assert derived.assessment_method == "устный опрос"
    assert derived.lesson_type == "теоретическое занятие"
    assert "организует" not in derived.planned_result.casefold()


def test_synthetic_measurement_does_not_invent_numbers() -> None:
    derived = fill_from_source(
        topic_title="Температура жидкости",
        program_content="Практические занятия. Измерение температуры жидкости.",
        theory_hours=0,
        practice_hours=1,
    )
    assert derived.planned_result == "Измеряет температуру жидкости."
    assert not re.search(r"\d+", derived.planned_result)


def test_institutional_organization_is_not_student_organizing() -> None:
    derived = fill_from_source(
        topic_title="История школы",
        program_content="Организация школьного музея в 1970-е годы.",
        theory_hours=1,
        practice_hours=0,
    )
    assert derived.planned_result.startswith("Характеризует организацию")
    assert "организует" not in derived.planned_result.casefold()
    assert derived.lesson_type == "теоретическое занятие"


def test_verbal_nouns_conjugate_by_suffix_not_word_list() -> None:
    diary = fill_from_source(
        topic_title="Дневник",
        program_content="Ведение дневника наблюдений.",
        theory_hours=0,
        practice_hours=1,
    )
    assert diary.planned_result == "Ведёт дневник наблюдений."

    talk = fill_from_source(
        topic_title="Доклад",
        program_content="Выступление с докладом.",
        theory_hours=0,
        practice_hours=1,
    )
    assert talk.planned_result.startswith("Выступает")
    assert "докладом" in talk.planned_result


def test_object_case_agrees_after_verbal_noun() -> None:
    duties = fill_from_source(
        topic_title="Обязанности",
        program_content="Выполнение обязанностей по плану.",
        theory_hours=0,
        practice_hours=1,
    )
    assert duties.planned_result == "Выполняет обязанности по плану."

    gear = fill_from_source(
        topic_title="Снаряжение",
        program_content="Подготовка личного и общественного снаряжения.",
        theory_hours=0,
        practice_hours=1,
    )
    assert gear.planned_result == "Подготавливает личное и общественное снаряжение."


def test_homogeneous_actions_transform_separately() -> None:
    derived = fill_from_source(
        topic_title="Чертёж",
        program_content="Измерение длины отрезка, построения чертежа.",
        theory_hours=0,
        practice_hours=1,
    )
    assert "Измеряет длину отрезка" in derived.planned_result
    assert "строит чертёж" in derived.planned_result.casefold() or "строит чертеж" in derived.planned_result.casefold()


def test_effect_is_not_student_action() -> None:
    derived = fill_from_source(
        topic_title="Память и чтение",
        program_content=(
            "Краткие сведения о памяти. "
            "Совершенствование функций памяти под воздействием чтения."
        ),
        theory_hours=1,
        practice_hours=1,
    )
    assert "совершенствует функций" not in derived.planned_result.casefold()
    assert derived.lesson_type == "теоретическое занятие"
    assert derived.assessment_method == "устный опрос"


def test_observation_control_follows_leading_action() -> None:
    derived = fill_from_source(
        topic_title="Рост растений",
        program_content="Практические занятия. Проведение наблюдений за ростом растений.",
        theory_hours=0,
        practice_hours=1,
    )
    assert derived.planned_result.startswith("Проводит наблюдения")
    assert derived.assessment_method == "наблюдение"


def test_game_type_only_when_leading() -> None:
    derived = fill_from_source(
        topic_title="Орфография",
        program_content="Практические занятия. Упражнения на правило, игры.",
        theory_hours=0,
        practice_hours=1,
    )
    assert derived.lesson_type != "игра"
    assert derived.planned_result.startswith("Выполняет упражнения")


def test_capacity_role_agrees_with_singular_student() -> None:
    contest = fill_from_source(
        topic_title="Конкурс чтецов",
        program_content="Выступление на конкурсе чтецов в качестве участников.",
        theory_hours=0,
        practice_hours=1,
    )
    assert "в качестве участника" in contest.planned_result.casefold()
    assert "участников" not in contest.planned_result.casefold()

    helpers = fill_from_source(
        topic_title="Лабораторная работа",
        program_content="Работа в лаборатории в качестве помощников.",
        theory_hours=0,
        practice_hours=1,
    )
    assert "в качестве помощника" in helpers.planned_result.casefold()

    already = fill_from_source(
        topic_title="Практика в музее",
        program_content="Работа в музее в качестве помощника экскурсовода.",
        theory_hours=0,
        practice_hours=1,
    )
    assert "в качестве помощника" in already.planned_result.casefold()

    young = fill_from_source(
        topic_title="Школьный театр",
        program_content="Выступление на сцене в качестве юных актёров.",
        theory_hours=0,
        practice_hours=1,
    )
    assert "в качестве юного актёра" in young.planned_result.casefold()
    assert "актёров" not in young.planned_result.casefold()


def test_multiweek_rotates_source_clauses() -> None:
    first = fill_from_source(
        topic_title="Физическая подготовка",
        program_content=(
            "Практические занятия. Упражнение на развитие выносливости. "
            "Упражнения на развитие быстроты."
        ),
        theory_hours=0,
        practice_hours=2,
        occurrence_index=0,
    )
    second = fill_from_source(
        topic_title="Физическая подготовка",
        program_content=(
            "Практические занятия. Упражнение на развитие выносливости. "
            "Упражнения на развитие быстроты."
        ),
        theory_hours=0,
        practice_hours=2,
        occurrence_index=1,
    )
    assert "выносливости" in first.planned_result
    assert "быстроты" in second.planned_result
    assert first.planned_result != second.planned_result
