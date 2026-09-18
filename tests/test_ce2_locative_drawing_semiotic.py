from calendar_pedagoga.content_engine_v2 import derive_fields_v2


def _practice(text: str, topic: str = "Тема"):
    return derive_fields_v2(
        topic_title=topic,
        theory_text="",
        practice_text=text,
        program_content=text,
        theory_hours=0,
        practice_hours=2,
    )


def test_locative_drawings_become_draws_object():
    actual = _practice("Животные и птицы в рисунках детей.")
    assert actual.planned_result.startswith("Рисует животных и птиц")
    assert "просмотр рисунков" in actual.assessment_method.casefold()
    assert "создаёт" not in actual.planned_result.casefold()


def test_locative_pupils_and_students_aliases():
    pupils = _practice("Реки в рисунках учащихся.")
    students = _practice("Горы в рисунках учеников.")
    assert pupils.planned_result.casefold().startswith("рисует")
    assert students.planned_result.casefold().startswith("рисует")
    assert "реки" in pupils.planned_result.casefold() or "рек" in pupils.planned_result.casefold()


def test_explicit_drawing_and_making_and_creating_and_design():
    drawing = _practice("Рисование листьев.")
    making = _practice("Изготовление кормушек.")
    creating = _practice("Создание макета родного края.")
    design = _practice("Разработка маршрутного листа.")
    finite = _practice("Рисуют деревья.")
    assert drawing.planned_result.casefold().startswith("рисует")
    assert making.planned_result.casefold().startswith("изготавливает")
    assert creating.planned_result.casefold().startswith("создаёт")
    assert design.planned_result.casefold().startswith("разрабатывает")
    assert finite.planned_result.casefold().startswith("рисует")
    assert "просмотр и оценка готовой работы" in making.assessment_method.casefold()
    assert "просмотр и оценка готовой работы" in creating.assessment_method.casefold()


def test_semiotic_sign_symbol_emblem_pictogram_without_making():
    sign = _practice("Запрещающие знаки «Берегите природу».")
    symbol = _practice("Условные символы карты.")
    emblem = _practice("Эмблема отряда.")
    pictogram = _practice("Пиктограммы безопасности.")
    for actual in (sign, symbol, emblem, pictogram):
        low = actual.planned_result.casefold()
        assert low.startswith("распознаёт")
        assert "объясняет" in low and "значение" in low
        assert "изготавливает" not in low
        assert "создаёт" not in low
        assert "устный опрос по значению" in actual.assessment_method.casefold()


def test_rule_and_monument_are_not_semiotic_fallback():
    rule = _practice("Правила поведения на природе.")
    monument = _practice("Памятники города.")
    for actual in (rule, monument):
        low = actual.planned_result.casefold()
        assert "распознаёт" not in low
        assert "изготавливает" not in low
        assert "создаёт" not in low


def test_creative_type_does_not_invent_create():
    actual = _practice("Животные в рисунках детей.")
    assert "создаёт" not in actual.planned_result.casefold()
    assert "создает" not in actual.planned_result.casefold()
    assert actual.planned_result.casefold().startswith("рисует")


def test_making_signs_stays_productive_not_semiotic():
    actual = _practice("Изготовление запрещающих знаков.")
    assert actual.planned_result.casefold().startswith("изготавливает")
    assert "распознаёт" not in actual.planned_result.casefold()


def test_existing_drawing_noun_clause_unchanged():
    actual = _practice(
        "Проведение дидактических и ролевых игр: «Давай поговорим». Рисунки."
    )
    assert "выполняет рисунки" in actual.planned_result.casefold()


def test_key_w17_locative_and_prohibition_signs():
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
    assert "природоохранн" not in actual.planned_result.casefold()
    assert "природоохранн" not in actual.assessment_method.casefold()
