"""从保存请求的标准窗口、附注和政策独立复算安克修正模型，不调用引擎。"""
import json
from decimal import Decimal as D
from pathlib import Path
import sys
from verify_market_wacc import verify as verify_wacc


def verify(path):
    run=json.loads(Path(path).read_text());req=run['request'];policy=req['policy'];p=policy['parameters']
    assert policy['policy_id']=='anker-consolidated-v2' and req['data']['code']=='300866'
    period=req['data']['report_period']
    w={r['field']:D(r['value'])/1000000 for r in req['data']['windows'] if r['value'] is not None}
    notes={r['item']:D(r['value'])/(1 if r['unit']=='CNY/share' else 1000000) for r in req['data']['supplements'] if r['period']==period}
    binding=req.get('wacc_binding')
    wacc=D(str(run['report']['cost_of_capital']['wacc'])) if binding else D(str(p['wacc']))
    book=(w['FN41']+w['FN55']+w['FN56']+w['FN439']+sum(notes[k] for k in ['current_loans','current_bonds','current_leases']))*D(str(p['debt_multiple']))
    bond=(w['FN56']+notes['current_bonds'])*D(str(p['debt_multiple']))
    if policy['debt_basis']=='wacc_estimate':
        assert binding
        kd=D(str(run['report']['cost_of_capital']['cost_of_debt_pretax']))
        def pv(key):
            return sum(notes[f'debt_cf_{key}_{bucket}']/(1+kd)**year for bucket,year in [('0_1',0),('1_2',1),('2_5',2),('5_plus',5)])
        book=sum(pv(key) for key in ('short','long','lease','bond'));bond=pv('bond')
    components=dict(
        excess_cash=w['FN133']+(w['FN8']-w['FN133']-notes['extra_restricted_cash'])*D(str(p['cash_other_recovery']))-w['FN230']*D(str(p['operating_cash_ratio'])),
        financial_assets_after_haircut=notes['extra_current_financial_debt']+notes['extra_noncurrent_financial_debt']+notes['deposits']+(notes['extra_current_financial_equity']+notes['extra_noncurrent_financial_equity']+w['FN25']+notes['loan_receivable']-notes['loan_allowance'])*D(str(p['investment_recovery'])),
        debt_claim_proxy=-book,minority_claim_proxy=-w['FN97']*D(str(p['minority_earnings_multiple'])),
        convertible_option_book_proxy=-w['FN299'],existing_other_claims=-sum(notes[k] for k in ['income_tax_payable','repurchase_payable','capex_payable','ipo_payable'])-w['FN59'])
    shares=w['FN238']
    if policy['capital_basis']=='disclosed_share_scenario':
        market=binding['market_capital'];mp=binding['policy']['market']
        shares=sum(D(r['value']) for r in market['share_counts'] if r['share_basis']=='outstanding')/1000000
        components['post_report_funding_cash_scenario']=sum(D(r['net_proceeds']) for r in market['funding_events'])*D(market['fx']['value'])*D(str(mp['funding_cash_retention']))/1000000
    shares*=1+D(str(p['extra_dilution_rate']))
    release=bond+w['FN299'];dilution=notes['convertible_face']/notes['extra_conversion_price']
    def near(actual,expected):
        assert abs(D(str(actual))-expected)<=max(D('1e-7'),abs(expected)*D('1e-11')),(actual,expected)
    bridge=run['inputs']['equity_bridge']
    assert set(bridge['components'])==set(components)
    for key,value in components.items(): near(bridge['components'][key],value)
    near(bridge['shares'],shares);near(bridge['conversion_release'],release);near(bridge['conversion_shares'],dilution)
    if binding: wacc=D(verify_wacc(path)['wacc'])
    rev=w['FN230'];total=D(0)
    forecast=policy['annual_forecast'];assert len(forecast)==10
    for year,row in enumerate(forecast,1):
        previous=rev;rev*=1+D(str(row['growth']))
        fcff=rev*D(str(row['margin']))*(1-D(str(row['tax'])))-(rev-previous)/D(str(p['sales_to_capital']))
        pv=fcff/(1+wacc)**year;total+=pv
        near(run['report']['dcf']['revenue_projections'][year-1],rev)
        near(run['report']['dcf']['fcff_projections'][year-1],fcff)
        near(run['report']['dcf']['pv_fcff'][year-1],pv)
    g=D(str(p['terminal_growth']));assert 0<=g<wacc
    tv=rev*(1+g)*D(str(forecast[-1]['margin']))*(1-D(str(forecast[-1]['tax'])))*(1-g/D(str(p['terminal_roic'])))/(wacc-g)/(1+wacc)**10
    ev=total+tv;equity=ev+sum(components.values())
    plain=equity/shares;converted=(equity+release)/(shares+dilution);price=min(plain,converted)
    near(run['report']['dcf']['value_of_operating_assets'],ev)
    near(run['report']['final']['value_per_share'],price)
    assert run['report']['cashflow']['fcff'] is None
    return dict(run_id=run['run_id'],capital_basis=policy['capital_basis'],debt_basis=policy['debt_basis'],wacc=str(wacc),
        per_share_cny=str(price),no_conversion_cny=str(plain),full_conversion_proxy_cny=str(converted),
        operating_value_million_cny=str(ev),pv_terminal_fraction=str(tv/ev),shares_million=str(shares))


if __name__=='__main__':
    if len(sys.argv)<2: raise SystemExit('usage: verify_revised_valuation.py saved-api-run.json [...]')
    print(json.dumps([verify(p) for p in sys.argv[1:]],ensure_ascii=False,indent=2))
