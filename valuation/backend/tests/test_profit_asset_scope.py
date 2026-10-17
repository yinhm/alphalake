from copy import deepcopy
from datetime import date
from decimal import Decimal
import sqlite3

import pytest

from tools import review_profit_asset_scope as scope


END = date(2026, 6, 30)
CONTEXT = dict(ticker='SZSE:300866', instrument_id=7, model_scope=dict(status='nonfinancial_by_reference'))


def source_rows(end=END):
    values = dict(reported_ebit=11000000, profit_before_tax=10000000, interest_expense=1000000,
        finance_costs=-2000000, interest_income=3000000, investment_income=4000000,
        associates_and_joint_ventures_investment_income=2000000, fair_value_change_income=-500000,
        asset_disposal_income=0, monetary_funds=9000000, cash_and_cash_equivalents=8000000,
        equity_parent=20000000, noncontrolling_interests=1000000, total_equity=21000000)
    rows = []
    for field, periods in scope.projection(end).items():
        for period in periods:
            instant = field in scope.BALANCE_FIELDS
            rows.append(dict(ticker=CONTEXT['ticker'], instrument_id=7, field=field, period=period,
                value=str(values.get(field, 1000000)), unit='CNY', statement_scope='provider_default',
                period_type='instant' if instant else {6: 'H1', 12: 'FY'}[date.fromisoformat(period).month],
                source_record_id=period, artifact_sha256='a'*64, available_at=None,
                announcement_source='unknown', source_observed_at='2026-10-17T00:00:00Z'))
    return rows


def consumed():
    return dict(ebit=11, cash_and_marketable_securities=8, bv_equity=20, minority_interests=1, cross_holdings=5)


def test_reconstruction_matches_native_and_keeps_nested_returns_separate():
    rows = source_rows()
    before = deepcopy(rows)
    result = scope.review_company(rows, CONTEXT, consumed(), END)
    assert result['source_values']['reported_ebit']['value_cny']=='11000000'
    assert result['source_values']['finance_costs']['value_cny']=='-2000000'
    assert all(r['status']=='matches' for r in result['native_reconciliation'].values())
    assert result['arithmetic_checks']['pretax_profit_plus_interest_expense']['status']=='within_source_rounding'
    assert result['arithmetic_checks']['pretax_profit_plus_finance_costs']['status']=='outside_source_rounding'
    assert result['arithmetic_checks']['consolidated_equity']['residual_cny']=='0'
    assert result['source_values']['investment_income']['value_cny']=='4000000'
    assert result['source_values']['associates_and_joint_ventures_investment_income']['value_cny']=='2000000'
    assert result['observed_pairs']['cash_returns']['status']=='asset_and_return_observed'
    assert result['source_records'].keys()=={p for p,_ in scope.flow_terms(END)}
    assert rows==before and 'normalized_ebit' not in result
    net_interest_rows=deepcopy(rows)
    for row in net_interest_rows:
        if row['field']=='reported_ebit':row['value']='8000000'
    checks=scope.review_company(net_interest_rows,CONTEXT,consumed() | {'ebit':8},END)['arithmetic_checks']
    assert checks['pretax_profit_plus_net_interest']['status']=='within_source_rounding'
    assert all(r['status']=='within_source_rounding' for r in checks['pretax_profit_plus_net_interest']['source_period_checks'].values())
    assert checks['pretax_profit_plus_interest_expense']['status']=='outside_source_rounding'
    # Opposing source-period differences must remain visible even if TTM cancels them.
    for row in net_interest_rows:
        if row['field']=='reported_ebit' and row['period'] in ('2025-12-31','2025-06-30'):
            row['value']='8100000'
    check=scope.review_company(net_interest_rows,CONTEXT,consumed() | {'ebit':8},END)['arithmetic_checks']['pretax_profit_plus_net_interest']
    assert check['status']=='within_source_rounding'
    assert check['source_period_checks']['2025-12-31']['status']=='outside_source_rounding'
    annual = scope.review_company(source_rows(date(2025,12,31)), CONTEXT, consumed(), date(2025,12,31))
    assert len(annual['source_records'])==1 and annual['native_reconciliation']==result['native_reconciliation']


