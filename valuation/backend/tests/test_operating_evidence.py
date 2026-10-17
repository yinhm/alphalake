from copy import deepcopy
from datetime import date
import sqlite3

import pytest

from tools.review_operating_evidence import audit, FIELDS, projection


def sample():
    end = date(2026,6,30)
    values = {field:100 for field in FIELDS}
    values.update(revenue_cumulative=1000,reported_ebit=100,research_and_development_expense=120,
        capital_expenditure_cash=50,depreciation_depletion=80,inventory_decrease_cashflow=-40)
    facts = [dict(ticker='SZSE:300866',field=field,period=p,value=str(value),unit='CNY',
        period_type='FY' if p.endswith('12-31') else 'H1',statement_scope='provider_default',
        instrument_id=7,source_record_id=i,artifact_sha256='a'*64,available_at=None,
        announcement_source='unknown',source_observed_at='2026-10-17')
        for i,p in enumerate(projection(end)['revenue_cumulative']) for field,value in values.items()]
    conn = sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
    conn.executescript('CREATE TABLE companies(ticker,company_name,period_date_annual);'
        'CREATE TABLE financials_annual(ticker,fy_offset,revenues,ebit,capex,r_and_d_expense,d_a);'
        'CREATE TABLE financials_ttm(ticker,revenues,ebit,capex,r_and_d_expense,d_a);')
    conn.execute("INSERT INTO companies VALUES('SZSE:300866','Synthetic','2025-12-31')")
    conn.executemany('INSERT INTO financials_annual VALUES(?,?,?,?,?,?,?)',
        [('SZSE:300866',i,.001,.0001,.00005,.00012,None) for i in range(5)])
    conn.execute("INSERT INTO financials_ttm VALUES('SZSE:300866',.001,.0001,.00005,.00012,NULL)")
    return conn,facts,end


def test_operating_evidence_keeps_periods_source_components_and_cashflow_scope():
    conn,facts,end=sample()
    before=deepcopy(facts)
    with conn:
        report=audit(conn,facts,['SZSE:300866'],end)
    row=report['companies'][0]
    assert report['comparisons']['matched']==24
    assert not report['comparisons']['differences']
    assert [r['year'] for r in row['annual']]==[2021,2022,2023,2024,2025]
    assert row['current_ttm']['values_cny']['inventory_decrease_cashflow']=='-40'
    assert row['current_ttm']['research_expense_to_revenue']=='0.12'
    assert row['depreciation']['status']=='standard_components_present_native_total_not_supplied'
    assert row['depreciation']['native_total_million_cny'] is None
    assert row['forecast_approval']=='not_inferred_from_historical_facts'
    assert facts==before


def test_operating_evidence_exposes_source_missing_and_consumer_differences():
    conn,facts,end=sample()
    facts=[f for f in facts if not (f['field']=='right_of_use_depreciation' and f['period']=='2025-06-30')]
    conn.execute('UPDATE financials_ttm SET capex=123')
    with conn:
        report=audit(conn,facts,['SZSE:300866'],end)
    row=report['companies'][0]
    assert row['current_ttm']['values_cny']['right_of_use_depreciation'] is None
    assert row['depreciation']['standard_components_missing']==['right_of_use_depreciation']
    assert report['comparisons']['differences']==[dict(ticker='SZSE:300866',period='TTM',
        field='capital_expenditure_cash',source_million_cny=.00005,sqlite_million_cny=123,status='different')]


@pytest.mark.parametrize('change', ['unit','period','duplicate','record'])
def test_operating_evidence_rejects_incompatible_or_conflicting_facts(change):
    conn,facts,end=sample()
    if change=='unit':facts[0]['unit']='share'
    elif change=='period':facts[0]['period']='2020-12-31'
    elif change=='duplicate':facts.append(deepcopy(facts[0]))
    else:facts[0]['source_record_id']=999
    with conn,pytest.raises(ValueError,match='operating|source record'):
        audit(conn,facts,['SZSE:300866'],end)
