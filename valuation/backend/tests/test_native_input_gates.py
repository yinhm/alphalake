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
    store = SimpleNamespace(reference_snapshot={}, industry_mapper=SimpleNamespace(lookup=lambda _: SimpleNamespace(country='United States',industry_group='Synthetic')), lookup_industry=lambda *a, **kw: industry,
        list_industries=lambda *a: ['Synthetic'], list_countries=lambda: ['United States'], lookup_country=lambda *a: MacroInputs(risk_free_rate=.04,equity_risk_premium=.05,tax_rate_marginal=.25))
    monkeypatch.setattr('api.routes._get_damodaran_store', lambda: store)
    monkeypatch.setattr('api.routes._get_industry_mapper', lambda: SimpleNamespace(lookup=lambda _: SimpleNamespace(country='United States',industry_group='Synthetic')))
    return record


def test_one_annual_year_can_value_without_optional_history(monkeypatch):
    record = sample(monkeypatch)
    record['financials_annual'] += [dict(fy_offset=1, revenues=None, ebit=None),
                                     dict(fy_offset=3, revenues=800, ebit=100)]
    gate = db.native_compatibility(None, 'TEST')
    assert gate['status'] == 'ready' and len(gate['optional_history_missing']) == 2
    inputs, _ = _db_record_to_company_input(record, .04, None)
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
    inputs, _ = _db_record_to_company_input(record,.04,None)
    assert len(inputs.quarterly_financials)==6
    assert inputs.quarterly_financials[2].revenues is None
    record["financials_quarterly"].append(dict(fq_offset=7,revenues=None,ebit=None))
    assert len(_db_record_to_company_input(record,.04,None)[0].quarterly_financials)==8
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
    inputs, _ = _db_record_to_company_input(record,.04,None)
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
    inputs, _ = _db_record_to_company_input(record,.04,None)
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
    record['data_source'] = dict(contract='alphalake-sqlite-v8', report_period='2025-12-31', information_as_of='2026-09-25')
    record['financials_annual'].append(dict(fy_offset=1,revenues=None,ebit=None))
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE export_cells(ticker,series,period_offset,field,period,status,evidence_json)')
    conn.executemany('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
        [('TEST','annual',0,f,'2025-12-31','available','[]') for f in record['financials_annual'][0] if f != 'fy_offset'])
    conn.execute('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
        ('TEST','company',0,'mv_equity_listing','2025-12-31','available','{}'))
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
            assert not any(f['path']=='industry_data.industry_name' for f in response.json()['unresolved_fields'])
            assert not any('占位' in w for w in response.json()['warnings'])
            resolved = client.patch('/api/valuation/'+response.json()['id'], json={'overrides':{'industry_data.industry_name':'Synthetic'}})
            assert resolved.status_code == 200
            assert not any(f['path']=='industry_data.industry_name' for f in resolved.json()['unresolved_fields'])
            # 即使填了数值，映射未获批准仍拒绝；不是取消全局阻断就任意放行。
            conn.execute("UPDATE export_cells SET status='requires_separate_valuation_definition' WHERE field='bv_debt'")
            assert client.post('/api/valuation/from-database',json=dict(ticker='TEST',risk_free_rate=.04)).status_code==422
            conn.execute("UPDATE export_cells SET status='available' WHERE field='bv_debt'")
            conn.execute("UPDATE export_cells SET status='partial_target_scope' WHERE field='cash_and_marketable_securities'")
            gap = client.get('/api/database/compatibility/TEST').json()['required_missing']
            assert len(gap)==1 and gap[0]['available_value']==50
            assert '部分组成' in client.get('/api/database/compatibility/TEST').json()['blockers'][0]
            assert client.post('/api/valuation/from-database',json=dict(ticker='TEST',risk_free_rate=.04)).status_code==422
            conn.execute("UPDATE export_cells SET status='estimated_partial_scope', evidence_json=? WHERE field='cash_and_marketable_securities'",
                         ('[{"available_component_million_cny":50,"not_complete_target":true}]',))
            conn.execute("UPDATE export_cells SET status='a_share_total_share_proxy' WHERE field='mv_equity_listing'")
            response = client.post('/api/valuation/from-database',json=dict(ticker='TEST',risk_free_rate=.04))
            assert response.status_code == 200 and '账面代理' in response.text
            assert response.json()['valuation_proxy']['market_proxy']['status'] == 'a_share_total_share_proxy'
            assert 'WACC' in response.text
            assert response.json()['valuation_proxy']['exported_asset_proxies'][0]['value'] == 50
            updated = client.patch('/api/valuation/'+response.json()['id'], json={'overrides': {}})
            assert updated.status_code == 200 and '账面代理' in updated.text
            assert updated.json()['valuation_proxy'] == response.json()['valuation_proxy']
            record['financials_annual'][0]['cash_and_marketable_securities'] = None
            assert client.post('/api/valuation/from-database',json=dict(ticker='TEST',risk_free_rate=.04)).status_code == 422
    finally:
        conn.close()


