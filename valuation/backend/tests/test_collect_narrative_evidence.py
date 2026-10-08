"""目录身份、报告选择及证据篡改拒绝；不把正文提取称语义审核。"""
import hashlib
from pathlib import Path

import pytest

from tools import collect_narrative_evidence as tool


def document():
    return dict(provider_code='300866', filing_type='annual', period='2025-12-31',
        filing_variant='full', resolution_status='resolved', source_filing_id='1234',
        source_url='https://static.cninfo.com.cn/finalpage/2026-04-10/1234.PDF',
        local_path=None, sha256=None)


def test_explicit_report_identity_and_duplicate_candidates_are_preserved():
    original = document()
    second = dict(original, source_filing_id='1235',
        source_url='https://static.cninfo.com.cn/finalpage/2026-04-11/1235.PDF')
    inventory = dict(scope={'SZSE:300866':'nonfinancial_by_reference'}, reports=[
        original, second, dict(original, filing_variant='summary'),
        dict(original, filing_type='annual', period='2024-12-31')])
    assert tool.reports(inventory) == [original, second]
    inventory['reports'] = [dict(original, source_url=original['source_url'].replace('.PDF', '.pdf'))]
    assert len(tool.reports(inventory)) == 1  # 原始目录确有五条小写扩展名。
    with pytest.raises(ValueError, match='outside frozen scope'):
        tool.reports(inventory, ['600519'])
    inventory['reports'] = [dict(original, source_url='https://other.example/1234.PDF')]
    with pytest.raises(ValueError, match='official filing identity'):
        tool.reports(inventory)


def test_cached_pdf_tamper_is_rejected_before_text_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, 'workspace_path', lambda *parts: tmp_path.joinpath(*parts))
    raw = tmp_path/'cninfo'/'300866-2025-12-31-1234.pdf'
    raw.parent.mkdir()
    raw.write_bytes(b'%PDF-1.7\noriginal source')
    row = dict(document(), local_path=str(raw.relative_to(tmp_path)),
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    out = tmp_path/'derived'; out.mkdir()

    def extract(args, **kwargs):
        Path(args[-1]).write_text('公司经营材料\f')

    monkeypatch.setattr(tool.subprocess, 'run', extract)
    positive = tool.collect_one(row, out)
    assert positive['status'] == 'archived_for_review' and positive['pages'] == 1
    assert positive['pdf_sha256'] == row['sha256']
    assert positive['semantic_review'].startswith('pending;')
    tool.save(out/'collection.json', dict(selected_documents=1, archived=1, failed=0, reports=[positive]))
    assert tool.verify_collection(out)['verified_documents'] == 1
    (out/positive['text_file']).write_text('篡改业务正文')
    with pytest.raises(ValueError, match='hash/path mismatch'):
        tool.verify_collection(out)
    raw.write_bytes(b'%PDF-1.7\ntampered source')
    monkeypatch.setattr(tool.subprocess, 'run', lambda *a, **k: pytest.fail('corrupt source was extracted'))
    negative = tool.collect_one(row, out)
    assert negative['status'] == 'source_or_extraction_failed'
    assert 'corrupt cache rejected' in negative['reason']
