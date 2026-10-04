"""普通亏损尾部与显式融资路径：独立逐年现金流、解析算例及API拒绝。"""
from copy import deepcopy
import math

import pytest
from pydantic import ValidationError

from engine.data_dictionary import (AdjustedFinancials, CashFlowMetrics, CostOfCapital,
    DebtTaxShieldForecast, MacroInputs, RawFinancials, ValuationAssumptions)
from engine.module_4_dcf import compute_dcf, InvalidTaxForecast


def calculate(*, nol=1000, growth=0, ebit=200, ebits=None, spread=.003, financing=None, tax=.25):
    raw = RawFinancials(fiscal_year=2025, revenues=1000, ebit=ebit)
    profits = [ebit] if ebits is None else ebits
    a = ValuationAssumptions(projection_years=len(profits), high_growth_years=len(profits),
        annual_forecast=[dict(growth=0, margin=e/1000, tax=tax) for e in profits],
        stable_growth_rate=growth, cost_of_capital_stable_override=.08,
        roic_stable_override=.08, override_nol=True, nol_amount=nol,
        debt_tax_shield=financing, sales_to_capital_high=2)
    risk = CostOfCapital(wacc=.08, approach_used='detailed', weight_debt=.2,
        weight_equity=.8, beta_l=1, d_e_ratio=.25, cost_of_equity=.08875,
        cost_of_debt_pretax=.06, cost_of_debt_aftertax=.06-spread/.2)
    return compute_dcf(CashFlowMetrics(), risk, AdjustedFinancials(adjusted_ebit=ebit),
        raw, a, MacroInputs(risk_free_rate=.04, equity_risk_premium=.04, tax_rate_marginal=.25))


def test_four_year_tax_savings_are_discounted_not_immediate_or_lost():
    dcf = calculate(spread=0)
    savings = sum(50/1.08**year for year in range(1,5))
    assert dcf.unused_nol_at_terminal == 800
    assert dcf.terminal_cash_tax == 50 and dcf.terminal_first_year_cash_tax == 0
    assert dcf.terminal_fcff == 150
    assert dcf.terminal_tax_transition_value == pytest.approx(savings)
    assert dcf.terminal_value_firm == pytest.approx(1875+savings)
    assert savings < 800*.25
    assert dcf.terminal_nol_treatment == 'unlimited_carryforward_without_expiry_valued_in_tax_tail'
    normal = calculate(nol=0, spread=0)
    assert normal.terminal_value_firm == 1875 and normal.terminal_tax_transition_value == 0


@pytest.mark.parametrize('growth,nol,ebit,spread', [(0,1000,200,.003),(.03,5000,200,.003),
    (-.03,5000,200,.003),(-.03,100000,200,.003),(0,1050,200,.003),
    (0,0,-100,.003),(.03,0,200,0)])
def test_plain_terminal_matches_independent_thousand_year_sum(growth,nol,ebit,spread):
    dcf = calculate(nol=nol,growth=growth,ebit=ebit,spread=spread)
    profit = ebit*(1+growth)
    normal_nopat = profit-max(0,profit)*.25
    investment = normal_nopat*growth/.08
    loss = max(0,nol-ebit)
    scale = discount = 1.0
    value = 0.0
    for _ in range(1000):
        base = profit*scale
        taxable = max(0,base-loss)
        availability = taxable/base if base > 0 else 0
        loss = max(0,loss-base)
        discount /= 1+.08+spread*(1-availability)
        value += (base-taxable*.25-investment*scale)*discount
        scale *= 1+growth
    assert dcf.terminal_value_firm == pytest.approx(value, rel=1e-11, abs=1e-8)
    assert dcf.terminal_value_firm == pytest.approx(
        dcf.terminal_fcff/(dcf.terminal_wacc-growth)+dcf.terminal_tax_transition_value)


def test_explicit_financing_components_cover_forecast_and_tail_without_changing_fcff():
    year = dict(normal_wacc=.09, debt_weight=.2, cost_of_debt_pretax=.06, tax_shield_rate=.25)
    terminal = dict(normal_wacc=.07, debt_weight=.1, cost_of_debt_pretax=.04, tax_shield_rate=.25)
    path = dict(policy_id='synthetic-financing', years=[year], terminal=terminal)
    dcf = calculate(financing=path)
    assert dcf.debt_tax_shield_spreads == [.003]
    assert dcf.terminal_debt_tax_shield_spread == .001
    assert dcf.discount_factors == pytest.approx([1/1.093])
    assert dcf.fcff_projections == calculate().fcff_projections
    expected = sum(200/1.071**i for i in range(1,5))+150/.07/1.071**4
    assert dcf.terminal_value_firm == pytest.approx(expected)
    assert dcf.terminal_wacc == .07
    assert 'explicit_normal_wacc_and_debt_component_path' in dcf.tax_shield_basis
    debt_free = deepcopy(path)
    debt_free['years'][0]['debt_weight'] = debt_free['terminal']['debt_weight'] = 0
    free = calculate(financing=debt_free)
    assert free.wacc_tax_shield_adjustments == [0]
    assert free.terminal_debt_tax_shield_spread == 0


