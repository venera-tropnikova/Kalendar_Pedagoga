"""Сопоставление УТП↔программа: номер не переносит чужое содержание."""

from calendar_pedagoga.content_generation import build_content_model
from calendar_pedagoga.matching import (
    MatchStatus,
    bound_program_item,
    match_position,
    match_utp_to_program,
)
from calendar_pedagoga.parsing import Hours, Topic, UtpMetadata, UtpParseResult
from calendar_pedagoga.program_parsing import ProgramContentItem
from calendar_pedagoga.scheduling import AcademicWeek, ScheduledElement, ScheduleResult
from datetime import date


def _topic(number, title, section="Раздел", theory=1, practice=1):
    return Topic(number, title, Hours(theory + practice, theory, practice), section)


def _item(number, title, content, section="Раздел", study_year=None):
    return ProgramContentItem(number, title, content, section, study_year)


def test_same_number_unconfirmed_titles_do_not_transfer_content():
    topic = _topic("4.1", "Рисование натюрморта", "Изобразительное искусство")
    foreign = _item(
        "4.1",
        "Сольфеджио",
        "Чужое описание: интервалы и слуховые диктанты.",
        "Изобразительное искусство",
    )
    match = match_position(topic, (foreign,))
    assert match.status is MatchStatus.UNCONFIRMED
    assert match.program_item is None
    assert bound_program_item(match) is None
    assert "Сольфеджио" in match.ambiguous_candidates
    assert topic.hours == Hours(2, 1, 1)
    assert topic.title == "Рисование натюрморта"


def test_lone_same_number_mismatch_stays_unconfirmed_in_batch():
    topic = _topic("4.1", "Рисование натюрморта", "Изобразительное искусство")
    foreign = _item(
        "4.1",
        "Сольфеджио",
        "Чужое описание: интервалы и слуховые диктанты.",
        "Изобразительное искусство",
    )
    match = match_utp_to_program((topic,), (foreign,))[0]
    assert match.status is MatchStatus.UNCONFIRMED
    assert "Сольфеджио" in match.ambiguous_candidates
    assert bound_program_item(match) is None


def test_confirmed_title_occupies_same_number_candidate():
    halt = _topic("4.1", "Рисование натюрморта", "Изобразительное искусство")
    keep = _topic("4.2", "Сольфеджио", "Изобразительное искусство")
    item = _item(
        "4.1",
        "Сольфеджио",
        "Интервалы и слуховые диктанты.",
        "Изобразительное искусство",
        1,
    )
    matches = match_utp_to_program((halt, keep), (item,))
    assert matches[1].status is MatchStatus.EXACT
    assert bound_program_item(matches[1]) is item
    assert matches[0].status is MatchStatus.NOT_MATCHED
    assert matches[0].ambiguous_candidates == ()
    assert bound_program_item(matches[0]) is None
    from calendar_pedagoga.match_review import (
        is_disputed_match,
        is_missing_program_content,
        unresolved_disputed,
    )

    assert not is_disputed_match(matches[0])
    assert is_missing_program_content(matches[0])
    assert unresolved_disputed(matches, {}) == ()


def test_unique_same_number_section_similar_is_text_match():
    topic = _topic("1.11", "Подведение итогов туристского путешествия", "Основы")
    item = _item("1.11", "Подведение итогов похода", "Обсуждение итогов и отчёт.", "Основы", 1)
    match = match_utp_to_program((topic,), (item,), study_year=1)[0]
    assert match.status is MatchStatus.TEXT_MATCH
    assert bound_program_item(match) is item
    assert match.ambiguous_candidates == ()
    from calendar_pedagoga.match_review import is_disputed_match

    assert not is_disputed_match(match)
    assert match.program_item.content == "Обсуждение итогов и отчёт."


def test_two_same_number_similar_candidates_stay_unconfirmed():
    topic = _topic("2.7", "Ориентирование по местным приметам", "Топография")
    items = (
        _item("2.7", "Ориентирование по местным предметам.", "Солнце и луна.", "Топография", 1),
        _item("2.7", "Ориентирование по местным признакам.", "Мох и звезда.", "Топография", 1),
    )
    match = match_utp_to_program((topic,), items, study_year=1)[0]
    assert match.status is MatchStatus.UNCONFIRMED
    assert bound_program_item(match) is None
    assert set(match.ambiguous_candidates) == {
        "Ориентирование по местным предметам.",
        "Ориентирование по местным признакам.",
    }


def test_occupied_unique_similar_number_does_not_bind():
    keep = _topic("1.11", "Подведение итогов похода", "Основы")
    halt = _topic("1.11", "Подведение итогов туристского путешествия", "Основы")
    item = _item("1.11", "Подведение итогов похода", "Отчёт группы.", "Основы", 1)
    matches = match_utp_to_program((keep, halt), (item,), study_year=1)
    assert matches[0].status is MatchStatus.EXACT
    assert bound_program_item(matches[0]) is item
    assert matches[1].status is MatchStatus.NOT_MATCHED
    assert bound_program_item(matches[1]) is None
    assert matches[1].ambiguous_candidates == ()


