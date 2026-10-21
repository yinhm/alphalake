"""故事编排契约；真实SQLite与网页HTTP另作固定范围验收。"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation
from tools import narrative_valuation as nv
from tests.test_native_assumption_selection import sample


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    monkeypatch.setattr(nv, 'runtime_identity', lambda _: {'test_engine':'fixed'})
    database = tmp_path/'valuation.sqlite'
    monkeypatch.setenv('US_CN_HK_DB_PATH', str(database))
    metadata = dict(contract='alphalake-sqlite-v13', reference_snapshot_id='test',
        source_database_sha256='a'*64, report_period='2025-12-31', information_as_of='2026-10-07T00:00:00+00:00')
    with sqlite3.connect(database) as conn:
        conn.executescript('CREATE TABLE companies(ticker TEXT); CREATE TABLE metadata(key TEXT,value TEXT);')
        conn.executemany('INSERT INTO companies VALUES(?)', [('SHSE:600519',), ('SZSE:000001',)])
        conn.executemany('INSERT INTO metadata VALUES(?,?)', metadata.items())
    coverage = tmp_path/'coverage.json'
    nv.save(coverage, dict(contract_version='alphalake-native-coverage-v1',
        scope_policy='nonfinancial-tdx-damodaran-2026-v1', sqlite_sha256=nv.digest(database),
        source_database_sha256=metadata['source_database_sha256'], report_period=metadata['report_period'],
        information_as_of=metadata['information_as_of'], snapshot_candidates=2,
        scope_counts={'nonfinancial_by_reference':1, 'outside_financial_scope':1}, companies=[
            dict(ticker='SHSE:600519', scope='nonfinancial_by_reference'),
            dict(ticker='SZSE:000001', scope='outside_financial_scope')]))
    def calculate(inputs):
        inputs = CompanyValuationInput.model_validate(inputs)
        report = run_full_valuation(inputs)
        body = dict(inputs=inputs.model_dump(mode='json'), id='synthetic', reference_snapshot={'id':'test'},
            equity_bridge=report.equity_bridge, convertible_claim=report.convertible_claim,
            **{k:getattr(report, k).model_dump(mode='json') for k in
                ('ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final')})
        return SimpleNamespace(status_code=200, json=lambda:body)
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def delete(self, path): return SimpleNamespace(status_code=204)
        def post(self, path, json):
            if path.endswith('/from-database'):
                if json['ticker'] == 'SZSE:000001':
                    return SimpleNamespace(status_code=422, json=lambda:{'detail':'financial scope'})
                return calculate(sample()['inputs'])
            return calculate(json['inputs'])
    monkeypatch.setattr('fastapi.testclient.TestClient', Client)
    directory = tmp_path/'derived'/'prepared'
    nv.prepare(database, coverage, ['SHSE:600519', 'SZSE:000001'], directory)
    return database, directory


def conditional(document):
    story = document['stories'][0]
    story.update(status='conditional', title='显式扩产条件', narrative='明确给定两年销量规模、利润及总投入；教学条件不是公司预测。',
        annual_operating_plan=[dict(year=1, revenue_million_cny=1100., adjusted_operating_margin=.12, tax_rate=.25),
            dict(year=2, revenue_million_cny=1210., adjusted_operating_margin=.13, tax_rate=.25)],
        assumption_overrides={'valuation_assumptions.annual_net_reinvestment':[80.,90.]})
    story['claims'][0]['parameters'].append('valuation_assumptions.annual_net_reinvestment')
    return document


def test_story_to_native_value_and_explicit_revision(prepared, tmp_path):
    database, directory = prepared
    document = conditional(nv.read_json(directory/'stories.json'))
    alternate = deepcopy(document['stories'][0])
    alternate.update(id='competition-pressure', title='竞争压价条件')
    alternate['annual_operating_plan'][0]['adjusted_operating_margin'] = .10
    document['stories'].append(alternate)
    nv.save(directory/'stories.json', document)
    output = tmp_path/'derived'/'first'
    summary = nv.run(database, directory/'stories.json', output)
    assert summary['companies'] == 2 and summary['stories'] == 3
    assert summary['statuses'] == {'calculated_conditional':2, 'outside_scope':1}
    assert summary['selected_story'] is None and not summary['automatic_adoption']
    assert nv.verify(output)['calculated'] == 2
    body = nv.read_json(output/(document['stories'][0]['id']+'-result.json'))
    assert body['dcf']['fcff_projections'] == pytest.approx([19.,27.975])
    baseline = nv.read_json(directory/'shse-600519-baseline.json')
    for field in ('raw_financials', 'prepared_ttm', 'option_inputs', 'equity_bridge'):
        assert body['inputs'][field] == baseline['body']['inputs'][field]
    assert '竞争压价条件' in (output/'report.md').read_text()
    assert 'outside_scope' in (output/'summary.csv').read_text()
    document['previous_run_id'] = summary['run_id']
    document['stories'][0]['revision'] += 1
    document['stories'][0]['annual_operating_plan'][0]['adjusted_operating_margin'] = .11
    nv.save(directory/'revision.json', document)
    revised = tmp_path/'derived'/'revised'
    nv.run(database, directory/'revision.json', revised)
    comparison = nv.compare(output, revised)
    assert comparison['results'][0]['difference_per_share'] < 0
    assert comparison['document_changes'] and not comparison['automatic_adoption']
    with pytest.raises(ValueError, match='immutable'):
        nv.run(database, directory/'stories.json', output)


def test_story_accepts_equivalent_utc_representation_without_exempting_financial_overrides(prepared):
    _, directory = prepared
    document = conditional(nv.read_json(directory/'stories.json'))
    story = nv.Story.model_validate(document['stories'][0])
    baseline = nv.read_json(directory/'shse-600519-baseline.json')
    baseline['body']['inputs']['prepared_ttm'] = dict(financials=baseline['body']['ltm_financials'],
        period_start='2025-01-01', period_end='2025-12-31', information_as_of='2026-10-07T00:00:00+00:00',
        currency='CNY', money_unit='million_reporting_currency', shares_unit='million_shares', provenance={'source': 'synthetic'})
    request, binding = nv.compile_story(story, baseline)
    assert request['inputs']['prepared_ttm']['information_as_of'] == '2026-10-07T00:00:00Z'
    assert not any(change['path'].startswith('/prepared_ttm') for change in binding['input_changes'])
    assert CompanyValuationInput.model_validate(request['inputs']).prepared_ttm == CompanyValuationInput.model_validate(baseline['body']['inputs']).prepared_ttm
    altered = story.model_dump(mode='json')
    altered['assumption_overrides']['prepared_ttm.information_as_of'] = '2026-10-08T00:00:00Z'
    altered['claims'][0]['parameters'].append('prepared_ttm.information_as_of')
    with pytest.raises(ValueError, match='only existing assumption/reference'):
        nv.compile_story(nv.Story.model_validate(altered), baseline)


def test_draft_missing_bindings_and_protected_facts(prepared, tmp_path):
    database, directory = prepared
    summary = nv.run(database, directory/'stories.json', tmp_path/'derived'/'drafts')
    assert summary['statuses'] == {'draft':1, 'outside_scope':1}
    document = conditional(nv.read_json(directory/'stories.json'))
    story = document['stories'][0]
    base = nv.read_json(directory/'shse-600519-baseline.json')
    invalid = deepcopy(story)
    invalid['claims'][0]['parameters'].remove('margin')
    with pytest.raises(ValueError, match='bindings'):
        nv.Story.model_validate(invalid)
    for path, value, message in [('raw_financials', [], 'only existing'),
        ('valuation_assumptions.typo', 1., 'unknown'),
        ('valuation_assumptions.annual_forecast', [], 'operating path'),
        ('valuation_assumptions.failure_probability', .2, 'equity/claim'),
        ('methodology_choices.has_convertible', True, 'equity/claim'),
        ('methodology_choices.preferred_stock.shares', 1., 'equity/claim')]:
        invalid = deepcopy(story)
        invalid['assumption_overrides'][path] = value
        invalid['claims'][0]['parameters'].append(path)
        with pytest.raises(ValueError, match=message):
            nv.compile_story(nv.Story.model_validate(invalid), base)
    invalid = deepcopy(story)
    invalid['annual_operating_plan'][0]['revenue_million_cny'] = 3000.
    with pytest.raises(ValueError, match='less than or equal'):
        nv.compile_story(nv.Story.model_validate(invalid), base)
    invalid = deepcopy(document)
    invalid['stories'][0]['claims'][0]['evidence'] = ['missing']
    with pytest.raises(ValueError, match='registered evidence'):
        nv.Document.model_validate(invalid)


def test_hash_guard_and_semantic_tamper_rejection(prepared, tmp_path):
    database, directory = prepared
    nv.save(directory/'stories.json', conditional(nv.read_json(directory/'stories.json')))
    output = tmp_path/'derived'/'verified'
    nv.run(database, directory/'stories.json', output)
    path = output/'shse-600519-source-condition-result.json'
    body = nv.read_json(path)
    body['dcf']['fcff_projections'][0] += 1
    nv.save(path, body)
    with pytest.raises(ValueError, match='hash mismatch'):
        nv.verify(output)
    # 即使把哈希一并改写，实际引擎重放仍能捕获假的现金流。
    manifest = nv.read_json(output/'manifest.json')
    manifest['artifacts'][path.name] = nv.digest(path)
    nv.save(output/'manifest.json', manifest)
    with pytest.raises(ValueError, match='does not replay'):
        nv.verify(output)
    original = directory/'shse-600519-baseline.json'
    original.write_text('{}')
    with pytest.raises(ValueError, match='path/hash mismatch'):
        nv.run(database, directory/'stories.json', tmp_path/'derived'/'corrupt-source')


def test_evidence_hash_does_not_authorize_replacement_financial_facts(prepared, tmp_path):
    database, directory = prepared
    document = conditional(nv.read_json(directory/'stories.json'))
    original = directory/'shse-600519-baseline.json'
    baseline = nv.read_json(original)
    baseline['body']['inputs']['raw_financials'][0]['revenues'] += 1
    nv.save(original, baseline)
    document['evidence'][0]['sha256'] = nv.digest(original)
    nv.save(directory/'stories.json', document)
    output = tmp_path/'derived'/'forged-source'
    with pytest.raises(ValueError, match='authoritative native source'):
        nv.run(database, directory/'stories.json', output)
    assert (output/'failure.json').exists() and not (output/'manifest.json').exists()


def test_pdf_is_a_canonical_source_reference_not_a_run_copy(prepared, tmp_path):
    from data_sources.cninfo_documents import canonical_pdf
    database, directory = prepared
    document = conditional(nv.read_json(directory/'stories.json'))
    original = tmp_path/'cninfo'/'original.pdf'; original.parent.mkdir()
    original.write_bytes(b'%PDF-1.7\noriginal company report')
    sha = nv.digest(original)
    pdf = canonical_pdf(original, sha)
    relative = str(pdf.relative_to(tmp_path))
    document['evidence'].append(dict(id='annual-report', kind='company_plan', file=relative,
        sha256=sha, locator='官方年报#page=10', explanation='经营规模和扩产计划，不是预测认证'))
    document['stories'][0]['claims'][0]['evidence'].append('annual-report')
    nv.save(directory/'stories.json', document)
    output = tmp_path/'derived'/'pdf-referenced'
    nv.run(database, directory/'stories.json', output)
    assert not list(output.rglob('*.pdf'))
    assert nv.read_json(output/'manifest.json')['pdf_references'] == {
        'annual-report': dict(file=relative, sha256=sha)}
    assert pdf.name in (output/'report.md').read_text()
    assert nv.verify(output)['calculated'] == 1
    pdf.write_bytes(b'%PDF-1.7\ntampered source')
    with pytest.raises(ValueError, match='path/hash mismatch'):
        nv.verify(output)
