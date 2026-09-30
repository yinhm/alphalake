"""标准数据直通：专项分量缺失不能覆盖报表值，真实缺项仍拒绝。"""
from datetime import date, datetime
import sqlite3

import pytest
from data_sources import us_cn_hk_db as db
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


@pytest.mark.parametrize("ticker", ["SZSE:300866", "SZSE:002032", "SHSE:600519"])
def test_standard_ebit_is_not_replaced_by_sample_policy(sample, ticker):
    sample.execute("UPDATE companies SET ticker=?", (ticker,))
    sample.execute("UPDATE financials_annual SET ticker=?", (ticker,))
    sample.execute("UPDATE financials_quarterly SET ticker=?", (ticker,))
    sample.execute("UPDATE financials_ttm SET ticker=?", (ticker,))
    sample.execute("UPDATE standard_facts SET ticker=?", (ticker,))
    sample.execute("UPDATE export_cells SET ticker=?", (ticker,))
    raw = db.fetch_company(sample, ticker)
    assert raw['financials_annual'][0]['ebit'] == 440
    assert raw['financials_quarterly'][0]['ebit'] == 110
    assert raw['financials_annual'][0]['cash_and_marketable_securities'] is None
    # 有效来源EBIT不能因不参与标准导出的专项组成缺失而被清空。
    sample.execute("DELETE FROM standard_facts WHERE field='interest_income'")
    unchanged = db.fetch_company(sample, ticker)
    assert unchanged['financials_annual'] == raw['financials_annual']
    assert unchanged['financials_quarterly'] == raw['financials_quarterly']
    gate = db.native_compatibility(sample, ticker)
    assert not any(r['field'] == 'ebit' for r in gate['required_missing'])
    assert any(r['field'] == 'cash_and_marketable_securities' for r in gate['required_missing'])
    assert any('重复计价' in warning for warning in gate['warnings'])


def test_standard_missing_and_conflicting_ebit_still_block(sample):
    sample.execute("UPDATE financials_ttm SET ebit=NULL")
    gate = db.native_compatibility(sample, 'SZSE:300866')
    assert any(r['field'] == 'ebit' for r in gate['required_missing'])
    sample.execute("UPDATE financials_ttm SET ebit=440")
    sample.execute("UPDATE export_cells SET status='source_record_conflict' WHERE period='2026-06-30'")
    gate = db.native_compatibility(sample, 'SZSE:300866')
    assert any(r['field'] == 'ebit' and r['status'] == 'source_record_conflict' for r in gate['required_missing'])
