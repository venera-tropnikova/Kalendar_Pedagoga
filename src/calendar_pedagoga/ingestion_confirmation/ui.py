"""One teacher-facing renderer for every document. Not connected to the app."""
from html import escape

from . import SHADOW_ENABLED
from .machine import apply, assess, start
from .models import ROLE_LABELS, SOURCE_LABELS, State
from .projection import table_preview



def _review_style(st):
    # Scoped by module use: only the explicitly enabled confirmation shadow renders this CSS.
    st.markdown("""<style>
[data-testid="stHeader"] {display: none;}
[data-testid="stMainBlockContainer"] {padding: 2rem 2rem 3rem; max-width: 1120px;}
[data-testid="stHeading"] h1 {font-size: 2rem; line-height: 1.25; padding: 0 0 1rem;}
[data-testid="stWidgetLabel"] p, [role="option"] {white-space: normal; overflow-wrap: anywhere;}
[data-testid="stMultiSelectTagsContainer"] {height: auto; max-width: 100%;}
[data-testid="stMultiSelect"] [data-tag] {height: auto; max-width: 100%; align-items: flex-start;}
[data-testid="stMultiSelect"] [data-tag] > span[title] {
  white-space: normal !important; overflow: visible !important;
  text-overflow: clip !important; overflow-wrap: anywhere;
}
[data-testid="stText"] {white-space: pre-wrap; overflow-wrap: anywhere;}
[data-testid="stButton"] button {height: auto; min-height: 2.5rem; white-space: normal;}
[data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th {white-space: normal; overflow-wrap: anywhere;}
.review-mobile-cards {display: none;}
.review-card {border: 1px solid #dfe3e8; border-radius: 8px; padding: 12px; margin: 0 0 10px;}
.review-card dl {margin: 0; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 6px 12px;}
.review-card dt {color: #586573; font-weight: 500;}
.review-card dd {margin: 0; overflow-wrap: anywhere; white-space: pre-wrap;}
.review-card .review-card-title {grid-column: 1 / -1; font-weight: 600; margin: 0 0 6px;}
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] {padding: 1rem 1rem 2rem;}
  [data-testid="stHeading"] h1 {font-size: 1.5rem; line-height: 1.25; padding-bottom: .7rem;}
  [data-testid="stDataFrame"] {display: none !important;}
  .review-mobile-cards {display: block;}
  [data-testid="stSelectbox"] button {height: auto; min-height: 2.5rem; white-space: normal;}
}
</style>""", unsafe_allow_html=True)


def _responsive_table(st, records):
    # Both layouts use the same values; CSS chooses presentation, never model state.
    st.dataframe(records, hide_index=True, use_container_width=True)
    cards = []
    for record in records:
        items = list(record.items())
        if not items:
            continue
        title, value = items[0]
        cells = ['<dt class="review-card-title">' + escape(str(title)) + ': ' + escape(str(value)) + '</dt>']
        cells += ['<dt>' + escape(str(k)) + '</dt><dd>' + escape(str(v)) + '</dd>' for k, v in items[1:]]
        cards.append('<article class="review-card"><dl>' + ''.join(cells) + '</dl></article>')
    st.markdown('<div class="review-mobile-cards">' + ''.join(cards) + '</div>', unsafe_allow_html=True)


def _raw_table(st, preview):
    _responsive_table(st, [{'Строка': i, **{'Колонка ' + str(c): value for c, value in enumerate(row, 1)}}
                           for i, row in enumerate(preview, 1)])


def number(value):
    if value is None:
        return 'не определено'
    if value.denominator == 1:
        return str(value.numerator)
    return str(value.numerator) + '/' + str(value.denominator)


