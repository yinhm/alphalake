"""10月19日审计反例：索偿、输入域与辅助诊断，预期不沿用错误结果。"""
import math
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from engine.data_dictionary import (AdjustmentInputs, CompanyValuationInput, DCFResult,
    InvalidValuationInput, OptionInputs)
from engine.orchestrator import run_full_valuation
from engine.module_6_options import black_scholes_call, compute_options_and_final_value


def inputs(**changes):
    body=dict(ticker='AUDIT', reporting_currency='CNY', stock_price_currency='CNY',
        raw_financials=[dict(fiscal_year=2025,revenues=1000,ebit=200,net_income=135,
            interest_expense=20,capex=60,d_a=50,change_in_noncash_wc=10,net_debt_issued=0,
            cash_and_marketable_securities=100,bv_equity=600,consolidated_book_equity=600,
            bv_debt=400,mv_equity=2000,shares_outstanding=100,stock_price=20,
            minority_interests=0,cross_holdings=0)],
        macro_inputs=dict(risk_free_rate=.03,equity_risk_premium=.05,tax_rate_marginal=.25),
        industry_data=dict(industry_name='Synthetic',beta_u=1,cost_of_debt_pretax=.08),
        valuation_assumptions=dict(revenue_growth_next_year=.06,revenue_growth_years_2_5=.06,
            operating_margin_next_year=.2,target_operating_margin=.2,sales_to_capital_high=2,
            cost_of_capital_stable_override=.08))
    body.update(changes)
    return CompanyValuationInput.model_validate(body)


def test_debt_pricing_and_equity_bridge_use_same_claim():
    model=inputs(methodology_choices=dict(use_bond_pricing_for_debt=True,kd_approach='direct',kd_direct_input=.1))
    report=run_full_valuation(model)
    pv=sum(20/1.1**t for t in range(1,6))+400/1.1**5
    assert report.cost_of_capital.mv_debt_total==pytest.approx(pv)
    assert report.dcf.value_of_equity==pytest.approx(report.dcf.value_of_operating_assets-pv+100)
    assert sum(report.dcf.equity_bridge_components.values())+report.dcf.value_of_operating_assets==pytest.approx(report.dcf.value_of_equity)
    model.raw_financials[0].mv_debt=300
    explicit=run_full_valuation(model)
    assert explicit.cost_of_capital.mv_debt_total==300
    assert explicit.dcf.equity_bridge_components['debt']==-300


@pytest.mark.parametrize('approach',['detailed','direct'])
def test_preferred_deducted_once_even_with_aggregate_wacc(approach):
    model=inputs(methodology_choices=dict(cost_of_capital_approach=approach,wacc_direct_input=.08,
        has_preferred=True,preferred_stock=dict(shares=10,price_per_share=20,dividend_per_share=1)))
    report=run_full_valuation(model)
    assert report.dcf.equity_bridge_components['preferred']==-200
    assert report.dcf.value_of_equity==pytest.approx(report.dcf.value_of_operating_assets-400+100-200)


def test_research_cohort_missing_is_not_zero_and_mutation_is_checked():
    with pytest.raises(ValidationError,match='complete selected research cohort'):
        AdjustmentInputs(has_r_and_d=True,r_and_d_expense_current=100,r_and_d_expense_past=[])
    model=inputs(adjustment_inputs=dict(has_r_and_d=True,r_and_d_expense_current=100,r_and_d_expense_past=[0]*5))
    assert run_full_valuation(model).adjusted.amortization_r_and_d==0
    model.adjustment_inputs.amortization_period_n=6
    with pytest.raises(InvalidValuationInput,match='complete selected research cohort'):
        run_full_valuation(model)


def test_lease_discount_uses_selected_debt_cost_and_cashflow_does_not_add_free_depreciation():
    model=inputs(adjustment_inputs=dict(has_operating_leases=True,operating_lease_expense_current=100,
        operating_lease_commitments=[100]*5),methodology_choices=dict(kd_approach='direct',kd_direct_input=.02))
    report=run_full_valuation(model)
    assert report.adjusted.pv_of_operating_leases==pytest.approx(sum(100/1.02**t for t in range(1,6)))
    assert report.cashflow.fcfe is None and report.cashflow.fcff is None
    assert report.cashflow.reinvestment_firm is None
    assert report.final.value_per_share is not None
    model.methodology_choices.kd_direct_input=0
    assert run_full_valuation(model).adjusted.pv_of_operating_leases==500
    model.methodology_choices.kd_approach='synthetic_rating'
    with pytest.raises(InvalidValuationInput,match='circular'):
        run_full_valuation(model)


