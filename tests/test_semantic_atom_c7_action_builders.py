"""C7: explicit action builders on the shadow dispatcher."""

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
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY, MISSING_OBJECT
from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    SemanticFrameDispatcher,
    UNSUPPORTED_ATOM_SHAPE,
    RegisteredBuilder,
)
from calendar_pedagoga.semantic_atom.frame_adapter import (
    C5_REGISTRY,
    full_dispatcher,
    project_frames,
    reset_frame_adapter_calls,
)
from calendar_pedagoga.semantic_atom.models import (
    CandidateConfidence,
    FrameCandidate,
    FrameKind,
    LexicalCheckResult,
    ObjectStatus,
    SourceAtom,
    StructuralEvidence,
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
ACTION_PATH = PRODUCTION_DIR / "semantic_atom" / "action_builders.py"


def test_c1_oracle_file_is_unchanged() -> None:
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_action_builders_import_production_one_way() -> None:
    tree = ast.parse(ACTION_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "calendar_pedagoga" in imported or (
        "calendar_pedagoga.content_engine_v2" in imported
    )
    assert "calendar_pedagoga.semantic_atom.frame_adapter" in imported


def test_production_does_not_import_action_builders() -> None:
    reset_frame_adapter_calls()
    reset_shadow_invocation_count()
    needles = ("ACTION_REGISTRY", "action_builders", "full_dispatcher")
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


def _assert_identity(source: str, *, theory: bool = False) -> None:
    row = derive_fields_v2(
        topic_title="Тема",
        theory_text=source if theory else "",
        practice_text="" if theory else source,
        program_content=source,
        theory_hours=2 if theory else 0,
        practice_hours=0 if theory else 2,
    )
    shadow = run_passthrough_shadow(row, source=source)
    report = project_passthrough_graph(row, source=source)
    projection = project_frames(source, row)
    assert compare_identity(row, shadow)[0].kind is DiffKind.EQUAL
    assert report.projected_result == row.planned_result
    assert projection.identity_result == row.planned_result
    assert row.planned_result == shadow.planned_result


def test_finite_and_deverbal_unseen_objects() -> None:
    finite = "Рисуют деревья у ручья."
    deverbal = "Составление карты местности."
    making = "Изготовление кормушек."
    for source, prefix in (
        (finite, "рисует"),
        (deverbal, "составляет"),
        (making, "изготавливает"),
    ):
        _assert_identity(source)
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, source
        assert frame.projected_result.casefold().startswith(prefix), frame.projected_result
        assert frame.kind in {FrameKind.ACTION, FrameKind.CREATIVE_PRODUCT}


def test_conduct_perform_create_design() -> None:
    cases = (
        ("Проведение подвижных игр.", "проводит"),
        ("Выполнение упражнений на бревне.", "выполняет"),
        ("Создание макета родного края.", "создаёт"),
        ("Разработка маршрутного листа.", "разрабатывает"),
    )
    for source, prefix in cases:
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, source
        assert prefix in frame.projected_result.casefold(), frame.projected_result


def test_participation_visit_excursion() -> None:
    visit = "Участие в краеведческой викторине."
    excursion = "Экскурсия в парк."
    games = "Подвижные игры на поляне."
    visit_frame = project_frames(visit).frames[0]
    excursion_frame = project_frames(excursion).frames[0]
    games_frame = project_frames(games).frames[0]
    assert visit_frame.status is ObjectStatus.PROVEN
    assert visit_frame.kind is FrameKind.ACTION
    assert "участвует" in visit_frame.projected_result.casefold() or "викторин" in visit_frame.projected_result.casefold()
    assert excursion_frame.status is ObjectStatus.PROVEN
    assert "экскурси" in excursion_frame.projected_result.casefold()
    assert games_frame.status is ObjectStatus.PROVEN
    assert games_frame.kind is FrameKind.ACTION


def test_several_independent_actions_and_permutation() -> None:
    parts = (
        "Изготовление кормушек.",
        "Проведение подвижных игр.",
        "Составление карты местности.",
    )
    texts = []
    for order in permutations(parts):
        source = " ".join(order)
        projection = project_frames(source)
        assert [frame.status for frame in projection.frames] == [
            ObjectStatus.PROVEN
        ] * 3
        texts.append(frozenset(frame.projected_result for frame in projection.frames))
    assert len(set(texts)) == 1


def test_questions_and_knowledge_np_stay_unresolved() -> None:
    sources = (
        "Как складывают оригами?",
        "Почему желтеют хвощи?",
        "Что такое гербарий?",
        "Основные сведения о крае.",
        "Правила поведения на природе.",
        "Памятники города.",
        "Игры: «Давай поговорим».",
    )
    for source in sources:
        projection = project_frames(source)
        assert projection.frames, source
        assert all(
            frame.status is ObjectStatus.UNRESOLVED for frame in projection.frames
        ), (source, [frame.projected_result for frame in projection.frames])


def test_missing_object_is_not_guessed() -> None:
    for source in ("Рисование.", "Изготовление.", "Рисуют.", "Посещение."):
        projection = project_frames(source)
        assert projection.frames[0].status is ObjectStatus.UNRESOLVED, source
        reasons = {
            item.rejection_reason
            for item in projection.candidates
            if item.builder_id in {item.builder_id for item in ACTION_REGISTRY}
        }
        assert MISSING_OBJECT in reasons or projection.frames[0].reason in {
            UNSUPPORTED_ATOM_SHAPE,
            MISSING_OBJECT,
        }


def test_c5_locative_not_stolen_and_action_conflict_is_unresolved() -> None:
    locative = "Животные и птицы в рисунках детей."
    locative_frame = project_frames(locative).frames[0]
    assert locative_frame.status is ObjectStatus.PROVEN
    assert locative_frame.kind is FrameKind.ACTION
    assert locative_frame.projected_result.startswith("Рисует животных и птиц")

    atom = locative_frame
    conflicting = FrameCandidate(
        builder_id="fake_conflict",
        atom_id=atom.atom_id if hasattr(atom, "atom_id") else project_frames(locative).atoms[0].id,
        span=project_frames(locative).atoms[0].span,
        source_fingerprint=project_frames(locative).atoms[0].source_fingerprint,
        proposed_kind=FrameKind.KNOWLEDGE,
        proposed_predicate="называет",
        proposed_object="другой объект",
        proposed_complement="",
        proposed_result="Называет другой объект.",
        proposed_control="",
        structural_evidence=StructuralEvidence(notes=("fake_conflict",)),
        lexical_check=LexicalCheckResult(passed=True),
        confidence=CandidateConfidence.VALID,
    )

    def propose(_atom: SourceAtom) -> FrameCandidate:
        return conflicting

    mixed = SemanticFrameDispatcher(
        (*C5_REGISTRY, RegisteredBuilder("fake_conflict", propose))
    )
    decision = project_frames(locative, dispatcher=mixed)
    assert decision.frames[0].status is ObjectStatus.UNRESOLVED
    assert decision.frames[0].reason == AMBIGUOUS_FRAME_CANDIDATES


def test_helper_exception_does_not_break_row(monkeypatch) -> None:
    from calendar_pedagoga.semantic_atom import action_builders as builders

    def boom(text: str):
        raise RuntimeError("helper failed")

    monkeypatch.setattr(builders, "_explicit_action", boom)
    source = "Рисуют снегирей на ветке."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.PROVEN
    assert projection.frames[0].projected_result.casefold().startswith("рисует")


def test_type_and_hours_do_not_invent_action() -> None:
    source = "Основные сведения о крае."
    row = derive_fields_v2(
        topic_title="Практикум",
        theory_text="",
        practice_text=source,
        program_content=source,
        theory_hours=0,
        practice_hours=2,
    )
    projection = project_frames(source, row)
    assert row.lesson_type
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.candidate_result == ""
    _assert_identity(source)


def test_c5_knowledge_still_proven() -> None:
    source = "Понятия: ритм, темп, динамика."
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.kind is FrameKind.KNOWLEDGE


def test_full_dispatcher_contains_c5_and_actions() -> None:
    ids = full_dispatcher().builder_ids
    assert tuple(item.builder_id for item in C5_REGISTRY) == ids[:6]
    assert "explicit_action" in ids
    assert "finite_produce" in ids
