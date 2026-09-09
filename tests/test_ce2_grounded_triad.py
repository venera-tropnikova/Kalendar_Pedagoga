"""Grounding checks: forms and controls cannot supply missing programme facts."""

import re
import os
from pathlib import Path

import pytest

from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    ContentEngineV2Result,
    _aggregate_week_lesson_type,
    _noun_gen_to_acc,
    _noun_nom_to_acc,
    _observable_result,
    _phrase_to_genitive,
    _quality_issue,
    _salvage_proven_finite_result,
    build_lesson_content_v2,
    control_from_frame,
    fill_from_source,
    type_from_frame,
    derive_fields_v2,
    select_source_clause,
)
from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.lesson_content import is_single_pedagogical_lesson_type


def test_row_local_practice_cannot_be_replaced_by_whole_program_theory():
    from calendar_pedagoga.content_engine_v2 import derive_fields_v2, select_source_clause
    kwargs = dict(topic_title="Животный мир", theory_text="",
                  practice_text="Животные в рисунках детей. Запрещающие знаки.",
                  program_content="Животный мир. Птицы: перелетные, оседлые, зимующие.",
                  theory_hours=0, practice_hours=2)
    clause, theory_only, pool = select_source_clause(**kwargs)
    assert not theory_only
    assert "Птицы" not in clause
    assert all("Птицы" not in part for part in pool)
    actual = derive_fields_v2(**kwargs)
    assert actual.planned_result == "Выполняет практическое задание по теме „Животный мир“."
    assert "птиц" not in actual.planned_result.casefold()
    assert actual.practice_text == kwargs["practice_text"]


def test_real_key1_all_36_weeks_keep_sources_and_hours():
    source_name = os.environ.get("CALENDAR_KEY1_DOC")
    if not source_name:
        pytest.skip("Set CALENDAR_KEY1_DOC to the real user regression document")
    from calendar_pedagoga.content_generation import build_content_model
    from calendar_pedagoga.program_parsing import parse_program
    from calendar_pedagoga.resolve_utp import resolve_utp
    from calendar_pedagoga.scheduling import build_schedule
    from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload
    source = Path(source_name)
    upload = validate_upload(UploadPurpose.PROGRAM, source.name, source.read_bytes())
    utp = resolve_utp(None, upload)
    program = parse_program(upload.content, upload.filename, study_year=1)
    schedule = build_schedule(utp, "2026–2027")
    rows = build_content_model(schedule, utp, program, source.name)
    before = repr(rows)
    generated = build_lesson_content_v2(rows)
    assert len(generated) == len(schedule.weeks) == 36
    assert (utp.table_totals.total, utp.table_totals.theory, utp.table_totals.practice) == (72, 22, 50)
    assert sum(row.theory_hours for row in rows) == 22
    assert sum(row.practice_hours for row in rows) == 50
    # Unnumbered topics are distinct positions too; number alone is not an ID.
    assert len({(part.topic_number, part.topic_title) for row in rows for part in row.week_parts}) == 19
    assert repr(rows) == before
    assert generated == build_lesson_content_v2(rows)
    for source_row, lesson in zip(rows, generated):
        assert lesson.source is source_row
        text = f"{lesson.planned_result} {lesson.assessment_method}".casefold()
        assert not any(bad in text for bad in ("имену", "в проведение", "ролевых игре", "подвижныхе", "развлечает", "предприявает", "занявает", "природныму"))
        assert text.count("„") == text.count("“")
        titles = {part.topic_title.strip(" .") for part in source_row.week_parts}
        for title in re.findall("„([^“]+)“", lesson.planned_result):
            assert title in titles
    assert generated[16].planned_result == "Выполняет практическое задание по теме „Животный мир Башкортостана“."
    assert all(is_single_pedagogical_lesson_type(row.lesson_type) for row in generated)
    assert generated[0].lesson_type == "теоретико-практическое занятие"
    assert generated[21].lesson_type == "теоретико-практическое занятие"
    assert generated[25].lesson_type == "теоретико-практическое занятие"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Выполняет упражнения по определению масштаба.", "Определяет масштаб."),
        ("Выполняет упражнения по определению сторон горизонта по Солнцу.",
         "Определяет стороны горизонта по Солнцу."),
        ("Выполняет упражнения на глазомерную оценку азимутов и упражнения на инструментальное измерение азимутов на карте (транспортиром).",
         "Оценивает азимуты глазомерно и измеряет азимуты на карте (транспортиром)."),
        ("Выполняет упражнения для мышц шеи.", "Выполняет упражнения для мышц шеи."),
        ("Выполняет упражнения на постановку руки.", "Выполняет упражнения на постановку руки."),
        ("Выполняет упражнения по определению масштаба, измерению расстояния на карте.",
         "Определяет масштаб и измеряет расстояние на карте."),
        (
            "Выполняет упражнения по отбору основных контрольных ориентиров на карте "
            "по заданному маршруту, отысканию на карте сходных (параллельных) ситуаций, "
            "определению способов привязки.",
            "Отбирает основные контрольные ориентиры на карте по заданному маршруту, "
            "находит сходные (параллельные) ситуации и определяет способы привязки.",
        ),
        ("Составляет план подготовки похода и план-график движения.",
         "Составляет план подготовки похода и план-график движения."),
        ("Характеризует строение человеческого организма (органы и системы).",
         "Характеризует строение человеческого организма (органы и системы)."),
    ],
)
def test_observable_operations_preserve_objects_and_conditions(before, after):
    assert _observable_result(before) == after


def test_report_does_not_imply_defence_or_checklist():
    result = fill_from_source(
        topic_title="Отчёт", program_content="Составление отчёта о наблюдениях.",
        practice_hours=1, theory_hours=0,
    )
    assert result.planned_result == "Составляет отчёт о наблюдениях."
    assert result.lesson_type == "практикум по подготовке отчёта"
    assert result.assessment_method == "проверка отчёта о наблюдениях"


@pytest.mark.parametrize(
    ("result", "clause", "expected"),
    [
        ("Рисует картину с натуры.", "Рисование картины с натуры.", "практикум"),
        ("Измеряет температуру.", "Измерение температуры.", "практикум"),
        ("Ведёт дневник наблюдений.", "Ведение дневника наблюдений.", "практикум"),
        ("Ориентирует карту по компасу.", "Ориентирование карты по компасу.", "практикум по ориентированию"),
        ("Исследует рост растений.", "Исследование роста растений. Сравнение измерений.", "исследовательское занятие"),
        ("Характеризует рост растений.", "Изучение роста растений.", "практическое занятие"),
    ],
)
def test_form_requires_evidence_in_selected_activity(result, clause, expected):
    frame = ActionFrame(clause, "", "", "")
    assert type_from_frame(
        frame, planned_result=result, theory_hours=0, practice_hours=1,
        theory_text="", practice_text=clause,
        program_content=clause + " Исследование, экскурсия, защита проекта.",
    ) == expected


def test_control_cannot_take_product_from_unselected_source():
    frame = ActionFrame("Измерение температуры.", "", "", "")
    assert control_from_frame(
        frame, planned_result="Измеряет температуру жидкости.",
        lesson_type="практикум", theory_hours=0, practice_hours=1,
    ) == "практическое задание по измерению температуры жидкости"


def test_missing_practical_source_never_invents_practical_scenario():
    result = fill_from_source(
        topic_title="Строение растения", program_content="Строение растения.",
        theory_hours=1, practice_hours=1,
    )
    assert result.lesson_type == "теоретическое занятие"
    assert result.assessment_method.startswith("устный опрос")
    assert not any(word in str(result).lower() for word in ("чек-лист", "защита", "маршрутное задание"))


def test_mixed_hours_form_mentions_are_not_activity_without_practice_source():
    source = (
        "Профилактика заболеваний. Личная гигиена. Значение водных процедур. "
        "Как правильно одеться на прогулку, экскурсию, в поход."
    )
    result = fill_from_source(
        topic_title="Профилактика заболеваний",
        program_content=source,
        theory_hours=1,
        practice_hours=1,
    )
    assert result.lesson_type == "теоретическое занятие"
    assert "экскурс" not in result.lesson_type.casefold()
    assert result.assessment_method.startswith("устный опрос")


