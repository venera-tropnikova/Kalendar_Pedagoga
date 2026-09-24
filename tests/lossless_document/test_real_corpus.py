"""Mandatory real corpus: absent or changed inputs FAIL, never skip."""
import json
import os
from pathlib import Path
import pytest

from tools.lossless_document.corpus import verify_corpus


@pytest.fixture(scope='module')
def corpus_result(tmp_path_factory):
    report=verify_corpus(Path(__file__).with_name('corpus.json'),tmp_path_factory.mktemp('real_lossless_corpus'))
    target=Path(os.environ.get('KP_INGESTION_REPORT','_shadow_out/lossless_stage_a_report.json'))
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


@pytest.mark.parametrize('name',['key','tour','climb','nature','orientation'])
def test_real_document_has_no_unexplained_losses_or_duplicates(corpus_result,name):
    record=next(d for d in corpus_result['documents'] if d['id']==name)
    assert record['losses']==record['unexplained_duplicates']==[]
    assert record['checks']['unexplained_losses']==record['checks']['unexplained_duplicates']==0
    assert record['checks']['repeat_ids_and_structure']
    assert record['checks']['renamed_ids_and_structure']
    assert record['checks']['no_year_assignment']
    if record['format']=='DOC':
        assert record['word_witness']['doc_and_docx_equal']
        assert record['doc_conversion_comparison']['ordered_nonempty_text_equal']
        assert record['conversion_log'][0]['status']=='converted'


def test_entire_corpus_is_one_gate(corpus_result):
    assert len(corpus_result['documents'])==5
    assert corpus_result['status']=='PASS'
