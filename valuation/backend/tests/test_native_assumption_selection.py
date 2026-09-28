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
