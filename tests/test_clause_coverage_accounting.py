from calendar_pedagoga.content_engine_v2 import derive_fields_v2, _nonempty_result_in


def derive(source):
    return derive_fields_v2(
        topic_title="Учебная тема", theory_text="", practice_text=source,
        practice_hours=2,
    )


def test_empty_result_is_never_evidence():
    for empty in ("", " ", ".", " . "):
        assert not _nonempty_result_in(empty, "Измеряет длину.")


def test_each_clause_has_status_and_empty_local_result_is_not_lost():
    result = derive("Измерение длины. Знакомство с краеведческими объектами г.Салавата и Башкортостана.")
    assert result.clause_coverage
    assert all(status in {"COVERED", "NEEDS_REVIEW"} for _, status in result.clause_coverage)
    unknown = [(clause, status) for clause, status in result.clause_coverage if "Знакомство" in clause]
    assert unknown and unknown[0][1] == "NEEDS_REVIEW"
    assert any("Знакомство" in warning for warning in result.warnings)
    assert "Измеряет длину" in result.planned_result


def test_retained_contextual_complement_not_reviewed_again():
    source = "Укладка рюкзаков, подгонка снаряжения. Работа со снаряжением, уход за ним и ремонт."
    result = derive(source)
    assert "ухаживает" in result.planned_result and "ремонтирует" in result.planned_result
    assert result.clause_coverage == (
        ("Укладка рюкзаков, подгонка снаряжения", "COVERED"),
        ("Работа со снаряжением, уход за ним и ремонт", "COVERED"),
    )
    assert not any("NEEDS_REVIEW" in warning for warning in result.warnings)


def test_same_object_does_not_cover_another_action():
    result = derive("Изготовление кормушки. Развешивание кормушки.")
    states = dict(result.clause_coverage)
    assert states["Изготовление кормушки"] == "COVERED"
    assert states["Развешивание кормушки"] == "NEEDS_REVIEW"
    assert any("Развешивание" in warning for warning in result.warnings)


def test_bare_list_single_clause_not_covered():
    result = derive("Альфа, бета, гамма.")
    assert result.clause_coverage == (("Альфа, бета, гамма", "NEEDS_REVIEW"),)


def test_successful_independent_operations_are_covered():
    result = derive("Выполнение разминки. Измерение пульса.")
    assert len(result.clause_coverage) == 2
    assert all(status == "COVERED" for _, status in result.clause_coverage)