def test_safe_fallback_does_not_reopen_other_practice_slots():
    practice = (
        "Экскурсия по улицам микрорайона. "
        "Мой любимый уголок микрорайона. "
        "Анализ ситуаций, игры-фантазии."
    )
    result = derive_fields_v2(
        topic_title="Мой микрорайон",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=1,
        practice_appearance_count=2,
    )
    assert result.frame.clause == (
        "Мой любимый уголок микрорайона. Анализ ситуаций, игры-фантазии"
    )
    assert result.lesson_type == "практическое занятие"
    assert "экскурс" not in (
        f"{result.lesson_type} {result.planned_result} {result.assessment_method}"
    ).casefold()


def test_analysis_with_trailing_game_keeps_analysis_action():
    result = derive_fields_v2(
        topic_title="Ситуации общения",
        theory_text="",
        practice_text="Анализ ситуаций, игры-фантазии.",
        program_content="Анализ ситуаций, игры-фантазии.",
        theory_hours=0,
        practice_hours=2,
        occurrence_index=0,
        practice_appearance_count=2,
    )
    low = result.planned_result.casefold()
    assert low.startswith("анализирует")
    assert "участвует в анализ" not in low
    assert not result.planned_result.startswith("Выполняет практическое задание")


def test_conducting_games_is_grounded_activity_not_generic():
    result = derive_fields_v2(
        topic_title="Общение",
        theory_text="",
        practice_text="Проведение дидактических и ролевых игр.",
        program_content="Проведение дидактических и ролевых игр.",
        theory_hours=0,
        practice_hours=2,
        occurrence_index=0,
        practice_appearance_count=2,
    )
    low = result.planned_result.casefold()
    assert low.startswith("проводит")
    assert "участвует" not in low
    assert not result.planned_result.startswith("Выполняет практическое задание")
    assert "дидактическ" in low and "ролев" in low


