from dataclasses import replace
from fractions import Fraction
from pathlib import Path
import ast
import pytest
from calendar_pedagoga.ingestion_confirmation import apply, assess, build_model, start, State
from calendar_pedagoga.ingestion_adapter import AdaptationError, adapt
from .conftest import confirm, structure, interpret, p, table
from .test_adapter import assert_lossless


def test_duplicate_fragment_id_is_blocking():
    b=structure()
    b=replace(b,source_fragments=(*b.source_fragments,b.source_fragments[0]))
    with pytest.raises(AdaptationError,match="DUPLICATE_FRAGMENT"):
        adapt(confirm(build_model(b)))


def test_source_bytes_changed_under_stale_hash_blocks(confirmed):
    b=confirmed.model.program
    b=replace(b,source_document=replace(b.source_document,original_bytes=b"other bytes"))
    model=build_model(b)
    with pytest.raises(AdaptationError,match="SOURCE_INTEGRITY"):
        adapt(replace(confirmed,model=model))


def test_sections_and_totals_remain_separate_from_topics():
    values=[("1","Раздел","4","1","1","2"),
            ("1.1","Тема Альфа","2","0,5","0,5","1"),
            ("1.2","Тема Бета","2","0,5","0,5","1"),
            ("","Итого","4","1","1","2")]
    b=interpret(p("Учебно-тематический план 1 года обучения",bold=True)+table(values=values)+
                p("Содержание программы 1 года обучения",bold=True)+
                p("Раздел 1",bold=True)+p("Тема 1.1 Тема Альфа",bold=True)+p("First source.")+
                p("Тема 1.2 Тема Бета",bold=True)+p("Second source."))
    session=start(build_model(b))
    view=assess(session)
    if view.state==State.BINDING_AMBIGUOUS:
        choices={q.topic_id:[view.content[min(i,len(view.content)-1)].id] for i,q in enumerate(view.questions)}
        session=apply(session,"bind_topics",{"bindings":choices})
    assert assess(session).state==State.VALID
    packet=assert_lossless(apply(session,"confirm"))
    assert len(packet.plan.topics)==2
    assert {r.kind for r in packet.plan.canonical_plan.rows}>={"SECTION","TOPIC","TOTAL"}
    assert sum(dict(t.canonical.hours)["TOTAL"].value for t in packet.plan.topics)==Fraction(4)
    assert all(t.provenance.parent_section for t in packet.plan.topics)


def test_selected_source_renaming_does_not_change_structural_selection():
    first=adapt(confirm(build_model(structure(label="Unrelated title A",text="Arbitrary source A."))))
    second=adapt(confirm(build_model(structure(label="Unrelated title B",text="Arbitrary source B."))))
    assert first.plan.source==second.plan.source
    assert [r.canonical.kind for r in first.plan.topics]==[r.canonical.kind for r in second.plan.topics]
    assert len(first.fragments)==len(second.fragments)
    assert first.plan.canonical_plan.totals[0][1].value==second.plan.canonical_plan.totals[0][1].value


def test_adapter_never_calls_parse_or_generation_entry_points():
    root=Path(__file__).parents[2]/"src/calendar_pedagoga/ingestion_adapter"
    calls=[]
    for path in root.glob("*.py"):
        tree=ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node,ast.Call):
                calls.append(node.func.id if isinstance(node.func,ast.Name) else getattr(node.func,"attr",""))
    assert not set(calls)&{"parse_utp","parse_program","extract_document","interpret_document",
                           "confirmed_plan_from_external_utp","build_generation_payload","generate_calendar"}