def test_single_token_without_number_stays_not_matched():
    topic = _topic("4.2", "Аптечка", "Гигиена")
    item = _item(None, "Медицинская аптечка.", "Комплектование аптечки.", "Гигиена", 1)
    match = match_utp_to_program((topic,), (item,), study_year=1)[0]
    assert match.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(match) is None
    assert "Медицинская аптечка." in match.ambiguous_candidates


def test_different_numbers_confirmed_same_title_still_match():
    topic = _topic("2.1", "Ансамбль", "Музыка")
    item = _item("5.0", "Ансамбль", "Репетиция состава.", "Музыка")
    match = match_position(topic, (item,))
    assert match.status is MatchStatus.EXACT
    assert bound_program_item(match) is item
    assert match.program_item.content == "Репетиция состава."


def test_formulation_difference_is_not_enough_to_reject():
    dotted = match_position(
        _topic("2.1", "Моя семья", "Краеведение"),
        (_item("2.1", "Моя семья.", "Семья.", "Краеведение"),),
    )
    assert dotted.status is MatchStatus.NORMALIZED
    assert bound_program_item(dotted) is not None


def test_same_action_different_object_is_not_title_proof():
    cases = (
        (
            "Измерение сопротивления электрической цепи",
            "Измерение напряжения электрической цепи",
            "Электротехника",
        ),
        (
            "Оказание первой помощи при ожогах",
            "Оказание первой помощи при переломах",
            "Медицина",
        ),
        ("Уход за одеждой", "Уход за обувью", "Быт"),
    )
    for left, right, section in cases:
        match = match_position(_topic("1.1", left, section), (_item("1.1", right, "Чужое.", section),))
        assert bound_program_item(match) is None
        assert match.status in {MatchStatus.UNCONFIRMED, MatchStatus.NOT_MATCHED}
        assert match.status is not MatchStatus.TEXT_MATCH
        assert right in match.ambiguous_candidates
        assert match.utp_position.title == left
        assert match.utp_position.hours == Hours(2, 1, 1)


def test_single_token_containment_is_not_title_proof():
    cases = (
        ("Компас", "Работа с компасом", "Ориентирование"),
        ("Туризм", "Пеший туризм", "Туризм"),
        ("Ориентирование", "Ориентирование без карты", "Туризм"),
    )
    for left, right, section in cases:
        match = match_position(_topic("1.1", left, section), (_item("1.1", right, "Чужое.", section),))
        assert bound_program_item(match) is None
        assert match.status in {MatchStatus.UNCONFIRMED, MatchStatus.NOT_MATCHED}
        assert match.status is not MatchStatus.TEXT_MATCH
        assert right in match.ambiguous_candidates
        assert match.utp_position.title == left
        assert match.utp_position.hours == Hours(2, 1, 1)


def test_utp_section_heading_matches_when_program_numbers_differ():
    topic = Topic("5", "Основы лазания", Hours(30, 4, 26), "Основы лазания", True)
    heading = _item("4", "Основы лазания", "Техника передвижения.", "Основы лазания")
    match = match_position(topic, (heading,))
    assert match.status is MatchStatus.EXACT
    assert bound_program_item(match) is heading
    assert "Техника передвижения." in match.program_item.content


def test_utp_section_prefers_section_heading_over_intro_mention():
    from calendar_pedagoga.match_review import is_disputed_match, unresolved_disputed

    topic = Topic("5", "Основы лазания", Hours(30, 4, 26), "Основы лазания", True)
    intro = _item(
        "5",
        "Тема: Цели и задачи программы «Основы лазания»",
        "Вводный инструктаж и цели.",
        None,
    )
    heading = _item(
        "4",
        "Раздел 4. Основы лазания",
        "Тема 1. Основы технической подготовки. Передвижение по рельефу.",
        "Основы лазания",
    )
    match = match_utp_to_program((topic,), (intro, heading))[0]
    assert bound_program_item(match) is heading
    assert match.status is MatchStatus.EXACT
    assert "технической подготовки" in match.program_item.content
    assert not is_disputed_match(match)
    assert unresolved_disputed((match,), {}) == ()


def test_utp_section_intro_mention_is_not_a_disputed_candidate():
    from calendar_pedagoga.match_review import is_disputed_match

    topic = Topic("5", "Основы лазания", Hours(30, 4, 26), "Основы лазания", True)
    intro = _item(
        None,
        "Тема: Цели и задачи программы «Основы лазания»",
        "Цели программы.",
        None,
    )
    match = match_position(topic, (intro,))
    assert match.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(match) is None
    assert match.ambiguous_candidates == ()
    assert not is_disputed_match(match)


