from io import BytesIO
from types import SimpleNamespace

from docx import Document
import pytest

from calendar_pedagoga import docx_qa as qa
from calendar_pedagoga.docx_generation import (
    _allow_row_split, _prevent_row_split, _repeat_table_header_rows,
    _merge_month_cells_by_page_segments, _columns_for_table,
)


def test_vertical_minimum_tracks_text_font_and_margins_without_changing_layout():
    from calendar_pedagoga.docx_generation import _protect_vertical_cell_height
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    doc = Document()
    table = doc.add_table(rows=5, cols=2)
    table.style = 'Table Grid'
    for row, text, size in zip(table.rows[2:], ('7\n01–07.03', '28\n15–21.03', '28\n15–21.03'), (12, 12, 18)):
        cell = row.cells[1]
        cell.text = text
        cell.paragraphs[0].runs[0].font.size = Pt(size)
        direction = OxmlElement('w:textDirection')
        direction.set(qn('w:val'), 'btLr')
        cell._tc.get_or_add_tcPr().append(direction)
    before = [[cell._tc.xml for cell in row.cells] for row in table.rows]
    _protect_vertical_cell_height(table)
    heights = [int(row._tr.trPr.find(qn('w:trHeight')).get(qn('w:val'))) for row in table.rows[2:]]
    assert heights[0] == heights[1]
    assert heights[2] > heights[1] > 45.89 * 20
    assert all(row._tr.trPr.find(qn('w:trHeight')).get(qn('w:hRule')) == 'atLeast' for row in table.rows[2:])
    assert before == [[cell._tc.xml for cell in row.cells] for row in table.rows]
    assert not table.rows[0]._tr.xpath('./w:trPr/w:trHeight')
    xml = table._tbl.xml
    _protect_vertical_cell_height(table)
    assert table._tbl.xml == xml


def test_horizontal_cells_do_not_receive_vertical_minimum():
    from calendar_pedagoga.docx_generation import _protect_vertical_cell_height
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    table.cell(2, 1).text = 'Long horizontal content ' * 10
    _protect_vertical_cell_height(table)
    assert not table._tbl.xpath('.//w:trHeight')


@pytest.mark.parametrize('direction', ['btLr', 'tbRl'])
def test_vertical_minimum_uses_actual_text_and_inherited_padding(direction):
    from calendar_pedagoga.docx_generation import _protect_vertical_cell_height
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    cell = table.cell(2, 0)
    cell.text = 'Май'
    cell.paragraphs[0].runs[0].font.size = Pt(12)
    cell.paragraphs[0].paragraph_format.space_after = Pt(3)
    cell.paragraphs[0].paragraph_format.line_spacing = 1.2
    marker = OxmlElement('w:textDirection')
    marker.set(qn('w:val'), direction)
    cell._tc.get_or_add_tcPr().append(marker)
    def height():
        row = table.rows[2]
        for old in row._tr.xpath('./w:trPr/w:trHeight'):
            old.getparent().remove(old)
        _protect_vertical_cell_height(table)
        return int(row._tr.trPr.find(qn('w:trHeight')).get(qn('w:val')))
    short = height()
    cell.paragraphs[0].runs[0].text = 'Сентябрь / Октябрь'
    long = height()
    assert long > short
    margins = OxmlElement('w:tblCellMar')
    left = OxmlElement('w:left')
    left.set(qn('w:w'), '200')
    margins.append(left)
    table._tbl.tblPr.append(margins)
    inherited_left = int(table.style.element.xpath('./w:tblPr/w:tblCellMar/w:left')[0].get(qn('w:w')))
    assert height() == long + 200 - inherited_left
    assert cell.paragraphs[0].paragraph_format.space_after == Pt(3)
    assert cell.paragraphs[0].paragraph_format.line_spacing == 1.2


def test_allow_split_removes_only_pagination_constraints():
    doc = Document()
    table = doc.add_table(rows=3, cols=8)
    _repeat_table_header_rows(table)
    row = table.rows[2]
    row.cells[2].text = 'Unchanged content'
    _prevent_row_split(row)
    row.cells[2].paragraphs[0].paragraph_format.page_break_before = True
    _allow_row_split(row)
    assert not row._tr.xpath('.//w:cantSplit | .//w:pageBreakBefore')
    assert row.cells[2].text == 'Unchanged content'
    assert len(table._tbl.xpath('.//w:tblHeader')) == 2


