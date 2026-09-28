"""自动选择不改变事实，不绕过亏损/收缩或失效参考的拒绝。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation
from tools.select_native_assumptions import select

ROOT = Path(__file__).resolve().parents[3]
POLICY = json.loads((ROOT/'valuation/examples/native-assumption-selection.json').read_text())


def sample():
    # Shared native model contract, deliberately synthetic company facts.
    from engine.data_dictionary import RawFinancials, IndustryData, MacroInputs
    inputs = CompanyValuationInput(ticker='SHSE:600519', reporting_currency='CNY', stock_price_currency='CNY',
        period_date_10k='2025-12-31', raw_financials=[RawFinancials(fiscal_year=2025-i,
            revenues=1000/(1.1**i), ebit=100, r_and_d_expense=0, bv_equity=500, bv_debt=100,
            cash_and_marketable_securities=50, cross_holdings=0,minority_interests=0,shares_outstanding=10) for i in range(6)],
        industry_data=IndustryData(industry_name='Beverage (Alcoholic)',beta_u=.7,cost_of_debt_pretax=.05),
        macro_inputs=MacroInputs(risk_free_rate=.04,equity_risk_premium=.04,tax_rate_marginal=.25))
    report = run_full_valuation(inputs)
    baseline = dict(inputs=inputs.model_dump(mode='json'), **{name:getattr(report,name).model_dump(mode='json')
        for name in ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')})
    # Selection requires an explicit reference WACC, supplied separately below.
    return baseline


def test_selection_requires_reference_wacc():
    baseline=sample()
    with pytest.raises(ValueError,match='reference WACC'):
        select(baseline,baseline['inputs'],[],'2026-09-27T00:00:00+00:00',POLICY)


def test_sustainability_evidence_keeps_accounting_and_history_gaps():
    from tools.review_native_policy import review_report
    inputs=CompanyValuationInput.model_validate(sample()['inputs'])
    inputs.adjustment_inputs.has_r_and_d=True
    inputs.industry_data.pretax_operating_margin=.2
    def review():
        report=run_full_valuation(inputs)
        return review_report(dict(inputs=inputs.model_dump(mode='json'),
            **{name:getattr(report,name).model_dump(mode='json') for name in
               ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')}))['sustainability_evidence']
    full=review()
    assert full['research_adjusted_margin_years']==1
    assert full['annual_rows'][0]['unavailable_research_cohort_years']==[]
    assert full['annual_rows'][1]['unavailable_research_cohort_years']==[2019]
    assert full['industry_references'][0]['pretax_unadjusted_operating_margin']==.2
    assert not full['industry_references'][0]['comparable_for_adjusted_target_selection']
    inputs.raw_financials.pop(2)
    short=review()
    assert short['absent_annual_years']==[2023]
    assert short['research_adjusted_margin_years']==0
    assert 2023 in short['annual_rows'][0]['unavailable_research_cohort_years']


def reference_inputs(baseline):
    inputs=deepcopy(baseline['inputs'])
    inputs['methodology_choices'].update(cost_of_capital_approach='reference_snapshot',reference_capital_inputs=dict(
        risk_free_rate=.02,beta_u=.7,mature_market_erp=.04,country_risk_contribution=.01,
        debt_weight=.2,tax_shield_rate=.25,debt_cost_pretax=.05))
    inputs['valuation_assumptions']['cost_of_capital_stable_override']=.08
    return inputs


def reference():
    return dict(subject='Beverage (Alcoholic)',metric='sales_to_invested_capital_ltm',region='global',
        value='2',status='reported',unit='dimensionless',method_code='provider_reported',sample_count=100,
        observation_date='2026-01-05',available_at='2026-09-20T00:00:00+00:00')


def test_selected_inputs_flow_to_engine_without_mutating_facts():
    baseline=sample();original=deepcopy(baseline)
    payload,audit=select(baseline,reference_inputs(baseline),[reference()], '2026-09-27T00:00:00+00:00',POLICY)
    assert baseline==original and payload['inputs']['raw_financials']==original['inputs']['raw_financials']
    assert audit['status']=='selected_conditional' and not audit['automatic_adoption']
    assert audit['economic_basis']['status']=='not_established'
    assert 'sustainable_adjusted_operating_margin' in audit['economic_basis']['unresolved']
    report=run_full_valuation(CompanyValuationInput.model_validate(payload['inputs']))
    assert report.dcf.revenue_projections[0]==pytest.approx(1100)
    growth=report.dcf.revenue_projections[1]/report.dcf.revenue_projections[0]-1
    assert .02<growth<.1  # fade starts in year two, not after five constant years
    assert report.dcf.revenue_projections[-1]/report.dcf.revenue_projections[-2]-1==pytest.approx(.02)
    assert report.dcf.reinvestment_projections[0]==pytest.approx((report.dcf.revenue_projections[1]-1100)/2)
    assert payload['inputs']['valuation_assumptions']['roic_stable_override']==.08
    assert report.cashflow.fcff is None


def test_perpetual_effective_tax_is_not_silently_inherited():
    baseline=sample(); inputs=reference_inputs(baseline)
    inputs['macro_inputs']['tax_rate_effective']=.10
    inputs['valuation_assumptions']['override_tax_convergence']=True
    payload,audit=select(baseline,inputs,[reference()], '2026-09-27T00:00:00+00:00',POLICY)
    assert payload is None
    assert 'perpetual_effective_tax_requires_explicit_marginal_tax_basis' in audit['issues']
    # An explicit phase override is the rate actually consumed by the engine.
    inputs['valuation_assumptions']['effective_tax_rate_override_years_1_5']=.25
    payload,audit=select(baseline,inputs,[reference()], '2026-09-27T00:00:00+00:00',POLICY)
    assert payload is not None and audit['economic_basis']['status']=='not_established'


@pytest.mark.parametrize('key,value', [('region','us'),('sample_count',29),('value','NaN'),('value','0'),
    ('unit','percent'),('observation_date','2024-01-01'),('observation_date','2027-01-01'),
    ('available_at','2027-01-01T00:00:00+00:00'),('status','missing')])
def test_bad_reference_rejects_without_default(key,value):
    baseline=sample();row=reference();row[key]=value
    payload,audit=select(baseline,reference_inputs(baseline),[row], '2026-09-27T00:00:00+00:00',POLICY)
    assert payload is None and audit['decisions']['capital']['status']=='rejected'


def test_decline_missing_quarters_and_losses_are_not_repaired():
    for problem in ('decline','loss','missing_quarters'):
        baseline=sample();inputs=CompanyValuationInput.model_validate(baseline['inputs'])
        if problem=='decline':inputs.raw_financials[0].revenues=800
        elif problem=='loss':inputs.raw_financials[0].ebit=-10
        else:
            inputs.quarters_since_10k=2;inputs.period_date_10q='2026-06-30'
            with pytest.raises(ValueError,match='insufficient quarterly'):
                run_full_valuation(inputs)
            continue
        report=run_full_valuation(inputs)
        baseline=dict(inputs=inputs.model_dump(mode='json'),**{name:getattr(report,name).model_dump(mode='json')
            for name in ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')})
        payload,audit=select(baseline,reference_inputs(baseline),[reference()], '2026-09-27T00:00:00+00:00',POLICY)
        assert payload is None and audit['issues']


def test_ambiguous_reference_and_unknown_policy_rejected():
    baseline=sample()
    payload,audit=select(baseline,reference_inputs(baseline),[reference(),reference()], '2026-09-27T00:00:00+00:00',POLICY)
    assert payload is None and 'unique matching Global capital reference required' in audit['issues']
    policy=deepcopy(POLICY);policy['growth']='three_year_revenue_cagr'
    with pytest.raises(ValueError,match='unsupported'):
        select(baseline,reference_inputs(baseline),[reference()], '2026-09-27T00:00:00+00:00',policy)


def test_independent_history_retains_research_when_ebit_missing():
    from tools.review_native_policy import annual_capital_evidence
    rows=[dict(fiscal_year=2025-i,r_and_d_expense=10,ebit=100 if i==0 else None,
        revenues=1000,bv_equity=500,bv_debt=100,cash_and_marketable_securities=50) for i in range(6)]
    evidence=annual_capital_evidence(rows,5)
    assert evidence[0]['research_adjusted_margin']==.1
    assert evidence[0]['research_asset_million_cny']==30
    assert evidence[0]['research_adjusted_sales_to_capital']==pytest.approx(1000/580)
    assert evidence[1]['research_adjusted_margin'] is None
    assert evidence[1]['research_asset_million_cny']==30
    rows.pop(2)
    assert annual_capital_evidence(rows,5)[0]['research_adjusted_margin'] is None
    assert annual_capital_evidence(rows,None)[0]['research_adjusted_margin']==.1
    from tools.review_native_policy import review_report
    rows[0].update(ticker='SZSE:300866',fy_offset=0)
    with pytest.raises(ValueError,match='identity/period'):
        review_report(sample(),dict(rows=[rows[0]],cells=[]))


def test_historical_capital_changes_reconcile_and_keep_gaps():
    from tools.review_native_policy import annual_capital_evidence
    rows=[dict(fiscal_year=2020+i,r_and_d_expense=10*(i+1),ebit=None,
        revenues=100+20*i,bv_equity=100+30*i,bv_debt=50+5*i,
        cash_and_marketable_securities=10+2*i) for i in range(4)]
    result=annual_capital_evidence(rows,2)
    bridge=result[0]['capital_change_bridge']
    assert bridge['capital_change_million_cny']==48  # 30+5-2+15，研发队列独立滚动
    assert [p['contribution_million_cny'] for p in bridge['components']]==[30,5,-2,15]
    assert bridge['revenue_change_per_capital_change']==pytest.approx(20/48)
    assert bridge['automatic_adoption'] is False
    changed=deepcopy(rows);changed[-1]['revenues']=100
    assert annual_capital_evidence(changed,2)[0]['capital_change_bridge']['revenue_change_per_capital_change']<0
    for equity in (142,100):  # 零和负资本增量不伪造正倍率
        changed=deepcopy(rows);changed[-1]['bv_equity']=equity
        b=annual_capital_evidence(changed,2)[0]['capital_change_bridge']
        assert b['status']=='nonpositive_capital_change' and b['revenue_change_per_capital_change'] is None
    changed=deepcopy(rows);changed[-2]['bv_debt']=None
    assert annual_capital_evidence(changed,2)[0]['capital_change_bridge']['status']=='missing_capital_components'
    changed=deepcopy(rows);changed[-1]['revenues']=None
    assert annual_capital_evidence(changed,2)[0]['capital_change_bridge']['status']=='missing_revenue'
    changed=deepcopy(rows);changed.pop(-2)
    assert annual_capital_evidence(changed,2)[0]['capital_change_bridge']['status']=='missing_previous_year'
    assert result[-1]['capital_change_bridge']['status']=='missing_previous_year'


def test_capital_scope_distinguishes_consolidation_from_investment_proxy():
    from tools.review_native_policy import annual_capital_evidence
    row=dict(fiscal_year=2025,r_and_d_expense=None,ebit=100,revenues=1000,
        bv_equity=500,bv_debt=100,cash_and_marketable_securities=50,
        minority_interests=20,cross_holdings=80)
    original=deepcopy(row)
    result=annual_capital_evidence([row],None)[0]
    scope=result['capital_scope_bridge']
    assert row==original
    assert result['research_adjusted_invested_capital_million_cny']==550
    assert scope['including_minority_capital_million_cny']==570
    assert scope['excluding_investment_proxy_capital_million_cny']==490
    assert scope['missing_inputs']==[] and not scope['automatic_adoption']
    for field in ('minority_interests','cross_holdings','bv_debt'):
        changed=deepcopy(row);changed[field]=None
        scope=annual_capital_evidence([changed],None)[0]['capital_scope_bridge']
        assert field in scope['missing_inputs']
        assert scope['excluding_investment_proxy_capital_million_cny'] is None
    changed=deepcopy(row);changed['cross_holdings']=1000
    assert annual_capital_evidence([changed],None)[0]['capital_scope_bridge']['excluding_investment_proxy_capital_million_cny']==-430
    changed=deepcopy(row);changed['minority_interests']=-20
    assert annual_capital_evidence([changed],None)[0]['capital_scope_bridge']['including_minority_capital_million_cny']==530
    scope=annual_capital_evidence([row],5)[0]['capital_scope_bridge']
    assert 'research_asset' in scope['missing_inputs'] and scope['input_capital_million_cny'] is None


def test_review_keeps_model_and_sqlite_bases_separate():
    from tools.review_native_policy import review_report
    body=sample()
    rows=[dict(r,ticker=body['inputs']['ticker'],fy_offset=2025-r['fiscal_year'])
          for r in body['inputs']['raw_financials']]
    original=deepcopy(body)
    rows[0]['ebit']=140
    rows[0]['cash_and_marketable_securities']=None
    # 独立年度仍完整，模型缺年不得从中静默补齐。
    rows.append(dict(rows[-1],fiscal_year=2019,fy_offset=6))
    review=review_report(body,dict(rows=rows,cells=[]))
    evidence=review['sustainability_evidence']
    assert evidence['cross_basis_merge_allowed'] is False
    assert evidence['input_basis_comparison'][0]['differences']==[
        dict(field='ebit',model_input=100,sqlite_input=140),
        dict(field='cash_and_marketable_securities',model_input=50,sqlite_input=None)]
    assert evidence['input_basis_comparison'][-1]['status']=='missing_or_ambiguous_model_year'
    assert review['margin_bridge']['model_input_ebit']==original['ltm_financials']['ebit']
    assert 'reported_ebit' not in review['margin_bridge']
    assert 'recent_reported_window' not in review
    assert body==original


@pytest.mark.parametrize('section,field,value',[
    ('raw_financials','ebit',101),
    ('raw_financials','cash_and_marketable_securities',60),
    ('adjustment_inputs','has_r_and_d',True),
    ('adjustment_inputs','amortization_period_n',3),
    ('industry_data','industry_name','Software'),
    ('root','period_date_10k','2024-12-31'),
])
def test_selection_rejects_mixed_financial_and_adjustment_basis(section,field,value):
    baseline=sample();candidate=reference_inputs(baseline)
    if section=='root':
        candidate[field]=value
    elif section=='raw_financials':
        candidate[section][0][field]=value
    else:
        candidate[section][field]=value
    original=deepcopy(candidate)
    with pytest.raises(ValueError,match='basis differs'):
        select(baseline,candidate,[reference()],'2026-09-27T00:00:00+00:00',POLICY)
    assert candidate==original


def test_legal_rebuild_uses_new_baseline_and_preserves_old_report(tmp_path):
    from tools.select_native_assumptions import rebuild
    evidence=tmp_path/'review.txt';evidence.write_text('合成测试：显式更新经营利润，不代表公司事实')
    baseline=sample();inputs=reference_inputs(baseline)
    inputs['raw_financials'][0]['ebit']=150
    request=dict(baseline=baseline,inputs=inputs,reason='合成测试的利润更新',
        evidence_files=[str(evidence)],reference_rows=[reference()],
        reference_cutoff='2026-09-27T00:00:00+00:00',policy=POLICY)
    original=deepcopy(request)
    result=rebuild(request)
    assert request==original
    assert result['status']=='calculated_conditional'
    expected=result['rebuilt_baseline']['adjusted']['adjusted_ebit']/result['rebuilt_baseline']['ltm_financials']['revenues']
    assert result['candidate']['inputs']['valuation_assumptions']['target_operating_margin']==expected
    assert result['prior_report']==baseline and result['input_changes']
    assert result['prior_replay_status']=='reproduced'
    assert result['run_id']==rebuild(request)['run_id']
    request['baseline']['final']['value_per_share']+=1
    rebuilt=rebuild(request)
    assert rebuilt['prior_replay_status']=='differs_under_current_engine_not_verified'
    assert rebuilt['candidate']==result['candidate']
    assert rebuilt['run_id']!=result['run_id']
    request['baseline']['inputs']['valuation_assumptions']['roic_stable_override']=0
    unavailable=rebuild(request)
    assert unavailable['prior_replay_status']=='unavailable_under_current_engine'
    assert unavailable['candidate']==result['candidate']
    assert unavailable['baseline_result_changes'] is None
    request['reason']=''
    with pytest.raises(ValueError,match='reason and evidence'):
        rebuild(request)


def test_controlled_growth_replays_and_preserves_other_assumptions():
    from tools.compare_native_growth import compare
    baseline = sample()
    prev = baseline['ltm_financials']['revenues']
    growth = []
    for revenue in baseline['dcf']['revenue_projections']:
        growth.append(revenue / prev - 1)
        prev = revenue
    scenarios = [dict(name='same', reason='equivalence', growth=growth),
                 dict(name='contraction', reason='negative growth retained', growth=[-.02]*10)]
    result = compare(baseline, scenarios)
    same, lower = result['scenarios']
    assert same['final']['value_per_share'] == pytest.approx(baseline['final']['value_per_share'])
    assert lower['dcf']['revenue_projections'][0] < baseline['ltm_financials']['revenues']
    assert lower['final']['value_per_share'] != same['final']['value_per_share']
    assert not result['automatic_adoption']
    for row in result['scenarios']:
        restored = deepcopy(row['inputs'])
        restored['valuation_assumptions']['annual_forecast'] = baseline['inputs']['valuation_assumptions']['annual_forecast']
        assert restored == baseline['inputs']
    for invalid in ([], scenarios + scenarios, [dict(name='short', reason='bad', growth=[.1])],
                    [dict(name='nan', reason='bad', growth=[float('nan')]*10)]):
        with pytest.raises(ValueError):
            compare(baseline, invalid)
    bad = deepcopy(baseline)
    bad['dcf']['revenue_projections'][0] += 1
    with pytest.raises(ValueError, match='does not replay'):
        compare(bad, scenarios)


def test_growth_comparison_rejects_implicit_terminal_tax_change():
    from tools.compare_native_growth import compare
    inputs = CompanyValuationInput.model_validate(sample()['inputs'])
    inputs.valuation_assumptions.high_growth_years = 10
    inputs.macro_inputs.tax_rate_effective = .1
    report = run_full_valuation(inputs)
    baseline = dict(inputs=inputs.model_dump(mode='json'), **{name:getattr(report,name).model_dump(mode='json')
        for name in ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')})
    with pytest.raises(ValueError, match='annual path changes baseline semantics'):
        compare(baseline, [dict(name='test', reason='tax mismatch', growth=[.1]*10)])
