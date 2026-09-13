# -*- coding: utf-8 -*-
"""Stray leading ':' after a practice marker must not stay in SOURCE."""

from calendar_pedagoga.lesson_content import (
    _split_explicit_practice,
    _strip_leading_item_colon,
)
from calendar_pedagoga.lesson_display import brief_practice_summary


def test_strip_leading_item_colon_positive() -> None:
    assert (
        _strip_leading_item_colon(": формирование походной медицинской аптечки.")
        == "Формирование походной медицинской аптечки."
    )
    assert _strip_leading_item_colon(":  текст") == "Текст"


def test_strip_leading_item_colon_negative_keeps_internal() -> None:
    source = "Назначение и дозировка препаратов: ампулы, таблетки, порошки."
    assert _strip_leading_item_colon(source) == source
    assert ":" in _strip_leading_item_colon(source)


def test_explicit_practice_marker_with_colon_does_not_leak() -> None:
    text = (
        "Составление медицинской аптечки. Хранение и транспортировка аптечки.\n"
        "Практические занятия: формирование походной медицинской аптечки."
    )
    split = _split_explicit_practice(text)
    assert split is not None
    theory, practice = split
    assert "Составление медицинской аптечки" in theory
    assert not practice.startswith(":")
    assert practice.startswith("Формирование походной медицинской аптечки")
    assert ":" not in practice


def test_explicit_practice_period_marker_unchanged() -> None:
    text = "Теория темы.\nПрактические занятия. Укладка рюкзаков, подгонка снаряжения."
    split = _split_explicit_practice(text)
    assert split is not None
    _theory, practice = split
    assert practice.startswith("Укладка рюкзаков")


def test_brief_practice_summary_strips_marker_colon() -> None:
    content = (
        "Составление медицинской аптечки.\n"
        "Практические занятия: формирование походной медицинской аптечки."
    )
    practice, kind = brief_practice_summary(content)
    assert kind == "block"
    assert not practice.startswith(":")
    assert practice.casefold().startswith("формирование походной медицинской аптечки")
