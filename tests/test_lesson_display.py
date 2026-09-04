from calendar_pedagoga.lesson_display import (
    brief_practice_summary,
    brief_theory_fragment,
    format_practice_cell,
    format_theory_cell,
    selected_practice_clause,
)


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
    from calendar_pedagoga.content_generation import build_content_model
    from calendar_pedagoga.docx_generation import (
        _practice_appearance_counts,
        _topic_cells_for_lesson,
        _topic_display_numbers,
    )
    from calendar_pedagoga.lesson_resolution import resolve_lesson_content
    from calendar_pedagoga.pipeline import _lesson_rows_from_v2
    from calendar_pedagoga.program_parsing import parse_program
    from calendar_pedagoga.resolve_utp import resolve_utp
    from calendar_pedagoga.scheduling import build_schedule
    from calendar_pedagoga.upload_validation import UploadPurpose, validate_upload
    from test_ce2_grounded_triad import CE2_TP1_WEEK_SNAPSHOT

    source = Path(__file__).resolve().parents[1] / "references" / "Программа ТУРИСТЫ-ПРОВОДНИКИ 1 г.docx"
    upload = validate_upload(UploadPurpose.PROGRAM, source.name, source.read_bytes())
    utp = resolve_utp(None, upload)
    program = parse_program(upload.content, upload.filename, study_year=1)
    rows = build_content_model(build_schedule(utp, "2026–2027"), utp, program, source.name)
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
        _theory, practice = _topic_cells_for_lesson(
            lesson,
            display_numbers,
            topic_counts=counts,
            topic_occurrences=occurrences,
        )
        snap = CE2_TP1_WEEK_SNAPSHOT[week - 1]
        assert lesson.lesson_type == snap[1]
        assert lesson.planned_result == snap[2]
        assert lesson.assessment_method == snap[3]
        if week not in tokens:
            continue
        token = tokens[week]
        low = practice.casefold()
        assert token in low
        assert token in lesson.planned_result.casefold()
        assert "основная задача общей физической" not in low
        assert "роль и значение специальной" not in low
        assert "индивидуальный подход" not in low
        if week in {29, 30, 31}:
            seen_ofp.append(practice)
        if week == 36:
            assert practice.startswith("Продолжение.")
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
