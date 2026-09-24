"""Universal, pure confirmation transitions. No parsing, matching or downstream calls."""
from dataclasses import replace
import json

from calendar_pedagoga.lossless_document.models import SourceSpan
from calendar_pedagoga.structural_interpretation.models import Evidence

from .models import (
    Assessment, BindingQuestion, Event, Session, State, canonical,
)
from .projection import (
    boundary_options, content_options, custom_boundary, hour_issues, plan_options,
    reviewed_columns, sources_differ, years,
)


ACTIONS = {State.YEAR_AMBIGUOUS: 'choose_year', State.SOURCE_CONFLICT: 'choose_source',
           State.PLAN_AMBIGUOUS: 'choose_plan', State.COLUMN_AMBIGUOUS: 'map_columns',
           State.CONTENT_BOUNDARY_AMBIGUOUS: 'choose_bounds',
           State.BINDING_AMBIGUOUS: 'bind_topics', State.VALID: 'confirm'}
DEPENDENTS = {'choose_year': ('choose_source', 'choose_plan', 'map_columns', 'choose_bounds', 'bind_topics'),
              'choose_source': ('choose_plan', 'map_columns', 'bind_topics'),
              'choose_plan': ('map_columns', 'bind_topics'),
              'map_columns': ('bind_topics',), 'choose_bounds': ('bind_topics',)}


def answers(session):
    result = {}
    for event in session.events:
        payload = json.loads(event.payload)
        if event.action == 'edit':
            state = State(payload['state'])
            action = ACTIONS[state]
            for key in (action, *DEPENDENTS.get(action, ())):
                result.pop(key, None)
            result['editing'] = state
        elif event.action != 'confirm':
            for key in DEPENDENTS.get(event.action, ()):
                result.pop(key, None)
            result[event.action] = payload
            result.pop('editing', None)
    return result


def start(model, previous=None):
    if previous is None:
        return Session(model)
    if previous.model.fingerprint == model.fingerprint:
        return replace(previous, model=model)
    return Session(model, archived=(*previous.archived, *previous.events))


