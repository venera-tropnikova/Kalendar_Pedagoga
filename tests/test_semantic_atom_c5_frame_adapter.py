"""C5: shadow FrameAdapter for accepted be1b745 / 7a714d6 builders only."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import _build_pipeline_lesson_content
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.atom_adapter import atomize
from calendar_pedagoga.semantic_atom.frame_adapter import (
    LEXICAL_VIOLATION,
    UNSUPPORTED_ATOM_SHAPE,
    frame_adapter_calls,
    project_frames,
    reset_frame_adapter_calls,
)
from calendar_pedagoga.semantic_atom.models import FrameKind, ObjectStatus
from calendar_pedagoga.semantic_atom.passthrough import (
    DiffKind,
    compare_identity,
    reset_shadow_invocation_count,
    run_passthrough_shadow,
    shadow_invocation_count,
)

ROOT = Path(__file__).resolve().parents[1]
C1_ORACLE = ROOT / "tests" / "oracles" / "semantic_atom_7a714d6.json"
C1_ORACLE_SHA256 = "432ff7b694dc2e235c5446769f4779c4b7f9498b9ebda3a57a931c9c7e059344"
PRODUCTION_DIR = ROOT / "src" / "calendar_pedagoga"
FRAME_PATH = PRODUCTION_DIR / "semantic_atom" / "frame_adapter.py"
MODELS_PATH = PRODUCTION_DIR / "semantic_atom" / "models.py"


def test_c1_oracle_file_is_unchanged() -> None:
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_models_still_avoid_production_imports() -> None:
    tree = ast.parse(MODELS_PATH.read_text(encoding="utf-8"))
    allowed = {
        "calendar_pedagoga.semantic_atom.canonicalize",
        "calendar_pedagoga.semantic_atom.models",
    }
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.startswith("calendar_pedagoga") and name not in allowed:
                raise AssertionError(f"models imported {name}")


def test_frame_adapter_imports_production_one_way() -> None:
    tree = ast.parse(FRAME_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "calendar_pedagoga" in imported or (
        "calendar_pedagoga.content_engine_v2" in imported
    )
    assert "calendar_pedagoga.semantic_atom.atom_adapter" in imported


def test_production_does_not_import_or_call_frame_adapter() -> None:
    reset_frame_adapter_calls()
    reset_shadow_invocation_count()
    needles = (
        "project_frames",
        "FrameAdapter",
        "frame_adapter",
        "FrameProjection",
    )
    for path in PRODUCTION_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            assert needle not in text, f"{path.name} contains {needle}"
    row = CalendarContentRow(
        week_number=1,
        date_range="01–07.09",
        month="Сентябрь",
        section="Раздел",
        topic_number="1.1",
        topic_title="Тема",
        source_topic_title="Тема",
        theory_hours=0,
        practice_hours=2,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic="Тема",
        program_content_full="Рисование деревьев.",
        program_content_preview="Рисование деревьев.",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    _build_pipeline_lesson_content((row,), use_content_engine_v2=True)
    assert frame_adapter_calls() == 0
    assert shadow_invocation_count() == 0


def _theory(text: str, topic: str = "Теоретическая тема"):
    return derive_fields_v2(
        topic_title=topic,
        theory_text=text,
        practice_text="",
        program_content=text,
        theory_hours=2,
        practice_hours=0,
    )


def _practice(text: str, topic: str = "Тема"):
    return derive_fields_v2(
        topic_title=topic,
        theory_text="",
        practice_text=text,
        program_content=text,
        theory_hours=0,
        practice_hours=2,
    )


def _assert_identity_equal(row, source: str) -> None:
    shadow = run_passthrough_shadow(row, source=source)
    report = project_passthrough_graph(row, source=source)
    assert compare_identity(row, shadow)[0].kind is DiffKind.EQUAL
    assert report.projected_result == row.planned_result
    assert report.projected_control == row.assessment_method
    assert report.projected_type == row.lesson_type
    assert row.planned_result == shadow.planned_result


def _assert_span_coverage(projection) -> None:
    by_id = {atom.id: atom for atom in projection.atoms}
    for frame, binding in zip(projection.frames, projection.bindings):
        atom = by_id[frame.atom_id]
        assert frame.span.start == atom.span.start
        assert frame.span.end == atom.span.end
        assert binding.span.start == atom.span.start
        assert binding.span.end == atom.span.end
        assert binding.atom_id == atom.id
        assert binding.frame_id == frame.id


def test_locative_drawings_shadow_frame() -> None:
    source = "Животные и птицы в рисунках детей."
    row = _practice(source)
    _assert_identity_equal(row, source)
    projection = project_frames(source, row)
    assert len(projection.frames) == 1
    frame = projection.frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.kind is FrameKind.ACTION
    assert frame.projected_result.startswith("Рисует животных и птиц")
    assert "создаёт" not in frame.projected_result.casefold()
    assert "просмотр рисунков" in frame.projected_control.casefold()
    _assert_span_coverage(projection)


def test_locative_pupils_and_students_aliases() -> None:
    for source in ("Реки в рисунках учащихся.", "Горы в рисунках учеников."):
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN
        assert frame.projected_result.casefold().startswith("рисует")


def test_semiotic_sign_symbol_emblem_pictogram() -> None:
    sources = (
        "Запрещающие знаки «Берегите природу».",
        "Условные символы карты.",
        "Эмблема отряда.",
        "Пиктограммы безопасности.",
    )
    for source in sources:
        row = _practice(source)
        _assert_identity_equal(row, source)
        frame = project_frames(source, row).frames[0]
        low = frame.projected_result.casefold()
        assert frame.status is ObjectStatus.PROVEN
        assert low.startswith("распознаёт")
        assert "объясняет" in low and "значение" in low
        assert "изготавливает" not in low
        assert "создаёт" not in low


def test_concept_colon_list_shadow_frame() -> None:
    source = "Понятия: ритм, темп, динамика."
    row = _theory(source)
    _assert_identity_equal(row, source)
    frame = project_frames(source, row).frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.projected_result == (
        "Объясняет значения понятий «ритм», «темп», «динамика»."
    )
    assert "понятия «ритм», «темп», «динамика»" in frame.projected_control.casefold()


def test_purpose_standalone_and_parenthetical() -> None:
    standalone = "Для чего нужны карта, компас и фонарь?"
    wrapped = "Загадки прибора (для чего нужны шкала, стрелка, корпус)."
    for source in (standalone, wrapped):
        row = _theory(source)
        _assert_identity_equal(row, source)
        frame = project_frames(source, row).frames[0]
        assert frame.status is ObjectStatus.PROVEN
        assert frame.projected_result.startswith("Объясняет функции ")
        assert "тайны" not in frame.projected_result.casefold()
        assert "загадк" not in frame.projected_result.casefold()


def test_closed_classification_and_dash_symbol() -> None:
    classification = "Инструменты: духовые и ударные."
    symbol = "Флаг — символ государства."
    class_row = _theory(classification)
    symbol_row = _theory(symbol)
    _assert_identity_equal(class_row, classification)
    _assert_identity_equal(symbol_row, symbol)
    class_frame = project_frames(classification, class_row).frames[0]
    symbol_frame = project_frames(symbol, symbol_row).frames[0]
    assert class_frame.status is ObjectStatus.PROVEN
    assert class_frame.kind is FrameKind.CLASSIFICATION
    assert class_frame.projected_result == "Различает духовые и ударные инструменты."
    assert symbol_frame.status is ObjectStatus.PROVEN
    assert symbol_frame.kind is FrameKind.DEFINITION
    assert symbol_frame.projected_result == "Называет флаг символом государства."


def test_key_w17_two_eligible_atoms_stay_independent() -> None:
    source = (
        "Животные и птицы в рисунках детей. "
        "Запрещающие знаки «Берегите природу»."
    )
    row = _practice(source, topic="Животный мир Башкортостана")
    _assert_identity_equal(row, source)
    projection = project_frames(source, row)
    assert [frame.status for frame in projection.frames] == [
        ObjectStatus.PROVEN,
        ObjectStatus.PROVEN,
    ]
    assert projection.frames[0].projected_result.startswith("Рисует животных и птиц")
    assert projection.frames[1].projected_result.casefold().startswith("распознаёт")
    assert projection.candidate_result.startswith("Рисует животных и птиц")
    assert "Распознаёт запрещающие знаки" in projection.candidate_result
    assert row.planned_result == (
        "Рисует животных и птиц. Распознаёт запрещающие знаки «Берегите природу» "
        "и объясняет их значение."
    )
    _assert_span_coverage(projection)


def test_key_w18_four_knowledge_builders() -> None:
    source = (
        "Понятия: лес, поляна, луг, степь, болото. "
        "Тайны растений (для чего нужны корни, стебли, цветы, плоды). "
        "Деревья и кустарники: лиственные и хвойные. "
        "Курай – символ башкирского народа."
    )
    row = _theory(source, topic="Растительный мир Башкортостана")
    _assert_identity_equal(row, source)
    projection = project_frames(source, row)
    assert len(projection.frames) == 4
    assert all(frame.status is ObjectStatus.PROVEN for frame in projection.frames)
    assert projection.frames[0].projected_result.startswith("Объясняет значения понятий")
    assert projection.frames[1].projected_result.startswith("Объясняет функции")
    assert projection.frames[2].projected_result.startswith("Различает")
    assert projection.frames[3].projected_result.startswith("Называет курай")
    assert "тайны" not in projection.candidate_result.casefold()
    _assert_span_coverage(projection)


def test_what_is_clause_stays_unresolved_in_c5() -> None:
    source = (
        "Что такое краеведение? "
        "Понятия: маршрут, ориентир. "
        "Для чего нужны карта и компас?"
    )
    row = _theory(source)
    _assert_identity_equal(row, source)
    projection = project_frames(source, row)
    assert [frame.status for frame in projection.frames] == [
        ObjectStatus.UNRESOLVED,
        ObjectStatus.PROVEN,
        ObjectStatus.PROVEN,
    ]
    assert projection.frames[0].reason == UNSUPPORTED_ATOM_SHAPE
    assert "что такое" not in projection.candidate_result.casefold()


def test_permutation_and_neighbor_do_not_change_builder() -> None:
    locative = "Животные и птицы в рисунках детей."
    concept = "Понятия: ритм, темп, динамика."
    leftover = "Памятники города."
    lone = project_frames(locative).frames[0]
    mixed = project_frames(f"{leftover} {locative} {concept}")
    swapped = project_frames(f"{concept} {locative} {leftover}")
    proven = [
        frame
        for frame in (*mixed.frames, *swapped.frames)
        if frame.status is ObjectStatus.PROVEN
    ]
    locative_hits = [
        frame
        for frame in proven
        if frame.projected_result.startswith("Рисует животных и птиц")
    ]
    concept_hits = [
        frame
        for frame in proven
        if frame.projected_result.startswith("Объясняет значения понятий")
    ]
    assert lone.status is ObjectStatus.PROVEN
    assert len(locative_hits) == 2
    assert len(concept_hits) == 2
    assert all(
        frame.projected_result == lone.projected_result for frame in locative_hits
    )
    assert any(frame.reason == UNSUPPORTED_ATOM_SHAPE for frame in mixed.frames)
    assert any(frame.reason == UNSUPPORTED_ATOM_SHAPE for frame in swapped.frames)


def test_negative_shapes_stay_unresolved() -> None:
    sources = (
        "Игры: «Давай поговорим».",
        "Правила поведения на природе.",
        "Памятники города.",
        "Инструменты: духовые, ударные, струнные.",
        "Инструменты: духовые и ударные и другие.",
        "Изготовление запрещающих знаков.",
        "Рисование листьев.",
        "Рисуют деревья.",
    )
    for source in sources:
        projection = project_frames(source)
        assert projection.frames, source
        assert all(
            frame.status is ObjectStatus.UNRESOLVED for frame in projection.frames
        ), source
        assert all(frame.reason == UNSUPPORTED_ATOM_SHAPE for frame in projection.frames)


def test_lexical_violations_force_unresolved(monkeypatch) -> None:
    from calendar_pedagoga.semantic_atom import frame_adapter as adapter

    def fake_locative(text: str):
        return "рисует животных и квантовый лес", "рисование", "животных и птиц", ""

    monkeypatch.setattr(adapter._ce2, "_locative_drawing_result", fake_locative)
    projection = project_frames("Животные и птицы в рисунках детей.")
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.frames[0].reason == LEXICAL_VIOLATION
    assert projection.frames[0].projected_result == ""


def test_unknown_atoms_are_never_false_proven() -> None:
    source = "День защиты детей. Основные сведения о крае."
    projection = project_frames(source)
    assert projection.frames
    assert all(frame.status is ObjectStatus.UNRESOLVED for frame in projection.frames)
    assert all(frame.status is not ObjectStatus.PROVEN for frame in projection.frames)
    assert projection.candidate_result == ""


def test_candidate_shadow_does_not_rewrite_old_result() -> None:
    source = "Понятия: ритм, темп, динамика."
    row = _theory(source)
    before = row.planned_result
    projection = project_frames(source, row)
    assert row.planned_result == before
    assert projection.identity_result == before
    assert projection.identity_control == row.assessment_method
    assert projection.identity_type == row.lesson_type
    assert projection.candidate_result
    atoms = atomize(source)
    assert [atom.id for atom in projection.atoms] == [atom.id for atom in atoms.atoms]
