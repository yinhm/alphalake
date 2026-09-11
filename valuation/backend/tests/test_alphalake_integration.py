"""真实 Go 数据库导出 → HTTP → 完整引擎 → 原生桥接与可复现记录。"""
import copy
from dataclasses import asdict
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from fastapi.encoders import jsonable_encoder
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
    assert result['terminal_sensitivity'] is None
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
    return {code:json.loads((output/(code+'.json')).read_text()) for code in ['300866','600519','300866-funding']}


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


def test_contractual_debt_wacc(exports,reference_export,market_export,tmp_path,monkeypatch):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=market_request(exports,reference_export,market_export,'anker')
    req['wacc_binding']['policy']=json.loads((REPO/'valuation/examples/wacc/anker-contractual-debt-policy.json').read_text())
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==200,response.text
        result=response.json()
        assert client.post('/api/valuation/from-alphalake',json=req).json()==result
        audit=result['audit']['wacc_reference']['market_capital']['debt_valuation']
        rate=D(str(result['report']['cost_of_capital']['cost_of_debt_pretax']))
        notes={r['item']:D(r['value'])/1000000 for r in req['data']['supplements']}
        upper=sum(notes[f'debt_cf_{k}_{c}']/(1+rate)**t for k in ('short','long','lease','bond') for c,t in [('0_1',0),('1_2',1),('2_5',2),('5_plus',5)])
        lower=sum(notes[f'debt_cf_{k}_{c}']/(1+rate)**t for k in ('short','long','lease','bond') for c,t in [('0_1',1),('1_2',2),('2_5',5)])
        near(float(audit['upper_bound_million_cny']),float(upper))
        near(float(audit['lower_bound_million_cny']),float(lower))
        near(result['report']['cost_of_capital']['mv_debt_total'],float(upper))
        assert lower<upper
        assert result['report']['cashflow']['fcff'] is None
        for change in ('missing','amount','book','unit','period'):
            bad=copy.deepcopy(req)
            row=next(r for r in bad['data']['supplements'] if r['item']=='debt_cf_long_5_plus')
            if change=='missing': bad['data']['supplements'].remove(row)
            elif change=='amount': row['value']='1'
            elif change=='book': next(r for r in bad['data']['supplements'] if r['item']=='debt_cf_long_book')['value']='1'
            elif change=='unit': row['unit']='HKD'
            else: row['period']='2025-12-31'
            assert client.post('/api/valuation/from-alphalake',json=bad).status_code==422,change
    from data_sources.alphalake_market import contractual_debt_value
    value,zero=contractual_debt_value(lambda item:float(notes[item]),audit['standard_book_million_cny'],0)
    near(value,float(zero['lower_bound_million_cny']))


def test_disclosed_funding_scenario(exports,reference_export,market_export,tmp_path,monkeypatch):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=market_request(exports,reference_export,market_export,'anker')
    req['wacc_binding']['market_capital']=copy.deepcopy(market_export['300866-funding'])
    req['wacc_binding']['policy']=json.loads((REPO/'valuation/examples/wacc/anker-funding-scenario-policy.json').read_text())
    with TestClient(app) as client:
        r=client.post('/api/valuation/from-alphalake',json=req)
        assert r.status_code==200,r.text
        result=r.json()
        audit=result['audit']['wacc_reference']['market_capital']
        adjustment=D('4860860000')*D('0.86482')/1000000
        near(float(audit['funding_cash_scenario']['cash_adjustment_million_cny']),float(adjustment))
        assert audit['current_fair_value_complete'] is False
        original=copy.deepcopy(req)
        for retention in (0,.5):
            req['wacc_binding']['policy']['market']['funding_cash_retention']=retention
            response=client.post('/api/valuation/from-alphalake',json=req)
            assert response.status_code==200,response.text
            current=response.json()['audit']['wacc_reference']['market_capital']
            near(float(current['operating_equity_million_cny'])-float(audit['operating_equity_million_cny']),float(adjustment)*(1-retention))
        for case in ('missing','duplicate','currency','status','date_status','company','date','shares','release','no_policy','bad_retention'):
            bad=copy.deepcopy(original);s=bad['wacc_binding']['market_capital'];p=bad['wacc_binding']['policy']['market'];row=s['funding_events'][0]
            if case=='missing': s['funding_events'].pop()
            elif case=='duplicate': s['funding_events'][1]=copy.deepcopy(row)
            elif case=='currency': row['currency']='CNY'
            elif case=='status': row['amount_status']='settled_cash'
            elif case=='date_status': s['funding_events'][1]['date_status']='reported_listing_date_not_cash_settlement'
            elif case=='company': row['company_id']+=1
            elif case=='date': row['listing_date']='2026-09-09'
            elif case=='shares': row['issued_shares']='1'
            elif case=='release': row['release_id']=s['funding_events'][1]['release_id']
            elif case=='no_policy': p.pop('funding_cash_retention');p.pop('funding_cash_reason')
            else: p['funding_cash_retention']=1.1
            assert client.post('/api/valuation/from-alphalake',json=bad).status_code==422,case
        assert client.post('/api/valuation/from-alphalake',json=original).json()==result


def test_revised_anker_annual_forecast(exports,reference_export,market_export,tmp_path,monkeypatch):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=request_for(exports,'anker')
    req['policy']=json.loads((REPO/'valuation/examples/anker-2026H1-revised.json').read_text())
    with TestClient(app) as client:
        direct=client.post('/api/valuation/from-alphalake',json=req)
        assert direct.status_code==200,direct.text
        assert abs(direct.json()['report']['final']['value_per_share']-153.50)<.005
        bound=market_request(exports,reference_export,market_export,'anker')
        bound['policy']=json.loads((REPO/'valuation/examples/wacc/anker-revised-forecast-policy.json').read_text())
        bound['wacc_binding']['policy']=json.loads((REPO/'valuation/examples/wacc/anker-contractual-debt-policy.json').read_text())
        response=client.post('/api/valuation/from-alphalake',json=bound)
        assert response.status_code==200,response.text
        assert client.post('/api/valuation/from-alphalake',json=bound).json()==response.json()
        for request,result in [(req,direct.json()),(bound,response.json())]:
            p=request['policy']['parameters'];forecast=request['policy']['annual_forecast']
            windows={r['field']:D(r['value'])/1000000 for r in request['data']['windows'] if r['value'] is not None}
            rev=windows['FN230'];pv=D(0);wacc=D(str(result['report']['cost_of_capital']['wacc']))
            for i,row in enumerate(forecast):
                before=rev;rev*=1+D(str(row['growth']))
                reinv=(rev-before)/D(str(p['sales_to_capital']))
                fcff=rev*D(str(row['margin']))*(1-D(str(row['tax'])))-reinv
                pv+=fcff/(1+wacc)**(i+1)
                near(float(rev),result['report']['dcf']['revenue_projections'][i])
                near(float(fcff),result['report']['dcf']['fcff_projections'][i])
            g=D(str(p['terminal_growth']))
            tv=rev*(1+g)*D(str(forecast[-1]['margin']))*(1-D(str(forecast[-1]['tax'])))*(1-g/D(str(p['terminal_roic'])))/(wacc-g)/(1+wacc)**10
            near(float(pv+tv),result['report']['dcf']['value_of_operating_assets'])
            bridge=result['inputs']['equity_bridge'];eq=pv+tv+sum(D(str(x)) for x in bridge['components'].values())
            plain=eq/D(str(bridge['shares']));converted=(eq+D(str(bridge['conversion_release'])))/(D(str(bridge['shares']))+D(str(bridge['conversion_shares'])))
            near(float(min(plain,converted)),result['report']['final']['value_per_share'])
            near(bridge['components']['minority_claim_proxy'],-float(windows['FN97']*15))
            subprocess.run([sys.executable,str(REPO/'valuation/backend/tools/verify_revised_valuation.py'),str(tmp_path/(result['run_id']+'.json'))],check=True,capture_output=True,text=True)
            assert result['report']['cashflow']['fcff'] is None
        audit=response.json()['audit']['wacc_reference']['market_capital']['debt_valuation']
        bridge=response.json()['inputs']['equity_bridge']
        near(-bridge['components']['debt_claim_proxy'],float(audit['upper_bound_million_cny']))
        for case in ('short','tax','growth','legacy_scalar','version','missing_nci','missing_binding'):
            bad=copy.deepcopy(bound)
            if case=='short': bad['policy']['annual_forecast'].pop()
            elif case=='tax': bad['policy']['annual_forecast'][0]['tax']=1.1
            elif case=='growth': bad['policy']['annual_forecast'][0]['growth']=-1
            elif case=='legacy_scalar': bad['policy']['parameters']['growth']=.12
            elif case=='version': bad['policy']['policy_id']='anker-consolidated-v1'
            elif case=='missing_nci': bad['data']['windows']=[r for r in bad['data']['windows'] if r['field']!='FN97']
            else: bad.pop('wacc_binding');bad['policy']['parameters']['wacc']=.075
            assert client.post('/api/valuation/from-alphalake',json=bad).status_code==422,case


