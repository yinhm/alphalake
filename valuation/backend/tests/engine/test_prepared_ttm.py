"""Explicit TTM and missing-data regression at shared calculation boundaries."""
import pytest
from pydantic import ValidationError
from engine.data_dictionary import (RawFinancials, PreparedTTM, CompanyValuationInput,
    MacroInputs, IndustryData, MethodologyChoices, ValuationAssumptions)
from engine.ltm_calculator import compute_ltm_financials
from engine.orchestrator import run_full_valuation


def payload():
    raw = RawFinancials(fiscal_year=2026, revenues=100, ebit=20, capex=5, d_a=2,
        net_income=15, bv_equity=40, bv_debt=10, cash_and_marketable_securities=3,
        minority_interests=0, cross_holdings=0, shares_outstanding=10)
    ttm = PreparedTTM(financials=raw, period_start='2025-07-01', period_end='2026-06-30',
        information_as_of='2026-09-06T00:00:00Z', currency='CNY',
        money_unit='million_reporting_currency', shares_unit='million_shares',
        provenance={'source': 'explicit test fixture'})
    return CompanyValuationInput(ticker='TEST', reporting_currency='CNY', prepared_ttm=ttm,
        macro_inputs=MacroInputs(risk_free_rate=.03, equity_risk_premium=.06, tax_rate_marginal=.25),
        industry_data=IndustryData(industry_name='unused', beta_u=0),
        methodology_choices=MethodologyChoices(cost_of_capital_approach='direct', wacc_direct_input=.09),
        valuation_assumptions=ValuationAssumptions(revenue_growth_next_year=.1))


def test_prepared_roundtrip_missing_and_explicit_zero():
    inputs = CompanyValuationInput.model_validate_json(payload().model_dump_json())
    report = run_full_valuation(inputs)
    assert report.ltm_financials.revenues == 100
    assert report.cashflow.fcff is None and report.cashflow.fcfe is None
    assert report.multiples.pe_ratio_intrinsic is None
    assert report.multiples.ev_sales_intrinsic is None
    assert len(report.dcf.fcff_projections) == 10
    assert report.warnings
    inputs.prepared_ttm.financials.change_in_noncash_wc = 0
    inputs.prepared_ttm.financials.net_debt_issued = 0
    report = run_full_valuation(inputs)
    assert report.cashflow.fcff == 12
    assert report.cashflow.fcfe == 12


@pytest.mark.parametrize('changes', [dict(quarters_since_10k=2), dict(reporting_currency='USD')])
def test_prepared_rejects_rotation_and_currency(changes):
    data = payload().model_dump() | changes
    with pytest.raises(ValidationError):
        CompanyValuationInput.model_validate(data)


@pytest.mark.parametrize('changes', [dict(period_start='2026-01-01'),
    dict(information_as_of='2026-06-01T00:00:00Z'), dict(information_as_of='2026-09-06T00:00:00'),
    dict(money_unit='CNY'), dict(provenance={})])
def test_prepared_metadata(changes):
    with pytest.raises(ValidationError):
        PreparedTTM.model_validate(payload().prepared_ttm.model_dump() | changes)


def test_quarterly_missing():
    annual = RawFinancials(fiscal_year=2025, revenues=100, ebit=20, capex=8, bv_debt=10)
    quarter = RawFinancials(fiscal_year=2026, revenues=30, ebit=6)
    qs = [quarter] * 6
    assert compute_ltm_financials(annual, qs, 2).capex is None
    assert compute_ltm_financials(annual, qs, 2).bv_debt is None
    with pytest.raises(ValueError, match='insufficient'):
        compute_ltm_financials(annual, qs[:2], 2)
    complete = [quarter.model_copy(update={'capex': 0}) for _ in range(6)]
    assert compute_ltm_financials(annual, complete, 2).capex == 8


@pytest.mark.parametrize('field', ['bv_debt', 'cash_and_marketable_securities',
    'minority_interests', 'cross_holdings', 'shares_outstanding'])
def test_missing_bridge_does_not_publish_price(field):
    inputs = payload()
    setattr(inputs.prepared_ttm.financials, field, None)
    report = run_full_valuation(inputs)
    assert report.dcf.value_of_operating_assets is not None
    assert report.dcf.value_per_share_pre_options is None
    assert report.final.value_per_share is None
