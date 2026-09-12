"""
Tests for Module 4: DCF Valuation.
"""

import pytest

from engine.data_dictionary import (
    CashFlowMetrics, CostOfCapital, AdjustedFinancials,
    RawFinancials, ValuationAssumptions, MacroInputs,
)
from engine.module_4_dcf import compute_dcf


@pytest.fixture
def macro():
    return MacroInputs(
        risk_free_rate=0.04,
        equity_risk_premium=0.05,
        tax_rate_marginal=0.21,
    )


@pytest.fixture
def cost_of_capital():
    return CostOfCapital(
        d_e_ratio=0.3,
        beta_l=1.1,
        cost_of_equity=0.095,
        cost_of_debt_pretax=0.055,
        cost_of_debt_aftertax=0.04345,
        weight_equity=0.77,
        weight_debt=0.23,
        wacc=0.0832,
    )


@pytest.fixture
def adjusted():
    return AdjustedFinancials(
        adjusted_ebit=200.0,
        adjusted_net_income=150.0,
        adjusted_mv_debt=300.0,
    )


@pytest.fixture
def raw():
    return RawFinancials(
        fiscal_year=0,
        revenues=1000.0,
        ebit=200.0,
        cash_and_marketable_securities=100.0, minority_interests=0.0, cross_holdings=0.0,
        shares_outstanding=50.0,
    )


@pytest.fixture
def cf_metrics():
    return CashFlowMetrics(
        adjusted_capex=80.0,
        adjusted_d_a=40.0,
        reinvestment_firm=50.0,
        fcff=108.0,
        expected_growth_ebit=0.08,
        rir_firm=0.35,
    )


class TestDCF:

    def test_basic_dcf(self, cf_metrics, cost_of_capital, adjusted, raw, macro):
        """Basic 10-year DCF projection.

        Note: revenue_growth_next_year must be explicitly set. Since the
        silent ROIC × RIR cascade was removed (commit e6b60ee), a blank
        growth input stays blank → 0% growth → flat EBIT. The folder's
        philosophy (module_05 §2) is that growth is analyst judgment; this
        test now makes that judgment explicit with a 4% year-1 growth rate.
        """
        assumptions = ValuationAssumptions(
            projection_years=10,
            high_growth_years=5,
            revenue_growth_next_year=0.04,   # 4% growth — explicit story input
        )

        result = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)

        # Should have 10 years of projections
        assert len(result.ebit_projections) == 10
        assert len(result.fcff_projections) == 10
        assert len(result.discount_factors) == 10

        # EBIT should grow year over year in high-growth period
        for i in range(1, 5):
            assert result.ebit_projections[i] > result.ebit_projections[i - 1]

        # Terminal value should be positive
        assert result.terminal_value_firm > 0
        assert result.pv_terminal_value > 0

        # Value of equity should be positive (firm value + cash - debt)
        assert result.value_of_equity is not None
        assert result.value_per_share_pre_options is not None

    def test_discount_factors_decrease(self, cf_metrics, cost_of_capital, adjusted, raw, macro):
        """Discount factors should decrease over time."""
        assumptions = ValuationAssumptions(projection_years=5, high_growth_years=5)
        result = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)

        for i in range(1, len(result.discount_factors)):
            assert result.discount_factors[i] < result.discount_factors[i - 1]

    def test_pv_equals_fcff_times_df(self, cf_metrics, cost_of_capital, adjusted, raw, macro):
        """PV(FCFF) = FCFF * discount_factor for each year."""
        assumptions = ValuationAssumptions(projection_years=5, high_growth_years=5)
        result = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)

        for fcff, df, pv in zip(result.fcff_projections, result.discount_factors, result.pv_fcff):
            assert pv == pytest.approx(fcff * df, rel=1e-6)

    def test_value_bridge(self, cf_metrics, cost_of_capital, adjusted, raw, macro):
        """Value of equity = operating assets + cash - debt."""
        assumptions = ValuationAssumptions(projection_years=5, high_growth_years=5)
        result = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)

        expected_equity = result.value_of_operating_assets + 100.0 - 300.0
        assert result.value_of_equity == pytest.approx(expected_equity, rel=1e-6)

    def test_stable_growth_capped_at_rf(self, cf_metrics, cost_of_capital, adjusted, raw, macro):
        """Stable growth should be capped at risk-free rate."""
        assumptions = ValuationAssumptions(
            projection_years=5,
            high_growth_years=5,
            stable_growth_rate=0.10,  # Higher than Rf=4%
        )
        result = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)

        # 比较整份DCF，防止“正值”断言漏掉未实际执行的增长上限。
        capped = assumptions.model_copy(update={'stable_growth_rate': macro.risk_free_rate})
        expected = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, capped, macro)
        assert result == expected
        # 选择较低增长且有超额回报，使负向对照不依赖无效Gordon分母。
        lower = assumptions.model_copy(update={'stable_growth_rate': 0.02, 'roic_stable_override': 0.12})
        higher = assumptions.model_copy(update={'roic_stable_override': 0.12})
        low = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, lower, macro)
        high = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, higher, macro)
        assert high.terminal_value_firm > low.terminal_value_firm

    def test_shares_outstanding_affects_per_share(self, cf_metrics, cost_of_capital, adjusted, macro):
        """More shares → lower value per share."""
        raw_few = RawFinancials(fiscal_year=0, revenues=1000.0, ebit=200.0,
                                cash_and_marketable_securities=100.0, minority_interests=0.0, cross_holdings=0.0, shares_outstanding=10.0)
        raw_many = RawFinancials(fiscal_year=0, revenues=1000.0, ebit=200.0,
                                 cash_and_marketable_securities=100.0, minority_interests=0.0, cross_holdings=0.0, shares_outstanding=100.0)
        assumptions = ValuationAssumptions(projection_years=5, high_growth_years=5)

        r1 = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw_few, assumptions, macro)
        r2 = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw_many, assumptions, macro)

        assert r1.value_per_share_pre_options > r2.value_per_share_pre_options

    def test_failure_probability(self, cf_metrics, cost_of_capital, adjusted, raw, macro):
        """Failure probability should reduce equity value."""
        assumptions_no_fail = ValuationAssumptions(projection_years=5, high_growth_years=5)
        assumptions_fail = ValuationAssumptions(
            projection_years=5, high_growth_years=5,
            failure_probability=0.10, distress_proceeds_pct=0.3,
        )

        r1 = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions_no_fail, macro)
        r2 = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions_fail, macro)

        # With positive equity, failure probability reduces value
        if r1.value_of_equity > 0:
            assert r2.value_of_equity < r1.value_of_equity