def test_neighbor_game_mention_does_not_override_leading_action():
    result = derive_fields_v2(
        topic_title="План местности",
        theory_text="",
        practice_text="Составление плана местности, игры на внимание.",
        program_content="Составление плана местности, игры на внимание.",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert low.startswith("составляет")
    assert "участвует" not in low
    assert result.lesson_type != "игра"


def test_partial_coordination_keeps_proven_finite_result():
    result = derive_fields_v2(
        topic_title="Праздник",
        theory_text="",
        practice_text="Подготовка и участие в мероприятиях.",
        program_content="Подготовка и участие в мероприятиях.",
        theory_hours=0,
        practice_hours=2,
    )
    low = result.planned_result.casefold()
    assert "подготавливает в" not in low
    assert not re.search(r"(?i)^подготавливает\s+(?:в|во|на|по|при)\b", low)
    assert "участие" not in low
    assert low.startswith("участвует в")
    assert "мероприятиях" in low
    assert not result.planned_result.startswith("Выполняет практическое задание")
    assert result.assessment_method


def test_parenthetical_details_do_not_add_extra_outcomes():
    assert _observable_result(
        "Оказывает первую помощь условно пострадавшему (определяет травму или ставит диагноз, практически оказывает помощь)."
    ) == "Оказывает первую помощь условно пострадавшему."


def test_source_fields_are_not_rewritten():
    text = "Практические занятия. Измерение температуры жидкости."
    result = fill_from_source(
        topic_title="Температура", program_content=text,
        theory_hours=0, practice_hours=1,
    )
    assert result.practice_text == "Измерение температуры жидкости."
    assert "число" not in result.planned_result
    assert "градус" not in result.planned_result


CE2_TP1_WEEK_SNAPSHOT = (
    ("1.1", "теоретическое занятие", "Характеризует историю развития туризма в г. Салават. Раскрывает роль туризма в подготовке к защите Родины, в выборе профессии и подготовке к предстоящей трудовой деятельности.", "устный опрос по истории развития туризма в г. Салават; устный опрос по роли туризма в подготовке к защите Родины, в выборе профессии и подготовке к предстоящей трудовой деятельности"),
    ("1.3", "практикум по работе со снаряжением", "Укладывает рюкзаки, подгоняет снаряжение, ухаживает за ним и ремонтирует его.", "педагогическое наблюдение за укладкой рюкзака, подгонкой снаряжения, уходом за ним и ремонтом"),
    ("1.4", "практикум по организации бивака", "Определяет места, пригодные для организации привалов и ночлегов, развертывает и свертывает лагерь (бивак), разжигает костёр.", "педагогическое наблюдение за выбором места для привалов и ночлегов, развертыванием и свертыванием лагеря и разжиганием костра"),
    ("1.5", "проектно-практическое занятие", "Составляет план подготовки похода и план-график движения, подготавливает личное и общественное снаряжение.", "проверка плана подготовки похода и плана-графика движения; педагогическое наблюдение за подготовкой личного и общественного снаряжения"),
    ("1.6", "практикум по организации питания", "Составляет меню и список продуктов для похода, готовит пищу на костре.", "проверка меню и списка продуктов; педагогическое наблюдение за приготовлением пищи на костре"),
    ("1.7", "практикум по исполнению должностей", "Выполняет обязанности по должностям в период подготовки.", "педагогическое наблюдение за выполнением обязанностей по должностям в период подготовки"),
    ("1.8", "учебно-тренировочное занятие на местности", "Отрабатывает технику движения по дорогам, тропам, по пересеченной местности.", "педагогическое наблюдение за техникой движения"),
    ("1.9", "учебно-тренировочное занятие", "Отрабатывает технику преодоления естественных препятствий: склонов, подъёмов, организует переправу по бревну с самостраховкой.", "педагогическое наблюдение за техникой преодоления препятствий и самостраховкой"),
    ("1.10", "туристские соревнования", "Выступает в туристских соревнованиях в качестве участника.", "выступление в туристских соревнованиях"),
    ("1.11", "практикум по подготовке отчёта", "Составляет отчёт о походе.", "проверка отчёта о походе"),
    ("2.1", "практикум по работе с картой", "Определяет масштаб и измеряет расстояние на карте.", "практическое задание по определению масштаба и измерению расстояния на карте"),
    ("2.2", "практикум по работе с топографическими знаками", "Распознаёт знаки.", "топографический диктант"),
    ("2.3", "измерительный практикум", "Оценивает азимуты глазомерно и измеряет азимуты на карте (транспортиром).", "практическое задание по оценке и измерению азимутов"),
    ("2.4", "практикум по ориентированию", "Ориентирует карту по компасу.", "практическое задание по ориентированию карты по компасу"),
    ("2.5", "измерительный практикум", "Измеряет свой средний шаг (пару шагов), строит графики перевода пар шагов в метры для разных условий ходьбы.", "практическое задание по измерению шага; проверка графика перевода пар шагов в метры для разных условий ходьбы"),
    ("2.6", "практикум по ориентированию", "Отбирает основные контрольные ориентиры на карте по заданному маршруту, находит сходные (параллельные) ситуации и определяет способы привязки.", "практическое задание по отбору основных контрольных ориентиров на карте по заданному маршруту, отысканию сходных ситуаций и определению способов привязки"),
    ("2.7", "ситуационный тренинг", "Определяет стороны горизонта по местным предметам, по Солнцу. Имитация ситуации потери ориентировки. Действия по восстановлению местонахождения.", "практическое задание по определению сторон горизонта и восстановлению ориентировки"),
    ("3.1", "викторина", "Характеризует природные особенности, историю родного края и известных земляков.", "краеведческая викторина"),
    ("3.2", "экскурсия", "Совершает прогулки и экскурсии по ближайшим окрестностям, посещает музеи, экскурсионные объекты г. Салавата и Башкортостана.", "педагогическое наблюдение на экскурсии"),
    ("3.2", "экскурсия", "Совершает прогулки и экскурсии по ближайшим окрестностям, посещает музеи, экскурсионные объекты г. Салавата и Башкортостана.", "педагогическое наблюдение на экскурсии"),
    ("3.3", "краеведческий практикум", "Подготавливает и заслушивает доклады по району предстоящего похода.", "проверка докладов по району предстоящего похода"),
    ("3.4", "занятие-наблюдение", "Проводит краеведческие наблюдения.", "педагогическое наблюдение за проведением краеведческих наблюдений"),
    ("4.1", "практикум по личной гигиене", "Применяет средства личной гигиены в походах и во время тренировочного процесса, подбирает одежду и обувь для тренировок и походов, ухаживает за одеждой и обувью.", "педагогическое наблюдение за применением средств личной гигиены, подбором одежды и обуви и уходом за одеждой и обувью"),
    ("4.2", "практикум по комплектованию аптечки", "Формирует походную медицинскую аптечку.", "проверка состава походной медицинской аптечки"),
    ("4.3", "практикум по оказанию первой помощи", "Оказывает первую помощь условно пострадавшему.", "практическое задание по оказанию первой помощи условно пострадавшему"),
    ("4.4", "практикум по транспортировке пострадавшего", "Изготавливает носилки, волокуши, разучивает различные способы транспортировки пострадавшего.", "проверка изготовленных носилок и волокуш; педагогическое наблюдение при разучивании способов транспортировки"),
    ("5.1", "теоретическое занятие", "Характеризует строение человеческого организма (органы и системы).", "устный опрос по строению человеческого организма (органы и системы)"),
    ("5.2", "практикум по самоконтролю", "Ведёт дневник самоконтроля.", "проверка дневника самоконтроля"),
    ("5.3", "учебно-тренировочное занятие", "Выполняет упражнения для рук и плечевого пояса, мышц шеи, туловища и ног, а также упражнения с сопротивлением.", "педагогическое наблюдение за выполнением упражнений для рук и плечевого пояса, мышц шеи, туловища и ног, а также упражнений с сопротивлением"),
    ("5.3", "учебно-тренировочное занятие", "Выполняет упражнения со скакалкой и гантелями, элементы акробатики, участвует в подвижных играх, эстафетах и занятиях легкой атлетикой.", "педагогическое наблюдение за выполнением упражнений со скакалкой и гантелями, элементов акробатики и участием в подвижных играх, эстафетах и занятиях легкой атлетикой"),
    ("5.3", "учебно-тренировочное занятие", "Участвует в занятиях лыжным спортом, выполняет гимнастические упражнения, участвует в спортивных играх: баскетбол, футбол, волейбол, осваивает один из способов плавания.", "педагогическое наблюдение за участием в занятиях лыжным спортом, выполнением гимнастических упражнений, участием в спортивных играх: баскетбол, футбол, волейбол и освоением одного из способов плавания"),
    ("5.4", "учебно-тренировочное занятие", "Выполняет упражнения на развитие выносливости.", "педагогическое наблюдение за выполнением упражнений на развитие выносливости"),
    ("5.4", "учебно-тренировочное занятие", "Выполняет упражнения на развитие быстроты.", "педагогическое наблюдение за выполнением упражнений на развитие быстроты"),
    ("5.4", "учебно-тренировочное занятие", "Выполняет упражнения на развитие силы.", "педагогическое наблюдение за выполнением упражнений на развитие силы"),
    ("5.4", "учебно-тренировочное занятие", "Выполняет упражнения на развитие гибкости, на растягивание и расслабление мышц.", "педагогическое наблюдение за выполнением упражнений на развитие гибкости, на растягивание и расслабление мышц"),
    ("5.4", "учебно-тренировочное занятие", "Выполняет упражнения на развитие гибкости, на растягивание и расслабление мышц.", "педагогическое наблюдение за выполнением упражнений на развитие гибкости, на растягивание и расслабление мышц"),
)


def test_tp1_live_schedule_hours_and_mixed_week():
    from pathlib import Path

    from calendar_pedagoga.resolve_utp import resolve_utp
    from calendar_pedagoga.scheduling import build_schedule
    from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload

    source = Path(__file__).resolve().parents[1] / "references" / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    upload = validate_upload(UploadPurpose.PROGRAM, source.name, source.read_bytes())
    utp = resolve_utp(None, upload)
    schedule = build_schedule(utp, "2026–2027")
    assert len(schedule.weeks) == 36
    assert utp.table_totals.total == 72
    assert utp.table_totals.theory == 27
    assert utp.table_totals.practice == 45
    assert sum(element.hours for element in schedule.elements if element.part_type == "theory") == 27
    assert sum(element.hours for element in schedule.elements if element.part_type == "practice") == 45
    week1: dict[str | None, int] = {}
    for element in schedule.elements:
        if element.week.number != 1:
            continue
        week1[element.topic_number] = week1.get(element.topic_number, 0)
        if element.part_type == "theory":
            week1[element.topic_number] += element.hours
    assert {(number, hours) for number, hours in week1.items()} == {("1.1", 1), ("1.2", 1)}


def test_all_36_first_year_rows_keep_source_schedule_and_grounded_results():
    import inspect

    from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
    import tp1_fixed_content
    from tp1_fixed_content import tp1_number_bound_content_rows

    fixture_src = inspect.getsource(tp1_fixed_content)
    assert "match_utp_to_program" not in fixture_src
    assert "bound_program_item" not in fixture_src
    assert "build_content_model" not in fixture_src
    rows = tp1_number_bound_content_rows()
    before = repr(rows)
    generated = build_lesson_content_v2(rows)
    assert len(generated) == 36
    assert repr(rows) == before
    clone = re.compile(
        r"(?i)^(практическая работа|педагогическое наблюдение):\s+"
        r"(уложить|составить|применить|выполнить|отработать|определить|"
        r"ориентировать|измерить|отобрать|оказать|сформировать|вести)"
    )
    for index, (original, lesson) in enumerate(zip(rows, generated)):
        number, lesson_type, result, control = CE2_TP1_WEEK_SNAPSHOT[index]
        week = original.week_number
        assert lesson.source is original
        assert original.topic_number == number, (
            f"неделя {week}: topic_number\n"
            f"expected: {number!r}\n"
            f"actual: {original.topic_number!r}"
        )
        assert lesson.lesson_type == lesson_type, (
            f"неделя {week}: TYPE\n"
            f"expected: {lesson_type!r}\n"
            f"actual: {lesson.lesson_type!r}"
        )
        assert is_single_pedagogical_lesson_type(lesson.lesson_type)
        assert "+" not in lesson.lesson_type
        assert lesson.planned_result == result, (
            f"неделя {week}: RESULT\n"
            f"expected: {result!r}\n"
            f"actual: {lesson.planned_result!r}"
        )
        assert lesson.assessment_method == control, (
            f"неделя {week}: CONTROL\n"
            f"expected: {control!r}\n"
            f"actual: {lesson.assessment_method!r}"
        )
        assert clone.search(lesson.assessment_method) is None
        triad = f"{lesson.lesson_type} {lesson.planned_result} {lesson.assessment_method}".lower()
        assert not any(word in triad for word in ("чек-лист", "защита", "норматив", "баллов", "секунд"))


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("Выполняет упражнения.", "педагогическое наблюдение за выполнением упражнений"),
        (
            "Выполняет упражнения на постановку руки.",
            "педагогическое наблюдение за выполнением упражнений на постановку руки",
        ),
        (
            "Выполняет упражнения для мышц шеи.",
            "педагогическое наблюдение за выполнением упражнений для мышц шеи",
        ),
        (
            "Определяет масштаб.",
            "практическое задание по определению масштаба",
        ),
    ],
)
def test_exercise_control_keeps_result_tail_without_rewriting_skill_wrappers(result, expected):
    frame = ActionFrame(result, "", "", "")
    control = control_from_frame(
        frame,
        planned_result=result,
        lesson_type="учебно-тренировочное занятие",
        theory_hours=0,
        practice_hours=1,
    )
    assert control == expected
    if result == "Определяет масштаб.":
        assert _observable_result("Выполняет упражнения по определению масштаба.") == result


