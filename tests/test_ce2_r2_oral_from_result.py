"""R2: oral CONTROL is built from the accepted RESULT object only."""

from calendar_pedagoga.content_engine_v2 import (
    ActionFrame,
    build_lesson_content_v2,
    control_from_frame,
    derive_fields_v2,
)
from test_ce2_grounded_triad import _synthetic_week


def _oral(result: str, *, clause: str = "") -> str:
    return control_from_frame(
        ActionFrame(clause or result, "", "", ""),
        planned_result=result,
        lesson_type="теоретическое занятие",
        theory_hours=1,
        practice_hours=0,
    )


def test_long_result_oral_keeps_full_object():
    result = (
        "Характеризует роль туризма в подготовке к защите Родины, "
        "в выборе профессии и подготовке к трудовой деятельности."
    )
    control = _oral(result)
    assert control.startswith("устный опрос по роли туризма")
    assert "защите Родины" in control
    assert "выборе профессии" in control
    assert "трудовой деятельности" in control
    assert control == (
        "устный опрос по роли туризма в подготовке к защите Родины, "
        "в выборе профессии и подготовке к трудовой деятельности"
    )


def test_coordinated_x_and_y_does_not_drop_y():
    result = "Характеризует устройство прибора и правила обращения."
    control = _oral(result)
    assert control.startswith("устный опрос по")
    assert "устройств" in control
    assert "правил" in control


def test_several_prepositional_phrases_are_not_cut():
    result = (
        "Характеризует значение леса в жизни человека, "
        "в хозяйстве и в охране природы."
    )
    control = _oral(result)
    assert "жизни человека" in control
    assert "хозяйстве" in control
    assert "охране природы" in control


def test_mixed_week_keeps_oral_and_observation_separate():
    row = _synthetic_week(
        (
            "A.1",
            "Роль прибора",
            "Роль прибора в обучении, в выборе профессии и подготовке к труду.",
            1,
            0,
        ),
        (
            "A.2",
            "Сборка прибора",
            "Сборка учебной модели прибора.",
            0,
            1,
        ),
    )
    merged = build_lesson_content_v2((row,))[0]
    assert merged.planned_result.casefold().startswith("характеризует")
    chunks = [item.strip() for item in merged.assessment_method.split(";")]
    oral = next(item for item in chunks if item.startswith("устный опрос по"))
    assert "рол" in oral.casefold()
    assert "сборк" not in oral.casefold()
    rest = [item for item in chunks if not item.startswith("устный опрос")]
    assert rest
    assert any(
        item.startswith("педагогическое наблюдение") or item.startswith("проверка")
        for item in rest
    )


def test_control_does_not_add_object_absent_from_result():
    result = "Характеризует назначение прибора."
    clause = (
        "Прибор, его устройство и назначение, правила обращения, "
        "градусное значение основных направлений."
    )
    control = _oral(result, clause=clause)
    assert control == "устный опрос по назначению прибора"
    assert "устройств" not in control
    assert "правил" not in control
    assert "градусн" not in control


def test_derive_fields_oral_follows_accepted_result_not_source_tail():
    derived = derive_fields_v2(
        topic_title="Роль туризма",
        theory_text=(
            "Роль туризма в подготовке к защите Родины, "
            "в выборе профессии и подготовке к трудовой деятельности."
        ),
        practice_text="",
        program_content=(
            "Роль туризма в подготовке к защите Родины, "
            "в выборе профессии и подготовке к трудовой деятельности."
        ),
        theory_hours=1,
        practice_hours=0,
    )
    assert derived.planned_result.startswith("Характеризует роль туризма")
    assert "выборе профессии" in derived.assessment_method
    assert "трудовой деятельности" in derived.assessment_method
