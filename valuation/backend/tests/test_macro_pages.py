"""宏观页面的数据边界、证据绑定及历史冻结；不读真实主库。"""
import asyncio
from copy import deepcopy
import json

from fastapi import FastAPI
import httpx
import pytest

from api.macro import router
from data_sources import macro_pages as store


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    return tmp_path


def inputs():
    snapshot = dict(contract='alphalake-macro-snapshot-v1', exported_at='2026-10-20T10:00:00Z',
                    vintage='latest_source_vintage_not_historical_pit', releases=[dict(
                        release_id=1, artifact_id=1, source='nbs', dataset='macro_cpi_v1',
                        url='https://www.stats.gov.cn/sj/zxfb/synthetic.html', sha256='a' * 64,
                        source_published_at=None, publication_precision='unknown',
                        first_acquired_at='2026-10-20T09:00:00Z', recorded_at='2026-10-20T09:30:00Z')],
                    series=[dict(series_code='cn_cpi_yoy', name_zh='合成居民消费价格同比', source='nbs',
                        dataset='macro_cpi_v1', geography='CN', frequency='monthly', period_basis='month',
                        unit='fraction', price_basis='price_change', seasonal_adjustment='provider_unspecified',
                        definition='仅用于回归，不是真实宏观数据。', points=[
                            dict(period_start='2026-07-01', period_end='2026-07-31', value='0.000000000000', value_status='reported', release_id=1, source_locator='synthetic/1'),
                            dict(period_start='2026-08-01', period_end='2026-08-31', value='-0.001000000000', value_status='reported', release_id=1, source_locator='synthetic/2'),
                            dict(period_start='2026-09-01', period_end='2026-09-30', value=None, value_status='not_reported', release_id=1, source_locator='synthetic/3')])])
    commentary = dict(title='合成宏观观察', summary='未知不是零，负值保留。', author='测试', reviewed_at='2026-10-20T10:01:00Z',
        sections=[dict(title='合成数据观察', body='**负数保留**，最新期未列报。', evidence=[dict(series_code='cn_cpi_yoy', period_end='2026-08-31', value='-0.001')])],
        limitations=['合成测试，非实际数据；不自动改变估值。'])
    return snapshot, commentary


def request(path, headers=None):
    app = FastAPI()
    app.include_router(router, prefix='/api')

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.get(path, headers=headers)
    return asyncio.run(run())


def test_empty_macro_is_read_only(workspace):
    assert request('/api/macro').json()['publications'] == []
    assert request('/api/macro/latest').status_code == 404
    assert request('/api/macro/publications/missing').status_code == 404
    assert not (workspace / 'macro').exists()


def test_publication_replay_history_and_api_never_call_go(workspace, monkeypatch):
    import subprocess
    snapshot, commentary = inputs()
    first = store.publish(snapshot, commentary, 'macro-first')
    for invalid in ('../escape', 'history'):
        with pytest.raises(ValueError, match='ID无效'):
            store.publish(snapshot, commentary, invalid)
    snapshot['exported_at'] = '2026-10-20T10:02:00Z'
    assert store.publish(snapshot, commentary, 'macro-first').published_at == first.published_at
    commentary['summary'] = '新版解读，事实不变。'
    with pytest.raises(ValueError, match='不允许覆盖'):
        store.publish(snapshot, commentary, 'macro-first')
    second = store.publish(snapshot, commentary, 'macro-second')
    assert second.previous_id == first.id
    assert store.catalog()['current_id'] == second.id
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('HTTP request must not start Go'))
    latest = request('/api/macro/latest')
    assert latest.status_code == 200 and latest.json()['id'] == second.id
    assert request('/api/macro/latest', {'if-none-match': latest.headers['etag']}).status_code == 304
    old = request('/api/macro/publications/macro-first')
    assert old.json()['commentary']['summary'] == '未知不是零，负值保留。'
    points = old.json()['snapshot']['series'][0]['points']
    assert [p['value'] for p in points] == ['0E-12', '-0.001000000000', None]
    assert points[-1]['value_status'] == 'not_reported'
    compared = request('/api/macro/compare?before=macro-first&after=macro-second')
    assert compared.status_code == 200
    assert compared.json()['total'] == 0 and compared.json()['commentary_changed']
    assert request('/api/macro/compare?before=macro-first&after=macro-second', {'if-none-match': compared.headers['etag']}).status_code == 304
    assert request('/api/macro/compare?before=macro-second&after=macro-first').status_code == 400
    assert request('/api/macro/compare?before=macro-first&after=macro-first').status_code == 400


