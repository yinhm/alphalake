"""报告阅读／附件的真实文件边界；全部使用临时workspace。"""
import asyncio
from copy import deepcopy
import json
import os

from fastapi import FastAPI
import httpx
import pytest

from api.research import router
from data_sources.research_reports import catalog, publish, file_digest
from tools.research_reports import main


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    return tmp_path


def request(path):
    app = FastAPI()
    app.include_router(router, prefix='/api')

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get(path)
    return asyncio.run(run())


def draft(workspace, identifier='test-report', **extra):
    folder = workspace / 'research' / identifier
    folder.mkdir(parents=True)
    (folder / 'report.md').write_text('# 合成研究\n\n| 条件 | 值 |\n|---|---|\n| 合成 | 1 |\n', encoding='utf-8')
    (folder / 'assumptions.md').write_text('合成假设，不是公司事实。', encoding='utf-8')
    pdf = workspace / 'cninfo' / 'objects' / 'test.pdf'
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b'%PDF-1.4\nsynthetic')
    body = dict(contract='alphalake-research-v1', id=identifier, title='合成安克研究', summary='只用于临时文件回归',
                kind='initial_coverage', tickers=['SZSE:300866'], research_date='2026-10-17',
                information_as_of='2026-10-17T00:00:00+00:00', limitations=['合成条件不代表实际估值'], files=[
                    dict(id='report', label='研究正文', path=f'research/{identifier}/report.md'),
                    dict(id='assumptions', label='年度假设', path=f'research/{identifier}/assumptions.md'),
                    dict(id='source', label='源PDF', path='cninfo/objects/test.pdf')])
    body.update(extra)
    return folder, body


def test_missing_directory_and_unknown_report_never_create_files(workspace):
    response = request('/api/research')
    assert response.status_code == 200 and response.json()['total'] == 0
    assert request('/api/research/no-report').status_code == 404
    assert not (workspace / 'research').exists()


def test_publish_search_detail_documents_and_read_only_pdf(workspace):
    folder, body = draft(workspace)
    published = publish(folder, body)
    before = file_digest(folder / 'research.json')
    assert published.published_at.tzinfo is not None
    assert request('/api/research?q=３００８６６').json()['total'] == 1
    assert request('/api/research?q=missing').json()['reports'] == []
    assert request('/api/research?kind=thesis').json()['total'] == 0
    listed = request('/api/research').json()['reports'][0]
    assert 'files' not in listed and 'body_md' not in listed
    detail = request('/api/research/test-report').json()
    assert detail['document']['body_md'].startswith('# 合成研究')
    assert detail['files'][2]['path'] == 'cninfo/objects/test.pdf'
    assert request('/api/research/test-report?document=assumptions').json()['document']['body_md'] == '合成假设，不是公司事实。'
    assert request('/api/research/test-report?document=source').status_code == 422
    file = request('/api/research/test-report/files/source')
    assert file.content.startswith(b'%PDF') and file.headers['content-type'] == 'application/pdf'
    assert file.headers['content-disposition'].startswith('inline;')
    assert request('/api/research/test-report/files/source?download=true').headers['content-disposition'].startswith('attachment;')
    assert file.headers['x-content-type-options'] == 'nosniff' and 'sandbox' in file.headers['content-security-policy']
    assert request('/api/research/test-report/files/not-registered').status_code == 404
    assert file_digest(folder / 'research.json') == before
    assert not (folder / '.research.pending.json').exists()
    with pytest.raises(ValueError, match='already published'):
        publish(folder, body)


def test_order_pagination_and_corrupt_registration_are_explicit(workspace):
    for identifier, day in [('older', '2026-10-10'), ('newer', '2026-10-17'), ('unknown-date', None)]:
        folder, body = draft(workspace, identifier, research_date=day)
        publish(folder, body)
    bad = workspace / 'research' / 'broken'
    bad.mkdir()
    (bad / 'research.json').write_text('{not JSON')
    response = request('/api/research?limit=1&offset=0').json()
    assert response['total'] == 3 and response['reports'][0]['id'] == 'newer'
    assert response['issues'] == [dict(id='broken', code='invalid_report', message='研究登记字段或内容无效')]
    assert request('/api/research?limit=1&offset=1').json()['reports'][0]['id'] == 'older'
    assert request('/api/research?limit=1&offset=2').json()['reports'][0]['id'] == 'unknown-date'
    assert request('/api/research?offset=-1').status_code == 422
    assert request('/api/research?kind=unknown').status_code == 422
    assert request('/api/research/broken').status_code == 422


@pytest.mark.parametrize('path', ['/etc/passwd', '../secret.txt', 'research\\secret.txt', 'research/secret.html',
                                 'alphalake.duckdb', 'valuation.sqlite', 'research//other.txt'])
def test_publish_rejects_unsafe_or_unsupported_files(workspace, path):
    folder, body = draft(workspace)
    body['files'][2]['path'] = path
    with pytest.raises(ValueError):
        publish(folder, body)
    assert not (folder / 'research.json').exists()


def test_symlink_escape_and_hash_tampering_are_rejected(workspace, tmp_path_factory):
    folder, body = draft(workspace)
    publish(folder, body)
    assert request('/api/research').json()['total'] == 1
    source = workspace / 'cninfo' / 'objects' / 'test.pdf'
    outside = tmp_path_factory.mktemp('outside') / 'private.pdf'
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)
    assert request('/api/research/test-report/files/source').status_code == 422
    assert request('/api/research/test-report').json()['files'][2]['available'] is False
    (folder / 'report.md').write_text('tampered')
    assert request('/api/research/test-report').status_code == 409
    assert request('/api/research/test-report/files/report').status_code == 409


