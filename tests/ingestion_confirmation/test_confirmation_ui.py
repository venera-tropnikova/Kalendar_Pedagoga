from dataclasses import replace
from pathlib import Path
import ast
import re

import pytest
from streamlit.testing.v1 import AppTest
from calendar_pedagoga.ingestion_confirmation import State, apply, assess, build_model, start, SHADOW_ENABLED
from conftest import structure

APP = Path(__file__).parents[2] / 'tools/ingestion_confirmation/app.py'


def run_ui(model, monkeypatch, session=None):
    monkeypatch.setenv('KP_INGESTION_CONFIRMATION_SHADOW', '1')
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state['review_model'] = model
    if session:
        app.session_state['ingestion_review'] = session
    app.run()
    assert not app.exception
    return app


def test_shadow_is_off_without_explicit_opt_in(monkeypatch):
    monkeypatch.delenv('KP_INGESTION_CONFIRMATION_SHADOW', raising=False)
    assert SHADOW_ENABLED is False
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    assert not app.file_uploader if hasattr(app, 'file_uploader') else not app.get('file_uploader')
    assert not app.button


def test_valid_render_has_one_click_and_no_technical_vocabulary(monkeypatch):
    app = run_ui(build_model(structure()), monkeypatch)
    text = ' '.join(item.value for item in app.markdown)
    assert 'Мы нашли:' in text and 'Всё верно?' in text
    assert all(word not in text for word in ('SOURCE', 'confidence', 'ColumnMapping', 'YearScope'))
    assert [b.label for b in app.button] == ['Да, продолжить', 'Исправить']
    app.button[0].click().run()
    assert not app.exception and app.session_state['ingestion_review'].confirmed
    assert len(app.session_state['ingestion_review'].events) == 1
    assert app.success


def test_hours_conflict_renders_exact_difference_and_disabled_continue(monkeypatch):
    app = run_ui(build_model(structure(hours_delta=1)), monkeypatch)
    assert next(b for b in app.button if b.label == 'Да, продолжить').disabled
    frame = app.dataframe[0].value
    assert '-1' in frame['Разница (расчёт − документ)'].tolist()
    assert app.error and not app.session_state['ingestion_review'].confirmed


def test_candidate_plan_selection_keeps_other_candidates(monkeypatch):
    model = build_model(structure(count=2))
    app = run_ui(model, monkeypatch)
    assert len(app.dataframe) == 2
    options = assess(start(model)).plans
    app.radio[0].set_value(options[1].id).run()
    next(b for b in app.button if b.label == 'Подтвердить план').click().run()
    assert not app.exception
    assert assess(app.session_state['ingestion_review']).selected.id == options[1].id
    assert next(b for b in app.button if b.label == 'Да, продолжить')


def test_group_binding_screen_confirms_all_at_once(monkeypatch):
    b = structure()
    b = replace(b, bindings=tuple(replace(x, decision=replace(x.decision, status='NEEDS_CONFIRMATION')) for x in b.bindings))
    app = run_ui(build_model(b), monkeypatch)
    assert len(app.multiselect) == 2
    assert len([b for b in app.button if b.label == 'Подтвердить все связи']) == 1
    next(b for b in app.button if b.label == 'Подтвердить все связи').click().run()
    assert not app.exception and assess(app.session_state['ingestion_review']).state == State.VALID


def test_source_conflict_has_both_plans_and_source_choice(monkeypatch):
    model = build_model(structure(), external=structure(label='Иной предмет'))
    app = run_ui(model, monkeypatch)
    assert len(app.dataframe) == 3
    assert 'Различия вариантов' in ' '.join(x.value for x in app.markdown)
    app.radio[0].set_value('EMBEDDED').run()
    next(b for b in app.button if b.label == 'Использовать выбранный источник').click().run()
    assert not app.exception and assess(app.session_state['ingestion_review']).source == 'EMBEDDED'