def test_control_is_not_infinitive_clone_of_result():
    frame = ActionFrame("Укладка рюкзаков, подгонка снаряжения.", "укладка", "рюкзаки", "")
    control = control_from_frame(
        frame,
        planned_result="Укладывает рюкзаки, подгоняет снаряжение.",
        lesson_type="практикум по работе со снаряжением",
        theory_hours=1,
        practice_hours=1,
    )
    assert control == "педагогическое наблюдение за укладкой рюкзака и подгонкой снаряжения"
    assert "практическая работа:" not in control
    assert "уложить" not in control


def _synthetic_week(*parts: tuple[str, str, str, int, int]) -> CalendarContentRow:
    week_parts = tuple(
        WeekTopicPart(
            topic_number=number,
            topic_title=title,
            section="Раздел",
            theory_hours=theory,
            practice_hours=practice,
            match_status=MatchStatus.EXACT,
            program_section="Раздел",
            program_topic=title,
            program_content_full=content,
        )
        for number, title, content, theory, practice in parts
    )
    theory_hours = sum(part.theory_hours for part in week_parts)
    practice_hours = sum(part.practice_hours for part in week_parts)
    return CalendarContentRow(
        week_number=1,
        date_range="01–06.09",
        month="Сентябрь",
        section=week_parts[0].section,
        topic_number=week_parts[0].topic_number,
        topic_title=week_parts[0].topic_title,
        source_topic_title=week_parts[0].topic_title,
        theory_hours=theory_hours,
        practice_hours=practice_hours,
        total_hours=theory_hours + practice_hours,
        match_status=week_parts[0].match_status,
        program_section=week_parts[0].program_section,
        program_topic=week_parts[0].program_topic,
        program_content_full="\n".join(part.program_content_full for part in week_parts),
        program_content_preview="",
        source_program_name="program.docx",
        source_utp_name="utp.docx",
        week_parts=week_parts,
    )


def test_multi_topic_week_keeps_independent_grounded_triads():
    first = (
        "A.1",
        "История прибора",
        "История прибора в городе. Виды приборов.",
        1,
        0,
    )
    second = (
        "A.2",
        "Роль прибора",
        "Роль прибора в обучении и выборе профессии.",
        1,
        0,
    )
    alone_first = fill_from_source(
        topic_title=first[1], program_content=first[2], theory_hours=1, practice_hours=0,
    )
    alone_second = fill_from_source(
        topic_title=second[1], program_content=second[2], theory_hours=1, practice_hours=0,
    )
    merged = build_lesson_content_v2((_synthetic_week(first, second),))[0]
    assert alone_first.lesson_type == alone_second.lesson_type == merged.lesson_type
    assert alone_first.planned_result.rstrip(".") in merged.planned_result
    assert alone_second.planned_result.casefold().startswith("характеризует роль")
    assert "Раскрывает роль" in merged.planned_result
    assert alone_second.planned_result.split(" ", 1)[1].rstrip(".") in merged.planned_result
    assert merged.planned_result.casefold().count("характеризует") == 1
    assert merged.planned_result.casefold().count("раскрывает") == 1
    assert " и роль " not in merged.planned_result.casefold()
    assert alone_first.assessment_method in merged.assessment_method
    assert alone_second.assessment_method in merged.assessment_method
    assert merged.assessment_method.count("устный опрос") == 2
    assert "; " in merged.assessment_method
    assert " и роли " not in merged.assessment_method.casefold()


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (
            (
                ("A.1", "Теория", "Основные понятия.", 1, 0),
                ("A.2", "Практика", "Выполнение упражнения.", 0, 1),
            ),
            "теоретико-практическое занятие",
        ),
        (
            (
                ("A.1", "Первая тема", "Основные понятия.", 1, 0),
                ("A.2", "Вторая тема", "История развития.", 1, 0),
            ),
            "теоретическое занятие",
        ),
        (
            (
                ("A.1", "Первое упражнение", "Выполнение упражнения.", 0, 1),
                ("A.2", "Второе упражнение", "Отработка навыка.", 0, 1),
            ),
            "практическое занятие",
        ),
    ],
)
def test_week_type_aggregates_complete_hour_composition(parts, expected):
    generated = build_lesson_content_v2((_synthetic_week(*parts),))[0]
    assert generated.lesson_type == expected


def _type_part(theory, practice):
    return WeekTopicPart(
        topic_number="A.1",
        topic_title="Устройство прибора",
        section="Раздел",
        theory_hours=theory,
        practice_hours=practice,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic="Устройство прибора",
        program_content_full="Устройство прибора. Выполнение упражнения с прибором.",
    )


def _type_result(lesson_type):
    return ContentEngineV2Result(
        frame=ActionFrame("", "", "", ""),
        lesson_type=lesson_type,
        planned_result="",
        assessment_method="",
        theory_text="",
        practice_text="",
    )


def test_single_mixed_part_uses_safe_theory_practice_fallback():
    actual = _aggregate_week_lesson_type(
        (_type_part(1, 1),),
        [_type_result("практическое занятие")],
        theory_text="Основные понятия.",
        practice_text="Выполнение упражнения.",
    )
    assert actual == "теоретико-практическое занятие"


def test_mixed_part_with_missing_row_local_source_preserves_candidate(caplog):
    with caplog.at_level("INFO"):
        actual = _aggregate_week_lesson_type(
            (_type_part(1, 1),),
            [_type_result("практическое занятие")],
            theory_text="",
            practice_text="Выполнение упражнения.",
        )
    assert actual == "практическое занятие"
    assert "CE2 type ambiguity" in caplog.text


def test_legacy_combined_candidate_never_becomes_final_type(caplog):
    with caplog.at_level("INFO"):
        actual = _aggregate_week_lesson_type(
            (_type_part(1, 1),),
            [_type_result("комбинированное занятие")],
            theory_text="Основные понятия.",
            practice_text="",
        )
    assert actual == "теоретико-практическое занятие"
    assert "CE2 type ambiguity" in caplog.text


@pytest.mark.parametrize(
    "invalid_type",
    [
        "теоретическое занятие + практикум",
        "теория / практика",
        "теоретическое и практическое занятие",
        "лекция и практикум",
        "экскурсия, практикум",
        "комбинированное занятие",
    ],
)
def test_final_type_invariant_rejects_composed_or_technical_labels(invalid_type):
    assert not is_single_pedagogical_lesson_type(invalid_type)


@pytest.mark.parametrize(
    ("practice_type", "expected"),
    [
        ("тестирование", "тестирование"),
        ("практикум", "практикум"),
        ("практикум по ориентированию", "практикум по ориентированию"),
    ],
)
def test_mixed_parts_compose_confirmed_practice_type(practice_type, expected):
    actual = _aggregate_week_lesson_type(
        (_type_part(1, 0), _type_part(0, 1)),
        [_type_result("теоретическое занятие"), _type_result(practice_type)],
        theory_text="Основные понятия.",
        practice_text="Выполнение задания.",
    )
    assert actual == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Диагностика освоенных навыков.", "тестирование"),
        ("Ролевая игра по теме занятия.", "игра"),
        ("Экскурсионная поездка по родному краю.", "экскурсия"),
        ("Практикум по оказанию первой помощи.", "практикум"),
        ("Тренинг командного взаимодействия.", "тренинг"),
        ("Творческая работа по материалам занятия.", "творческая работа"),
        ("Соревнования туристских команд.", "соревнования"),
        ("Наблюдения за сезонными изменениями.", "занятие-наблюдение"),
        ("Поход выходного дня.", "поход"),
        ("Прогулка по экологической тропе.", "прогулка"),
    ],
)
def test_type_from_frame_recovers_only_grounded_strong_activity_cues(source, expected):
    frame = ActionFrame(
        clause=source,
        action="",
        object="",
        conditions="",
    )
    assert (
        type_from_frame(
            frame,
            planned_result="Выполняет практическое задание по теме «Раздел».",
            theory_hours=0,
            practice_hours=2,
            theory_text="",
            practice_text=source,
            program_content=source,
        )
        == expected
    )


