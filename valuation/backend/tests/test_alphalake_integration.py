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