def test_revised_capital_scenario_moves_cash_and_shares_together(exports,reference_export,market_export,tmp_path,monkeypatch):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=market_request(exports,reference_export,market_export,'anker')
    req['wacc_binding']['market_capital']=copy.deepcopy(market_export['300866-funding'])
    req['wacc_binding']['policy']=json.loads((REPO/'valuation/examples/wacc/anker-funding-scenario-policy.json').read_text())
    req['policy']=json.loads((REPO/'valuation/examples/wacc/anker-revised-capital-scenario.json').read_text())
    with TestClient(app) as client:
        for retention in (0,.5,1):
            req['wacc_binding']['policy']['market']['funding_cash_retention']=retention
            response=client.post('/api/valuation/from-alphalake',json=req)
            assert response.status_code==200,response.text
            result=response.json();bridge=result['inputs']['equity_bridge']
            near(bridge['shares'],588.054402*1.01)
            near(bridge['components']['post_report_funding_cash_scenario'],4860.86*.86482*retention)
            near(bridge['conversion_shares'],1104.6608/108.86)
            audit=result['audit']['wacc_reference']['market_capital']
            equity=(D(audit['common_equity_million_cny'])-sum(D(str(v)) for k,v in bridge['components'].items() if k!='debt_claim_proxy'))
            near(float(equity),result['report']['cost_of_capital']['mv_equity'])
            eq=result['report']['dcf']['value_of_operating_assets']+sum(bridge['components'].values())
            expected=min(eq/bridge['shares'],(eq+bridge['conversion_release'])/(bridge['shares']+bridge['conversion_shares']))
            near(expected,result['report']['final']['value_per_share'])
            assert result['audit']['capital_scenario']['conversion_terms_period']=='2026-06-30'
            subprocess.run([sys.executable,str(REPO/'valuation/backend/tools/verify_revised_valuation.py'),str(tmp_path/(result['run_id']+'.json'))],check=True,capture_output=True,text=True)
            assert result['report']['cashflow']['fcff'] is None
        for case in ('cash_only','shares_only','missing_carry_reason'):
            bad=copy.deepcopy(req)
            if case=='cash_only': bad['policy']['capital_basis']='financial_date';bad['policy'].pop('capital_carry_reason')
            elif case=='shares_only': bad['wacc_binding']['market_capital']=copy.deepcopy(market_export['300866']);bad['wacc_binding']['policy']=json.loads((REPO/'valuation/examples/wacc/anker-contractual-debt-policy.json').read_text())
            else: bad['policy'].pop('capital_carry_reason')
            assert client.post('/api/valuation/from-alphalake',json=bad).status_code==422,case


def test_batch_keeps_denominator_and_isolates_missing_inputs(exports,tmp_path,monkeypatch):
    from tools.batch_valuate_alphalake import BatchPolicy,run_batch
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    requests=[request_for(exports,c) for c in ('anker','moutai')]
    policy=BatchPolicy(policy_version='reviewed-two-company-v1',review_note='真实标准链测试',
        assignments={r['data']['code']:dict(policy=r['policy']) for r in requests})
    companies=[]
    for request,symbol in zip(requests,['sz300866','sh600519']):
        companies.append(dict(instrument_id=request['data']['facts'][0]['instrument_id'],name=symbol,
            symbols=[symbol],financial_status='financial_core_complete_requires_policy',missing_core_fields=[]))
    companies.append(dict(instrument_id=999999,name='无事实',symbols=['sz999999'],
        financial_status='blocked_no_standard_facts',missing_core_fields=['FN230']))
    readiness=dict(contract_version='alphalake-readiness-v1',report_period=requests[0]['data']['report_period'],
        information_as_of=requests[0]['data']['information_as_of'],universe_scope='test_known_universe',universe_count=3,companies=companies)
    result=run_batch(readiness,policy,lambda code:exports[code])
    assert result['status_counts']=={'illustrative_valuation_completed':2,'blocked_policy_not_assigned':1}
    assert len(list(tmp_path.glob('*.json')))==2
    assert run_batch(readiness,policy,lambda code:exports[code])==result
    damaged=copy.deepcopy(exports)
    damaged['300866']['supplements']=[]
    result=run_batch(readiness,policy,lambda code:damaged[code])
    assert result['status_counts']=={'illustrative_valuation_completed':1,'blocked_missing_inputs':1,'blocked_policy_not_assigned':1}
    assert result['companies'][0]['missing']
    bad=copy.deepcopy(readiness);bad['companies'][0]['instrument_id']=999
    assert run_batch(bad,policy,lambda code:exports[code])['companies'][0]['status']=='rejected_input_or_policy'


def test_generic_earnings_power_has_no_unreviewed_equity_value(exports,tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    policy=dict(policy_id='nonfinancial-earnings-power-v1',approved_report_period='2026-06-30',
        scenario='zero-growth-10pct',review_note='零名义增长、维持投入等于折旧，仅经营价值敏感性',
        nonfinancial_scope_review='安克合并主营业务；套期影响未恢复，不作为完整估值',wacc=.1,tax_rate=.25)
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=dict(data=exports['300866'],policy=policy))
        assert response.status_code==200,response.text
        result=response.json()
        assert result['status']=='illustrative_enterprise_value_only'
        assert result['report']['final']['value_per_share'] is None
        assert result['report']['cashflow']['fcff'] is None
        ebit=result['audit']['automatic_drivers']['adjusted_ebit']
        near(result['report']['dcf']['value_of_operating_assets'],ebit*.75/.1)
        assert result['report']['dcf']['reinvestment_projections']==[0]*10
        assert client.post('/api/valuation/from-alphalake',json=dict(data=exports['600519'],policy=policy)).status_code==422
        missing=copy.deepcopy(exports['300866']);missing['windows']=[r for r in missing['windows'] if r['field']!='FN305']
        assert client.post('/api/valuation/from-alphalake',json=dict(data=missing,policy=policy)).status_code==422