def test_shared_generic_word_is_not_title_proof():
    topic = _topic("3.1", "Виды туризма", "Туризм")
    item = _item("3.9", "Пеший туризм", "Техника движения.", "Туризм")
    match = match_position(topic, (item,))
    assert match.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(match) is None


def test_same_numbers_in_different_study_years_are_not_mixed():
    year1 = _item("2.1", "Моя семья.", "Имена и традиции первого года.", "Краеведение", 1)
    year2 = _item("2.1", "Моя семья.", "Профессии родителей второго года.", "Краеведение", 2)
    topic = _topic("2.1", "Моя семья", "Краеведение")
    match = match_position(topic, (year1, year2), study_year=1)
    assert match.status is MatchStatus.NORMALIZED
    assert bound_program_item(match) is year1
    assert "второго года" not in match.program_item.content

    other = match_position(topic, (year2,), study_year=1)
    assert other.status is MatchStatus.UNCONFIRMED or other.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(other) is None


def test_ambiguous_or_incomplete_extraction_is_explicit():
    topic = _topic(None, "Моя семья", "Краеведение")
    match = match_utp_to_program(
        (topic,),
        (
            _item(None, "Моя семья!", "Первый текст", "Краеведение"),
            _item(None, "МОЯ СЕМЬЯ", "Второй текст", "Краеведение"),
        ),
    )[0]
    assert match.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(match) is None
    assert len(match.ambiguous_candidates) == 2

    empty_pool = match_position(_topic("1.1", "Введение", "Введение"), ())
    assert empty_pool.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(empty_pool) is None


def test_utp_topic_order_and_hours_survive_unconfirmed_number():
    topics = (
        _topic("1.1", "Введение", "Введение", theory=2, practice=0),
        _topic("4.1", "Рисование натюрморта", "ИЗО", theory=0, practice=2),
        _topic("4.2", "Лепка", "ИЗО", theory=1, practice=1),
    )
    items = (
        _item("1.1", "Введение", "Правила студии.", "Введение"),
        _item("4.1", "Сольфеджио", "Чужой курс.", "ИЗО"),
        _item("4.2", "Лепка", "Глина и стеки.", "ИЗО"),
    )
    matches = match_utp_to_program(topics, items)
    assert [m.utp_position.title for m in matches] == [
        "Введение",
        "Рисование натюрморта",
        "Лепка",
    ]
    assert [m.utp_position.hours for m in matches] == [
        Hours(2, 2, 0),
        Hours(2, 0, 2),
        Hours(2, 1, 1),
    ]
    assert matches[1].status is MatchStatus.UNCONFIRMED
    assert bound_program_item(matches[1]) is None
    assert bound_program_item(matches[2]) is items[2]


def test_downstream_does_not_restore_rejected_number_content():
    """content_generation берёт только bound item, не ищет программу по номеру."""

    utp_topic = Topic(
        "4.1",
        "Рисование натюрморта",
        Hours(2, 0, 2),
        "Изобразительное искусство",
    )
    utp = UtpParseResult(
        metadata=UtpMetadata(
            hours_per_week=2,
            hours_per_year=2,
            study_weeks=1,
            workload_provenance="document",
        ),
        sections=(),
        topics=(utp_topic,),
        table_totals=Hours(2, 0, 2),
    )
    week = AcademicWeek(1, date(2026, 9, 1), date(2026, 9, 7), "Сентябрь", "2026–2027")
    schedule = ScheduleResult(
        weeks=(week,),
        elements=(
            ScheduledElement(
                utp_topic.parent_section or utp_topic.title,
                utp_topic.number,
                utp_topic.title,
                "practice",
                2,
                week,
            ),
        ),
        warnings=(),
    )
    program_items = (
        _item(
            "4.1",
            "Сольфеджио",
            "Чужое описание интервалов.",
            "Изобразительное искусство",
        ),
    )
    from calendar_pedagoga.program_parsing import ProgramData

    program = ProgramData(
        title="Синтетика",
        duration=None,
        student_age=None,
        goal=None,
        tasks=(),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
        content_items=program_items,
    )
    rows = build_content_model(schedule, utp, program, "synthetic-utp.docx")
    assert len(rows) == 1
    row = rows[0]
    assert row.topic_title == "Рисование натюрморта"
    assert row.topic_number == "4.1"
    assert row.practice_hours == 2
    assert row.theory_hours == 0
    assert row.program_content_full == ""
    assert row.program_topic == ""
    assert row.match_status is MatchStatus.UNCONFIRMED
    assert any("не сопоставлена" in warning for warning in row.warnings)


