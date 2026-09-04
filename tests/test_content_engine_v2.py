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
    ("1.5", "Составляет план подготовки похода и план-график движения."),
    ("1.6", "Составляет меню и список продуктов для похода, готовит пищу на костре."),
    ("1.8", "Отрабатывает технику движения по дорогам, тропам, по пересеченной местности."),
    ("2.4", "Ориентирует карту по компасу."),
    ("3.1", "Характеризует природные особенности, историю родного края и известных земляков."),
    ("3.2", "Совершает прогулки и экскурсии по ближайшим окрестностям, посещает музеи, экскурсионные объекты г. Салавата и Башкортостана."),
    ("4.3", "Оказывает первую помощь условно пострадавшему."),
    ("5.4", "Выполняет упражнения на развитие выносливости."),
)

APPROVED_TP_CONTROL_TYPE = {
    "1.1": ("устный опрос по истории развития туризма в г. Салават", "теоретическое занятие"),
    "1.3": ("проверка укладки рюкзака и подгонки снаряжения", "практикум по работе со снаряжением"),
    "1.4": ("педагогическое наблюдение при развертывании и свертывании лагеря", "практикум"),
    "1.5": ("проверка плана подготовки похода и плана-графика движения", "проектно-практическое занятие"),
    "1.6": ("проверка меню и приготовления пищи на костре", "практикум по организации питания"),
    "1.8": ("педагогическое наблюдение за техникой движения", "учебно-тренировочное занятие на местности"),
    "2.4": ("проверка ориентирования карты по компасу", "топографический практикум"),
    "3.1": ("краеведческая викторина", "викторина"),
    "3.2": ("педагогическое наблюдение на экскурсии", "экскурсия"),
    "4.3": ("практическое задание по оказанию первой помощи условно пострадавшему", "практикум"),
    "5.4": ("педагогическое наблюдение за выполнением упражнений на развитие выносливости", "учебно-тренировочное занятие"),
}

GIGACHAT_REGRESSION_TOPICS = ("1.1", "1.3", "1.4", "1.5", "1.6")
GENERIC_CONTROLS = {"практическое задание", "демонстрация навыка"}


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
            r"(?i)(характеризует|называет|укладывает|подгоняет|развертывает|составляет|"
            r"отрабатывает|ориентирует|участвует|совершает|посещает|оказывает|"
            r"выполняет|готовит)",
            derived.planned_result,
        )


def test_results_keep_source_entities_without_invented_numbers() -> None:
    one = _fill_tp_topic("1.1")
    assert "г. Салават" in one.planned_result
    eight = _fill_tp_topic("3.2")
    assert "ближайшим окрестностям" in eight.planned_result
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
    assert derived.assessment_method == "педагогическое наблюдение за выполнением упражнений на постановку руки"
    assert derived.lesson_type == "учебно-тренировочное занятие"