@pytest.mark.parametrize('case,kind,count', [
    ('new', 'new_period', 1), ('history', 'historical_added', 1), ('value', 'value_revised', 1),
    ('scope', 'scope_changed', 1), ('source', 'source_changed', 3),
    ('removed', 'removed_observation', 1), ('definition', 'definition_changed', 1),
    ('coverage', 'coverage_added', 3),
])
def test_comparison_separates_observation_revision_scope_and_source(workspace, case, kind, count):
    before, commentary = inputs()
    after = deepcopy(before)
    points = after['series'][0]['points']
    if case == 'new': before['series'][0]['points'].pop()
    elif case == 'history': before['series'][0]['points'].pop(0)
    elif case == 'value': points[0]['value'] = '0.002'
    elif case == 'scope': points[0]['period_start'] = '2026-07-02'
    elif case == 'source': after['releases'][0]['sha256'] = 'b' * 64
    elif case == 'removed': points.pop()
    elif case == 'definition': after['series'][0]['unit'] = 'index_points'
    elif case == 'coverage':
        added = deepcopy(after['series'][0])
        added['series_code'] = 'cn_synthetic_added'
        after['series'].append(added)
    store.publish(before, commentary, 'comparison-before')
    store.publish(after, commentary, 'comparison-after')
    response = request('/api/macro/compare?before=comparison-before&after=comparison-after')
    assert response.status_code == 200
    result = response.json()
    assert result['total'] == count and result['summary'][kind] == count
    assert sum(result['summary'].values()) == count
    if case == 'new':
        assert result['changes'][0]['before'] is None
        assert result['changes'][0]['after']['value'] is None
    if case == 'value': assert result['changes'][0]['delta'] == '0.002000000000'
    if case in ('scope', 'definition', 'source'):
        assert all(row['delta'] is None for row in result['changes'])


def test_comparison_ignores_release_id_churn_and_preserves_decimal_precision(workspace):
    from data_sources.macro_comparison import compare
    before, commentary = inputs()
    store.publish(before, commentary, 'comparison-before')
    after = deepcopy(before)
    after['releases'][0]['release_id'] = 2
    for point in after['series'][0]['points']: point['release_id'] = 2
    store.publish(after, commentary, 'comparison-identities')
    assert compare('comparison-before', 'comparison-identities')['total'] == 0
    before['series'][0]['unit'] = 'CNY'
    before['series'][0]['points'][0]['value'] = '99999999999999999999999999.000000000001'
    store.publish(before, commentary, 'large-before')
    before['series'][0]['points'][0]['value'] = '99999999999999999999999999.000000000002'
    store.publish(before, commentary, 'large-after')
    assert compare('large-before', 'large-after')['changes'][0]['delta'] == '1E-12'


@pytest.mark.parametrize('mutation', ['value', 'period', 'release', 'duplicate', 'infinite', 'unsafe_url', 'precision'])
def test_bad_inputs_reject_before_publication(workspace, mutation):
    snapshot, commentary = inputs()
    if mutation == 'value': commentary['sections'][0]['evidence'][0]['value'] = '0'
    elif mutation == 'period': commentary['sections'][0]['evidence'][0]['period_end'] = '2026-06-30'
    elif mutation == 'release': snapshot['series'][0]['points'][0]['release_id'] = 2
    elif mutation == 'duplicate': snapshot['series'].append(deepcopy(snapshot['series'][0]))
    elif mutation == 'infinite': snapshot['series'][0]['points'][0]['value'] = 'NaN'
    elif mutation == 'unsafe_url': snapshot['releases'][0]['url'] = 'javascript:alert(1)'
    elif mutation == 'precision': snapshot['releases'][0]['publication_precision'] = 'timestamp'
    with pytest.raises(ValueError): store.publish(snapshot, commentary, 'bad-publication')
    assert not (workspace / 'macro' / 'index.json').exists()


