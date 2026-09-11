"""零增长与滞后利润校准组合；目标公司不参与自己的系数拟合。"""
import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path

from data_sources.alphalake_calibration import CalibrationObservation, fitted_scale
from tools.backtest_tdx_history import at, error
from tools.backtest_tdx_multiyear_growth import actual_operating
from tools.backtest_tdx_normalized_margin import metrics
from tools.backtest_tdx_revenue_cagr import predictions

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_SHA = 'b1fd4c4fe211f9fef0df423d599a31571c9c9bd727b2272218938336b16c24b3'
ZERO, COMBINED, SHORT, CALIBRATED = MODELS = ('zero_growth', 'zero_growth_half_calibrated', 'short_or_flat', 'short_or_flat_half_calibrated')


def fit(observations, target_code, minimum=30):
    if len({r.code for r in observations}) != len(observations):
        raise ValueError('duplicate training company')
    selected = [r for r in observations if r.code != target_code]
    if len(selected) < minimum:
        raise ValueError('insufficient training after target exclusion')
    raw = fitted_scale(selected)
    scale = max(.5, min(1.5, raw))
    return dict(status='applied', observations=len(selected), excluded_target=any(r.code == target_code for r in observations),
                raw_scale=raw, scale=scale, multiplier=(1+scale)/2)


def baseline(parent, index, artifacts, code, end, cutoff):
    current = actual_operating(index, artifacts, code, end, cutoff)
    forecasts = {ZERO:dict(revenue=current['revenue'], ebit=current['ebit'])}
    amounts, issues, evidence = predictions(parent, index, artifacts, code, end, cutoff)
    name = 'short_quarter_yoy_median_revenue_only'
    revenue = amounts.get(name, current['revenue'])
    forecasts[SHORT] = dict(revenue=revenue, ebit=current['ebit']*revenue/current['revenue'])
    if name not in amounts:
        evidence['short_flat_fallback'] = issues.get(name, 'short history unavailable')
    return dict(current=current, forecasts=forecasts, revenue_evidence=evidence)