def assess(session):
    model = session.model
    values = answers(session)
    possible_years = years(model)
    chosen_year = values.get('choose_year', {}).get('year')
    forced = values.get('editing')
    year = chosen_year if chosen_year is not None else possible_years[0] if len(possible_years) == 1 else None
    evidence = tuple(e for source in model.sources for region in source.document.year_regions
                     if region.channel in ('GENERAL', 'PLAN', 'CONTENT') for e in region.decision.evidence)
    if not evidence:
        node = model.program.source_document.nodes[0]
        evidence = (Evidence('DOCUMENT_UNRESOLVED_YEAR', (node.id,), (node.source,), 'explicit year required'),)
    base = Assessment(State.YEAR_AMBIGUOUS, years=possible_years, year=year, evidence=evidence)
    if year is None or forced == State.YEAR_AMBIGUOUS:
        return base
    by_source = {s.kind: plan_options(model, s.kind, year) for s in model.sources}
    available = tuple(k for k, options in by_source.items() if options)
    # Manual input is a fallback, never a generated replacement for a valid plan.
    priority = ('EXTERNAL', 'EMBEDDED', 'MANUAL')
    chosen_source = values.get('choose_source', {}).get('source')
    all_sources = {s.kind: plan_options(model, s.kind, None) for s in model.sources}
    foreign_sources = {kind for kind, options in all_sources.items() if options and not by_source[kind]}
    conflicting = bool(foreign_sources) or any(sources_differ(by_source[left], by_source[right])
                      for i, left in enumerate(available) for right in available[i + 1:])
    base = replace(base, state=State.SOURCE_CONFLICT, sources=available,
                   plans=tuple(p for candidates in (all_sources if foreign_sources else by_source).values() for p in candidates),
                   notice='В одном из источников указан другой год обучения. Можно выбрать план этого года или изменить год.' if foreign_sources else '')
    if forced == State.SOURCE_CONFLICT or (conflicting and chosen_source is None):
        return base
    source = chosen_source or next((k for k in priority if k in available and any(
        p.plan and p.plan.decision.status == 'SUPPORTED' for p in by_source[k])), None)
    source = source or next((k for k in priority if k in available), 'EMBEDDED')
    candidates = by_source.get(source, ())
    chosen_plan = values.get('choose_plan', {}).get('id')
    selected = next((p for p in candidates if p.id == chosen_plan), None)
    if selected is None and len(candidates) == 1 and (candidates[0].plan is not None or
                                                   candidates[0].table.classification == 'STUDY_PLAN'):
        selected = candidates[0]
    base = replace(base, state=State.PLAN_AMBIGUOUS, source=source, plans=candidates, selected=selected)
    if forced == State.PLAN_AMBIGUOUS or selected is None:
        return replace(base, notice='' if candidates else
                       'Учебный план пока не найден. Загрузите план или введите его вручную для проверки.')
    if selected.table.classification != 'STUDY_PLAN' and chosen_plan is None:
        return base
    plan = selected.plan
    column_answer = values.get('map_columns')
    if column_answer:
        plan = reviewed_columns(model, selected, column_answer, year)
    base = replace(base, state=State.COLUMN_AMBIGUOUS, plan=plan)
    if forced == State.COLUMN_AMBIGUOUS or plan is None or plan.mapping.decision.status != 'SUPPORTED':
        return base
    issues = hour_issues(model, selected, plan)
    if issues:
        return replace(base, state=State.HOURS_CONFLICT, hour_issues=issues,
                       evidence=tuple(e for issue in issues for e in issue.evidence))
    boundaries = boundary_options(model, year)
    bound_answer = values.get('choose_bounds')
    if bound_answer:
        boundaries = tuple(custom_boundary(model, year, item['start'], item.get('end'))
                           for item in bound_answer['ranges'])
    regions = [r for r in model.program.year_regions if r.id in {b.id for b in boundaries}]
    uncertain = any(r.years != (year,) or r.decision.status != 'SUPPORTED' for r in regions)
    content = content_options(model, year, boundaries, plan, source)
    base = replace(base, state=State.CONTENT_BOUNDARY_AMBIGUOUS, boundaries=boundaries, content=content)
    if forced == State.CONTENT_BOUNDARY_AMBIGUOUS or (not bound_answer and uncertain) or not content:
        return base
    eligible = {item.id: item for item in content}
    supported = {b.topic_id: b for b in model.program.bindings if b.decision.status == 'SUPPORTED'
                 and b.years == (year,) and b.section_ids and set(b.section_ids) <= eligible.keys()}
    original = {b.topic_id: b for b in model.program.bindings}
    original_rows = {r.id: r for table in model.program.tables for candidate in table.plans for r in candidate.rows}
    confirmed = values.get('bind_topics', {}).get('bindings', {})
    bindings, questions = [], []
    for row in plan.rows:
        if row.kind not in ('TOPIC', 'SECTION'):
            continue
        selected_ids = confirmed.get(row.id)
        original_row = original_rows.get(row.id)
        unchanged = original_row is not None and (row.title_block_ids, row.key, row.hours) == (
            original_row.title_block_ids, original_row.key, original_row.hours)
        binding = supported.get(row.id) if source == 'EMBEDDED' and unchanged else None
        if selected_ids:
            bindings.append((row.id, tuple(selected_ids)))
        elif binding:
            bindings.append((row.id, binding.section_ids))
        else:
            previous = original.get(row.id) if source == 'EMBEDDED' else None
            suggested = tuple(i for i in previous.section_ids if i in eligible) if previous else ()
            evidence = (*previous.decision.evidence, *previous.decision.contradictions) if previous else row.decision.evidence
            total = dict(row.hours).get('TOTAL')
            notes = tuple('В плане: ' + str(total.arithmetic_value) + ' ч; в разделе «' + s.title + '»: ' +
                          str(s.declared_hours) + ' ч. Подтверждается только связь, исходные часы сохраняются.'
                          for s in model.program.content_sections if s.id in suggested and total
                          and s.declared_hours is not None and s.declared_hours != total.arithmetic_value)
            questions.append(BindingQuestion(row.id, row.title, content, suggested, tuple(evidence), notes))
    base = replace(base, state=State.BINDING_AMBIGUOUS, bindings=tuple(bindings), questions=tuple(questions))
    if forced == State.BINDING_AMBIGUOUS:
        pending = {q.topic_id: q for q in questions}
        questions = [pending.get(r.id) or BindingQuestion(r.id, r.title, content, dict(bindings).get(r.id, ()), r.decision.evidence)
                     for r in plan.rows if r.kind in ('TOPIC', 'SECTION')]
        return replace(base, questions=tuple(questions))
    if questions:
        return base
    return replace(base, state=State.VALID)


