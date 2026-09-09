"""真实 Go 数据库导出 → HTTP → 完整引擎 → 原生桥接与可复现记录。"""
import copy
import csv
import json
import math
import os
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from api.main import app
from data_sources.alphalake import build_inputs, AlphaLakeRequest
from engine.orchestrator import run_full_valuation

REPO = Path(__file__).resolve().parents[3]
CHAIN = REPO/'internal/ingest/testdata/valuation-chain-2026'


@pytest.fixture(scope='module')
def exports(tmp_path_factory):
    output = tmp_path_factory.mktemp('alphalake_exports')
    env = os.environ | {'ALPHALAKE_VALUATION_EXPORT_DIR':str(output),'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealValuationStandardChain$','-count=1'],
                   cwd=REPO,env=env,check=True,capture_output=True,text=True)
    return {code:json.loads((output/(code+'.json')).read_text()) for code in ['300866','600519','999999']}


def request_for(exports,company,scenario='central'):
    code = '300866' if company == 'anker' else '600519'
    return dict(data=copy.deepcopy(exports[code]),
                policy=json.loads((REPO/f'valuation/examples/{company}-2026H1-{scenario}.json').read_text()))


def near(a,b):
    assert math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-7),(a,b)


@pytest.mark.parametrize('company', ['anker','moutai'])
@pytest.mark.parametrize('scenario', ['cautious','central','expansive'])
def test_real_http_forecast_bridge_and_replay(exports,tmp_path,monkeypatch,company,scenario):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req = request_for(exports,company,scenario)
    with TestClient(app) as client:
        response = client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code == 200,response.text
        result = response.json()
        assert client.post('/api/valuation/from-alphalake',json=req).json() == result
    stored, = tmp_path.glob('*.json')
    assert json.loads(stored.read_text()) == result
    report,p = result['report'],req['policy']['parameters']
    assert report['cashflow']['fcff'] is None and report['cashflow']['fcfe'] is None
    assert report['final']['value_per_share'] == report['equity_bridge']['per_share']
    # 从公式独立计算十年预测，不调用引擎的预测或桥接辅助函数。
    raw = result['inputs']['prepared_ttm']['financials']
    rev, margin0 = raw['revenues'], raw['ebit']/raw['revenues']
    pv = 0
    for year in range(1,11):
        growth = p['growth'] if year <= 5 else p['growth']+(p['terminal_growth']-p['growth'])*(year-5)/5
        revenue = rev*(1+growth)
        margin = margin0 if year == 1 else margin0+(p['margin']-margin0)*min(year/5,1)
        tax = p['tax_start'] if year <= 5 else p['tax_start']+(p['tax_terminal']-p['tax_start'])*(year-5)/5
        reinvestment = (revenue-rev)/p['sales_to_capital']
        fcff = revenue*margin*(1-tax)-reinvestment
        near(report['dcf']['revenue_projections'][year-1],revenue)
        near(report['dcf']['fcff_projections'][year-1],fcff)
        pv += fcff/(1+p['wacc'])**year
        rev = revenue
    terminal = rev*(1+p['terminal_growth'])*p['margin']*(1-p['tax_terminal'])*(1-p['terminal_growth']/p['terminal_roic'])/(p['wacc']-p['terminal_growth'])
    ev = pv+terminal/(1+p['wacc'])**10
    near(report['dcf']['value_of_operating_assets'],ev)
    code = req['data']['code']
    with (CHAIN/(code+'-equity-bridge.csv')).open() as f:
        original = {r['item']:float(r['value'])/1e6 for r in csv.DictReader(f) if r['scenario']==scenario}
    with (CHAIN/'inputs.csv').open() as f:
        i = {r['item']:float(r['value'])/(1 if r['item']=='operating_minority_fraction_proxy' else 1e6)
             for r in csv.DictReader(f) if r['code']==code}
    if company=='anker':
        offset = sum(v for k,v in original.items() if k!='operating_enterprise_value')
        equity = ev+offset
        shares = i['shares']*(1+p['extra_dilution_rate'])
        price = min(equity/shares,(equity+i['convertible_debt']*p['debt_multiple']+i['convertible_equity_book'])/
                    (shares+i['convertible_face']/(i['conversion_price']*1e6)))
    else:
        minority = -original['operating_minority_proxy']/original['liquor_enterprise_proxy']
        fixed = sum(v for k,v in original.items() if k not in ('liquor_enterprise_proxy','operating_minority_proxy'))
        price = (ev*(1-minority)+fixed)/i['shares']
    assert abs(report['final']['value_per_share']-price) < .000001
    # 没有隐藏额外 API 外桥接：直接共享编排器得到同一最终值。
    inputs,_ = build_inputs(AlphaLakeRequest.model_validate(req))
    near(run_full_valuation(inputs).final.value_per_share,price)