def test_key_year1_number_collision_keeps_confirmed_title_only():
    """Корпус: номер 3.4 без подтверждения названия; 3.5 подтверждается заголовком."""

    from pathlib import Path

    from calendar_pedagoga.program_parsing import parse_program

    program_path = Path(__file__).resolve().parents[1] / "references" / "Программа КЛЮЧ.DOC"
    program = parse_program(program_path.read_bytes(), program_path.name, study_year=1)
    topics = (
        _topic("3.4", "Привал (бивак)", "Туризм"),
        _topic("3.5", "Ориентирование", "Туризм"),
    )
    matches = match_utp_to_program(topics, program.content_items)
    halt = matches[0]
    keep = matches[1]
    assert halt.status is MatchStatus.NOT_MATCHED
    assert halt.ambiguous_candidates == ()
    assert bound_program_item(halt) is None
    assert halt.utp_position.title == "Привал (бивак)"
    assert "ориентир" not in " ".join(halt.ambiguous_candidates).casefold()
    assert keep.status is MatchStatus.NORMALIZED
    assert bound_program_item(keep) is not None
    assert "ориентир" in keep.program_item.title.casefold()
    assert "ориентир" in keep.program_item.content.casefold()
    from calendar_pedagoga.match_review import is_disputed_match, is_missing_program_content

    assert not is_disputed_match(halt)
    assert is_missing_program_content(halt)
    assert not is_missing_program_content(keep)


