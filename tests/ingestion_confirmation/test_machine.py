from dataclasses import replace
from fractions import Fraction
from itertools import permutations
import json
import random

import pytest
from calendar_pedagoga.ingestion_confirmation import State, apply, assess, build_model, restore, save, start
from calendar_pedagoga.ingestion_confirmation.models import Session
from calendar_pedagoga.ingestion_confirmation.projection import plan_options
from conftest import structure, p, table, interpret


def review(document=None, **kwargs):
    return start(build_model(document or structure(), **kwargs))


def change_plan(b, transform):
    first = b.tables[0]
    return replace(b, tables=(replace(first, plans=tuple(transform(p) for p in first.plans)), *b.tables[1:]))


def assert_kept(session, original):
    assert session.model.program is original
    assert session.model.program.source_document is original.source_document
    assert [r.block_id for r in original.block_roles] == list(original.source_document.block_ids)


ORDERS = random.Random(17393).sample(list(permutations(range(6))), 24)


@pytest.mark.parametrize('order', ORDERS)
def test_every_valid_needs_one_confirmation_after_column_permutation(order):
    b = structure(order=order)
    session = review(b)
    assert assess(session).state == State.VALID
    done = apply(session, 'confirm')
    assert done.confirmed and len(done.events) == 1
    assert_kept(done, b)
    assert restore(done.model, save(done)) == done


@pytest.mark.parametrize('depth,merged', [(1, False), (2, False), (3, False), (1, True)])
@pytest.mark.parametrize('year', [1, 2, 7])
def test_header_merge_and_year_mutations_preserve_one_click(depth, merged, year):
    session = review(structure(depth=depth, merged=merged, year=year))
    assert assess(session).state == State.VALID
    assert assess(session).year == year
    assert apply(session, 'confirm').confirmed


@pytest.mark.parametrize('delta', [-2, -1, 1, 2])
def test_hour_mutations_block_every_confirmation_and_report_exact_difference(delta):
    session = review(structure(hours_delta=delta))
    view = assess(session)
    assert view.state == State.HOURS_CONFLICT
    assert any(i.difference == -delta for i in view.hour_issues)
    for action in ['confirm', 'bind_topics', 'choose_bounds', 'choose_plan']:
        with pytest.raises(ValueError):
            apply(session, action, {})
    assert not restore(session.model, save(session)).confirmed
    fixed = start(build_model(structure()), session)
    assert apply(fixed, 'confirm').confirmed


@pytest.mark.parametrize('count', [2, 3, 5])
def test_equivalent_candidate_count_never_auto_selects(count):
    session = review(structure(count=count))
    view = assess(session)
    assert view.state == State.PLAN_AMBIGUOUS and len(view.plans) == count
    for option in view.plans:
        selected = apply(session, 'choose_plan', {'id': option.id})
        assert assess(selected).state == State.VALID
        assert apply(selected, 'confirm').confirmed


def test_multiple_years_then_keep_year_and_ask_only_remaining_questions():
    b = interpret(p('Учебно-тематический план 1 года обучения') + table() +
                  p('Содержание программы 1 года обучения', bold=True) + p('1. Тема Альфа', bold=True) + p('Текст.') +
                  p('Учебно-тематический план 2 года обучения') + table())
    session = review(b)
    assert assess(session).state == State.YEAR_AMBIGUOUS
    chosen = apply(session, 'choose_year', {'year': 1})
    assert assess(chosen).year == 1
    assert assess(chosen).state != State.YEAR_AMBIGUOUS
    assert restore(chosen.model, save(chosen)) == chosen
    assert_kept(chosen, b)


def test_unknown_year_requires_explicit_positive_value_without_rejection():
    b = structure()
    b = replace(b, year_regions=tuple(replace(r, years=()) for r in b.year_regions))
    b = change_plan(b, lambda plan: replace(plan, years=()))
    session = review(b)
    assert assess(session).state == State.YEAR_AMBIGUOUS
    chosen = apply(session, 'choose_year', {'year': 4})
    assert assess(chosen).year == 4
    for year in [0, -1, True, 1.5]:
        with pytest.raises(ValueError):
            apply(session, 'choose_year', {'year': year})


def test_column_uncertainty_is_a_separate_state_and_mapping_is_revalidated():
    b = change_plan(structure(), lambda plan: replace(plan, mapping=replace(plan.mapping,
                    decision=replace(plan.mapping.decision, status='NEEDS_CONFIRMATION'))))
    session = review(b)
    assert assess(session).state == State.COLUMN_AMBIGUOUS
    columns = {'roles': ['NUMBER', 'TITLE', 'TOTAL', 'THEORY', 'TRAINING', 'PRACTICE'], 'header_count': 1}
    mapped = apply(session, 'map_columns', columns)
    assert assess(mapped).state == State.VALID
    assert_kept(mapped, b)
    with pytest.raises(ValueError):
        apply(session, 'map_columns', {**columns, 'roles': ['TOTAL'] * 6})
    assert restore(mapped.model, save(mapped)) == mapped


