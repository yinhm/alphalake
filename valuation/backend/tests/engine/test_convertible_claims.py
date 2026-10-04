"""纯债、含权残差、WACC与现有股数：独立现金流复核及拒绝护栏。"""
from copy import deepcopy
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from engine.data_dictionary import CompanyValuationInput, ConvertibleDebt, EquityBridgeInputs
from engine.equity_bridge import InvalidConvertibleInput, value_convertible
from engine.orchestrator import run_full_valuation


def terms(**changes):
    return dict(book_value=80, face_value=100, market_value=130, discount_rate=.1,
        cashflows=[dict(time_years=1,amount=10),dict(time_years=2,amount=10),dict(time_years=3,amount=110)],
        conversion_shares=5, **changes)


def inputs(*, detailed=False, bridge=False, options=False):
    body=dict(ticker='SHSE:600519',reporting_currency='CNY',stock_price_currency='CNY',
        period_date_10k='2025-12-31',raw_financials=[dict(fiscal_year=2025,revenues=1000,ebit=200,
            shares_outstanding=10,bv_debt=280,cash_and_marketable_securities=50,minority_interests=0,
            cross_holdings=0,mv_equity=1000,interest_expense=23)],
        macro_inputs=dict(risk_free_rate=.04,equity_risk_premium=.05,tax_rate_marginal=.25),
        industry_data=dict(industry_name='test',beta_u=1,cost_of_debt_pretax=.06),
        methodology_choices=dict(cost_of_capital_approach='detailed' if detailed else 'direct',
            wacc_direct_input=.08,has_convertible=True,convertible_debt=terms()),
        valuation_assumptions=dict(projection_years=1,high_growth_years=1,sales_to_capital_high=2,
            stable_growth_rate=0,cost_of_capital_stable_override=.08,roic_stable_override=.08,
            annual_forecast=[dict(growth=0,margin=.2,tax=.25)]))
    if bridge:
        body['prepared_ttm']=dict(financials=body['raw_financials'][0],period_start='2025-01-01',
            period_end='2025-12-31',information_as_of='2026-01-01T00:00:00+00:00',currency='CNY',
            money_unit='million_reporting_currency',shares_unit='million_shares',provenance={'test':'synthetic'})
        body['equity_bridge']=dict(policy_id='test-book-bridge',operating_ownership=1,shares=10,
            components=dict(cash=50,debt=-280))
    if options:
        body['option_inputs']=dict(has_options=True,number_of_options=1,average_strike_price=50,
            average_maturity=2,stock_price_std_dev=.3,dividend_yield=0)
    return CompanyValuationInput.model_validate(body)


def test_split_matches_decimal_cashflow_pv_and_keeps_time_value():
    result=value_convertible(ConvertibleDebt(**terms()))
    rate=Decimal('1.1')
    debt=sum(Decimal(amount)/rate**year for year,amount in [(1,10),(2,10),(3,110)])
    assert result['straight_debt_value']==pytest.approx(float(debt))
    assert result['conversion_rights_value']==pytest.approx(30)
    assert result['straight_debt_value']+result['conversion_rights_value']==130
    assert value_convertible(ConvertibleDebt(**(terms()|{'book_value':0})))['book_value']==0
    regular=terms();regular.update(cashflows=[],annual_coupon=10,maturity_years=3)
    assert value_convertible(ConvertibleDebt(**regular))['straight_debt_value']==pytest.approx(100)
    # 非完整剩余年度和阶梯票息仅由实际现金流处理。
    irregular=terms();irregular['cashflows']=[dict(time_years=.2,amount=2),dict(time_years=1.2,amount=104)]
    exact=sum(row['amount']/1.1**row['time_years'] for row in irregular['cashflows'])
    assert value_convertible(ConvertibleDebt(**irregular))['straight_debt_value']==pytest.approx(exact)


