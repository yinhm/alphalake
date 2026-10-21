"""选股使用真实登记文件和临时workspace；不读取主库、不重新估值。"""
from collections import Counter
import asyncio
import json
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from api.valuation_results import router
from data_sources import valuation_results as source
from data_sources.research_reports import file_digest, publish
from tools import valuation_results as tool


def save(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, allow_nan=False))


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    source._load.cache_clear()
    return tmp_path


def register(root, identifier, files, tickers=None):
    folder = root / 'research' / identifier
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'report.md').write_text('# 合成研究\n')
    entries = [('report', folder / 'report.md'), *files]
    return publish(folder, dict(contract='alphalake-research-v1', id=identifier, title='合成研究批次', summary='合成条件',
        kind='research_note', status='partial', tickers=tickers or [], research_date='2026-10-21',
        files=[dict(id=key, label=key, path=path.relative_to(root).as_posix()) for key, path in entries]))


def fixture_batch(root):
    period, cutoff = '2026-06-30', '2026-10-20T00:00:00Z'
    states = ['ready'] * 4 + ['blocked_required_inputs', 'outside_product_scope', 'requires_scope_review']
    protocol = dict(contract='initial-coverage-protocol-v2', sqlite_sha256='a' * 64, reference_snapshot_id='ref-test',
        financial_report_period=period, information_as_of=cutoff, frozen_at=cutoff,
        runtime=dict(stages=dict(calculation='frozen-calc', review='frozen-review')), companies=[])
    for index, state in enumerate(states):
        ticker = f'SHSE:60000{index + 1}'
        request = dict(inputs=dict(ticker=ticker, adjustment_inputs=dict(has_r_and_d=index == 0), valuation_assumptions={}))
        diagnostic = dict(status=state)
        protocol['companies'].append(dict(ticker=ticker, name=f'合成公司{index + 1}', admission=state,
            financial_admission='ready' if state == 'ready' else 'blocked_required_inputs',
            missing_fields=[] if state == 'ready' else ['revenues'], reason=state,
            quote_date=period if index != 6 else None, stock_price_listing=10 if index != 6 else None,
            inputs_sha256=tool.digest_value(request['inputs']), diagnostic_sha256=tool.digest_value(diagnostic)))
    protocol_path = root / 'derived' / 'protocol.json'
    save(protocol_path, protocol)
    protocol_hash = file_digest(protocol_path)
    rows, risks = [], Counter()
    for index, frozen in enumerate(protocol['companies']):
        ticker, state = frozen['ticker'], frozen['admission']
        ready = state == 'ready'
        identifier = f'company-{index + 1}'
        model = dict(contract='initial-coverage-calculation-v2', ticker=ticker, admission=state,
            protocol_sha256=protocol_hash, financial_input_snapshot=protocol['sqlite_sha256'],
            runtime=protocol['runtime'], run_id='run-' + ticker,
            request=dict(inputs=dict(ticker=ticker, adjustment_inputs=dict(has_r_and_d=index == 0), valuation_assumptions={})),
            diagnostic=dict(status=state), api_verified=True)
        value = [0, -5, 30, 20][index] if ready else None
        if ready:
            model.update(response=dict(ticker=ticker, inputs=model['request']['inputs'], reference_snapshot=dict(id='ref-test'),
                final=dict(value_per_share=value), ltm_financials=dict(stock_price_reporting=10),
                cost_of_capital=dict(wacc=[0, .1, .08, .06][index])),
                review=dict(forecast=[dict(growth=None if index == 1 else .2, adjusted_operating_margin=2 if index == 0 else .2,
                    capital_funding=dict(sales_to_capital=None if index == 1 else 2))],
                    terminal=dict(growth=.02, wacc=.08, value_share=.6),
                    economic_checks=dict(capital_release=dict(gross_release_million_cny=10 if index == 0 else 0))))
        model_path = root / 'research' / identifier / 'valuation.json'
        save(model_path, model)
        register(root, identifier, [('valuation', model_path)], [ticker])
        comparison = 'unreconciled_post_period_capital_change' if index == 2 else 'capital_date_proxy'
        difference = None if not ready or index == 2 else (value / 10 - 1) * 100
        rows.append(dict(**{key: frozen[key] for key in ('ticker','name','admission','financial_admission','missing_fields','reason')},
            reference_industry='Synthetic' if index < 6 else None, report_id=identifier, url='/research/' + identifier,
            mode='native_baseline_with_operating_context' if ready else None, condition_value_cny_per_share=value,
            close_cny=frozen['stock_price_listing'], close_date=frozen['quote_date'], descriptive_difference_pct=difference,
            price_comparison_status=comparison, price_comparison_reason='合成资本比较边界', financial_period=period,
            information_as_of=cutoff, research_frozen_at=cutoff, share_date=period if ready else None, run_id=model['run_id']))
        risks.update(tool.model_review_flags(model))
    summary = dict(contract='initial-coverage-all-v1', count=len(rows), source_universe=len(rows), results=rows,
        protocol_sha256=protocol_hash, sqlite_sha256=protocol['sqlite_sha256'], report_period=period,
        valuation_information_as_of=cutoff, research_frozen_at=cutoff, company_evidence_acquired_at=cutoff,
        admissions=dict(Counter(states)), risk_and_diagnostic_counts=dict(risks))
    folder = root / 'research' / 'all-test'
    save(folder / 'results.json', summary)
    (folder / 'results.csv').write_text('ticker,value\nsynthetic,0\n')
    register(root, 'all-test', [('results', folder / 'results.json'), ('csv', folder / 'results.csv'), ('protocol', protocol_path)])
    return summary