def test_generic_book_dcf_forecast_and_equity_bridge(exports,tmp_path,monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    policy=dict(policy_id='nonfinancial-book-fcff-v1',approved_report_period='2026-06-30',scenario='book-scenario',
        review_note='通用模型的生产链回归，非安克推荐假设',nonfinancial_scope_review='安克非金融主营；已知金融业务由数据门控',wacc=.1,tax_rate=.25,
        annual_forecast=[dict(growth=.06 if i<5 else .02,margin=.09,tax=.25) for i in range(10)],
        sales_to_capital=3,terminal_growth=.02,terminal_roic=.1,cash_recovery=.8,operating_cash_ratio=.03,
        minority_book_multiple=1.5,debt_book_multiple=1,extra_dilution_rate=.02,additional_claims_million_cny=50,
        bridge_basis='report_date_book_debt_no_conversion_scenario',financial_asset_policy='no_credit_pending_classification',
        bridge_review='不转股、全部到期非流动负债作为债务；附加索偿50百万元是情景，未分类金融投资不计入')
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=dict(data=exports['300866'],policy=policy))
        assert response.status_code==200,response.text
        result=response.json();assert result['status']=='illustrative_book_equity_scenario'
        assert result['growth_sensitivity'] is None  # 显式逐年政策不套历史规则研究。
        w={r['field']:float(r['value'])/1e6 for r in exports['300866']['windows'] if r['value'] is not None}
        rev=w['FN230'];pv=0
        for year,row in enumerate(policy['annual_forecast'],1):
            previous=rev;rev*=1+row['growth']
            cash=rev*row['margin']*(1-row['tax'])-(rev-previous)/3
            near(result['report']['dcf']['fcff_projections'][year-1],cash)
            pv+=cash/1.1**year
        terminal=rev*1.02*.09*.75*(1-.02/.1)/(.1-.02)
        ev=pv+terminal/1.1**10
        fixed=w['FN133']*.8-w['FN230']*.03-sum(w[f] for f in ('FN41','FN52','FN55','FN56','FN439'))-w['FN69']*1.5-50
        near(result['report']['final']['value_per_share'],(ev+fixed)/(w['FN238']*1.02))
        assert result['report']['cashflow']['fcff'] is None
        assert result['terminal_sensitivity']['delta_per_share']==0
        # 真正单因素：高于/低于WACC及零增长；用终值公式独立核对影响。
        for roic,growth in ((.15,.02),(.08,.02),(.15,0)):
            changed=policy|dict(terminal_roic=roic,terminal_growth=growth)
            req=dict(data=exports['300866'],policy=changed)
            response=client.post('/api/valuation/from-alphalake',json=req)
            assert response.status_code==200,response.text
            observed=response.json();sensitivity=observed['terminal_sensitivity']
            assert client.post('/api/valuation/from-alphalake',json=req).json()==observed
            near(sensitivity['counterfactual_value_per_share'],
                 (pv+rev*(1+growth)*.09*.75/.1/1.1**10+fixed)/(w['FN238']*1.02))
            inputs,_=build_inputs(AlphaLakeRequest.model_validate(req))
            direct=run_full_valuation(inputs)
            assert observed['report']['dcf']==jsonable_encoder(asdict(direct))['dcf']
            near(sensitivity['delta_per_share'],sensitivity['counterfactual_value_per_share']-observed['report']['final']['value_per_share'])
            if growth==0: near(sensitivity['delta_per_share'],0)
            if roic>.1 and growth>0:
                claims=changed['additional_claims_million_cny']+(sensitivity['baseline_value_per_share']+sensitivity['counterfactual_value_per_share'])/2*w['FN238']*1.02
                stressed=client.post('/api/valuation/from-alphalake',json=dict(data=exports['300866'],policy=changed|dict(additional_claims_million_cny=claims)))
                assert stressed.status_code==200,stressed.text
                stress=stressed.json()
                assert stress['report']['final']['value_per_share']>0
                assert stress['terminal_sensitivity']['counterfactual_value_per_share']<0
                assert stress['terminal_sensitivity']['counterfactual_equity_status']=='nonpositive_equity_residual_requires_distress_model'
        assert client.post('/api/valuation/from-alphalake',json=dict(data=exports['600519'],policy=policy)).status_code==422
        for field in ('FN238','FN52','FN133'):
            bad=copy.deepcopy(exports['300866']);bad['windows']=[r for r in bad['windows'] if r['field']!=field]
            assert client.post('/api/valuation/from-alphalake',json=dict(data=bad,policy=policy)).status_code==422
        invalid=policy|dict(terminal_growth=.1)
        assert client.post('/api/valuation/from-alphalake',json=dict(data=exports['300866'],policy=invalid)).status_code==422
        automatic={k:v for k,v in policy.items() if k!='annual_forecast'}
        automatic.update(policy_id='nonfinancial-history-fcff-v1',growth_floor=-.1,growth_ceiling=.2,growth_shift=0,margin_shift=0)
        response=client.post('/api/valuation/from-alphalake',json=dict(data=exports['300866'],policy=automatic))
        assert response.status_code==200,response.text
        auto=response.json()
        q={f['period']:float(f['value']) for f in exports['300866']['facts'] if f['field']=='FN230'}
        expected=min(.2,max(-.1,((q['2026-03-31']/q['2025-03-31']-1)+(q['2026-06-30']/q['2025-06-30']-1))/2))
        near(auto['audit']['forecast_rule_evidence']['clipped_scenario_growth'],expected)
        generated=auto['audit']['generated_policy'];assert len(generated['annual_forecast'])==10
        rev=w['FN230'];pv=0;ebit=w['FN86']+w['FN305']-w['FN306']-w['FN83']-w['FN82']-w['FN301'];margin=ebit/rev
        for year in range(1,11):
            growth=expected if year<=5 else expected+(.02-expected)*(year-5)/5
            previous=rev;rev*=1+growth
            cash=rev*margin*.75-(rev-previous)/3
            near(auto['report']['dcf']['fcff_projections'][year-1],cash)
            pv+=cash/1.1**year
        ev=pv+rev*1.02*margin*.75*(1-.02/.1)/(.1-.02)/1.1**10
        near(auto['report']['final']['value_per_share'],(ev+fixed)/(w['FN238']*1.02))
        sensitivity=auto['growth_sensitivity']
        assert sensitivity['evidence']['adoption']=='not_adopted_as_default_or_company_specific_forecast'
        assert 'one_year_validation_failed' in sensitivity['evidence']['counterevidence']
        from hashlib import sha256
        receipt=sensitivity['evidence']['receipt']
        assert sha256((REPO/receipt['path']).read_bytes()).hexdigest()==receipt['sha256']
        # 从四个冻结驱动独立复算零增长对照，不复制引擎的增长生成函数。
        rev=w['FN230'];zero_pv=0
        for year,g in enumerate([0]*5+[.004,.008,.012,.016,.02],1):
            previous=rev;rev*=1+g
            cash=rev*margin*.75-(rev-previous)/3
            near(sensitivity['counterfactual_fcff_million_cny'][year-1],cash)
            zero_pv+=cash/1.1**year
        zero_ev=zero_pv+rev*1.02*margin*.75*(1-.02/.1)/(.1-.02)/1.1**10
        near(sensitivity['counterfactual_value_per_share'],(zero_ev+fixed)/(w['FN238']*1.02))
        near(sensitivity['delta_per_share'],(zero_ev-ev)/(w['FN238']*1.02))
        assert sensitivity['baseline_fcff_million_cny']==auto['report']['dcf']['fcff_projections']
        from api.alphalake import growth_path_sensitivity
        original_inputs,_=build_inputs(AlphaLakeRequest.model_validate(dict(data=exports['300866'],policy=automatic)))
        original_report=run_full_valuation(original_inputs)
        before=original_inputs.model_dump(mode='json')
        assert growth_path_sensitivity(original_inputs,original_report)==sensitivity
        assert original_inputs.model_dump(mode='json')==before
        assert jsonable_encoder(asdict(original_report))==auto['report']
        zero_req=dict(data=exports['300866'],policy=automatic|dict(growth_floor=0,growth_ceiling=0))
        zero=client.post('/api/valuation/from-alphalake',json=zero_req).json()
        near(zero['report']['final']['value_per_share'],sensitivity['counterfactual_value_per_share'])
        assert zero['growth_sensitivity']['delta_per_share']==0
        assert client.post('/api/valuation/from-alphalake',json=zero_req).json()==zero
        assert auto['report']['final']['value_per_share']>sensitivity['counterfactual_value_per_share']
        claims=automatic['additional_claims_million_cny']+(auto['report']['final']['value_per_share']+sensitivity['counterfactual_value_per_share'])/2*w['FN238']*1.02
        stressed=client.post('/api/valuation/from-alphalake',json=dict(data=exports['300866'],policy=automatic|dict(additional_claims_million_cny=claims)))
        assert stressed.status_code==200,stressed.text
        assert stressed.json()['growth_sensitivity']['counterfactual_value_per_share']<0
        assert stressed.json()['growth_sensitivity']['counterfactual_equity_status']=='nonpositive_equity_residual_requires_distress_model'
        # 2025Q1 位于当前 TTM 窗口之外，历史规则也必须校验其源位。
        corrupted=copy.deepcopy(exports['300866'])
        next(f for f in corrupted['facts'] if f['field']=='FN230' and f['period']=='2025-03-31')['value']='1'
        assert client.post('/api/valuation/from-alphalake',json=dict(data=corrupted,policy=automatic)).status_code==422
        short=copy.deepcopy(exports['300866']);short['facts']=[f for f in short['facts'] if not(f['field']=='FN230' and f['period']=='2025-03-31')]
        assert client.post('/api/valuation/from-alphalake',json=dict(data=short,policy=automatic)).status_code==422
        distressed=automatic|dict(additional_claims_million_cny=1e9)
        assert client.post('/api/valuation/from-alphalake',json=dict(data=exports['300866'],policy=distressed)).status_code==422


