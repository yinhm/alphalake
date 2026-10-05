"""合成输入锁定数据门槛；不冒充TDX金额或会计口径验收。"""
from types import SimpleNamespace

import pytest

from api.database import _db_record_to_company_input, _initialize_native_assumptions, _initialize_native_cny_financing
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


def test_source_initial_conditions_replay_without_changing_facts_or_user_inputs(monkeypatch):
    record = sample(monkeypatch)
    record['financials_annual'].append(dict(fy_offset=1, revenues=800, ebit=100))
    inputs, _ = _db_record_to_company_input(record, .04, None)
    inputs.industry_data.sales_to_capital = 4
    before = run_full_valuation(inputs)
    facts = [f.model_dump() for f in inputs.raw_financials]
    sources = _initialize_native_assumptions(inputs, before)
    a = inputs.valuation_assumptions
    assert (a.revenue_growth_next_year, a.revenue_growth_years_2_5, a.high_growth_years) == (.25, .25, 1)
    assert (a.operating_margin_next_year, a.target_operating_margin) == (.15, .15)
    assert (a.sales_to_capital_high, a.sales_to_capital_stable) == (4, 4)
    after = run_full_valuation(inputs)
    assert after.dcf.revenue_projections[0] == 1250
    assert after.dcf.revenue_projections[1] / 1250 - 1 == pytest.approx(.25-(.25-.04)/9)
    assert after.dcf.tax_rate_projections == before.dcf.tax_rate_projections  # 同边际/有效税率
    assert after.adjusted == before.adjusted and after.cost_of_capital == before.cost_of_capital
    assert [f.model_dump() for f in inputs.raw_financials] == facts
    assert 'US/Synthetic' in sources['valuation_assumptions.sales_to_capital_high']
    assert _initialize_native_assumptions(inputs, after) == {}
    a.revenue_growth_next_year = a.revenue_growth_years_2_5 = 0
    a.operating_margin_next_year = .2
    a.target_operating_margin = None
    a.sales_to_capital_high = 8
    a.sales_to_capital_stable = None
    a.high_growth_years = 3
    assert _initialize_native_assumptions(inputs, after) == {}
    assert (a.revenue_growth_next_year, a.operating_margin_next_year, a.sales_to_capital_high, a.high_growth_years) == (0, .2, 8, 3)
    assert a.target_operating_margin is None and a.sales_to_capital_stable is None


def test_missing_or_invalid_reference_initialization_keeps_explicit_limitations(monkeypatch):
    inputs, _ = _db_record_to_company_input(sample(monkeypatch), .04, None)
    inputs.industry_data.sales_to_capital = -2
    report = run_full_valuation(inputs)
    _initialize_native_assumptions(inputs, report)
    assert inputs.valuation_assumptions.revenue_growth_next_year is None
    assert inputs.valuation_assumptions.sales_to_capital_high is None
    assert inputs.valuation_assumptions.high_growth_years == 5
    assert any('同比' in w for w in report.warnings) and any('资本倍率' in w for w in report.warnings)