def test_financing_inputs_validate_length_units_and_finite_values():
    year = dict(normal_wacc=.08, debt_weight=.2, cost_of_debt_pretax=.06, tax_shield_rate=.25)
    path = dict(policy_id='declared', years=[year], terminal=year)
    with pytest.raises(ValidationError, match='every projection'):
        ValuationAssumptions(projection_years=2, debt_tax_shield=path)
    for field, value in [('normal_wacc',math.nan),('debt_weight',1.1),
            ('cost_of_debt_pretax',-.01),('tax_shield_rate',25)]:
        bad = deepcopy(path); bad['years'][0][field] = value
        with pytest.raises(ValidationError):
            DebtTaxShieldForecast.model_validate(bad)
    with pytest.raises(InvalidTaxForecast, match='tax rates'):
        raw=RawFinancials(fiscal_year=2025, revenues=1000, ebit=200)
        risk=CostOfCapital(wacc=.08,beta_l=1,d_e_ratio=0,cost_of_equity=.08,
            cost_of_debt_pretax=0,cost_of_debt_aftertax=0,weight_equity=1,weight_debt=0)
        compute_dcf(CashFlowMetrics(),risk,AdjustedFinancials(adjusted_ebit=200),
            raw,ValuationAssumptions(),MacroInputs(risk_free_rate=.04,equity_risk_premium=.04,tax_rate_marginal=-.25))


def test_each_forecast_year_uses_its_own_financing_component():
    years=[dict(normal_wacc=wacc,debt_weight=weight,cost_of_debt_pretax=cost,tax_shield_rate=.25)
        for wacc,weight,cost in [(.09,.2,.06),(.08,.1,.04),(.07,.05,.04)]]
    dcf=calculate(ebits=[-100,50,100],nol=0,
        financing=dict(policy_id='three-year-financing',years=years,terminal=years[-1]))
    assert dcf.debt_tax_shield_spreads == pytest.approx([.003,.001,.0005])
    assert dcf.debt_tax_shield_availability == [0,0,.5]
    assert dcf.wacc_tax_shield_adjustments == pytest.approx([.003,.001,.00025])
    assert dcf.discount_factors == pytest.approx([1/1.093,1/(1.093*1.081),1/(1.093*1.081*1.07025)])
    assert dcf.fcff_projections == [-100,50,87.5]


def test_api_tax_rejection_preserves_session_and_review_uses_valued_tail(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api import routes, session_store
    from data_sources.damodaran_store import DamodaranStore
    from tools.review_native_policy import review_report
    monkeypatch.setattr(routes,'_get_damodaran_store',lambda: DamodaranStore())
    monkeypatch.setattr(routes,'_build_industry_lookup',lambda _: None)
    monkeypatch.setattr(session_store,'_sessions',{})
    inputs=dict(ticker='SHSE:600519',reporting_currency='CNY',stock_price_currency='CNY',
        period_date_10k='2025-12-31',raw_financials=[dict(fiscal_year=2025,revenues=1000,ebit=200,
            shares_outstanding=10,bv_debt=0,cash_and_marketable_securities=0,minority_interests=0,cross_holdings=0)],
        macro_inputs=dict(risk_free_rate=.04,equity_risk_premium=.04,tax_rate_marginal=.25),
        industry_data=dict(industry_name='Synthetic',beta_u=1),
        valuation_assumptions=dict(projection_years=1,high_growth_years=1,override_nol=True,nol_amount=1000,
            stable_growth_rate=0,cost_of_capital_stable_override=.08,sales_to_capital_high=2))
    with TestClient(app) as client:
        response=client.post('/api/valuation',json={'inputs':inputs})
        assert response.status_code == 200, response.text
        saved=response.json(); sid=saved['id']
        review=review_report(saved)
        assert 'terminal_unused_nol_not_separately_valued' not in review['methodology']['findings']
        assert 'nol_utilization_requires_tax_law_and_profit_evidence' in review['methodology']['findings']
        for overrides in ({'valuation_assumptions.nol_amount':-1},
                {'macro_inputs.tax_rate_marginal':-.1},
                {'valuation_assumptions.debt_tax_shield':{'policy_id':'bad','years':[],'terminal':{}}}):
            bad=client.patch('/api/valuation/'+sid,json={'overrides':overrides})
            assert bad.status_code == 422, bad.text
            assert client.get('/api/valuation/'+sid).json() == saved
        year=dict(normal_wacc=.09,debt_weight=.2,cost_of_debt_pretax=.06,tax_shield_rate=.25)
        valid=client.patch('/api/valuation/'+sid,json={'overrides':{
            'valuation_assumptions.debt_tax_shield':dict(policy_id='user-financing',years=[year],terminal=year)}})
        assert valid.status_code == 200, valid.text
        changed=valid.json()
        assert changed['dcf']['fcff_projections'] == saved['dcf']['fcff_projections']
        assert changed['dcf']['discount_factors'] == pytest.approx([1/1.093])
        reviewed=review_report(changed)
        assert 'tax_shield_path_uses_constant_initial_debt_component' not in reviewed['methodology']['findings']
        assert reviewed['methodology']['terminal_wacc']['explicit_financing_policy']['policy_id'] == 'user-financing'
