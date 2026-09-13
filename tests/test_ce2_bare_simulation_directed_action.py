# -*- coding: utf-8 -*-
"""Bare simulation / directed-action NPs must not become pseudo-RESULT."""

from calendar_pedagoga.content_engine_v2 import (
    _bare_directed_actions_np,
    _bare_simulation_setting,
    _is_finite_result_phrase,
    derive_fields_v2,
    transform_clause_to_result,
)


def test_bare_simulation_is_not_standalone_result() -> None:
    phrase, _frame = transform_clause_to_result(
        "имитация ситуации потери ориентировки",
        theory_only=False,
        full_source="имитация ситуации потери ориентировки",
    )
    assert not phrase or phrase.strip(" .") == ""
    assert not _is_finite_result_phrase(phrase or "")


def test_bare_directed_actions_become_finite_perform() -> None:
    phrase, _frame = transform_clause_to_result(
        "действия по восстановлению местонахождения",
        theory_only=False,
        full_source="действия по восстановлению местонахождения",
    )
    low = phrase.casefold()
    assert low.startswith("выполняет действия по")
    assert "восстановлен" in low
    assert "местонахожд" in low


def test_simulation_attaches_as_circumstance_not_object() -> None:
    source = (
        "Определение точки стояния на спортивной карте, "
        "имитация ситуации потери ориентировки, "
        "действия по восстановлению местонахождения"
    )
    phrase, _frame = transform_clause_to_result(
        source, theory_only=False, full_source=source
    )
    low = phrase.casefold()
    assert "определяет" in low and "точк" in low and "карт" in low
    assert "выполняет действия по восстановлению местонахождения" in low
    assert "при имитации" in low
    assert not low.strip().startswith("имитац")
    # Two finite actions stay separate sentences, not one glued object list.
    assert "определяет" in low.split(".")[0]
    assert any(part.strip().startswith("выполняет") for part in low.split(".")[1:])


def test_negative_bare_nominals_not_glued_as_prior_object() -> None:
    """Negative: do not treat imitation/actions as complements of определяет."""

    assert _bare_simulation_setting("имитация ситуации потери ориентировки")
    assert _bare_directed_actions_np("действия по восстановлению местонахождения")
    source = (
        "Определение точки стояния на спортивной карте, "
        "имитация ситуации потери ориентировки, "
        "действия по восстановлению местонахождения"
    )
    phrase, _frame = transform_clause_to_result(
        source, theory_only=False, full_source=source
    )
    # Must not read as «определяет … имитацию …» / bare «Имитация ….» RESULT.
    assert "определяет имитац" not in phrase.casefold()
    assert "имитация ситуации" not in phrase.casefold()
    assert not any(
        part.strip().startswith("имитац") for part in phrase.casefold().split(".")
    )


def test_week_keeps_map_point_and_recovery_without_dupes() -> None:
    practice = (
        "Упражнения по определению азимута движения по тени от солнца, "
        "определение азимута в разное время дня. "
        "Упражнения по определению сторон горизонта по местным предметам, "
        "по Солнцу, Луне, Полярной звезде. "
        "Определение точки стояния на спортивной карте, "
        "имитация ситуации потери ориентировки, "
        "действия по восстановлению местонахождения."
    )
    derived = derive_fields_v2(
        topic_title=(
            "Ориентирование по местным приметам. "
            "Действия в случае потери ориентировки"
        ),
        theory_text="Ориентирование по местным приметам.",
        practice_text=practice,
        program_content=practice,
        theory_hours=1,
        practice_hours=1,
    )
    result = derived.planned_result
    control = derived.assessment_method
    low = result.casefold()
    assert "азимут" in low
    assert "сторон" in low and "горизонт" in low
    assert "точк" in low and "карт" in low
    assert "выполняет действия по восстановлению местонахождения" in low
    assert "при имитации" in low
    # Week fold (build path) collapses a repeated recovery sentence; derive may
    # still list it once per covered clause before fold.
    from calendar_pedagoga.content_engine_v2 import _fold_week_result

    folded = _fold_week_result(result).casefold()
    assert folded.count("выполняет действия по восстановлению местонахождения") == 1
    assert "имитация ситуации" not in folded
    assert "восстановлен" in control.casefold() or "местонахожд" in control.casefold()
    assert "точк" in control.casefold() or "азимут" in control.casefold()
    assert "«" not in control or ". " not in control.split("«", 1)[-1].split("»", 1)[0]
