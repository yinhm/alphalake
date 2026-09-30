import hashlib
import pytest
from tools import pdf_evidence as tool


def test_text_reuse_still_rejects_changed_files_and_reextracts_changed_parser(tmp_path, monkeypatch):
    path = tmp_path/'evidence.pdf'; path.write_bytes(b'original')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []
    def extract(command, **kwargs):
        calls.append(command)
        return 'original text'
    monkeypatch.setattr(tool.subprocess, 'check_output', extract)
    assert tool.verified_pdf_text(path, sha) == tool.verified_pdf_text(path, sha) == 'original text'
    assert len(calls) == 1
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='PDF hash'):
        tool.verified_pdf_text(path, sha)
    path.write_bytes(b'original')
    monkeypatch.setattr(tool.subprocess, 'check_output', lambda *a, **kw: 'changed parser text')
    assert tool.verified_pdf_text(path, sha) == 'changed parser text'
    assert tool._extract.cache_info().maxsize == 16