def study(p, parent, source):
    if p['protocol_id'] != 'tdx-zero-calibration-v1' or tuple(p['models']) != MODELS or source['contract_version'] != 'tdx-history-source-v1':
        raise ValueError('unsupported protocol/source')
    codes = [s['code'] for s in parent['samples']]
    if not codes or len(set(codes)) != len(codes) or any(s['split'] != 'development' for s in parent['samples']):
        raise ValueError('unique development samples required')
    code_set = set(codes)
    artifacts = {a['file']: a for a in source['artifacts']}
    if len(artifacts) != len(source['artifacts']):
        raise ValueError('duplicate artifact')
    index = defaultdict(list)
    for r in source['records']:
        if r['code'] not in code_set:
            raise ValueError('unexpected source company')
        index[r['code'], r['period']].append(r)
    rows = []; training = {}
    for origin in p['origins']:
        end = date.fromisoformat(origin); prior = end.replace(year=end.year-1)
        cutoff = f'{end.year}-09-01T00:00:00+08:00'
        prior_cutoff = f'{prior.year}-09-01T00:00:00+08:00'
        target = end.replace(year=end.year+1)
        if (end.month, end.day) != (6,30) or not at(prior_cutoff) < at(cutoff) < at(p['evaluation_as_of']) or target >= at(p['evaluation_as_of']).date():
            raise ValueError('window chronology differs')
        pool = dict(origin=prior.isoformat(), forecast_as_of=prior_cutoff, realization_as_of=cutoff, admitted=[], rejected=[])
        training[origin] = pool
        observations = {m:[] for m in (ZERO, SHORT)}
        for code in codes:
            try:
                old = baseline(parent, index, artifacts, code, prior, prior_cutoff)
                now = actual_operating(index, artifacts, code, end, cutoff)
                if old['current']['ebit'] <= 0:
                    raise ValueError('nonpositive training prediction')
                entry = dict(code=code, prior=old, realized=now)
                prepared = {m:CalibrationObservation(code=code, predicted_ebit=old['forecasts'][m]['ebit'],
                                actual_ebit=now['ebit'], actual_revenue=now['revenue']) for m in observations}
                pool['admitted'].append(entry)
                for m, item in prepared.items():
                    observations[m].append(item)
            except (ValueError, KeyError, ArithmeticError) as exc:
                pool['rejected'].append(dict(code=code, reason=str(exc)))
        for code in codes:
            row = dict(code=code, origin=origin, target=target.isoformat(), forecast_as_of=cutoff,
                       forecasts={}, calibration={}, status='baseline_blocked', actual_fcff=None)
            rows.append(row)
            try:
                inputs = baseline(parent, index, artifacts, code, end, cutoff)
                row.update(inputs)
                row['profit_group'] = 'positive' if inputs['current']['ebit'] > 0 else 'nonpositive'
                for base, model in ((ZERO, COMBINED), (SHORT, CALIBRATED)):
                    forecast = row['forecasts'][base]
                    if forecast['ebit'] <= 0:
                        row['calibration'][model] = dict(status='nonpositive_unscaled')
                        row['forecasts'][model] = dict(forecast)
                        continue
                    try:
                        coefficient = fit(observations[base], code, p['training']['minimum_observations'])
                        row['calibration'][model] = coefficient
                        row['forecasts'][model] = forecast | dict(ebit=forecast['ebit']*coefficient['multiplier'])
                    except (ValueError, ArithmeticError) as exc:
                        row['calibration'][model] = dict(status='blocked_training', reason=str(exc))
            except (ValueError, KeyError, ArithmeticError) as exc:
                row['baseline_reason'] = str(exc)
    # Target outcomes are inaccessible to fitting and prediction above.
    for row in rows:
        if not row['forecasts']:
            continue
        try:
            actual = actual_operating(index, artifacts, row['code'], date.fromisoformat(row['target']), p['evaluation_as_of'])
            row.update(actual=actual, errors={m:error(f,actual) for m,f in row['forecasts'].items()},
                       status='evaluated' if len(row['forecasts']) == len(MODELS) else 'calibration_blocked')
        except (ValueError, KeyError, ArithmeticError) as exc:
            row.update(status='actual_blocked', actual_reason=str(exc))
    common = [r for r in rows if r['status'] == 'evaluated']
    summary = metrics(rows, MODELS)
    independent = {m:metrics([r | dict(status='evaluated') for r in rows if 'actual' in r and m in r['forecasts']], (m,)) for m in MODELS}
    by_origin = {o:metrics([r for r in rows if r['origin'] == o], MODELS) for o in p['origins']}
    g = p['gates']; key = 'ebit_mae_pct_actual_revenue'
    def nonworse(m, other=ZERO, metric=key, ratio=1):
        a, b = m['models'][COMBINED][metric], m['models'][other][metric]
        return a is not None and b is not None and a <= b*ratio
    losses = {m:defaultdict(float) for m in (ZERO, COMBINED)}
    for r in common:
        for m in losses:
            losses[m][r['code']] += abs(r['errors'][m]['ebit_error_pct_actual_revenue'])
    totals = {m:sum(v.values()) for m,v in losses.items()}
    count = independent[ZERO]['models'][ZERO]['ebit_n']
    checks = dict(minimum_pairs=len(common) >= g['minimum_pairs'], minimum_companies=len(losses[ZERO]) >= g['minimum_companies'],
        each_origin_minimum=all(m['models'][COMBINED]['ebit_n'] >= g['minimum_each_origin_pairs'] for m in by_origin.values()),
        applied_each_origin=all(sum(r['origin'] == o and r['calibration'][COMBINED]['status'] == 'applied' for r in common) >= g['minimum_applied_each_origin'] for o in p['origins']),
        baseline_retention=count > 0 and len(common)/count >= g['minimum_baseline_retention'],
        primary_improvement=summary['models'][ZERO][key] is not None and summary['models'][ZERO][key] > 0 and nonworse(summary, ratio=1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(summary, metric='ebit_wape_pct'),
        comparators_nonworse=all(nonworse(summary, other, metric) for other in (SHORT, CALIBRATED) for metric in (key,'ebit_wape_pct')),
        each_origin_nonworse=all(nonworse(m, ratio=g['maximum_each_origin_primary_ratio']) for m in by_origin.values()),
        leave_one_evaluation_company_out_nonworse=len(losses[ZERO]) > 1 and all(totals[COMBINED]-losses[COMBINED][c] <= totals[ZERO]-losses[ZERO][c] for c in losses[ZERO]))
    return dict(protocol_id=p['protocol_id'], candidates=len(rows), summary=summary, independent=independent, by_origin=by_origin,
        calibration_status={o:{m:dict(Counter(r['calibration'].get(m,{}).get('status','baseline_blocked') for r in rows if r['origin']==o)) for m in (COMBINED,CALIBRATED)} for o in p['origins']},
        by_profit_group={k:metrics([r for r in rows if r.get('profit_group','unknown')==k], MODELS) for k in ('positive','nonpositive','unknown')},
        training=training, decision=dict(passed=all(checks.values()), checks=checks), results=rows, boundary=p['boundary'])


def load_inputs(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('frozen protocol hash differs')
    p = json.loads(raw); data = {}
    for name, ref in p['inputs'].items():
        raw = (ROOT/ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError(name+' hash differs')
        data[name] = json.loads(raw)
    if data['parent_protocol']['snapshot_sha256'] != p['inputs']['snapshot']['sha256']:
        raise ValueError('parent snapshot binding differs')
    return p, data['parent_protocol'], data['snapshot']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('protocol', type=Path)
    result = study(*load_inputs(parser.parse_args().protocol))
    names = ('data_sources/alphalake_calibration.py', 'tools/backtest_tdx_history.py', 'tools/backtest_tdx_multiyear_growth.py', 'tools/backtest_tdx_normalized_margin.py', 'tools/backtest_tdx_revenue_cagr.py')
    result['evidence'] = dict(protocol_sha256=PROTOCOL_SHA, tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        helpers={n:hashlib.sha256((ROOT/'valuation/backend'/n).read_bytes()).hexdigest() for n in names})
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