@pytest.mark.parametrize('mode', ['missing_note','missing_standard','unit','window_value','source_bits','future',
    'no_time','wrong_basis','wrong_scope','duplicate','wrong_policy','new_period','bad_wacc','wrong_currency','empty_security'])
def test_rejects_incompatible_or_incomplete_inputs(exports,tmp_path,monkeypatch,mode):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req = request_for(exports,'anker')
    data = req['data']
    if mode=='missing_note':
        data['supplements'] = [r for r in data['supplements'] if r['item']!='current_leases']
    elif mode=='missing_standard':
        data['windows'] = [r for r in data['windows'] if r['field']!='FN230']
    elif mode=='unit':
        next(r for r in data['windows'] if r['field']=='FN230')['unit']='million_CNY'
    elif mode=='window_value':
        next(r for r in data['windows'] if r['field']=='FN230')['value']='1'
    elif mode=='source_bits':
        next(r for r in data['facts'] if r['field']=='FN230' and r['period']=='2026-06-30')['bits']=0
    elif mode=='future':
        data['supplements'][0]['available_at']='2027-01-01T00:00:00+00:00'
    elif mode=='no_time':
        next(r for r in data['supplements'] if r['item']=='current_leases')['available_at']=None
    elif mode=='wrong_basis':
        r=next(r for r in data['windows'] if r['field']=='FN86')
        r.update(calculation_basis='instant',period_type='instant',required_inputs=1,available_inputs=1,
                 source_fact_ids=r['source_fact_ids'][:1],input_periods=r['input_periods'][:1],input_coefficients=[1])
        r['value']=next(f['value'] for f in data['facts'] if f['fact_id']==r['source_fact_ids'][0])
    elif mode=='wrong_scope':
        next(r for r in data['supplements'] if r['item']=='current_leases')['scope']='finance_subsidiary'
    elif mode=='duplicate':
        data['windows'].append(data['windows'][0])
    elif mode=='wrong_policy':
        req['policy']['policy_id']='moutai-liquor-proxy-v1'
    elif mode=='new_period':
        data['report_period']='2026-09-30';data['information_as_of']='2026-11-01T00:00:00Z'
    elif mode=='bad_wacc':
        req['policy']['parameters']['wacc']=.02
    elif mode=='wrong_currency':
        next(r for r in data['supplements'] if r['item']=='current_leases')['unit']='USD'
    else:
        data.update(facts=[],windows=[],supplements=[])
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
    assert response.status_code==422,response.text
    assert not list(tmp_path.glob('*.json'))
    if mode in ('missing_note','missing_standard','empty_security'):
        assert response.json()['detail']['status']=='blocked_missing_inputs'


