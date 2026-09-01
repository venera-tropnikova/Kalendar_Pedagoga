from calendar_pedagoga.lesson_display import (
    brief_practice_summary,
    brief_theory_fragment,
    format_practice_cell,
    format_theory_cell,
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


def test_practice_does_not_duplicate_theory_title() -> None:
    theory = "1.3. Личное и групповое туристское снаряжение (1)"
    content = (
        "Понятие о личном и групповом снаряжении.\n"
        "Практические занятия. Укладка рюкзаков."
    )
    practice = format_practice_cell("1.3", "Личное и групповое туристское снаряжение", content, 1)
    assert practice != theory
    assert "Укладка рюкзаков" in practice


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