def test_missing_source_zero_partial_investments_and_negative_balance_are_distinct():
    rows = source_rows()
    for row in rows:
        if row['field']=='interest_income':row['value']='0'
    result=scope.review_company(rows, CONTEXT, consumed(), END)
    assert result['observed_pairs']['cash_returns']['status']=='asset_only_observed'
    rows=[r for r in rows if (r['field'],r['period'])!=('interest_income','2025-06-30') and r['field']!='debt_investments']
    result=scope.review_company(rows, CONTEXT, consumed() | {'cross_holdings':4}, END)
    assert result['source_values']['interest_income']['value_cny'] is None
    assert result['observed_pairs']['cash_returns']['status']=='incomplete_components'
    assert result['native_reconciliation']['cross_holdings']['status']=='matches'
    assert result['native_reconciliation']['cross_holdings']['missing_components']==['debt_investments']
    for row in rows:
        if row['field']=='other_debt_investments':row['value']='-1'
    assert scope.review_company(rows, CONTEXT, consumed(), END)['native_reconciliation']['cross_holdings']['status']=='negative_investment_component_requires_review'
    # A zero reported denominator remains a source fact; no division or inferred rejection.
    for row in rows:
        if row['field']=='reported_ebit':row['value']='0'
    assert scope.review_company(rows, CONTEXT, consumed() | {'ebit':0}, END)['native_reconciliation']['ebit']['status']=='matches'


def test_tampered_native_values_and_incompatible_source_are_caught():
    rows = source_rows()
    assert scope.review_company(rows, CONTEXT, consumed() | {'ebit':12}, END)['native_reconciliation']['ebit']['status']=='differs'
    for key,value in [('unit','USD'),('period_type','Q2'),('statement_scope','parent_only'),('instrument_id',9)]:
        changed=deepcopy(rows);changed[0][key]=value
        with pytest.raises(ValueError,match='Incompatible'):
            scope.review_company(changed, CONTEXT, consumed(), END)
    with pytest.raises(ValueError,match='duplicate'):
        scope.review_company(rows+[rows[0]], CONTEXT, consumed(), END)
    changed=deepcopy(rows);changed[0]['artifact_sha256']='b'*64
    with pytest.raises(ValueError,match='Conflicting source records'):
        scope.review_company(changed, CONTEXT, consumed(), END)


def test_identity_bound_handles_source_precision_without_certifying_formula():
    facts={('total','2025-12-31'):Decimal(1000000000),
           ('part','2025-12-31'):Decimal(999999968)}
    terms=[('total','2025-12-31',1),('part','2025-12-31',-1)]
    assert scope.identity_check(facts,terms)['status']=='within_source_rounding'
    facts['part','2025-12-31']=Decimal(999999000)
    assert scope.identity_check(facts,terms)['status']=='outside_source_rounding'
    del facts['part','2025-12-31']
    assert scope.identity_check(facts,terms)['status']=='missing_components'


def test_scope_denominator_is_preserved_and_no_valuation_is_run(monkeypatch):
    conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.executescript('CREATE TABLE companies(ticker TEXT); CREATE TABLE financials_ttm(ticker TEXT, ebit REAL);')
    tickers=['SZSE:300866','SHSE:600000','SHSE:601919']
    conn.executemany('INSERT INTO companies VALUES(?)',[(t,) for t in tickers])
    conn.execute('INSERT INTO financials_ttm VALUES(?,11)',(tickers[0],))
    contexts=[CONTEXT]+[dict(ticker=t,model_scope=dict(status=s)) for t,s in
        zip(tickers[1:],['outside_financial_scope','unresolved_industry_scope'])]
    monkeypatch.setattr(scope.native,'snapshot_metadata',lambda _:dict(report_period=END.isoformat(),information_as_of='2026-10-17T00:00:00Z'))
    monkeypatch.setattr(scope.native,'input_contexts',lambda _:iter(contexts))
    monkeypatch.setattr(scope.native,'load_input_context',lambda _,t,c:next(r for r in contexts if r['ticker']==t))
    result=scope.audit(conn,source_rows())
    assert result['published_companies']==3 and result['reviewed_nonfinancial_companies']==1
    assert len(result['scope_counts'])==3 and result['valuations_run']==0
    assert not result['automatic_adjustment'] and not result['operating_scope_verified']
    assert result['counts']['native_reconciliation:ebit']=={'matches':1}
    assert scope.projection(END)['cash_and_cash_equivalents']==['2026-06-30']
    assert len(scope.projection(END)['investment_income'])==3
    with pytest.raises(ValueError,match='Unpublished'):
        scope.audit(conn,[source_rows()[0] | {'ticker':'SZSE:999999'}])
    conn.close()