def test_black_scholes_continuous_limits():
    expected=100-100*math.exp(-.05)
    assert black_scholes_call(100,100,1,.05,0)==pytest.approx(expected)
    assert black_scholes_call(100,100,1,.05,1e-12)==pytest.approx(expected)
    assert black_scholes_call(100,0,1,.05,.3,.02)==pytest.approx(100*math.exp(-.02))
    assert black_scholes_call(110,100,0,.05,.3)==10
    with pytest.raises(InvalidValuationInput):black_scholes_call(100,100,1,.05,-.3)


def test_option_iteration_refuses_unconverged_claims_and_exports_actual_state():
    raw=DCFResult(value_of_equity=1000,value_per_share_pre_options=10)
    options=OptionInputs(has_options=True,number_of_options=10000,average_strike_price=.01,
        average_maturity=10,stock_price_std_dev=.3)
    with pytest.raises(InvalidValuationInput,match='did not converge'):
        compute_options_and_final_value(raw,options,inputs().macro_inputs)
    options.number_of_options=10
    options.average_strike_price=8
    result=compute_options_and_final_value(raw,options,inputs().macro_inputs)
    state=result.option_pricing
    expected=black_scholes_call(state['adjusted_stock_price'],8,10,.03,.3)
    assert result.call_value_per_option==expected
    assert state['residual']<.01 and state['iterations']<=20


def test_api_invalid_inputs_refuse_and_preserve_existing_session(monkeypatch):
    from api import routes, session_store
    from api.main import app
    monkeypatch.setattr(routes,'_get_damodaran_store',lambda:SimpleNamespace(reference_snapshot={'id':'test'}))
    monkeypatch.setattr(routes,'_build_industry_lookup',lambda _:None)
    monkeypatch.setattr(session_store,'_sessions',{})
    with TestClient(app,raise_server_exceptions=False) as client:
        response=client.post('/api/valuation',json={'inputs':inputs().model_dump(mode='json')})
        assert response.status_code==200,response.text
        baseline=response.json();url='/api/valuation/'+baseline['id']
        for overrides in [
            {'valuation_assumptions.failure_probability':1.5},
            {'valuation_assumptions.failure_probability':-.1},
            {'methodology_choices.cost_of_capital_approach':'direct','methodology_choices.wacc_direct_input':-1},
            {'methodology_choices.wacc_level_shift_bps':-20000},
            {'methodology_choices.cost_of_capital_approach':'decile'},
            {'adjustment_inputs.has_r_and_d':True},
        ]:
            bad=client.patch(url,json={'overrides':overrides})
            assert bad.status_code==422,(overrides,bad.text)
            assert client.get(url).json()==baseline
        bars = client.post(url+'/sensitivity').json()['bars']
        shift = next(row for row in bars if row['driver'] == 'wacc_level_shift_bps')
        assert shift['vps_lo'] != shift['vps_hi']
        year = dict(normal_wacc=.08,debt_weight=.2,cost_of_debt_pretax=.05,tax_shield_rate=.25)
        financing = dict(policy_id='audit-explicit-financing',years=[year]*10,terminal=year)
        changed = client.patch(url,json={'overrides':{'valuation_assumptions.debt_tax_shield':financing}})
        assert changed.status_code == 200, changed.text
        bars = client.post(url+'/sensitivity').json()['bars']
        assert not any(row['driver'] == 'wacc_level_shift_bps' for row in bars)



@pytest.mark.parametrize('approach', ['direct', 'industry_average', 'decile', 'detailed'])
def test_discount_sensitivity_shift_is_consumed_by_every_selected_branch(approach):
    model = inputs(reporting_currency='USD', stock_price_currency='USD',
        methodology_choices=dict(cost_of_capital_approach=approach, wacc_direct_input=.08))
    model.industry_data.wacc = .08
    baseline = run_full_valuation(model)
    model.methodology_choices.wacc_level_shift_bps = 50
    shifted = run_full_valuation(model)
    assert shifted.cost_of_capital.wacc - baseline.cost_of_capital.wacc == pytest.approx(.005)
    assert shifted.dcf.discount_factors[0] != baseline.dcf.discount_factors[0]
    assert shifted.final.value_per_share != baseline.final.value_per_share


