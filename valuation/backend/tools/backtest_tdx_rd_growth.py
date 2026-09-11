"""冻结的研发净投入收入选择开发复验；研发不是总再投资。"""
import argparse
from collections import Counter
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_history import value
from tools.backtest_tdx_revenue_cagr import metrics

ROOT = Path(__file__).resolve().parents[3]
BASE = 'short_quarter_yoy_median_revenue_only'
ZERO = 'zero_growth'
PRIMARY = 'rd_net_nonpositive_zero_5yr'
SENSITIVITY = 'rd_net_nonpositive_zero_3yr'
MODELS = (BASE, PRIMARY, ZERO, SENSITIVITY)
PROTOCOL_SHA = 'c4ed7361e74548c73704f7b75e87c8046c3a11efcce649ed2cb864636a2c2bb8'


def signal(row, life):
    inputs = row['annual_inputs'][:life+1]
    if len(inputs) != life+1:
        raise ValueError('RD input slots missing')
    year = int(row['origin'][:4])-1
    if [r['period'] for r in inputs] != [f'{year-j}-12-31' for j in range(life+1)]:
        raise ValueError('RD annual periods differ')
    if any(r['status'] != 'positive_source' for r in inputs):
        return dict(group='rd_history_unavailable',net_rd_cny=None,selected=BASE)
    amounts = [value({'bits':{'FN304':r['bits']}},'FN304') for r in inputs]
    if any(n <= 0 or n != Decimal(r['value_cny']) for n,r in zip(amounts,inputs)):
        raise ValueError('RD source bits/value differ')
    scaled = life*amounts[0]-sum(amounts[1:])
    return dict(group='nonpositive_net_rd' if scaled <= 0 else 'positive_net_rd',
                net_rd_cny=str(scaled/life),selected=ZERO if scaled <= 0 else BASE)


def study(p, parent, revenues, rd):
    key = lambda r:(r['code'],r['origin'])
    forecasts = {key(r):r for r in revenues['results']}
    histories = {key(r):r for r in rd['results']}
    expected = {(s['code'],o) for s in parent['samples'] for o in p['origins']}
    if len(forecasts) != len(revenues['results']) or len(histories) != len(rd['results']) or forecasts.keys() != expected or histories.keys() != expected:
        raise ValueError('duplicate or missing sample/origin')
    rows = []
    # Only forecasts and origin RD inputs are read in this pass; no target outcomes.
    for k,f in forecasts.items():
        h = histories[k]
        if h['information_as_of'] != f['forecast_as_of']:
            raise ValueError('RD/revenue cutoff differs')
        choices = {PRIMARY:signal(h,5),SENSITIVITY:signal(h,3)}
        predictions = {m:f['forecasts'][m] for m in (BASE,ZERO) if m in f['forecasts']}
        if BASE in predictions:
            if ZERO not in predictions:
                raise ValueError('baseline available without current revenue')
            predictions.update({m:predictions[c['selected']] for m,c in choices.items()})
        rows.append(dict(code=f['code'],origin=f['origin'],forecast_as_of=f['forecast_as_of'],target=f['target'],
                         profit_group=f['profit_group'],choices=choices,forecasts=predictions,rd_source_inputs=h['annual_inputs'],
                         revenue_evidence=f['evidence'],forecast_issues=f.get('forecast_issues',{}),actual_fcff=None))
    for r in rows:
        f = forecasts[key(r)]
        r['errors'] = {}
        if 'actual' in f:
            r['actual'] = f['actual']
        if 'actual_issue' not in f and f.get('actual',{}).get('value',0) > 0:
            r['errors'] = {m:v-f['actual']['value'] for m,v in r['forecasts'].items()}
        else:
            r['actual_issue'] = f.get('actual_issue','missing_or_nonpositive_actual')
        r['status'] = ('baseline_blocked' if BASE not in r['forecasts'] else
                       'actual_blocked' if BASE not in r['errors'] else 'evaluated')
    common = [r for r in rows if r['status']=='evaluated']
    summary = {m:metrics(common,m) for m in MODELS}
    by_origin = {o:{m:metrics([r for r in common if r['origin']==o],m) for m in MODELS} for o in p['origins']}
    independent = {m:metrics(rows,m) for m in MODELS}
    companies = {r['code'] for r in common}
    b,c,z = (summary[m] for m in (BASE,PRIMARY,ZERO));g = p['gates']
    def nonworse(a,b,key='mae_pct',ratio=1):
        return a[key] is not None and b[key] is not None and a[key] <= b[key]*ratio
    # ponytail: fixed 600-company panel; aggregate errors if the panel grows materially.
    checks = dict(minimum_pairs=len(common)>=g['minimum_pairs'],minimum_companies=len(companies)>=g['minimum_companies'],
        each_origin_minimum=all(v[PRIMARY]['n']>=g['minimum_each_origin_pairs'] for v in by_origin.values()),
        coverage_retention=independent[BASE]['n']>0 and len(common)/independent[BASE]['n']>=g['minimum_baseline_retention'],
        primary_improvement=b['mae_pct'] is not None and b['mae_pct']>0 and nonworse(c,b,ratio=1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(c,b,'wape_pct'),zero_growth_nonworse=all(nonworse(c,z,k) for k in ('mae_pct','wape_pct')),
        each_origin_nonworse=all(nonworse(v[PRIMARY],v[BASE],ratio=g['maximum_each_origin_mae_ratio']) for v in by_origin.values()),
        leave_one_company_out_nonworse=bool(companies) and all(nonworse(metrics([r for r in common if r['code']!=code],PRIMARY),metrics([r for r in common if r['code']!=code],BASE)) for code in companies))
    groups = {m:{group:dict(candidates=sum(r['choices'][m]['group']==group for r in rows),
        metrics={x:metrics([r for r in common if r['choices'][m]['group']==group],x) for x in MODELS})
        for group in ('positive_net_rd','nonpositive_net_rd','rd_history_unavailable')} for m in (PRIMARY,SENSITIVITY)}
    return dict(protocol_id=p['protocol_id'],candidates=len(rows),common_companies=len(companies),summary=summary,independent=independent,
        statuses=dict(Counter(r['status'] for r in rows)),by_origin=by_origin,by_rd_group=groups,
        forecast_changes={m:sum(r['forecasts'][m]!=r['forecasts'][BASE] for r in common) for m in (PRIMARY,SENSITIVITY)},
        by_profit_group={group:{m:metrics([r for r in common if r['profit_group']==group],m) for m in MODELS} for group in ('positive','nonpositive','unknown')},
        decision=dict(passed=all(checks.values()),checks=checks,sensitivity_adoption=False),results=rows,boundary=p['boundary'])


def load_inputs(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('frozen protocol hash differs')
    p = json.loads(raw); inputs = []
    for name in ('parent_protocol','revenue_results','rd_results'):
        ref = p['inputs'][name];data = (ROOT/ref['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError(name+' hash differs')
        inputs.append(json.loads(gzip.decompress(data) if ref['path'].endswith('.gz') else data))
    return p,*inputs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);args=parser.parse_args()
    result = study(*load_inputs(args.protocol))
    result['evidence'] = dict(protocol_sha256=PROTOCOL_SHA,tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
