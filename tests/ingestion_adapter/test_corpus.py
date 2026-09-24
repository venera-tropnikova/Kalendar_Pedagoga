"""All real documents use the same traversal, adapter and invariants."""
from dataclasses import asdict
import json
from pathlib import Path
import pytest
from calendar_pedagoga.lossless_document import extract_document
from calendar_pedagoga.structural_interpretation import interpret_document
from calendar_pedagoga.ingestion_confirmation import apply, assess, build_model, start, State
from calendar_pedagoga.ingestion_adapter import AdaptationError, adapt, inventory
from tools.lossless_document.corpus import corpus_paths
from .test_adapter import assert_lossless

ROOT=Path(__file__).parents[2]
RECORDS=json.loads((ROOT/"tests/lossless_document/corpus.json").read_text(encoding="utf-8-sig"))


def branches(session):
    view=assess(session)
    if view.state==State.YEAR_AMBIGUOUS and view.years:
        for year in view.years:
            yield from branches(apply(session,"choose_year",{"year":year}))
    elif view.state==State.PLAN_AMBIGUOUS and view.plans:
        for option in view.plans:
            yield from branches(apply(session,"choose_plan",{"id":option.id}))
    else:
        yield session


@pytest.mark.parametrize("record",RECORDS,ids=[r["id"] for r in RECORDS])
def test_real_corpus_transport_or_explicit_block(record):
    resolved=next(r for r in corpus_paths(ROOT/"tests/lossless_document/corpus.json") if r["id"]==record["id"])
    b=interpret_document(extract_document(resolved["path"]))
    model=build_model(b)
    reports=[]
    output=ROOT/"_shadow_out/adapter_stage_d"
    output.mkdir(parents=True,exist_ok=True)
    for ordinal,session in enumerate(branches(start(model))):
        view=assess(session)
        row={"year":view.year,"state_c":view.state.value,"input_ids":len(inventory(model))}
        if view.state!=State.VALID:
            with pytest.raises(AdaptationError) as caught:
                adapt(session)
            assert caught.value.code=="UNCONFIRMED"
            ledger=caught.value.ledger
            row.update(result="BLOCKED_AS_REQUIRED",reason=caught.value.code,fragments=0)
        else:
            confirmed=apply(session,"confirm")
            packet=assert_lossless(confirmed)
            ledger=packet.ledger
            row.update(result="ADAPTED",sections=len(packet.sections),fragments=len(packet.fragments),
                       topics=len(packet.plan.topics),losses=0,duplicates=0,cross_year_links=0,
                       source=packet.plan.source)
        assert {e.ref for e in ledger.entries}==set(inventory(model))
        assert all(e.reason for e in ledger.entries)
        (output/(record["id"]+"-"+str(ordinal)+"-ledger.json")).write_text(
            json.dumps(asdict(ledger),ensure_ascii=False,indent=2),encoding="utf-8")
        reports.append(row)
    (output/(record["id"]+".json")).write_text(json.dumps({
        "label":record["label"],"sha256":b.source_document.source_sha256,
        "blocks":len(b.source_document.block_ids),"branches":reports,
        "note":"Test confirmations are transport checks, not teacher approval of corpus ambiguities."
    },ensure_ascii=False,indent=2),encoding="utf-8")