def plan_differences(plans):
    """Exact positional comparison for display; does not bind or match topics."""
    snapshots = []
    for option in plans:
        if option.plan is None:
            snapshots.append({('Таблица', 'Структура'): 'Назначение колонок требует уточнения'})
            continue
        snapshot = {('План', 'Год обучения'): ', '.join(map(str, option.plan.years)) or 'не определён'}
        for i, row in enumerate(option.plan.rows, 1):
            snapshot[(f'Строка {i}', 'Тема / раздел')] = row.title
            for role, value in row.hours:
                rendered = number(value.arithmetic_value)
                if value.state != 'NUMBER':
                    rendered = (value.raw_text or 'пусто') + ' (' + rendered + ')'
                snapshot[(f'Строка {i}', ROLE_LABELS[role])] = rendered
        snapshots.append(snapshot)
    keys = dict.fromkeys(key for snapshot in snapshots for key in snapshot)
    result = []
    for row, field in keys:
        values = [snapshot.get((row, field), 'нет строки / колонки') for snapshot in snapshots]
        if len(set(values)) > 1:
            result.append({'Позиция': row, 'Отличие': field,
                           **{f'Вариант {i}': v for i, v in enumerate(values, 1)}})
    return result


def _plans(st, model, plans):
    if len(plans) > 1:
        differences = plan_differences(plans)
        if differences:
            st.write('Различия вариантов')
            st.caption('Строки сравниваются по порядку в планах; это не сопоставление тем с содержанием.')
            _responsive_table(st, differences)
        else:
            st.info('Значения совпадают, но это разные таблицы. Выберите нужную.')
    for i, option in enumerate(plans, 1):
        st.write(f'Вариант {i} · {SOURCE_LABELS[option.kind]}')
        if option.plan:
            if option.plan.years:
                st.caption('Год обучения: ' + ', '.join(map(str, option.plan.years)))
            records = []
            for row in option.plan.rows:
                record = {'Тема / раздел': row.title}
                record.update({ROLE_LABELS[role]: number(value.arithmetic_value) for role, value in row.hours})
                records.append(record)
            _responsive_table(st, records)
        else:
            _raw_table(st, table_preview(model, option))