def test_invalid_path_tamper_and_bad_index_are_explicit(workspace, tmp_path_factory):
    snapshot, commentary = inputs()
    with pytest.raises(ValueError): store.publish(snapshot, commentary, '../escape')
    first = store.publish(snapshot, commentary, 'macro-first')
    path = workspace / 'macro' / 'publications' / (first.id + '.json')
    raw = path.read_bytes()
    path.write_bytes(raw + b' ')
    assert request('/api/macro/latest').status_code == 409
    path.unlink()
    outside = tmp_path_factory.mktemp('other-macro') / 'page.json'
    outside.write_bytes(raw)
    path.symlink_to(outside)
    assert request('/api/macro/latest').status_code == 404
    index = workspace / 'macro' / 'index.json'
    index.write_text('{invalid')
    assert request('/api/macro').status_code == 422
    assert request('/api/macro/latest').status_code == 422


def test_failed_index_update_keeps_previous_current(workspace, monkeypatch):
    snapshot, commentary = inputs()
    store.publish(snapshot, commentary, 'macro-first')
    before = (workspace / 'macro' / 'index.json').read_bytes()
    commentary['summary'] = '第二版'
    def failed(*args): raise OSError('synthetic replace failure')
    monkeypatch.setattr(store.os, 'replace', failed)
    with pytest.raises(OSError): store.publish(snapshot, commentary, 'macro-second')
    assert (workspace / 'macro' / 'index.json').read_bytes() == before
    assert not (workspace / 'macro' / 'publications' / 'macro-second.json').exists()
    assert not (workspace / 'derived' / 'macro-pages' / 'index.pending.json').exists()


def test_index_metadata_and_current_registration_are_checked(workspace):
    snapshot, commentary = inputs()
    store.publish(snapshot, commentary, 'macro-first')
    path = workspace / 'macro' / 'index.json'
    index = json.loads(path.read_bytes())
    index['publications'][0]['observation_count'] += 1
    path.write_text(json.dumps(index))
    assert request('/api/macro/latest').status_code == 409
    index['current_id'] = 'not-registered'
    path.write_text(json.dumps(index))
    assert request('/api/macro').status_code == 422


@pytest.mark.full
def test_real_go_macro_snapshot_publishes_in_isolated_workspace(workspace, monkeypatch):
    import os
    from pathlib import Path
    import subprocess
    export = workspace / 'export.json'
    env = os.environ | {'ALPHALAKE_MACRO_EXPORT': str(export)}
    env.pop('ALPHALAKE_WORKSPACE', None)
    subprocess.run(['go', 'test', '-tags=full', './internal/ingest', '-run', '^TestMacroRealArchivesReplayAndFailedRunSurviveReopen$', '-count=1'],
                   cwd=Path(__file__).resolve().parents[3], env=env, check=True, capture_output=True, timeout=180)
    snapshot = json.loads(export.read_text())
    assert len(snapshot['series']) == 188 and sum(len(s['points']) for s in snapshot['series']) == 844
    _, commentary = inputs()
    commentary['sections'][0]['evidence'] = [dict(series_code='cn_cpi_yoy', period_end='2026-09-30', value='0.009')]
    page = store.publish(snapshot, commentary, 'real-archive-validation')
    assert len(page.snapshot.releases) == 17
    assert request('/api/macro/latest').json()['snapshot']['contract'] == 'alphalake-macro-snapshot-v1'