@pytest.mark.parametrize('base', ['B', 'V'])
def test_distress_and_excel_read_same_single_overlay_and_option_state(base):
    from io import BytesIO
    from openpyxl import load_workbook
    from api.export_workbook import generate_full_workbook
    model = inputs()
    model.valuation_assumptions.failure_probability = .2
    model.valuation_assumptions.failure_tie_to = base
    model.valuation_assumptions.distress_proceeds_pct = .3
    model.option_inputs = OptionInputs(has_options=True, number_of_options=2,
        average_strike_price=8, average_maturity=3, stock_price_std_dev=.3)
    report = run_full_valuation(model)
    expected_recovery = (1000 if base == 'B' else report.dcf.value_as_going_concern)*.3
    assert report.dcf.distress_value == pytest.approx(expected_recovery)
    assert report.dcf.value_of_operating_assets == pytest.approx(report.dcf.value_as_going_concern*.8+expected_recovery*.2)
    wb = load_workbook(BytesIO(generate_full_workbook(model, report)), data_only=True)
    def cell(sheet, label):
        return next(row[i+1].value for row in wb[sheet] for i,c in enumerate(row[:-1]) if c.value == label)
    assert cell('Failure Rate','Value of operating assets (going concern)') == pytest.approx(report.dcf.value_as_going_concern)
    assert cell('Failure Rate','Distress sale proceeds') == pytest.approx(report.dcf.distress_value)
    assert cell('Failure Rate','Expected operating asset value') == pytest.approx(report.dcf.value_of_operating_assets)
    state = report.final.option_pricing
    for label, key in [('Adjusted S (actual engine input)','adjusted_stock_price'),('d1','d1'),('N(d1)','n_d1'),('d2','d2'),('N(d2)','n_d2')]:
        assert cell('Option Value',label) == pytest.approx(state[key])
    assert cell('Valuation Picture','Terminal revenues') == pytest.approx(report.dcf.terminal_revenue)
    assert cell('Valuation Output','Equity bridge: debt') == pytest.approx(-report.cost_of_capital.mv_debt_total)


def test_zero_adjusted_profit_does_not_fall_back_to_reported_market_pe():
    from engine.data_dictionary import AdjustedFinancials, CashFlowMetrics, CostOfCapital
    from engine.module_5_multiples import compute_multiples
    model = inputs()
    result = compute_multiples(AdjustedFinancials(adjusted_ebit=0,adjusted_net_income=0),
        model.raw_financials[0], CashFlowMetrics(), run_full_valuation(model).cost_of_capital, model.valuation_assumptions, model.macro_inputs)
    assert result.pe_ratio_market is None


def test_explicit_market_debt_convertible_and_preferred_claims_do_not_duplicate():
    from tests.engine.test_convertible_claims import inputs as convertible_inputs
    model = convertible_inputs(detailed=True)
    model.raw_financials[0].mv_debt = 300  # 200 ordinary + 100 straight convertible.
    model.methodology_choices.use_bond_pricing_for_debt = True
    report = run_full_valuation(model)
    assert report.cost_of_capital.mv_straight_debt == pytest.approx(200)
    assert report.cost_of_capital.mv_debt_total == pytest.approx(300)
    assert report.dcf.equity_bridge_components['convertible_adjustment'] == pytest.approx(-30)
    assert report.dcf.value_of_equity == pytest.approx(report.dcf.value_of_operating_assets+50-300-30)
    bridge = convertible_inputs(bridge=True)
    bridge.methodology_choices.has_preferred = True
    bridge.methodology_choices.preferred_stock.shares = 10
    bridge.methodology_choices.preferred_stock.price_per_share = 2
    with pytest.raises(ValidationError, match='same signed preferred claim'):
        CompanyValuationInput.model_validate(bridge.model_dump())
    bridge.equity_bridge.components['preferred'] = -20
    report = run_full_valuation(CompanyValuationInput.model_validate(bridge.model_dump()))
    assert report.dcf.value_of_equity == pytest.approx(report.dcf.value_of_operating_assets+50-200-130-20)
    assert sum(report.dcf.equity_bridge_components.values())+report.dcf.value_of_operating_assets == pytest.approx(report.dcf.value_of_equity)
