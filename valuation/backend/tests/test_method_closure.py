"""两份真实主库标准请求的离线方法回归；数据库实时查询另记验收。"""
from copy import deepcopy
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
    receipt = json.loads((ROOT.parents[2]/'docs/acceptance/method-closure-20260912.json').read_text())
    expected = next(r for r in receipt['companies'] if r['code'] == code)
    assert hashlib.sha256(raw).hexdigest() == expected['request_snapshot']['sha256']
    request = json.loads(gzip.decompress(raw))
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
    bad = deepcopy(request);bad['data']['windows'] = [w for w in bad['data']['windows'] if w['field'] != 'FN52']
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
        req = AlphaLakeRequest.model_validate_json(gzip.decompress((ROOT/f'{code}-request.json.gz').read_bytes()))
        inputs, audit = build_inputs(req)
        report = run_full_valuation(inputs)
        review = growth_capital_consistency(inputs, report, audit)
        assert review['company_capital_efficiency_verified'] is False
        assert review['company_evidence']['rd_expense_million_cny'] > 0
        assert review['company_evidence']['cash_capex_million_cny'] > 0
        incomplete = req.model_copy(deep=True)
        incomplete.data.windows = [w for w in incomplete.data.windows if w['field'] != 'FN304']
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
