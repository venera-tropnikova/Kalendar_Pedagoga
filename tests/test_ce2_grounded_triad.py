"""Grounding checks: forms and controls cannot supply missing programme facts."""

import re
import os
from pathlib import Path

import pytest

from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    ContentEngineV2Result,
    _aggregate_week_lesson_type,
    _observable_result,
    build_lesson_content_v2,
    control_from_frame,
    fill_from_source,
    type_from_frame,
)
from calendar_pedagoga.content_generation import CalendarContentRow, WeekTopicPart
from calendar_pedagoga.matching import MatchStatus


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
    assert generated[0].lesson_type == "теоретическое + практическое занятие"
    assert generated[21].lesson_type == "теоретическое + практическое занятие"
    assert generated[25].lesson_type == "теоретическое + практическое занятие"


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
    ("1.1", "теоретическое занятие", "Характеризует историю развития туризма в г. Салават и роль туризма в подготовке к защите Родины, в выборе профессии и подготовке к предстоящей трудовой деятельности.", "устный опрос по истории развития туризма в г. Салават и роли туризма в подготовке к защите Родины"),
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


def test_all_36_first_year_rows_keep_source_schedule_and_grounded_results():
    from pathlib import Path
    from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
    from calendar_pedagoga.content_generation import build_content_model
    from calendar_pedagoga.program_parsing import parse_program
    from calendar_pedagoga.resolve_utp import resolve_utp
    from calendar_pedagoga.scheduling import build_schedule
    from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload

    source = Path(__file__).resolve().parents[1] / "references" / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    upload = validate_upload(UploadPurpose.PROGRAM, source.name, source.read_bytes())
    utp = resolve_utp(None, upload)
    program = parse_program(upload.content, upload.filename, study_year=1)
    schedule = build_schedule(utp, "2026–2027")
    rows = build_content_model(schedule, utp, program, source.name)
    before = repr(rows)
    generated = build_lesson_content_v2(rows)
    assert len(generated) == 36
    assert len(schedule.weeks) == 36
    assert utp.table_totals.total == 72
    assert utp.table_totals.theory == 27
    assert utp.table_totals.practice == 45
    assert sum(row.theory_hours for row in rows) == 27
    assert sum(row.practice_hours for row in rows) == 45
    assert {(part.topic_number, part.theory_hours) for part in rows[0].week_parts} == {
        ("1.1", 1),
        ("1.2", 1),
    }
    assert repr(rows) == before
    clone = re.compile(
        r"(?i)^(практическая работа|педагогическое наблюдение):\s+"
        r"(уложить|составить|применить|выполнить|отработать|определить|"
        r"ориентировать|измерить|отобрать|оказать|сформировать|вести)"
    )
    for index, (original, lesson) in enumerate(zip(rows, generated)):
        number, lesson_type, result, control = CE2_TP1_WEEK_SNAPSHOT[index]
        assert lesson.source is original
        assert original.topic_number == number
        assert lesson.lesson_type == lesson_type
        assert lesson.planned_result == result
        assert lesson.assessment_method == control
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


def test_multi_topic_week_merges_both_grounded_triads():
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
    first_object = re.sub(r"(?i)^характеризует\s+", "", alone_first.planned_result).rstrip(".")
    second_object = re.sub(r"(?i)^характеризует\s+", "", alone_second.planned_result).rstrip(".")
    assert first_object in merged.planned_result
    assert second_object in merged.planned_result
    assert merged.planned_result.casefold().count("характеризует") == 1
    first_control = alone_first.assessment_method.removeprefix("устный опрос по ")
    second_control = alone_second.assessment_method.removeprefix("устный опрос по ")
    assert first_control in merged.assessment_method
    assert second_control in merged.assessment_method
    assert merged.assessment_method.startswith("устный опрос по ")
    assert merged.assessment_method.count("устный опрос") == 1


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (
            (
                ("A.1", "Теория", "Основные понятия.", 1, 0),
                ("A.2", "Практика", "Выполнение упражнения.", 0, 1),
            ),
            "теоретическое занятие + практикум",
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


def test_single_mixed_part_uses_explicit_generic_composition():
    actual = _aggregate_week_lesson_type(
        (_type_part(1, 1),),
        [_type_result("практическое занятие")],
        theory_text="Основные понятия.",
        practice_text="Выполнение упражнения.",
    )
    assert actual == "теоретическое + практическое занятие"


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
    ("practice_type", "expected"),
    [
        ("тестирование", "теоретическое занятие + тестирование"),
        ("практикум", "теоретическое занятие + практикум"),
        (
            "практикум по ориентированию",
            "теоретическое занятие + практикум по ориентированию",
        ),
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
