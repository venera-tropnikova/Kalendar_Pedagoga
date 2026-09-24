from dataclasses import replace
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import pytest

from calendar_pedagoga.ingestion_confirmation import apply, assess, build_model, start, State
from calendar_pedagoga.ingestion_adapter import (
    AdaptationError, CompatibilityError, ExplicitWorkload, adapt, from_worker_json,
    inventory, legacy_content, legacy_plan, to_worker_json,
)
from calendar_pedagoga.ingestion_adapter.codec import dumps, loads
from calendar_pedagoga.ingestion_adapter.models import CanonicalRef
from .conftest import confirm, structure, p, table, interpret


def assert_lossless(session):
    packet = adapt(session)
    view = assess(session)
    content = {s.id: s for s in view.content}
    expected = tuple(f for f in session.model.program.source_fragments
                     if f.section_id in content and f.block_id in content[f.section_id].block_ids)
    assert {f.canonical.id: f.canonical for f in packet.fragments} == {f.id: f for f in expected}
    assert len(packet.fragments) == len(expected) == len({f.canonical.id for f in packet.fragments})
    assert packet.plan.canonical_plan == view.plan
    assert [t.canonical for t in packet.plan.topics] == [r for r in view.plan.rows if r.kind == "TOPIC"]
    records = (*packet.plan.topics, *packet.sections, *packet.fragments)
    assert all(r.provenance.year_scope == (view.year,) for r in records)
    assert all(r.provenance.confirmation_events and r.provenance.spans for r in records)
    assert len(packet.plan.row_provenance) == len(view.plan.rows)
    assert [m.ref.id for m in packet.plan.row_provenance] == [r.id for r in view.plan.rows]
    assert all(edge.provenance.ref.id and edge.provenance.spans and edge.confirmation_events for edge in packet.bindings)
    assert set(inventory(session.model,view)) == {e.ref for e in packet.ledger.entries}
    assert all(e.reason and (e.destinations if e.disposition == "TRANSFERRED" else not e.destinations) for e in packet.ledger.entries)
    wire = to_worker_json(packet)
    assert wire == to_worker_json(adapt(session))
    assert from_worker_json(wire, current_model=session.model) == packet
    assert loads(dumps(session.model)) == session.model
    assert packet.session is session
    return packet


@pytest.mark.parametrize("order,depth", [(tuple(range(6)),1), ((1,0,5,4,3,2),1), ((5,4,3,2,1,0),1), (tuple(range(6)),2), (tuple(range(6)),3)])
@pytest.mark.parametrize("year", [1,3])
def test_structural_mutations_preserve_all_data(order, depth, year):
    assert_lossless(confirm(build_model(structure(year=year,order=order,depth=depth))))


@pytest.mark.parametrize("merged", [False,True])
def test_merged_headers(merged):
    assert_lossless(confirm(build_model(structure(merged=merged))))


def test_ui_to_worker_round_trip_in_another_process(confirmed, tmp_path):
    packet = assert_lossless(confirmed)
    payload,model,out = (tmp_path / name for name in ("packet.json","current.json","received.json"))
    payload.write_text(to_worker_json(packet),encoding="utf-8")
    model.write_text(dumps(confirmed.model),encoding="utf-8")
    script = """from pathlib import Path
import sys
from calendar_pedagoga.ingestion_adapter import from_worker_json, to_worker_json
from calendar_pedagoga.ingestion_adapter.codec import loads
packet = from_worker_json(Path(sys.argv[1]).read_text(encoding='utf-8'), current_model=loads(Path(sys.argv[2]).read_text(encoding='utf-8')))
Path(sys.argv[3]).write_text(to_worker_json(packet),encoding='utf-8')
assert 'calendar_pedagoga.pipeline' not in sys.modules
assert 'calendar_pedagoga.generation_service' not in sys.modules
assert 'calendar_pedagoga.docx_generation' not in sys.modules
"""
    subprocess.run([sys.executable,"-c",script,str(payload),str(model),str(out)],check=True,timeout=60)
    assert out.read_bytes() == payload.read_bytes()


@pytest.mark.parametrize("delta",[0,1])
def test_unconfirmed_and_hours_conflict_block(delta):
    session=start(build_model(structure(hours_delta=delta)))
    with pytest.raises(AdaptationError,match="UNCONFIRMED") as error:
        adapt(session)
    assert {e.ref for e in error.value.ledger.entries} == set(inventory(session.model))
    assert any(e.disposition == "BLOCKED" for e in error.value.ledger.entries)


def test_stale_confirmation_and_mutated_source_block(confirmed):
    fresh=build_model(structure(text="Changed source."))
    with pytest.raises(ValueError,match="Source changed"):
        from_worker_json(to_worker_json(adapt(confirmed)),current_model=fresh)
    with pytest.raises(AdaptationError,match="UNCONFIRMED|INVALID_CONFIRMATION"):
        adapt(replace(confirmed,model=fresh))
    with pytest.raises(AdaptationError,match="STALE_CONFIRMATION"):
        adapt(replace(confirmed,model=replace(confirmed.model,sources=fresh.sources)))