def test_missing_attachment_does_not_hide_readable_report(workspace):
    folder, body = draft(workspace)
    publish(folder, body)
    (workspace / 'cninfo' / 'objects' / 'test.pdf').unlink()
    detail = request('/api/research/test-report')
    assert detail.status_code == 200 and detail.json()['files'][2]['available'] is False
    assert request('/api/research/test-report/files/source').status_code == 404


def test_publish_requires_id_match_known_schema_and_valid_text(workspace):
    folder, original = draft(workspace)
    for field, value in [('id', 'wrong'), ('report_file', 'source'), ('information_as_of', '2026-10-17T00:00:00')]:
        body = deepcopy(original)
        body[field] = value
        with pytest.raises(ValueError):
            publish(folder, body)
        assert not (folder / 'research.json').exists()
    body = deepcopy(original)
    body['files'][0]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='hash mismatch'):
        publish(folder, body)
    (folder / 'report.md').write_bytes(b'\xff\xfe')
    with pytest.raises(UnicodeError):
        publish(folder, original)
    assert not (folder / 'research.json').exists()


def test_publish_cli_and_validate(workspace, monkeypatch, capsys):
    folder, body = draft(workspace)
    metadata = workspace / 'draft.json'
    metadata.write_text(json.dumps(body, ensure_ascii=False))
    monkeypatch.setattr('sys.argv', ['research_reports', 'publish', str(folder), '--metadata', str(metadata)])
    main()
    assert json.loads(capsys.readouterr().out)['url'] == '/research/test-report'
    monkeypatch.setattr('sys.argv', ['research_reports', 'validate', str(folder)])
    main()
    assert json.loads(capsys.readouterr().out)['status'] == 'verified'


def test_catalog_and_direct_read_reject_aliased_roots(workspace):
    folder, body = draft(workspace)
    publish(folder, body)
    assert catalog()['total'] == 1
    root = workspace / 'research'
    target = workspace / 'other'
    root.rename(target)
    root.symlink_to(target, target_is_directory=True)
    assert request('/api/research').status_code == 422
    assert request('/api/research/test-report').status_code == 422
    with pytest.raises(ValueError, match='canonical'):
        publish(folder, body)


def test_catalog_reuses_unchanged_validation_and_returns_detached_page(workspace, monkeypatch):
    from data_sources import research_reports as reports
    for identifier in ('first', 'second'):
        folder, body = draft(workspace, identifier)
        publish(folder, body)
    original = reports._read_manifest
    reads = []

    def tracked(path, identifier):
        reads.append(identifier)
        return original(path, identifier)

    monkeypatch.setattr(reports, '_read_manifest', tracked)
    first = catalog()
    assert sorted(reads) == ['first', 'second']
    first['reports'][0]['title'] = '外部修改不得污染缓存'
    first['reports'][0]['tickers'].clear()
    assert catalog(query='３００８６６')['total'] == 2
    fresh = catalog(offset=1, limit=1)['reports'][0]
    assert fresh['title'] == '合成安克研究' and fresh['tickers'] == ['SZSE:300866']
    assert len(reads) == 2


def test_cached_catalog_detects_same_size_edit_even_with_restored_mtime(workspace):
    folder, body = draft(workspace, title='初版研究')
    publish(folder, body)
    assert catalog(query='初版')['total'] == 1
    path = folder/'research.json'
    before = path.stat()
    encoded = path.read_text().replace('初版研究', '修订研究')
    assert len(encoded.encode()) == before.st_size
    path.write_text(encoded)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert catalog(query='初版')['total'] == 0
    assert catalog(query='修订')['reports'][0]['title'] == '修订研究'
    path.write_text('{broken JSON')
    rejected = catalog()
    assert rejected['reports'] == [] and rejected['issues'][0]['code'] == 'invalid_report'
    path.write_text(encoded)
    assert catalog()['total'] == 1 and catalog()['issues'] == []


def test_cached_catalog_observes_atomic_replacement_addition_and_removal(workspace):
    folder, body = draft(workspace, 'first')
    publish(folder, body)
    assert catalog()['total'] == 1
    path = folder/'research.json'
    record = json.loads(path.read_text())
    record['kind'] = 'thesis'
    pending = folder/'pending.json'
    pending.write_text(json.dumps(record))
    pending.replace(path)
    assert catalog(kind='thesis')['total'] == 1
    other, metadata = draft(workspace, 'second')
    publish(other, metadata)
    assert catalog()['total'] == 2
    path.unlink()
    assert [row['id'] for row in catalog()['reports']] == ['second']


def test_cached_catalog_rejects_folder_and_manifest_symlink_replacements(workspace):
    folder, body = draft(workspace)
    publish(folder, body)
    assert catalog()['total'] == 1
    relocated = workspace/'relocated'
    folder.rename(relocated)
    folder.symlink_to(relocated, target_is_directory=True)
    assert catalog()['reports'] == [] and catalog()['issues'][0]['code'] == 'invalid_report'
    assert request('/api/research/test-report').status_code == 422
    folder.unlink()
    relocated.rename(folder)
    assert catalog()['total'] == 1
    source = folder/'research.json'
    target = workspace/'registration.json'
    source.rename(target)
    source.symlink_to(target)
    assert catalog()['reports'] == [] and catalog()['issues'][0]['code'] == 'invalid_report'
    assert request('/api/research/test-report').status_code == 422


def test_catalog_cache_isolated_between_workspaces(workspace, tmp_path, monkeypatch):
    folder, body = draft(workspace, title='第一空间')
    publish(folder, body)
    assert catalog()['reports'][0]['title'] == '第一空间'
    second = tmp_path/'another-workspace'
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(second))
    folder, body = draft(second, title='第二空间')
    publish(folder, body)
    assert catalog()['reports'][0]['title'] == '第二空间'
