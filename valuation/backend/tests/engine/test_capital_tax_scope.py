"""合并账面资本与市场少数权益分离；亏损税盾沿现金流和折现路径传导。"""
import pytest
from engine.data_dictionary import (RawFinancials, AdjustedFinancials, CashFlowMetrics,
    CostOfCapital, MacroInputs, ValuationAssumptions)
from engine.module_3_cashflow import _compute_historical_series
from engine.module_4_dcf import compute_dcf


def test_consolidated_capital_is_independent_of_minority_market_claim():
    rows = [RawFinancials(fiscal_year=y, revenues=1000, ebit=100,
        bv_equity=500, consolidated_book_equity=550, bv_debt=100,
        cash_and_marketable_securities=50, minority_interests=200,
        earnings_before_tax=100, total_tax_expense=20) for y in (2025, 2024)]
    result = _compute_historical_series(rows, None)
    assert result['historical_s_c_by_year'] == [1000/600]*2
    assert result['historical_roic_by_year'] == [80/600, None]
    rows[1].minority_interests = 999
    assert _compute_historical_series(rows, None) == result
    rows[1].consolidated_book_equity = None
    assert _compute_historical_series(rows, None)['historical_roic_by_year'] == [None,None]


def calculate(margins, approach='detailed', nol=0):
    raw = RawFinancials(fiscal_year=2025, revenues=100, ebit=10,
        consolidated_book_equity=50, bv_equity=40, bv_debt=20,
        cash_and_marketable_securities=0, cross_holdings=0, minority_interests=10,
        shares_outstanding=10)
    a = ValuationAssumptions(annual_forecast=[dict(growth=0,margin=m,tax=.25) for m in margins],
        sales_to_capital_high=2, sales_to_capital_stable=2,
        override_growth_perpetuity=True, growth_perpetuity_rate=0,
        cost_of_capital_stable_override=.08, override_nol=True, nol_amount=nol)
    risk = CostOfCapital(approach_used=approach, d_e_ratio=.25,beta_l=1,
        cost_of_equity=.11375,cost_of_debt_pretax=.06,cost_of_debt_aftertax=.045,
        weight_equity=.8,weight_debt=.2,wacc=.1)
    return compute_dcf(CashFlowMetrics(),risk,AdjustedFinancials(adjusted_ebit=10,
        adjusted_bv_equity=40,adjusted_mv_debt=20),raw,a,
        MacroInputs(risk_free_rate=.04,equity_risk_premium=.04,tax_rate_marginal=.25))


def test_nol_absorption_changes_discount_not_unlevered_cashflows():
    result = calculate([-.1,.05,.1]+[.2]*7)
    assert result.debt_tax_shield_availability == [0,0,.5]+[1]*7
    assert result.wacc_tax_shield_adjustments == pytest.approx([.003,.003,.0015]+[0]*7)
    assert result.discount_factors[:3] == pytest.approx([1/1.103,1/1.103**2,1/(1.103**2*1.1015)])
    assert result.fcff_projections[:3] == [-10,5,8.75]
    assert result.terminal_wacc == .08 and result.terminal_roic == .08
    assert result.implied_roic_projections[0] == pytest.approx(-10/70)
    profitable = calculate([.2]*10)
    assert profitable.wacc_tax_shield_adjustments == [0]*10
    assert profitable.discount_factors[0] == 1/1.1


def test_terminal_loss_has_neither_cash_tax_credit_nor_debt_tax_shield():
    result = calculate([-.1]*10)
    assert result.terminal_wacc == pytest.approx(.083)
    assert result.terminal_roic == result.terminal_wacc
    assert result.terminal_value_firm == pytest.approx(-10/.083)
    assert result.terminal_tax_shield_adjustment == pytest.approx(.003)
    assert result.unused_nol_at_terminal == 100


def test_aggregate_wacc_cannot_invent_a_debt_component():
    result = calculate([-.1]*10, 'direct')
    assert result.wacc_tax_shield_adjustments == [0]*10
    assert 'not_decomposed' in result.tax_shield_basis
    with pytest.raises(ValueError,match='NOL'):
        calculate([.2]*10,nol=-1)


def test_company_metrics_use_same_capital_without_deducting_investment_bucket():
    from engine.company_metrics import compute_company_metrics
    rows = [RawFinancials(fiscal_year=y, revenues=revenue, ebit=100,
        bv_equity=500, consolidated_book_equity=550, bv_debt=100,
        cash_and_marketable_securities=50, cross_holdings=200)
        for y,revenue in ((2025,1000),(2024,900))]
    result = compute_company_metrics(rows,tax_rate=.2)
    assert result.sales_to_capital == 1000/600
    assert result.roic == 80/600
    rows[0].cash_and_marketable_securities = None
    assert compute_company_metrics(rows,tax_rate=.2).sales_to_capital is None
    rows[1].consolidated_book_equity = None
    assert compute_company_metrics(rows,tax_rate=.2).roic is None