@pytest.mark.parametrize('status', ['NEEDS_CONFIRMATION', 'CONFLICT'])
def test_boundary_uncertainty_preserved_until_explicit_range_confirmation(status):
    b = structure()
    regions = tuple(replace(r, decision=replace(r.decision, status=status)) if r.channel == 'CONTENT' else r
                    for r in b.year_regions)
    session = review(replace(b, year_regions=regions))
    view = assess(session)
    assert view.state == State.CONTENT_BOUNDARY_AMBIGUOUS
    resolved = apply(session, 'choose_bounds', {'ranges': [{'start': r.start, 'end': r.end} for r in view.boundaries]})
    assert assess(resolved).state == State.VALID
    assert restore(resolved.model, save(resolved)) == resolved


def test_unknown_boundary_is_selectable_using_original_blocks():
    b = structure(boundary=False)
    session = review(b)
    assert assess(session).state == State.CONTENT_BOUNDARY_AMBIGUOUS
    paragraphs = [p for p in b.source_document.paragraphs if p.cell is None]
    changed = apply(session, 'choose_bounds', {'ranges': [{'start': paragraphs[1].id, 'end': None}]})
    assert assess(changed).state == State.BINDING_AMBIGUOUS
    assert_kept(changed, b)


def test_grouped_binding_confirmation_preserves_all_fragments():
    b = structure()
    b = replace(b, bindings=tuple(replace(x, decision=replace(x.decision, status='NEEDS_CONFIRMATION')) for x in b.bindings))
    session = review(b)
    view = assess(session)
    assert view.state == State.BINDING_AMBIGUOUS and len(view.questions) == 2
    with pytest.raises(ValueError):
        apply(session, 'bind_topics', {'bindings': {view.questions[0].topic_id: list(view.questions[0].suggested_ids)}})
    changed = apply(session, 'bind_topics', {'bindings': {q.topic_id: list(q.suggested_ids) for q in view.questions}})
    assert assess(changed).state == State.VALID
    assert_kept(changed, b)
    assert apply(changed, 'confirm').confirmed
    assert restore(changed.model, save(changed)) == changed


def test_cross_year_boundary_and_binding_rejected():
    b = structure()
    b = replace(b, year_regions=tuple(replace(r, years=(2,)) if r.channel == 'CONTENT' else r for r in b.year_regions),
                content_sections=tuple(replace(s, years=(2,)) for s in b.content_sections))
    session = apply(review(b), 'choose_year', {'year': 1})
    view = assess(session)
    assert view.state == State.CONTENT_BOUNDARY_AMBIGUOUS
    with pytest.raises(ValueError, match='другого года'):
        apply(session, 'choose_bounds', {'ranges': [{'start': b.content_sections[0].heading_block_id, 'end': None}]})


def test_source_conflict_shows_both_sources_and_requires_explicit_choice():
    embedded = structure()
    external = structure(label='Другое название')
    session = review(embedded, external=external)
    view = assess(session)
    assert view.state == State.SOURCE_CONFLICT
    assert {p.kind for p in view.plans} == {'EMBEDDED', 'EXTERNAL'}
    chosen = apply(session, 'choose_source', {'source': 'EMBEDDED'})
    assert assess(chosen).source == 'EMBEDDED' and assess(chosen).state == State.VALID
    assert apply(chosen, 'confirm').confirmed
    assert_kept(chosen, embedded)
    assert chosen.model.source('EXTERNAL').document is external


def test_equivalent_external_preferred_despite_permuted_columns():
    session = review(structure(), external=structure(order=(5, 1, 4, 3, 2, 0)))
    assert assess(session).source == 'EXTERNAL'
    assert assess(session).state == State.BINDING_AMBIGUOUS
    # No automatic cross-document topic matching is performed in C.
    assert session.model.program is not session.model.source('EXTERNAL').document


def test_embedded_plan_requires_no_external_file():
    session = review()
    assert assess(session).source == 'EMBEDDED'
    assert apply(session, 'confirm').confirmed


def test_manual_input_is_only_used_when_no_recognized_plan_exists():
    no_plan = interpret(p('Содержание программы 1 года обучения', bold=True) + p('1. Раздел', bold=True) + p('Текст.'))
    session = review(no_plan, manual=structure())
    assert assess(session).source == 'MANUAL'
    assert assess(session).state == State.BINDING_AMBIGUOUS