def test_key_year2_and_tourists_keep_confirmed_links():
    from pathlib import Path

    from calendar_pedagoga.parsing import parse_utp
    from calendar_pedagoga.program_parsing import parse_program
    from calendar_pedagoga.resolve_utp import resolve_utp
    from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload

    references = Path(__file__).resolve().parents[1] / "references"
    key_program = parse_program(
        (references / "Программа КЛЮЧ.DOC").read_bytes(),
        "Программа КЛЮЧ.DOC",
        study_year=2,
    )
    key_utp = parse_utp(references / "УТП КЛЮЧ 2 г. 2ч.docx")
    key_matches = match_utp_to_program(key_utp.topics, key_program.content_items)
    assert [
        (
            match.utp_position.number,
            match.utp_position.title,
            match.status,
            match.program_item.title if match.program_item else None,
        )
        for match in key_matches
    ] == [
        (None, "Моя семья", MatchStatus.NORMALIZED, "Моя семья."),
        (None, "Мой город", MatchStatus.EXACT, "Мой город"),
        (None, "История родного края", MatchStatus.NORMALIZED, "История родного края."),
        (None, "Природа родного края", MatchStatus.NORMALIZED, "Природа родного края."),
        ("3.1", "Пеший туризм", MatchStatus.NORMALIZED, "Пеший туризм."),
        ("3.2", "Лыжный туризм", MatchStatus.NORMALIZED, "Лыжный туризм."),
        ("3.3", "Ориентирование", MatchStatus.NORMALIZED, "Ориентирование."),
        ("4.1", "Личная гигиена", MatchStatus.NORMALIZED, "Личная гигиена."),
        ("4.2", "Аптечка", MatchStatus.NOT_MATCHED, None),
        ("1", "Введение", MatchStatus.EXACT, "Введение"),
        ("5", "Оздоровительные мероприятия", MatchStatus.EXACT, "Оздоровительные мероприятия"),
        ("6", "Экскурсионные поездки", MatchStatus.EXACT, "Экскурсионные поездки"),
        (
            "7",
            "Туристско-краеведческие праздники, соревнования.",
            MatchStatus.EXACT,
            "Туристско-краеведческие праздники, соревнования.",
        ),
    ]
    pharmacy = next(match for match in key_matches if match.utp_position.title == "Аптечка")
    assert bound_program_item(pharmacy) is None
    assert "Медицинская аптечка." in pharmacy.ambiguous_candidates
    assert all(
        bound_program_item(match) is not None or match.utp_position.title == "Аптечка"
        for match in key_matches
    )
    assert all(match.status is not MatchStatus.NUMBER_MATCH for match in key_matches)
    assert [match.utp_position.title for match in key_matches] == [
        topic.title for topic in key_utp.topics
    ]
    assert [match.utp_position.hours for match in key_matches] == [
        topic.hours for topic in key_utp.topics
    ]

    program_path = references / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    validated = validate_upload(UploadPurpose.PROGRAM, program_path.name, program_path.read_bytes())
    tourists_utp = resolve_utp(None, validated)
    tourists_program = parse_program(validated.content, validated.filename, study_year=1)
    tourist_matches = match_utp_to_program(tourists_utp.topics, tourists_program.content_items)
    assert [
        (
            match.utp_position.number,
            match.utp_position.title,
            match.status,
            match.program_item.title if match.program_item else None,
            bound_program_item(match) is not None,
        )
        for match in tourist_matches
    ] == [
        (
            "1.1",
            "Туристские путешествия, история развития туризма",
            MatchStatus.TEXT_MATCH,
            "Туристские путешествия, история развития туризма в г. Салават",
            True,
        ),
        ("1.2", "Воспитательная роль туризма", MatchStatus.EXACT, "Воспитательная роль туризма", True),
        (
            "1.3",
            "Личное и групповое туристское снаряжение",
            MatchStatus.EXACT,
            "Личное и групповое туристское снаряжение",
            True,
        ),
        (
            "1.4",
            "Организация туристского быта. Привалы и ночлеги",
            MatchStatus.EXACT,
            "Организация туристского быта. Привалы и ночлеги",
            True,
        ),
        ("1.5", "Подготовка к походу, путешествию", MatchStatus.EXACT, "Подготовка к походу, путешествию", True),
        ("1.6", "Питание в туристском походе", MatchStatus.EXACT, "Питание в туристском походе", True),
        ("1.7", "Туристские должности в группе", MatchStatus.EXACT, "Туристские должности в группе", True),
        (
            "1.8",
            "Правила движения в походе, преодоление препятствий",
            MatchStatus.EXACT,
            "Правила движения в походе, преодоление препятствий",
            True,
        ),
        (
            "1.9",
            "Техника безопасности при проведении туристских походов, занятий",
            MatchStatus.EXACT,
            "Техника безопасности при проведении туристских походов, занятий",
            True,
        ),
        (
            "1.10",
            "Туристские слеты и соревнования",
            MatchStatus.NORMALIZED,
            "Туристские слёты и соревнования",
            True,
        ),
        (
            "1.11",
            "Подведение итогов туристского путешествия",
            MatchStatus.TEXT_MATCH,
            "Подведение итогов похода",
            True,
        ),
        (
            "2.1",
            "Понятие о топографической и спортивной карте",
            MatchStatus.EXACT,
            "Понятие о топографической и спортивной карте",
            True,
        ),
        ("2.2", "Условные знаки", MatchStatus.EXACT, "Условные знаки", True),
        (
            "2.3",
            "Ориентирование по горизонту, азимут",
            MatchStatus.EXACT,
            "Ориентирование по горизонту, азимут",
            True,
        ),
        ("2.4", "Компас. Работа с компасом", MatchStatus.NORMALIZED, "Компас, работа с компасом", True),
        ("2.5", "Измерение расстояний", MatchStatus.EXACT, "Измерение расстояний", True),
        ("2.6", "Способы ориентирования", MatchStatus.EXACT, "Способы ориентирования", True),
        (
            "2.7",
            "Ориентирование по местным приметам. Действия в случае потери ориентировки",
            MatchStatus.TEXT_MATCH,
            "Ориентирование по местным предметам.",
            True,
        ),
        (
            "3.1",
            "Родной край, его природные особенности, история, известные земляки",
            MatchStatus.TEXT_MATCH,
            "Родной край, природные особенности Башкортостана, история, известные земляки г. Салават.",
            True,
        ),
        (
            "3.2",
            "Туристские возможности родного края, обзор экскурсионных объектов, музеи",
            MatchStatus.TEXT_MATCH,
            "Туристские возможности Башкортостана, обзор экскурсионных объектов, музеи г. Салавата и Башкортостана.",
            True,
        ),
        ("3.3", "Изучение района путешествия", MatchStatus.EXACT, "Изучение района путешествия", True),
        (
            "3.4",
            "Общественно полезная работа в путешествии, охрана природы и памятников культуры",
            MatchStatus.TEXT_MATCH,
            "Общественно полезная работа в путешествии, охрана природы и памятников культуры Башкортостана.",
            True,
        ),
        (
            "4.1",
            "Личная гигиена туриста, профилактика заболеваний",
            MatchStatus.EXACT,
            "Личная гигиена туриста, профилактика заболеваний",
            True,
        ),
        (
            "4.2",
            "Походная медицинская аптечка, использование лекарственных растений",
            MatchStatus.TEXT_MATCH,
            "Походная медицинская аптечка",
            True,
        ),
        (
            "4.3",
            "Основные приемы оказания первой доврачебной помощи",
            MatchStatus.NORMALIZED,
            "Основные приёмы оказания первой доврачебной помощи",
            True,
        ),
        (
            "4.4",
            "Приемы транспортировки пострадавшего",
            MatchStatus.NORMALIZED,
            "Приёмы транспортировки пострадавшего",
            True,
        ),
        (
            "5.1",
            "Краткие сведения о строении и функциях организма человека и влиянии физических упражнений",
            MatchStatus.EXACT,
            "Краткие сведения о строении и функциях организма человека и влиянии физических упражнений",
            True,
        ),
        (
            "5.2",
            "Врачебный контроль, самоконтроль, предупреждение спортивных травм на тренировках",
            MatchStatus.NORMALIZED,
            "Врачебный контроль, самоконтроль, предупреждение спортивных травм на тренировках.",
            True,
        ),
        ("5.3", "Общая физическая подготовка", MatchStatus.EXACT, "Общая физическая подготовка", True),
        (
            "5.4",
            "Специальная физическая подготовка",
            MatchStatus.EXACT,
            "Специальная физическая подготовка",
            True,
        ),
    ]
    assert [match.utp_position.hours for match in tourist_matches] == [
        topic.hours for topic in tourists_utp.topics
    ]
    from calendar_pedagoga.match_review import is_disputed_match

    for number in ("1.11", "2.7", "3.2"):
        match = next(item for item in tourist_matches if item.utp_position.number == number)
        assert match.status is MatchStatus.TEXT_MATCH
        assert not is_disputed_match(match)
        assert bound_program_item(match) is not None


