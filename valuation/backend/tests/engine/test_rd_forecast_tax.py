"""研发税基桥：独立税项/队列与长现金流核验，不只复跑共享引擎。"""
from copy import deepcopy
import math

import pytest
from pydantic import ValidationError

from engine.data_dictionary import (AdjustmentInputs, CashFlowMetrics, CostOfCapital,
    MacroInputs, RawFinancials, ResearchTaxForecast, ValuationAssumptions)
from engine.module_1_adjustments import compute_adjustments, after_tax_operating_income
from engine.module_4_dcf import compute_dcf


def calculate(ebits, *, expenses=None, deductions=None, growth=0, nol=0, terminal_expense=100, past=None):
    count = len(ebits)
    raw = RawFinancials(fiscal_year=2025, revenues=1000, ebit=200)
    adj = AdjustmentInputs(has_r_and_d=True, r_and_d_expense_current=100, r_and_d_expense_past=past or [40]*5)
    capital = CostOfCapital(wacc=.08, approach_used='detailed', weight_debt=.2, weight_equity=.8,
        beta_l=1, d_e_ratio=.25, cost_of_equity=.09,
        cost_of_debt_pretax=.06, cost_of_debt_aftertax=.045)
    assumptions = ValuationAssumptions(projection_years=count, high_growth_years=count,
        annual_forecast=[dict(growth=0, margin=e/1000, tax=.25) for e in ebits],
        stable_growth_rate=growth, cost_of_capital_stable_override=.08, roic_stable_override=.08,
        override_nol=True, nol_amount=nol, sales_to_capital_high=2,
        research_tax=ResearchTaxForecast(policy_id='synthetic-test', expenses=expenses or [100]*count,
            deductions=deductions or expenses or [100]*count, terminal_expense=terminal_expense,
            terminal_deduction=terminal_expense, tax_rate_basis='taxable_profit_rate'))
    return compute_dcf(CashFlowMetrics(), capital, compute_adjustments(raw, adj, .06), raw,
        assumptions, MacroInputs(risk_free_rate=.04, equity_risk_premium=.04, tax_rate_marginal=.25),
        adjustment_inputs=adj)


def test_roll_forward_taxes_and_total_reinvestment():
    dcf = calculate([248,236,224,212,200])
    assert dcf.research_amortization_projections == [52,64,76,88,100]
    assert dcf.tax_base_projections == [200]*5
    assert dcf.cash_tax_projections == [50]*5
    assert dcf.nopat_projections == [198,186,174,162,150]
    assert dcf.nol_projections == [0]*5
    assert dcf.reinvestment_projections == [0]*5
    assert dcf.fcff_projections == dcf.nopat_projections  # No second deduction of R-A.
    assert dcf.terminal_tax_transition_value == pytest.approx(0, abs=1e-9)
    # CIQ supplies ten observations even when the selected life is only five.
    assert calculate([248,236,224,212,200], past=[40]*5+[999]*5).tax_base_projections == [200]*5


def test_economic_profit_tax_loss_and_recovery():
    dcf = calculate([38,150], nol=0)
    assert dcf.tax_base_projections == [-10,114]
    assert dcf.nol_projections == [10,0]
    assert dcf.cash_tax_projections == [0,26]
    assert dcf.nopat_projections == [38,124]
    assert dcf.debt_tax_shield_availability == pytest.approx([0,104/114])
    assert dcf.wacc_tax_shield_adjustments == pytest.approx([.003,.003*10/114])
    # Economic loss can still be taxable when expensed R&D is less than amortization.
    declining = calculate([-10], expenses=[1], deductions=[1], terminal_expense=1)
    assert declining.tax_base_projections == [41]
    assert declining.nopat_projections == [-20.25]
    assert declining.nol_projections == [0]


def test_base_loss_does_not_create_unverified_cash_refund():
    raw = RawFinancials(fiscal_year=2025, revenues=1000, ebit=-10)
    adj = AdjustmentInputs(has_r_and_d=True, r_and_d_expense_current=100, r_and_d_expense_past=[30]*5)
    adjusted = compute_adjustments(raw, adj, .06)
    assert after_tax_operating_income(adjusted, raw, .25) == 60


@pytest.mark.parametrize('growth,nol,ebit', [(0,5000,200),(.03,5000,200),(-.03,5000,200),
    (-.03,100000,200),(0,0,-100),(.03,100,-10),(.03,0,200)])
