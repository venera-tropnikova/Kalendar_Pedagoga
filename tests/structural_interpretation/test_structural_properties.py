"""Generated metamorphic cases supplement, never replace, the real corpus."""
from fractions import Fraction
from io import BytesIO
from itertools import permutations
from xml.sax.saxutils import escape
import random
import zipfile
import pytest
from calendar_pedagoga.lossless_document import extract_bytes
from calendar_pedagoga.structural_interpretation import interpret_document
from tools.structural_interpretation.audit import audit_structure

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ROLES = ('NUMBER', 'TITLE', 'TOTAL', 'THEORY', 'TRAINING', 'PRACTICE')
HEADERS = ('№ п/п', 'Название темы', 'Всего', 'Теория', 'Учебно-тренировочные занятия', 'Практика')


def p(text, bold=False, properties=''):
    return '<w:p><w:pPr>' + properties + '</w:pPr><w:r>' + ('<w:rPr><w:b/></w:rPr>' if bold else '') + '<w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>'


def cell(text='', props='', body=None):
    return '<w:tc><w:tcPr>' + props + '</w:tcPr>' + (p(text) if body is None else body) + '</w:tc>'


def row(cells):
    return '<w:tr>' + ''.join(cells) + '</w:tr>'


def package(body, extra=None):
    out = BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr('word/document.xml', '<w:document xmlns:w="' + W + '" xmlns:x="urn:unclassified"><w:body>' + body + '</w:body></w:document>')
        for name, text in (extra or {}).items():
            z.writestr(name, text)
    return out.getvalue()


def table(order=tuple(range(6)), header_depth=1, combined=False, values=None):
    data = values or [('1', 'Тема Альфа', '2', '0,5', '0.5', '1'),
                      ('2', 'Тема Бета', '2', '1/2', '0,5', '1'),
                      ('', 'Итого', '4', '1', '1', '2')]
    if combined:
        order = tuple(c for c in order if c != 0)
        data = [(n, (n + '. ' if n else '') + title, *hours) for n, title, *hours in data]
    rows = []
    if header_depth == 1:
        rows.append(row([cell(HEADERS[c]) for c in order]))
    else:
        assert not combined and order == tuple(range(6))
        rows.append(row([cell(HEADERS[0], '<w:vMerge w:val="restart"/>'),
                         cell(HEADERS[1], '<w:vMerge w:val="restart"/>'),
                         cell('Количество часов', '<w:gridSpan w:val="4"/>')]))
        rows.append(row([cell('', '<w:vMerge/>'), cell('', '<w:vMerge/>')] +
                         [cell(h, '<w:vMerge w:val="restart"/>' if header_depth == 3 else '') for h in HEADERS[2:]]))
        if header_depth == 3:
            rows.append(row([cell('', '<w:vMerge/>') for _ in range(6)]))
    rows.extend(row([cell(values[c]) for c in order]) for values in data)
    return '<w:tbl><w:tblGrid>' + '<w:gridCol w:w="1200"/>' * len(order) + '</w:tblGrid>' + ''.join(rows) + '</w:tbl>'


def interpret(body):
    a = extract_bytes(package(body))
    b = interpret_document(a)
    assert b.source_document is a
    audit_structure(b)
    return b


def only_plan(b):
    return next(p for t in b.tables for p in t.plans)


def shape(b):
    return ([(r.channel, r.years) for r in b.year_regions],
            [(t.classification, [(p.years, p.decision.status,
               [(r.kind, r.key, [(k, v.state, v.value, v.derived_value) for k, v in r.hours]) for r in p.rows]) for p in t.plans]) for t in b.tables],
            [(s.years, s.key, s.origin, s.decision.status) for s in b.content_sections],
            [(s.years, s.kind) for s in b.source_fragments],
            [(r.role, r.years) for r in b.block_roles],
            [(c.years, c.decision.status) for c in b.plan_choices])


ORDERS = random.Random(8723).sample(list(permutations(range(6))), 36)