def test_batch_industry_rules_gate_age_conflict_and_override(exports,tmp_path,monkeypatch):
    from datetime import datetime,timedelta
    from tools.batch_valuate_alphalake import BatchPolicy,run_batch
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    data=exports['300866'];at=datetime.fromisoformat(data['information_as_of'])
    profile=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    profile['nonfinancial_scope_review']='路由规则模拟、安克财务真实标准链；不声称实际行业归属验收'
    policy=BatchPolicy(policy_version='routing-test-v1',review_note='routing unit check',assignments={},industry_rules=[dict(
        rule_id='reviewed-node',source='tdx',taxonomy_code='tdx_industry',node_codes=['T010101'],max_age_days=30,review_note='test node only',policy=profile)])
    member=dict(source='tdx',taxonomy_code='tdx_industry',node_code='T010101',node_id=1,ingest_run_id=1,
        observed_at=(at-timedelta(hours=1)).isoformat(),run_finished_at=at.isoformat())
    company=dict(instrument_id=data['facts'][0]['instrument_id'],name='anker',symbols=['sz300866'],
        financial_status='financial_core_complete_requires_policy',missing_core_fields=[],industry_memberships=[member])
    scan=dict(contract_version='alphalake-readiness-v1',report_period=data['report_period'],information_as_of=data['information_as_of'],
        universe_scope='test',universe_count=1,companies=[company])
    calls=[]
    def export(code):calls.append(code);return exports[code]
    result=run_batch(scan,policy,export)
    assert result['companies'][0]['status']=='illustrative_book_equity_scenario'
    assert result['companies'][0]['policy_route']['classification_evidence']==[member]
    assert calls==['300866']
    calls.clear();stale=copy.deepcopy(scan);stale['companies'][0]['industry_memberships'][0]['observed_at']=(at-timedelta(days=31)).isoformat()
    assert run_batch(stale,policy,export)['companies'][0]['status']=='blocked_no_reviewed_industry_policy'
    incomplete=copy.deepcopy(scan);incomplete['companies'][0]['missing_core_fields']=['FN238']
    blocked=run_batch(incomplete,policy,lambda code:pytest.fail('known core gap must not repeat TTM export'))
    assert blocked['companies'][0]['missing']==['TDX/FN238']
    assert blocked['companies'][0]['status']=='blocked_missing_inputs'

    assert not calls
    ambiguous=policy.model_copy(deep=True);ambiguous.industry_rules.append(policy.industry_rules[0].model_copy(update={'rule_id':'other'}))
    assert run_batch(scan,ambiguous,export)['companies'][0]['status']=='blocked_ambiguous_industry_policy'
    assert not calls
    from tools.batch_valuate_alphalake import Assignment
    ambiguous.assignments['300866']=Assignment(policy=profile)
    assert run_batch(scan,ambiguous,export)['companies'][0]['policy_route']=={'kind':'explicit_company_assignment'}
    broken=copy.deepcopy(scan);broken['companies'][0]['industry_memberships'][0]['observed_at']='bad-date'
    assert run_batch(broken,policy,export)['companies'][0]['status']=='rejected_industry_evidence'
    # 已知口径问题优先于公司指定和行业匹配，不导出、不生成估值文件。
    calls.clear()
    ambiguous.exclusions['300866']='真实审核发现历史口径未闭合；此处测试隔离优先级'
    assert run_batch(scan,ambiguous,export)['companies'][0]['status']=='blocked_review_exclusion'
    assert not calls
    with pytest.raises(ValueError):
        BatchPolicy(policy_version='invalid',review_note='invalid',assignments={},exclusions={'300866':'   '})


def test_source_conflict_blocks_old_usable_values(exports,tmp_path,monkeypatch):
    from data_sources.alphalake import MissingInputs
    from tools.batch_valuate_alphalake import BatchPolicy,run_batch
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=request_for(exports,'anker')
    conflict=dict(code='300866',period='2025-12-31',artifact_id=123,artifact_sha256='a'*64,
                  available_at='2026-09-06T00:00:00+00:00',reason='conflicting duplicate provider records: test')
    req['data']['source_conflicts']=[conflict]
    assert req['data']['facts'] and req['data']['windows']
    with pytest.raises(MissingInputs,match='source_record_conflict:2025-12-31'):
        build_inputs(AlphaLakeRequest.model_validate(req))
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==422 and response.json()['detail']['status']=='blocked_missing_inputs'
    policy=BatchPolicy(policy_version='blocked-source-test',review_note='old usable facts remain blocked',assignments={'300866':dict(policy=req['policy'])})
    readiness=dict(contract_version='alphalake-readiness-v1',report_period=req['data']['report_period'],
        information_as_of=req['data']['information_as_of'],universe_scope='test',universe_count=1,
        companies=[dict(instrument_id=req['data']['facts'][0]['instrument_id'],name='Anker',symbols=['sz300866'],
                        financial_status='blocked_source_record_conflict',missing_core_fields=[],source_conflicts=[conflict])])
    result=run_batch(readiness,policy,lambda code:pytest.fail('conflict must not be exported for valuation'))
    assert result['status_counts']=={'blocked_source_record_conflict':1}
    assert not list(tmp_path.glob('*.json'))


def test_new_companies_standard_chain_and_review_hold(tmp_path,monkeypatch):
    from decimal import Decimal
    from api.alphalake import evaluate
    from tools.batch_valuate_alphalake import BatchPolicy,run_batch
    output=tmp_path/'exports'
    env=os.environ|{'ALPHALAKE_GENERIC_EXPORT_DIR':str(output),'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealGenericValuationSourceChain$','-count=1'],
                   cwd=REPO,env=env,check=True,capture_output=True,text=True)
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path/'runs'))
    profile=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    profile['review_note']='管线数学回归：已知比较列差异保留，批量正式试跑隔离这两家公司；不确认可比增长率'
    profile['nonfinancial_scope_review']='汇川工业自动化、海天调味品合并报表样本；仅作原披露混合版本的机械情景'
    companies=[]
    for code in ('300124','603288'):
        data=json.loads((output/(code+'.json')).read_text())
        result=evaluate(AlphaLakeRequest(data=data,policy=profile))
        assert result['status']=='illustrative_book_equity_scenario'
        w={r['field']:float(r['value'])/1e6 for r in data['windows'] if r['value'] is not None}
        facts={(r['field'],r['period']):Decimal(r['value']) for r in data['facts']}
        revenue=sum(facts['FN230',p] for p in ('2025-09-30','2025-12-31','2026-03-31','2026-06-30'))
        near(w['FN230'],float(revenue)/1e6)
        for field in ('FN86','FN305','FN306','FN83','FN82','FN301'):
            expected=facts[field,'2025-12-31']+facts[field,'2026-06-30']-facts[field,'2025-06-30']
            near(w[field],float(expected)/1e6)
        for field in ('FN238','FN133','FN41','FN52','FN55','FN56','FN439','FN69'):
            near(w[field],float(facts[field,'2026-06-30'])/1e6)
        q={r['period']:float(r['value']) for r in data['facts'] if r['field']=='FN230'}
        growth=min(.2,max(-.1,((q['2026-03-31']/q['2025-03-31']-1)+(q['2026-06-30']/q['2025-06-30']-1))/2))
        near(result['audit']['forecast_rule_evidence']['clipped_scenario_growth'],growth)
        rev=w['FN230'];margin=(w['FN86']+w['FN305']-w['FN306']-w['FN83']-w['FN82']-w['FN301'])/rev;pv=0
        for year in range(1,11):
            rate=growth if year<=5 else growth+(.02-growth)*(year-5)/5
            previous=rev;rev*=1+rate
            cash=rev*margin*.75-(rev-previous)/3
            near(result['report']['dcf']['fcff_projections'][year-1],cash)
            pv+=cash/1.1**year
        ev=pv+rev*1.02*margin*.75*(1-.02/.1)/(.1-.02)/1.1**10
        fixed=max(0,w['FN133']*.8-w['FN230']*.03)-sum(w[f] for f in ('FN41','FN52','FN55','FN56','FN439'))-w['FN69']
        near(result['report']['final']['value_per_share'],(ev+fixed)/(w['FN238']*1.02))
        assert result['report']['cashflow']['fcff'] is None
        companies.append(dict(instrument_id=data['facts'][0]['instrument_id'],name=code,symbols=[('sz' if code=='300124' else 'sh')+code],
                              financial_status='financial_core_complete_requires_policy',missing_core_fields=[]))
    policy=BatchPolicy(policy_version='real-comparative-review-v1',review_note='已知原文比较列差异未协调',
                       assignments={code:dict(policy=profile) for code in ('300124','603288')},
                       exclusions={code:'generic-valuation-2026/values.json：旧披露与新比较列不一致，暂停自动历史预测' for code in ('300124','603288')})
    scan=dict(contract_version='alphalake-readiness-v1',report_period=data['report_period'],information_as_of=data['information_as_of'],
              universe_scope='real_two_company_sample',universe_count=2,companies=companies)
    result=run_batch(scan,policy,lambda code:pytest.fail('review hold must precede export'))
    assert result['status_counts']=={'blocked_review_exclusion':2}