def test_original_manual_reference_selection_updates_numbers_and_keeps_failed_session(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api.session_store import create_session

    record = sample(monkeypatch)
    inputs, _ = _db_record_to_company_input(record, .04, None)
    original = run_full_valuation(inputs)
    power = IndustryData(industry_name='Power', beta_u=.7, cost_of_debt_pretax=.045)
    store = SimpleNamespace(reference_snapshot={},
        lookup_industry=lambda name, **kw: power if name == 'Power' else None,
        list_industries=lambda *a: ['Power'], list_countries=lambda: ['China'],
        lookup_country=lambda name: MacroInputs(risk_free_rate=.02, equity_risk_premium=.06, tax_rate_marginal=.25) if name == 'China' else None)
    session = create_session(inputs, original, unresolved_fields=[{'path':'industry_data.industry_name'}, {'path':'country'}], reference_store=store)
    monkeypatch.setattr('api.routes._get_damodaran_store', lambda: store)
    monkeypatch.setattr('api.routes._get_damodaran_store', lambda: (_ for _ in ()).throw(AssertionError('old session must not load newly published references')))
    with TestClient(app) as client:
        result = client.patch('/api/valuation/'+session.id, json={'overrides':{
            'industry_data.industry_name':'Power', 'country':'China', 'macro_inputs.risk_free_rate':.032}})
        assert result.status_code == 200, result.text
        assert session.inputs.industry_data.beta_u == .7
        assert session.inputs.macro_inputs.equity_risk_premium == .06
        assert session.inputs.macro_inputs.risk_free_rate == .032
        assert session.inputs.macro_inputs.tax_rate_effective == inputs.macro_inputs.tax_rate_effective
        assert not any(f['path'] in ('industry_data.industry_name','country') for f in result.json()['unresolved_fields'])
        assert session.report.final.value_per_share != original.final.value_per_share
        tax = client.patch('/api/valuation/'+session.id, json={'overrides':{'effective_tax_rate_ciq':.18}})
        assert tax.status_code == 200
        assert session.inputs.macro_inputs.tax_rate_effective == .18
        assert not any(f['path']=='effective_tax_rate_ciq' for f in tax.json()['unresolved_fields'])
        saved = session.inputs.model_dump()
        for path in ('industry_data.industry_name','country'):
            rejected = client.patch('/api/valuation/'+session.id, json={'overrides':{path:'Unknown'}})
            assert rejected.status_code == 422
            assert session.inputs.model_dump() == saved


def test_native_gate_requires_linked_review_for_source_zero(monkeypatch):
    import json
    import sqlite3
    import pytest
    record = sample(monkeypatch)
    record['data_source'] = dict(contract='alphalake-sqlite-v8')
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE export_cells(ticker,series,period_offset,field,period,status,evidence_json)')
    conn.execute('CREATE TABLE reviewed_source_zeros(import_sha256,ticker,evidence_json)')
    conn.executemany('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
        [('TEST','annual',0,f,'2025-12-31','available','[]') for f in record['financials_annual'][0] if f != 'fy_offset'])
    conn.execute('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
        ('TEST','company',0,'mv_equity_listing','2025-12-31','available','{}'))
    proof = dict(import_sha256='a'*64,code='TEST',field='bonds_payable',period='2025-12-31',value='0')
    ref = dict(kind='reviewed_source_zero',import_sha256='a'*64,field='bonds_payable',period='2025-12-31')
    conn.execute('INSERT INTO reviewed_source_zeros VALUES(?,?,?)',('a'*64,'TEST',json.dumps(proof)))
    conn.execute("UPDATE export_cells SET status='available_with_reviewed_source_zero',evidence_json=? WHERE field='bv_debt'",(json.dumps([ref]),))
    report = db.native_compatibility(conn,'TEST')
    assert report['status'] == 'ready'
    assert len(report['reviewed_source_zeros']) == 1
    conn.execute('DELETE FROM reviewed_source_zeros')
    with pytest.raises(ValueError,match='Missing reviewed source zero'):
        db.native_compatibility(conn,'TEST')
    conn.close()


def test_incomplete_income_year_keeps_research_cohort_without_inventing_profit(monkeypatch):
    from copy import deepcopy
    from engine.data_dictionary import CompanyValuationInput
    record = sample(monkeypatch)
    record['financials_annual'][0]['r_and_d_expense'] = 60
    record['financials_annual'] += [dict(fy_offset=i, revenues=None, ebit=None, r_and_d_expense=60-10*i) for i in range(1,6)]
    inputs, _ = _db_record_to_company_input(record, .04, None)
    assert len(inputs.raw_financials) == 1
    assert inputs.historical_research_expenses == {2024:50,2023:40,2022:30,2021:20,2020:10}
    report = run_full_valuation(inputs)
    # 五年摊销=(50+40+30+20+10)/5=30；历史EBIT=150+60-30。
    assert report.cashflow.historical_margin_by_year == [pytest.approx(.18)]
    missing = deepcopy(inputs)
    missing.historical_research_expenses.pop(2020)
    partial = run_full_valuation(missing)
    assert partial.cashflow.historical_margin_by_year == [None]
    assert partial.dcf == report.dcf and partial.final == report.final
    for year,value in ((2025,60),(2019,-1),(2019,float('nan'))):
        bad = inputs.model_dump()
        bad['historical_research_expenses'][year] = value
        with pytest.raises(ValueError,match='supplementary research'):
            CompanyValuationInput.model_validate(bad)
    # 更晚年度不回填旧年的研发队列。
    future = deepcopy(inputs)
    future.historical_research_expenses[2026] = 999
    assert run_full_valuation(future).cashflow.historical_margin_by_year == report.cashflow.historical_margin_by_year


def test_input_contract_exposes_selection_without_filling_missing_history(monkeypatch):
    record = sample(monkeypatch)
    record['financials_annual'].append(dict(fy_offset=1, revenues=None, ebit=None))
    gate = db.native_compatibility(None, 'TEST')
    contract = gate['input_contract']
    assert not contract['historical_fcff_required']
    assert not contract['silent_previous_period_fallback']
    optional = [r for r in contract['inputs'] if r['requirement']=='optional']
    assert len(optional)==2 and all(not r['usable'] and r['missing_action']=='retain_gap' for r in optional)
    record['financials_annual'][0]['bv_debt'] = None
    gate = db.native_compatibility(None, 'TEST')
    debt = next(r for r in gate['input_contract']['inputs'] if r['field']=='bv_debt')
    assert debt['requirement']=='required' and not debt['usable'] and debt['value'] is None
    assert gate['status']=='blocked_required_inputs'
