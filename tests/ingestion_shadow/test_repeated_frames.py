"""Repeated RESULT/CONTROL uses the existing phase or catalog allocator."""
from fractions import Fraction
from pathlib import Path
import runpy

import pytest

from calendar_pedagoga.ingestion_adapter import ExplicitWorkload
from calendar_pedagoga.ingestion_confirmation import apply, assess, build_model, start, State
from calendar_pedagoga.ingestion_shadow import ShadowBlocked, run_shadow

helpers = runpy.run_path(str(Path(__file__).parents[1] / "structural_interpretation" / "test_structural_properties.py"))
p, table, interpret = helpers["p"], helpers["table"], helpers["interpret"]
YEAR = "2026–2027"


def document(topics):
    total = sum(hours for _title, hours, _fragments in topics)
    values = [
        (str(index), title, str(hours), str(hours), "0", "0")
        for index, (title, hours, _fragments) in enumerate(topics, start=1)
    ]
    values.append(("", "Итого", str(total), str(total), "0", "0"))
    body = p("Учебно-тематический план 1 года обучения", bold=True)
    body += table(values=values)
    body += p("Содержание программы 1 года обучения", bold=True)
    for index, (title, _hours, fragments) in enumerate(topics, start=1):
        body += p(f"{index}. {title}", bold=True)
        for fragment in fragments:
            body += p(fragment)
    return interpret(body)


def confirm(model):
    session = start(build_model(model))
    view = assess(session)
    if view.state == State.BINDING_AMBIGUOUS:
        session = apply(session, "bind_topics", {"bindings": {
            question.topic_id: [view.content[index].id]
            for index, question in enumerate(view.questions)
        }})
    assert assess(session).state == State.VALID
    return apply(session, "confirm")


def published(topics):
    total = sum(hours for _title, hours, _fragments in topics)
    return run_shadow(
        confirm(document(topics)),
        academic_year=YEAR,
        workload=ExplicitWorkload(total, Fraction(1), "explicit"),
        publish=False,
    )


def rendered(result):
    return [
        (
            lesson.source.source.topic_title,
            lesson.planned_result,
            lesson.assessment_method,
        )
        for lesson in result.lessons
    ]


def test_distinct_sources_collapse_is_blocked_without_stage_numbers():
    with pytest.raises(ShadowBlocked) as caught:
        published([
            ("Тема Альфа", 2, ["Изучает карту местности.", "Изучает рельеф местности."]),
        ])
    assert caught.value.code == "SEMANTIC_COLLAPSE"
    assert caught.value.docx is None


def test_identical_source_on_several_slots_uses_phase_frames():
    result = published([
        ("Тема Альфа", 2, ["Изучает устройство компаса.", "Изучает устройство компаса."]),
    ])
    results = [item[1] for item in rendered(result)]
    controls = [item[2] for item in rendered(result)]
    assert results == [
        "Выполняет этап 1 из 2 практической работы по теме «Тема Альфа».",
        "Выполняет этап 2 из 2 практической работы по теме «Тема Альфа».",
    ]
    assert len(set(results)) == 2
    assert len(set(controls)) == 2
    assert all("этап" in control for control in controls)


def test_title_only_multiweek_uses_phase_frames():
    result = published([("Тема Альфа", 2, [])])
    results = [item[1] for item in rendered(result)]
    assert "этап 1 из 2" in results[0]
    assert "этап 2 из 2" in results[1]
    assert results[0] != results[1]


def test_catalog_members_are_distributed_without_reuse():
    result = published([("Фестивали, слёты, соревнования", 3, [])])
    results = [item[1] for item in rendered(result)]
    assert results == [
        "Участвует в фестивалях.",
        "Участвует в слётах.",
        "Участвует в соревнованиях.",
    ]


def test_phase_indexes_do_not_mix_topics():
    result = published([
        ("Тема Альфа", 2, []),
        ("Тема Бета", 2, []),
    ])
    by_topic = {}
    for title, planned, _control in rendered(result):
        by_topic.setdefault(title, []).append(planned)
    assert all("из 2" in planned for planned in by_topic["Тема Альфа"])
    assert all("из 2" in planned for planned in by_topic["Тема Бета"])
    assert all("Бета" not in planned for planned in by_topic["Тема Альфа"])
    assert all("Альфа" not in planned for planned in by_topic["Тема Бета"])


def test_repeated_route_is_deterministic():
    topics = [("Фестивали, слёты, соревнования", 3, [])]
    assert rendered(published(topics)) == rendered(published(topics))