def test_mixed_week_preserves_one_confirmed_special_form():
    actual = _aggregate_week_lesson_type(
        (_type_part(1, 0), _type_part(0, 1)),
        [_type_result("экскурсия"), _type_result("экскурсия")],
        theory_text="Подготовка к экскурсии.",
        practice_text="Проведение экскурсии.",
    )
    assert actual == "экскурсия"


def test_mixed_week_with_conflicting_practice_types_uses_safe_fallback():
    actual = _aggregate_week_lesson_type(
        (_type_part(1, 0), _type_part(0, 1), _type_part(0, 1)),
        [
            _type_result("теоретическое занятие"),
            _type_result("тестирование"),
            _type_result("практикум"),
        ],
        theory_text="Основные понятия.",
        practice_text="Тестирование и практическая работа.",
    )
    assert actual == "теоретико-практическое занятие"


@pytest.mark.parametrize(
    ("source", "result_stem", "lesson_type", "control_stem"),
    [
        (
            "Первая доврачебная помощь при ушибах, потёртостях, ссадинах и ранах.",
            "оказывает первую доврачебную помощь при",
            "практикум по оказанию первой помощи",
            "педагогическое наблюдение за оказанием первой помощи",
        ),
        (
            "Закаливание природными факторами (солнце, воздух вода).",
            "выполняет закаливание природными факторами",
            "практикум",
            "педагогическое наблюдение за выполнением закаливания",
        ),
        (
            "Любимые зимние развлечения – катание на санках, на коньках.",
            "выполняет катание на санках",
            "практикум",
            "педагогическое наблюдение за выполнением катания",
        ),
        (
            "Экскурсионные поездки: Стерлитамакские шиханы, Торатау.",
            "совершает экскурсионные поездки:",
            "экскурсия",
            "педагогическое наблюдение на экскурсии",
        ),
    ],
)
def test_nominal_activity_yields_finite_result_from_same_frame(
    source, result_stem, lesson_type, control_stem,
):
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert low.startswith(result_stem)
    assert not derived.planned_result.startswith("Выполняет практическое задание")
    assert derived.lesson_type == lesson_type
    assert derived.assessment_method.casefold().startswith(control_stem)
    assert derived.frame.clause
    triad = f"{derived.lesson_type} {derived.planned_result} {derived.assessment_method}".casefold()
    assert any(marker in triad for marker in result_stem.split()[:2])


@pytest.mark.parametrize(
    "source",
    [
        "История семьи.",
        "Содержание домашних животных.",
        "Развлечение.",
        "Калибрование прибора.",
        "Правила поведения в лесу.",
        "Значение закаливания.",
        "Помощь родителям.",
        "Поездки выходного дня: городской парк.",
        "Любимые зимние развлечения.",
        "Предприятие.",
        "Занятие.",
        "Абракание материала.",
    ],
)
def test_descriptive_nouns_do_not_invent_nominal_activity(source):
    derived = derive_fields_v2(
        topic_title="Учебная тема",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert not any(
        invented in low
        for invented in (
            "развлекает",
            "содержит",
            "калибрует",
            "закаливает",
            "катает",
            "катается",
            "поезжает",
            "предприявает",
            "занявает",
            "абракает",
        )
    )
    assert not low.startswith("выполняет содержание")
    assert not low.startswith("выполняет развлечение")
    assert not low.startswith("выполняет калибрование")
    assert not low.startswith("выполняет правила")
    assert not low.startswith("выполняет историю")
    assert not low.startswith("оказывает помощь родителям")
    assert not low.startswith("совершает поездки выходного")
    assert "катание" not in low
    first = low.split()[0]
    from calendar_pedagoga.content_engine_v2 import _proven_finite_predicates

    assert first in _proven_finite_predicates()


def test_theory_only_does_not_wrap_nominal_activity_as_performance():
    derived = derive_fields_v2(
        topic_title="Закаливание",
        theory_text="Закаливание природными факторами (солнце, воздух вода).",
        practice_text="",
        program_content="Закаливание природными факторами (солнце, воздух вода).",
        theory_hours=1,
        practice_hours=0,
    )
    low = derived.planned_result.casefold()
    assert not low.startswith("выполняет закаливание")
    assert not low.startswith("закаливает")


def test_w1_didactic_games_become_participation():
    source = (
        "Дидактические игры: «Загадки-задачки Рассеянного», "
        "«Сколько орехов», «Что растет в родном краю»."
    )
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert low.startswith("участвует в дидактических играх")
    assert not derived.planned_result.startswith("Выполняет практическое задание")
    assert derived.lesson_type == "дидактическое занятие"
    assert derived.assessment_method.casefold().startswith(
        "педагогическое наблюдение за участием в"
    )


def test_w_gt1_outdoor_games_use_same_participation_converter():
    practice = (
        "Закаливание природными факторами (солнце, воздух, вода). "
        "Подвижные игры на свежем воздухе, эстафеты, дни здоровья."
    )
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=1,
        practice_appearance_count=2,
    )
    low = derived.planned_result.casefold()
    assert low.startswith("участвует в подвижных играх")
    assert "эстафетах" in low
    assert "дни здоровья" not in low
    assert derived.lesson_type == "игра"
    assert derived.assessment_method.casefold().startswith(
        "педагогическое наблюдение за участием в"
    )


def test_day_hikes_become_participation_in_hike():
    source = "Походы выходного дня."
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert low.startswith("участвует в походах")
    assert "выходного дня" in low
    assert derived.lesson_type == "поход"
    assert derived.assessment_method.casefold().startswith(
        "педагогическое наблюдение за участием в"
    )


def test_creative_source_stays_observable_without_invented_verbs():
    source = (
        "Аппликация, конструирование из бумаги, рисунки национальной одежды, "
        "орнамента (творческая работа)."
    )
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert not derived.planned_result.startswith("Выполняет практическое задание")
    assert "апплицирует" not in low
    assert any(
        token in low for token in ("выполняет аппликацию", "конструирует")
    )
    assert derived.lesson_type == "творческая работа"
    assert derived.assessment_method
    assert not derived.assessment_method.startswith("устный опрос по теме")


def test_creative_finite_result_does_not_glue_raw_np_tail():
    source = (
        "Аппликация, конструирование из бумаги, рисунки национальной одежды, "
        "орнамента (творческая работа)."
    )
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    result = derived.planned_result.rstrip(".")
    low = result.casefold()
    assert "конструирует из бумаги, рисунки" not in low
    assert "рисунк" not in low
    assert "орнамент" not in low
    assert "выполняет аппликацию" in low
    assert "конструирует из бумаги" in low
    finite_re = re.compile(r"(?i)^[А-Яа-яЁё]+(?:ет|ит|ёт|ут|ют|ает|яет)\b")
    for part in re.split(r",\s+", result):
        assert finite_re.match(part), part
    control = derived.assessment_method.casefold()
    assert "рисунк" not in control
    assert "орнамент" not in control
    assert "конструирует" not in control
    assert "выполнением" in control
    assert "конструированием" in control
    assert derived.lesson_type == "творческая работа"


_ACCUSATIVE_AFTER_PERFORMING = re.compile(
    r"(?i)за выполнением\s+(?:[а-яё-]+(?:ую|юю)\s+)*"
    r"[а-яё-]*(?:ию|(?<![и])ю|(?:[бвгджзклмнпрстфхцчшщ])у)\b"
)


