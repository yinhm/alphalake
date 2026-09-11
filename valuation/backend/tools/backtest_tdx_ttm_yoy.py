"""完整TTM同比的一年开发比较；复用现有源窗口和生产增长基线。"""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from statistics import mean

from tools.backtest_tdx_multiyear_growth import forecast_rows
from tools.backtest_tdx_revenue_cagr import revenue
from tools.backtest_tdx_history import error, metrics as original_metrics

ROOT = Path(__file__).resolve().parents[3]
MODELS = ('current_rule', 'ttm_yoy', 'zero_growth')


def metrics(rows, models=MODELS):
    result = original_metrics(rows, models)
    valid = [r for r in rows if r['status'] == 'evaluated']
    for model in models:
        result['models'][model]['revenue_mae_pct'] = mean(abs(r['errors'][model]['revenue_error_pct']) for r in valid) if valid else None
    return result


def study(p, source, sample_protocol, helper_protocol):
    if p['analysis_id'] != 'tdx-ttm-yoy-v1' or p['horizon'] != 1 or p['phase'] != 'development':
        raise ValueError('unsupported study')
    base_policy = sample_protocol['base_policy']
    config = helper_protocol | dict(samples=[s for s in sample_protocol['samples'] if s['split'] == 'development'],
        base_policy=base_policy, evaluation_as_of=sample_protocol['evaluation_as_of'],
        windows=[dict(origin=o, horizon=1) for o in p['origins']])
    rows = forecast_rows(config, source, horizons=(1,))
    index = defaultdict(list)
    for r in source['records']:
        index[(r['code'], r['period'])].append(r)
    artifacts = {a['file']: a for a in source['artifacts']}
    for row in rows:
        row['baseline_status'] = row['status']
        if 'forecasts' in row:
            row['forecasts'] = {'current_rule': row['forecasts']['flat_first_five'], 'zero_growth': row['forecasts']['zero_growth']}
        if 'errors' in row:
            row['errors'] = {'current_rule': row['errors']['flat_first_five'], 'zero_growth': row['errors']['zero_growth']}
        if 'base' not in row:
            continue
        try:
            end = date.fromisoformat(row['origin'])
            prior = revenue(index, artifacts, row['code'], end.replace(year=end.year-1), row['forecast_as_of'])
            row['prior_revenue'] = prior
            if prior['value'] <= 0:
                raise ValueError('positive prior revenue required')
            raw_growth = row['base']['revenue']/prior['value']-1
            growth = max(base_policy['growth_floor'], min(base_policy['growth_ceiling'], raw_growth))
            predicted = dict(revenue=row['base']['revenue']*(1+growth), ebit=row['base']['ebit']*(1+growth))
            row.update(candidate_raw_growth=raw_growth, candidate_growth=growth)
            row['forecasts']['ttm_yoy'] = predicted
            if row['baseline_status'] == 'evaluated':
                row['errors']['ttm_yoy'] = error(predicted, row['actual'])
        except (ValueError, KeyError, ArithmeticError) as exc:
            row['candidate_issue'] = str(exc)
            if row['baseline_status'] == 'evaluated':
                row['status'] = 'incomplete_candidate'
    summary = metrics(rows)
    baseline = metrics([r | dict(status=r['baseline_status']) for r in rows], ('current_rule', 'zero_growth'))
    by_origin = {o: metrics([r for r in rows if r['origin'] == o]) for o in p['origins']}
    b, c, z = (summary['models'][m] for m in MODELS)
    g = p['gates']; companies = {r['code'] for r in rows if r['status'] == 'evaluated'}
    def nonworse(a, b, key, ratio=1):
        return a[key] is not None and b[key] is not None and a[key] <= b[key]*ratio
    checks = dict(minimum_pairs=c['revenue_n'] >= g['minimum_pairs'], minimum_companies=len(companies) >= g['minimum_companies'],
        each_origin_minimum=all(v['models']['ttm_yoy']['revenue_n'] >= g['minimum_each_origin_pairs'] for v in by_origin.values()),
        coverage_retention=c['revenue_n'] >= baseline['models']['current_rule']['revenue_n']*g['minimum_baseline_retention'],
        primary_improvement=b['revenue_mae_pct'] is not None and b['revenue_mae_pct']>0 and nonworse(c,b,'revenue_mae_pct',1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(c,b,'revenue_wape_pct'), zero_growth_nonworse=all(nonworse(c,z,k) for k in ('revenue_mae_pct','revenue_wape_pct')),
        ebit_nonworse=nonworse(c,b,'ebit_mae_pct_actual_revenue'),
        each_origin_nonworse=all(nonworse(v['models']['ttm_yoy'],v['models']['current_rule'],'revenue_mae_pct',g['maximum_each_origin_mae_ratio']) for v in by_origin.values()))
    return dict(summary=summary, baseline=baseline, by_origin=by_origin, common_companies=len(companies),
        decision=dict(passed=all(checks.values()),checks=checks), results=rows, boundary=p['boundary'])


def load_inputs(path):
    p = json.loads(path.read_bytes()); inputs = {}
    for key, ref in p['inputs'].items():
        raw = (ROOT/ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('input hash differs: '+key)
        inputs[key] = json.loads(raw)
    return p, inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('protocol', type=Path)
    args = parser.parse_args(); p, inputs = load_inputs(args.protocol)
    result = study(p, **inputs)
    result['protocol_sha256'] = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
