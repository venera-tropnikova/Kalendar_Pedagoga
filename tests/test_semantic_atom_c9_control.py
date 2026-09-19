"""C9: shadow CONTROL composed only from proven frames."""

from __future__ import annotations

import ast
import hashlib
from itertools import permutations
from pathlib import Path

from calendar_pedagoga.content_engine_v2 import derive_fields_v2
from calendar_pedagoga.content_generation import CalendarContentRow
from calendar_pedagoga.matching import MatchStatus
from calendar_pedagoga.pipeline import _build_pipeline_lesson_content
from calendar_pedagoga.semantic_atom import USE_SEMANTIC_ATOM_ENGINE
from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.control_adapter import (
    MISSING_BINDING,
    NEW_INVENTS,
    UNMAPPED_CONTROL,
    UNRESOLVED_FRAME,
    compose_control,
    project_control,
)
from calendar_pedagoga.semantic_atom.frame_adapter import (
    C5_REGISTRY,
    project_frames,
    reset_frame_adapter_calls,
)
from calendar_pedagoga.semantic_atom.models import (
    ControlKind,
    FrameKind,
    ObjectStatus,
    Provenance,
    SemanticFrame,
    SourceSpan,
    to_jsonable,
)
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
CONTROL_PATH = PRODUCTION_DIR / "semantic_atom" / "control_adapter.py"

W17 = (
    "Животные и птицы в рисунках детей. "
    "Запрещающие знаки «Берегите природу»."
)
W18 = (
    "Понятия: лес, поляна, луг, степь, болото. "
    "Тайны растений (для чего нужны корни, стебли, цветы, плоды). "
    "Деревья и кустарники: лиственные и хвойные. "
    "Курай – символ башкирского народа."
)