@pytest.mark.parametrize('kind',['history','screen','industry_weights'])
def test_generic_reference_wacc_real_chain(exports,reference_export,tmp_path,monkeypatch,kind):
    from decimal import Decimal as D
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=reference_request(exports,reference_export,'anker')
    policy=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    policy.pop('wacc')
    policy['nonfinancial_scope_review']='安克合并普通经营模型回归；不是公司预测建议'
    if kind=='screen':
        policy={k:v for k,v in policy.items() if k in ('approved_report_period','scenario','review_note','nonfinancial_scope_review','tax_rate')}
        policy['policy_id']='nonfinancial-earnings-power-v1'
    req['policy']=policy
    if kind=='industry_weights':
        req['wacc_binding']['policy'].update(capital_structure_basis='industry_reference_weights',target_debt_weight=None)
        req['wacc_binding']['policy']['industries'][0]['weight']=.35
        req['wacc_binding']['policy']['industries'].append(dict(industry='Machinery',weight=.65,reason='验证多行业加权D/E代理；不是安克业务分类结论'))
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==200,response.text
        result=response.json();p=req['wacc_binding']['policy']
        country={(r['subject_code'],r['metric_code']):D(r['value']) for r in reference_export['country_risk']}
        industries={(r['industry'],r['metric_code']):D(r['value']) for r in reference_export['industry_stats']}
        rf=next(D(r['value']) for r in reference_export['yield_curve'] if r['tenor_months']==120)-country['CN','sovereign_default_spread']
        beta=sum(D(str(i['weight']))*industries[i['industry'],p['beta_metric']] for i in p['industries'])
        tax,kd=[D(str(p[k])) for k in ('tax_shield_rate','debt_cost_pretax')]
        if kind=='industry_weights':
            de=sum(D(str(i['weight']))*industries[i['industry'],'debt_equity_ratio'] for i in p['industries'])
            dw=de/(1+de)
            assert result['report']['cost_of_capital']['capital_structure_basis']=='industry_reference_weights'
            assert result['report']['cost_of_capital']['mv_equity'] is None
            assert result['audit']['wacc_reference']['industry_capital']['status']=='industry_target_proxy_not_company_market_structure'
            near(result['audit']['wacc_reference']['industry_capital']['debt_weight'],float(dw))
            for invalid in ('override','negative','sample','unit'):
                bad=copy.deepcopy(req)
                if invalid=='override':bad['wacc_binding']['policy']['target_debt_weight']=.1
                else:
                    row=next(r for r in bad['wacc_binding']['references']['industry_stats'] if r['industry']==p['industries'][0]['industry'] and r['metric_code']=='debt_equity_ratio')
                    if invalid=='negative':row.update(value='-0.100000000000',raw_value='-0.1')
                    elif invalid=='unit':row['raw_unit']='percent'
                    else:row['sample_count']+=1
                assert client.post('/api/valuation/from-alphalake',json=bad).status_code==422
        else:
            dw=D(str(p['target_debt_weight']))
        crp=sum(D(str(c['weight']))*D(str(c['exposure_scale']))*country[c['country'],'country_risk_premium'] for c in p['countries'])
        wacc=(rf+beta*(1+(1-tax)*dw/(1-dw))*country['mature','mature_market_erp']+crp)*(1-dw)+kd*(1-tax)*dw
        near(result['report']['cost_of_capital']['wacc'],float(wacc))
        near(result['inputs']['valuation_assumptions']['cost_of_capital_stable_override'],float(wacc))
        assert result['inputs']['methodology_choices']['cost_of_capital_approach']=='reference_snapshot'
        assert result['report']['cashflow']['fcff'] is None
        assert 'wacc_binding' in result['inputs']['prepared_ttm']['provenance']
        if kind=='screen':near(result['report']['dcf']['value_of_operating_assets'],result['audit']['automatic_drivers']['adjusted_ebit']*.75/float(wacc))
        else:
            assert result['status']=='illustrative_book_equity_scenario'
            near(result['audit']['generated_policy']['wacc'],float(wacc))
        assert client.post('/api/valuation/from-alphalake',json=req).json()==result
        for mutation in ('dual','neither','company','scope','stale','terminal'):
            bad=copy.deepcopy(req)
            if mutation=='dual':bad['policy']['wacc']=.1
            elif mutation=='neither':bad.pop('wacc_binding')
            elif mutation=='company':bad['wacc_binding']['policy']['code']='000001'
            elif mutation=='scope':bad['wacc_binding']['policy']['scope']='liquor_proxy'
            elif mutation=='stale':bad['wacc_binding']['policy']['max_beta_age_days']=0
            elif kind=='history':bad['policy']['terminal_growth']=.099
            else:continue
            assert client.post('/api/valuation/from-alphalake',json=bad).status_code==422,mutation
    assert len(list(tmp_path.glob('*.json')))==1


def test_industry_wacc_routes_real_reference_and_financial_packets(exports,reference_export,tmp_path,monkeypatch):
    from tools.batch_valuate_alphalake import BatchPolicy,run_batch
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    req=reference_request(exports,reference_export,'anker')
    d=req['data'];wp=req['wacc_binding']['policy'];wp.pop('code')
    profile=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text());profile.pop('wacc')
    profile['nonfinancial_scope_review']='真实安克财务和参考包，行业关系为路由受控夹具'
    rule=dict(rule_id='reference-test',source='tdx',taxonomy_code='test-routing',node_codes=['TEST'],max_age_days=30,
        review_note='模拟行业归属，不是实际分类验收',policy=profile,wacc_policy=wp)
    policy=BatchPolicy(policy_version='reference-routing-test',review_note='explicit target-weight reference scenario',
        assignments={},industry_rules=[rule],wacc_references=reference_export)
    member=dict(source='tdx',taxonomy_code='test-routing',node_code='TEST',observed_at=d['information_as_of'],run_finished_at=d['information_as_of'])
    scan=dict(contract_version='alphalake-readiness-v1',report_period=d['report_period'],information_as_of=d['information_as_of'],
        universe_count=1,universe_scope='test',companies=[dict(instrument_id=d['facts'][0]['instrument_id'],name='anker',symbols=['sz300866'],
        financial_status='financial_core_complete_requires_policy',missing_core_fields=[],industry_memberships=[member])])
    result=run_batch(scan,policy,lambda code:d)
    row=result['companies'][0]
    assert row['status']=='illustrative_book_equity_scenario'
    saved=json.loads((tmp_path/(row['run_id']+'.json')).read_text())
    assert saved['request']['wacc_binding']['policy']['code']=='300866'
    assert saved['request']['policy']['wacc'] is None
    assert saved['report']['cost_of_capital']['approach_used']=='reference_snapshot'
    assert result['policy']['industry_rules'][0]['wacc_policy']['code'] is None
    assert run_batch(scan,policy,lambda code:d)==result
    missing=policy.model_copy(update={'wacc_references':None})
    assert run_batch(scan,missing,lambda code:pytest.fail('missing references must block before export'))['companies'][0]['status']=='blocked_missing_wacc_references'
    for change in ({'code':'300866'},{'scope':'liquor_proxy'},{'report_period':'2025-12-31'}):
        bad=copy.deepcopy(rule);bad['wacc_policy'].update(change)
        with pytest.raises(ValueError):BatchPolicy(policy_version='bad',review_note='bad',assignments={},industry_rules=[bad])
    bad=copy.deepcopy(rule);bad['policy']['wacc']=.1
    with pytest.raises(ValueError):BatchPolicy(policy_version='dual',review_note='dual',assignments={},industry_rules=[bad])


def test_bear_standard_chain_preserves_interest_scope_review(tmp_path,monkeypatch):
    from api.alphalake import evaluate
    from tools.batch_valuate_alphalake import BatchPolicy,run_batch
    exports=tmp_path/'exports'
    env=os.environ|{'ALPHALAKE_BEAR_EXPORT_DIR':str(exports),'GOPROXY':'off','GOSUMDB':'off'}
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealBearValuationSourceChain$','-count=1'],cwd=REPO,env=env,check=True,capture_output=True,text=True)
    d=json.loads((exports/'002959.json').read_text())
    p=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    p['nonfinancial_scope_review']='小熊真实源链机械验算；年度与半年报租赁利息口径差异另列审核'
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path/'runs'))
    result=evaluate(AlphaLakeRequest(data=d,policy=p))
    assert result['status']=='illustrative_book_equity_scenario'
    assert result['report']['cashflow']['fcff'] is None
    ledger=json.loads((REPO/'internal/ingest/testdata/bear-valuation-2026/values.json').read_text())
    scope=next(r for r in ledger if r['comparison']=='different_float32')
    assert scope['kind']=='interest_including_separate_lease'
    fact=next(f for f in result['request']['data']['facts'] if f['field']=='FN305' and f['period']=='2025-12-31')
    assert fact['bits']==scope['source_bits']
    assert float(fact['value'])!=float(scope['pdf_value'])
    scan=dict(contract_version='alphalake-readiness-v1',report_period=d['report_period'],information_as_of=d['information_as_of'],universe_scope='test',universe_count=1,
        companies=[dict(instrument_id=fact['instrument_id'],name='小熊电器',symbols=['sz002959'],financial_status='financial_core_complete_requires_policy',missing_core_fields=[])])
    policy=BatchPolicy(policy_version='bear-scope-review-v1',review_note='不能把主表位匹配当租赁口径统一',assignments={'002959':dict(policy=p)},
        exclusions={'002959':'年度主表利息不含附注另列租赁融资费用，半年报包含；政策尚待审核'})
    assert run_batch(scan,policy,lambda code:pytest.fail('known scope hold must block export'))['companies'][0]['status']=='blocked_review_exclusion'