def test_updated_assumption_recalculates_and_preserves_previous(exports,tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=request_for(exports,'anker')
    with TestClient(app) as client:
        first=client.post('/api/valuation/from-alphalake',json=req).json()
        req['policy']['parameters']['growth']=.13
        second=client.post('/api/valuation/from-alphalake',json=req).json()
    assert first['run_id']!=second['run_id']
    assert first['report']['final']['value_per_share']!=second['report']['final']['value_per_share']
    assert len(list(tmp_path.glob('*.json')))==2


def test_optional_historical_cashflow_missing_does_not_fake_fcff(exports,tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=request_for(exports,'anker')
    req['data']['windows']=[r for r in req['data']['windows'] if r['field']!='FN114']
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
    assert response.status_code==200,response.text
    result=response.json()
    assert result['report']['cashflow']['fcff'] is None
    assert result['inputs']['prepared_ttm']['financials']['capex'] is None
    near(result['report']['final']['value_per_share'],101.64374605804052)


def test_unknown_security_has_no_inferred_policy(exports,tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=request_for(exports,'anker')
    req['data']=exports['999999']
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
    assert response.status_code==422
    assert 'not approved' in response.text


def test_no_repeated_adjustments_or_employee_option_overlay(exports):
    inputs,_=build_inputs(AlphaLakeRequest.model_validate(request_for(exports,'anker')))
    for group,key in [('adjustment_inputs','has_operating_leases'),('adjustment_inputs','has_r_and_d'),('option_inputs','has_options')]:
        data=inputs.model_dump()
        data[group][key]=True
        with pytest.raises(ValueError):
            type(inputs).model_validate(data)


@pytest.fixture(scope='module')
def reference_export(tmp_path_factory):
    output = tmp_path_factory.mktemp('wacc_references')
    env = os.environ | {'ALPHALAKE_WACC_EXPORT_DIR':str(output),'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','./internal/ingest','-run','^TestWACCReferenceExport$','-count=1'],
                   cwd=REPO,env=env,check=True,capture_output=True,text=True)
    return json.loads((output/'credit-references.json').read_text())


def reference_request(exports, reference_export, company):
    req = request_for(exports,company)
    req['policy'] = json.loads((REPO/f'valuation/examples/wacc/{company}-forecast-policy.json').read_text())
    req['data']['information_as_of'] = reference_export['information_as_of']
    req['wacc_binding'] = dict(references=copy.deepcopy(reference_export),
        policy=json.loads((REPO/f'valuation/examples/wacc/{company}-target-policy.json').read_text()))
    return req


@pytest.mark.parametrize('company', ['anker','moutai'])
def test_reference_wacc_through_real_financial_chain(exports,reference_export,tmp_path,monkeypatch,company):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req = reference_request(exports,reference_export,company)
    with TestClient(app) as client:
        response = client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code == 200,response.text
        result = response.json()
        assert client.post('/api/valuation/from-alphalake',json=req).json() == result
    p = req['wacc_binding']['policy']
    # Independent Decimal reconstruction using exported rows, not engine helpers.
    country = {(r['subject_code'],r['metric_code']):D(r['value']) for r in reference_export['country_risk']}
    industry = {(r['industry'],r['metric_code']):D(r['value']) for r in reference_export['industry_stats']}
    rf = next(D(r['value']) for r in reference_export['yield_curve'] if r['tenor_months']==120) - country['CN','sovereign_default_spread']
    beta_u = sum(D(str(i['weight']))*industry[i['industry'],p['beta_metric']] for i in p['industries'])
    dw, tax, kd = [D(str(p[k])) for k in ('target_debt_weight','tax_shield_rate','debt_cost_pretax')]
    beta_l = beta_u*(1+(1-tax)*dw/(1-dw))
    crp = sum(D(str(c['weight']))*D(str(c['exposure_scale']))*country[c['country'],'country_risk_premium'] for c in p['countries'])
    ke = rf + beta_l*country['mature','mature_market_erp'] + crp
    wacc = (1-dw)*ke + dw*kd*(1-tax)
    coc = result['report']['cost_of_capital']
    near(coc['wacc'],float(wacc));near(coc['cost_of_equity'],float(ke));near(coc['beta_l'],float(beta_l))
    assert coc['approach_used']=='reference_snapshot' and coc['capital_structure_basis']=='target_weights'
    assert coc['mv_equity'] is None and coc['mv_debt_total'] is None
    assert coc['country_risk_contribution'] == float(crp)
    near(result['inputs']['valuation_assumptions']['cost_of_capital_stable_override'],float(wacc))
    assert result['report']['cashflow']['fcff'] is None
    assert result['audit']['wacc_reference']['selected_observations']
    # Only discounting changes: independently run the legacy direct branch with
    # this computed WACC and compare the native equity bridge result.
    direct = copy.deepcopy(req);direct.pop('wacc_binding');direct['policy']['parameters']['wacc']=float(wacc)
    legacy = run_full_valuation(build_inputs(AlphaLakeRequest.model_validate(direct))[0])
    near(result['report']['final']['value_per_share'],legacy.final.value_per_share)


@pytest.mark.parametrize('mutation', ['dual_wacc','missing_reference','future_available','future_recorded',
    'stale_curve','duplicate_industry','weights','wrong_scope','wrong_code','wrong_cutoff','wrong_unit','beta_method','sample','missing_key'])
def test_reference_wacc_rejects_invalid_policy_and_snapshot(exports,reference_export,tmp_path,monkeypatch,mutation):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req = reference_request(exports,reference_export,'anker')
    binding = req['wacc_binding'];p=binding['policy'];s=binding['references']
    if mutation=='dual_wacc':req['policy']['parameters']['wacc']=.08
    elif mutation=='missing_reference':s['yield_curve'].pop()
    elif mutation=='future_available':s['releases'][0]['available_at']='2099-01-01T00:00:00Z'
    elif mutation=='future_recorded':s['recorded_cutoff']='2000-01-01T00:00:00Z'
    elif mutation=='stale_curve':p['max_beta_age_days']=1
    elif mutation=='duplicate_industry':p['industries']*=2
    elif mutation=='weights':p['industries'][0]['weight']=.5
    elif mutation=='wrong_scope':p['scope']='liquor_proxy'
    elif mutation=='wrong_code':p['code']='600519'
    elif mutation=='wrong_cutoff':s['information_as_of']='2099-01-01T00:00:00Z'
    elif mutation=='wrong_unit':
        for r in s['yield_curve']:r['raw_unit']='fraction'
    elif mutation=='beta_method':
        for r in s['industry_stats']:r['method_code']='guessed'
    elif mutation=='sample':p['minimum_sample_count']=1000000
    elif mutation=='missing_key':s['releases'][0].pop('content_key')
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==422,response.text
    assert not list(tmp_path.glob('*.json'))


def test_synthetic_debt_through_standard_financial_chain(exports,reference_export,tmp_path,monkeypatch):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req = reference_request(exports,reference_export,'anker')
    p = req['wacc_binding']['policy']
    p.pop('debt_cost_pretax')
    p['synthetic_debt'] = dict(firm_type='large_nonfinancial',coverage_basis='policy_operating_ebit_over_tdx_gross_interest',
        applicability_reason='explicit US large firm proxy for this reviewed consolidated sample',
        sovereign_spread_policy='add_cn_default_spread',sovereign_spread_reason='explicit country borrowing overlay',max_credit_age_days=370)
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==200,response.text
        r=response.json(); a=r['audit']['wacc_reference']['synthetic_debt']
        ebit=r['inputs']['prepared_ttm']['financials']['ebit']
        interest=next(D(w['value'])/D(1000000) for w in req['data']['windows'] if w['field']=='FN305')
        near(float(a['coverage_ratio']),float(D(str(ebit))/interest))
        assert a['rating']=='Aaa/AAA'
        # In this explicit policy RF deducts, then Kd adds the same country spread.
        government=next(D(w['value']) for w in reference_export['yield_curve'] if w['tenor_months']==120)
        near(a['debt_cost_pretax'],float(government+D('.004')))
        near(r['report']['cost_of_capital']['cost_of_debt_pretax'],a['debt_cost_pretax'])
        assert client.post('/api/valuation/from-alphalake',json=req).json()==r
        for mode in ['dual','missing_table','stale','wrong_scope']:
            bad=copy.deepcopy(req)
            if mode=='dual':bad['wacc_binding']['policy']['debt_cost_pretax']=.03
            elif mode=='missing_table':bad['wacc_binding']['references']['credit_spreads']=[]
            elif mode=='stale':bad['wacc_binding']['policy']['synthetic_debt']['max_credit_age_days']=1
            else:
                bad=reference_request(exports,reference_export,'moutai')
                bad['wacc_binding']['policy'].pop('debt_cost_pretax')
                bad['wacc_binding']['policy']['synthetic_debt']=p['synthetic_debt']
            assert client.post('/api/valuation/from-alphalake',json=bad).status_code==422
    assert len(list(tmp_path.glob('*.json')))==1


def test_synthetic_credit_source_boundaries(exports,reference_export):
    from data_sources.alphalake_wacc import WACCBinding, resolve_wacc
    from datetime import datetime, date
    req=reference_request(exports,reference_export,'anker')
    p=req['wacc_binding']['policy'];p.pop('debt_cost_pretax')
    p['synthetic_debt']=dict(firm_type='large_nonfinancial',coverage_basis='policy_operating_ebit_over_tdx_gross_interest',
        applicability_reason='boundary test',sovereign_spread_policy='none',sovereign_spread_reason='test',max_credit_age_days=370)
    binding=WACCBinding.model_validate(req['wacc_binding'])
    for ebit,interest in [(.2,1),(.1999995,1),(100001,1),(1,0),(1,-1)]:
        with pytest.raises(ValueError):resolve_wacc(binding,'300866',date(2026,6,30),binding.references.information_as_of,ebit=ebit,interest=interest)
    _,audit=resolve_wacc(binding,'300866',date(2026,6,30),binding.references.information_as_of,ebit=.199999,interest=1)
    assert audit['synthetic_debt']['rating']=='D2/D'


def test_v1_reference_packet_still_supported(exports,reference_export):
    req=reference_request(exports,reference_export,'anker')
    s=req['wacc_binding']['references'];s['contract_version']='alphalake-wacc-references-v1';s.pop('credit_spreads')
    s['releases']=[r for r in s['releases'] if r['dataset']!='synthetic-credit-large-2026-v1']
    inputs,_=build_inputs(AlphaLakeRequest.model_validate(req))
    assert inputs.methodology_choices.cost_of_capital_approach=='reference_snapshot'


@pytest.fixture(scope='module')
def market_export(tmp_path_factory):
    import sys
    output=tmp_path_factory.mktemp('market_capital')
    env=os.environ|{'ALPHALAKE_MARKET_EXPORT_DIR':str(output),'ALPHALAKE_TEST_PYTHON':sys.executable,'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','./internal/ingest','-run','^TestMarketCapitalArchiveReplay$','-count=1'],cwd=REPO,env=env,check=True,capture_output=True,text=True)
    return {code:json.loads((output/(code+'.json')).read_text()) for code in ['300866','600519']}


def market_request(exports,reference_export,market_export,company):
    req=reference_request(exports,reference_export,company)
    capital=copy.deepcopy(market_export[req['data']['code']])
    req['data']['information_as_of']=capital['information_as_of']
    req['wacc_binding']['references']['information_as_of']=capital['information_as_of']
    req['wacc_binding']['policy']=json.loads((REPO/f'valuation/examples/wacc/{company}-market-policy.json').read_text())
    req['wacc_binding']['market_capital']=capital
    return req


@pytest.mark.parametrize('company',['anker','moutai'])
def test_market_capital_wacc_http(exports,reference_export,market_export,company,tmp_path,monkeypatch):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=market_request(exports,reference_export,market_export,company)
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==200,response.text
        result=response.json()
        assert client.post('/api/valuation/from-alphalake',json=req).json()==result
    s=req['wacc_binding']['market_capital'];p=req['wacc_binding']['policy']
    shares={r['trading_currency']:D(r['value']) for r in s['share_counts'] if r['share_basis']=='outstanding'}
    common=shares['CNY']*D(s['a_quote']['quote']['close'])
    if company=='anker': common+=shares['HKD']*D(s['h_quote']['close'])*D(s['fx']['raw_value'])/100
    common/=D(1000000)
    bridge=result['inputs']['equity_bridge'];dk='debt_claim_proxy' if company=='anker' else 'lease_debt'
    offset=sum(D(str(v)) for k,v in bridge['components'].items() if k!=dk)
    equity=(common-offset)/D(str(bridge['operating_ownership']))
    debt=-D(str(bridge['components'][dk]))/D(str(bridge['operating_ownership']))
    w=debt/(equity+debt);tax=D(str(p['tax_shield_rate']))
    country={(r['subject_code'],r['metric_code']):D(r['value']) for r in reference_export['country_risk']}
    gov=next(D(r['value']) for r in reference_export['yield_curve'] if r['tenor_months']==120)
    beta=next(D(r['value']) for r in reference_export['industry_stats'] if r['industry']==p['industries'][0]['industry'] and r['metric_code']==p['beta_metric'])
    spread=next(D(r['value']) for r in reference_export['credit_spreads'] if r['rating']=='Aaa/AAA')
    rf=gov-country['CN','sovereign_default_spread'];kd=gov+spread
    levered=beta*(1+(1-tax)*debt/equity)
    ke=rf+levered*country['mature','mature_market_erp']+country['CN','country_risk_premium']
    expected=(1-w)*ke+w*kd*(1-tax)
    c=result['report']['cost_of_capital']
    near(c['mv_equity'],float(equity));near(c['mv_debt_total'],float(debt));near(c['weight_debt'],float(w));near(c['wacc'],float(expected))
    assert c['capital_structure_basis']=='market_equity_estimated_debt'
    assert result['report']['cashflow']['fcff'] is None
    assert c['weight_debt']>0


@pytest.mark.parametrize('case', ['share_missing','treasury','wrong_class','duplicate_listing','future_share','stale_share','fx_direction','fx_date','fx_unit','hk_date','a_adjusted','future_release','target_override','missing_packet','debt_missing','bad_credit'])
def test_market_capital_rejects_bad_evidence(exports,reference_export,market_export,case,tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=market_request(exports,reference_export,market_export,'anker')
    b=req['wacc_binding'];s=b['market_capital'];p=b['policy']
    if case=='share_missing':s['share_counts'].pop()
    elif case=='treasury':next(r for r in s['share_counts'] if r['share_basis']=='treasury')['value']='1.0000000000'
    elif case=='wrong_class':s['h_quote']['instrument_id']=s['a_quote']['quote']['instrument_id']
    elif case=='duplicate_listing':s['share_counts']+=copy.deepcopy(s['share_counts'][:3])
    elif case=='future_share':s['share_counts'][0]['effective_date']='2099-01-01'
    elif case=='stale_share':p['market']['max_share_age_days']=0
    elif case=='fx_direction':s['fx']['base_currency']='CNY'
    elif case=='fx_date':s['fx']['observed_at']='2026-09-07T00:00:00+08:00'
    elif case=='fx_unit':s['fx']['value']=s['fx']['raw_value']
    elif case=='hk_date':s['h_quote']['trade_date']='2026-09-07'
    elif case=='a_adjusted':s['a_quote']['adjustment']='forward'
    elif case=='future_release':s['releases'][0]['available_at']='2099-01-01T00:00:00Z'
    elif case=='target_override':p['target_debt_weight']=.05
    elif case=='missing_packet':b.pop('market_capital')
    elif case=='debt_missing':p['market'].pop('debt_value_reason')
    elif case=='bad_credit':p['credit_band_debt']={'rating':'AAA'}
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==422,response.text
    assert not list(tmp_path.glob('*.json'))
