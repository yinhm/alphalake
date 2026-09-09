"""独立十进制复算已保存 API 运行的市值、经营权益、权重与 WACC；不调用引擎。"""
import json
import sys
from decimal import Decimal as D
from pathlib import Path


def verify(path):
    run=json.loads(Path(path).read_text())
    binding=run['request']['wacc_binding'];s=binding['market_capital'];p=binding['policy'];refs=binding['references']
    shares={r['trading_currency']:D(r['value']) for r in s['share_counts'] if r['share_basis']=='outstanding'}
    cap=shares['CNY']*D(s['a_quote']['quote']['close'])
    if s['code']=='300866': cap+=shares['HKD']*D(s['h_quote']['close'])*D(s['fx']['raw_value'])/100
    cap/=D(1000000)
    bridge=run['inputs']['equity_bridge'];key='debt_claim_proxy' if s['code']=='300866' else 'lease_debt'
    ownership=D(str(bridge['operating_ownership']))
    equity=(cap-sum(D(str(v)) for k,v in bridge['components'].items() if k!=key))/ownership
    debt=-D(str(bridge['components'][key]))/ownership
    assert run['request']['policy']['parameters'].get('debt_multiple',1)==1,'verifier supports disclosed-book proxy only'
    country={(r['subject_code'],r['metric_code']):D(r['value']) for r in refs['country_risk']}
    rf=next(D(r['value']) for r in refs['yield_curve'] if r['tenor_months']==p['government_tenor_months'])
    if p['risk_free_method']=='subtract_cn_default_spread': rf-=country['CN','sovereign_default_spread']
    beta=sum(D(str(i['weight']))*next(D(r['value']) for r in refs['industry_stats'] if r['industry']==i['industry'] and r['metric_code']==p['beta_metric']) for i in p['industries'])
    crp=sum(D(str(i['weight']))*D(str(i['exposure_scale']))*country[i['country'],'country_risk_premium'] for i in p['countries'])
    if p.get('synthetic_debt'):
        info=run['audit']['wacc_reference']['synthetic_debt']
        coverage=D(str(info['ebit_million_cny']))/D(str(info['gross_interest_million_cny']))
        selected=[r for r in refs['credit_spreads'] if D(r['coverage_lower'])<coverage<=D(r['coverage_upper'])]
        assert len(selected)==1
        policy=p['synthetic_debt']
    else:
        policy=p['credit_band_debt'];selected=[r for r in refs['credit_spreads'] if r['rating']==policy['rating']]
        assert len(selected)==1
    kd=rf+D(selected[0]['value'])
    if policy['sovereign_spread_policy']=='add_cn_default_spread': kd+=country['CN','sovereign_default_spread']
    if p['market']['debt_value_basis']=='contractual_cashflow_pv_upper_bound':
        notes={r['item']:D(r['value'])/1000000 for r in run['request']['data']['supplements'] if r['period']==p['report_period']}
        debt=sum(notes[f'debt_cf_{key}_{bucket}']/(1+kd)**year
            for key in ('short','long','lease','bond')
            for bucket,year in [('0_1',0),('1_2',1),('2_5',2),('5_plus',5)])
    tax=D(str(p['tax_shield_rate']));weight=debt/(debt+equity)
    leveraged=beta*(1+(1-tax)*debt/equity)
    ke=rf+leveraged*country['mature','mature_market_erp']+crp
    wacc=(1-weight)*ke+weight*kd*(1-tax)
    values=dict(mv_equity=equity,mv_debt_total=debt,weight_debt=weight,beta_l=leveraged,cost_of_equity=ke,cost_of_debt_pretax=kd,wacc=wacc)
    for k,v in values.items():
        actual=D(str(run['report']['cost_of_capital'][k]))
        assert abs(actual-v)<=max(D('1e-12'),abs(v)*D('1e-12')),(k,actual,v)
    assert run['report']['cashflow']['fcff'] is None
    return dict(code=s['code'],market_date=s['market_date'],common_equity_million_cny=str(cap),**{k:str(v) for k,v in values.items()})


if __name__=='__main__':
    if len(sys.argv)<2: raise SystemExit('usage: verify_market_wacc.py saved-api-run.json [...]')
    print(json.dumps([verify(p) for p in sys.argv[1:]],ensure_ascii=False,indent=2))
