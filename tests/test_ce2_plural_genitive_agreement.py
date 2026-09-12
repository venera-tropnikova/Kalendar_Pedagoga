import pytest

from calendar_pedagoga.content_engine_v2 import derive_fields_v2


def derive(theory_text):
    return derive_fields_v2(
        topic_title="Учебная тема", theory_text=theory_text, practice_text="",
        program_content=theory_text, theory_hours=2, practice_hours=0,
    )


@pytest.mark.parametrize(("source", "expected", "rejected"), [
    (
        "Знаменитые башкирские путешественники, их роль в развитии Башкортостана.",
        "путешественников", "путешественника",
    ),
    (
        "Волевые усилия и их значение в походах и тренировках.",
        "волевых усилий", "волевых усилия",
    ),
])
def test_object_of_plural_modifiers_stays_plural(source, expected, rejected):
    # Объект склоняется по числу своих определений, иначе гейт отвергает действие.
    derived = derive(source)
    for text in (derived.planned_result, derived.assessment_method):
        assert expected in text.casefold()
        assert rejected not in text.casefold()