def test_control_after_performing_rejects_accusative_object():
    assert _phrase_to_genitive(_noun_nom_to_acc("лекция")) == "лекции"
    assert _phrase_to_genitive(_noun_nom_to_acc("неделя")) == "недели"
    source = (
        "Аппликация, конструирование из бумаги, рисунки национальной одежды, "
        "орнамента (творческая работа)."
    )
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    control = derived.assessment_method
    assert control == (
        "педагогическое наблюдение за выполнением аппликации "
        "и конструированием из бумаги"
    )
    assert _ACCUSATIVE_AFTER_PERFORMING.search(control) is None
    assert _ACCUSATIVE_AFTER_PERFORMING.search(
        "педагогическое наблюдение за выполнением аппликацию и конструированием из бумаги"
    )


def test_aid_methods_keep_proven_first_aid_action():
    source = (
        "Основные приёмы оказания первой доврачебной помощи при ожогах, "
        "обморожениях."
    )
    derived = derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert low.startswith("оказывает")
    assert "помощь" in low
    assert not derived.planned_result.startswith("Выполняет практическое задание")
    assert derived.lesson_type == "практикум по оказанию первой помощи"
    assert derived.assessment_method.casefold().startswith(
        "педагогическое наблюдение за оказанием первой помощи"
    )


def _slot_fields(practice: str, *, index: int, weeks: int) -> ContentEngineV2Result:
    return derive_fields_v2(
        topic_title="Тема занятия",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=index,
        practice_appearance_count=weeks,
    )


def test_packed_raw_np_does_not_wipe_later_finite_activity():
    practice = (
        "Краткие сведения о районе движения. "
        "Выбор места привала. "
        "Установка палатки. "
        "Вязка туристских узлов."
    )
    first = _slot_fields(practice, index=0, weeks=2)
    assert not first.planned_result.startswith("Выполняет практическое задание")
    assert first.planned_result.casefold().startswith("выбирает")
    assert re.search(r"\bмест\b", first.planned_result.casefold()) is None
    assert "сведения" not in first.planned_result.casefold()
    assert first.assessment_method
    assert not first.assessment_method.startswith("устный опрос по теме")


def test_packed_others_keep_only_convertible_activity():
    practice = (
        "Техника движения в походе: темп, режим. "
        "Преодоление препятствий: крутые склоны. "
        "Выбор места привала. "
        "Установка палатки. "
        "Вязка туристских узлов. "
        "Занятия на скалодроме."
    )
    first = _slot_fields(practice, index=0, weeks=2)
    assert first.planned_result.casefold().startswith("выбирает")
    assert re.search(r"\bмест\b", first.planned_result.casefold()) is None
    assert not first.planned_result.casefold().startswith("техника")
    assert "преодоление препятствий" not in first.planned_result.casefold()


def test_activity_plus_catalog_stretches_activity_when_w_equals_2():
    practice = (
        "Подготовка и участие в массовых мероприятиях. "
        "Фестиваль, «Зимние старты», «Лесными тропами»."
    )
    first = _slot_fields(practice, index=0, weeks=2)
    second = _slot_fields(practice, index=1, weeks=2)
    low = first.planned_result.casefold()
    assert not first.planned_result.startswith("Выполняет практическое задание")
    assert "подготавливает в" not in low
    assert not low.startswith("фестиваль")
    assert first.planned_result == second.planned_result
    assert first.lesson_type == second.lesson_type
    assert first.assessment_method == second.assessment_method
    assert any("продолжение" in item.casefold() for item in second.warnings)


def test_activity_plus_catalog_stretches_activity_when_w_equals_4():
    practice = (
        "Подготовка и участие в массовых мероприятиях. "
        "Фестиваль, «Зимние старты», «Лесными тропами»."
    )
    weeks = [_slot_fields(practice, index=index, weeks=4) for index in range(4)]
    assert len({item.planned_result for item in weeks}) == 1
    assert len({item.lesson_type for item in weeks}) == 1
    assert "подготавливает в" not in weeks[0].planned_result.casefold()
    assert not weeks[0].planned_result.startswith("Выполняет практическое задание")
    assert not weeks[-1].planned_result.casefold().startswith("фестиваль")
    assert all(
        any("продолжение" in item.casefold() for item in week.warnings)
        for week in weeks[1:]
    )


def test_slot_continuation_keeps_the_same_triad():
    practice = "Выбор места привала."
    first = _slot_fields(practice, index=0, weeks=2)
    second = _slot_fields(practice, index=1, weeks=2)
    assert first.planned_result == second.planned_result
    assert first.lesson_type == second.lesson_type
    assert first.assessment_method == second.assessment_method
    assert first.planned_result.casefold().startswith("выбирает")
    assert re.search(r"\bмест\b", first.planned_result.casefold()) is None
    assert any("продолжение" in item.casefold() for item in second.warnings)


def test_ambiguous_genitive_object_is_not_damaged():
    assert _noun_gen_to_acc("места") == "места"
    assert _noun_gen_to_acc("поля") == "поля"
    assert _noun_gen_to_acc("рюкзака") == "рюкзак"
    assert _noun_gen_to_acc("стола") == "стол"
    assert _noun_gen_to_acc("снаряжения") == "снаряжение"
    assert _noun_gen_to_acc("мест") == "места"

    for source in ("Выбор места привала.", "Выбор поля."):
        derived = derive_fields_v2(
            topic_title="Тема занятия",
            theory_text="",
            practice_text=source,
            program_content=source,
            theory_hours=0,
            practice_hours=2,
        )
        low = derived.planned_result.casefold()
        assert low.startswith("выбирает")
        assert re.search(r"\bмест\b", low) is None
        assert "поль" not in low
        assert not derived.planned_result.startswith("Выполняет практическое задание")