@pytest.fixture
def published(workspace):
    summary = fixture_batch(workspace)
    result = tool.build('all-test', 'first', activate=True)
    return summary, result


@pytest.fixture
def client(workspace):
    app = FastAPI()
    app.include_router(router, prefix='/api')
    async def get(path, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as session:
            return await session.get(path, **kwargs)
    return SimpleNamespace(get=lambda path, **kwargs: asyncio.run(get(path, **kwargs)))


def test_empty_catalog_is_read_only(workspace, client):
    assert client.get('/api/valuation-results/datasets').json()['datasets'] == []
    assert client.get('/api/valuation-results/missing').status_code == 404
    assert not (workspace / 'derived').exists()


def test_projection_keeps_zero_negative_missing_and_frozen_versions(workspace, published):
    summary, receipt = published
    body, _, _ = source.load_dataset('first')
    assert receipt['count'] == 7 and body['admission_counts'] == summary['admissions']
    assert [r['condition_value_cny_per_share'] for r in body['rows']] == [0, -5, 30, 20, None, None, None]
    assert body['rows'][0]['assumptions']['initial_wacc_rate'] == 0
    assert body['rows'][1]['assumptions']['first_year_funding_sales_to_capital'] is None
    assert body['rows'][2]['descriptive_difference_pct'] is None
    assert body['rows'][4]['close_cny'] == 10 and body['rows'][4]['comparison_display_status'] == 'not_calculated'
    assert body['dataset']['runtime_stages']['calculation'] == 'frozen-calc'
    assert body['review_flag_counts'] == summary['risk_and_diagnostic_counts']
    assert tool.build('all-test', 'first', activate=True)['sha256'] == receipt['sha256']


def imported_projection(root):
    body = tool.project('all-test', 'imported')
    report = tool.read_report('all-test')
    entry = next(entry for entry in report.files if entry.id == 'results')
    body['dataset']['source_files'] = [dict(report_id=report.id, file_id=entry.id, sha256=entry.sha256)]
    return body


def test_import_current_snapshot_preserves_sources_versions_and_current_choice(workspace, published):
    body = imported_projection(workspace)
    path = workspace / 'derived' / 'snapshot.json'
    save(path, body)
    result = tool.import_snapshot(path)
    assert result['current'] == 'first'
    actual, _, _ = source.load_dataset('imported')
    assert actual == body
    assert tool.import_snapshot(path)['sha256'] == result['sha256']
    assert {item['id'] for item in source.datasets()['datasets']} == {'first', 'imported'}
    assert actual['rows'][0]['condition_value_cny_per_share'] == 0
    assert actual['dataset']['runtime_stages']['calculation'] == 'frozen-calc'


@pytest.mark.parametrize('change', [
    lambda body: body['dataset']['source_files'][0].update(sha256='0' * 64),
    lambda body: body.update(universe_count=99),
    lambda body: body['rows'][1].update(ticker=body['rows'][0]['ticker']),
    lambda body: body['rows'][0].update(admission='not_calculated'),
    lambda body: body['rows'][0].update(comparison_display_status='unreconciled_post_period_capital_change'),
    lambda body: body['rows'][0].update(url='/research/wrong'),
    lambda body: body['rows'][0]['model_source'].update(sha256='0' * 64),
])
def test_import_rejects_changed_sources_denominators_and_invalid_comparisons(workspace, published, change):
    body = imported_projection(workspace)
    change(body)
    path = workspace / 'derived' / 'bad-snapshot.json'
    save(path, body)
    with pytest.raises(ValueError):
        tool.import_snapshot(path)
    assert source.registry()['current'] == 'first'
    assert 'imported' not in source.registry()['datasets']


def test_queries_use_only_published_projection_and_do_not_mutate_cache(published, client, monkeypatch):
    monkeypatch.setattr(tool, 'registered_json', lambda *args: pytest.fail('查询不得重新扫描模型'))
    result = client.get('/api/valuation-results/first?q=６００００１').json()
    assert [row['ticker'] for row in result['rows']] == ['SHSE:600001']
    assert client.get('/api/valuation-results/first?q=合成公司2').json()['filtered_count'] == 1
    a = source.query_results('first', source.ResultQuery(q='600001'))
    a['rows'][0]['assumptions']['initial_wacc_rate'] = 123
    assert source.query_results('first', source.ResultQuery(q='600001'))['rows'][0]['assumptions']['initial_wacc_rate'] == 0
    assert client.get('/api/valuation-results/datasets').json()['current'] == 'first'


def test_cross_filters_numeric_units_unknown_counts_and_stable_paging(published, client):
    prefix = '/api/valuation-results/first'
    page = client.get(prefix + '?sort=value&direction=desc&limit=2').json()
    assert [r['condition_value_cny_per_share'] for r in page['rows']] == [30,20]
    tail = client.get(prefix + '?sort=value&direction=desc&limit=2&offset=999').json()
    assert tail['offset'] == 6 and tail['rows'][0]['condition_value_cny_per_share'] is None
    all_rows = [r['ticker'] for offset in range(0,7,2) for r in client.get(prefix + f'?limit=2&offset={offset}').json()['rows']]
    assert len(all_rows) == len(set(all_rows)) == 7
    result = client.get(prefix + '?wacc_max_rate=0&admission=ready').json()
    assert [r['ticker'] for r in result['rows']] == ['SHSE:600001']
    result = client.get(prefix + '?wacc_max_rate=0.1').json()
    assert result['filtered_count'] == 4 and result['range_unknown_counts']['wacc'] == 3
    result = client.get(prefix + '?difference_min_pct=20&wacc_max_rate=0.1').json()
    assert [r['ticker'] for r in result['rows']] == ['SHSE:600004']
    result = client.get(prefix + '?admission=ready&admission=blocked_required_inputs&flag=nonpositive_equity_condition').json()
    assert result['filtered_count'] == 2 and result['universe_count'] == 7
    assert result['facets']['admission']['outside_product_scope'] == 0
    result = client.get(prefix + '?q=不匹配').json()
    assert result['filtered_count'] == 0 and result['offset'] == 0


@pytest.mark.parametrize('query', ['unknown=1','sort=arbitrary','direction=random','admission=bad','flag=bad',
    'industry=not-known','limit=101','offset=-1','wacc_min_rate=NaN','wacc_max_rate=Infinity',
    'growth_min_rate=0.3&growth_max_rate=0.1'])
def test_invalid_query_is_rejected(published, client, query):
    assert client.get('/api/valuation-results/first?' + query).status_code == 422


def test_etag_scoped_to_query_and_tampering_invalidates_cache(workspace, published, client):
    url = '/api/valuation-results/first'
    response = client.get(url)
    etag = response.headers['etag']
    assert client.get(url,headers={'If-None-Match':etag}).status_code == 304
    assert client.get(url+'?q=600001',headers={'If-None-Match':etag}).status_code == 200
    path = workspace / 'derived/valuation-results/first/index.json'
    data = json.loads(path.read_text());data['rows'][0]['condition_value_cny_per_share'] += 1
    save(path,data)
    assert client.get(url).status_code == 503
    with pytest.raises(ValueError,match='摘要字节发生变化'):
        tool.build('all-test','first',activate=True)


def test_bad_sources_and_failed_publication_preserve_previous_pointer(workspace, published, monkeypatch):
    before = (workspace/'derived/valuation-results/current.json').read_bytes()
    original = tool.atomic_json
    def fail_pointer(path, body):
        if path.name == 'current.json': raise OSError('synthetic pointer failure')
        return original(path,body)
    monkeypatch.setattr(tool,'atomic_json',fail_pointer)
    with pytest.raises(OSError): tool.build('all-test','next',activate=True)
    assert (workspace/'derived/valuation-results/current.json').read_bytes() == before
    monkeypatch.setattr(tool,'atomic_json',original)
    tool.build('all-test','next',activate=True)
    tool.activate('first')
    assert source.registry()['current'] == 'first'
    model = workspace/'research/company-1/valuation.json'
    model.write_text('{}')
    with pytest.raises(ValueError): tool.build('all-test','corrupt',activate=True)
    assert source.registry()['current'] == 'first'


def test_resigned_numeric_mismatch_still_rejected(workspace, published):
    path = workspace/'research/all-test/results.json'
    value = json.loads(path.read_text());value['results'][0]['condition_value_cny_per_share'] = 1
    save(path,value)
    manifest = workspace/'research/all-test/research.json'
    data = json.loads(manifest.read_text())
    next(row for row in data['files'] if row['id']=='results')['sha256'] = file_digest(path)
    save(manifest,data)
    with pytest.raises(ValueError,match='数值或报价不一致'): tool.build('all-test','bad',activate=True)
    assert source.registry()['current'] == 'first'


def test_alias_paths_and_invalid_ids_rejected(workspace, published, client):
    path = workspace/'derived/valuation-results/first/index.json'
    alias = path.with_name('source.json');path.rename(alias);path.symlink_to(alias)
    assert client.get('/api/valuation-results/first').status_code == 503
    with pytest.raises(ValueError): tool.project('all-test','../outside')
