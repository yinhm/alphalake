"""研发重分类保持税项与历史现金流，覆盖三个实际消费路径。"""
import pytest

from engine.company_metrics import compute_company_metrics
from engine.data_dictionary import RawFinancials, AdjustedFinancials, AdjustmentInputs, CostOfCapital, MacroInputs, ValuationAssumptions
from engine.module_1_adjustments import compute_adjustments
from engine.module_3_cashflow import compute_cashflow_and_growth
from engine.module_5_multiples import compute_multiples


@pytest.mark.parametrize('current_rd', [20.0, 100.0])
@pytest.mark.parametrize('leases', [False, True])
def test_rd_reclassification_preserves_tax_and_cashflow(current_rd, leases):
    raw = RawFinancials(fiscal_year=2025, revenues=1000, ebit=200, ebitda=240,
        net_income=150, bv_equity=500, bv_debt=300, cash_and_marketable_securities=50,
        capex=80, d_a=40, change_in_noncash_wc=10, net_debt_issued=20)
    prior = raw.model_copy(update={'fiscal_year':2024, 'revenues':900.0})
    capital = CostOfCapital(d_e_ratio=.4, beta_l=1.2, cost_of_equity=.1,
        cost_of_debt_pretax=.06, cost_of_debt_aftertax=.045,
        weight_equity=.7, weight_debt=.3, wacc=.08)
    macro = MacroInputs(risk_free_rate=.03, equity_risk_premium=.05,
        tax_rate_marginal=.25, tax_rate_effective=.20)
    base = AdjustmentInputs(has_operating_leases=leases, operating_lease_expense_current=30,
        operating_lease_commitments=[30]*6)
    rd = base.model_copy(update={'has_r_and_d':True, 'r_and_d_expense_current':current_rd,
                                 'r_and_d_expense_past':[40]*5})
    before = compute_adjustments(raw, base, .06)
    after = compute_adjustments(raw, rd, .06)
    old_cf = compute_cashflow_and_growth(before, raw, base, capital, prior, macro)
    new_cf = compute_cashflow_and_growth(after, raw, rd, capital, prior, macro)
    delta = current_rd-40
    assert new_cf.reinvestment_firm-old_cf.reinvestment_firm == pytest.approx(delta)
    assert new_cf.fcff == pytest.approx(old_cf.fcff)
    assert new_cf.fcfe == pytest.approx(old_cf.fcfe)
    # Prior research asset = 40 × (1 + 4/5 + 3/5 + 2/5 + 1/5) = 120.
    assert new_cf.adjusted_invested_capital == pytest.approx(750+120)
    expected_nopat = (200+before.lease_adjustment_to_ebit)*.8+delta
    assert new_cf.fcff+new_cf.reinvestment_firm == pytest.approx(expected_nopat)
    metrics = compute_company_metrics([raw, prior], adjusted=after, tax_rate=.20)
    current_ic = 750+after.value_of_research_asset+after.pv_of_operating_leases
    assert metrics.roic == pytest.approx(expected_nopat/current_ic)
    assert metrics.marginal_sales_to_capital == pytest.approx(100/delta)
    multiples = compute_multiples(after, raw, new_cf, capital, ValuationAssumptions(stable_growth_rate=.02), macro)
    expected_value = old_cf.fcff/(.08-.02)
    assert multiples.ev_sales_intrinsic == pytest.approx(expected_value/1000)
    assert multiples.ev_ebitda_intrinsic == pytest.approx(expected_value/240)


def test_anker_reported_rd_matches_frozen_tax_unchanged_scenarios():
    import csv
    from decimal import Decimal
    from pathlib import Path
    from engine.module_1_adjustments import after_tax_operating_income

    root = Path(__file__).resolve().parents[4]/'internal/ingest/testdata/anker-history-2026'
    with (root/'annual-inputs.csv').open() as f:
        history = {int(r['year']):r for r in csv.DictReader(f)}
    with (root/'rd-capitalization.csv').open() as f:
        scenarios = list(csv.DictReader(f))
    for row in scenarios:
        delta = Decimal(row['ebit_and_reinvestment_adjustment'])
        raw = RawFinancials(fiscal_year=2025, revenues=float(history[2025]['revenue']),
            ebit=float(Decimal(row['adjusted_ebit'])-delta))
        inputs = AdjustmentInputs(has_r_and_d=True, amortization_period_n=5,
            r_and_d_expense_current=float(history[2025]['rd_expense']),
            r_and_d_expense_past=[float(history[y]['rd_expense']) for y in range(2024,2019,-1)])
        adjusted = compute_adjustments(raw, inputs, .05)
        assert adjusted.value_of_research_asset == pytest.approx(float(row['closing_research_asset']), abs=.00001, rel=0)
        opening = adjusted.value_of_research_asset-inputs.r_and_d_expense_current+adjusted.amortization_r_and_d
        assert opening == pytest.approx(float(row['opening_research_asset']), abs=.00001, rel=0)
        # Frozen scenario tax was rounded to cents; preserve that exact tax amount.
        tax_rate = float(Decimal(row['unchanged_modelled_tax'])/(Decimal(row['adjusted_ebit'])-delta))
        assert after_tax_operating_income(adjusted, raw, tax_rate) == pytest.approx(
            float(row['adjusted_nopat']), abs=.00001, rel=0)
        assert row['fcff'] == ''  # This real sample still lacks full operating reinvestment.


def test_zero_adjusted_capital_has_no_book_capital_ratio_fallback():
    raw = RawFinancials(fiscal_year=2025, revenues=1000, ebit=100,
        bv_equity=100, bv_debt=0, cash_and_marketable_securities=0, cross_holdings=100)
    metrics = compute_company_metrics([raw], adjusted=AdjustedFinancials(adjusted_ebit=100), tax_rate=.2)
    assert metrics.sales_to_capital is None
    assert metrics.roic is None