def test_different_actions_or_conditions_are_not_title_proof():
    cases = (
        (
            "Измерение сопротивления электрической цепи",
            "Сборка электрической цепи",
            "Электротехника",
        ),
        (
            "Ремонт туристского снаряжения",
            "Хранение туристского снаряжения",
            "Туризм",
        ),
        ("Работа с компасом", "Работа без компаса", "Ориентирование"),
    )
    for left, right, section in cases:
        match = match_position(_topic("1.1", left, section), (_item("1.1", right, "Чужое.", section),))
        assert match.status is not MatchStatus.TEXT_MATCH
        assert bound_program_item(match) is None
        assert right in match.ambiguous_candidates
        assert match.utp_position.title == left
        assert match.utp_position.hours == Hours(2, 1, 1)


def test_prefix_alignment_does_not_confirm_unrelated_stems():
    match = match_position(
        _topic("2.1", "Угольник", "Черчение"),
        (_item("2.1", "Угольный", "Другая основа.", "Черчение"),),
    )
    assert bound_program_item(match) is None
    assert match.status is not MatchStatus.TEXT_MATCH


def test_composition_section_conflict_is_not_bypassed_by_same_title():
    music = _item("5.1", "Композиция", "Музыкальное сочинение.", "Музыка")
    match = match_position(_topic("5.1", "Композиция", "ИЗО"), (music,))
    assert match.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(match) is None
    assert match.program_item is None

    same = match_position(
        _topic("5.1", "Композиция", "ИЗО"),
        (_item("5.1", "Композиция", "Рисунок и пятно.", "ИЗО"),),
    )
    assert same.status is MatchStatus.EXACT
    assert bound_program_item(same) is not None
    assert same.program_item.content == "Рисунок и пятно."

    dotted = match_position(
        _topic("5.1", "Композиция", "ИЗО"),
        (_item("5.1", "Композиция.", "Рисунок и пятно.", "ИЗО"),),
    )
    assert dotted.status is MatchStatus.NORMALIZED
    assert bound_program_item(dotted) is not None


def test_section_rejected_candidate_is_not_restored_by_number():
    foreign = _item("4.1", "Композиция", "Содержание чужого раздела.", "Музыка")
    match = match_position(_topic("4.1", "Композиция", "ИЗО"), (foreign,))
    assert bound_program_item(match) is None
    assert match.status is not MatchStatus.EXACT
    assert match.status is not MatchStatus.NORMALIZED
    assert match.status is not MatchStatus.TEXT_MATCH
    assert match.status is not MatchStatus.NUMBER_MATCH


def test_multiple_text_candidates_keep_ambiguity_without_number_hit():
    match = match_position(
        _topic("9.9", "Аптечка", "Гигиена"),
        (
            _item("1.1", "Походная аптечка", "Состав походный.", "Гигиена"),
            _item("1.2", "Домашняя аптечка", "Состав домашний.", "Гигиена"),
        ),
    )
    assert match.status is MatchStatus.NOT_MATCHED
    assert bound_program_item(match) is None
    assert match.ambiguous_candidates == ("Походная аптечка", "Домашняя аптечка")


def test_unknown_section_is_not_treated_as_conflict():
    match = match_position(
        _topic("1.1", "Введение", None),
        (_item("1.1", "Введение", "Правила студии.", None),),
    )
    assert match.status is MatchStatus.EXACT
    assert bound_program_item(match) is not None


def test_key_years_are_isolated_by_parsed_content_not_stamped_year():
    from pathlib import Path

    from calendar_pedagoga.program_parsing import parse_program

    program_path = Path(__file__).resolve().parents[1] / "references" / "Программа КЛЮЧ.DOC"
    year1 = parse_program(program_path.read_bytes(), program_path.name, study_year=1)
    year2 = parse_program(program_path.read_bytes(), program_path.name, study_year=2)
    family1 = next(item for item in year1.content_items if "семья" in item.title.casefold())
    family2 = next(item for item in year2.content_items if "семья" in item.title.casefold())
    assert "имена и фамилии" in family1.content.casefold()
    assert "профессии родителей" in family2.content.casefold()
    assert "профессии родителей" not in " ".join(item.content for item in year1.content_items).casefold()
    assert "имена и фамилии" not in " ".join(item.content for item in year2.content_items).casefold()
    assert any(item.title == "Знакомство с детьми." for item in year1.content_items)
    assert not any("знакомство с детьми" in item.title.casefold() for item in year2.content_items)
    assert any(item.title.startswith("Пеший туризм") for item in year2.content_items)
    assert not any(item.title.startswith("Пеший туризм") for item in year1.content_items)