def test_source_or_interpretation_change_invalidates_decisions():
    session = apply(review(), 'confirm')
    assert start(session.model, session) == session
    for b in [structure(text='Изменённый исходный текст.'),
              replace(session.model.program, schema_version='structural-interpretation/new')]:
        model = build_model(b)
        changed = start(model, session)
        assert not changed.confirmed and not changed.events
        assert changed.archived == session.events
        assert not restore(model, save(session)).confirmed
        assert not restore(model, save(session)).events


def test_snapshot_cannot_forge_confirmation_past_hours_gate():
    session = review(structure(hours_delta=1))
    data = json.loads(save(session))
    data['events'] = [{'action': 'confirm', 'payload': '{}', 'fingerprint': session.model.fingerprint}]
    with pytest.raises(ValueError):
        restore(session.model, json.dumps(data))


def test_editing_invalidates_dependent_answers_and_rechecks():
    session = apply(review(), 'confirm')
    editing = apply(session, 'edit', {'state': State.COLUMN_AMBIGUOUS.value})
    assert not editing.confirmed
    assert assess(editing).state == State.COLUMN_AMBIGUOUS
    assert_kept(editing, session.model.program)


def test_titles_are_opaque_data_for_state_machine():
    for title in ['Новая дисциплина', 'Совершенно иной предмет', 'Αλφα 123', 'Повторяющееся название']:
        session = review(structure(label=title))
        assert assess(session).state == State.VALID
        assert apply(session, 'confirm').confirmed


def test_equal_column_candidates_in_one_table_are_column_ambiguity():
    b = structure()
    t = b.tables[0]
    candidate = t.plans[0]
    other = replace(candidate, id=candidate.id + ':alternative',
                    mapping=replace(candidate.mapping, id=candidate.mapping.id + ':alternative'))
    b = replace(b, tables=(replace(t, plans=(candidate, other), mappings=(candidate.mapping, other.mapping)),))
    session = review(b)
    assert assess(session).state == State.COLUMN_AMBIGUOUS
    assert len(session.model.program.tables[0].plans) == 2


def test_foreign_year_external_source_is_reported_not_silently_ignored():
    session = review(structure(year=1), external=structure(year=2))
    assert assess(session).state == State.YEAR_AMBIGUOUS
    session = apply(session, 'choose_year', {'year': 1})
    assert assess(session).state == State.SOURCE_CONFLICT
    assert {p.kind for p in assess(session).plans} == {'EXTERNAL', 'EMBEDDED'}
    with pytest.raises(ValueError):
        apply(session, 'choose_source', {'source': 'EXTERNAL'})
    selected = apply(session, 'choose_source', {'source': 'EMBEDDED'})
    assert assess(selected).state == State.VALID


@pytest.mark.parametrize('offset', [0, 1, 2])
def test_boundary_positions_are_explicit_selections_never_drop_original_blocks(offset):
    b = structure(boundary=False)
    session = review(b)
    paragraphs = [p for p in b.source_document.paragraphs if p.cell is None]
    selected = apply(session, 'choose_bounds', {'ranges': [{'start': paragraphs[1 + offset].id, 'end': None}]})
    assert_kept(selected, b)
    assert assess(selected).boundaries[0].start == paragraphs[1 + offset].id
    assert not selected.confirmed


def test_results_year_reference_does_not_change_selected_content_year():
    b = structure()
    result_only = replace(b.year_regions[0], id='result-reference', channel='RESULTS', years=(4,))
    b = replace(b, year_regions=(*b.year_regions, result_only))
    session = review(b)
    assert assess(session).year == 1 and assess(session).state == State.VALID


def test_unresolved_and_empty_hours_cannot_pass_even_with_supported_cached_status():
    b = structure()
    def erase(plan):
        r = plan.rows[0]
        role, value = r.hours[0]
        r = replace(r, hours=((role, replace(value, state='EMPTY', value=None, derived_value=None)), *r.hours[1:]))
        return replace(plan, rows=(r, *plan.rows[1:]))
    session = review(change_plan(b, erase))
    assert assess(session).state == State.HOURS_CONFLICT
    with pytest.raises(ValueError):
        apply(session, 'confirm')


def test_archived_confirmation_evidence_survives_json_roundtrip():
    done = apply(review(), 'confirm')
    changed = start(build_model(structure(text='Новый текст')), done)
    assert restore(changed.model, save(changed)) == changed
    assert restore(changed.model, save(done)).archived == done.events


def test_unknown_table_requires_explicit_choice_then_column_confirmation():
    b = structure()
    unknown = replace(b.tables[0], classification='UNKNOWN', plans=(), mappings=())
    session = review(replace(b, tables=(unknown,)))
    view = assess(session)
    assert view.state == State.PLAN_AMBIGUOUS
    selected = apply(session, 'choose_plan', {'id': view.plans[0].id})
    assert assess(selected).state == State.COLUMN_AMBIGUOUS
    mapped = apply(selected, 'map_columns', {'header_count': 1,
                    'roles': ['NUMBER', 'TITLE', 'TOTAL', 'THEORY', 'TRAINING', 'PRACTICE']})
    assert assess(mapped).state == State.BINDING_AMBIGUOUS