def test_forged_confirmation_evidence_blocks(confirmed):
    forged=replace(confirmed.events[-1],evidence=(),actor="SYSTEM")
    with pytest.raises(AdaptationError,match="INVALID_CONFIRMATION"):
        adapt(replace(confirmed,events=(forged,)))


@pytest.mark.parametrize("target",["block","fragment","section"])
def test_unresolved_selected_object_blocks_after_c_confirmation(target):
    b=structure()
    f=b.source_fragments[0]
    if target=="block":
        b=replace(b,block_roles=tuple(replace(r,role="UNRESOLVED") if r.block_id==f.block_id else r for r in b.block_roles))
    elif target=="fragment":
        b=replace(b,source_fragments=(replace(f,decision=replace(f.decision,status="UNRESOLVED")),*b.source_fragments[1:]))
    else:
        s=b.content_sections[0]
        b=replace(b,content_sections=(replace(s,decision=replace(s.decision,status="UNRESOLVED")),*b.content_sections[1:]))
    with pytest.raises(AdaptationError,match="UNRESOLVED"):
        adapt(confirm(build_model(b)))


def test_identical_text_is_not_deduplicated():
    b=structure()
    b=structure(text=b.source_fragments[1].raw_text)
    packet=assert_lossless(confirm(build_model(b)))
    assert len(packet.fragments)==2
    assert packet.fragments[0].canonical.raw_text==packet.fragments[1].canonical.raw_text
    assert packet.fragments[0].canonical.id!=packet.fragments[1].canonical.id
    legacy=legacy_content(packet)
    assert len(legacy)==2 and legacy[0].fragment_id!=legacy[1].fragment_id
    assert all(v.item.content==f.canonical.raw_text and v.provenance==f.provenance for v,f in zip(legacy,packet.fragments))


def test_parent_fragment_and_list_level_survive():
    def listed(text, level):
        return p(text, properties='<w:numPr><w:ilvl w:val="' + str(level) + '"/><w:numId w:val="1"/></w:numPr>')
    b=interpret(p("Учебно-тематический план 1 года обучения",bold=True)+table()+
                p("Содержание программы 1 года обучения",bold=True)+p("1. Тема Альфа",bold=True)+
                listed("Outer item.",0)+listed("Nested item.",2)+p("2. Тема Бета",bold=True)+p("Other source."))
    packet=assert_lossless(confirm(build_model(b)))
    assert packet.fragments[1].canonical.parent_fragment_id==packet.fragments[0].canonical.id
    assert packet.fragments[1].canonical.list_level==2


def test_missing_parent_blocks():
    b=structure()
    first=b.source_fragments[0]
    b=replace(b,source_fragments=(replace(first,parent_fragment_id="missing"),*b.source_fragments[1:]))
    with pytest.raises(AdaptationError,match="MISSING_PARENT"):
        adapt(confirm(build_model(b)))


def document_with_hours(values):
    # Structural vocabulary belongs to generated input data, never the adapter.
    return interpret(p("Учебно-тематический план 1 года обучения",bold=True)+table(values=values)+
                     p("Содержание программы 1 года обучения",bold=True)+p("1. Тема Альфа",bold=True)+p("Text one.")+
                     p("2. Тема Бета",bold=True)+p("Text two."))


def test_exact_nonterminating_fraction():
    b=document_with_hours([("1","Тема Альфа","1","1/3","1/3","1/3"),("2","Тема Бета","1","1/3","1/3","1/3"),
                           ("","Итого","2","2/3","2/3","2/3")])
    packet=assert_lossless(confirm(build_model(b)))
    assert dict(packet.plan.topics[0].canonical.hours)["THEORY"].value==Fraction(1,3)


def test_two_years_same_titles_stay_separate():
    values=[("1","Same topic","2","0,5","0,5","1"),("","Итого","2","0,5","0,5","1")]
    body=""
    for year in (1,2):
        body+=p(f"Учебно-тематический план {year} года обучения",bold=True)+table(values=values)
        body+=p(f"Содержание программы {year} года обучения",bold=True)+p("1. Same topic",bold=True)+p(f"Text {year}.")
    model=build_model(interpret(body))
    packets=[]
    for year in (1,2):
        session=apply(start(model),"choose_year",{"year":year})
        view=assess(session)
        if view.state==State.BINDING_AMBIGUOUS:
            session=apply(session,"bind_topics",{"bindings":{q.topic_id:[view.content[0].id] for q in view.questions}})
        assert assess(session).state==State.VALID
        packets.append(assert_lossless(apply(session,"confirm")))
    assert not ({f.canonical.id for f in packets[0].fragments}&{f.canonical.id for f in packets[1].fragments})
    assert all(f.canonical.years==(packet.plan.study_year,) for packet in packets for f in packet.fragments)


