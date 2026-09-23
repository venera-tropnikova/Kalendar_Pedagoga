import pytest

from calendar_pedagoga.lesson_display import (
    _compact_repeated_practice_postfix,
    brief_allocated_work_labels,
    brief_practice_summary,
    brief_theory_fragment,
    format_practice_cell,
    format_schedule_channel_cell,
    format_schedule_topic_cell,
    format_theory_cell,
    normalize_display_topic_title,
    selected_practice_clause,
)


def _display_blocks(*blocks: tuple[str, str]) -> str:
    return " ".join(f"{main}. {postfix}." for main, postfix in blocks)


def test_display_compacts_exact_repeated_postfix_three_times() -> None:
    source = _display_blocks(
        ("Отработка узла A", "Круговое ОФП"),
        ("Отработка узла B", "Круговое ОФП"),
        ("Отработка узла C", "Круговое ОФП"),
    )

    display = _compact_repeated_practice_postfix(source)

    assert display.count("После каждого блока:") == 1
    assert display.count("Круговое ОФП") == 1
    assert all(f"узла {name}" in display for name in "ABC")
    assert all(f"{index})" in display for index in range(1, 4))


def test_display_compacts_same_postfix_and_same_dosage() -> None:
    source = _display_blocks(
        ("Блок равновесия", "Круговое ОФП (2 круга)"),
        ("Блок гибкости", "Круговое ОФП (2 круга)"),
    )

    display = _compact_repeated_practice_postfix(source)

    assert display.count("Круговое ОФП (2 круга)") == 1
    assert "После каждого блока:" in display


def test_display_compacts_ordered_dosage_vector_with_block_binding() -> None:
    source = _display_blocks(
        ("Блок осанки", "Коллективные приседания (40 раз)"),
        ("Блок корсета", "Коллективные приседания (50 раз)"),
        ("Блок внимания", "Коллективные приседания (50 раз)"),
    )

    display = _compact_repeated_practice_postfix(source)

    assert "блоки 1/2/3: 40/50/50 раз" in display
    assert display.count("Коллективные приседания") == 1


@pytest.mark.parametrize(
    "postfixes",
    (
        ("Круговое ОФП: планка", "Круговое ОФП: прыжки"),
        ("Лазание трасс на время", "Лазание трасс на точность"),
        ("Лазание легких трасс", "Лазание сложных трасс"),
    ),
)
def test_display_does_not_compact_different_postfix_frame(
    postfixes: tuple[str, str],
) -> None:
    source = _display_blocks(("Главное действие A", postfixes[0]), ("Главное действие B", postfixes[1]))

    assert _compact_repeated_practice_postfix(source) == source.rstrip(" .")


def test_display_does_not_compact_ambiguous_punctuation() -> None:
    postfix = "Круговое ОФП: планка, вис на турнике «складочки» (2 круга)"
    source = _display_blocks(("Главное действие A", postfix), ("Главное действие B", postfix))

    assert _compact_repeated_practice_postfix(source) == source.rstrip(" .")


def test_display_does_not_compact_safety_or_final_action() -> None:
    source = _display_blocks(
        ("Главное действие A", "Соблюдать требования безопасности"),
        ("Главное действие B", "Соблюдать требования безопасности"),
    )

    assert _compact_repeated_practice_postfix(source) == source.rstrip(" .")