def test_explicit_column_review_resolves_only_plan_roles():
    b=structure()
    original=b.tables[0]
    blocks=set(original.block_ids)
    b=replace(b,tables=(replace(original,classification="UNKNOWN",plans=()),),
              block_roles=tuple(replace(r,role="UNRESOLVED") if r.block_id in blocks else r for r in b.block_roles))
    session=start(build_model(b))
    assert assess(session).state==State.PLAN_AMBIGUOUS
    option=assess(session).plans[0]
    session=apply(session,"choose_plan",{"id":option.id})
    session=apply(session,"map_columns",{"roles":["NUMBER","TITLE","TOTAL","THEORY","TRAINING","PRACTICE"],"header_count":1})
    view=assess(session)
    if view.state==State.BINDING_AMBIGUOUS:
        session=apply(session,"bind_topics",{"bindings":{q.topic_id:[view.content[i].id] for i,q in enumerate(view.questions)}})
    assert assess(session).state==State.VALID
    packet=assert_lossless(apply(session,"confirm"))
    assert packet.session.model.program.block_roles==b.block_roles
    assert "USER_COLUMN_MAPPING" in {e.code for e in packet.plan.canonical_plan.mapping.decision.evidence}


@pytest.mark.parametrize("status",["NEEDS_CONFIRMATION","CONFLICT","UNRESOLVED"])
def test_unconfirmed_fragment_decision_blocks(status):
    b=structure()
    first=b.source_fragments[0]
    b=replace(b,source_fragments=(replace(first,decision=replace(first.decision,status=status)),*b.source_fragments[1:]))
    with pytest.raises(AdaptationError,match="UNRESOLVED"):
        adapt(confirm(build_model(b)))


def test_cross_section_parent_blocks():
    b=structure()
    first,second=b.source_fragments
    b=replace(b,source_fragments=(first,replace(second,parent_fragment_id=first.id)))
    with pytest.raises(AdaptationError,match="CROSS_SECTION_PARENT"):
        adapt(confirm(build_model(b)))


def test_shared_section_fragments_are_transferred_once():
    b=structure()
    b=replace(b,bindings=tuple(replace(edge,decision=replace(edge.decision,status="NEEDS_CONFIRMATION")) for edge in b.bindings))
    session=start(build_model(b))
    view=assess(session)
    selected=view.content[0].id
    session=apply(session,"bind_topics",{"bindings":{q.topic_id:[selected] for q in view.questions}})
    packet=assert_lossless(apply(session,"confirm"))
    matched=[f for f in packet.fragments if f.canonical.section_id==selected]
    assert len(matched)==1
    assert len(matched[0].provenance.topic_ids)==2
    assert len(packet.bindings)==2


def test_unselected_norms_and_results_stay_only_in_sidecar():
    def cell(text):
        return "<w:tc>" + p(text) + "</w:tc>"
    norms="<w:tbl><w:tr>" + "".join(map(cell, ("Тест","Мальчики","Девочки"))) + "</w:tr><w:tr>" + "".join(map(cell, ("Норматив","2","3"))) + "</w:tr></w:tbl>"
    b=interpret(p("Учебно-тематический план 1 года обучения",bold=True)+table()+
                p("Содержание программы 1 года обучения",bold=True)+p("1. Тема Альфа",bold=True)+p("Source one.")+
                p("2. Тема Бета",bold=True)+p("Source two.")+
                p("Планируемые результаты 1 года обучения",bold=True)+p("Expected outcome.")+norms)
    packet=assert_lossless(confirm(build_model(b)))
    assert b.expected_results
    assert any(t.classification=="NORMATIVE_CONTROL" for t in b.tables)
    assert not {r.id for r in b.expected_results}&{r.canonical.id for r in packet.fragments}
    excluded=[entry for entry in packet.ledger.entries if entry.disposition=="EXCLUDED"]
    assert any(entry.reason=="EXPECTED_RESULTS_NOT_SOURCE" for entry in excluded)
    assert any(entry.reason=="TABLE_PROVENANCE_ONLY:NORMATIVE_CONTROL" for entry in excluded)
    assert all(entry.reason and not entry.destinations for entry in excluded)
    assert packet.session.model.program.source_document==b.source_document
