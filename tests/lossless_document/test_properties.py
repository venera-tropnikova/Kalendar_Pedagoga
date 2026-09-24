"""Supplemental generated fixtures; never substitutes for the real corpus gate."""
from io import BytesIO
import random
import zipfile

import pytest

from calendar_pedagoga.lossless_document import ExtractionError, detect_format, extract_bytes, extract_document
from calendar_pedagoga.lossless_document.formats import parse_xml
from tools.lossless_document.audit import audit, structure_digest

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def package(body, extra=None, namespace=W):
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        'word/document.xml': f'<w:document xmlns:w="{namespace}" xmlns:x="urn:unknown"><w:body>{body}</w:body></w:document>',
    }
    parts.update(extra or {})
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, value in parts.items():
            z.writestr(name, value)
    return stream.getvalue()


def p(text):
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def cell(text, properties=''):
    return f'<w:tc><w:tcPr>{properties}</w:tcPr>{p(text)}</w:tc>'


@pytest.mark.parametrize('seed', range(24))
def test_generated_order_multiplicity_raw_identity_and_rename(seed, tmp_path):
    rng = random.Random(seed)
    values = ['  Повтор  ', 'Строка', '1-й год обучения', '2 год обучения', 'А\u00a0Б']
    content, expected = [], []
    for _ in range(25):
        raw = rng.choice(values)
        if rng.randrange(3) == 0:
            content.append('<w:tbl><w:tblGrid><w:gridCol/></w:tblGrid><w:tr>' + cell(raw) + '</w:tr></w:tbl>')
        elif rng.randrange(2):
            content.append('<w:sdt><w:sdtContent>' + p(raw) + '</w:sdtContent></w:sdt>')
        else:
            content.append(p(raw))
        expected.append(raw)
    data = package(''.join(content))
    first = extract_bytes(data)
    audit(first)
    assert [item.raw_text for item in first.paragraphs] == expected
    assert first == extract_bytes(data)
    for name in ('arbitrary.bin', 'other.DOC', 'renamed.docx'):
        path = tmp_path / name
        path.write_bytes(data)
        assert extract_document(path) == first
    assert len(set(first.block_ids)) == len(first.block_ids)


def test_nested_tables_textboxes_unknown_and_auxiliary_parts():
    nested = '<w:tbl><w:tr>' + cell('nested') + '</w:tr></w:tbl>'
    body = '<w:tbl><w:tr><w:tc>' + p('outer') + nested + p('tail') + '</w:tc></w:tr></w:tbl>'
    body += '<w:p><w:r><w:t>before</w:t><w:drawing><w:txbxContent>' + p('textbox') + '</w:txbxContent></w:drawing><w:t>after</w:t></w:r></w:p>'
    body += '<x:future><x:payload>unclassified</x:payload>' + p('preserved') + '</x:future>'
    data = package(body, {'word/header1.xml': f'<w:hdr xmlns:w="{W}">{p("header")}</w:hdr>',
                          'word/footnotes.xml': f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="1">{p("footnote")}</w:footnote></w:footnotes>',
                          'word/opaque.bin': b'\x00opaque\xff'})
    document = extract_bytes(data)
    audit(document)
    assert [p.raw_text for p in document.paragraphs] == ['outer','nested','tail','beforeafter','textbox','preserved','footnote','header']
    assert document.counts()['unknown_blocks'] == 1
    assert any(n.text == 'unclassified' for n in document.nodes)
    assert next(p for p in document.parts if p.name == 'word/opaque.bin').data == b'\x00opaque\xff'
    outer, inner = document.tables
    assert len(outer.physical_cells) == len(inner.physical_cells) == 1
    assert document.paragraphs[1].cell.table_id == inner.id


def test_multiline_headers_gridspan_vertical_and_legacy_horizontal_merge():
    body = '<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid>'
    body += '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + cell('a', '<w:gridSpan w:val="2"/><w:vMerge w:val="restart"/>') + cell('b') + '</w:tr>'
    body += '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + cell('continuation text', '<w:gridSpan w:val="2"/><w:vMerge/>') + cell('c') + '</w:tr>'
    body += '<w:tr>' + cell('left','<w:hMerge w:val="restart"/>') + cell('right','<w:hMerge/>') + cell('end') + '</w:tr></w:tbl>'
    document = extract_bytes(package(body))
    audit(document)
    table = document.tables[0]
    assert len(table.repeated_header_row_ids) == 2
    assert len(table.physical_cells) == 7
    assert len(table.logical_cells) == 5
    assert [c.grid_slots for c in table.logical_cells if c.merged] == [((0,0),(0,1),(1,0),(1,1)),((2,0),(2,1))]
    assert 'continuation text' in [p.raw_text for p in document.paragraphs]
    assert not document.diagnostics