def apply(session, action, payload=None):
    """Validate every transition, including events replayed from a saved session."""
    payload = payload or {}
    view = assess(session)
    evidence = view.evidence
    if action == 'edit':
        state = State(payload['state'])
        if state not in ACTIONS or state == State.VALID:
            raise ValueError('Это состояние нельзя подтвердить вручную.')
        if state in (State.COLUMN_AMBIGUOUS, State.BINDING_AMBIGUOUS,
                     State.CONTENT_BOUNDARY_AMBIGUOUS) and view.selected is None:
            raise ValueError('Сначала выберите учебный план.')
    else:
        if session.confirmed or ACTIONS.get(view.state) != action:
            raise ValueError('Продолжение заблокировано: сначала завершите текущую проверку.')
        if action == 'choose_year':
            year = payload['year']
            if type(year) is not int or year < 1 or (view.years and year not in view.years):
                raise ValueError('Выберите год обучения из найденных вариантов.')
        elif action == 'choose_source':
            if payload['source'] not in view.sources:
                raise ValueError('Источник плана отсутствует.')
            evidence = tuple(e for p in view.plans if p.kind == payload['source'] for e in p.table.decision.evidence)
        elif action == 'choose_plan':
            if payload['id'] not in {p.id for p in view.plans}:
                raise ValueError('Выберите один из показанных учебных планов.')
            selected = next(p for p in view.plans if p.id == payload['id'])
            evidence = selected.table.decision.evidence
        elif action == 'map_columns':
            plan = reviewed_columns(session.model, view.selected, payload, view.year)
            evidence = plan.mapping.decision.evidence
        elif action == 'choose_bounds':
            boundaries = [custom_boundary(session.model, view.year, r['start'], r.get('end'))
                          for r in payload['ranges']]
            if not boundaries:
                raise ValueError('Укажите хотя бы одну область содержания.')
            blocks = [b for boundary in boundaries for b in boundary.block_ids]
            if len(blocks) != len(set(blocks)):
                raise ValueError('Выбранные области содержания пересекаются.')
            if not content_options(session.model, view.year, boundaries, view.plan, view.source):
                raise ValueError('В выбранной области нет текста содержания.')
            evidence = tuple(e for boundary in boundaries for e in boundary.evidence)
        elif action == 'bind_topics':
            bindings = payload['bindings']
            if set(bindings) != {q.topic_id for q in view.questions}:
                raise ValueError('Подтвердите связи для всех показанных тем одним действием.')
            for question in view.questions:
                selected = bindings[question.topic_id]
                allowed = {item.id for item in question.options}
                if not selected or len(selected) != len(set(selected)) or not set(selected) <= allowed:
                    raise ValueError('Для каждой темы выберите разделы содержания выбранного года.')
            evidence = tuple(e for q in view.questions for e in (*q.evidence,
                             *(e for option in q.options if option.id in bindings[q.topic_id] for e in option.evidence)))
            # Preserve earlier, still applicable group confirmations as well.
            previous = answers(session).get('bind_topics', {}).get('bindings', {})
            payload = {'bindings': {**previous, **bindings}}
        elif action == 'confirm':
            evidence = (*view.plan.decision.evidence, *(e for b in view.boundaries for e in b.evidence))
    event = Event(action, canonical(payload), session.model.fingerprint, tuple(evidence))
    result = replace(session, events=(*session.events, event))
    if result.confirmed and assess(result).state != State.VALID:
        raise ValueError('Нельзя подтвердить незавершённую структуру.')
    return result


def save(session):
    """JSON only. The caller controls transient storage; no filesystem or pickle."""
    return canonical({'version': 1, 'fingerprint': session.model.fingerprint,
                      'events': session.events, 'archived': session.archived})


def _read_event(data):
    evidence = tuple(Evidence(e['code'], tuple(e['block_ids']),
                     tuple(SourceSpan(s['part_uri'], tuple(s['element_path']), s.get('char_start'), s.get('char_end'))
                           for s in e['spans']), e['observed']) for e in data.get('evidence', ()))
    return Event(data['action'], data['payload'], data['fingerprint'], evidence)


def restore(model, snapshot):
    data = json.loads(snapshot)
    if data.get('version') != 1:
        raise ValueError('Неизвестная версия сохранённого подтверждения.')
    if data.get('fingerprint') != model.fingerprint:
        # Old records are audit-only. Never apply them to changed source data.
        archived = tuple(_read_event(e) for e in (*data.get('archived', ()), *data.get('events', ())))
        return Session(model, archived=archived)
    session = Session(model, archived=tuple(_read_event(e) for e in data.get('archived', ())))
    for event in data['events']:
        if event['fingerprint'] != model.fingerprint:
            raise ValueError('Подтверждение относится к другой версии источника.')
        session = apply(session, event['action'], json.loads(event['payload']))
    return session
