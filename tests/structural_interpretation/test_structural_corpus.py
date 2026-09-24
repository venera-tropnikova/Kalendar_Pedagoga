"""The complete real corpus is mandatory; missing files or source drift fail."""
from io import BytesIO
import json
import os
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET
import pytest
from calendar_pedagoga.lossless_document import extract_bytes, extract_document
from calendar_pedagoga.structural_interpretation import interpret_document
from tools.lossless_document.corpus import corpus_paths
from tools.structural_interpretation.audit import audit_structure
from tools.structural_interpretation.corpus import report_document, markdown_report, semantic_shape


@pytest.fixture(scope='module')
def real_corpus():
    manifest = Path(__file__).parents[1] / 'lossless_document/corpus.json'
    result = {}
    for record in corpus_paths(manifest):
        a = extract_document(record['path'])
        b = interpret_document(a)
        result[record['id']] = (record, a, b)
    return result


@pytest.mark.parametrize('name', ['key', 'tour', 'climb', 'nature', 'orientation'])
def test_source_contract_and_all_a_blocks_are_preserved(real_corpus, name):
    record, a, b = real_corpus[name]
    packet = report_document(name, record['path'], a, b)
    assert packet['checks']['blocks_a'] == packet['checks']['blocks_b']
    assert b.source_document is a
    assert b == interpret_document(a)


# Names belong exclusively to QA input transformations, never runtime rules.
RENAMES = {
    'key': r'ключ',
    'tour': r'туристы\s*[–—-]\s*проводники',
    'climb': r'скалолазани[еяю]',
    'nature': r'природн(?:ым|ого|ый)\s+материал(?:ом|а)?',
    'orientation': r'ориентировани[еяю]',
}


def rename_xml_text(data, pattern):
    output = BytesIO()
    replacements = 0
    w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    with zipfile.ZipFile(BytesIO(data)) as original, zipfile.ZipFile(output, 'w') as modified:
        for member in original.infolist():
            content = original.read(member)
            if member.filename == 'word/document.xml':
                root = ET.fromstring(content)
                # Preserve runs/properties and XML coordinates, including split-run names.
                for paragraph in root.iter(w + 'p'):
                    texts = list(paragraph.iter(w + 't'))
                    before = ''.join(t.text or '' for t in texts)
                    after, count = re.subn(pattern, 'Предмет', before, flags=re.I)
                    if count:
                        replacements += count
                        lengths = [len(t.text or '') for t in texts]
                        offset = 0
                        for i, text in enumerate(texts):
                            end = offset + lengths[i] if i < len(texts) - 1 else len(after)
                            text.text = after[offset:end]
                            offset = end
                content = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            modified.writestr(member, content)
    assert replacements > 0, 'QA rename did not modify any source text'
    return output.getvalue()


@pytest.mark.parametrize('name', list(RENAMES))
def test_real_discipline_rename_preserves_structural_decisions(real_corpus, name):
    record, a, b = real_corpus[name]
    renamed = extract_bytes(rename_xml_text(a.package_bytes, RENAMES[name]))
    changed = interpret_document(renamed)
    audit_structure(changed)
    assert semantic_shape(b) == semantic_shape(changed)


def test_complete_evidence_packet(real_corpus):
    output = Path(os.environ.get('KP_STRUCTURAL_REPORT', '_shadow_out/structural_stage_b'))
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, (record, a, b) in real_corpus.items():
        packet = report_document(name, record['path'], a, b)
        records.append(packet)
        (output / (name + '.json')).write_text(json.dumps(packet, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'report.md').write_text(markdown_report(records), encoding='utf-8')
    assert len(records) == 5
