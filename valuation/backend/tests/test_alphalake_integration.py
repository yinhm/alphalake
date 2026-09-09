"""真实 Go 数据库导出 → HTTP → 完整引擎 → 原生桥接与可复现记录。"""
import copy
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import sys

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
    assert not calls
    ambiguous=policy.model_copy(deep=True);ambiguous.industry_rules.append(policy.industry_rules[0].model_copy(update={'rule_id':'other'}))
    assert run_batch(scan,ambiguous,export)['companies'][0]['status']=='blocked_ambiguous_industry_policy'
    assert not calls
    from tools.batch_valuate_alphalake import Assignment
    ambiguous.assignments['300866']=Assignment(policy=profile)
    assert run_batch(scan,ambiguous,export)['companies'][0]['policy_route']=={'kind':'explicit_company_assignment'}
    broken=copy.deepcopy(scan);broken['companies'][0]['industry_memberships'][0]['observed_at']='bad-date'
    assert run_batch(broken,policy,export)['companies'][0]['status']=='rejected_industry_evidence'


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