@pytest.mark.parametrize('order', ORDERS)
def test_column_permutation_preserves_schema_and_exact_fraction_hours(order):
    b = interpret(p('Учебно-тематический план 1 года обучения') + table(order=order))
    plan = only_plan(b)
    assert plan.decision.status == 'SUPPORTED'
    assert {c.role: c.column for c in plan.mapping.columns} == {ROLES[c]: i for i, c in enumerate(order)}
    assert dict(plan.totals)['TOTAL'].value == Fraction(4)
    assert [dict(r.hours)['THEORY'].value for r in plan.rows] == [Fraction(1, 2), Fraction(1, 2), Fraction(1)]
    assert b.plan_choices[0].accepted_id == plan.id


@pytest.mark.parametrize('depth', (1, 2, 3))
def test_header_depth_and_merged_cells(depth):
    b = interpret(p('Учебно-тематический план 1 года обучения') + table(header_depth=depth))
    plan = only_plan(b)
    assert len(b.tables[0].mappings) == 1
    assert len(plan.mapping.header_rows) == depth
    assert plan.decision.status == 'SUPPORTED'
    for col in plan.mapping.columns:
        assert col.header_block_ids and col.decision.evidence


@pytest.mark.parametrize('combined', (False, True))
def test_combined_and_separate_number_title_columns(combined):
    b = interpret(p('Учебно-тематический план 1 года обучения') + table(combined=combined))
    plan = only_plan(b)
    assert plan.mapping.combined_number_title == combined
    assert [r.key for r in plan.rows] == [(1,), (2,), ()]
    assert plan.decision.status == 'SUPPORTED'


def test_numeric_table_and_norms_never_become_a_plan_from_arithmetic_alone():
    numeric = '<w:tbl>' + row([cell('Название'), cell('Всего')]) + row([cell('Альфа'), cell('2')]) + row([cell('Итого'), cell('2')]) + '</w:tbl>'
    norm = '<w:tbl>' + row([cell('Тест'), cell('Мальчики'), cell('Девочки')]) + row([cell('Альфа'), cell('2'), cell('2')]) + '</w:tbl>'
    b = interpret(p('Учебно-тематический план 1 года обучения') + table() + numeric + norm)
    assert [t.classification for t in b.tables] == ['STUDY_PLAN', 'UNKNOWN', 'NORMATIVE_CONTROL']
    assert len(b.plan_choices[0].candidate_ids) == 1
    assert all(not t.plans for t in b.tables[1:])


def test_equal_plans_require_confirmation_and_preserve_both():
    body = p('Учебно-тематический план 1 года обучения') + table()
    b = interpret(body + body)
    assert len(b.plan_choices[0].candidate_ids) == 2
    assert b.plan_choices[0].accepted_id is None
    assert b.plan_choices[0].decision.status == 'NEEDS_CONFIRMATION'
    assert len(b.plan_choices[0].decision.alternatives) == 2


def test_one_changed_hour_is_a_conflict_not_a_new_baseline():
    values = [('1', 'Альфа', '3', '0,5', '0,5', '1'), ('2', 'Бета', '2', '0,5', '0,5', '1'), ('', 'Итого', '4', '1', '1', '2')]
    b = interpret(p('Учебно-тематический план 1 года обучения') + table(values=values))
    assert only_plan(b).decision.status == 'CONFLICT'
    assert any(c.kind.startswith('grand:') and c.decision.status == 'CONFLICT' for c in only_plan(b).arithmetic)
    assert b.plan_choices[0].accepted_id is None


def test_empty_dash_and_explicit_zero_remain_distinct():
    values = [('1', 'Альфа', '2', '', '—', '2'), ('2', 'Бета', '2', '0', '0', '2'), ('', 'Итого', '4', '0', '0', '4')]
    b = interpret(p('Учебно-тематический план 1 года обучения') + table(values=values))
    a, c, _ = only_plan(b).rows
    assert dict(a.hours)['THEORY'].state == 'EMPTY' and dict(a.hours)['THEORY'].value is None
    assert dict(a.hours)['TRAINING'].state == 'DASH' and dict(a.hours)['TRAINING'].value is None
    assert dict(a.hours)['THEORY'].derived_value == 0 and dict(a.hours)['THEORY'].derivation
    assert dict(c.hours)['THEORY'].state == 'NUMBER' and dict(c.hours)['THEORY'].value == 0


