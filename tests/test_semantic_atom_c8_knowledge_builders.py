"""C8: explicit knowledge builders on the shadow dispatcher."""

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
from calendar_pedagoga.semantic_atom.action_builders import ACTION_REGISTRY
from calendar_pedagoga.semantic_atom.adapter import project_passthrough_graph
from calendar_pedagoga.semantic_atom.dispatcher import (
    AMBIGUOUS_FRAME_CANDIDATES,
    SemanticFrameDispatcher,
)
from calendar_pedagoga.semantic_atom.frame_adapter import (
    C5_REGISTRY,
    LEXICAL_VIOLATION,
    full_dispatcher,
    project_frames,
    reset_frame_adapter_calls,
)
from calendar_pedagoga.semantic_atom.knowledge_builders import KNOWLEDGE_REGISTRY
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
KNOWLEDGE_PATH = PRODUCTION_DIR / "semantic_atom" / "knowledge_builders.py"

QUESTION_CASES = (
    "Что такое флумбер?",
    "Почему желтеет мохяник?",
    "Как измеряют температуру?",
    "Кто изобрёл барограф?",
    "Какой прибор измеряет влажность?",
    "Какая шкала у гигрометра?",
    "Какие слои у лишайника?",
    "Кем был Пржевальский?",
    "Откуда берётся роса?",
    "Где обитает мохяник?",
    "Когда цветёт вереск?",
)


def test_c1_oracle_file_is_unchanged() -> None:
    assert hashlib.sha256(C1_ORACLE.read_bytes()).hexdigest() == C1_ORACLE_SHA256


def test_flag_stays_off() -> None:
    assert USE_SEMANTIC_ATOM_ENGINE is False


