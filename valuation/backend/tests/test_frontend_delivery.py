"""真实临时构建目录覆盖完整依赖、跨发布资源、缓存和失败回滚。"""
import asyncio
import json

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
import httpx
import pytest

from api.frontend import mount_frontend
from tools import publish_frontend as tool


def build(root, version):
    candidate = root / ('candidate-' + version)
    (candidate / 'assets').mkdir(parents=True)
    (candidate / '.vite').mkdir()
    entry = 'assets/main-' + version * 8 + '.js'
    page = 'assets/page-' + version * 8 + '.js'
    for name, content in ((entry, 'entry ' + version), (page, 'page ' + version),
                          ('assets/shared-11111111.js', 'shared ' * 1000),
                          ('assets/site-11111111.css', 'body {}')):
        (candidate / name).write_text(content)
    manifest = {'index.html': dict(file=entry, imports=['shared'], dynamicImports=['page'], css=['assets/site-11111111.css']),
                'shared': dict(file='assets/shared-11111111.js'), 'page': dict(file=page)}
    (candidate / '.vite/manifest.json').write_text(json.dumps(manifest))
    (candidate / 'index.html').write_text(f'<script type="module" src="/{entry}"></script>')
    return candidate


def get(app, path, **kwargs):
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get(path, **kwargs)
    return asyncio.run(request())


def test_publish_keeps_old_lazy_chunks_and_switches_only_after_complete_build(tmp_path):
    destination, receipts = tmp_path / 'dist', tmp_path / 'receipts'
    first, second = build(tmp_path, 'a'), build(tmp_path, 'b')
    one = tool.publish(first, destination, receipts)
    two = tool.publish(second, destination, receipts)
    assert one['status'] == two['status'] == 'published'
    assert len(two['added_files']) == 2
    assert (receipts / 'previous-index.html').read_bytes() == (first / 'index.html').read_bytes()
    assert (destination / 'index.html').read_bytes() == (second / 'index.html').read_bytes()
    assert tool.publish(second, destination, receipts)['status'] == 'unchanged'
    app = FastAPI()
    mount_frontend(app, destination)
    assert get(app, '/assets/page-aaaaaaaa.js').text == 'page a'
    assert get(app, '/assets/page-bbbbbbbb.js').text == 'page b'


@pytest.mark.parametrize('problem', ['missing_lazy', 'missing_dependency', 'collision', 'html_mismatch'])
def test_broken_candidate_never_replaces_valid_html(tmp_path, problem):
    destination, receipts = tmp_path / 'dist', tmp_path / 'receipts'
    first, second = build(tmp_path, 'a'), build(tmp_path, 'b')
    tool.publish(first, destination, receipts)
    if problem == 'missing_lazy':
        (second / 'assets/page-bbbbbbbb.js').unlink()
    elif problem == 'missing_dependency':
        manifest = json.loads((second / '.vite/manifest.json').read_text())
        manifest['index.html']['dynamicImports'].append('unknown')
        (second / '.vite/manifest.json').write_text(json.dumps(manifest))
    elif problem == 'collision':
        (second / 'assets/shared-11111111.js').write_text('different bytes under the same name')
    else:
        (second / 'index.html').write_text('<script src="/assets/main-cccccccc.js"></script>')
    with pytest.raises(ValueError):
        tool.publish(second, destination, receipts)
    assert (destination / 'index.html').read_bytes() == (first / 'index.html').read_bytes()
    assert not (destination / 'assets/main-bbbbbbbb.js').exists()


def test_receipt_failure_restores_html_and_rollback_pointer(tmp_path, monkeypatch):
    destination, receipts = tmp_path / 'dist', tmp_path / 'receipts'
    first, second = build(tmp_path, 'a'), build(tmp_path, 'b')
    tool.publish(first, destination, receipts)
    previous = (receipts / 'current.json').read_bytes()
    write = tool.atomic_write
    def fail(path, content):
        if path.name == 'current.json':
            raise OSError('receipt unavailable')
        return write(path, content)
    monkeypatch.setattr(tool, 'atomic_write', fail)
    with pytest.raises(OSError, match='receipt unavailable'):
        tool.publish(second, destination, receipts)
    assert (destination / 'index.html').read_bytes() == (first / 'index.html').read_bytes()
    assert (receipts / 'current.json').read_bytes() == previous
    assert not (receipts / 'previous-index.html').exists()


def test_candidate_and_destination_assets_cannot_be_symlinks(tmp_path):
    candidate = build(tmp_path, 'a')
    destination = tmp_path / 'dist'
    destination.mkdir()
    (destination / 'assets').symlink_to(candidate / 'assets', target_is_directory=True)
    with pytest.raises(ValueError, match='软链接'):
        tool.publish(candidate, destination, tmp_path / 'receipts')


def test_html_static_and_compression_contract(tmp_path):
    candidate = build(tmp_path, 'a')
    destination = tmp_path / 'dist'
    tool.publish(candidate, destination, tmp_path / 'receipts')
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
    @app.get('/api/large')
    def large():
        return {'value': '研究内容' * 2000}
    mount_frontend(app, destination)
    html = get(app, '/research/example')
    assert html.headers['cache-control'] == 'no-store'
    asset = get(app, '/assets/shared-11111111.js')
    assert asset.headers['cache-control'] == 'public, max-age=31536000, immutable'
    assert asset.headers['content-encoding'] == 'gzip'
    assert asset.text == 'shared ' * 1000
    assert 'Accept-Encoding' in asset.headers['vary']
    unchanged = get(app, '/assets/shared-11111111.js', headers={'If-None-Match': asset.headers['etag']})
    assert unchanged.status_code == 304 and unchanged.content == b''
    ranged = get(app, '/assets/shared-11111111.js', headers={'Range': 'bytes=0-5'})
    assert ranged.status_code == 206 and ranged.content == b'shared'
    assert 'content-encoding' not in ranged.headers
    missing = get(app, '/assets/missing-11111111.js')
    assert missing.status_code == 404 and missing.headers['cache-control'] == 'no-store'
    assert get(app, '/api/missing').status_code == 404
    compressed = get(app, '/api/large')
    assert compressed.headers['content-encoding'] == 'gzip'
    assert compressed.json() == {'value': '研究内容' * 2000}