@pytest.mark.parametrize('detailed,bridge',[(False,False),(True,False),(False,True),(True,True)])
def test_rights_deduct_once_without_full_conversion_denominator(detailed,bridge):
    model=inputs(detailed=detailed,bridge=bridge)
    report=run_full_valuation(model)
    claim=report.convertible_claim
    expected_equity=report.dcf.value_of_operating_assets+50-200-100-30
    assert report.dcf.value_of_equity==pytest.approx(expected_equity)
    assert report.final.value_per_share==pytest.approx(expected_equity/10)
    assert claim['shares']==10 and claim['potential_conversion_shares']==5
    changed=model.model_copy(deep=True);changed.methodology_choices.convertible_debt.conversion_shares=50
    assert run_full_valuation(changed).final.value_per_share==report.final.value_per_share
    if detailed:
        risk=report.cost_of_capital
        assert risk.mv_straight_debt==200 and risk.mv_debt_total==pytest.approx(300)
        assert risk.mv_equity==pytest.approx(1030)
        assert risk.weight_debt==pytest.approx(300/1330)
        assert risk.d_e_ratio==pytest.approx(300/1030)
        assert risk.cost_of_debt_pretax==pytest.approx((200*.06+100*.1)/300)
        ke=.04+(1+.75*300/1030)*.05
        assert risk.wacc==pytest.approx(ke*1030/1330+(200*.06+100*.1)*.75/1330)


def test_remaining_debt_prices_only_remaining_book_interest():
    model=inputs(detailed=True)
    model.methodology_choices.use_bond_pricing_for_debt=True
    model.methodology_choices.debt_maturity_years=3
    with pytest.raises(InvalidConvertibleInput,match='accounting interest'):
        run_full_valuation(model)
    model.methodology_choices.convertible_debt.interest_expense=3
    result=run_full_valuation(model)
    remaining=sum(20/1.06**t for t in (1,2,3))+200/1.06**3
    assert result.cost_of_capital.mv_straight_debt==pytest.approx(remaining)
    assert result.dcf.value_of_equity==pytest.approx(result.dcf.value_of_operating_assets+50-remaining-130)


def test_employee_options_follow_convertible_claims_and_do_not_replace_them():
    report=run_full_valuation(inputs(options=True))
    assert report.final.value_of_all_options > 0
    assert report.final.value_per_share==pytest.approx(
        (report.dcf.value_of_equity-report.final.value_of_all_options)/10)


@pytest.mark.parametrize('field,value',[('market_value',90),('market_value',0),('face_value',0),
    ('book_value',None),
    ('discount_rate',None),('cashflows',[]),('cashflows',[
        dict(time_years=1,amount=1e308),dict(time_years=2,amount=1e308)])])
def test_missing_or_inconsistent_terms_refuse(field,value):
    body=terms();body[field]=value
    with pytest.raises(InvalidConvertibleInput):
        value_convertible(ConvertibleDebt(**body))
    with pytest.raises(ValidationError):
        ConvertibleDebt(**(terms()|{'market_value':float('nan')}))


def test_old_conversion_bridge_is_not_kept_as_runtime_compatibility():
    with pytest.raises(ValidationError,match='Extra inputs'):
        EquityBridgeInputs(policy_id='old',components=dict(debt=-80),operating_ownership=1,shares=10,
            conversion_release=80,conversion_shares=5)


def test_api_rejections_keep_session_and_replay_protects_claim_evidence(monkeypatch):
    from api.main import app
    from api import routes, session_store
    from data_sources.damodaran_store import DamodaranStore
    from tools.review_native_policy import review_report
    monkeypatch.setattr(routes,'_get_damodaran_store',lambda:DamodaranStore())
    monkeypatch.setattr(routes,'_build_industry_lookup',lambda _:None)
    monkeypatch.setattr(session_store,'_sessions',{})
    with TestClient(app) as client:
        response=client.post('/api/valuation',json={'inputs':inputs().model_dump(mode='json')})
        assert response.status_code==200,response.text
        saved=response.json();sid=saved['id']
        assert saved['convertible_claim']['conversion_rights_value']==pytest.approx(30)
        for field,value in [('market_value',90),('discount_rate',None),('book_value',300)]:
            bad=client.patch('/api/valuation/'+sid,json={'overrides':{
                'methodology_choices.convertible_debt.'+field:value}})
            assert bad.status_code==422,bad.text
            assert client.get('/api/valuation/'+sid).json()==saved
        assert review_report(saved)
        tampered=deepcopy(saved);tampered['convertible_claim']['conversion_rights_value']+=.01
        with pytest.raises(ValueError,match='convertible_claim'):
            review_report(tampered)
