"""目录身份、报告选择及证据篡改拒绝；不把正文提取称语义审核。"""
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

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
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    raw = tmp_path/'cninfo'/'300866-2025-12-31-1234.pdf'
    raw.parent.mkdir()
    raw.write_bytes(b'%PDF-1.7\noriginal source')
    row = dict(document(), local_path=str(raw.relative_to(tmp_path)),
        sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    out = tmp_path/'derived'; out.mkdir()

    def extract(args, **kwargs):
        Path(args[-1]).write_text('资本性支出承诺\f公司经营材料\f')

    monkeypatch.setattr(tool.subprocess, 'run', extract)
    positive = tool.collect_one(row, out)
    assert positive['status'] == 'archived_for_review' and positive['pages'] == 2
    assert positive['pdf_sha256'] == row['sha256']
    assert positive['semantic_review'].startswith('pending;')
    tool.save(out/'collection.json', dict(contract=tool.CONTRACT, selected_documents=1, archived=1, failed=0, reports=[positive]))
    assert tool.verify_collection(out)['verified_documents'] == 1
    positive['budget_review_index']['pages'][0]['read_pages'] = [1]
    tool.save(out/'collection.json', dict(contract=tool.CONTRACT, selected_documents=1, archived=1, failed=0, reports=[positive]))
    with pytest.raises(ValueError, match='budget review index differs'):
        tool.verify_collection(out)
    (out/positive['text_file']).write_text('篡改业务正文')
    with pytest.raises(ValueError, match='hash/path mismatch'):
        tool.verify_collection(out)
    raw.write_bytes(b'%PDF-1.7\ntampered source')
    monkeypatch.setattr(tool.subprocess, 'run', lambda *a, **k: pytest.fail('corrupt source was extracted'))
    negative = tool.collect_one(row, out)
    assert negative['status'] == 'source_or_extraction_failed'
    assert 'corrupt cache rejected' in negative['reason']


def test_budget_review_index_preserves_cross_page_tables_without_interpreting_amounts():
    text = ('资本性支出\n承诺 已签约和已批准 100\f'
        '重要在建工程项目 研发计划 200\f经营计划 资本开支计划\f')
    index = tool.budget_review_index(text)
    assert index['pages'] == [
        dict(physical_page=1, topics=['capital_commitment'], read_pages=[1,2]),
        dict(physical_page=2, topics=['construction_projects','research_plan'], read_pages=[2,3]),
        dict(physical_page=3, topics=['capital_expenditure_plan','management_plan'], read_pages=[3])]
    assert index['unmatched_topics'] == []
    assert index['status'] == 'page_candidates_not_semantically_reviewed'
    absent = tool.budget_review_index('本期实际购建现金100元\f')
    assert absent['pages'] == [] and 'capital_commitment' in absent['unmatched_topics']


def test_url_cache_is_shared_across_output_directories_and_catalogue_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    calls = []
    payload = b'%PDF-1.7\ncompany report'

    def download(request, **kwargs):
        calls.append(request.full_url)
        response = io.BytesIO(payload)
        response.url = request.full_url
        return response

    monkeypatch.setattr(tool, 'urlopen', download)
    monkeypatch.setattr(tool.subprocess, 'run', lambda args, **kw: Path(args[-1]).write_text('经营资料\f'))
    first = tmp_path/'derived'/'first'; first.mkdir(parents=True)
    second = tmp_path/'derived'/'second'; second.mkdir()
    a = tool.collect_one(document(), first)
    b = tool.collect_one(document(), second)
    assert a['status'] == b['status'] == 'archived_for_review'
    assert a['retrieval'] == 'official_download' and b['retrieval'] == 'existing_cache'
    assert a['pdf_file'] == b['pdf_file'] and a['first_fetched_at'] == b['first_fetched_at']
    assert calls == [document()['source_url']]
    assert len(list((tmp_path/'cninfo'/'objects').rglob('*.pdf'))) == 1
    view = tmp_path/'cninfo'/'documents'/'2026-04-10'/'1234.PDF'
    assert view.is_symlink() and view.resolve() == tmp_path/a['pdf_file']
    assert not list(first.rglob('*.pdf')) and not list(second.rglob('*.pdf'))
    # 已知源哈希足以复用Go原文对象，不要求某个固定文件名仍在。
    view.unlink(); view.with_name(view.name+'.json').unlink()
    missing_path = dict(document(), sha256=a['pdf_sha256'], local_path='cninfo/missing.pdf')
    c = tool.collect_one(missing_path, second)
    assert c['status'] == 'archived_for_review' and len(calls) == 1


def test_untrusted_cache_reference_is_rejected_without_download(tmp_path, monkeypatch):
    from data_sources import cninfo_documents as cache
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    path = tmp_path/'cninfo'/'original.pdf'; path.parent.mkdir()
    path.write_bytes(b'%PDF-1.7\noriginal')
    sha = cache.digest(path)
    with cache.document_lock(document()['source_url']):
        cache.publish_reference(document()['source_url'], path, sha)
    receipt = cache.document_path(document()['source_url']).with_name('1234.PDF.json')
    payload = tool.read_json(receipt); payload['local_path'] = '../outside.pdf'; tool.save(receipt, payload)
    monkeypatch.setattr(tool, 'urlopen', lambda *a, **k: pytest.fail('bad cache triggered download'))
    output = tmp_path/'derived'/'run'; output.mkdir()
    result = tool.collect_one(document(), output)
    assert result['status'] == 'source_or_extraction_failed' and 'canonical' in result['reason']


def test_concurrent_collectors_download_one_official_document(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    calls = []

    def download(request, **kwargs):
        calls.append(request.full_url)
        time.sleep(0.025)  # 留出另一调用者进入查找流程的窗口。
        response = io.BytesIO(b'%PDF-1.7\nshared concurrent report')
        response.url = request.full_url
        return response

    monkeypatch.setattr(tool, 'urlopen', download)
    monkeypatch.setattr(tool.subprocess, 'run', lambda args, **kw: Path(args[-1]).write_text('经营资料\f'))
    outputs = [tmp_path/'derived'/name for name in ('a', 'b')]
    for output in outputs:
        output.mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda output: tool.collect_one(document(), output), outputs))
    assert all(row['status'] == 'archived_for_review' for row in rows)
    assert rows[0]['pdf_file'] == rows[1]['pdf_file']
    assert calls == [document()['source_url']]


def test_pdf_lock_rejects_external_symlink(tmp_path, monkeypatch):
    from data_sources import cninfo_documents as cache
    root = tmp_path/'workspace'; root.mkdir()
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(root))
    external = tmp_path/'external'; external.write_text('unchanged')
    lock = root/'derived/pdf-cache/locks/2026-04-10/1234.PDF.lock'
    lock.parent.mkdir(parents=True)
    lock.symlink_to(external)
    with pytest.raises(ValueError, match='lock leaves workspace'):
        with cache.document_lock(document()['source_url']):
            pytest.fail('acquired an external lock')
    assert external.read_text() == 'unchanged'