def test_damodaran_terminal_reinvestment_identity(cf_metrics, cost_of_capital, adjusted, raw, macro):
    """固定预测期，独立检查g/ROIC；不把零净再投资当零现金资本开支。"""
    from engine.data_dictionary import ForecastYear
    wacc=0.09;terminal_nopat=200*(1-macro.tax_rate_marginal)
    for roic in (wacc,0.12):
        for g in (0,0.02,0.04):
            assumptions=ValuationAssumptions(projection_years=10,high_growth_years=5,
                annual_forecast=[ForecastYear(growth=0,margin=0.2,tax=macro.tax_rate_marginal) for _ in range(10)],
                override_reinvestment_lag=True,reinvestment_lag_years=0,sales_to_capital_high=3,sales_to_capital_stable=3,
                override_growth_perpetuity=True,growth_perpetuity_rate=g,
                cost_of_capital_stable_override=wacc,roic_stable_override=roic)
            result=compute_dcf(cf_metrics,cost_of_capital,adjusted,raw,assumptions,macro)
            assert result.reinvestment_projections==[0]*10
            assert result.fcff_projections==pytest.approx([terminal_nopat]*10)
            terminal_cash=terminal_nopat*(1+g)*(1-g/roic)
            assert result.terminal_value_firm==pytest.approx(terminal_cash/(wacc-g),rel=1e-12)
            if roic==wacc:
                # 中和增长再投资后，价值/下一年NOPAT=1/WACC。
                assert result.terminal_value_firm/(terminal_nopat*(1+g))==pytest.approx(1/wacc,rel=1e-12)
            assert cf_metrics.adjusted_capex==80 and cf_metrics.adjusted_d_a==40


@pytest.mark.parametrize('wacc,growth', [(0.02,0.02),(0.01,0.02),(float('nan'),0.02),(0.09,float('inf'))])
def test_invalid_terminal_denominator_rejected(wacc,growth,cf_metrics,cost_of_capital,adjusted,raw,macro):
    assumptions=ValuationAssumptions(override_growth_perpetuity=True,growth_perpetuity_rate=growth,
        cost_of_capital_stable_override=wacc,roic_stable_override=0.12)
    with pytest.raises(ValueError,match='terminal WACC must be finite'):
        compute_dcf(cf_metrics,cost_of_capital,adjusted,raw,assumptions,macro)