def test_positive_unknown_residual_cannot_be_guessed():
    values = [('1', 'Альфа', '2', '', '', '1'), ('', 'Итого', '2', '', '', '1')]
    b = interpret(p('Учебно-тематический план 1 года обучения') + table(values=values))
    assert only_plan(b).decision.status == 'NEEDS_CONFIRMATION'
    assert all(v.derived_value is None for r in only_plan(b).rows for _, v in r.hours)
    assert b.plan_choices[0].accepted_id is None


def test_same_names_in_different_years_and_result_mention_do_not_mix_content():
    body = p('Учебно-тематический план 1 года обучения') + table()
    body += p('Содержание программы 1 года обучения') + p('1. Тема Альфа', True) + p('Первый фрагмент')
    body += p('Учебно-тематический план 2 года обучения') + table()
    body += p('Содержание программы 2 года обучения') + p('1. Тема Альфа', True) + p('Второй фрагмент')
    body += p('Результаты первого года обучения') + p('Умеют перечислять пункты.')
    body += p('Содержание программы') + p('2. Тема Бета', True) + p('Следующий фрагмент второго года')
    b = interpret(body)
    assert [(f.raw_text, f.years) for f in b.source_fragments] == [('Первый фрагмент', (1,)), ('Второй фрагмент', (2,)), ('Следующий фрагмент второго года', (2,))]
    assert any(m.effect == 'RESULTS_ONLY' and m.years == (1,) for m in b.year_mentions)
    assert {s.years for s in b.content_sections} == {(1,), (2,)}
    assert len({s.id for s in b.content_sections}) == len(b.content_sections)


def test_multiple_years_are_alternatives_not_the_last_mention():
    b = interpret(p('Учебно-тематический план 1-го и 2-го года обучения') + table())
    plan = only_plan(b)
    assert plan.years == (1, 2)
    assert plan.decision.status == 'NEEDS_CONFIRMATION'
    assert b.plan_choices[0].accepted_id is None


def test_identical_nested_strings_and_unknown_blocks_are_not_deduplicated():
    body = p('Содержание программы 1 года обучения') + p('1. Альфа', True) + p('Повтор') * 3
    body += '<x:unfamiliar><x:literal>Нераспознанный текст</x:literal></x:unfamiliar>'
    b = interpret(body)
    assert [f.raw_text for f in b.source_fragments] == ['Повтор'] * 3
    assert len({f.id for f in b.source_fragments}) == 3
    assert all(next(r for r in b.block_roles if r.block_id == bid).role == 'UNRESOLVED' for bid in b.source_document.unknown_block_ids)


def test_title_and_embedded_source_are_distinct_entities():
    t = '<w:tbl>' + row([cell(h) for h in HEADERS])
    t += row([cell('1'), cell(body=p('Альфа') + p('1.1 Вложенная практическая работа') + p('Повтор') + p('Повтор')), cell('2'), cell('0'), cell('0'), cell('2')])
    t += row([cell(''), cell('Итого'), cell('2'), cell('0'), cell('0'), cell('2')]) + '</w:tbl>'
    b = interpret(p('Учебно-тематический план 2 года обучения') + t)
    plan = only_plan(b)
    assert plan.rows[0].title == 'Альфа'
    assert len(b.content_sections) == 1 and len(b.source_fragments) == 3
    assert b.content_sections[0].id != plan.rows[0].id
    assert all(f.years == (2,) and f.section_id == b.content_sections[0].id for f in b.source_fragments)


@pytest.mark.parametrize('new_name', ['Астрономия', 'Проект 712', 'Неизвестная дисциплина'])
def test_discipline_rename_does_not_change_structure(new_name):
    body = p('Учебно-тематический план 1 года обучения') + table()
    body += p('Содержание программы «Тема Альфа» 1 года обучения') + p('1. Тема Альфа', True) + p('Работа с материалом.')
    baseline = interpret(body)
    renamed = interpret(body.replace('Тема Альфа', new_name))
    assert shape(baseline) == shape(renamed)


