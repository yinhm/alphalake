"""账面代理不改事实；收益配套、缺项、期限与负向污染拒绝。"""
from datetime import date, datetime
import copy
import json
import sqlite3

import pytest
from data_sources import us_cn_hk_db as db
from data_sources.tdx_book_proxy import apply
from tools.export_alphalake_sqlite import export_snapshot


@pytest.fixture
def sample():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    facts = []
    amounts = dict(operating_profit_cumulative=100, interest_expense=5, interest_income=2,
        investment_income=8, fair_value_change_income=3, revenue_cumulative=1000, reported_ebit=110,
        monetary_funds=10, trading_financial_assets=3, long_term_equity_investments=5,
        other_noncurrent_financial_assets=6)
    for end in ['2025-03-31','2025-06-30','2025-12-31','2026-03-31','2026-06-30']:
        month = int(end[5:7])
        for field,value in amounts.items():
            instant = field in ('monetary_funds','trading_financial_assets','long_term_equity_investments','other_noncurrent_financial_assets')
            facts.append(dict(source='tdx',code='300866',instrument_id=7,period=end,field=field,canonical_field=field,
                value=str(value*1000000*(1 if instant else month//3)), unit='CNY',period_type='instant' if instant else {3:'Q1',6:'H1',12:'FY'}[month],
                statement_scope='provider_default',fact_id=end+'/'+field,available_at='2026-08-31T00:00:00Z',artifact_sha256='raw'))
    asof=datetime.fromisoformat('2026-09-26T00:00:00+00:00')
    def fetch(code,end):
        return dict(contract_version='alphalake-valuation-v2',code=code,report_period=end.isoformat(),information_as_of=asof.isoformat(),facts=[r for r in facts if r['period']<=end.isoformat()],source_conflicts=[])
    export_snapshot(conn,[dict(symbols=['sz300866'],symbol_count=1,identifier_count=1,exchange_mic='XSHE',instrument_id=7,name='样本')],fetch,date(2026,6,30),asof)
    conn.execute("INSERT INTO metadata VALUES('report_period','2026-06-30')")
    yield conn
    conn.close()


def test_proxy_preserves_facts_and_pairs_earnings(sample):
    raw=db.fetch_company(sample,'SZSE:300866')
    adjusted=db.fetch_valuation_company(sample,'SZSE:300866')
    assert raw['financials_annual'][0]['cash_and_marketable_securities'] is None
    assert raw['financials_annual'][0]['ebit']==440
    row=adjusted['financials_annual'][0]
    assert (row['cash_and_marketable_securities'],row['cross_holdings'],row['ebit'])==(13,11,368)
    assert adjusted['financials_quarterly'][0]['ebit']==92
    assert row['ebitda'] is None
    assert db.fetch_company(sample,'SZSE:300866')==raw
    item=next(e for e in adjusted['valuation_proxy']['cells'] if e['series']=='annual' and e['offset']==0 and e['field']=='cross_holdings')
    assert item['status']=='estimated' and len(item['omitted_components'])==3 and len(item['source_components'])==2
    gate=db.native_compatibility(sample,'SZSE:300866')
    assert not any(r['field'] in ('cash_and_marketable_securities','cross_holdings','ebit') for r in gate['required_missing'])
    raw['company']['ticker']='SHSE:600519'
    assert 'valuation_proxy' not in apply(sample,raw)
    raw['company']['ticker']='SZSE:300866';raw['data_source']['report_period']='2026-09-30'
    assert apply(sample,raw)['valuation_proxy']['status']=='not_applicable'


def test_proxy_missing_and_tampered_values_do_not_pass(sample):
    sample.execute("DELETE FROM standard_facts WHERE field='monetary_funds'")
    gate=db.native_compatibility(sample,'SZSE:300866')
    assert any(r['field']=='cash_and_marketable_securities' and r['status']=='missing_proxy_component' for r in gate['required_missing'])
    sample.execute("DELETE FROM standard_facts WHERE field='interest_income' AND period='2026-03-31'")
    gate=db.native_compatibility(sample,'SZSE:300866')
    assert sum(r['field']=='ebit' for r in gate['required_missing'])==2
    sample.execute("UPDATE standard_facts SET evidence_json=json_set(evidence_json,'$.unit','USD') WHERE field='trading_financial_assets'")
    with pytest.raises(ValueError,match='inconsistent standard fact evidence'):
        db.fetch_valuation_company(sample,'SZSE:300866')


def test_proxy_conflict_and_all_investments_missing(sample):
    sample.execute("DELETE FROM standard_facts WHERE field IN ('long_term_equity_investments','other_noncurrent_financial_assets')")
    gate=db.native_compatibility(sample,'SZSE:300866')
    assert any(r['field']=='cross_holdings' and r['status']=='missing_proxy_component' for r in gate['required_missing'])
    sample.execute("UPDATE export_cells SET status='source_record_conflict' WHERE period='2026-06-30'")
    gate=db.native_compatibility(sample,'SZSE:300866')
    assert any(r['field']=='ebit' and r['status']=='source_record_conflict' for r in gate['required_missing'])