def test_merge_does_not_bridge_excluded_split_row():
    doc = Document()
    table = doc.add_table(rows=5, cols=8)
    for row in table.rows[2:]:
        row.cells[0].text = 'Сентябрь'
    _merge_month_cells_by_page_segments(
        table, _columns_for_table(table), ('Сентябрь',) * 3, ((0, 2),),
    )
    assert not table._tbl.xpath('.//w:vMerge')


def _source():
    doc = Document()
    table = doc.add_table(rows=4, cols=3)
    for row, values in zip(table.rows[2:], [('Month', '19', 'abcdef'), ('Month', '20', 'gh')]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    result = BytesIO()
    doc.save(result)
    return result.getvalue()


def _pdf(monkeypatch, fragments):
    import pymupdf
    class Pdf:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            for rows in fragments:
                table = SimpleNamespace(col_count=3, extract=lambda rows=rows: [[], []] + rows)
                yield SimpleNamespace(find_tables=lambda table=table: SimpleNamespace(tables=[table]))
    monkeypatch.setattr(pymupdf, 'open', lambda **kwargs: Pdf())


def test_pdf_measures_continuation_and_monthly_week_numbers(monkeypatch):
    _pdf(monkeypatch, [[['Month', '19', 'abc']], [['', '', 'def'], ['Month', '20', 'gh']]])
    assert qa._data_row_page_spans_pdf(_source(), b'pdf', 2) == (
        qa.DataRowPageSpan(1, 2, True), qa.DataRowPageSpan(2, 2, True),
    )


@pytest.mark.parametrize('month,week', [('Mon', '19'), ('Month', '1'), ('', '')])
def test_split_with_incomplete_identifier_is_unsafe(monkeypatch, month, week):
    _pdf(monkeypatch, [[[month, week, 'abc']], [['', '', 'def'], ['Month', '20', 'gh']]])
    spans = qa._data_row_page_spans_pdf(_source(), b'pdf', 2)
    assert spans[0] == qa.DataRowPageSpan(1, 2, False)
    assert spans[1] == qa.DataRowPageSpan(2, 2, True)


def test_identifiers_may_be_intact_on_continuation_page(monkeypatch):
    _pdf(monkeypatch, [[['', '', 'abc']], [['Month', '19', 'def'], ['Month', '20', 'gh']]])
    assert qa._data_row_page_spans_pdf(_source(), b'pdf', 2)[0].split_safe


@pytest.mark.parametrize('safe', [True, False])
def test_generation_protects_only_unsafe_split_and_never_forces_break(monkeypatch, safe):
    from calendar_pedagoga import docx_generation as gen
    def populate(document, *args, **kwargs):
        table = document.add_table(rows=4, cols=8)
        _repeat_table_header_rows(table)
        for row in table.rows[2:]:
            row.cells[0].text = 'Month'
        return table, _columns_for_table(table), ('Month', 'Month')
    monkeypatch.setattr(gen, '_load_template', lambda template: Document())
    monkeypatch.setattr(gen, '_populate_calendar_table', populate)
    initial = (qa.DataRowPageSpan(1, 1, True), qa.DataRowPageSpan(1, 2, safe))
    final = initial if safe else (qa.DataRowPageSpan(1, 1, True), qa.DataRowPageSpan(2, 2, True))
    calls = iter([initial, final, final])
    monkeypatch.setattr(qa, 'detect_data_row_page_spans', lambda *args, **kwargs: next(calls))
    doc = Document(BytesIO(gen.generate_calendar_docx(
        None, (None, None), SimpleNamespace(uses_organization_template=False), '2026–2027')))
    assert not doc._element.xpath('.//w:pageBreakBefore')
    assert bool(doc.tables[0].rows[3]._tr.xpath('./w:trPr/w:cantSplit')) is not safe


@pytest.mark.parametrize('fragments', [
    [[['Month', '19', 'wrong']]],
    [[['Month', '19', 'abc']]],
    [[['Month', '19', 'abcdef'], ['Month', '20', 'gh'], ['Month', '20', 'gh']]],
])
def test_pdf_refuses_missing_changed_or_duplicate_content(monkeypatch, fragments):
    _pdf(monkeypatch, fragments)
    assert qa._data_row_page_spans_pdf(_source(), b'pdf', 2) is None


def test_word_measurement_uses_all_cells_and_both_ends(monkeypatch):
    monkeypatch.setattr(qa, '_docx_to_pdf_bytes', lambda content: None)
    class Range:
        Start = 0
        End = 10
        def __init__(self, start, end): self.pages = (start, end)
        @property
        def Duplicate(self): return Range(*self.pages)
        def Collapse(self, direction): self.page = self.pages[0 if direction == 1 else 1]
        def Information(self, code): return self.page
    cells = [SimpleNamespace(RowIndex=3, ColumnIndex=column, Range=Range(1, end))
             for column, end in [(1, 8), (2, 1), (3, 2)]]
    document = SimpleNamespace(Repaginate=lambda: None,
        Tables=lambda index: SimpleNamespace(Range=SimpleNamespace(Cells=cells)))
    monkeypatch.setattr(qa, '_run_with_word_document', lambda content, callback: callback(None, document))
    assert qa.detect_data_row_page_spans(b'docx', total_rows=1) == (qa.DataRowPageSpan(1, 2),)


@pytest.mark.parametrize('measurement_available', [True, False])
def test_monthly_restores_deleted_merge_anchor_and_respects_new_pages(monkeypatch, measurement_available):
    from calendar_pedagoga import ui
    from calendar_pedagoga.docx_generation import _merge_month_cell_group, _save_document
    doc = Document()
    table = doc.add_table(rows=7, cols=8)
    _repeat_table_header_rows(table)
    for row, week in zip(table.rows[2:], range(5, 10)):
        row.cells[0].text = 'Октябрь'
        row.cells[1].text = f'{week}\nДата {week}'
        row.cells[2].text = f'Topic {week}'
        row.cells[4].text = f'Practice {week}'
    _merge_month_cell_group(table.rows[2:], _columns_for_table(table), 'Октябрь')
    annual = _save_document(doc)
    header = [row._tr.xml for row in table.rows[:2]]
    spans = tuple(qa.DataRowPageSpan(page, page, True) for page in (1, 1, 2, 2))
    monkeypatch.setattr(ui, 'detect_data_row_page_spans',
                        lambda *args, **kwargs: spans if measurement_available else None)
    monthly = Document(BytesIO(ui._monthly_plan_docx(annual, '2026–2027', 2026, 10)))
    rows = monthly.tables[0].rows[2:]
    assert [row.cells[1].text for row in rows] == [f'{week}\nДата {week}' for week in range(6, 10)]
    assert [row.cells[0].text for row in rows] == ['Октябрь'] * 4
    assert [row.cells[2].text for row in rows] == [f'Topic {week}' for week in range(6, 10)]
    assert [row._tr.xml for row in monthly.tables[0].rows[:2]] == header
    assert not monthly._element.xpath('.//w:pageBreakBefore')
    # Row 8 starts page 2: its physical month cell must contain its own label.
    assert rows[2]._tr.tc_lst[0].xpath('.//w:t/text()') == ['Октябрь']
    assert _save_document(doc) == annual


def test_monthly_restores_week_merge_without_losing_multiple_parts(monkeypatch):
    from calendar_pedagoga import ui
    from calendar_pedagoga.docx_generation import _save_document
    doc = Document()
    table = doc.add_table(rows=5, cols=8)
    for row, week, part in zip(table.rows[2:], (7, 7, 8), ('A', 'B', 'C')):
        row.cells[0].text = 'Октябрь'
        row.cells[1].text = str(week)
        row.cells[2].text = part
    table.cell(2, 1).merge(table.cell(3, 1)).text = '7'
    monkeypatch.setattr(ui, 'detect_data_row_page_spans', lambda *args, **kwargs: None)
    monthly = Document(BytesIO(ui._monthly_plan_docx(_save_document(doc), '2026–2027', 2026, 10)))
    rows = monthly.tables[0].rows[2:]
    assert [row.cells[1].text for row in rows] == ['7', '7', '8']
    assert [row.cells[2].text for row in rows] == ['A', 'B', 'C']
    assert not monthly._element.xpath('.//w:vMerge')