def render_confirmation(session):
    """Render exactly one state; return a new immutable session on a user action."""
    import streamlit as st

    _review_style(st)
    view = assess(session)
    key = session.model.fingerprint + ':' + str(len(session.events))
    if session.confirmed:
        st.success('Структура подтверждена. Решения сохранены для этой версии документов.')
        return session
    if session.archived and not session.events:
        st.info('Документ изменился. Прежние подтверждения отменены; проверьте обновлённую структуру.')
    action, payload = None, None
    edit_area = None
    if view.state == State.VALID:
        topics = sum(row.kind == 'TOPIC' for row in view.plan.rows)
        total = dict(view.plan.totals).get('TOTAL')
        st.write('Мы нашли:')
        st.write(f'— год обучения: {view.year};')
        st.write(f'— учебный план: {topics} тем, {number(total.arithmetic_value if total else None)} часов;')
        st.write(f'— содержание программы: {len(view.content)} разделов.')
        st.write('Всё верно?')
        if st.button('Да, продолжить', key=key + ':confirm', type='primary'):
            action = 'confirm'
    elif view.state == State.YEAR_AMBIGUOUS:
        st.write('Выберите год обучения')
        if view.years:
            year = st.radio('Год обучения', view.years, index=None, key=key + ':year')
        else:
            year = st.number_input('Год обучения', min_value=1, value=None, step=1, key=key + ':year')
        if st.button('Подтвердить год', disabled=year is None, key=key + ':year-ok', type='primary'):
            action, payload = 'choose_year', {'year': year}
    elif view.state == State.SOURCE_CONFLICT:
        st.write('Источники учебного плана различаются. Сравните темы и часы, затем выберите источник.')
        if view.notice:
            st.warning(view.notice)
        source = st.radio('Какой план использовать?', view.sources, format_func=SOURCE_LABELS.get,
                          index=None, key=key + ':source')
        if st.button('Использовать выбранный источник', disabled=source is None, key=key + ':source-ok', type='primary'):
            action, payload = 'choose_source', {'source': source}
        _plans(st, session.model, view.plans)
    elif view.state == State.PLAN_AMBIGUOUS:
        st.write('Уточните учебный план')
        if view.notice:
            st.info(view.notice)
        labels = {p.id: f'Вариант {i}' for i, p in enumerate(view.plans, 1)}
        chosen = st.radio('Учебный план', tuple(labels), format_func=labels.get, index=None, key=key + ':plan')
        if st.button('Подтвердить план', disabled=chosen is None, key=key + ':plan-ok', type='primary'):
            action, payload = 'choose_plan', {'id': chosen}
        _plans(st, session.model, view.plans)
    elif view.state == State.COLUMN_AMBIGUOUS:
        st.write('Уточните назначение колонок')
        preview = table_preview(session.model, view.selected)
        mapping = view.plan.mapping if view.plan else (
            view.selected.table.mappings[0] if len(view.selected.table.mappings) == 1 else None)
        known = {c.column: c for c in mapping.columns} if mapping else {}
        width = len(preview[0]) if preview else 0
        roles = [None] * width
        unresolved = [col for col in range(width) if col not in known or
                      known[col].role not in ROLE_LABELS or known[col].decision.status != 'SUPPORTED']

        def column_input(col):
            recognized = known.get(col)
            role = recognized.role if recognized and recognized.role in ROLE_LABELS else None
            labels = tuple(ROLE_LABELS)
            return st.selectbox(f'Колонка {col + 1}', labels,
                                index=labels.index(role) if role else None, format_func=ROLE_LABELS.get,
                                placeholder='Выберите назначение', key=key + ':column:' + str(col))

        for col in unresolved:
            sample = ' / '.join(row[col] for row in preview[:len(mapping.header_rows) if mapping else 1] if row[col])
            if sample:
                st.caption('В шапке: ' + sample)
            roles[col] = column_input(col)
        with st.expander('Шапка и распознанные колонки'):
            header_count = st.number_input('Число строк шапки', min_value=1, max_value=max(1, len(preview) - 1),
                                          value=len(mapping.header_rows) if mapping else 1, key=key + ':headers')
            for col in range(width):
                if col not in unresolved:
                    roles[col] = column_input(col)
        if st.button('Подтвердить колонки', disabled=any(r is None for r in roles), key=key + ':columns-ok', type='primary'):
            action, payload = 'map_columns', {'roles': roles, 'header_count': header_count}
        with st.expander('Исходная таблица'):
            _raw_table(st, preview)
    elif view.state == State.HOURS_CONFLICT:
        st.error('Часы не согласованы. Продолжение заблокировано до исправления.')
        st.button('Да, продолжить', disabled=True, key=key + ':blocked')
        edit_area = st.container()
        _responsive_table(st, [{'Проверка': i.label, 'В документе': number(i.declared),
                               'По расчёту': number(i.calculated), 'Разница (расчёт − документ)': number(i.difference)}
                              for i in view.hour_issues])
        st.info('Исправьте часы или неясные значения в исходном плане и загрузите обновлённый документ. '
                'Если выбрана другая таблица или неверно распознаны колонки, нажмите «Исправить».')
    elif view.state == State.CONTENT_BOUNDARY_AMBIGUOUS:
        st.write('Уточните начало и конец содержания программы для выбранного года')
        paragraphs = {p.id: p.raw_text for p in session.model.program.source_document.paragraphs}
        if view.boundaries:
            labels = {b.id: paragraphs.get(b.start, 'Область содержания') for b in view.boundaries}
            chosen = st.multiselect('Найденные области содержания', tuple(labels), format_func=labels.get,
                                   placeholder='Выберите области содержания', key=key + ':boundaries')
            for boundary in view.boundaries:
                with st.expander(labels[boundary.id]):
                    for bid in boundary.block_ids:
                        if paragraphs.get(bid):
                            st.text(paragraphs[bid])
            if st.button('Подтвердить выбранные области', disabled=not chosen, key=key + ':bounds-ok', type='primary'):
                action, payload = 'choose_bounds', {'ranges': [{'start': b.start, 'end': b.end}
                                                for b in view.boundaries if b.id in chosen]}
        with st.expander('Указать границы по тексту'):
            blocks = session.model.program.source_document.block_ids
            labels = {b: f'{i + 1}. {paragraphs.get(b, "Таблица или другой блок")}' for i, b in enumerate(blocks)}
            begin = st.selectbox('Начало содержания', blocks, format_func=labels.get, index=None, key=key + ':begin')
            end = st.selectbox('Первый блок после содержания', (*blocks, None),
                               format_func=lambda b: labels.get(b, 'Конец документа'), index=None, key=key + ':end')
            to_end = st.checkbox('Содержание продолжается до конца документа', key=key + ':to-end')
            if st.button('Подтвердить границы', disabled=begin is None or (end is None and not to_end), key=key + ':range-ok'):
                action, payload = 'choose_bounds', {'ranges': [{'start': begin, 'end': None if to_end else end}]}
    elif view.state == State.BINDING_AMBIGUOUS:
        st.write('Сопоставьте темы учебного плана с разделами содержания')
        st.caption('Подтвердите все показанные связи одним действием.')
        if view.bindings:
            st.success('Подтверждено связей: ' + str(len(view.bindings)) + '. Они сохранены и не требуют повторного выбора.')
            with st.expander('Посмотреть подтверждённые связи'):
                titles = {r.id: r.title for r in view.plan.rows}
                sections = {s.id: s.title for s in view.content}
                for topic_id, section_ids in view.bindings:
                    st.write(titles[topic_id] + ' → ' + '; '.join(sections[s] for s in section_ids))
        st.write('Требуют подтверждения: ' + str(len(view.questions)))
        chosen = {}
        for i, question in enumerate(view.questions):
            for note in question.notes:
                st.warning(note)
            labels = {o.id: f'{j + 1}. {o.title}' for j, o in enumerate(question.options)}
            chosen[question.topic_id] = st.multiselect(question.title, tuple(labels), format_func=labels.get,
                                        default=list(question.suggested_ids), placeholder='Выберите разделы содержания', key=key + ':binding:' + str(i))
            with st.expander('Посмотреть фрагменты', expanded=False):
                paragraphs = {p.id: p.raw_text for p in session.model.program.source_document.paragraphs}
                for option in question.options:
                    if option.id not in chosen[question.topic_id]:
                        continue
                    st.write(labels[option.id])
                    for bid in option.block_ids:
                        if paragraphs.get(bid):
                            st.text(paragraphs[bid])
        if st.button('Подтвердить все связи', disabled=any(not v for v in chosen.values()), key=key + ':bindings-ok', type='primary'):
            action, payload = 'bind_topics', {'bindings': chosen}
    with edit_area if edit_area is not None else st.container():
        if st.button('Исправить', key=key + ':edit'):
            st.session_state[key + ':editing'] = True
        if st.session_state.get(key + ':editing'):
            choices = {State.YEAR_AMBIGUOUS: 'Год обучения', State.SOURCE_CONFLICT: 'Источник плана',
                       State.PLAN_AMBIGUOUS: 'Учебный план'}
            if view.selected:
                choices.update({State.COLUMN_AMBIGUOUS: 'Колонки плана',
                                State.CONTENT_BOUNDARY_AMBIGUOUS: 'Границы содержания',
                                State.BINDING_AMBIGUOUS: 'Связи тем и разделов'})
            selected = st.selectbox('Что исправить?', tuple(choices), format_func=choices.get,
                                   index=None, key=key + ':edit-what')
            if st.button('Открыть исправление', disabled=selected is None, key=key + ':edit-ok'):
                action, payload = 'edit', {'state': selected.value}
    if action:
        try:
            return apply(session, action, payload)
        except (ValueError, KeyError, TypeError) as error:
            st.error(str(error))
    return session


def render_shadow(model, *, enabled=SHADOW_ENABLED):
    import streamlit as st
    if not enabled:
        st.info('Проверка структуры отключена.')
        return None
    current = start(model, st.session_state.get('ingestion_review'))
    st.session_state['ingestion_review'] = current
    updated = render_confirmation(current)
    if updated is not current:
        st.session_state['ingestion_review'] = updated
        st.rerun()
    return current