@pytest.mark.parametrize('roic', [0, -0.01, float('nan'), float('inf')])
def test_invalid_terminal_roic_rejected(roic, cf_metrics, cost_of_capital, adjusted, raw, macro):
    assumptions = ValuationAssumptions(override_growth_perpetuity=True, growth_perpetuity_rate=0.02,
        cost_of_capital_stable_override=0.09, roic_stable_override=roic)
    with pytest.raises(ValueError, match='terminal ROIC must be finite and positive'):
        compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)


@pytest.mark.parametrize('field', ['sales_to_capital_high', 'sales_to_capital_stable'])
@pytest.mark.parametrize('ratio', [0, -2, float('nan'), float('inf')])
def test_invalid_capital_efficiency_cannot_remove_reinvestment(field, ratio, cf_metrics, cost_of_capital, adjusted, raw, macro):
    assumptions = ValuationAssumptions(revenue_growth_next_year=0.1,
        sales_to_capital_high=2, sales_to_capital_stable=2)
    setattr(assumptions, field, ratio)
    with pytest.raises(ValueError, match='sales-to-capital must be finite and positive'):
        compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)


def test_valid_capital_efficiency_preserves_cash_release():
    from engine.module_4_dcf import _reinvestment_path
    # 收入下降可产生负净再投资；不能与无效资本效率混同归零。
    assert _reinvestment_path([100, 90, 90, 100], 2, 4, 0, 1, 3) == [-5, 0, 2.5]


def test_invalid_capital_efficiency_api_keeps_saved_session(raw, macro, monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api import routes, session_store
    from engine.data_dictionary import CompanyValuationInput
    monkeypatch.setattr(routes, '_get_damodaran_store', lambda: None)
    monkeypatch.setattr(routes, '_build_lookups', lambda _: (None, None))
    monkeypatch.setattr(session_store, '_sessions', {})
    client = TestClient(app)
    inputs = CompanyValuationInput(ticker='TEST', raw_financials=[raw], macro_inputs=macro,
        industry_data={'industry_name': 'Synthetic', 'beta_u': 1},
        valuation_assumptions=ValuationAssumptions(revenue_growth_next_year=0.1,
            sales_to_capital_high=2, sales_to_capital_stable=2))
    saved = client.post('/api/valuation', json={'inputs': inputs.model_dump()})
    assert saved.status_code == 200, saved.text
    original = saved.json(); sid = original['id']
    for field in ('sales_to_capital_high', 'sales_to_capital_stable'):
        failed = client.patch('/api/valuation/'+sid,
            json={'overrides': {'valuation_assumptions.'+field: 0}})
        assert failed.status_code == 422
        assert failed.json()['detail']['status'] == 'rejected_input_or_policy'
        assert client.get('/api/valuation/'+sid).json() == original
    inputs.valuation_assumptions.sales_to_capital_high = -1
    assert client.post('/api/valuation', json={'inputs': inputs.model_dump()}).status_code == 422
    assert session_store.list_sessions() == [sid]


@pytest.mark.parametrize('missing', ['bv_equity', 'bv_debt', 'cash_and_marketable_securities'])
def test_implied_roic_requires_complete_opening_capital(cf_metrics, cost_of_capital, adjusted, raw, macro, missing):
    from decimal import Decimal as D
    raw = raw.model_copy(update=dict(bv_equity=500., bv_debt=300., cash_and_marketable_securities=100.))
    assumptions = ValuationAssumptions(revenue_growth_next_year=.05)
    complete = compute_dcf(cf_metrics, cost_of_capital, adjusted, raw, assumptions, macro)
    capital = D(700)
    for ebit, cash, reinvestment, roic in zip(complete.ebit_projections, complete.fcff_projections,
                                           complete.reinvestment_projections, complete.implied_roic_projections):
        assert roic == pytest.approx(float((D(str(cash))+D(str(reinvestment)))/capital))
        capital += D(str(reinvestment))
    incomplete = compute_dcf(cf_metrics, cost_of_capital, adjusted,
                             raw.model_copy(update={missing: None}), assumptions, macro)
    assert incomplete.implied_roic_projections == [None]*10
    assert incomplete.implied_roic_terminal is None
    for name in ('revenue_projections', 'fcff_projections', 'pv_fcff', 'value_of_operating_assets'):
        assert getattr(incomplete, name) == getattr(complete, name)
    # Explicitly supplied zero debt/cash is different from missing capital.
    zero = compute_dcf(cf_metrics, cost_of_capital, adjusted,
                       raw.model_copy(update=dict(bv_debt=0., cash_and_marketable_securities=0.)), assumptions, macro)
    assert zero.implied_roic_projections[0] == pytest.approx(
        (zero.fcff_projections[0]+zero.reinvestment_projections[0])/500.)
