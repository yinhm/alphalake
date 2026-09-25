"""合成输入锁定数据门槛；不冒充TDX金额或会计口径验收。"""
from types import SimpleNamespace

import pytest

from api.database import _db_record_to_company_input
from data_sources import us_cn_hk_db as db
from engine.data_dictionary import IndustryData, MacroInputs
from engine.ltm_calculator import compute_ltm_financials
from engine.orchestrator import run_full_valuation


def sample(monkeypatch, quarter='2025-12-31'):
    record = dict(company=dict(ticker='TEST', company_name='Synthetic', region='United States',
        filing_currency='USD', listing_currency='USD', period_date_annual='2025-12-31',
        period_date_quarterly=quarter, data_as_of='2026-09-25', mv_equity_listing=1000),
        financials_annual=[dict(fy_offset=0, revenues=1000, ebit=150,
            cash_and_marketable_securities=50, bv_debt=100, cross_holdings=0,
            minority_interests=0, shares_outstanding=10)], financials_quarterly=[])
    monkeypatch.setattr(db, 'fetch_company', lambda *_: record)
    industry = IndustryData(industry_name='Synthetic', beta_u=1, cost_of_debt_pretax=.05)
    store = SimpleNamespace(lookup_industry=lambda *a, **kw: industry,
        list_industries=lambda *a: ['Synthetic'], lookup_country=lambda *a: MacroInputs(risk_free_rate=.04,equity_risk_premium=.05,tax_rate_marginal=.25))
    monkeypatch.setattr('api.routes._get_damodaran_store', lambda: store)
    monkeypatch.setattr('api.routes._get_industry_mapper', lambda: SimpleNamespace(lookup=lambda _: None))
    return record


def test_one_annual_year_can_value_without_optional_history(monkeypatch):
    record = sample(monkeypatch)
    record['financials_annual'] += [dict(fy_offset=1, revenues=None, ebit=None),
                                     dict(fy_offset=3, revenues=800, ebit=100)]
    gate = db.native_compatibility(None, 'TEST')
    assert gate['status'] == 'ready' and len(gate['optional_history_missing']) == 2
    inputs = _db_record_to_company_input(record, .04, None)
    assert [r.fiscal_year for r in inputs.raw_financials] == [2025, 2022]
    inputs.raw_financials = inputs.raw_financials[:1]
    report = run_full_valuation(inputs)
    assert report.final.value_per_share > 0
    assert report.cashflow.fcff is None
    assert 'Historical FCFF unavailable: incomplete reinvestment inputs' in report.warnings
    # 零是显式输入，缺失不能等同于零；更早年度不能晋升为FY0。
    record['financials_annual'][0]['bv_debt'] = None
    assert any(r['field']=='bv_debt' for r in db.native_compatibility(None,'TEST')['required_missing'])
    record['financials_annual'].pop(0)
    with pytest.raises(ValueError, match='FY0'):
        _db_record_to_company_input(record, .04, None)


def test_ttm_reads_only_aligned_quarters_and_keeps_holes(monkeypatch):
    record = sample(monkeypatch, '2026-06-30')
    balance = record['financials_annual'][0]
    record['financials_quarterly'] = [dict(balance, fq_offset=i, revenues=v, ebit=v/10)
        for i,v in [(0,300),(1,280),(4,200),(5,180)]]
    assert db.native_compatibility(None,'TEST')['status']=='ready'
    inputs = _db_record_to_company_input(record,.04,None)
    assert len(inputs.quarterly_financials)==6
    assert inputs.quarterly_financials[2].revenues is None
    record["financials_quarterly"].append(dict(fq_offset=7,revenues=None,ebit=None))
    assert len(_db_record_to_company_input(record,.04,None).quarterly_financials)==8
    assert db.native_compatibility(None,"TEST")["status"]=="ready"
    record["financials_quarterly"].pop()
    ltm = compute_ltm_financials(inputs.raw_financials[0],inputs.quarterly_financials,2)
    assert ltm.revenues == 1200 and ltm.ebit == 170
    report = run_full_valuation(inputs)
    assert report.ltm_financials.mv_equity == 1000 and report.final.value_per_share > 0
    inputs.fx_rate = 2
    assert run_full_valuation(inputs).ltm_financials.mv_equity == 2000
    record['financials_quarterly'].pop()  # 实际消费的上年Q1缺失，必须拒绝。
    gate = db.native_compatibility(None,'TEST')
    assert [(r['offset'],r['field']) for r in gate['required_missing']]==[(5,'revenues'),(5,'ebit')]
    inputs = _db_record_to_company_input(record,.04,None)
    with pytest.raises(ValueError,match='incomplete LTM field'):
        compute_ltm_financials(inputs.raw_financials[0],inputs.quarterly_financials,2)