def test_key_year1_mixed_week_keeps_prival_unbound():
    from calendar_pedagoga.program_parsing import ProgramData, parse_program
    from pathlib import Path

    program = parse_program(
        (Path(__file__).resolve().parents[1] / "references" / "Программа КЛЮЧ.DOC").read_bytes(),
        "Программа КЛЮЧ.DOC",
        study_year=1,
    )
    duties = _topic("3.3", "Туристские должности", "Туризм", theory=1, practice=1)
    halt = _topic("3.4", "Привал (бивак)", "Туризм", theory=0, practice=1)
    orient = _topic("3.5", "Ориентирование", "Туризм", theory=1, practice=1)
    utp = UtpParseResult(
        metadata=UtpMetadata(
            hours_per_week=2,
            hours_per_year=4,
            study_weeks=2,
            workload_provenance="document",
        ),
        sections=(),
        topics=(duties, halt, orient),
        table_totals=Hours(4, 2, 2),
    )
    week23 = AcademicWeek(23, date(2027, 2, 8), date(2027, 2, 14), "Февраль", "2026–2027")
    week24 = AcademicWeek(24, date(2027, 2, 15), date(2027, 2, 21), "Февраль", "2026–2027")
    schedule = ScheduleResult(
        weeks=(week23, week24),
        elements=(
            ScheduledElement("Туризм", duties.number, duties.title, "theory", 1, week23),
            ScheduledElement("Туризм", duties.number, duties.title, "practice", 1, week23),
            ScheduledElement("Туризм", halt.number, halt.title, "practice", 1, week23),
            ScheduledElement("Туризм", orient.number, orient.title, "theory", 1, week24),
            ScheduledElement("Туризм", orient.number, orient.title, "practice", 1, week24),
        ),
        warnings=(),
    )
    rows = build_content_model(schedule, utp, program, "synthetic-key-year1.docx")
    mixed = next(row for row in rows if row.week_number == 23)
    oriented = next(row for row in rows if row.week_number == 24)
    halt_part = next(part for part in mixed.week_parts if part.topic_title == "Привал (бивак)")
    duty_part = next(part for part in mixed.week_parts if part.topic_title == "Туристские должности")
    assert halt_part.match_status is MatchStatus.NOT_MATCHED
    assert halt_part.program_content_full == ""
    assert halt_part.program_topic == ""
    assert "Не найден отдельный блок содержания программы для темы" in " ".join(
        halt_part.warnings
    )
    assert "номер без подтверждения названия" not in " ".join(halt_part.warnings)
    assert "решение педагога" not in " ".join(halt_part.warnings)
    assert duty_part.match_status is MatchStatus.NORMALIZED
    assert "командир" in duty_part.program_content_full.casefold()
    assert "ориентирован" not in duty_part.program_content_full.casefold()
    assert oriented.match_status is MatchStatus.NORMALIZED
    assert "ориентирован" in oriented.program_content_full.casefold()
    assert "привал" not in oriented.program_content_full.casefold()


def _two_year_program_docx() -> bytes:
    from io import BytesIO

    from docx import Document

    document = Document()
    headings = {
        "Содержание программы 1-го года обучения",
        "1. Введение",
        "2. Краеведение",
        "2.1 Моя семья",
        "Содержание программы 2-го года обучения",
    }
    for text in (
        "Дополнительная общеобразовательная программа «Синтетика»",
        "Содержание программы 1-го года обучения",
        "1. Введение",
        "Правила первого года.",
        "2. Краеведение",
        "Изучение родного края в первом году.",
        "2.1 Моя семья",
        "Имена и традиции первого года.",
        "Содержание программы 2-го года обучения",
        "1. Введение",
        "Правила второго года.",
        "2. Краеведение",
        "Изучение родного края во втором году.",
        "2.1 Моя семья",
        "Профессии родителей второго года.",
        "По окончании второго года обучения обучающиеся должны знать:",
        "правила безопасности.",
    ):
        paragraph = document.add_paragraph()
        run = paragraph.add_run(text)
        if text in headings:
            run.bold = True
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def _one_week_family_model(program, source_name: str, utp_study_year: str | None = None):
    topic = Topic("2.1", "Моя семья", Hours(2, 1, 1), "Краеведение")
    utp = UtpParseResult(
        metadata=UtpMetadata(
            hours_per_week=2,
            hours_per_year=2,
            study_weeks=1,
            workload_provenance="document",
            study_year=utp_study_year,
        ),
        sections=(),
        topics=(topic,),
        table_totals=Hours(2, 1, 1),
    )
    week = AcademicWeek(1, date(2026, 9, 1), date(2026, 9, 7), "Сентябрь", "2026–2027")
    schedule = ScheduleResult(
        weeks=(week,),
        elements=(
            ScheduledElement("Краеведение", topic.number, topic.title, "theory", 1, week),
            ScheduledElement("Краеведение", topic.number, topic.title, "practice", 1, week),
        ),
        warnings=(),
    )
    return build_content_model(schedule, utp, program, source_name)


