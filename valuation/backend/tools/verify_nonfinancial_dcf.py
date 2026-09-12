"""从标准窗口及显式政策独立十进制复算通用DCF；不导入估值引擎。

限十年、零滞后、固定WACC、不转股账面桥接。支持固定或行业/目标权重
参考WACC（显式债务成本/信用档），不覆盖专项、市场权重、校准及困境模型。
生产校验负责身份/时点/源语义；本工具负责数值复算，不取代原文核验。
"""
from datetime import date
from decimal import Decimal as D
import json
from pathlib import Path
import statistics
import sys


def near(actual, expected, label):
    actual, expected = D(str(actual)), D(str(expected))
    if not actual.is_finite() or abs(actual-expected) > max(D('1e-7'), abs(expected)*D('1e-11')):
        raise ValueError(f'{label}: {actual} != {expected}')


def verify(run):
    req = run['request']; p = req['policy']; d = req['data']; report = run['report']; dcf = report['dcf']
    if p['policy_id'] not in ('nonfinancial-history-fcff-v1', 'nonfinancial-book-fcff-v1'):
        raise ValueError('unsupported policy for independent verifier')
    facts = {r['fact_id']: r for r in d['facts']}
    w = {}
    for field in 'FN230 FN86 FN305 FN306 FN83 FN82 FN301 FN238 FN133 FN41 FN52 FN55 FN56 FN439 FN69'.split():
        rows = [r for r in d['windows'] if r['field'] == field]
        if len(rows) != 1 or rows[0]['value'] is None:
            raise ValueError('missing/ambiguous window: '+field)
        row = rows[0]
        expected = sum(D(facts[i]['value'])*D(c) for i,c in zip(row['source_fact_ids'], row['input_coefficients'], strict=True))
        near(row['value'], expected, 'standard window '+field)
        w[field] = expected / 1000000
    rev = w['FN230']; ebit = w['FN86']+w['FN305']-w['FN306']-w['FN83']-w['FN82']-w['FN301']
    margin = ebit/rev
    near(run['inputs']['prepared_ttm']['financials']['revenues'], rev, 'base revenue')
    near(report['adjusted']['adjusted_ebit'], ebit, 'base EBIT')
    if req.get('capital_binding'):
        binding = req['capital_binding']; cp = binding['policy']
        rows = [r for r in binding['references']['observations'] if r['industry'] == cp['industry']]
        if len(rows) != 1: raise ValueError('capital observation not unique')
        sc = D(rows[0]['value'])*D(str(cp['ratio_multiplier']))
    else:
        sc = D(str(p['sales_to_capital']))
    if req.get('wacc_binding'):
        binding = req['wacc_binding']; bp = binding['policy']; refs = binding['references']
        if bp['capital_structure_basis'] not in ('industry_reference_weights', 'target_weights') or bp.get('synthetic_debt'):
            raise ValueError('unsupported WACC branch for independent verifier')
        country = {(r['subject_code'],r['metric_code']):D(r['value']) for r in refs['country_risk']}
        industry = {(r['industry'],r['metric_code']):D(r['value']) for r in refs['industry_stats']}
        rf = next(D(r['value']) for r in refs['yield_curve'] if r['tenor_months'] == bp['government_tenor_months'])
        if bp['risk_free_method'] == 'subtract_cn_default_spread': rf -= country['CN','sovereign_default_spread']
        beta = sum(D(str(r['weight']))*industry[r['industry'],bp['beta_metric']] for r in bp['industries'])
        if bp['capital_structure_basis'] == 'industry_reference_weights':
            de = sum(D(str(r['weight']))*industry[r['industry'],'debt_equity_ratio'] for r in bp['industries'])
            dw = de/(1+de)
        else:
            dw = D(str(bp['target_debt_weight'])); de = dw/(1-dw)
        crp = sum(D(str(r['weight']))*D(str(r['exposure_scale']))*country[r['country'],'country_risk_premium'] for r in bp['countries'])
        if bp.get('credit_band_debt'):
            cp = bp['credit_band_debt']
            kd = rf+next(D(r['value']) for r in refs['credit_spreads'] if r['rating'] == cp['rating'])
            if cp['sovereign_spread_policy'] == 'add_cn_default_spread': kd += country['CN','sovereign_default_spread']
        else: kd = D(str(bp['debt_cost_pretax']))
        shield = D(str(bp['tax_shield_rate'])); levered = beta*(1+(1-shield)*de)
        ke = rf+levered*country['mature','mature_market_erp']+crp
        wacc = ke*(1-dw)+kd*(1-shield)*dw
        for name,value in dict(risk_free_rate=rf,beta_u=beta,beta_l=levered,weight_debt=dw,
                               cost_of_equity=ke,cost_of_debt_pretax=kd,wacc=wacc).items():
            near(report['cost_of_capital'][name], value, name)
    else: wacc = D(str(p['wacc']))
    g = D(str(p['terminal_growth'])); roic = D(str(p['terminal_roic']))
    if not 0 <= g < min(wacc,roic) or sc <= 0: raise ValueError('invalid terminal/capital policy')
    if p['policy_id'] == 'nonfinancial-history-fcff-v1':
        q = {r['period']:D(r['value']) for r in d['facts'] if r['field'] == 'FN230'}
        end = date.fromisoformat(d['report_period']); start = end.replace(year=end.year-1).isoformat()
        pairs = [v/q[f'{int(day[:4])-1}{day[4:]}']-1 for day,v in q.items()
                 if start < day <= end.isoformat() and v >= 0 and q.get(f'{int(day[:4])-1}{day[4:]}',0) > 0]
        if len(pairs) < 2: raise ValueError('insufficient YOY pairs')
        growth = max(D(str(p['growth_floor'])),min(D(str(p['growth_ceiling'])),statistics.median(pairs)+D(str(p['growth_shift']))))
        forecast = [dict(growth=growth if y<=5 else growth+(g-growth)*D(y-5)/5,
                         margin=margin+D(str(p['margin_shift']))*min(D(y)/5,1),tax=D(str(p['tax_rate']))) for y in range(1,11)]
    else: forecast = [{k:D(str(v)) for k,v in r.items()} for r in p['annual_forecast']]
    if len(forecast) != 10 or any(len(dcf[k]) != 10 for k in ('revenue_projections','ebit_projections','reinvestment_projections','fcff_projections','discount_factors','pv_fcff')):
        raise ValueError('ten forecast years required')
    total = D(0); years = []
    for y,row in enumerate(forecast,1):
        previous = rev; rev *= 1+row['growth']; earnings = rev*row['margin']
        nopat = earnings*(1-row['tax']); reinvestment = (rev-previous)/sc; fcff = nopat-reinvestment
        df = 1/(1+wacc)**y; pv = fcff*df; total += pv
        for key,value in dict(revenue_projections=rev,ebit_projections=earnings,reinvestment_projections=reinvestment,
                              fcff_projections=fcff,discount_factors=df,pv_fcff=pv).items():
            near(dcf[key][y-1], value, f'year {y} {key}')
        years.append(dict(year=y,revenue=str(rev),nopat=str(nopat),net_reinvestment=str(reinvestment),fcff=str(fcff),pv=str(pv)))
    terminal_nopat = rev*(1+g)*row['margin']*(1-row['tax'])
    tv = terminal_nopat*(1-g/roic)/(wacc-g); pv_terminal = tv/(1+wacc)**10; ev = total+pv_terminal
    components = dict(cash_recovery_scenario=w['FN133']*D(str(p['cash_recovery'])),
        operating_cash_reserve=-w['FN230']*D(str(p['operating_cash_ratio'])),
        debt_book_proxy=-sum(w[f] for f in ('FN41','FN52','FN55','FN56','FN439'))*D(str(p['debt_book_multiple'])),
        minority_book_proxy=-w['FN69']*D(str(p['minority_book_multiple'])),
        additional_claims_scenario=-D(str(p['additional_claims_million_cny'])))
    shares = w['FN238']*(1+D(str(p['extra_dilution_rate']))); equity = ev+sum(components.values()); value = equity/shares
    bridge = run['inputs']['equity_bridge']
    if set(bridge['components']) != set(components) or bridge['operating_ownership'] != 1 or bridge['conversion_release'] != 0 or bridge['conversion_shares'] != 0:
        raise ValueError('unsupported equity bridge')
    for key,amount in components.items(): near(bridge['components'][key],amount,key)
    near(bridge['shares'],shares,'diluted shares')
    for key,amount in dict(terminal_value_firm=tv,pv_terminal_value=pv_terminal,pv_cash_flows_sum=total,value_of_operating_assets=ev,value_of_equity=equity).items():
        near(dcf[key],amount,key)
    near(report['final']['value_per_share'],value,'final per share')
    if report['cashflow']['fcff'] is not None: raise ValueError('unclosed historical FCFF must remain null')
    return dict(code=d['code'],unit='million_CNY_except_per_share_CNY',wacc=str(wacc),sales_to_capital=str(sc),
                years=years,terminal_nopat=str(terminal_nopat),terminal_reinvestment=str(terminal_nopat*g/roic),
                pv_terminal=str(pv_terminal),operating_value=str(ev),bridge={k:str(v) for k,v in components.items()},
                equity_value=str(equity),shares_million=str(shares),value_per_share=str(value))


if __name__ == '__main__':
    if len(sys.argv) < 2: raise SystemExit('usage: python -m tools.verify_nonfinancial_dcf saved-run.json [...]')
    print(json.dumps([verify(json.loads(Path(p).read_text())) for p in sys.argv[1:]],ensure_ascii=False,indent=2))