def test_synthetic_chemistry_compose_equation() -> None:
    derived = fill_from_source(
        topic_title="Химические уравнения",
        program_content="Составление уравнений реакций.",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result == "Составляет уравнения реакций."
    assert derived.assessment_method == "проверка составления уравнений реакций"
    assert derived.lesson_type == "практикум"


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
    assert art.assessment_method == "проверка рисования натюрморта с натуры"


def test_synthetic_theory_characterizes_without_inventing_action() -> None:
    derived = fill_from_source(
        topic_title="Биография писателя",
        program_content="Биография писателя. Основные этапы творчества.",
        theory_hours=1,
        practice_hours=0,
    )
    assert derived.planned_result == "Характеризует биографию писателя."
    assert derived.assessment_method == "устный опрос по биографии писателя"
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
    assert derived.assessment_method.startswith("устный опрос")


def test_observation_control_follows_leading_action() -> None:
    derived = fill_from_source(
        topic_title="Рост растений",
        program_content="Практические занятия. Проведение наблюдений за ростом растений.",
        theory_hours=0,
        practice_hours=1,
    )
    assert derived.planned_result.startswith("Проводит наблюдения")
    assert derived.assessment_method == "проверка наблюдений за ростом растений"


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


AUDIT_REGRESSION_TOPICS = {
    "1.2": (
        "Характеризует роль туризма в подготовке к защите Родины, в выборе профессии и подготовке к предстоящей трудовой деятельности.",
        "устный опрос по роли туризма в подготовке к защите Родины",
    ),
    "1.5": (
        "Составляет план подготовки похода и план-график движения.",
        "проверка плана подготовки похода и плана-графика движения",
    ),
    "1.8": (
        "Отрабатывает технику движения по дорогам, тропам, по пересеченной местности.",
        "педагогическое наблюдение за техникой движения",
    ),
    "1.9": (
        "Отрабатывает технику преодоления естественных препятствий: склонов, подъёмов, организует переправу по бревну с самостраховкой.",
        "педагогическое наблюдение за техникой преодоления препятствий и самостраховкой",
    ),
    "2.2": (
        "Распознаёт знаки.",
        "топографический диктант",
    ),
    "2.3": (
        "Оценивает азимуты глазомерно и измеряет азимуты на карте (транспортиром).",
        "практическое задание по оценке и измерению азимутов",
    ),
    "2.6": (
        "Отбирает основные контрольные ориентиры на карте по заданному маршруту.",
        "проверка отбора ориентиров на карте",
    ),
    "4.1": (
        "Применяет средства личной гигиены в походах и во время тренировочного процесса.",
        "проверка применения средств личной гигиены",
    ),
    "5.1": (
        "Характеризует строение человеческого организма (органы и системы).",
        "устный опрос по строению человеческого организма (органы и системы)",
    ),
}

STABLE_TP_TOPICS = {
    "1.1": (
        "Характеризует историю развития туризма в г. Салават.",
        "устный опрос по истории развития туризма в г. Салават",
        "теоретическое занятие",
    ),
    "1.3": (
        "Укладывает рюкзаки, подгоняет снаряжение.",
        "проверка укладки рюкзака и подгонки снаряжения",
        "практикум по работе со снаряжением",
    ),
    "1.4": (
        "Развертывает и свертывает лагерь (бивак).",
        "педагогическое наблюдение при развертывании и свертывании лагеря",
        "практикум",
    ),
    "1.6": (
        "Составляет меню и список продуктов для похода, готовит пищу на костре.",
        "проверка меню и приготовления пищи на костре",
        "практикум по организации питания",
    ),
    "1.7": (
        "Выполняет обязанности по должностям в период подготовки.",
        "проверка исполнения обязанностей по должностям",
        "практикум",
    ),
    "1.10": (
        "Выступает в туристских соревнованиях в качестве участника.",
        "проверка участия в соревнованиях",
        "практикум",
    ),
    "1.11": (
        "Составляет отчёт о походе.",
        "проверка отчёта о походе",
        "практикум по подготовке отчёта",
    ),
    "2.1": (
        "Определяет масштаб.",
        "практическое задание по определению масштаба",
        "практикум",
    ),
    "2.4": (
        "Ориентирует карту по компасу.",
        "проверка ориентирования карты по компасу",
        "топографический практикум",
    ),
    "2.5": (
        "Измеряет свой средний шаг (пару шагов), строит графики перевода пар шагов в метры для разных условий ходьбы.",
        "проверка измерения шага и графика перевода пар шагов в метры",
        "практикум",
    ),
    "2.7": (
        "Определяет стороны горизонта по местным предметам, по Солнцу. Имитация ситуации потери ориентировки. Действия по восстановлению местонахождения.",
        "практическое задание по определению сторон горизонта и восстановлению ориентировки",
        "практикум",
    ),
    "3.1": (
        "Характеризует природные особенности, историю родного края и известных земляков.",
        "краеведческая викторина",
        "викторина",
    ),
    "3.2": (
        "Совершает прогулки и экскурсии по ближайшим окрестностям, посещает музеи, экскурсионные объекты г. Салавата и Башкортостана.",
        "педагогическое наблюдение на экскурсии",
        "экскурсия",
    ),
    "3.3": (
        "Подготавливает и заслушивает доклады по району предстоящего похода.",
        "проверка докладов по району предстоящего похода",
        "практикум",
    ),
    "3.4": (
        "Проводит краеведческие наблюдения.",
        "проверка краеведческих наблюдений",
        "занятие-наблюдение",
    ),
    "4.2": (
        "Формирует походную медицинскую аптечку.",
        "проверка состава походной медицинской аптечки",
        "практикум",
    ),
    "4.3": (
        "Оказывает первую помощь условно пострадавшему.",
        "практическое задание по оказанию первой помощи условно пострадавшему",
        "практикум",
    ),
    "4.4": (
        "Изготавливает носилки, волокуши, разучивает различные способы транспортировки пострадавшего.",
        "проверка изготовления носилок, волокуш и способов транспортировки",
        "практикум",
    ),
    "5.2": (
        "Ведёт дневник самоконтроля.",
        "проверка дневника самоконтроля",
        "практикум",
    ),
    "5.3": (
        "Выполняет упражнения для рук и плечевого пояса.",
        "педагогическое наблюдение за выполнением упражнений для рук и плечевого пояса",
        "учебно-тренировочное занятие",
    ),
    "5.4": (
        "Выполняет упражнения на развитие выносливости.",
        "педагогическое наблюдение за выполнением упражнений на развитие выносливости",
        "учебно-тренировочное занятие",
    ),
}


def test_audit_regression_nine_topics() -> None:
    dangling = re.compile(r"(?i)\b(ее|её|его|их)\s+(роль|значение|цель)\b")
    repeated_verb = re.compile(
        r"(?i)\b([а-яё]+(?:ет|ит|ёт|ут|ют))\b.+\b\1\b"
    )
    for number, (expected_result, expected_control) in AUDIT_REGRESSION_TOPICS.items():
        derived = _fill_tp_topic(number)
        assert derived.planned_result == expected_result, (
            f"{number}: {derived.planned_result!r} != {expected_result!r}"
        )
        assert derived.assessment_method == expected_control, (
            f"{number}: {derived.assessment_method!r} != {expected_control!r}"
        )
        assert not dangling.search(derived.planned_result)
        assert not repeated_verb.search(derived.planned_result)
        assert derived.planned_result.count("(") <= 1
        assert "краткие сведения" not in derived.planned_result.casefold()
        assert "гимнастик" not in derived.planned_result.casefold()


def test_remaining_21_topics_do_not_regress() -> None:
    assert len(STABLE_TP_TOPICS) == 21
    for number, (result, control, lesson_type) in STABLE_TP_TOPICS.items():
        derived = _fill_tp_topic(number)
        assert derived.planned_result == result, (
            f"{number}: {derived.planned_result!r} != {result!r}"
        )
        assert derived.assessment_method == control, (
            f"{number}: {derived.assessment_method!r} != {control!r}"
        )
        assert derived.lesson_type == lesson_type, (
            f"{number}: {derived.lesson_type!r} != {lesson_type!r}"
        )


def test_universal_result_cleanup_rules() -> None:
    pronoun = fill_from_source(
        topic_title="Воспитательная роль краеведения",
        program_content="Ее роль в развитии самостоятельности и выборе профессии.",
        theory_hours=1,
        practice_hours=0,
    )
    assert "ее роль" not in pronoun.planned_result.casefold()
    assert "роль краеведения" in pronoun.planned_result.casefold()

    repeated = fill_from_source(
        topic_title="Подготовка выступления",
        program_content="Практические занятия. Составление плана выступления. Составление плана-графика репетиций.",
        theory_hours=0,
        practice_hours=1,
    )
    assert repeated.planned_result.casefold().count("составляет") == 1
    assert " и " in repeated.planned_result
    assert "план-график" in repeated.assessment_method or "плана" in repeated.assessment_method

    long_list = fill_from_source(
        topic_title="Движение группы",
        program_content=(
            "Практические занятия. Отработка техники движения по дорогам, "
            "тропам, по пересеченной местности (лес, заросли кустарников, завалы, болото)."
        ),
        theory_hours=0,
        practice_hours=1,
    )
    assert "(" not in long_list.planned_result
    assert "заросли" not in long_list.planned_result.casefold()

    raw_list = fill_from_source(
        topic_title="Условные знаки карты",
        program_content=(
            "Практические занятия. Упражнения на запоминание знаков, "
            "Топографические диктанты, игры, мини соревнования."
        ),
        theory_hours=0,
        practice_hours=1,
    )
    assert raw_list.planned_result == "Распознаёт знаки."
    assert "диктант" not in raw_list.planned_result.casefold()
    assert "игр" not in raw_list.planned_result.casefold()

    wrapper = fill_from_source(
        topic_title="Краткие сведения о кровообращении",
        program_content="Краткие сведения о кровообращении и работе сердца.",
        theory_hours=1,
        practice_hours=0,
    )
    assert "краткие сведения" not in wrapper.planned_result.casefold()
    assert "кровообращени" in wrapper.planned_result.casefold()

    off_topic = fill_from_source(
        topic_title="Личная гигиена спортсмена",
        program_content=(
            "Гигиена тела и закаливание. "
            "Практические занятия. Разучивание комплекса упражнений гимнастики. "
            "Применение средств личной гигиены на тренировке."
        ),
        theory_hours=1,
        practice_hours=1,
    )
    assert "гимнастик" not in off_topic.planned_result.casefold()
    assert "гигиен" in off_topic.planned_result.casefold()


def test_gigachat_regression_five_topics() -> None:
    expected = {number: result for number, result in APPROVED_TP_TOPICS}
    for number in GIGACHAT_REGRESSION_TOPICS:
        derived = _fill_tp_topic(number)
        assert derived.planned_result == expected[number]
        control, lesson_type = APPROVED_TP_CONTROL_TYPE[number]
        assert derived.assessment_method == control
        assert derived.lesson_type == lesson_type
        assert derived.assessment_method not in GENERIC_CONTROLS
        assert " " in derived.planned_result


def test_all_tour_guides_topics_stay_specific() -> None:
    items, hours = _tp_program_and_hours()
    checked = 0
    for number in hours:
        if number not in items:
            continue
        derived = _fill_tp_topic(number)
        checked += 1
        assert derived.planned_result
        assert derived.assessment_method
        assert derived.assessment_method not in GENERIC_CONTROLS
        assert "норматив" not in derived.planned_result.casefold()
        assert not re.search(r"\d+\s*%", derived.planned_result)
    assert checked >= 30
