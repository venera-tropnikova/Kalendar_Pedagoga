"""One corpus traversal. Names are input metadata, never expected-state routing."""
from dataclasses import replace
from pathlib import Path
import json

import pytest
from streamlit.testing.v1 import AppTest
from calendar_pedagoga.lossless_document import extract_document
from calendar_pedagoga.structural_interpretation import interpret_document
from calendar_pedagoga.ingestion_confirmation import State, apply, assess, build_model, restore, save, start
from tools.lossless_document.corpus import corpus_paths
from tools.structural_interpretation.audit import audit_structure

ROOT = Path(__file__).parents[2]
RECORDS = json.loads((ROOT / 'tests/lossless_document/corpus.json').read_text(encoding='utf-8-sig'))


def explore(session):
    """Explore all year/plan branches without pretending to be the reviewing teacher."""
    view = assess(session)
    if view.state == State.YEAR_AMBIGUOUS and view.years:
        for year in view.years:
            yield from explore(apply(session, 'choose_year', {'year': year}))
    elif view.state == State.PLAN_AMBIGUOUS and view.plans:
        for option in view.plans:
            yield from explore(apply(session, 'choose_plan', {'id': option.id}))
    else:
        yield session


@pytest.mark.parametrize('record', RECORDS, ids=[r['id'] for r in RECORDS])
def test_entire_corpus_uses_identical_state_data_and_ui_invariants(record, monkeypatch):
    resolved = next(r for r in corpus_paths(ROOT / 'tests/lossless_document/corpus.json') if r['id'] == record['id'])
    a = extract_document(resolved['path'])
    b = interpret_document(a)
    baseline = audit_structure(b)
    model = build_model(b)
    initial = start(model)
    assert model.program is b and model.program.source_document is a
    evidence = []
    monkeypatch.setenv('KP_INGESTION_CONFIRMATION_SHADOW', '1')
    # Same UI, assertions and branch exploration for every input record.
    for session in (initial, *explore(initial)):
        view = assess(session)
        assert session.model.program is b and b.source_document is a
        assert len(b.block_roles) == len(a.block_ids)
        assert audit_structure(b) == baseline
        assert restore(model, save(session)) == session
        app = AppTest.from_file(str(ROOT / 'tools/ingestion_confirmation/app.py'), default_timeout=60)
        app.session_state['review_model'] = model
        app.session_state['ingestion_review'] = session
        app.run()
        assert not app.exception
        assert app.session_state['ingestion_review'].model.program is b
        if view.state == State.VALID:
            button = next(button for button in app.button if button.label == 'Да, продолжить')
            assert not button.disabled
            button.click().run()
            assert not app.exception
            confirmed = app.session_state['ingestion_review']
            assert confirmed.confirmed and len(confirmed.events) == len(session.events) + 1
        else:
            with pytest.raises(ValueError):
                apply(session, 'confirm')
        if view.state == State.HOURS_CONFLICT:
            assert view.hour_issues
            assert next(button for button in app.button if button.label == 'Да, продолжить').disabled
            for issue in view.hour_issues:
                if issue.declared is not None and issue.calculated is not None:
                    assert issue.difference == issue.calculated - issue.declared
        if view.state == State.BINDING_AMBIGUOUS:
            assert len(app.multiselect) == len(view.questions)
            assert len([button for button in app.button if button.label == 'Подтвердить все связи']) == 1
        evidence.append({'year': view.year, 'state': view.state.value,
                         'plan': view.selected.id if view.selected else None,
                         'confirmed_events': len(session.events), 'pending_bindings': len(view.questions),
                         'hour_issues': [{'label': i.label, 'declared': str(i.declared),
                                          'calculated': str(i.calculated), 'difference': str(i.difference)}
                                         for i in view.hour_issues]})
    changed = build_model(replace(b, schema_version=b.schema_version + ':changed'))
    for session in explore(initial):
        reset = start(changed, session)
        assert not reset.events and not reset.confirmed
        assert reset.archived == session.events
        assert reset.model.program.source_document is a
    packet = {'id': record['id'], 'label': record['label'], 'sha256': a.source_sha256,
              'blocks_a': len(a.block_ids), 'blocks_b': len(b.block_roles),
              'sections': len(b.content_sections), 'fragments': len(b.source_fragments),
              'initial_state': assess(initial).state.value, 'branches': evidence,
              'losses': 0, 'duplicates': 0, 'shared_ui_pass': True}
    output = ROOT / '_shadow_out/confirmation_stage_c'
    output.mkdir(parents=True, exist_ok=True)
    (output / (record['id'] + '.json')).write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
