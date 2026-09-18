"""固定五家营运余额比例候选；不生成完整营运资本、FCFF或估值。"""
import argparse
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal as D
import gzip
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_history import at, available
from tools.tdx_research_source import financial_value, source_field, source_components, canonical_components
from tools.backtest_tdx_working_cash import revenue_window

FIELDS = ('accounts_receivable', 'inventories', 'accounts_payable')
MODELS = ('proportional_revenue', 'repeat_balance', 'mean_two_intensities')


def evaluate(plan, source, baseline):
    if plan['analysis_id'] != 'fixed-five-trade-balance-mean-two-v1' or tuple(plan['candidate_fields']) != tuple(source_field(f) for f in FIELDS):
        raise ValueError('unsupported trade balance study')
    if any(plan['gates'][k] is not True for k in ('component_wape_nonworse', 'require_repeat_balance_nonworse')):
        raise ValueError('required gate disabled')
    artifacts = {a['file']: a for a in source['artifacts']}
    if len(artifacts) != len(source['artifacts']):
        raise ValueError('duplicate artifact')
    index = defaultdict(list)
    for r in source['records']:
        index[r['code'], r['period']].append(r)

    def observation(code, period, cutoff):
        matches = index[code, period]
        if len(matches) != 1:
            raise ValueError('missing/duplicate balance')
        r = matches[0]
        a = artifacts[r['artifact']]
        if a['report_period'] != period or available(r, a) > at(cutoff):
            raise ValueError('balance period/cutoff differs')
        values = {f: financial_value(r, f) for f in FIELDS}
        if any(v <= 0 for v in values.values()):
            raise ValueError('nonpositive/ambiguous balance')
        revenue, refs, _ = revenue_window(index, artifacts, code, date.fromisoformat(period), cutoff)
        return values, revenue, dict(period=period, artifact=r['artifact'], bits={source_field(f): r['bits'][source_field(f)] for f in FIELDS}, revenue_inputs=refs)

    rows = []
    keys = {(r['code'], r['origin'], r['horizon']) for r in baseline['results']}
    expected = {(c, o, h) for c in plan['codes'] for o in plan['origins'] for h in plan['horizons']}
    if keys != expected or len(keys) != len(baseline['results']):
        raise ValueError('baseline sample differs')
    for b in baseline['results']:
        row = {k: b[k] for k in ('code', 'origin', 'horizon', 'target', 'financial_scope_flags')}
        row.update(status='blocked_candidate_input', baseline_status=b['status'], actual_fcff=None)
        rows.append(row)
        try:
            year = int(b['origin'][:4])
            cutoff = f'{year}-09-01T00:00:00+08:00'
            current, revenue, current_refs = observation(b['code'], b['origin'], cutoff)
            if current != {f: D(v) for f, v in canonical_components(b['base_balances_cny']).items()} or revenue != D(b['base_revenue_cny']):
                raise ValueError('base differs from frozen forecast')
            prior, prior_revenue, prior_refs = observation(b['code'], f'{year-1}-06-30', cutoff)
            forecast_revenue = D(b['predicted_revenue_cny'])
            forecast = {f: (current[f]/revenue + prior[f]/prior_revenue)/2*forecast_revenue for f in FIELDS}
            row.update(source_inputs=[current_refs, prior_refs], predicted_revenue_cny=str(forecast_revenue), candidate_balances_cny=source_components({f: str(v) for f, v in forecast.items()}))
            if date.fromisoformat(b['target']) > at(plan['evaluation_as_of']).date():
                row['status'] = 'not_yet_observable'
                continue
            row['status'] = 'blocked_actual'
            actual, actual_revenue, actual_refs = observation(b['code'], b['target'], plan['evaluation_as_of'])
            if b['status'] != 'evaluated' or actual != {f: D(v) for f, v in canonical_components(b['actual_balances_cny']).items()} or actual_revenue != D(b['actual_revenue_cny']):
                raise ValueError('actual differs from frozen evaluation')
            predictions = {m: {f: D(v) for f, v in canonical_components(b['predictions'][m]).items()} for m in MODELS[:2]}
            predictions[MODELS[2]] = forecast
            row.update(status='evaluated', actual_source=actual_refs, actual_balances_cny=source_components({f: str(v) for f, v in actual.items()}), actual_revenue_cny=str(actual_revenue), errors_cny={m: source_components({f: str(v[f]-actual[f]) for f in FIELDS}) for m, v in predictions.items()})
        except (ValueError, KeyError, ArithmeticError) as error:
            row['reason'] = str(error)
    return rows