def test_display_compaction_does_not_mutate_source_semantic_model() -> None:
    from calendar_pedagoga.content_engine_v2 import derive_fields_v2

    source = _display_blocks(
        ("Отработка осанки", "Круговое ОФП (2 круга)"),
        ("Отработка гибкости", "Круговое ОФП (2 круга)"),
    )
    semantic = derive_fields_v2(
        topic_title="ОФП",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    snapshot = (
        semantic.planned_result,
        semantic.assessment_method,
        semantic.clause_coverage,
    )

    display = format_practice_cell("3", "ОФП", "Практика. " + source, 2)

    assert "После каждого блока:" in display
    assert source == (
        "Отработка осанки. Круговое ОФП (2 круга). "
        "Отработка гибкости. Круговое ОФП (2 круга)."
    )
    assert (
        semantic.planned_result,
        semantic.assessment_method,
        semantic.clause_coverage,
    ) == snapshot


def test_theory_without_practice_marker_uses_title_only() -> None:
    content = "Понятие о личном и групповом снаряжении. Перечень личного снаряжения."
    assert format_theory_cell("1.3", "Личное и групповое туристское снаряжение", content, 1) == (
        "1.3. Личное и групповое туристское снаряжение (1)"
    )


def test_theory_with_practice_marker_keeps_theory_fragment() -> None:
    content = (
        "Время основания города, откуда произошло название? "
        "Практика. Экскурсии по улицам города."
    )
    cell = format_theory_cell("2.2", "Мой город", content, 1)
    assert cell.startswith("2.2. Мой город. Время основания города")
    assert "Экскурсии" not in cell


def test_practice_uses_block_after_marker() -> None:
    content = (
        "Теория по теме.\n"
        "Практические занятия. Укладка рюкзаков, подгонка снаряжения. "
        "Работа со снаряжением, уход за ним и ремонт."
    )
    cell = format_practice_cell("1.3", "Снаряжение", content, 1)
    assert cell == (
        "Укладка рюкзаков, подгонка снаряжения. "
        "Работа со снаряжением, уход за ним и ремонт (1)"
    )


def test_practice_marker_without_period_does_not_take_theory() -> None:
    content = (
        "Основная задача общей физической подготовки – развитие качеств.\n"
        "Практические занятия Упражнения для рук и плечевого пояса. "
        "Упражнения для мышц шеи."
    )
    summary, kind = brief_practice_summary(content)
    assert kind == "block"
    assert "Основная задача" not in summary
    assert "Упражнения для рук и плечевого пояса" in summary
    cell = format_practice_cell("5.3", "Общая физическая подготовка", content, 2)
    assert "Основная задача" not in cell
    assert "Упражнения для рук" in cell


def test_practice_does_not_duplicate_theory_title() -> None:
    theory = "1.3. Личное и групповое туристское снаряжение (1)"
    content = (
        "Понятие о личном и групповом снаряжении.\n"
        "Практические занятия. Укладка рюкзаков."
    )
    practice = format_practice_cell("1.3", "Личное и групповое туристское снаряжение", content, 1)
    assert practice != theory
    assert "Укладка рюкзаков" in practice


def test_repeated_topic_shows_selected_ce2_clause() -> None:
    content = (
        "Роль специальной подготовки.\n"
        "Практические занятия Упражнение на развитие выносливости. "
        "Упражнения на развитие быстроты. Упражнения на развитие силы. "
        "Упражнения на развитие гибкости, на растягивание и расслабление мышц."
    )
    title = "Специальная физическая подготовка"
    expected = (
        "Упражнение на развитие выносливости",
        "Упражнения на развитие быстроты",
        "Упражнения на развитие силы",
        "Упражнения на развитие гибкости, на растягивание и расслабление мышц",
    )
    for index, clause in enumerate(expected):
        selected = selected_practice_clause(
            topic_title=title,
            content=content,
            theory_hours=0,
            practice_hours=2,
            occurrence_index=index,
            appearance_count=5,
        )
        assert selected.casefold().startswith(clause.casefold()[:20])
        cell = format_practice_cell("5.4", title, content, 2, selected)
        assert clause.rstrip(".") in cell
        assert "Роль специальной" not in cell
        for other in expected:
            if other != clause:
                assert other.rstrip(".") not in cell

    # Weeks > clauses continue the last assigned slot, not modulo to the first.
    wrapped = selected_practice_clause(
        topic_title=title,
        content=content,
        theory_hours=0,
        practice_hours=2,
        occurrence_index=4,
        appearance_count=5,
    )
    assert wrapped.startswith("Продолжение.")
    assert "гибкости" in wrapped.casefold()
    assert "выносливости" not in wrapped.casefold()


def test_no_ellipsis_in_display_cells() -> None:
    content = "А" * 500 + "\nПрактика. " + "Б" * 500
    for value in (
        format_theory_cell("1.1", "Тема", content, 1),
        format_practice_cell("1.1", "Тема", content, 1),
        brief_theory_fragment(content),
        brief_practice_summary(content)[0],
    ):
        assert "…" not in value
        assert "..." not in value


def test_tp1_weeks_29_36_practice_follows_result_without_changing_fields() -> None:
    from pathlib import Path

    from calendar_pedagoga.content_engine_v2 import build_lesson_content_v2
    from calendar_pedagoga.docx_generation import (
        _practice_appearance_counts,
        _topic_cells_for_lesson,
        _topic_display_numbers,
    )
    from calendar_pedagoga.lesson_resolution import resolve_lesson_content
    from calendar_pedagoga.pipeline import _lesson_rows_from_v2
    from calendar_pedagoga.resolve_utp import resolve_utp
    from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload
    from tp1_fixed_content import tp1_number_bound_content_rows

    source = Path(__file__).resolve().parents[1] / "references" / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    if not source.is_file():
        pytest.skip("нет программы «Туристы-проводники» 1 г.")
    upload = validate_upload(UploadPurpose.PROGRAM, source.name, source.read_bytes())
    from calendar_pedagoga.resolve_utp import UtpResolutionError

    try:
        utp = resolve_utp(None, upload)
    except UtpResolutionError:
        pytest.skip("программа «Туристы-проводники» 1 г. без уникального встроенного УТП")
    rows = tuple(
        row for row in tp1_number_bound_content_rows() if row.week_number >= 29
    )
    generated = build_lesson_content_v2(rows)
    resolved = resolve_lesson_content(_lesson_rows_from_v2(generated))
    counts = _practice_appearance_counts(resolved)
    occurrences: dict = {}
    display_numbers = _topic_display_numbers(utp)
    tokens = {
        29: "мышц шеи",
        30: "скакалкой",
        31: "баскетбол",
        32: "выносливости",
        33: "быстроты",
        34: "силы",
        35: "гибкости",
        36: "гибкости",
    }
    seen_ofp: list[str] = []
    for lesson in resolved:
        week = lesson.source.source.week_number
        assert week in tokens
        _theory, practice = _topic_cells_for_lesson(
            lesson,
            display_numbers,
            topic_counts=counts,
            topic_occurrences=occurrences,
        )
        token = tokens[week]
        low = practice.casefold()
        assert practice.rstrip().endswith(")")
        assert "(" in practice
        assert token not in low
        assert token in lesson.planned_result.casefold(), (
            f"неделя {week}: в RESULT нет слота {token!r}\n"
            f"actual: {lesson.planned_result!r}"
        )
        assert "основная задача общей физической" not in low
        assert "роль и значение специальной" not in low
        assert "индивидуальный подход" not in low
        if week in {29, 30, 31}:
            seen_ofp.append(lesson.planned_result)
            assert "общая физическая подготовка" in low
        if week == 36:
            assert "продолжение" not in low
            assert "выносливости" not in low
            assert "выносливости" not in lesson.planned_result.casefold()
    fifth_sfp = [
        count for key, count in occurrences.items() if key[0] == "5.4"
    ]
    assert fifth_sfp == [5]
    joined_ofp = " ".join(seen_ofp).casefold()
    for fragment in (
        "рук и плечевого пояса",
        "мышц шеи",
        "туловища",
        "сопротивлением",
        "скакалкой",
        "акробатики",
        "эстафет",
        "легкая атлетика",
        "лыжный спорт",
        "гимнастические",
        "баскетбол",
        "плавание",
    ):
        assert fragment in joined_ofp


def test_schedule_cell_prints_title_and_hours_not_source() -> None:
    cell = format_schedule_topic_cell(
        "1",
        "Вводное занятие. Правила по ТБ",
        "1.5",
    )
    assert cell == "1. Вводное занятие. Правила по ТБ. (1.5)"
    assert "инструктаж" not in cell.casefold()


def test_schedule_cell_normalizes_double_period_and_nested_quotes() -> None:
    assert normalize_display_topic_title("«Украшения в технике «папье-маше»»") == (
        "«Украшения в технике папье-маше»"
    )
    assert normalize_display_topic_title("«Украшения в технике папье-маше") == (
        "«Украшения в технике папье-маше»"
    )
    assert normalize_display_topic_title("Тема.. продолжение.") == "Тема. продолжение."
    assert format_schedule_topic_cell(
        "7.",
        "«Украшения в технике «папье-маше»»",
        3,
    ) == "7. «Украшения в технике папье-маше». (3)"


def test_channel_cell_prints_quoted_work_names_instead_of_utp_title() -> None:
    assert brief_allocated_work_labels(
        ("«Аппликация из семян»", "«Аппликация из опила»")
    ) == ("«Аппликация из семян»", "«Аппликация из опила»")
    assert format_schedule_channel_cell(
        "2",
        "Аппликация. Настенные композиции.",
        3,
        ("«Аппликация из семян»", "«Аппликация из опила»"),
    ) == "«Аппликация из семян». «Аппликация из опила» (3)"
    assert format_schedule_channel_cell(
        "5",
        "Плетение.",
        3,
        ("«Изонить. Открытка»",),
    ) == "«Изонить. Открытка» (3)"


def test_channel_cell_normalizes_nested_work_quotes() -> None:
    assert format_schedule_channel_cell(
        "4",
        "Декоративные украшения.",
        3,
        ("«Украшения в технике «папье-маше»",),
    ) == "«Украшения в технике папье-маше» (3)"


def test_channel_cell_falls_back_to_utp_title_without_concrete_work() -> None:
    assert format_schedule_channel_cell(
        "1",
        "Вводное занятие. Правила по ТБ.",
        "1.5",
        (
            "Тема № 1 Вводное занятие",
            "Знакомство учащихся с направлением, планом работы",
            "Правила по технике безопасности при работе с острорежущими инструментами",
        ),
    ) == "1. Вводное занятие. Правила по ТБ. (1.5)"
    assert format_schedule_channel_cell(
        "5",
        "Плетение.",
        3,
        (),
    ) == "5. Плетение. (3)"
    assert format_schedule_channel_cell(
        "5",
        "Плетение.",
        2,
        ("«изонить»", "Тема № 5 Плетение", "История плетения"),
    ) == "5. Плетение. (2)"
    cell = format_schedule_channel_cell(
        "3",
        "Скульптурная композиция. Объёмные изделия.",
        3,
        ("Составление композиций из шишек, желудей, ракушек, яичной скорлупы, папье-маше",),
    )
    assert cell == "3. Скульптурная композиция. Объёмные изделия. (3)"
    assert "шишек" not in cell


def test_docx_topic_cells_use_allocated_work_labels() -> None:
    from types import SimpleNamespace

    from calendar_pedagoga import docx_generation as generation
    from calendar_pedagoga.content_generation import WeekTopicPart
    from calendar_pedagoga.matching import MatchStatus

    part = WeekTopicPart(
        topic_number="2",
        topic_title="Аппликация. Настенные композиции.",
        section="Аппликация",
        theory_hours=0,
        practice_hours=3,
        match_status=MatchStatus.EXACT,
        program_section="Аппликация",
        program_topic="Аппликация. Настенные композиции.",
        program_content_full="Практика. «Аппликация из семян». «Аппликация из опила».",
        weekly_content_assigned=True,
        practice_units=("«Аппликация из семян»", "«Аппликация из опила»"),
    )
    lesson = SimpleNamespace(
        source=SimpleNamespace(source=SimpleNamespace(week_parts=(part,))),
        planned_result="Выполняет аппликацию из семян и опила.",
    )
    key = generation._content_occurrence_key(part)
    theory, practice = generation._topic_cells_for_lesson(
        lesson,
        {generation._topic_part_key(part): "2"},
        topic_counts={key: 1},
        topic_occurrences={},
    )
    assert theory == ""
    assert practice == "«Аппликация из семян». «Аппликация из опила» (3)"
    assert "Настенные композиции" not in practice