def test_repeat_is_deterministic_and_interpreter_cannot_mutate_stage_a():
    a = extract_bytes(package(p('Учебно-тематический план 1 года обучения') + table()))
    before = repr(a)
    first = interpret_document(a)
    assert first == interpret_document(a)
    assert repr(a) == before
    audit_structure(first)

def test_conflicting_year_caption_keeps_both_scopes_and_evidence():
    b = interpret(p('Учебно-тематический план 1 года обучения') + p('2 год обучения') + table())
    assert only_plan(b).years == (1, 2)
    assert b.plan_choices[0].accepted_id is None
    assert any(r.decision.contradictions for r in b.year_regions)


def test_normative_caption_with_plan_like_numbers_requires_schema_confirmation():
    b = interpret(p('Контрольные нормативы') + table())
    assert b.tables[0].classification == 'UNKNOWN'
    assert b.tables[0].subtype == 'CONFLICTING_SCHEMAS'
    assert not b.plan_choices[0].accepted_id
    assert b.tables[0].decision.alternatives


def test_unsupported_header_preserves_column_and_every_block():
    numeric = '<w:tbl>' + row([cell('Непонятная колонка'), cell('X')]) + row([cell('Повтор'), cell('7')]) + '</w:tbl>'
    b = interpret(numeric)
    assert b.tables[0].classification == 'UNKNOWN'
    assert len(b.block_roles) == len(b.source_document.block_ids)
    assert all(r.role in ('UNKNOWN', 'UNRESOLVED') for r in b.block_roles)


def test_missing_total_is_incomplete_evidence_not_a_fabricated_zero():
    b = interpret(p('Учебно-тематический план 1 года обучения') + table(values=[('1','Альфа','2','0','0','2')]))
    assert only_plan(b).decision.status == 'NEEDS_CONFIRMATION'
    assert only_plan(b).totals == ()
    assert b.plan_choices[0].accepted_id is None

def test_single_year_duration_conflicting_with_explicit_plan_is_not_auto_accepted():
    b = interpret(p('Программа рассчитана на один год обучения.') + p('Учебно-тематический план 2 года обучения') + table())
    assert only_plan(b).decision.status == 'NEEDS_CONFIRMATION'
    assert b.plan_choices[0].accepted_id is None
    assert any(e.code == 'SINGLE_YEAR_DURATION_CONFLICT' for r in b.year_regions for e in r.decision.contradictions)


def test_decimal_numbering_override_has_evidence_and_nested_lists_keep_parent():
    numbering = '<w:numbering xmlns:w="' + W + '"><w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2."/></w:lvl></w:abstractNum><w:num w:numId="5"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/></w:lvlOverride></w:num></w:numbering>'
    def num(level):
        return '<w:numPr><w:ilvl w:val="' + str(level) + '"/><w:numId w:val="5"/></w:numPr>'
    body = p('Содержание программы 1 года обучения') + p('Раздел 1. Альфа')
    body += p('Пункт', properties=num(0)) + p('Вложенный пункт', properties=num(1))
    a = extract_bytes(package(body, {'word/numbering.xml': numbering}))
    b = interpret_document(a)
    audit_structure(b)
    first, nested = b.source_fragments
    assert first.list_level == 0 and nested.list_level == 1 and nested.parent_fragment_id == first.id
    from calendar_pedagoga.structural_interpretation.index import DocumentIndex
    index = DocumentIndex(a)
    assert index.key(a.paragraphs[2]) == (3,)
    assert index.key(a.paragraphs[3]) == (3, 1)
    assert any(span.part_uri == '/word/numbering.xml' for span in index.key_evidence(a.paragraphs[3]).spans)


def test_audit_detects_loss_duplication_invention_and_cross_year_tampering():
    from dataclasses import replace
    b = interpret(p('Содержание программы 1 года обучения') + p('Раздел 1. Альфа') + p('Исходный текст'))
    f = b.source_fragments[0]
    damaged = [replace(b, block_roles=b.block_roles[:-1]),
               replace(b, source_fragments=b.source_fragments + (f,)),
               replace(b, source_fragments=(replace(f, raw_text='Придуманный текст'),)),
               replace(b, source_fragments=(replace(f, years=(2,)),))]
    for item in damaged:
        with pytest.raises(AssertionError):
            audit_structure(item)