def test_numbering_style_inheritance_disabled_list_and_controls():
    styles = f'<w:styles xmlns:w="{W}"><w:style w:styleId="Base" w:type="paragraph"><w:pPr><w:numPr><w:numId w:val="8"/><w:ilvl w:val="2"/></w:numPr></w:pPr></w:style><w:style w:styleId="Child" w:type="paragraph"><w:basedOn w:val="Base"/></w:style></w:styles>'
    body = '<w:p><w:pPr><w:pStyle w:val="Child"/></w:pPr><w:r><w:t> A </w:t><w:tab/><w:t>B</w:t><w:br/><w:t>C</w:t></w:r></w:p>'
    body += '<w:p><w:pPr><w:pStyle w:val="Child"/><w:numPr><w:numId w:val="0"/></w:numPr></w:pPr><w:r><w:t>plain</w:t></w:r></w:p>'
    document = extract_bytes(package(body, {'word/styles.xml':styles,'word/numbering.xml':f'<w:numbering xmlns:w="{W}"><w:num w:numId="8"><w:abstractNumId w:val="4"/></w:num></w:numbering>'}))
    audit(document)
    listed, plain = document.paragraphs
    assert listed.raw_text == ' A \tB\nC'
    assert listed.normalized_text == 'A B C'
    assert (listed.numbering.num_id, listed.numbering.level, listed.numbering.inherited_from_style) == ('8','2',True)
    assert plain.numbering is None


def test_field_instructions_revisions_and_drawing_properties_are_separate():
    body = '<w:p><w:r><w:instrText> PAGE </w:instrText><w:delText>removed</w:delText><w:t>2</w:t><w:drawing><x:position>200</x:position></w:drawing></w:r></w:p>'
    document = extract_bytes(package(body))
    audit(document)
    assert document.paragraphs[0].raw_text == ' PAGE removed2'
    assert document.fragments[-1].raw_text == '200'
    assert document.fragments[-1].role == 'opaque_xml_text'


def test_year_candidates_are_evidence_only_and_same_text_has_distinct_ids():
    document = extract_bytes(package(p('I год обучения')+p('год обучения: 2')+p('1-й год обучения')+p('I год обучения')))
    assert len(document.year_candidates) == 4
    assert len({y.id for y in document.year_candidates}) == 4
    audit(document)
    assert all('candidate_only_no_content_assignment' in y.evidence for y in document.year_candidates)


def test_strict_ooxml_namespace_and_opaque_parts():
    document = extract_bytes(package(p('strict'), {'customXml/item.xml':b'not XML'}, namespace='http://purl.oclc.org/ooxml/wordprocessingml/main'))
    audit(document)
    assert document.paragraphs[0].raw_text == 'strict'
    assert [d.code for d in document.diagnostics] == ['OPAQUE_XML_PART']


@pytest.mark.parametrize('bad', [b'', b'not a program', b'PK\x03\x04garbage', bytes.fromhex('d0cf11e0a1b11ae1')+b'\0'*600])
def test_signature_errors_are_technical(bad):
    with pytest.raises(ExtractionError):
        detect_format(bad)


def test_extension_is_not_format_and_malformed_xml_does_not_get_guessed():
    assert detect_format(package(p('text'))) == 'DOCX'
    with pytest.raises(ExtractionError):
        extract_bytes(package('<w:p>'))
    with pytest.raises(ExtractionError):
        parse_xml(b'<!DOCTYPE x [<!ENTITY a "bad">]><x>&a;</x>')


def test_unsafe_and_duplicate_zip_entries_rejected():
    for name in ('../escape', '/absolute'):
        with pytest.raises(ExtractionError):
            detect_format(package(p('text'), {name:b'bad'}))
    stream = BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('same', '1')
        with pytest.warns(UserWarning):
            z.writestr('same','2')
    with pytest.raises(ExtractionError,match='Duplicate'):
        detect_format(stream.getvalue())


def test_malformed_merges_are_retained_and_reported():
    document = extract_bytes(package('<w:tbl><w:tr>'+cell('a','<w:vMerge/>')+cell('b','<w:hMerge/>')+'</w:tr></w:tbl>'))
    audit(document)
    assert [p.raw_text for p in document.paragraphs] == ['a','b']
    assert {d.code for d in document.diagnostics} == {'ORPHAN_VERTICAL_MERGE','ORPHAN_HORIZONTAL_MERGE'}