def test_confirmation_evidence_references_original_blocks_and_spans():
    b = structure()
    b = replace(b, bindings=tuple(replace(x, decision=replace(x.decision, status='NEEDS_CONFIRMATION')) for x in b.bindings))
    session = review(b)
    session = apply(session, 'bind_topics', {'bindings': {q.topic_id: list(q.suggested_ids) for q in assess(session).questions}})
    session = apply(session, 'confirm')
    nodes = {n.id: n for n in b.source_document.nodes}
    for event in session.events:
        assert event.evidence and event.actor == 'USER' and event.confidence == 1.0
        for evidence in event.evidence:
            assert evidence.block_ids and evidence.spans
            assert set(evidence.block_ids) <= nodes.keys()
            assert all(any(s.part_uri == n.source.part_uri and s.element_path == n.source.element_path
                           for n in nodes.values()) for s in evidence.spans)


def test_opaque_fractional_hours_remain_exact_after_review():
    from calendar_pedagoga.lossless_document import extract_bytes
    from calendar_pedagoga.structural_interpretation import interpret_document
    b = structure()
    same = interpret_document(extract_bytes(b.source_document.original_bytes))
    assert build_model(b).fingerprint == build_model(same).fingerprint
    done = apply(review(b), 'confirm')
    assert start(build_model(same), done).confirmed
    assert dict(assess(done).plan.rows[0].hours)['THEORY'].value == Fraction(1, 2)


def test_plan_content_hour_disagreement_is_visible_without_rewriting_source():
    b = structure()
    section = replace(b.content_sections[0], declared_hours=Fraction(9))
    binding = b.bindings[0]
    b = replace(b, content_sections=(section, *b.content_sections[1:]),
                bindings=(replace(binding, decision=replace(binding.decision, status='NEEDS_CONFIRMATION')), *b.bindings[1:]))
    session = review(b)
    view = assess(session)
    assert view.state == State.BINDING_AMBIGUOUS
    assert view.questions[0].notes and '9' in view.questions[0].notes[0]
    selected = apply(session, 'bind_topics', {'bindings': {q.topic_id: list(q.suggested_ids) for q in view.questions}})
    assert selected.model.program.content_sections[0].declared_hours == 9
    assert dict(assess(selected).plan.rows[0].hours)['TOTAL'].value == 2


def test_confirmed_boundary_never_includes_fragments_beyond_its_end():
    b = structure()
    session = apply(review(b), 'edit', {'state': State.CONTENT_BOUNDARY_AMBIGUOUS.value})
    section = b.content_sections[0]
    fragment = next(f for f in b.source_fragments if f.section_id == section.id)
    changed = apply(session, 'choose_bounds', {'ranges': [{'start': section.heading_block_id, 'end': fragment.block_id}]})
    view = assess(changed)
    assert all(fragment.block_id not in option.block_ids for option in view.content)
    assert fragment in changed.model.program.source_fragments


def test_changed_column_meaning_invalidates_automatic_old_bindings():
    session = apply(review(), 'edit', {'state': State.COLUMN_AMBIGUOUS.value})
    # Same totals but swapped component meanings remain a deliberate review action.
    mapped = apply(session, 'map_columns', {'header_count': 1,
                   'roles': ['NUMBER', 'TITLE', 'TOTAL', 'TRAINING', 'THEORY', 'PRACTICE']})
    assert assess(mapped).state == State.BINDING_AMBIGUOUS
    assert not mapped.confirmed


def test_table_without_plan_schema_can_be_marked_explicitly_but_never_auto_accepted():
    b = structure()
    reference = replace(b.tables[0], classification='REFERENCE', plans=(), mappings=())
    session = review(replace(b, tables=(reference,)))
    assert assess(session).state == State.PLAN_AMBIGUOUS
    option = assess(session).plans[0]
    assert option.plan is None
    chosen = apply(session, 'choose_plan', {'id': option.id})
    assert assess(chosen).state == State.COLUMN_AMBIGUOUS


def test_explicit_binding_edit_exposes_known_and_unknown_bindings_together():
    b = structure()
    b = replace(b, bindings=(replace(b.bindings[0], decision=replace(b.bindings[0].decision,
                                      status='NEEDS_CONFIRMATION')), *b.bindings[1:]))
    session = review(b)
    assert len(assess(session).questions) == 1
    editing = apply(session, 'edit', {'state': State.BINDING_AMBIGUOUS.value})
    assert len(assess(editing).questions) == 2