def test_adjustments_require_only_their_selected_inputs(monkeypatch):
    record = sample(monkeypatch)
    fy0 = record['financials_annual'][0]
    fy0['r_and_d_expense'] = 20
    fy0['operating_lease_expense'] = 5
    gate = db.native_compatibility(None,'TEST')
    assert len(gate['conditional_missing']) == 11  # 5个研发年度、6个租赁承诺桶。
    record['financials_annual'] += [dict(fy_offset=i,r_and_d_expense=20-i) for i in range(1,6)]
    record['company'].update({f'lease_commitment_yr{i}':0 for i in range(1,6)})
    record['company']['lease_commitment_beyond'] = 0
    assert db.native_compatibility(None,'TEST')['status']=='ready'
    inputs = _db_record_to_company_input(record,.04,None)
    assert inputs.adjustment_inputs.r_and_d_expense_past == [19,18,17,16,15]
    assert len(inputs.raw_financials)==1  # 研发队列不要求旧年收入/EBIT齐全。
    record['financials_annual'][2]['r_and_d_expense']=None
    assert len(db.native_compatibility(None,'TEST')['conditional_missing'])==1
    with pytest.raises(ValueError,match='consecutive year offset 2'):
        _db_record_to_company_input(record,.04,None)
    record['company']['options_outstanding']=1
    assert any(r['field']=='options_avg_strike' for r in db.native_compatibility(None,'TEST')['conditional_missing'])
    record['company']['period_date_quarterly']='2025-09-30'
    assert any('期间身份无效' in b for b in db.native_compatibility(None,'TEST')['blockers'])


def test_original_api_admits_reviewed_current_inputs_with_history_gaps(monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from api.main import app

    record = sample(monkeypatch)
    record['data_source'] = dict(report_period='2025-12-31', information_as_of='2026-09-25')
    record['financials_annual'].append(dict(fy_offset=1,revenues=None,ebit=None))
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE export_cells(ticker,series,period_offset,field,period,status)')
    conn.executemany('INSERT INTO export_cells VALUES(?,?,?,?,?,?)',
        [('TEST','annual',0,f,'2025-12-31','available') for f in record['financials_annual'][0] if f != 'fy_offset'])
    @contextmanager
    def connection():
        yield conn
    monkeypatch.setattr(db,'get_connection',connection)
    try:
        with TestClient(app) as client:
            assert client.get('/api/database/compatibility/TEST').json()['status']=='ready'
            response = client.post('/api/valuation/from-database',json=dict(ticker='TEST',risk_free_rate=.04))
            assert response.status_code==200, response.text
            assert '可选历史不完整' in response.text and '报告EBIT' in response.text
            # 即使填了数值，映射未获批准仍拒绝；不是取消全局阻断就任意放行。
            conn.execute("UPDATE export_cells SET status='requires_separate_valuation_definition' WHERE field='bv_debt'")
            assert client.post('/api/valuation/from-database',json=dict(ticker='TEST',risk_free_rate=.04)).status_code==422
    finally:
        conn.close()