def summarize(rows):
    valid = [r for r in rows if r['status'] == 'evaluated']
    models = {}
    for model in MODELS:
        errors = lambda r, f: abs(D(canonical_components(r['errors_cny'][model])[f]))
        models[model] = dict(n=len(valid), gross_component_mae_pct_revenue=float(sum((sum((errors(r, f) for f in FIELDS), D(0))/D(r['actual_revenue_cny'])*100 for r in valid), D(0))/len(valid)) if valid else None,
                            component_wape_pct=source_components({f: float(100*sum((errors(r, f) for r in valid), D(0))/sum((D(canonical_components(r['actual_balances_cny'])[f]) for r in valid), D(0))) if valid else None for f in FIELDS}))
    return dict(positions=len(rows), statuses=dict(Counter(r['status'] for r in rows)), models=models)


def run(path):
    plan = json.loads(path.read_bytes())
    inputs = {}
    for name, digest in plan['inputs'].items():
        raw = (path.parent/name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('input digest differs: '+name)
        inputs[name] = json.loads(gzip.decompress(raw) if name.endswith('.gz') else raw)
    rows = evaluate(plan, inputs['capital-snapshot.json'], inputs['trade-balance-result.json.gz'])
    summary = summarize(rows)
    groups = {name: {str(v): summarize([r for r in rows if r[key] == v]) for v in plan[name]} for name, key in (('origins', 'origin'), ('horizons', 'horizon'), ('codes', 'code'))}
    m = summary['models']; g = plan['gates']; old = m[MODELS[0]]; new = m[MODELS[2]]
    mature = sum(r['status'] == 'evaluated' for r in inputs['trade-balance-result.json.gz']['results'])
    comparisons = lambda group: [(s['models'][MODELS[0]]['gross_component_mae_pct_revenue'], s['models'][MODELS[2]]['gross_component_mae_pct_revenue']) for s in group.values()]
    baseline_error = old['gross_component_mae_pct_revenue']; candidate_error = new['gross_component_mae_pct_revenue']
    gates = dict(common_positions=new['n'] >= g['min_common_positions'], baseline_coverage=bool(mature and new['n']/mature >= g['min_baseline_coverage']),
                 gross_improvement=baseline_error is not None and candidate_error <= baseline_error*(1-g['min_gross_mae_improvement']),
                 component_wape=all(canonical_components(new['component_wape_pct'])[f] is not None and canonical_components(new['component_wape_pct'])[f] <= canonical_components(old['component_wape_pct'])[f] for f in FIELDS),
                 repeat_nonworse=candidate_error is not None and candidate_error <= m[MODELS[1]]['gross_component_mae_pct_revenue'],
                 periods=all(a is not None and b is not None and b <= a*g['max_period_mae_ratio'] for name in ('origins', 'horizons') for a, b in comparisons(groups[name])),
                 companies=sum(a is not None and b is not None and b <= a for a, b in comparisons(groups['codes'])) >= g['min_companies_nonworse'])
    return dict(plan_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), summary=summary, by_origin=groups['origins'], by_horizon=groups['horizons'], by_company=groups['codes'], gates=gates,
                decision='eligible_for_larger_development_not_adoption' if all(gates.values()) else 'stop_candidate_no_adoption', results=rows, boundary=plan['boundary'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.plan), ensure_ascii=False, indent=2, allow_nan=False))