def test_terminal_cohort_and_nol_match_independent_annual_cashflows(growth, nol, ebit):
    dcf = calculate([ebit], growth=growth, nol=nol)
    # Explicit independent 1000-year cash flow sum; no analytical engine tail helper.
    q = 1+growth
    terminal_ebit = ebit*q
    stable_a = sum(100/q**i for i in range(1,6))/5
    stable_base = terminal_ebit+stable_a-100
    stable_nopat = terminal_ebit-max(0,stable_base)*.25
    investment = stable_nopat*growth/.08
    cohorts = [100,100,40,40,40]
    loss = dcf.nol_projections[-1]
    discount = 1.0
    value = 0.0
    scale = 1.0
    for _ in range(1000):
        base = terminal_ebit*scale+sum(cohorts)/5-100*scale
        taxable = max(0,base-loss)
        cash_tax = taxable*.25
        fraction = taxable/base if base > 0 else 0
        loss = max(0,loss-base)
        discount /= 1+.08+.003*(1-fraction)
        value += (terminal_ebit*scale-cash_tax-investment*scale)*discount
        cohorts = [100*scale]+cohorts[:4]
        scale *= q
    assert dcf.terminal_value_firm == pytest.approx(value, rel=1e-11, abs=1e-8)
    assert dcf.terminal_cash_tax == pytest.approx(max(0,stable_base)*.25)
    assert dcf.terminal_nopat == pytest.approx(stable_nopat)
    assert dcf.terminal_value_firm == pytest.approx(
        dcf.terminal_fcff/(dcf.terminal_wacc-growth)+dcf.terminal_tax_transition_value)


def test_tax_inputs_reject_omissions_nonfinite_and_double_counted_incentives():
    tax = dict(policy_id='declared', expenses=[100]*5, deductions=[100]*5,
        terminal_expense=100, terminal_deduction=100, tax_rate_basis='reported_effective_rate_proxy')
    with pytest.raises(ValidationError, match='every projection'):
        ValuationAssumptions(projection_years=4, research_tax=tax)
    for field, value in [('expenses',[math.nan]*5), ('deductions',[-1]*5), ('deductions',[200]*5)]:
        changed = deepcopy(tax); changed[field] = value
        with pytest.raises(ValidationError):
            ResearchTaxForecast.model_validate(changed)


def test_explicit_tax_bridge_api_review_and_failed_patch_preserve_session(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api import routes, session_store
    from data_sources.damodaran_store import DamodaranStore
    from engine.data_dictionary import CompanyValuationInput
    from tools.review_native_policy import review_report
    from tools.evaluate_native_policy import research_tax_condition
    monkeypatch.setattr(routes, '_get_damodaran_store', lambda: DamodaranStore())
    monkeypatch.setattr(routes, '_build_industry_lookup', lambda _: None)
    monkeypatch.setattr(session_store, '_sessions', {})
    inputs = CompanyValuationInput(ticker='SHSE:600519', reporting_currency='CNY', stock_price_currency='CNY',
        period_date_10k='2025-12-31', raw_financials=[RawFinancials(fiscal_year=2025, revenues=1000,
            ebit=200, r_and_d_expense=100, shares_outstanding=10, bv_debt=0,
            cash_and_marketable_securities=0, minority_interests=0, cross_holdings=0)],
        macro_inputs=MacroInputs(risk_free_rate=.04, equity_risk_premium=.04, tax_rate_marginal=.25),
        adjustment_inputs=AdjustmentInputs(has_r_and_d=True, r_and_d_expense_current=100, r_and_d_expense_past=[40]*5),
        industry_data={'industry_name':'Synthetic','beta_u':1},
        valuation_assumptions=ValuationAssumptions(revenue_growth_next_year=.1, stable_growth_rate=.03))
    with TestClient(app) as client:
        baseline = client.post('/api/valuation', json={'inputs':inputs.model_dump(mode='json')}).json()
        before = deepcopy(baseline)
        request, evidence = research_tax_condition(baseline, .03)
        assert baseline == before and not evidence['automatic_adoption']
        assert request['inputs']['raw_financials'] == baseline['inputs']['raw_financials']
        response = client.post('/api/valuation', json=request)
        assert response.status_code == 200, response.text
        saved = response.json()
        review = review_report(saved)
        assert review['forecast'][0]['research_expense_million_cny'] == pytest.approx(110)
        assert review['forecast'][0]['research_amortization_million_cny'] == 52
        assert review['terminal']['nol_treatment'] == 'unlimited_carryforward_without_expiry_valued_in_tax_tail'
        for field in ('revenue_projections','ebit_projections','reinvestment_projections'):
            assert saved['dcf'][field] == baseline['dcf'][field]
        assert saved['final']['value_per_share'] > baseline['final']['value_per_share']
        sid = saved['id']
        invalid = deepcopy(request['inputs']['valuation_assumptions']['research_tax'])
        invalid['deductions'] = [0]
        failed = client.patch('/api/valuation/'+sid,
            json={'overrides':{'valuation_assumptions.research_tax':invalid}})
        assert failed.status_code == 422
        assert client.get('/api/valuation/'+sid).json() == saved
        with pytest.raises(ValueError, match='do not overwrite'):
            research_tax_condition(saved, .03)