def test_foreign_fragment_inside_confirmed_section_blocks():
    b=structure()
    first=b.source_fragments[0]
    b=replace(b,source_fragments=(replace(first,years=(2,)),*b.source_fragments[1:]))
    with pytest.raises(AdaptationError,match="UNREPRESENTED_CONTENT|CROSS_YEAR"):
        adapt(confirm(build_model(b)))


@pytest.mark.parametrize("chosen",["EMBEDDED","EXTERNAL"])
def test_selected_source_is_authoritative(chosen):
    model=build_model(structure(),external=structure(label="Different topic"))
    session=apply(start(model),"choose_source",{"source":chosen})
    view=assess(session)
    if view.state==State.BINDING_AMBIGUOUS:
        session=apply(session,"bind_topics",{"bindings":{q.topic_id:[view.content[i].id] for i,q in enumerate(view.questions)}})
    packet=assert_lossless(apply(session,"confirm"))
    assert packet.plan.source==chosen
    assert all(t.provenance.ref.source==chosen for t in packet.plan.topics)


def test_external_priority_and_qualified_ids():
    b=structure()
    packet=assert_lossless(confirm(build_model(b,external=b)))
    assert packet.plan.source=="EXTERNAL"
    for source in ("EXTERNAL","EMBEDDED"):
        assert CanonicalRef(source,b.source_document.id) in inventory(packet.session.model)


def test_manual_fallback_is_explicit_input():
    b=structure()
    packet=assert_lossless(confirm(build_model(replace(b,tables=()),manual=b)))
    assert packet.plan.source=="MANUAL"


def test_only_topics_and_monotonic_source_order(confirmed):
    packet=assert_lossless(confirmed)
    assert all(t.canonical.kind=="TOPIC" for t in packet.plan.topics)
    assert any(r.kind=="TOTAL" for r in packet.plan.canonical_plan.rows)
    orders=[t.provenance.order for t in packet.plan.topics]
    assert orders==sorted(orders) and len(set(orders))==len(orders)


def test_changed_packet_and_unknown_wire_version_block(confirmed):
    packet=adapt(confirmed)
    with pytest.raises(ValueError,match="Corrupt"):
        from_worker_json(dumps(replace(packet,fragments=packet.fragments[:1])),current_model=confirmed.model)
    with pytest.raises(ValueError,match="Unsupported"):
        from_worker_json(dumps(replace(packet,version="unknown")),current_model=confirmed.model)
    with pytest.raises(ValueError,match="Unknown transport type"):
        loads('{"$type":"subprocess.Popen","fields":{}}')
    with pytest.raises(ValueError,match="Duplicate JSON key"):
        loads('{"x":1,"x":2}')


def test_legacy_refuses_unrepresentable_contract(confirmed):
    with pytest.raises(CompatibilityError,match="embedded"):
        legacy_plan(adapt(confirmed),ExplicitWorkload(2,Fraction(2),"explicit"))
    b=structure()
    packet=adapt(confirm(build_model(b,external=b)))
    with pytest.raises(CompatibilityError,match="workload"):
        legacy_plan(packet,None)
    with pytest.raises(CompatibilityError,match="training"):
        legacy_plan(packet,ExplicitWorkload(2,Fraction(2),"explicit"))


def test_exact_compatible_legacy_plan():
    b=document_with_hours([("1","Тема Альфа","2","0,5","0","1,5"),("2","Тема Бета","2","0,5","0","1,5"),
                           ("","Итого","4","1","0","3")])
    packet=adapt(confirm(build_model(b,external=b)))
    bundle=legacy_plan(packet,ExplicitWorkload(2,Fraction(2),"Teacher input"))
    from calendar_pedagoga.confirmed_study_plan import ConfirmedStudyPlan
    from calendar_pedagoga.generation_contract import ConfirmedStudyPlanDTO
    assert isinstance(bundle.plan,ConfirmedStudyPlan)
    assert ConfirmedStudyPlanDTO.from_model(bundle.plan).to_model()==bundle.plan
    assert bundle.overlay is packet
    assert bundle.plan.topics[0].hours.theory==Fraction(1,2)


def test_no_production_imports_or_name_rules():
    root=Path(__file__).parents[2]
    runtime=root/"src/calendar_pedagoga/ingestion_adapter"
    text="\n".join(path.read_text(encoding="utf-8") for path in runtime.glob("*.py"))
    records=json.loads((root/"tests/lossless_document/corpus.json").read_text(encoding="utf-8-sig"))
    for record in records:
        assert record["label"] not in text
    for forbidden in ("calendar_pedagoga.pipeline","calendar_pedagoga.generation_service","calendar_pedagoga.scheduling","calendar_pedagoga.docx_generation"):
        assert forbidden not in text
    for path in (root/"src/calendar_pedagoga").glob("*.py"):
        assert "ingestion_adapter" not in path.read_text(encoding="utf-8")
