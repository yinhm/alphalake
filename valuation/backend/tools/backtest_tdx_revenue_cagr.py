"""冻结的一年收入比较；利润只作分组，不参与收入准入。"""
import argparse
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean, median

from tools.backtest_tdx_history import at, available, quarter_periods
from tools.tdx_research_source import financial_value, source_field

ROOT = Path(__file__).resolve().parents[3]
MODELS = ('short_quarter_yoy_median_revenue_only', 'three_year_ttm_cagr', 'zero_growth')
PROTOCOL_SHA = '9604ee4003117b454ed8fdc2606311117e214dab18d7bc747c1564d280f3a32d'


def quarter(index, artifacts, code, period, cutoff):
    matches = index.get((code, period), [])
    out = dict(period=period, field=source_field('revenue'), information_as_of=cutoff, status='missing')
    if not matches:
        return out
    if len(matches) != 1:
        raise ValueError('duplicate source: ' + period)
    r = matches[0]; a = artifacts[r['artifact']]
    if a['report_period'] != period:
        raise ValueError('artifact period differs: ' + period)
    out.update(artifact=r['artifact'])
    if available(r, a) > at(cutoff):
        return out | dict(status='unavailable_at_cutoff')
    return out | dict(status='available', bits=r['bits'][source_field('revenue')], value_cny=str(financial_value(r, 'revenue')))


def revenue(index, artifacts, code, end, cutoff):
    refs = [quarter(index, artifacts, code, p, cutoff) for p in quarter_periods(end)]
    if any(r['status'] != 'available' for r in refs):
        raise ValueError('incomplete revenue TTM: ' + str([(r['period'], r['status']) for r in refs if r['status'] != 'available']))
    return dict(value=float(sum(Decimal(r['value_cny']) for r in refs)/1000000), source_inputs=refs)


def predictions(p, index, artifacts, code, end, cutoff):
    forecasts = {}; issues = {}; evidence = {}
    try:
        current = revenue(index, artifacts, code, end, cutoff)
        evidence['current'] = current
        if current['value'] <= 0:
            raise ValueError('positive current revenue required')
        forecasts[MODELS[2]] = current['value']
    except (ValueError, KeyError, ArithmeticError) as exc:
        return forecasts, {m: str(exc) for m in MODELS}, evidence
    clip = lambda g: max(p['growth_floor'], min(p['growth_ceiling'], g))
    try:
        pairs = []; prior_refs = []
        for r in current['source_inputs']:
            previous = quarter(index, artifacts, code, date.fromisoformat(r['period']).replace(year=int(r['period'][:4])-1).isoformat(), cutoff)
            prior_refs.append(previous)
            if previous['status'] == 'available' and Decimal(previous['value_cny']) > 0 and Decimal(r['value_cny']) >= 0:
                pairs.append(float(Decimal(r['value_cny'])/Decimal(previous['value_cny'])-1))
        evidence['short_history'] = prior_refs
        if len(pairs) < 2:
            raise ValueError('at least two same-quarter revenue pairs required')
        g = clip(median(pairs))
        evidence['short_growth'] = g
        forecasts[MODELS[0]] = current['value']*(1+g)
    except (ValueError, KeyError, ArithmeticError) as exc:
        issues[MODELS[0]] = str(exc)
    try:
        history = [current]
        evidence['cagr_history'] = history
        for years in (1, 2, 3):
            observation = revenue(index, artifacts, code, end.replace(year=end.year-years), cutoff)
            history.append(observation)
            if observation['value'] <= 0:
                raise ValueError('positive historical revenue required')
        g = clip((current['value']/history[3]['value'])**(1/3)-1)
        evidence['cagr_growth'] = g
        forecasts[MODELS[1]] = current['value']*(1+g)
    except (ValueError, KeyError, ArithmeticError) as exc:
        issues[MODELS[1]] = str(exc)
    return forecasts, issues, evidence


def metrics(rows, model):
    selected = [r for r in rows if model in r.get('errors', {})]
    return dict(n=len(selected), mae_pct=mean(abs(r['errors'][model])*100/r['actual']['value'] for r in selected) if selected else None,
        wape_pct=100*sum(abs(r['errors'][model]) for r in selected)/sum(r['actual']['value'] for r in selected) if selected else None,
        bias_pct=mean(r['errors'][model]*100/r['actual']['value'] for r in selected) if selected else None)