def test_source_change_replaces_completed_confirmation(monkeypatch):
    model = build_model(structure())
    app = run_ui(model, monkeypatch, apply(start(model), 'confirm'))
    assert app.success
    app.session_state['review_model'] = build_model(structure(text='Другой исходный текст.'))
    app.run()
    assert not app.exception
    assert not app.session_state['ingestion_review'].confirmed
    assert app.info and next(b for b in app.button if b.label == 'Да, продолжить')


def test_runtime_has_no_corpus_names_or_downstream_dependencies():
    root = Path(__file__).parents[2]
    import json
    corpus = json.loads((root / 'tests/lossless_document/corpus.json').read_text(encoding='utf-8-sig'))
    package = root / 'src/calendar_pedagoga/ingestion_confirmation'
    forbidden_imports = {'matching', 'program_parsing', 'parsing', 'pipeline', 'scheduling',
                         'content_generation', 'docx_generation', 'confirmed_study_plan'}
    for path in package.glob('*.py'):
        source = path.read_text(encoding='utf-8-sig')
        assert all(not re.search(r'(?<!\w)' + re.escape(record['label']) + r'(?!\w)', source, re.I) for record in corpus)
        parsed = ast.parse(source)
        for node in ast.walk(parsed):
            if isinstance(node, ast.ImportFrom):
                assert not (set((node.module or '').split('.')) & forbidden_imports)
            if isinstance(node, ast.Import):
                assert not any(set(alias.name.split('.')) & forbidden_imports for alias in node.names)
    for path in (root / 'src/calendar_pedagoga').glob('*.py'):
        assert 'ingestion_confirmation' not in path.read_text(encoding='utf-8-sig')


def test_year_screen_selects_once_and_preserves_choice(monkeypatch):
    b = structure()
    second = replace(b.year_regions[0], id='another-year', years=(2,))
    model = build_model(replace(b, year_regions=(*b.year_regions, second)))
    app = run_ui(model, monkeypatch)
    app.radio[0].set_value(1).run()
    next(b for b in app.button if b.label == 'Подтвердить год').click().run()
    assert not app.exception and assess(app.session_state['ingestion_review']).state == State.VALID
    assert not app.radio


def test_column_screen_prefills_recognized_values_and_revalidates(monkeypatch):
    b = structure()
    table = b.tables[0]
    plan = table.plans[0]
    mapping = replace(plan.mapping, decision=replace(plan.mapping.decision, status='NEEDS_CONFIRMATION'))
    model = build_model(replace(b, tables=(replace(table, plans=(replace(plan, mapping=mapping),)),)))
    app = run_ui(model, monkeypatch)
    assert [s.value for s in app.selectbox] == ['NUMBER', 'TITLE', 'TOTAL', 'THEORY', 'TRAINING', 'PRACTICE']
    next(b for b in app.button if b.label == 'Подтвердить колонки').click().run()
    assert not app.exception and assess(app.session_state['ingestion_review']).state == State.VALID


def test_boundary_screen_displays_source_and_accepts_explicit_range(monkeypatch):
    b = structure()
    b = replace(b, year_regions=tuple(replace(r, decision=replace(r.decision, status='NEEDS_CONFIRMATION'))
                                     if r.channel == 'CONTENT' else r for r in b.year_regions))
    model = build_model(b)
    app = run_ui(model, monkeypatch)
    boundaries = assess(start(model)).boundaries
    app.multiselect[0].set_value([b.id for b in boundaries]).run()
    next(b for b in app.button if b.label == 'Подтвердить выбранные области').click().run()
    assert not app.exception and assess(app.session_state['ingestion_review']).state == State.VALID


def test_valid_edit_button_opens_only_requested_correction(monkeypatch):
    app = run_ui(build_model(structure()), monkeypatch)
    next(b for b in app.button if b.label == 'Исправить').click().run()
    app.selectbox[0].set_value(State.COLUMN_AMBIGUOUS).run()
    next(b for b in app.button if b.label == 'Открыть исправление').click().run()
    assert not app.exception
    assert assess(app.session_state['ingestion_review']).state == State.COLUMN_AMBIGUOUS