def test_repacking_changes_source_identity_but_not_structure():
    first=package(p('text'))
    second=package(p('text'),{'irrelevant.bin':b'keep'})
    a,b=extract_bytes(first),extract_bytes(second)
    assert a.id != b.id
    assert structure_digest(a) == structure_digest(b)

@pytest.mark.parametrize('seed',range(16))
def test_random_rectangular_merge_partitions_keep_each_physical_text_once(seed):
    rng=random.Random(seed)
    rows,cols=5,6
    taken=set(); rectangles=[]
    for r in range(rows):
        for c in range(cols):
            if (r,c) in taken:continue
            width=rng.randint(1,min(3,cols-c));height=rng.randint(1,min(3,rows-r))
            while any((i,j) in taken for i in range(r,r+height) for j in range(c,c+width)):
                if width>1:width-=1
                else:height-=1
            slots={(i,j) for i in range(r,r+height) for j in range(c,c+width)}
            taken|=slots;rectangles.append((r,c,width,height,slots))
    body='<w:tbl><w:tblGrid>'+('<w:gridCol/>'*cols)+'</w:tblGrid>'
    expected_physical=[]
    for r in range(rows):
        body+='<w:tr>'
        for index,(start,col,width,height,slots) in sorted(enumerate(rectangles),key=lambda v:v[1][1]):
            if not start<=r<start+height:continue
            merge=(f'<w:vMerge w:val="{"restart" if start==r else "continue"}"/>' if height>1 else '')
            text=f'cell {index} row {r}'
            body+=cell(text,f'<w:gridSpan w:val="{width}"/>'+merge)
            expected_physical.append(text)
        body+='</w:tr>'
    body+='</w:tbl>'
    document=extract_bytes(package(body));audit(document)
    assert [p.raw_text for p in document.paragraphs]==expected_physical
    assert sorted(c.grid_slots for c in document.tables[0].logical_cells)==sorted(tuple(sorted(item[4])) for item in rectangles)
    assert not document.diagnostics


def test_nested_paragraph_tabstop_is_not_a_text_tab():
    inner='<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr><w:r><w:t>inside</w:t></w:r></w:p>'
    document=extract_bytes(package('<w:p><w:r><w:txbxContent>'+inner+'</w:txbxContent></w:r></w:p>'))
    audit(document)
    assert [p.raw_text for p in document.paragraphs]==['','inside']


def test_alternate_representations_remain_separate_source_objects():
    body='<x:wrapper xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><mc:AlternateContent><mc:Choice Requires="x">'+p('same')+'</mc:Choice><mc:Fallback>'+p('same')+'</mc:Fallback></mc:AlternateContent></x:wrapper>'
    document=extract_bytes(package(body));audit(document)
    assert [b.kind for b in document.alternative_branches]==['Choice','Fallback']
    assert document.paragraphs[0].id!=document.paragraphs[1].id
    assert len(document.paragraphs)==2


def test_no_foreign_story_or_style_leakage_into_paragraph_raw_text():
    body='<w:p><w:r><w:t>source</w:t><x:layout>999</x:layout></w:r></w:p>'
    document=extract_bytes(package(body))
    assert document.paragraphs[0].raw_text=='source'
    assert any(n.raw_text=='999' and n.normalized_text=='999' for n in document.nodes)


def test_package_limits_are_enforced(monkeypatch):
    import calendar_pedagoga.lossless_document.formats as formats
    data=package(p('text'))
    monkeypatch.setattr(formats,'MAX_PARTS',1)
    with pytest.raises(ExtractionError,match='resource limit'):
        extract_bytes(data)


def test_expansion_limits_do_not_silently_truncate(monkeypatch):
    import calendar_pedagoga.lossless_document.reader as reader
    monkeypatch.setattr(reader,'MAX_XML_NODES',3)
    with pytest.raises(ExtractionError,match='XML node resource limit'):
        extract_bytes(package(p('preserved or rejected technically')))
    monkeypatch.setattr(reader,'MAX_XML_NODES',300000)
    monkeypatch.setattr(reader,'MAX_GRID_SLOTS',2)
    with pytest.raises(ExtractionError,match='Table grid resource limit'):
        extract_bytes(package('<w:tbl><w:tr>'+cell('text','<w:gridSpan w:val="3"/>')+'</w:tr></w:tbl>'))