def test_capital_reference_binding_and_industry_route(exports,tmp_path,monkeypatch):
    from datetime import datetime
    from api.alphalake import evaluate
    from tools.batch_valuate_alphalake import BatchPolicy,run_batch
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    refs=json.loads((REPO/'internal/source/damodaran/testdata/capital-export.json').read_text())
    data=copy.deepcopy(exports['300866'])
    # 财务取自真实Go标准链；本单元检查使用固定参考包时钟和受控行业成员。
    data['information_as_of']=refs['information_as_of']
    p=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    p['sales_to_capital']=None
    cp=dict(code='300866',report_period=data['report_period'],industry='Electronics (Consumer & Office)',
        mapping_reason='test explicit single-industry proxy',minimum_sample_count=30,max_age_days=370,ratio_multiplier=1,
        adoption_basis='historical_industry_ratio_as_marginal_reinvestment_proxy',adoption_reason='test forecast proxy, not a company reported fact')
    req=dict(data=data,policy=p,capital_binding=dict(references=refs,policy=cp))
    result=evaluate(AlphaLakeRequest(**req))
    assert evaluate(AlphaLakeRequest(**req))==result
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=req)
        assert response.status_code==200,response.text
        assert response.json()==result
    assert result['audit']['capital_reference']['sales_to_capital']==1.905898248522
    assert result['inputs']['valuation_assumptions']['sales_to_capital_high']==1.905898248522
    assert result['inputs']['prepared_ttm']['provenance']['capital_binding']
    assert any('capitalizes R&D' in b for b in result['audit']['boundaries'])
    assert result['request']['data']==AlphaLakeRequest(**req).data.model_dump(mode='json')
    for mutate in (
        lambda v:v['policy'].update(sales_to_capital=3),
        lambda v:v.update(capital_binding=None),
        lambda v:v['capital_binding']['policy'].update(code='600519'),
        lambda v:v['capital_binding']['policy'].update(report_period='2026-03-31'),
        lambda v:v['capital_binding']['policy'].update(industry='unknown'),
        lambda v:v['capital_binding']['policy'].update(max_age_days=0),
        lambda v:v['capital_binding']['policy'].update(minimum_sample_count=123),
        lambda v:v['capital_binding']['references']['observations'].pop(),
        lambda v:v['capital_binding']['references']['observations'][0].update(raw_unit='percent'),
        lambda v:v['capital_binding']['references']['observations'][0].update(value='3.000000000000'),
        lambda v:v['capital_binding']['references'].update(information_as_of='2020-01-01T00:00:00Z'),
        lambda v:v['capital_binding']['references'].update(recorded_cutoff='2020-01-01T00:00:00Z'),
    ):
        bad=copy.deepcopy(req);mutate(bad)
        with pytest.raises(ValueError):build_inputs(AlphaLakeRequest(**bad))
    rule=dict(rule_id='capital-proxy-test',source='tdx',taxonomy_code='tdx_shenwan_industry',node_codes=['X400202'],
        max_age_days=7,review_note='controlled route test',policy=p,capital_policy={k:v for k,v in cp.items() if k!='code'})
    policy=BatchPolicy(policy_version='capital-test',review_note='controlled industry, real financial chain',assignments={},industry_rules=[rule],capital_references=refs)
    at=datetime.fromisoformat(data['information_as_of'])
    member=dict(source='tdx',taxonomy_code='tdx_shenwan_industry',node_code='X400202',node_id=1,ingest_run_id=1,observed_at=at.isoformat(),run_finished_at=at.isoformat())
    company=dict(instrument_id=data['facts'][0]['instrument_id'],name='anker',symbols=['sz300866'],financial_status='financial_core_complete_requires_policy',missing_core_fields=[],industry_memberships=[member])
    scan=dict(contract_version='alphalake-readiness-v1',report_period=data['report_period'],information_as_of=data['information_as_of'],universe_scope='controlled industry',universe_count=1,companies=[company])
    row=run_batch(scan,policy,lambda code:data)['companies'][0]
    assert row['status']=='illustrative_book_equity_scenario' and row['run_id']==result['run_id']
    missing=policy.model_copy(update={'capital_references':None})
    assert run_batch(scan,missing,lambda code:pytest.fail('missing reference must block before export'))['companies'][0]['status']=='blocked_missing_capital_references'
    for change in ({'code':'300866'},{'report_period':'2026-03-31'}):
        bad=copy.deepcopy(rule);bad['capital_policy'].update(change)
        with pytest.raises(ValueError):BatchPolicy(policy_version='bad',review_note='bad',assignments={},industry_rules=[bad])


