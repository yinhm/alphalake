"""两份真实主库标准请求的离线方法回归；数据库实时查询另记验收。"""
from copy import deepcopy
from tools.migrate_standard_contract import upgrade_legacy
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from api.alphalake import evaluate
from data_sources.alphalake import AlphaLakeRequest
from tools.verify_nonfinancial_dcf import verify

ROOT = Path(__file__).resolve().parents[3]/'valuation/research/method-closure-20260912'


@pytest.mark.parametrize('code', ['300866','002032'])
def test_real_standard_method_closure(code,tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    raw = (ROOT/f'{code}-request.json.gz').read_bytes()
    receipt = upgrade_legacy(json.loads((ROOT.parents[2]/'docs/acceptance/method-closure-20260912.json').read_text()))
    expected = next(r for r in receipt['companies'] if r['code'] == code)
    assert hashlib.sha256(raw).hexdigest() == expected['request_snapshot']['sha256']
    request = upgrade_legacy(json.loads(gzip.decompress(raw)))
    baseline = evaluate(AlphaLakeRequest.model_validate(request))
    checked = verify(baseline)
    assert evaluate(AlphaLakeRequest.model_validate(request)) == baseline
    assert baseline['report']['dcf']['implied_roic_projections'] == [None]*10
    assert baseline['report']['dcf']['implied_roic_terminal'] is None
    assessment = baseline['method_assessment']
    assert assessment['reinvestment']['implied_roic_status'] == 'missing_opening_capital'
    assert assessment['operating_profit']['rd'] == 'expensed_not_capitalized'
    assert assessment['wacc']['basis'] == 'industry_reference_weights'
    assert assessment['equity_bridge']['options'] == 'not_priced_not_asserted_absent'
    assert len(assessment['unresolved']) == 5
    for key in ('fcff_projections','pv_fcff'):
        bad = deepcopy(baseline);bad['report']['dcf'][key][0] += 1
        with pytest.raises(ValueError,match=key): verify(bad)
    bad = deepcopy(baseline);bad['report']['cost_of_capital']['wacc'] += .01
    with pytest.raises(ValueError,match='wacc'): verify(bad)
    bad = deepcopy(request);bad['data']['windows'] = [w for w in bad['data']['windows'] if w['field'] != 'current_portion_noncurrent_liabilities']
    with pytest.raises(ValueError): evaluate(AlphaLakeRequest.model_validate(bad))
    bad = deepcopy(request);bad['policy']['terminal_growth'] = .5
    with pytest.raises(ValueError): evaluate(AlphaLakeRequest.model_validate(bad))
    bad = deepcopy(request);bad['policy']['approved_report_period'] = '2025-06-30'
    with pytest.raises(ValueError): evaluate(AlphaLakeRequest.model_validate(bad))
    # 只改资本效率倍率：净投入同比例改变，收入、利润、WACC与桥接均不变。
    changed = deepcopy(request);changed['capital_binding']['policy']['ratio_multiplier'] *= 2
    scenario = evaluate(AlphaLakeRequest.model_validate(changed));verify(scenario)
    for key in ('revenue_projections','ebit_projections'):
        assert scenario['report']['dcf'][key] == baseline['report']['dcf'][key]
    assert scenario['report']['cost_of_capital'] == baseline['report']['cost_of_capital']
    assert scenario['inputs']['equity_bridge'] == baseline['inputs']['equity_bridge']
    for before,after in zip(baseline['report']['dcf']['reinvestment_projections'],scenario['report']['dcf']['reinvestment_projections']):
        assert after == pytest.approx(before/2)
    assert float(checked['value_per_share']) == pytest.approx(baseline['report']['final']['value_per_share'])


def test_growth_capital_economic_diagnostics(tmp_path, monkeypatch):
    from api.alphalake import growth_capital_consistency
    from data_sources.alphalake import build_inputs
    from engine.orchestrator import run_full_valuation
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    for code in ('300866', '002032'):
        req = AlphaLakeRequest.model_validate(upgrade_legacy(json.loads(gzip.decompress((ROOT/f'{code}-request.json.gz').read_bytes()))))
        inputs, audit = build_inputs(req)
        report = run_full_valuation(inputs)
        review = growth_capital_consistency(inputs, report, audit)
        assert review['company_capital_efficiency_verified'] is False
        assert review['company_evidence']['rd_expense_million_cny'] > 0
        assert review['company_evidence']['cash_capex_million_cny'] > 0
        incomplete = req.model_copy(deep=True)
        incomplete.data.windows = [w for w in incomplete.data.windows if w['field'] != 'research_and_development_expense']
        missing_inputs, missing_audit = build_inputs(incomplete)
        assert missing_audit['company_capital_evidence']['rd_expense_million_cny'] is None
        assert run_full_valuation(missing_inputs).final.value_per_share == report.final.value_per_share
        assert report.cashflow.fcff is None
        assert report.dcf.implied_roic_projections == [None]*10
        for row in review['years']:
            assert row['revenue_contribution_million_cny']+row['margin_tax_contribution_million_cny'] == pytest.approx(row['nopat_change_million_cny'], abs=1e-8)
            assert row['fcff_million_cny'] == pytest.approx(row['nopat_million_cny']-row['net_reinvestment_million_cny'])
        release = [r['year'] for r in review['years'] if 'capital_release_requires_recoverability_evidence' in r['flags']]
        assert release == ([] if code == '300866' else [1,2,3,4,5,6])
        if code == '002032':
            assert all(review['years'][i]['revenue_linked_incremental_return_proxy'] is None for i in range(6))
        # 改变利润率与税率时不能仍把全部利润增长归给收入；零投入不计算回报。
        changed = inputs.model_copy(deep=True)
        changed.valuation_assumptions.annual_forecast[0].growth = 0
        changed.valuation_assumptions.annual_forecast[0].margin *= .8
        changed.valuation_assumptions.annual_forecast[0].tax = .3
        counter = growth_capital_consistency(changed, run_full_valuation(changed), audit)['years'][0]
        assert counter['revenue_contribution_million_cny'] == 0
        assert counter['revenue_linked_incremental_return_proxy'] is None
        assert counter['margin_tax_contribution_million_cny'] == pytest.approx(counter['nopat_change_million_cny'])
        # 高投入需求产生负FCFF，保留而非归零或拒绝；低边际回报给出审核信号。
        changed = inputs.model_copy(deep=True)
        changed.valuation_assumptions.annual_forecast[0].growth = .2
        changed.valuation_assumptions.sales_to_capital_high = .1
        row = growth_capital_consistency(changed, run_full_valuation(changed), audit)['years'][0]
        assert row['fcff_million_cny'] < 0
        assert 'negative_fcff_requires_funding_plan_not_automatic_rejection' in row['flags']
        assert 'revenue_growth_return_proxy_below_wacc' in row['flags']

def test_supor_restricted_asset_evidence():
    import runpy

    runpy.run_module('tools.verify_supor_reviewed_assets', run_name='__main__')


def test_partial_asset_policy_with_explicit_synthetic_archive(tmp_path, monkeypatch):
    """标准请求+人工归档关联夹具；不冒称镜像已入生产库。"""
    from data_sources.alphalake import content_hash
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    request = upgrade_legacy(json.loads(gzip.decompress((ROOT/'002032-request.json.gz').read_bytes())))
    baseline = evaluate(AlphaLakeRequest.model_validate(request))
    receipt = upgrade_legacy(json.loads((ROOT.parent/'reviewed-assets-20260917/supor/receipt.json').read_bytes()))
    request['policy'].update(policy_id='nonfinancial-reviewed-history-fcff-v1',
        financial_asset_policy='reviewed_standard_asset_addbacks', code='002032',
        reviewed_at=request['data']['information_as_of'], valid_until='2026-10-17T00:00:00Z',
        asset_addbacks=[])
    for field, total, restricted in (('noncurrent_assets_due_within_one_year', 'current_total', 'current_restricted'),
                                      ('other_debt_investments', 'noncurrent_total', 'noncurrent_restricted')):
        fact = next(f for f in request['data']['facts'] if f['field'] == field and f['period'] == '2026-06-30')
        fact['pdf_sha256'] = receipt['sha256']  # 人工关联夹具，只验证适配器边界。
        notes = []
        for key in (total, restricted):
            note = dict(code='002032', item=key, period='2026-06-30', value=receipt['amounts_cny'][key],
                unit='CNY', period_basis='instant', scope='consolidated_note_component',
                import_sha256='1'*64, reviewer='synthetic archive test', review_note='not a production archive',
                available_at=fact['available_at'], announcement_id=fact['announcement_id'],
                pdf_sha256=receipt['sha256'], pdf_url=fact['pdf_url'], pdf_page=86 if field == 'noncurrent_assets_due_within_one_year' else 87)
            request['data']['supplements'].append(note)
            notes.append(dict(item=key, import_sha256=note['import_sha256'], evidence_sha256=content_hash(note)))
        request['policy']['asset_addbacks'].append(dict(field=field, **notes[0],
            source_artifact_sha256=fact['artifact_sha256'], restricted_component=notes[1],
            classification='nonoperating_partially_restricted_financial_asset',
            valuation_basis='reported_book_value_proxy', recovery=1, review_note='exclude pledged component'))
    result = evaluate(AlphaLakeRequest.model_validate(request))
    verify(result)
    for key in ('revenue_projections', 'ebit_projections', 'reinvestment_projections', 'fcff_projections', 'value_of_operating_assets'):
        assert result['report']['dcf'][key] == baseline['report']['dcf'][key]
    expected = (1823720960 - 750000000 + 967473281.25 - 20000000)/1e6
    assert sum(v for k, v in result['inputs']['equity_bridge']['components'].items() if k.startswith('reviewed_asset_')) == pytest.approx(expected)
    assert result['report']['final']['value_per_share']-baseline['report']['final']['value_per_share'] == pytest.approx(expected/result['inputs']['equity_bridge']['shares'])
    haircut = deepcopy(request)
    for rule in haircut['policy']['asset_addbacks']:
        rule['recovery'] = .5
    discounted = evaluate(AlphaLakeRequest.model_validate(haircut))
    verify(discounted)
    assert sum(v for k, v in discounted['inputs']['equity_bridge']['components'].items() if k.startswith('reviewed_asset_')) == pytest.approx(expected*.5)
    for problem in ('missing', 'negative', 'too_large', 'changed', 'unit', 'period', 'pdf', 'scope', 'duplicate', 'classification'):
        bad = deepcopy(request)
        rule = bad['policy']['asset_addbacks'][0]
        note = next(r for r in bad['data']['supplements'] if r['item'] == rule['restricted_component']['item'])
        if problem == 'missing': bad['data']['supplements'].remove(note)
        elif problem in ('negative', 'too_large'):
            note['value'] = '-1' if problem == 'negative' else '3000000000'
            rule['restricted_component']['evidence_sha256'] = content_hash(note)
        elif problem == 'changed': note['value'] = '0'
        elif problem == 'unit': note['unit'] = 'USD'
        elif problem == 'period': note['period'] = '2025-06-30'
        elif problem == 'pdf': note['pdf_sha256'] = '0'*64
        elif problem == 'scope': note['scope'] = 'consolidated_statement'
        elif problem == 'duplicate': rule['restricted_component'] = {k:rule[k] for k in ('item','import_sha256','evidence_sha256')}
        else: rule['classification'] = 'nonoperating_unrestricted_financial_asset'
        with pytest.raises(ValueError):
            evaluate(AlphaLakeRequest.model_validate(bad))


def test_supor_real_mirror_export_replay(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    directory = ROOT.parent/'reviewed-assets-20260917/supor'
    requests = [upgrade_legacy(json.loads(gzip.decompress((directory/(name+'-request.json.gz')).read_bytes()))) for name in ('before','after')]
    before, after = [evaluate(AlphaLakeRequest.model_validate(r)) for r in requests]
    verify(before)
    verify(after)
    receipt = upgrade_legacy(json.loads((directory/'acceptance.json').read_bytes()))
    assert before['report']['final']['value_per_share'] == pytest.approx(receipt['before_value'])
    assert after['report']['final']['value_per_share'] == pytest.approx(receipt['after_value'])
    for key in ('revenue_projections','ebit_projections','reinvestment_projections','fcff_projections','value_of_operating_assets'):
        assert before['report']['dcf'][key] == after['report']['dcf'][key]
    expected_review = deepcopy(receipt['document_review'])
    for key in ('fetched_at', 'reviewed_at'):
        expected_review[key] = expected_review[key].replace('+00:00', 'Z')
    for note in requests[1]['data']['supplements']:
        assert note['document_provenance'] == expected_review
    bad = deepcopy(requests[1])
    bad['data']['information_as_of'] = '2026-09-17T00:00:00Z'
    with pytest.raises(ValueError, match='document review time'):
        AlphaLakeRequest.model_validate(bad)
    bad = deepcopy(requests[1])
    bad['data']['supplements'][0]['document_provenance']['canonical_url'] += '?changed'
    with pytest.raises(ValueError, match='document review identity'):
        AlphaLakeRequest.model_validate(bad)


def test_standard_contract_rejects_source_keys_and_preserves_economics(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    legacy = json.loads(gzip.decompress((ROOT/'300866-request.json.gz').read_bytes()))
    original = deepcopy(legacy)
    with pytest.raises(ValueError):
        AlphaLakeRequest.model_validate(legacy)
    current = upgrade_legacy(legacy)
    assert legacy == original
    baseline = evaluate(AlphaLakeRequest.model_validate(current))
    changed = deepcopy(current)
    for fact in changed['data']['facts']:
        fact['source_provider_field'] = 'opaque_vendor_identifier'
    assert evaluate(AlphaLakeRequest.model_validate(changed))['report'] == baseline['report']
    changed['data']['windows'][0]['field'] = 'FN110'
    with pytest.raises(ValueError, match='standard field identity'):
        AlphaLakeRequest.model_validate(changed)


def test_application_layers_do_not_depend_on_fn_identifiers():
    import re
    backend = Path(__file__).resolve().parents[1]
    paths = [p for directory in ('api', 'data_sources', 'engine') for p in (backend/directory).rglob('*.py')]
    paths += [backend/'tools'/name for name in ('company_valuation.py', 'batch_valuate_alphalake.py',
              'incremental_valuation.py', 'review_company_inputs.py', 'review_valuation_forecast.py')]
    for path in paths:
        assert not re.search(r'\bFN[0-9]+\b', path.read_text()), path