def test_cny_source_financing_rebases_debt_and_preserves_explicit_rf(monkeypatch):
    import gzip
    import json
    from pathlib import Path
    from decimal import Decimal
    path=Path(__file__).resolve().parents[3]/'valuation/research/current-references-20260930/method-closure-20260912/300866-request.json.gz'
    refs=json.loads(gzip.decompress(path.read_bytes()))['wacc_binding']['references']
    store=SimpleNamespace(wacc_references=refs,native_debt_bases={'US':dict(value='0.039500000000',release_id=1,source_locator='Industry Averages!D9')})
    record=sample(monkeypatch)
    inputs,_=_db_record_to_company_input(record,.0425,None)
    run_full_valuation(inputs)
    before=inputs.model_dump()
    assert _initialize_native_cny_financing(inputs,store,None)==({},[])
    inputs.ticker='SZSE:300866'
    inputs.reporting_currency=inputs.stock_price_currency='CNY'
    gov=Decimal(next(r['value'] for r in refs['yield_curve'] if r['tenor_months']==120))
    sovereign=Decimal(next(r['value'] for r in refs['country_risk'] if (r['subject_code'],r['metric_code'])==('CN','sovereign_default_spread')))
    for explicit in (None,0,.03):
        sources,warnings=_initialize_native_cny_financing(inputs,store,explicit)
        rf=float(gov-sovereign) if explicit is None else explicit
        assert inputs.macro_inputs.risk_free_rate==rf
        assert inputs.methodology_choices.kd_direct_input==pytest.approx(rf+float(sovereign)+.05-.0395)
        report=run_full_valuation(inputs)
        assert report.cost_of_capital.cost_of_debt_pretax==inputs.methodology_choices.kd_direct_input
        assert report.cost_of_capital.risk_free_rate==rf
        assert [f.model_dump() for f in inputs.raw_financials]==before['raw_financials']
        assert 'D9' in sources['methodology_choices.kd_direct_input'] and warnings
    inputs.industry_data.cost_of_debt_pretax=.02
    with pytest.raises(ValueError,match='invalid CNY financing'):
        _initialize_native_cny_financing(inputs,store,None)
    # 实际PATCH只联动来源初值；独立覆盖优先，清空融资条件恢复人民币初值。
    from api.routes import _get_damodaran_store
    from api.main import app
    from api.session_store import create_session
    from fastapi.testclient import TestClient
    bound_store=_get_damodaran_store()
    bound_store.wacc_references=refs
    bound_store.native_debt_bases=store.native_debt_bases
    inputs.industry_data.cost_of_debt_pretax=.05
    sources,_=_initialize_native_cny_financing(inputs,bound_store,None)
    report=run_full_valuation(inputs)
    stable='valuation_assumptions.cost_of_capital_stable_override'
    inputs.valuation_assumptions.cost_of_capital_stable_override=report.cost_of_capital.wacc
    session=create_session(inputs,run_full_valuation(inputs),reference_store=bound_store)
    session.source_tracker.record('methodology_choices','Native CNY financing initialization v1;')
    for field,basis in sources.items():session.source_tracker.record(field,basis)
    session.source_tracker.record(stable,'人民币融资初值v1；持有当前融资')
    with TestClient(app) as client:
        def patch(overrides):
            response=client.patch('/api/valuation/'+session.id,json={'overrides':overrides})
            assert response.status_code==200,response.text
            return response.json()
        changed=patch({'macro_inputs.risk_free_rate':.03})
        assert changed['cost_of_capital']['cost_of_debt_pretax']==pytest.approx(.03+float(sovereign)+.0105)
        assert changed['inputs']['valuation_assumptions']['cost_of_capital_stable_override']==changed['cost_of_capital']['wacc']
        patch({'methodology_choices.kd_direct_input':0,stable:.09})
        changed=patch({'macro_inputs.risk_free_rate':.02})
        assert changed['cost_of_capital']['cost_of_debt_pretax']==0
        assert changed['inputs']['valuation_assumptions']['cost_of_capital_stable_override']==.09
        changed=patch({'methodology_choices.kd_direct_input':None,stable:None})
        assert changed['cost_of_capital']['cost_of_debt_pretax']==pytest.approx(.02+float(sovereign)+.0105)
        assert changed['inputs']['valuation_assumptions']['cost_of_capital_stable_override']==changed['cost_of_capital']['wacc']


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


def test_prepared_ttm_growth_rebuilds_comparison_from_cumulative_facts_without_quarters(monkeypatch):
    import json
    record = sample(monkeypatch, quarter='2026-06-30')
    record['data_source'] = dict(contract='alphalake-sqlite-v11', information_as_of='2026-09-25T00:00:00Z', source_database_sha256='fixture')
    record['financials_ttm'] = record['financials_annual'][0] | dict(revenues=1200, ebit=170)
    record['ttm_evidence'] = {'revenues':[dict(period=p, value=v) for p,v in
        [('2025-12-31','1000000000'),('2026-06-30','600000000'),('2025-06-30','400000000')]]}
    record['standard_financials'] = [dict(field='revenue_cumulative', period=p, value=v, unit='USD',
        period_type=basis, statement_scope='provider_default') for p,v,basis in
        [('2025-12-31','1000000000','FY'),('2026-06-30','600000000','H1'),
         ('2025-06-30','400000000','H1'),('2024-12-31','900000000','FY'),('2024-06-30','350000000','H1')]]
    inputs, _ = _db_record_to_company_input(record,.04,None)
    assert not inputs.quarterly_financials
    evidence = json.loads(inputs.prepared_ttm.provenance['prior_ttm_revenue'])
    assert evidence['revenues'] == '950' and evidence['period_end'] == '2025-06-30'
    before = run_full_valuation(inputs)
    assert inputs.company_metrics.revenue_growth == pytest.approx(1200/950-1)
    for change in [dict(unit='CNY'), dict(period_type='instant'), dict(statement_scope='parent_statement'),
                   dict(value='NaN'), dict(period='2024-03-31')]:
        original = record['standard_financials'][-1]
        record['standard_financials'][-1] = original | change
        invalid, _ = _db_record_to_company_input(record,.04,None)
        after = run_full_valuation(invalid)
        assert invalid.company_metrics.revenue_growth is None
        assert after.dcf == before.dcf  # 可选同比诊断不阻断或改变估值。
        record['standard_financials'][-1] = original