def test_company_entry_selection_summary_and_no_fallback(exports,tmp_path,monkeypatch):
    from tools.batch_valuate_alphalake import BatchPolicy
    from tools.company_valuation import company_valuation
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    data=exports['300866']
    company=dict(instrument_id=data['facts'][0]['instrument_id'],name='安克创新',symbols=['sz300866'],exchange_mic='XSHE',
                 financial_status='financial_core_complete_requires_policy',missing_core_fields=[],industry_memberships=[dict(
                     source='tdx',taxonomy_code='tdx_industry',node_code='test',observed_at=data['information_as_of'],run_finished_at=data['information_as_of'])])
    scan=dict(contract_version='alphalake-readiness-v1',report_period=data['report_period'],information_as_of=data['information_as_of'],
              universe_scope='real financial sample with synthetic classification',universe_count=1,companies=[company])
    profile=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    generic=BatchPolicy(policy_version='industry-v1',review_note='synthetic industry routing; real financial input',assignments={},industry_rules=[dict(
        rule_id='test',source='tdx',taxonomy_code='tdx_industry',node_codes=['test'],max_age_days=30,review_note='test only',policy=profile)])
    specific=BatchPolicy(policy_version='anker-v2',review_note='reviewed company policy',assignments={'300866':dict(
        policy=json.loads((REPO/'valuation/examples/anker-2026H1-revised.json').read_text()))})
    calls=[]
    def export(code):calls.append(code);return exports[code]
    result=company_valuation(scan,'300866',[generic,specific],export,tmp_path)
    assert calls==['300866']  # 两个候选共享同一标准导出。
    assert result['selection']==dict(policy_version='anker-v2',reason='configured_company_assignment_precedes_industry',fallback_applied=False)
    assert len(result['candidates'])==2
    assert result['valuation']['policy_id']=='anker-consolidated-v2'
    assert round(result['valuation']['value_per_share']['value'],2)==153.50
    assert result['valuation']['value_per_share']['unit']=='CNY/share'
    assert result['valuation']['share_date']==data['report_period']
    assert result['valuation']['historical_fcff_status'] is not None
    assert Path(result['valuation']['evidence']['run_file']).exists()
    assert company_valuation(scan,'300866',[specific,generic],export,tmp_path)['valuation']==result['valuation']
    explicit=company_valuation(scan,'300866',[specific,generic],export,tmp_path,select='industry-v1')
    assert explicit['selection']['policy_version']=='industry-v1'
    assert explicit['valuation']['policy_id']=='nonfinancial-history-fcff-v1'
    assert explicit['valuation']['terminal_sensitivity']['method']=='terminal_roic_equals_terminal_wacc'
    assert explicit['valuation']['growth_sensitivity']['status']=='illustrative_growth_path_sensitivity'
    assert result['valuation']['growth_sensitivity'] is None
    # 同级专项绝不按价格或传入顺序取一个。
    other=specific.model_copy(update={'policy_version':'another-reviewed-scenario'})
    ambiguous=company_valuation(scan,'300866',[specific,other],export,tmp_path)
    assert ambiguous['status']=='blocked_ambiguous_policy' and ambiguous['valuation'] is None
    # 过期专项不会悄悄替换为成功行业值。
    expired=specific.model_copy(deep=True)
    from datetime import date
    expired.assignments['300866'].policy.approved_report_period=date(2025,12,31)
    blocked=company_valuation(scan,'300866',[expired,generic],export,tmp_path)
    assert blocked['selection']['policy_version']=='anker-v2'
    assert blocked['status']=='rejected_input_or_policy' and blocked['valuation'] is None
    assert blocked['candidates'][1]['valuation'] is not None
    for code in ('999999','abc'):
        if code=='abc':
            with pytest.raises(ValueError):company_valuation(scan,code,[specific],export,tmp_path)
        else:
            assert company_valuation(scan,code,[specific],lambda _:pytest.fail('unknown identity'),tmp_path)['status']=='blocked_security_identity'
    with pytest.raises(ValueError,match='uniquely'):
        company_valuation(scan,'300866',[specific,specific],export,tmp_path)
    with pytest.raises(ValueError,match='not supplied'):
        company_valuation(scan,'300866',[specific],export,tmp_path,select='missing')
    conflict=copy.deepcopy(scan);conflict['companies'][0]['source_conflicts']=[dict(reason='conflicting source rows')]
    rejected=company_valuation(conflict,'300866',[specific,generic],lambda _:pytest.fail('source conflict must not export'),tmp_path)
    assert rejected['status']=='blocked_source_record_conflict' and rejected['valuation'] is None
    cli=subprocess.run([sys.executable,'-m','tools.company_valuation','unused.duckdb','bad-code',
                        '--period','2026-06-30','--as-of',data['information_as_of'],'--policy','unused.json'],
                       cwd=REPO/'valuation/backend',text=True,capture_output=True)
    assert cli.returncode==1
    failure=json.loads(cli.stdout)
    assert failure['status']=='failed_request' and failure['valuation'] is None
    assert failure['code']=='bad-code' and failure['candidates']==[]

    # 真实失败子进程覆盖入口、参考选择和逐公司导出，不只测字符串辅助函数。
    policy_file=tmp_path/'policy.json'
    policy_file.write_text(specific.model_dump_json())
    executable=tmp_path/'failing-alphalake'
    for stage in ('valuation-readiness','export-wacc-references','export-valuation'):
        executable.write_text(f'''#!{sys.executable}
import json,sys
if sys.argv[1] == {stage!r}:
    print("底层诊断: simulated database lock conflict", file=sys.stderr)
    raise SystemExit(17)
print({json.dumps(scan)!r})
''')
        executable.chmod(0o700)
        args=[sys.executable,'-m','tools.company_valuation','unused.duckdb','300866',
              '--period','2026-06-30','--as-of',data['information_as_of'],
              '--policy',str(policy_file),'--alphalake',str(executable)]
        if stage=='export-wacc-references':
            args+=['--reference-database','reference.duckdb']
        cli=subprocess.run(args,cwd=REPO/'valuation/backend',text=True,capture_output=True,timeout=30)
        failure=json.loads(cli.stdout)
        assert cli.returncode==1 and failure['valuation'] is None
        assert failure['status']==('failed_execution' if stage=='export-valuation' else 'failed_request')
        assert '底层诊断: simulated database lock conflict' in failure['reason']
        assert '17' in failure['reason']
        assert cli.stderr==''  # 原因随结构化结果交付。
        batch_args=[sys.executable,'-m','tools.batch_valuate_alphalake','unused.duckdb',
                    '--period','2026-06-30','--as-of',data['information_as_of'],
                    '--policy',str(policy_file),'--alphalake',str(executable),
                    '--output-dir',str(tmp_path/'batch')]
        if stage=='export-wacc-references':
            batch_args+=['--reference-database','reference.duckdb']
        batch=subprocess.run(batch_args,cwd=REPO/'valuation/backend',text=True,capture_output=True,timeout=30)
        if stage=='export-valuation':
            assert batch.returncode==0  # 已隔离的公司失败写入批次报告。
            report=json.loads(Path(json.loads(batch.stdout)['report']).read_text())
            assert report['status_counts']=={'failed_execution':1}
            assert '底层诊断: simulated database lock conflict' in report['companies'][0]['reason']
        else:
            assert batch.returncode==1 and batch.stdout==''
            assert '底层诊断: simulated database lock conflict' in batch.stderr

    from tools.batch_valuate_alphalake import execution_error_reason
    timeout=subprocess.TimeoutExpired(['alphalake'],300,stderr='超时前诊断'.encode())
    assert '超时前诊断' in execution_error_reason(timeout)
    assert execution_error_reason(OSError('missing executable'))=='missing executable'




def test_company_entry_moutai_and_corrupt_saved_result(exports,tmp_path,monkeypatch):
    from tools.batch_valuate_alphalake import BatchPolicy
    from tools.company_valuation import company_valuation, summarize_run
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    data=exports['600519']
    company=dict(instrument_id=data['facts'][0]['instrument_id'],name='贵州茅台',symbols=['sh600519'],exchange_mic='XSHG',
                 financial_status='financial_core_complete_requires_policy',missing_core_fields=[],industry_memberships=[])
    scan=dict(contract_version='alphalake-readiness-v1',report_period=data['report_period'],information_as_of=data['information_as_of'],
              universe_scope='real financial sample',universe_count=1,companies=[company])
    policy=BatchPolicy(policy_version='moutai-reviewed-v1',review_note='reviewed company policy',assignments={'600519':dict(
        policy=json.loads((REPO/'valuation/examples/moutai-2026H1-central.json').read_text()))})
    result=company_valuation(scan,'600519',[policy],lambda _:data,tmp_path)
    assert round(result['valuation']['value_per_share']['value'],2)==1269.02
    run_id=result['valuation']['run_id']
    row=dict(run_id=run_id,status=result['status'],value_per_share=result['valuation']['value_per_share']['value'])
    p=tmp_path/(run_id+'.json');saved=json.loads(p.read_text());saved['request']['policy']['scenario']='tampered'
    p.write_text(json.dumps(saved))
    with pytest.raises(ValueError,match='differs'):summarize_run(row,tmp_path)


def test_compare_runs_replays_and_limits_attribution(exports,tmp_path,monkeypatch):
    from api.alphalake import evaluate
    from tools.compare_valuations import compare_runs,load_run,changes
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    p=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    def compute(policy):
        r=evaluate(AlphaLakeRequest(data=exports['300866'],policy=policy))
        return load_run(tmp_path,r['run_id'])[0]
    before=compute(p);after=compute(p|dict(wacc=.09))
    stored={f.name:f.read_bytes() for f in tmp_path.glob('*.json')}
    result=compare_runs(before,after)
    assert result['attribution']['status']=='verified_wacc_only'
    near(float(result['attribution']['wacc_contribution_per_share']),after['report']['final']['value_per_share']-before['report']['final']['value_per_share'])
    assert result['derived']['forecast_unchanged']
    assert result['verification']['normalization_changes'][0]['count']==0
    multiple=compute(p|dict(wacc=.09,tax_rate=.2))
    assert compare_runs(before,multiple)['attribution']['wacc_contribution_per_share'] is None
    assert float(compare_runs(before,before)['value_difference']['after_minus_before'])==0
    altered=copy.deepcopy(after);altered['report']['final']['value_per_share']+=.01
    with pytest.raises(ValueError,match='reproduce'):compare_runs(before,altered)
    # 历史规范化字段不同须显式披露；即使价格仍可重现也不能声称单因素。
    normalized=copy.deepcopy(after);normalized['inputs']['prepared_ttm']['provenance']['legacy']='test'
    result=compare_runs(before,normalized,max_changes=1)
    assert result['verification']['normalization_changes'][1]['count']==1
    assert result['attribution']['status']=='not_attributed'
    altered=copy.deepcopy(after);altered['request']['data']['code']='600519'
    with pytest.raises(ValueError,match='different security'):compare_runs(before,altered)
    diff=list(changes({'a':None,'b':[1,2]}, {'b':[1,3],'c':None}))
    assert diff[0]['before_present'] and not diff[0]['after_present'] and diff[0]['before'] is None
    assert diff[1]['path']=='/b/1'
    for filename,raw in stored.items():assert (tmp_path/filename).read_bytes()==raw
    with pytest.raises(ValueError,match='64 lowercase'):load_run(tmp_path,'../not-a-run')
    corrupt=tmp_path/(after['run_id']+'.json');body=json.loads(corrupt.read_text());body['request']['policy']['wacc']=.08;corrupt.write_text(json.dumps(body))
    with pytest.raises(ValueError,match='hash differs'):load_run(tmp_path,after['run_id'])


