"""One resolution contract for scalar, annual, display and sensitivity paths."""
import pytest
from pydantic import ValidationError

from engine.data_dictionary import (
    AdjustedFinancials, CashFlowMetrics, CostOfCapital, ForecastYear,
    MacroInputs, RawFinancials, ValuationAssumptions,
)
from engine.module_4_dcf import compute_dcf
from engine.sensitivity_ranges import _median_band_from_industry
from engine.data_dictionary import IndustryData


def test_resolved_values_preserve_blank_zero_and_annual_priority():
    raw = RawFinancials(fiscal_year=2025, revenues=1000, ebit=100)
    adjusted = AdjustedFinancials(adjusted_ebit=200)
    macro = MacroInputs(risk_free_rate=.04, equity_risk_premium=.04, tax_rate_marginal=.25)
    assumptions = ValuationAssumptions()

    def calculate():
        return compute_dcf(CashFlowMetrics(), CostOfCapital(wacc=.08, beta_l=1, d_e_ratio=0, cost_of_equity=.08, cost_of_debt_pretax=.05, cost_of_debt_aftertax=.0375, weight_equity=1, weight_debt=0), adjusted, raw, assumptions, macro)

    default = calculate()
    p = default.forecast_parameters
    assert p['operating_margin_next_year'].value == .2 != raw.ebit/raw.revenues
    assert p['operating_margin_next_year'].source == 'adjusted_base'
    assert p['target_operating_margin'].value == .2
    assert p['revenue_growth_years_2_5'].value == 0
    assert p['sales_to_capital_high'].value == p['sales_to_capital_stable'].value == 2.5
    assert p['sales_to_capital_high'].source == 'application_default'
    assumptions.revenue_growth_next_year = .12
    assert calculate().forecast_parameters['revenue_growth_years_2_5'].value == .12
    assumptions.operating_margin_next_year = 0
    assert calculate().forecast_parameters['target_operating_margin'].value == 0
    assert calculate().ebit_projections == [0]*10
    assumptions.operating_margin_next_year = None
    assert calculate().ebit_projections[0]/calculate().revenue_projections[0] == .2

    # Conflicting scalars stay as user inputs, but do not masquerade as effective.
    assumptions.target_operating_margin = .9
    assumptions.annual_forecast = [ForecastYear(growth=0, margin=.3-i*.01, tax=.15) for i in range(10)]
    assumptions.annual_sales_to_capital = [3]*10
    assumptions.stable_growth_rate = .01
    result = calculate()
    p = result.forecast_parameters
    assert p['operating_margin_next_year'].value == .3
    assert p['target_operating_margin'].value == pytest.approx(.21)
    assert not p['target_operating_margin'].editable
    assert p['revenue_growth_years_2_5'].value is None
    assert not p['sales_to_capital_high'].editable
    assert result.terminal_growth == .01
    assert result.terminal_revenue == 1010
    assert result.terminal_ebit == pytest.approx(1010*.21)
    assert result.terminal_tax_rate == .15
    assert result.tax_rate_projections == [.15]*10
    assert result.terminal_nopat == pytest.approx(result.terminal_ebit-result.terminal_cash_tax)

    # Local reference bands must never be labelled measured quartiles.
    assert _median_band_from_industry(IndustryData(industry_name='Synthetic', beta_u=1, revenue_growth=-.1), 'revenue_growth') == pytest.approx((-.12,-.08))
    for invalid in ({'margin_convergence_year':0}, {'projection_years':0},
                    {'revenue_growth_next_year':-1.01}, {'target_operating_margin':float('inf')}):
        with pytest.raises(ValidationError):
            ValuationAssumptions(**invalid)