def study(p, source, diagnostics):
    if p['protocol_id'] != 'tdx-revenue-cagr-v1' or source['contract_version'] != 'tdx-history-source-v1':
        raise ValueError('unsupported protocol/source')
    codes = [s['code'] for s in p['samples']]
    if len(codes) != len(set(codes)) or not codes:
        raise ValueError('unique samples required')
    artifacts = {a['file']: a for a in source['artifacts']}
    if len(artifacts) != len(source['artifacts']):
        raise ValueError('duplicate artifact')
    index = defaultdict(list)
    for r in source['records']:
        index[(r['code'], r['period'])].append(r)
    groups = {(r['code'], r['origin']): r.get('ebit_sign', 'unknown') for r in diagnostics['results']}
    rows = []
    # Complete all origin predictions before opening any target revenue.
    for code in codes:
        for origin in p['origins']:
            end = date.fromisoformat(origin); cutoff = f'{end.year}-09-01T00:00:00+08:00'
            forecasts, issues, evidence = predictions(p, index, artifacts, code, end, cutoff)
            rows.append(dict(code=code, origin=origin, forecast_as_of=cutoff, target=end.replace(year=end.year+1).isoformat(),
                profit_group=groups.get((code, origin), 'unknown'), forecasts=forecasts, forecast_issues=issues,
                evidence=evidence, status='blocked', actual_fcff=None))
    for r in rows:
        try:
            actual = revenue(index, artifacts, r['code'], date.fromisoformat(r['target']), p['evaluation_as_of'])
            r['actual'] = actual
            if actual['value'] <= 0:
                raise ValueError('positive actual revenue required for percentage errors')
            r['errors'] = {m: v-actual['value'] for m, v in r['forecasts'].items()}
            r['status'] = 'evaluated' if len(r['errors']) == 3 else 'incomplete_forecasts'
        except (ValueError, KeyError, ArithmeticError) as exc:
            r['actual_issue'] = str(exc)
    common = [r for r in rows if r['status'] == 'evaluated']
    summary = {m: metrics(common, m) for m in MODELS}
    independent = {m: metrics(rows, m) for m in MODELS}
    by_origin = {o: {m: metrics([r for r in common if r['origin'] == o], m) for m in MODELS} for o in p['origins']}
    g = p['gates']; b, c, z = (summary[m] for m in MODELS)
    companies = {r['code'] for r in common}
    def nonworse(a, b, key='mae_pct', ratio=1):
        return a[key] is not None and b[key] is not None and a[key] <= b[key]*ratio
    checks = dict(minimum_pairs=len(common) >= g['minimum_pairs'], minimum_companies=len(companies) >= g['minimum_companies'],
        each_origin_minimum=all(v[MODELS[1]]['n'] >= g['minimum_each_origin_pairs'] for v in by_origin.values()),
        coverage_retention=independent[MODELS[0]]['n'] > 0 and len(common)/independent[MODELS[0]]['n'] >= g['minimum_baseline_retention'],
        primary_improvement=b['mae_pct'] is not None and b['mae_pct'] > 0 and nonworse(c, b, ratio=1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(c, b, 'wape_pct'), zero_growth_nonworse=all(nonworse(c, z, k) for k in ('mae_pct', 'wape_pct')),
        each_origin_nonworse=all(nonworse(v[MODELS[1]], v[MODELS[0]], ratio=g['maximum_each_origin_mae_ratio']) for v in by_origin.values()),
        leave_one_company_out_nonworse=bool(companies) and all(nonworse(metrics([r for r in common if r['code'] != code], MODELS[1]), metrics([r for r in common if r['code'] != code], MODELS[0])) for code in companies))
    return dict(protocol_id=p['protocol_id'], phase='development', candidates=len(rows), common_companies=len(companies),
        summary=summary, independent=independent, statuses=dict(Counter(r['status'] for r in rows)), by_origin=by_origin,
        by_origin_coverage={o: dict(candidates=sum(r['origin'] == o for r in rows), statuses=dict(Counter(r['status'] for r in rows if r['origin'] == o)), independent={m: metrics([r for r in rows if r['origin'] == o], m) for m in MODELS}) for o in p['origins']},
        by_profit_group={group: dict(candidates=sum(r['profit_group'] == group for r in rows), independent={m: metrics([r for r in rows if r['profit_group'] == group], m) for m in MODELS}, common={m: metrics([r for r in common if r['profit_group'] == group], m) for m in MODELS}) for group in ('positive', 'nonpositive', 'unknown')},
        decision=dict(passed=all(checks.values()), checks=checks), amount_unit='million_CNY', results=rows, boundary=p['boundary'])


def load_inputs(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('frozen protocol hash differs')
    p = json.loads(raw); inputs = {}
    for key in ('parent_protocol', 'snapshot', 'origin_diagnostics'):
        data = (ROOT/p[key]).read_bytes()
        if hashlib.sha256(data).hexdigest() != p[key+'_sha256']:
            raise ValueError(key + ' hash differs')
        inputs[key] = json.loads(gzip.decompress(data) if key == 'origin_diagnostics' else data)
    if p['samples'] != [s for s in inputs['parent_protocol']['samples'] if s['split'] == 'development']:
        raise ValueError('development samples differ')
    return p, inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('protocol', type=Path); args = parser.parse_args()
    p, inputs = load_inputs(args.protocol)
    result = study(p, inputs['snapshot'], inputs['origin_diagnostics'])
    result['evidence'] = dict(protocol_sha256=PROTOCOL_SHA, snapshot_sha256=p['snapshot_sha256'], origin_diagnostics_sha256=p['origin_diagnostics_sha256'],
        helpers={name: hashlib.sha256((ROOT/'valuation/backend'/name).read_bytes()).hexdigest() for name in ('tools/backtest_tdx_revenue_cagr.py', 'tools/backtest_tdx_history.py', 'tools/tdx_research_source.py', 'data_sources/alphalake.py')})
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