def test_knowledge_builders_import_production_one_way() -> None:
    tree = ast.parse(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "calendar_pedagoga.content_engine_v2" in imported or (
        "calendar_pedagoga" in imported
    )


def test_production_does_not_import_knowledge_builders() -> None:
    reset_frame_adapter_calls()
    reset_shadow_invocation_count()
    needles = ("KNOWLEDGE_REGISTRY", "knowledge_builders")
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
    report = project_passthrough_graph(row, source=source)
    projection = project_frames(source, row)
    assert compare_identity(row, shadow)[0].kind is DiffKind.EQUAL
    assert report.projected_result == row.planned_result
    assert projection.identity_result == row.planned_result
    assert row.planned_result == shadow.planned_result


def _keys(source: str) -> frozenset[tuple[str, str]]:
    projection = project_frames(source)
    return frozenset(
        (frame.kind.value, frame.projected_result.casefold())
        for frame in projection.frames
        if frame.status is ObjectStatus.PROVEN
    )


def test_all_question_kinds_on_unseen_lexicon() -> None:
    for source in QUESTION_CASES:
        _assert_identity(source)
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, (
            source,
            frame.reason,
            frame.projected_result,
        )
        assert frame.kind is FrameKind.KNOWLEDGE, source
        folded = frame.projected_result.casefold()
        assert folded.startswith("объясняет"), frame.projected_result
        body = source.rstrip("?").casefold()
        assert body in folded, (source, frame.projected_result)


def test_how_measure_stays_knowledge_not_action() -> None:
    source = "Как измеряют температуру?"
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.PROVEN
    assert frame.kind is FrameKind.KNOWLEDGE
    folded = frame.projected_result.casefold()
    assert "как измеряют температуру" in folded
    assert frame.kind is not FrameKind.ACTION
    assert not folded.startswith("измеряет")


def test_definitions_em_en_dash_and_eto() -> None:
    cases = (
        "Флумбер — прибор для ветра.",
        "Флумбер – прибор для ветра.",
        "Флумбер — это прибор для ветра.",
    )
    for source in cases:
        _assert_identity(source)
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, (source, frame.reason)
        assert frame.kind is FrameKind.DEFINITION
        folded = frame.projected_result.casefold()
        assert folded.startswith("объясняет")
        assert "флумбер" in folded
        assert "прибор для ветра" in folded
        assert "краевед" not in folded


def test_dash_inside_quotes_is_not_definition() -> None:
    quoted = "«Юный следопыт — флумбер»."
    event = "Конкурс «Юный следопыт — флумбер»."
    quoted_frame = project_frames(quoted).frames[0]
    event_frame = project_frames(event).frames[0]
    assert quoted_frame.status is ObjectStatus.UNRESOLVED
    assert quoted_frame.kind is not FrameKind.DEFINITION
    assert event_frame.kind is not FrameKind.DEFINITION
    assert "объясняет" not in event_frame.projected_result.casefold()


def test_numeric_range_is_not_definition() -> None:
    source = "10 — 15."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED


def test_form_activity_dash_stays_action_not_definition() -> None:
    source = "Форма деятельности — рисование гербария."
    frame = project_frames(source).frames[0]
    assert frame.kind is not FrameKind.DEFINITION
    assert "объясняет" not in frame.projected_result.casefold()
    if frame.status is ObjectStatus.PROVEN:
        assert frame.kind in {FrameKind.ACTION, FrameKind.CREATIVE_PRODUCT}


def test_closed_catalog_comma_and_semicolon() -> None:
    comma = "Составляющие: кварц, слюда, шпат."
    semicolon = "Составляющие: кварц; слюда; шпат."
    for source in (comma, semicolon):
        _assert_identity(source)
        frame = project_frames(source).frames[0]
        assert frame.status is ObjectStatus.PROVEN, (source, frame.reason)
        assert frame.kind is FrameKind.KNOWLEDGE
        folded = frame.projected_result.casefold()
        assert folded.startswith("называет")
        assert "кварц" in folded and "слюда" in folded and "шпат" in folded


def test_open_catalog_is_unresolved() -> None:
    sources = (
        "Составляющие: кварц, слюда и другие.",
        "Составляющие: кварц, слюда и т. д.",
        "Составляющие: кварц, слюда и прочее.",
        "Составляющие: кварц, слюда…",
        "Составляющие: кварц, слюда,",
    )
    for source in sources:
        projection = project_frames(source)
        assert projection.frames[0].status is ObjectStatus.UNRESOLVED, source
        assert projection.candidate_result == ""


def test_action_catalog_is_not_knowledge() -> None:
    source = "Игры: «Найди пару», «Тихий круг»."
    frame = project_frames(source).frames[0]
    assert frame.kind is not FrameKind.KNOWLEDGE or frame.status is ObjectStatus.UNRESOLVED
    assert "называет" not in frame.projected_result.casefold()
    assert "объясняет" not in frame.projected_result.casefold()


def test_independent_knowledge_atoms_and_permutation() -> None:
    parts = (
        "Что такое флумбер?",
        "Составляющие: кварц, слюда, шпат.",
        "Флумбер — прибор для ветра.",
    )
    keys = []
    for order in permutations(parts):
        source = " ".join(order)
        projection = project_frames(source)
        assert [frame.status for frame in projection.frames] == [
            ObjectStatus.PROVEN
        ] * 3, source
        keys.append(
            frozenset(
                (frame.kind.value, frame.projected_result.casefold())
                for frame in projection.frames
            )
        )
    assert len(set(keys)) == 1


def test_action_plus_knowledge_keeps_both() -> None:
    source = "Рисуют флумберы у ручья. Что такое флумбер?"
    projection = project_frames(source)
    assert [frame.status for frame in projection.frames] == [
        ObjectStatus.PROVEN,
        ObjectStatus.PROVEN,
    ]
    kinds = {frame.kind for frame in projection.frames}
    assert FrameKind.KNOWLEDGE in kinds
    assert kinds & {FrameKind.ACTION, FrameKind.CREATIVE_PRODUCT}
    assert _keys("Что такое флумбер? Рисуют флумберы у ручья.") == _keys(source)


def test_neighbor_action_does_not_suppress_knowledge() -> None:
    knowledge = "Почему желтеет мохяник?"
    mixed = "Рисуют флумберы у ручья. Почему желтеет мохяник?"
    solo = project_frames(knowledge).frames[0]
    neighbor = project_frames(mixed).frames[1]
    assert solo.status is ObjectStatus.PROVEN
    assert neighbor.status is ObjectStatus.PROVEN
    assert solo.kind is neighbor.kind is FrameKind.KNOWLEDGE
    assert solo.projected_result == neighbor.projected_result


def test_conflicting_builders_are_unresolved() -> None:
    source = "Что такое флумбер — зонд?"
    frame = project_frames(source).frames[0]
    assert frame.status is ObjectStatus.UNRESOLVED
    assert frame.reason == AMBIGUOUS_FRAME_CANDIDATES
    assert frame.projected_result == ""


def test_helper_exception_does_not_break_other_candidates(monkeypatch) -> None:
    from calendar_pedagoga.semantic_atom import knowledge_builders as builders

    def boom(text: str):
        raise RuntimeError("helper failed")

    monkeypatch.setattr(builders, "_question", boom)
    source = "Что такое флумбер? Составляющие: кварц, слюда, шпат."
    projection = project_frames(source)
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.frames[1].status is ObjectStatus.PROVEN
    assert "кварц" in projection.frames[1].projected_result.casefold()


def test_lexical_violation_is_unresolved(monkeypatch) -> None:
    from calendar_pedagoga.semantic_atom import knowledge_builders as builders

    def fake_question(text: str):
        return "объясняет квантовый флумбер", "объясняет", "флумбер", ""

    monkeypatch.setattr(builders, "_question", fake_question)
    projection = project_frames("Что такое флумбер?")
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.frames[0].reason == LEXICAL_VIOLATION
    assert projection.candidate_result == ""


def test_generic_bare_np_stays_unresolved() -> None:
    for source in ("Флумбер.", "Мохяник.", "Основные сведения о крае."):
        projection = project_frames(source)
        assert projection.frames[0].status is ObjectStatus.UNRESOLVED, source
        assert projection.candidate_result == ""


def test_type_and_hours_do_not_invent_knowledge() -> None:
    source = "Флумбер."
    row = derive_fields_v2(
        topic_title="Теория",
        theory_text=source,
        practice_text="",
        program_content=source,
        theory_hours=2,
        practice_hours=0,
    )
    projection = project_frames(source, row)
    assert row.lesson_type
    assert projection.frames[0].status is ObjectStatus.UNRESOLVED
    assert projection.candidate_result == ""
    _assert_identity(source)


def test_c5_c7_regression_pins() -> None:
    c5_only = SemanticFrameDispatcher(C5_REGISTRY)
    what = project_frames("Что такое краеведение?", dispatcher=c5_only)
    assert what.frames[0].status is ObjectStatus.UNRESOLVED
    concepts = project_frames("Понятия: ритм, темп, динамика.")
    assert concepts.frames[0].status is ObjectStatus.PROVEN
    assert concepts.frames[0].kind is FrameKind.KNOWLEDGE
    action = project_frames("Рисуют деревья у ручья.")
    assert action.frames[0].status is ObjectStatus.PROVEN
    assert action.frames[0].kind in {FrameKind.ACTION, FrameKind.CREATIVE_PRODUCT}


def test_full_dispatcher_contains_c5_actions_and_knowledge() -> None:
    ids = full_dispatcher().builder_ids
    assert tuple(item.builder_id for item in C5_REGISTRY) == ids[:6]
    assert "explicit_action" in ids
    assert "knowledge_question" in ids
    assert "knowledge_definition" in ids
    assert "knowledge_catalog" in ids
    assert tuple(item.builder_id for item in ACTION_REGISTRY)
    assert tuple(item.builder_id for item in KNOWLEDGE_REGISTRY)


def test_c5_what_is_mixed_row_keeps_c5_shapes() -> None:
    source = (
        "Что такое флумбер? "
        "Понятия: маршрут, ориентир. "
        "Для чего нужны карта и компас?"
    )
    projection = project_frames(source)
    assert [frame.status for frame in projection.frames] == [
        ObjectStatus.PROVEN,
        ObjectStatus.PROVEN,
        ObjectStatus.PROVEN,
    ]
    assert projection.frames[0].kind is FrameKind.KNOWLEDGE
    assert projection.frames[1].kind is FrameKind.KNOWLEDGE
    assert "что такое флумбер" in projection.frames[0].projected_result.casefold()
