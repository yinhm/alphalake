"""起点前对数增长校准；共享原预测及研究税率/资本效率，不回写DCF。"""
import argparse
from collections import Counter
import hashlib
import json
import math
from statistics import linear_regression
from pathlib import Path

from tools.backtest_tdx_error_bands import baseline, load_inputs
from tools.backtest_tdx_history import error
from tools.backtest_tdx_ttm_yoy import metrics

MODELS = ('current_rule', 'past_log_calibration', 'zero_growth')


def study(p, source, sample_protocol, design):
    if p['protocol_id'] != 'tdx-past-log-growth-calibration-v1' or tuple(p['models']) != MODELS or p['minimum_training_pairs'] != 80:
        raise ValueError('unsupported growth calibration')
    policy = sample_protocol['base_policy']; rows = []; fits = {}
    for origin in design['origins']:
        year = int(origin[:4]); cutoff = f'{year}-09-01T00:00:00+08:00'
        training = baseline(design,source,policy,'calibration',f'{year-1}-06-30',cutoff)
        pairs = [dict(code=r['code'], x=math.log(r['forecast']['revenue']/r['base']['revenue']),
                      y=math.log(r['actual']['revenue']/r['base']['revenue']))
                 for r in training if r['status']=='evaluated']
        fit = dict(origin=f'{year-1}-06-30',target=origin,cutoff=cutoff,status='blocked',pairs=pairs,
                   statuses=dict(Counter(r['status'] for r in training)),
                   refusals=[dict(code=r['code'],reason=r.get('reason')) for r in training if r['status']!='evaluated'])
        fits[origin] = fit
        try:
            if len(pairs)<p['minimum_training_pairs']:
                raise ValueError('insufficient training pairs')
            slope,intercept = linear_regression([r['x'] for r in pairs],[r['y'] for r in pairs])
            if not math.isfinite(slope) or not math.isfinite(intercept):
                raise ValueError('nonfinite fitted coefficient')
            fit.update(status='fitted',slope=slope,intercept=intercept)
        except (ValueError,ArithmeticError) as exc:
            fit['reason'] = str(exc)
        for old in baseline(design,source,policy,'evaluation',origin,design['evaluation_as_of']):
            row = dict(code=old['code'],origin=origin,status=old['status'],baseline_status=old['status'],
                       reason=old.get('reason'),profit_scope=old.get('profit_scope'),
                       financial_scope_flags=old.get('financial_scope_flags'),actual_fcff=None)
            rows.append(row)
            if 'forecast' not in old:
                continue
            base = old['base']; row.update(base=base,forecasts=dict(current_rule=old['forecast'],zero_growth={k:base[k] for k in ('revenue','ebit')}))
            if fit['status'] != 'fitted':
                row['status']='blocked_calibration'
                continue
            try:
                x = math.log(old['forecast']['revenue']/base['revenue'])
                raw = math.expm1(fit['intercept']+fit['slope']*x)
                growth = max(policy['growth_floor'],min(policy['growth_ceiling'],raw))
                revenue = base['revenue']*(1+growth)
                row['forecasts']['past_log_calibration'] = dict(revenue=revenue,ebit=revenue*base['ebit']/base['revenue'])
                row.update(raw_growth=raw,growth=growth)
                row['policy_cash'] = {}
                for model,f in row['forecasts'].items():
                    nopat = f['ebit']*(1-policy['tax_rate'])
                    reinvestment = (f['revenue']-base['revenue'])/policy['sales_to_capital']
                    row['policy_cash'][model] = dict(nopat=nopat,reinvestment=reinvestment,fcff=nopat-reinvestment)
                if old['status']=='evaluated':
                    row.update(actual=old['actual'],errors={m:error(f,old['actual']) for m,f in row['forecasts'].items()})
            except (ValueError,ArithmeticError) as exc:
                row.update(status='blocked_candidate',reason=str(exc))
    summary = metrics(rows,MODELS)
    by_origin = {o:metrics([r for r in rows if r['origin']==o],MODELS) for o in design['origins']}
    b,c,z = (summary['models'][m] for m in MODELS); g = p['gates']
    def nonworse(a,b,key,ratio=1):
        return a[key] is not None and b[key] is not None and a[key]<=b[key]*ratio
    checks = dict(minimum_pairs=c['revenue_n']>=g['minimum_pairs'],
                  each_origin_minimum=all(s['models'][MODELS[1]]['revenue_n']>=g['minimum_each_origin'] for s in by_origin.values()),
                  primary_improvement=(b['revenue_mae_pct'] or 0)>0 and nonworse(c,b,'revenue_mae_pct',1-g['minimum_revenue_mae_improvement']),
                  revenue_wape=nonworse(c,b,'revenue_wape_pct'),
                  zero_growth=all(nonworse(c,z,k) for k in ('revenue_mae_pct','revenue_wape_pct')),
                  ebit_mae=nonworse(c,b,'ebit_mae_pct_actual_revenue'),
                  each_origin=all(nonworse(s['models'][MODELS[1]],s['models'][MODELS[0]],'revenue_mae_pct',g['maximum_each_origin_mae_ratio']) for s in by_origin.values()))
    return dict(fits=fits,summary=summary,by_origin=by_origin,results=rows,decision=dict(passed=all(checks.values()),checks=checks),
                amount_unit='million_CNY',boundary=p['scope']+'; '+p['cash_bridge'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('protocol',type=Path)
    args = parser.parse_args(); p,inputs = load_inputs(args.protocol)
    result = study(p,**inputs)
    result['protocol_sha256'] = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