def test_parsed_two_year_document_keeps_year_isolation_in_content_model():
    from calendar_pedagoga.program_parsing import parse_program_docx

    data = _two_year_program_docx()
    year1 = parse_program_docx(data, study_year=1)
    year2 = parse_program_docx(data, study_year=2)
    unknown = parse_program_docx(data, study_year=None)

    year1_blob = " ".join(item.content for item in year1.content_items).casefold()
    year2_blob = " ".join(item.content for item in year2.content_items).casefold()
    unknown_blob = " ".join(item.content for item in unknown.content_items).casefold()
    assert "традиции первого года" in year1_blob
    assert "профессии родителей второго года" not in year1_blob
    assert "профессии родителей второго года" in year2_blob
    assert "традиции первого года" not in year2_blob
    assert all(item.study_year == 1 for item in year1.content_items)
    assert all(item.study_year == 2 for item in year2.content_items)
    assert all(item.study_year is None for item in unknown.content_items)
    assert "традиции первого года" in unknown_blob
    assert "профессии родителей второго года" not in unknown_blob
    assert not any(
        "содержание программы" in item.title.casefold() for item in year1.content_items
    )

    row1 = _one_week_family_model(year1, "synthetic-year1.docx")[0]
    row2 = _one_week_family_model(year2, "synthetic-year2.docx")[0]
    row_unknown = _one_week_family_model(unknown, "synthetic-year-unknown.docx")[0]
    confirmed = {MatchStatus.EXACT, MatchStatus.NORMALIZED}
    assert row1.match_status in confirmed
    assert "традиции первого года" in row1.program_content_full.casefold()
    assert "профессии родителей" not in row1.program_content_full.casefold()
    assert row2.match_status in confirmed
    assert "профессии родителей второго года" in row2.program_content_full.casefold()
    assert "традиции первого года" not in row2.program_content_full.casefold()
    assert row_unknown.match_status in confirmed
    assert "традиции первого года" in row_unknown.program_content_full.casefold()
    assert all(item.study_year is None for item in unknown.content_items)


def test_build_content_model_passes_study_year_to_matching():
    """Live matching получает год обучения УТП и не смешивает одинаковые темы разных годов."""

    import inspect

    from calendar_pedagoga.content_generation import study_year_for_matching
    from calendar_pedagoga.program_parsing import ProgramData
    from calendar_pedagoga import pipeline, ui

    year1 = _item("2.1", "Моя семья.", "Имена и традиции первого года.", "Краеведение", 1)
    year2 = _item("2.1", "Моя семья.", "Профессии родителей второго года.", "Краеведение", 2)
    program = ProgramData(
        title="Синтетика",
        duration=None,
        student_age=None,
        goal=None,
        tasks=(),
        lesson_forms=(),
        teaching_methods=(),
        expected_results=(),
        knowledge_outcomes=(),
        skill_outcomes=(),
        content_items=(year1, year2),
    )

    model_src = inspect.getsource(build_content_model)
    assert "study_year_for_matching" in model_src
    assert "study_year=study_year_for_matching(utp)" in model_src
    pipeline_src = inspect.getsource(pipeline.run_calendar_pipeline)
    assert "build_content_model(" in pipeline_src
    assert "match_utp_to_program" not in pipeline_src
    ui_src = inspect.getsource(ui)
    assert "study_year=matching_year" in ui_src
    assert "study_year_for_matching" in ui_src

    row1 = _one_week_family_model(program, "mixed-years.docx", "1 год обучения")[0]
    assert study_year_for_matching(
        UtpParseResult(
            metadata=UtpMetadata(study_year="1 год обучения"),
            sections=(),
            topics=(),
            table_totals=None,
        )
    ) == 1
    assert row1.match_status is MatchStatus.NORMALIZED
    assert row1.program_content_full == year1.content
    assert "традиции первого года" in row1.program_content_full.casefold()
    assert "профессии родителей" not in row1.program_content_full.casefold()
    assert bound_program_item(
        match_utp_to_program(
            (_topic("2.1", "Моя семья", "Краеведение"),),
            program.content_items,
            study_year=1,
        )[0]
    ) is year1

    row2 = _one_week_family_model(program, "mixed-years.docx", "2 год обучения")[0]
    assert row2.match_status is MatchStatus.NORMALIZED
    assert row2.program_content_full == year2.content
    assert "профессии родителей второго года" in row2.program_content_full.casefold()
    assert "традиции первого года" not in row2.program_content_full.casefold()

    unknown = _one_week_family_model(program, "mixed-years.docx")[0]
    assert unknown.match_status is MatchStatus.NOT_MATCHED
    assert unknown.program_content_full == ""
    assert unknown.program_topic == ""
    assert any("не сопоставлена" in warning for warning in unknown.warnings)
