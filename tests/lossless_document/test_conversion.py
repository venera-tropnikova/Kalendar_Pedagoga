"""Converter failure paths and real timeout cleanup, independent of downstream."""
from hashlib import sha256
from pathlib import Path
import tempfile

import pytest
from calendar_pedagoga.lossless_document import ConversionError, LibreOfficeConverter, detect_format

SOURCE=Path(__file__).resolve().parents[2]/'references'/'Программа КЛЮЧ.DOC'


def test_doc_signature_and_missing_backend_have_a_failure_journal(tmp_path):
    data=SOURCE.read_bytes()
    assert detect_format(data)=='DOC'
    converter=LibreOfficeConverter(tmp_path/'missing-office')
    with pytest.raises(ConversionError) as caught:
        converter.convert(data)
    event=caught.value.event
    assert event.status=='failed'
    assert event.input_sha256==sha256(data).hexdigest()
    assert event.output_sha256 is None
    assert event.elapsed_seconds>=0


def test_real_converter_timeout_is_journaled_and_its_directory_is_released():
    source=SOURCE.read_bytes()
    existing=set(Path(tempfile.gettempdir()).glob('kp_extract_doc_*'))
    converter=LibreOfficeConverter(LibreOfficeConverter.discover().executable,timeout_seconds=0.001)
    with pytest.raises(ConversionError) as caught:
        converter.convert(source)
    assert caught.value.event.status=='timeout'
    assert caught.value.event.output_sha256 is None
    assert set(Path(tempfile.gettempdir()).glob('kp_extract_doc_*'))==existing
    assert SOURCE.read_bytes()==source


def test_environment_is_sanitized_without_mutating_caller(monkeypatch):
    import os
    from calendar_pedagoga.lossless_document.formats import office_environment
    monkeypatch.setenv('PYTHONPATH','untrusted-module-path')
    assert 'PYTHONPATH' not in office_environment()
    assert os.environ['PYTHONPATH']=='untrusted-module-path'