def test_salvage_drops_dependent_pp_with_unproven_conjunct():
    assert _salvage_proven_finite_result(
        "Подготавливает и участие в мероприятиях"
    ) is None
    assert _salvage_proven_finite_result(
        "Подготавливает и участие на местности"
    ) is None
    salvaged = _salvage_proven_finite_result("Подготавливает и участие снаряжение")
    assert salvaged is not None
    assert salvaged.casefold().startswith("подготавливает снаряжение")
    assert not re.search(r"(?i)\s(?:в|во|на|по|при)\s", salvaged)

    derived = derive_fields_v2(
        topic_title="Праздник",
        theory_text="",
        practice_text="Подготовка и участие в массовых мероприятиях.",
        program_content="Подготовка и участие в массовых мероприятиях.",
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert "подготавливает в мероприятиях" not in low
    assert not re.search(r"(?i)^подготавливает\s+(?:в|во|на|по|при)\b", low)
    assert re.search(r"\bмест\b", low) is None
    assert low.startswith("участвует")
    assert not derived.planned_result.startswith("Выполняет практическое задание")


def test_catalog_alone_is_not_an_activity_slot():
    practice = "Фестиваль, «Зимние старты», «Лесными тропами»."
    first = _slot_fields(practice, index=0, weeks=2)
    second = _slot_fields(practice, index=1, weeks=2)
    assert first.planned_result.startswith("Выполняет практическое задание")
    assert second.planned_result.startswith("Выполняет практическое задание")
    assert first.lesson_type == "практическое занятие"
    assert "фестиваль" not in first.lesson_type.casefold()


def test_lessons_with_prepositional_place_are_participation():
    derived = _slot_fields("Занятия в бассейне.", index=0, weeks=2)
    low = derived.planned_result.casefold()
    assert low.startswith("участвует в занятиях в бассейне")
    assert "устанавливает" not in low
    assert not derived.planned_result.startswith("Выполняет практическое задание")
    assert "по теме" not in derived.assessment_method.casefold()


def test_bare_lessons_label_is_not_participation():
    derived = _slot_fields("Занятия.", index=0, weeks=2)
    assert derived.planned_result.startswith("Выполняет практическое задание")


def test_packed_climbing_slot_keeps_all_source_activities():
    practice = (
        "Техника движения в походе: темп, режим. "
        "Преодоление препятствий: залесенная местность, крутые склоны. "
        "Выбор места привала. "
        "Занятия на скалодроме. "
        "Установка палатки. "
        "Вязка туристских узлов."
    )
    derived = _slot_fields(practice, index=1, weeks=2)
    low = derived.planned_result.casefold()
    assert derived.planned_result == (
        "Участвует в занятиях на скалодроме, "
        "выполняет установку палатки и вязку туристских узлов."
    )
    assert "выполняет установку палатки, выполняет" not in low
    assert "устанавливает" not in low
    assert "вяжет" not in low
    assert derived.assessment_method == (
        "педагогическое наблюдение за участием в занятиях на скалодроме "
        "и выполнением установки палатки и вязки туристских узлов"
    )
    assert derived.assessment_method.casefold().count("выполнением") == 1


def test_event_name_and_lone_process_stay_generic_fallback():
    family = derive_fields_v2(
        topic_title="Моя семья",
        theory_text="",
        practice_text="Мероприятие «Мама, папа, я – дружная семья».",
        program_content="Мероприятие «Мама, папа, я – дружная семья».",
        theory_hours=1,
        practice_hours=1,
    )
    assert family.planned_result.startswith("Выполняет практическое задание")
    braking = _slot_fields("Торможение.", index=0, weeks=2)
    assert braking.planned_result.startswith("Выполняет практическое задание")


def test_stronger_form_is_not_replaced_by_quoted_event_title_overlap():
    derived = derive_fields_v2(
        topic_title="Дружба",
        theory_text="",
        practice_text="Экскурсии в парк. Мероприятие «Праздник дружбы».",
        program_content="Экскурсии в парк. Мероприятие «Праздник дружбы».",
        theory_hours=0,
        practice_hours=2,
    )
    low = derived.planned_result.casefold()
    assert low.startswith("совершает экскурсии")
    assert "парк" in low
    assert "мероприятие" not in low
    assert not derived.planned_result.startswith("Выполняет практическое задание")


def test_topic_overlap_breaks_ties_inside_same_action_class():
    clause, theory_only, _pool = select_source_clause(
        topic_title="Краеведение",
        theory_text="",
        practice_text="Экскурсии в музей. Экскурсии по краеведению.",
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    assert not theory_only
    assert clause.casefold().startswith("экскурсии по краеведению")


def test_family_week_keeps_grounded_walk_not_quoted_event():
    derived = derive_fields_v2(
        topic_title="Моя семья",
        theory_text="Профессии родителей. Кем я буду, когда стану взрослым?",
        practice_text=(
            "Экскурсии на места работы родителей. "
            "Мероприятие «Мама, папа, я – дружная семья»."
        ),
        program_content=(
            "Экскурсии на места работы родителей. "
            "Мероприятие «Мама, папа, я – дружная семья»."
        ),
        theory_hours=1,
        practice_hours=1,
    )
    low = derived.planned_result.casefold()
    assert low.startswith("совершает экскурсии")
    assert "родител" in low
    assert not derived.planned_result.startswith("Выполняет практическое задание")
    assert "по теме" not in derived.assessment_method.casefold()


def test_ski_technique_list_stays_generic_fallback():
    derived = derive_fields_v2(
        topic_title="Лыжный туризм",
        theory_text="",
        practice_text=(
            "Способы передвижения на лыжах. "
            "Подъем «лесенкой», «ёлочкой». "
            "Спуск с горы, способы поворота. "
            "Торможение. "
            "Преодоление препятствий на лыжах."
        ),
        program_content="",
        theory_hours=0,
        practice_hours=2,
    )
    assert derived.planned_result.startswith("Выполняет практическое задание")
    assert "тормозит" not in derived.planned_result.casefold()
    assert "преодолевает" not in derived.planned_result.casefold()


@pytest.mark.parametrize(
    "source",
    [
        "Сказка о царе.",
        "Лодка на берегу.",
        "Книжка на полке.",
        "Песня о дружбе.",
        "Стенка из кирпича.",
        "Загадка на заборе.",
        "Вязка.",
        "Установка требований.",
    ],
)
def test_ordinary_or_suffix_np_is_not_invented_activity(source):
    derived = _slot_fields(source, index=0, weeks=2)
    assert derived.planned_result.startswith("Выполняет практическое задание")
    low = derived.planned_result.casefold()
    assert "вяжет" not in low
    assert "устанавливает" not in low
    assert not re.search(r"^выполняет (?!практическое задание)", low)


def _theory_fields(source: str, *, title: str = "Тема занятия") -> ContentEngineV2Result:
    return derive_fields_v2(
        topic_title=title,
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
        practice_hours=0,
    )


def test_theory_characterizes_clothing_inventory():
    derived = _theory_fields("Одежда, зимний инвентарь.")
    low = derived.planned_result.casefold()
    assert low.startswith("характеризует одежду")
    assert "инвентарь" in low
    assert not derived.planned_result.startswith("Характеризует материал по теме")
    assert derived.lesson_type == "теоретическое занятие"


def test_theory_characterizes_compass_knowledge_head():
    derived = _theory_fields(
        "Компас, его устройство и назначение, правила обращения."
    )
    assert derived.planned_result == "Характеризует устройство и назначение компаса."
    assert derived.assessment_method == "устный опрос по устройству и назначению компаса"
    assert not derived.planned_result.startswith("Характеризует материал по теме")


def test_theory_characterizes_colon_heading_not_catalogue():
    derived = _theory_fields(
        "Памятники природы: Стерлитамакские шиханы, Капова пещера и другие."
    )
    low = derived.planned_result.casefold()
    assert low.startswith("характеризует памятники природы")
    assert "шиханы" not in low
    assert ":" not in derived.planned_result
    assert not derived.planned_result.startswith("Характеризует материал по теме")


def test_theory_characterizes_trip_heading_not_catalogue():
    derived = _theory_fields(
        "Экскурсионные поездки: Стерлитамакские Шиханы, водопад Кук-Караук и другие."
    )
    low = derived.planned_result.casefold()
    assert "характеризует экскурсионные поездки" in low
    assert "шиханы" not in low
    assert ":" not in derived.planned_result
    assert not derived.planned_result.startswith("Характеризует материал по теме")


def test_theory_skips_interrogative_clause_with_parentheses():
    question = "Что послужило началом строительства города (место, предприятие)?"
    lone = _theory_fields(question, title="Мой город")
    assert lone.planned_result.count("(") == lone.planned_result.count(")")
    assert "(место." not in lone.planned_result
    mixed = _theory_fields(
        question + " Предприятия и учреждения города.",
        title="Мой город",
    )
    low = mixed.planned_result.casefold()
    assert mixed.planned_result.count("(") == mixed.planned_result.count(")")
    assert "(место." not in mixed.planned_result
    assert "что послужило" not in low
    assert "предприятию" not in low
    if not mixed.planned_result.startswith("Характеризует материал по теме"):
        assert "предприятия" in low or "учреждения" in low or "памятник" in low


def test_theory_only_does_not_conjugate_coordinated_activity():
    derived = _theory_fields(
        "Подготовка и участие в туристско-краеведческих массовых мероприятиях."
    )
    low = derived.planned_result.casefold()
    assert low.startswith("характеризует подготовку и участие")
    assert not low.startswith("подготавливает")
    assert "подготавливает и участие" not in low
    assert not derived.planned_result.startswith("Характеризует материал по теме")


def test_mixed_hours_empty_practice_still_conjugates_care_segment():
    derived = derive_fields_v2(
        topic_title="Личная гигиена",
        theory_text=(
            "Соблюдение правил личной гигиены, утренний и вечерний туалет, "
            "уход за ногами, обувью, одеждой."
        ),
        practice_text="",
        program_content=(
            "Соблюдение правил личной гигиены, утренний и вечерний туалет, "
            "уход за ногами, обувью, одеждой."
        ),
        theory_hours=1,
        practice_hours=1,
    )
    assert "ухаживает" in derived.planned_result.casefold()
    assert "одеждой" in derived.planned_result.casefold()
    assert "одежную" not in derived.planned_result.casefold()


def _finite_fields(source: str, *, title: str = "Тема занятия") -> ContentEngineV2Result:
    return derive_fields_v2(
        topic_title=title,
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )


def test_care_pp_keeps_instrumental_list():
    derived = _finite_fields("Уход за ногами, обувью, одеждой.")
    low = derived.planned_result.casefold()
    assert "ухаживает за" in low
    assert "одеждой" in low
    assert "одежную" not in low


def test_care_pp_keeps_tent_and_dishware():
    derived = _finite_fields("Уход за палаткой, посудой.")
    low = derived.planned_result.casefold()
    assert "ухаживает за" in low
    assert "посудой" in low
    assert "посущую" not in low


def test_observation_pp_keeps_instrumental_list():
    derived = _finite_fields("Наблюдение за погодой, одеждой.")
    low = derived.planned_result.casefold()
    assert "наблюдает за" in low
    assert "одеждой" in low
    assert "одежную" not in low


def test_work_with_keeps_source_pp():
    derived = _finite_fields("Работа с картой.")
    low = derived.planned_result.casefold()
    assert "с картой" in low


def test_participation_in_keeps_source_pp():
    derived = _finite_fields("Участие в мероприятиях.")
    low = derived.planned_result.casefold()
    assert "участвует в" in low
    assert "мероприятиях" in low


def test_quality_rejects_ungrounded_acc_inside_governed_pp():
    assert _quality_issue(
        "Ухаживает за ногами, обувью, одежную.",
        "устный опрос по теме „Личная гигиена“",
    ) == "unproven_verb_valency"


def test_theory_oral_control_uses_result_object_not_topic_title():
    derived = derive_fields_v2(
        topic_title="Введение",
        theory_text="Рассказы об интересных походах, экскурсиях.",
        practice_text="",
        program_content="Рассказы об интересных походах, экскурсиях.",
        theory_hours=2,
        practice_hours=0,
    )
    assert derived.lesson_type == "теоретическое занятие"
    assert derived.planned_result.casefold().startswith("характеризует рассказы")
    control = derived.assessment_method.casefold()
    assert control.startswith("устный опрос по ")
    assert "по теме" not in control
    assert "по рассказам" in control
    assert "по рассказы" not in control
    assert "поход" in control


def test_theory_oral_control_keeps_all_characterized_objects():
    source = (
        "Народное мастерство (национальная одежда, башкирский орнамент). "
        "Башкирские легенды и предания – памятники народного творчества."
    )
    derived = derive_fields_v2(
        topic_title="История родного края",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
        practice_hours=0,
    )
    assert derived.planned_result.casefold().startswith("характеризует")
    assert "мастерств" in derived.planned_result.casefold()
    assert "легенд" in derived.planned_result.casefold()
    control = derived.assessment_method.casefold()
    assert control.startswith("устный опрос по ")
    assert "по теме" not in control
    assert "мастерств" in control
    assert "легенд" in control
    assert "по народному мастерству" in control
    assert "по народное" not in control
    assert "башкирскию" not in control
    assert "башкирским легендам" in control
    assert "башкирские легенды" not in control


def test_selects_rest_spot_control_comes_from_result_not_slot_neighbours():
    practice = (
        "Техника движения в походе: темп, режим. "
        "Преодоление препятствий: залесенная местность, крутые склоны. "
        "Выбор места привала. "
        "Занятия на скалодроме. "
        "Установка палатки. "
        "Вязка туристских узлов."
    )
    derived = derive_fields_v2(
        topic_title="Пеший туризм",
        theory_text="",
        practice_text=practice,
        program_content=practice,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=0,
        practice_appearance_count=2,
    )
    assert derived.planned_result == "Выбирает места привала."
    control = derived.assessment_method.casefold()
    assert "выбор" in control
    assert "привал" in control
    assert "устный опрос" not in control
    assert "по теме" not in control
    assert "техник" not in control
    assert "скалодром" not in control
    assert "палатк" not in control


def test_mixed_result_control_keeps_care_action_off_topic_title():
    source = (
        "Соблюдение правил личной гигиены, утренний и вечерний туалет, "
        "уход за ногами, обувью, одеждой."
    )
    derived = derive_fields_v2(
        topic_title="Личная гигиена",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=1,
        practice_hours=1,
    )
    result = derived.planned_result.casefold()
    assert "характеризует" in result
    assert "ухаживает за" in result
    control = derived.assessment_method.casefold()
    assert "по теме" not in control
    assert "гигиен" in control or "соблюден" in control
    assert "по соблюдению" in control
    assert "по соблюдение " not in control
    assert "туалету" in control
    assert "уход" in control or "ногам" in control or "обув" in control


def test_characterizing_first_aid_kit_stays_oral_not_product_check():
    derived = derive_fields_v2(
        topic_title="Аптечка",
        theory_text="Назначение лекарственных препаратов. Комплектование аптечки.",
        practice_text="",
        program_content="Назначение лекарственных препаратов. Комплектование аптечки.",
        theory_hours=2,
        practice_hours=0,
    )
    assert derived.planned_result == "Характеризует комплектование аптечки."
    control = derived.assessment_method.casefold()
    assert control.startswith("устный опрос по ")
    assert "комплектован" in control
    assert "по комплектованию" in control
    assert "по комплектование " not in control
    assert "по теме" not in control
    assert "проверка состава" not in control


def _theory_oral(result: str) -> str:
    return control_from_frame(
        ActionFrame(result, "характеризует", "", ""),
        planned_result=result,
        lesson_type="теоретическое занятие",
        theory_hours=2,
        practice_hours=0,
    )


def test_theory_oral_dative_for_plural_stories():
    control = _theory_oral(
        "Характеризует рассказы об интересных походах, экскурсиях."
    )
    assert control == (
        "устный опрос по рассказам об интересных походах, экскурсиях"
    )


def test_theory_oral_dative_for_adjective_noun_craft():
    control = _theory_oral("Характеризует народное мастерство.")
    assert control == "устный опрос по народному мастерству"
    assert "по народное" not in control.casefold()


def test_theory_oral_dative_for_verbal_noun_kit():
    control = _theory_oral("Характеризует комплектование аптечки.")
    assert control == "устный опрос по комплектованию аптечки"


def test_theory_oral_does_not_copy_nominative_object_after_po():
    samples = (
        "Характеризует рассказы об интересных походах.",
        "Характеризует народное мастерство.",
        "Характеризует комплектование аптечки.",
        "Характеризует соблюдение правил личной гигиены.",
    )
    for result in samples:
        object_head = re.sub(
            r"(?i)^характеризует\s+", "", result
        ).rstrip(".").split()[0]
        control = _theory_oral(result).casefold()
        assert control.startswith("устный опрос по ")
        assert f"по {object_head.casefold()} " not in control
        assert not control.endswith(f"по {object_head.casefold()}")


def test_theory_oral_dative_from_feminine_acc_and_masculine_soft_sign():
    control = _theory_oral("Характеризует одежду, зимний инвентарь.")
    assert control == "устный опрос по одежде, зимнему инвентарю"


def test_theory_oral_dative_keeps_neuter_plural_and_last_conjunct():
    control = _theory_oral(
        "Характеризует назначение, правила обращения, "
        "градусное значение основных и дополнительных направлений."
    )
    low = control.casefold()
    assert low.startswith("устный опрос по назначению, правилам обращения")
    assert "правиле " not in low
    assert "градусному значению" in low
    assert "направлен" in low


def test_theory_oral_dative_from_coordinated_acc_and_neuter():
    control = _theory_oral(
        "Характеризует подготовку и участие в туристско-краеведческих "
        "массовых мероприятиях."
    )
    assert control.startswith(
        "устный опрос по подготовке и участию в туристско-краеведческих"
    )
    assert "по подготовку" not in control.casefold()


def test_clothing_inventory_theory_keeps_result_and_fixes_oral_control():
    derived = _theory_fields("Одежда, зимний инвентарь.")
    assert derived.planned_result.casefold().startswith("характеризует одежду")
    assert derived.lesson_type == "теоретическое занятие"
    assert derived.assessment_method == "устный опрос по одежде, зимнему инвентарю"