def test_run_query_retains_ties_duplicates_and_corruption(exports,tmp_path,monkeypatch):
    from api.alphalake import evaluate
    from tools.list_valuation_runs import list_runs
    from tools.compare_valuations import load_run,compare_runs
    first=tmp_path/'first';second=tmp_path/'second';first.mkdir();second.mkdir()
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(first))
    p=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_text())
    a=evaluate(AlphaLakeRequest(data=exports['300866'],policy=p))
    b=evaluate(AlphaLakeRequest(data=exports['300866'],policy=p|dict(wacc=.09)))
    revised=json.loads((REPO/'valuation/examples/anker-2026H1-revised.json').read_text())
    c=evaluate(AlphaLakeRequest(data=exports['300866'],policy=revised))
    snapshot={f.name:f.read_bytes() for f in first.iterdir()}
    # 修改mtime不改变信息截止下的并列；不运行引擎便能查询既有结果。
    os.utime(first/(a['run_id']+'.json'),(1,1))
    (second/(a['run_id']+'.json')).write_bytes(snapshot[a['run_id']+'.json'])
    query=list_runs([first,second],'300866',latest_per_model=True,limit=1)
    assert query['status']=='ok' and query['matched_count']==query['result_count']==3
    assert query['has_more'] and len(query['results'])==1
    groups={g['policy_id']:g for g in query['model_groups']}
    assert groups[p['policy_id']]['latest_count']==2 and groups[p['policy_id']]['unique_latest_run_id'] is None
    assert groups[revised['policy_id']]['unique_latest_run_id']==c['run_id']
    all_rows=list_runs([first,second],'300866',limit=50)['results']
    assert len(next(r for r in all_rows if r['run_id']==a['run_id'])['locations'])==2
    assert list_runs([first],'300866',models=[revised['policy_id']])['result_count']==1
    assert list_runs([first],'300866',as_of='2020-01-01T00:00:00Z')['status']=='no_matches'
    assert list_runs([first],'300866',period='2025-12-31')['status']=='no_matches'
    assert list_runs([first],'600519')['status']=='no_matches'
    with pytest.raises(ValueError):list_runs([first],'300866',as_of='2026-09-10')
    # 同run ID不同报告内容不能任意保留一份；所有唯一最新选择同时失效。
    bad=json.loads(snapshot[a['run_id']+'.json']);bad['report']['final']['value_per_share']+=1
    (second/(a['run_id']+'.json')).write_text(json.dumps(bad))
    partial=list_runs([first,second],'300866',latest_per_model=True)
    assert partial['status']=='partial' and not partial['latest_selection_complete']
    assert a['run_id'] not in {r['run_id'] for r in partial['results']}
    assert all(g['unique_latest_run_id'] is None for g in partial['model_groups'])
    (second/'broken.json').write_text('{')
    assert len(list_runs([first,second],'300866')['issues'])==2
    assert list_runs([first,tmp_path/'absent'],'300866')['status']=='partial'
    # 从查询结果取ID可直接进入既有比较链，无硬编码测试run ID。
    found=list_runs([first],'300866')['results']
    ids={r['value_per_share']['value']:r['run_id'] for r in found if r['policy_id']==p['policy_id']}
    left=load_run(first,ids[a['report']['final']['value_per_share']])[0]
    right=load_run(first,ids[b['report']['final']['value_per_share']])[0]
    assert compare_runs(left,right)['attribution']['status']=='verified_wacc_only'
    assert {f.name:f.read_bytes() for f in first.iterdir()}==snapshot
    from datetime import datetime,timedelta,timezone
    later=copy.deepcopy(exports['300866'])
    at=datetime.fromisoformat(later['information_as_of'])+timedelta(hours=1)
    later['information_as_of']=at.isoformat()
    newer=evaluate(AlphaLakeRequest(data=later,policy=p))
    latest=list_runs([first],'300866',models=[p['policy_id']],latest_per_model=True)
    assert latest['result_count']==1 and latest['results'][0]['run_id']==newer['run_id']
    later['information_as_of']=at.astimezone(timezone(timedelta(hours=8))).isoformat()
    evaluate(AlphaLakeRequest(data=later,policy=p))
    same_instant=list_runs([first],'300866',models=[p['policy_id']],latest_per_model=True)
    assert same_instant['result_count']==2 and same_instant['model_groups'][0]['unique_latest_run_id'] is None


@pytest.mark.parametrize('code',['300866','600519'])
def test_first_year_calibration_standard_chain(exports,tmp_path,monkeypatch,code):
    from api.alphalake import evaluate
    from tools.batch_valuate_alphalake import Assignment
    from tools.prepare_forecast_calibration import prepare
    root=REPO/'valuation/research/tdx-growth-expanded'
    calibration=json.loads((root/'calibration-2026H1.json').read_bytes())
    rebuilt=prepare((root/'protocol-v7.json').read_bytes(),(root/'snapshot-v7.json').read_bytes(),(root/'holdout-v7-summary.json').read_bytes(),
                    '2026-06-30',calibration['approved_codes'],calibration['prepared_at'])
    assert rebuilt.model_dump(mode='json')==calibration
    policy=json.loads((REPO/'valuation/examples/nonfinancial-history-template.json').read_bytes())
    corrected=policy|dict(policy_id='nonfinancial-history-fcff-calibrated-v1',calibration=calibration)
    assert Assignment(policy=corrected).policy.calibration.multiplier==rebuilt.multiplier
    data=copy.deepcopy(exports[code]);data['information_as_of']=calibration['prepared_at']
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path))
    if code=='600519':
        corrected['calibration']['approved_codes'].append(code)
        for candidate in (policy,corrected):
            with pytest.raises(ValueError,match='financial operations require separate model'):
                evaluate(AlphaLakeRequest(data=data,policy=candidate))
        assert not list(tmp_path.glob('*.json'))
        return
    base=evaluate(AlphaLakeRequest(data=data,policy=policy))
    with TestClient(app) as client:
        response=client.post('/api/valuation/from-alphalake',json=dict(data=data,policy=corrected))
        assert response.status_code==200,response.text
        result=response.json()
        assert client.post('/api/valuation/from-alphalake',json=dict(data=data,policy=corrected)).json()==result
    assert result['request']['data']==base['request']['data']
    before=base['inputs']['valuation_assumptions']['annual_forecast'];after=result['inputs']['valuation_assumptions']['annual_forecast']
    assert before[1:]==after[1:]
    assert before[0]['growth']==after[0]['growth'] and before[0]['tax']==after[0]['tax']
    near(after[0]['margin'],before[0]['margin']*rebuilt.multiplier)
    assert result['inputs']['equity_bridge']==base['inputs']['equity_bridge']
    left=base['report'];right=result['report']
    assert left['cost_of_capital']==right['cost_of_capital']
    for key in ('revenue_projections','reinvestment_projections'):
        assert left['dcf'][key]==right['dcf'][key]
    for key in ('ebit_projections','fcff_projections'):
        assert left['dcf'][key][1:]==right['dcf'][key][1:]
    delta=(right['dcf']['ebit_projections'][0]-left['dcf']['ebit_projections'][0])*(1-before[0]['tax'])/(1+left['cost_of_capital']['wacc'])
    near(right['dcf']['value_of_operating_assets']-left['dcf']['value_of_operating_assets'],delta)
    near(right['final']['value_per_share']-left['final']['value_per_share'],delta/result['inputs']['equity_bridge']['shares'])
    assert result['run_id']!=base['run_id'] and len(list(tmp_path.glob('*.json')))==2
    for change in ('coefficient','horizon','weight','unapproved','future','period','base_rule','old_version'):
        bad=copy.deepcopy(corrected)
        if change=='coefficient':bad['calibration']['scale']+=.01
        elif change=='horizon':bad['calibration']['forecast_year']=5
        elif change=='weight':bad['calibration']['weight']=1
        elif change=='unapproved':bad['calibration']['approved_codes']=['999999']
        elif change=='future':bad['calibration']['prepared_at']='2027-01-01T00:00:00Z'
        elif change=='period':bad['approved_report_period']='2025-06-30'
        elif change=='base_rule':bad['margin_shift']=.01
        else:bad['policy_id']='nonfinancial-history-fcff-v1'
        with pytest.raises(ValueError):evaluate(AlphaLakeRequest(data=data,policy=bad))
    assert len(list(tmp_path.glob('*.json')))==2