def test_original_api_admits_reviewed_current_inputs_with_history_gaps(monkeypatch):
    import sqlite3
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from api.main import app

    record = sample(monkeypatch)
    record['data_source'] = dict(contract='alphalake-sqlite-v11', report_period='2025-12-31', information_as_of='2026-09-25')
    record['financials_ttm'] = record['financials_annual'][0]
    record['financials_annual'].append(dict(fy_offset=1,revenues=None,ebit=None))
    conn = sqlite3.connect(':memory:', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE export_cells(ticker,series,period_offset,field,period,status,evidence_json)')
    conn.execute('CREATE TABLE standard_records(record_id,evidence_json)')
    conn.execute('CREATE TABLE standard_facts(ticker,period,field,value,unit,period_type,statement_scope,fact_id,record_id)')
    conn.executemany('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
        [('TEST',series,0,f,'2025-12-31','available','[]') for series in ('annual','ttm') for f in record['financials_annual'][0] if f != 'fy_offset'])
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


def test_forecast_patch_resolution_and_inactive_sensitivity(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api.session_store import create_session
    record = sample(monkeypatch)
    inputs, _ = _db_record_to_company_input(record, .04, None)
    from api.routes import _get_damodaran_store
    session = create_session(inputs, run_full_valuation(inputs), reference_store=_get_damodaran_store())
    url = '/api/valuation/'+session.id
    with TestClient(app) as client:
        edited = client.patch(url, json={'overrides': {'valuation_assumptions.revenue_growth_next_year':.12}})
        assert edited.status_code == 200, edited.text
        assert edited.json()['dcf']['forecast_parameters']['revenue_growth_years_2_5']['value'] == .12
        saved = client.get(url).json()
        for overrides in ({'valuation_assumptions.margin_convergence_year':0},
                          {'valuation_assumptions.sales_to_capital_high':0},
                          {'valuation_assumptions.revenue_growth_next_year':-1.1}):
            rejected = client.patch(url, json={'overrides':overrides})
            assert rejected.status_code == 422, rejected.text
            assert client.get(url).json() == saved
        annual = client.patch(url, json={'overrides': {
            'valuation_assumptions.annual_forecast': [dict(growth=0, margin=.2, tax=.25)]*10,
            'valuation_assumptions.annual_sales_to_capital':[3]*10,
        }})
        assert annual.status_code == 200, annual.text
        assert not annual.json()['dcf']['forecast_parameters']['target_operating_margin']['editable']
        sensitivity = client.post(url+'/sensitivity').json()
        assert {b['driver'] for b in sensitivity['bars']} == {
            'growth_perpetuity_rate', 'wacc_level_shift_bps', 'failure_probability'}
        assert all(b['range_source'] == 'application_example' for b in sensitivity['bars'])


def test_native_gate_requires_linked_review_for_source_zero(monkeypatch):
    import json
    import sqlite3
    import pytest
    record = sample(monkeypatch)
    record['data_source'] = dict(contract='alphalake-sqlite-v11')
    record['financials_ttm'] = record['financials_annual'][0]
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE export_cells(ticker,series,period_offset,field,period,status,evidence_json)')
    conn.execute('CREATE TABLE standard_records(record_id,evidence_json)')
    conn.execute('CREATE TABLE standard_facts(ticker,period,field,value,unit,period_type,statement_scope,fact_id,record_id)')
    conn.execute('CREATE TABLE reviewed_source_zeros(import_sha256,ticker,evidence_json)')
    conn.executemany('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
        [('TEST',series,0,f,'2025-12-31','available','[]') for series in ('annual','ttm') for f in record['financials_annual'][0] if f != 'fy_offset'])
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


def test_sensitivity_growth_label_follows_native_initial_period():
    from engine.sensitivity_ranges import build_ranges
    from engine.data_dictionary import CompanyValuationInput
    inputs = CompanyValuationInput(ticker='TEST', macro_inputs=MacroInputs(risk_free_rate=.04, equity_risk_premium=.05, tax_rate_marginal=.25), industry_data=IndustryData(industry_name='Synthetic', beta_u=1))
    inputs.valuation_assumptions.high_growth_years = 1
    initial = next(r for r in build_ranges(inputs) if r.driver == 'revenue_growth_years_2_5')
    assert initial.label == 'Revenue growth (transition anchor)'
    inputs.valuation_assumptions.high_growth_years = 3
    later = next(r for r in build_ranges(inputs) if r.driver == initial.driver)
    assert later.label == 'Revenue growth (Y2-3)'
    assert (later.patch_path, later.range_lo, later.range_hi) == (initial.patch_path, initial.range_lo, initial.range_hi)