def test_stage_b_import_boundary_excludes_old_parser_and_downstream():
    import ast
    from pathlib import Path
    root = Path(__file__).parents[2] / 'src/calendar_pedagoga/structural_interpretation'
    allowed = {'__future__', 'collections', 'dataclasses', 'fractions', 're', 'calendar_pedagoga.lossless_document.models'}
    for path in root.glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level or node.module in allowed, (path, node.module)
            elif isinstance(node, ast.Import):
                assert all(n.name in allowed for n in node.names)

def test_empty_unknown_table_has_source_evidence_and_never_disappears():
    b = interpret('<w:tbl/>')
    assert len(b.tables) == 1 and b.tables[0].classification == 'UNKNOWN'
    assert b.tables[0].decision.evidence[0].block_ids == (b.source_document.tables[0].id,)


def test_combined_hour_semantics_are_not_guessed():
    t = '<w:tbl>' + row([cell('Тема'), cell('Всего'), cell('Теория и практика')])
    t += row([cell('Альфа'), cell('2'), cell('2')]) + row([cell('Итого'), cell('2'), cell('2')]) + '</w:tbl>'
    b = interpret(p('Учебно-тематический план 1 года обучения') + t)
    assert b.tables[0].classification == 'UNKNOWN'
    assert any(c.role == 'UNRESOLVED' and c.decision.alternatives for m in b.tables[0].mappings for c in m.columns)
    assert not any(c.accepted_id for c in b.plan_choices)


def test_single_hour_component_must_also_reconcile_to_total():
    t = '<w:tbl>' + row([cell('Тема'), cell('Всего'), cell('Теория')])
    t += row([cell('Альфа'), cell('2'), cell('1')]) + row([cell('Итого'), cell('2'), cell('1')]) + '</w:tbl>'
    b = interpret(p('Учебно-тематический план 1 года обучения') + t)
    assert only_plan(b).decision.status == 'CONFLICT'
    assert b.plan_choices[0].accepted_id is None

def test_result_table_metadata_is_not_promoted_to_expected_outcomes():
    t = '<w:tbl>' + row([cell('№'), cell('Тема'), cell('Знания'), cell('Умения и навыки')])
    t += row([cell('1'), cell('Альфа'), cell('Знать порядок действий.'), cell('Выполнить работу.')]) + '</w:tbl>'
    b = interpret(p('Результаты 1 года обучения') + t)
    assert b.tables[0].subtype == 'EXPECTED_RESULTS'
    assert [f.raw_text for f in b.expected_results if f.kind == 'EXPECTED_RESULT_TABLE'] == ['Знать порядок действий.', 'Выполнить работу.']


def test_new_plan_year_cannot_leave_unqualified_content_in_previous_year():
    body = p('Содержание программы 1 года обучения') + p('Раздел 1. Альфа') + p('Содержание первого года')
    body += p('Учебно-тематический план 2 года обучения') + table()
    body += p('Содержание программы') + p('Раздел 1. Альфа') + p('Без отдельного маркера года')
    b = interpret(body)
    assert b.source_fragments[-1].years == ()
    assert b.source_fragments[-1].decision.status == 'NEEDS_CONFIRMATION'


def test_plan_scope_provenance_includes_the_single_year_duration_source():
    b = interpret(p('Программа рассчитана на один год обучения.') + p('Учебно-тематический план') + table())
    duration = b.source_document.paragraphs[0].id
    assert duration in {bid for e in only_plan(b).decision.evidence for bid in e.block_ids}


def test_grand_total_evidence_references_numeric_cells():
    b = interpret(p('Учебно-тематический план 1 года обучения') + table())
    plan = only_plan(b)
    grand = next(c for c in plan.arithmetic if c.kind == 'grand:TOTAL')
    total_ids = {bid for r in plan.rows for k,v in r.hours if k == 'TOTAL' for bid in v.source_ids}
    assert total_ids <= set(grand.decision.evidence[0].block_ids)