def test_c1_oracle_file_is_unchanged() -> None:
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_control_adapter_imports_production_one_way() -> None:
    tree = ast.parse(CONTROL_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "calendar_pedagoga.content_engine_v2" in imported or (
        "calendar_pedagoga" in imported
    )


def test_production_does_not_import_c9() -> None:
    reset_frame_adapter_calls()
    reset_shadow_invocation_count()
    needles = (
        "compose_control",
        "project_control",
        "control_adapter",
        "ShadowControlReport",
        "ControlPiece",
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
        theory_hours=2,
        practice_hours=0,
        total_hours=2,
        match_status=MatchStatus.EXACT,
        program_section="Раздел",
        program_topic="Тема",
        program_content_full="Основные сведения о крае.",
        program_content_preview="Основные сведения о крае.",
        source_program_name="Программа",
        source_utp_name="utp.docx",
        warnings=(),
    )
    _build_pipeline_lesson_content((row,), use_content_engine_v2=True)
    assert shadow_invocation_count() == 0


def _assert_identity(source: str, *, theory: bool = True) -> None:
    row = derive_fields_v2(
        topic_title="Тема",
        theory_text=source if theory else "",
        practice_text="" if theory else source,
        program_content=source,
        theory_hours=2 if theory else 0,
        practice_hours=0 if theory else 2,
    )
    shadow = run_passthrough_shadow(row, source=source)
    graph = project_passthrough_graph(row, source=source)
    projection = project_frames(source, row)
    report = compose_control(projection)
    assert compare_identity(row, shadow)[0].kind is DiffKind.EQUAL
    assert graph.projected_control == row.assessment_method
    assert projection.identity_control == row.assessment_method
    assert row.assessment_method == shadow.assessment_method
    assert report.composed_control is not None


def _proven(report) -> list:
    return [piece for piece in report.pieces if piece.status is ObjectStatus.PROVEN]


def _labels(report) -> frozenset[str]:
    return frozenset(piece.label.casefold() for piece in _proven(report))


def test_knowledge_maps_to_oral_survey() -> None:
    source = "Понятия: ритм, темп, динамика."
    _assert_identity(source)
    report = project_control(source)
    pieces = _proven(report)
    assert len(pieces) == 1
    assert pieces[0].kind is ControlKind.ORAL_SURVEY
    assert pieces[0].label.casefold().startswith("устный опрос")
    assert "ритм" in pieces[0].label.casefold()
    assert "по теме" not in pieces[0].label.casefold()


def test_definition_maps_to_oral_survey() -> None:
    source = "Флаг — символ государства."
    report = project_control(source)
    piece = _proven(report)[0]
    assert piece.kind is ControlKind.ORAL_SURVEY
    assert piece.label.casefold().startswith("устный опрос")
    assert "флаг" in piece.label.casefold()


def test_classification_maps_to_oral_survey() -> None:
    source = "Инструменты: духовые и ударные."
    report = project_control(source)
    piece = _proven(report)[0]
    assert piece.kind is ControlKind.ORAL_SURVEY
    assert "устный опрос" in piece.label.casefold()
    assert "духовые" in piece.label.casefold()


def test_creative_product_maps_to_review() -> None:
    source = "Изготовление кормушек."
    _assert_identity(source, theory=False)
    report = project_control(source)
    piece = _proven(report)[0]
    assert piece.kind is ControlKind.PRODUCT_REVIEW
    folded = piece.label.casefold()
    assert "просмотр" in folded and "оценк" in folded


def test_action_uses_observation_from_evidence() -> None:
    source = "Проведение подвижных игр."
    report = project_control(source)
    piece = _proven(report)[0]
    assert piece.kind is ControlKind.PEDAGOGICAL_OBSERVATION
    assert piece.label.casefold().startswith("педагогическое наблюдение")
    assert "по теме" not in piece.label.casefold()


def test_participation_excursion_exercise_making_use_existing_mappings() -> None:
    cases = (
        ("Участие в краеведческой викторине.", "участи"),
        ("Экскурсия в парк.", "экскурси"),
        ("Выполнение упражнений на бревне.", "упражнен"),
        ("Изготовление кормушек.", "просмотр"),
    )
    for source, needle in cases:
        report = project_control(source)
        piece = _proven(report)[0]
        assert needle in piece.label.casefold(), (source, piece.label)
        assert piece.status is ObjectStatus.PROVEN


def test_w17_drawings_and_sign_oral() -> None:
    _assert_identity(W17, theory=False)
    report = project_control(W17)
    pieces = _proven(report)
    assert len(pieces) == 2
    kinds = {piece.kind for piece in pieces}
    labels = " ".join(piece.label.casefold() for piece in pieces)
    assert ControlKind.PRODUCT_REVIEW in kinds
    assert ControlKind.ORAL_SURVEY in kinds
    assert "просмотр рисунков" in labels
    assert "устный опрос" in labels and "знак" in labels


def test_w18_stable_oral_survey_of_four_knowledge_frames() -> None:
    _assert_identity(W18)
    report = project_control(W18)
    pieces = _proven(report)
    assert len(pieces) == 4
    assert {piece.kind for piece in pieces} == {ControlKind.ORAL_SURVEY}
    composed = report.composed_control.casefold()
    assert composed.startswith("устный опрос")
    assert composed.count("устный опрос") == 1
    assert "понятия" in composed
    assert "функции" in composed
    assert "лиственные" in composed
    assert "кура" in composed
    assert "тайны" not in composed
    assert "растительный мир" not in composed


def test_mixed_action_and_knowledge_keep_both() -> None:
    source = "Рисуют флумберы у ручья. Что такое флумбер?"
    report = project_control(source)
    kinds = {piece.kind for piece in _proven(report)}
    assert ControlKind.PRODUCT_REVIEW in kinds or ControlKind.PEDAGOGICAL_OBSERVATION in kinds
    assert ControlKind.ORAL_SURVEY in kinds
    assert len(_proven(report)) == 2


def test_mixed_creative_and_knowledge_keep_both() -> None:
    source = "Изготовление кормушек. Понятия: ритм, темп, динамика."
    report = project_control(source)
    kinds = {piece.kind for piece in _proven(report)}
    assert ControlKind.PRODUCT_REVIEW in kinds
    assert ControlKind.ORAL_SURVEY in kinds


def test_unresolved_frame_does_not_create_proven_control() -> None:
    source = "Флумбер."
    report = project_control(source)
    assert _proven(report) == []
    assert report.composed_control == ""
    assert any(piece.reason == UNRESOLVED_FRAME for piece in report.pieces)


def test_proven_frame_without_mapping_is_explicitly_unresolved() -> None:
    projection = project_frames("Что такое флумбер?")
    frame = projection.frames[0]
    blank = SemanticFrame(
        id=frame.id,
        span=frame.span,
        source_fingerprint=frame.source_fingerprint,
        provenance=frame.provenance,
        status=ObjectStatus.PROVEN,
        kind=FrameKind.PROJECTED,
        atom_id=frame.atom_id,
        clause_id=frame.clause_id,
        projected_type="",
        projected_result="",
        projected_control="",
        coverage_status="COVERED",
        predicate="",
        object="",
        complement="",
        reason="",
    )
    report = compose_control(
        projection.__class__(
            source=projection.source,
            atoms=projection.atoms,
            frames=(blank,),
            bindings=projection.bindings,
            candidate_result="",
        )
    )
    assert _proven(report) == []
    assert report.uncovered_frame_ids == (blank.id,)
    assert report.pieces[0].reason == UNMAPPED_CONTROL


def test_duplicate_pieces_are_deduplicated() -> None:
    source = "Понятия: ритм, темп, динамика. Понятия: ритм, темп, динамика."
    report = project_control(source)
    assert len(_proven(report)) == 1


def test_permutation_does_not_change_piece_set() -> None:
    parts = (
        "Понятия: ритм, темп, динамика.",
        "Изготовление кормушек.",
        "Что такое флумбер?",
    )
    sets = []
    for order in permutations(parts):
        sets.append(_labels(project_control(" ".join(order))))
    assert len(set(sets)) == 1


def test_every_proven_piece_has_binding() -> None:
    report = project_control(W17)
    by_piece = {binding.piece_id: binding for binding in report.bindings}
    for piece in _proven(report):
        binding = by_piece[piece.id]
        assert binding.frame_id == piece.frame_id
        assert binding.atom_id == piece.atom_id
        assert binding.span.start == piece.span.start
        assert binding.span.end == piece.span.end


def test_missing_binding_is_forbidden() -> None:
    projection = project_frames("Понятия: ритм, темп, динамика.")
    frame = projection.frames[0]
    empty_span = SourceSpan(
        id="span:unbound",
        start=0,
        end=0,
        source_fingerprint=frame.source_fingerprint,
        provenance=Provenance(adapter="test", role="unbound"),
        status=ObjectStatus.UNASSESSED,
    )
    unbound = SemanticFrame(
        id=frame.id,
        span=empty_span,
        source_fingerprint=frame.source_fingerprint,
        provenance=frame.provenance,
        status=ObjectStatus.PROVEN,
        kind=frame.kind,
        atom_id="",
        clause_id=frame.clause_id,
        projected_type="",
        projected_result=frame.projected_result,
        projected_control=frame.projected_control,
        coverage_status="COVERED",
        predicate=frame.predicate,
        object=frame.object,
        complement=frame.complement,
    )
    report = compose_control(
        projection.__class__(
            source=projection.source,
            atoms=projection.atoms,
            frames=(unbound,),
            bindings=(),
            candidate_result=frame.projected_result,
        )
    )
    assert _proven(report) == []
    assert report.pieces[0].reason == MISSING_BINDING


def test_lexical_violation_is_new_invents(monkeypatch) -> None:
    from calendar_pedagoga.semantic_atom import control_adapter as adapter

    def fake_knowledge(result: str) -> str:
        return "устный опрос: квантовый ритм"

    projection = project_frames("Понятия: ритм, темп, динамика.")
    monkeypatch.setattr(adapter._ce2, "_declared_knowledge_control", fake_knowledge)
    report = compose_control(projection)
    assert _proven(report) == []
    assert report.pieces[0].reason == NEW_INVENTS
    assert report.composed_control == ""


def test_finite_result_is_not_cited_as_control() -> None:
    for source in (
        "Рисуют флумберы у ручья.",
        "Что такое флумбер?",
        "Изготовление кормушек.",
    ):
        frame = project_frames(source).frames[0]
        report = project_control(source)
        for piece in _proven(report):
            assert piece.label.casefold() != frame.projected_result.casefold()
            verb = frame.projected_result.split()[0].casefold().rstrip(".")
            assert not piece.label.casefold().startswith(verb)


def test_title_only_and_empty_source_have_no_proven_control() -> None:
    assert _proven(project_control("Растительный мир Башкортостана.")) == []
    assert _proven(project_control("")) == []
    assert project_control("").composed_control == ""


def test_helper_exception_does_not_break_other_pieces(monkeypatch) -> None:
    from calendar_pedagoga.semantic_atom import control_adapter as adapter

    def boom(result: str) -> str:
        raise RuntimeError("helper failed")

    projection = project_frames(W17)
    monkeypatch.setattr(adapter._ce2, "_declared_knowledge_control", boom)
    report = compose_control(projection)
    labels = " ".join(piece.label.casefold() for piece in _proven(report))
    assert "просмотр рисунков" in labels
    assert any(piece.kind is ControlKind.PRODUCT_REVIEW for piece in _proven(report))


def test_c5_c8_regression_and_identity() -> None:
    locative = project_frames("Животные и птицы в рисунках детей.")
    assert locative.frames[0].status is ObjectStatus.PROVEN
    concepts = project_frames("Понятия: ритм, темп, динамика.")
    assert concepts.frames[0].kind is FrameKind.KNOWLEDGE
    action = project_frames("Рисуют деревья у ручья.")
    assert action.frames[0].status is ObjectStatus.PROVEN
    question = project_frames("Что такое флумбер?")
    assert question.frames[0].kind is FrameKind.KNOWLEDGE
    c5_only = project_frames(
        "Что такое краеведение?",
        dispatcher=__import__(
            "calendar_pedagoga.semantic_atom.dispatcher",
            fromlist=["SemanticFrameDispatcher"],
        ).SemanticFrameDispatcher(C5_REGISTRY),
    )
    assert c5_only.frames[0].status is ObjectStatus.UNRESOLVED
    _assert_identity("Понятия: ритм, темп, динамика.")


def test_report_roundtrip() -> None:
    report = project_control(W17)
    payload = to_jsonable(report)
    restored = report.__class__.from_dict(payload)
    assert to_jsonable(restored) == payload


def test_generic_question_oral_uses_source_object() -> None:
    report = project_control("Что такое флумбер?")
    piece = _proven(report)[0]
    assert piece.kind is ControlKind.ORAL_SURVEY
    assert "флумбер" in piece.label.casefold()
    assert "по теме" not in piece.label.casefold()
