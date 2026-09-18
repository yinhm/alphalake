"""只用起点前已兑现误差选择收入输入，不修改生产DCF。"""
import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from statistics import mean

from tools import backtest_tdx_revenue_cagr as parent

ROOT = Path(__file__).resolve().parents[3]
MODELS = (parent.MODELS[0], 'past_error_selected_revenue', parent.MODELS[2])
PROTOCOL_SHA = '1db29b5ba76be8e4db96d8ede29e264d99f04927ef8722424037c9bc85015255'


def inputs_at(p, index, artifacts, code, end):
    cutoff = f'{end.year}-09-01T00:00:00+08:00'
    f, issues, evidence = parent.predictions(p, index, artifacts, code, end, cutoff)
    return ({m: f[m] for m in (MODELS[0], MODELS[2]) if m in f},
            {m: issues[m] for m in (MODELS[0], MODELS[2]) if m in issues},
            {k: v for k, v in evidence.items() if k in ('current', 'short_history', 'short_growth')})


def select(p, base, index, artifacts, code, end):
    cutoff = f'{end.year}-09-01T00:00:00+08:00'
    f, issues, evidence = inputs_at(base, index, artifacts, code, end)
    out = dict(forecasts=f, forecast_issues=issues, evidence=evidence, history=[], selection='baseline_blocked')
    if MODELS[0] not in f:
        return out
    for offset in range(1, p['history_origins']+1):
        origin = end.replace(year=end.year-offset); target = origin.replace(year=origin.year+1)
        forecasts, reasons, refs = inputs_at(base, index, artifacts, code, origin)
        row = dict(origin=origin.isoformat(), target=target.isoformat(), forecast_as_of=f'{origin.year}-09-01T00:00:00+08:00',
                   actual_as_of=cutoff, forecasts=forecasts, issues=reasons, evidence=refs, status='blocked')
        out['history'].append(row)
        try:
            if len(forecasts) != 2:
                raise ValueError('paired historical forecasts required')
            actual = parent.revenue(index, artifacts, code, target, cutoff)
            row['actual'] = actual
            if actual['value'] <= 0:
                raise ValueError('positive historical actual required')
            row.update(status='evaluated', errors={m: abs(v-actual['value'])*100/actual['value'] for m, v in forecasts.items()})
        except (ValueError, KeyError, ArithmeticError) as exc:
            row['reason'] = str(exc)
    pairs = [r for r in out['history'] if r['status'] == 'evaluated']
    out['matured_pairs'] = len(pairs)
    if len(pairs) < p['minimum_matured_pairs']:
        choice = p['insufficient_history_choice']; out['selection'] = 'insufficient_matured_history_use_baseline'
    else:
        losses = {m: mean(r['errors'][m] for r in pairs) for m in (MODELS[0], MODELS[2])}
        choice = MODELS[0] if losses[MODELS[0]] < losses[MODELS[2]] else p['tie_choice']
        out.update(selection='past_error_choice', selection_mae_pct=losses)
    out['choice'] = choice; f[MODELS[1]] = f[choice]
    return out


def study(p, base, source, diagnostics):
    artifacts = {a['file']: a for a in source['artifacts']}; index = defaultdict(list)
    if len(artifacts) != len(source['artifacts']):
        raise ValueError('duplicate artifact')
    codes = [s['code'] for s in base['samples']]
    if len(codes) != len(set(codes)) or not codes:
        raise ValueError('unique samples required')
    for r in source['records']:
        index[(r['code'], r['period'])].append(r)
    groups = {(r['code'], r['origin']): r.get('ebit_sign', 'unknown') for r in diagnostics['results']}
    rows = []
    # 所有选择先于当期目标评分；历史实际只用当前起点截止。
    for code in codes:
        for origin in base['origins']:
            end = date.fromisoformat(origin)
            rows.append(dict(code=code, origin=origin, target=end.replace(year=end.year+1).isoformat(),
                profit_group=groups.get((code, origin), 'unknown'), status='blocked', actual_fcff=None,
                **select(p, base, index, artifacts, code, end)))
    for row in rows:
        try:
            actual = parent.revenue(index, artifacts, row['code'], date.fromisoformat(row['target']), base['evaluation_as_of'])
            row['actual'] = actual
            if actual['value'] <= 0:
                raise ValueError('positive current target required')
            row['errors'] = {m: v-actual['value'] for m, v in row['forecasts'].items()}
            row['status'] = 'evaluated' if len(row['errors']) == 3 else 'incomplete_forecasts'
        except (ValueError, KeyError, ArithmeticError) as exc:
            row['actual_issue'] = str(exc)
    common = [r for r in rows if r['status'] == 'evaluated']
    summarize = lambda selected: {m: parent.metrics(selected, m) for m in MODELS}
    summary = summarize(common); independent = summarize(rows)
    by_origin = {o: dict(candidates=sum(r['origin'] == o for r in rows), common=summarize([r for r in common if r['origin'] == o]),
        independent=summarize([r for r in rows if r['origin'] == o])) for o in base['origins']}
    b, c, z = (summary[m] for m in MODELS); g = p['gates']; companies = {r['code'] for r in common}
    def nonworse(a, b, key='mae_pct', ratio=1):
        return a[key] is not None and b[key] is not None and a[key] <= b[key]*ratio
    checks = dict(minimum_pairs=len(common) >= g['minimum_pairs'], minimum_companies=len(companies) >= g['minimum_companies'],
        each_origin_minimum=all(v['common'][MODELS[1]]['n'] >= g['minimum_each_origin_pairs'] for v in by_origin.values()),
        coverage_retention=independent[MODELS[0]]['n'] > 0 and len(common)/independent[MODELS[0]]['n'] >= g['minimum_baseline_retention'],
        primary_improvement=b['mae_pct'] is not None and b['mae_pct'] > 0 and nonworse(c, b, ratio=1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(c, b, 'wape_pct'), zero_growth_nonworse=all(nonworse(c, z, k) for k in ('mae_pct', 'wape_pct')),
        each_origin_nonworse=all(nonworse(v['common'][MODELS[1]], v['common'][MODELS[0]], ratio=g['maximum_each_origin_mae_ratio']) for v in by_origin.values()),
        leave_one_company_out_nonworse=bool(companies) and all(nonworse(parent.metrics([r for r in common if r['code'] != code], MODELS[1]), parent.metrics([r for r in common if r['code'] != code], MODELS[0])) for code in companies))
    return dict(protocol_id=p['protocol_id'], candidates=len(rows), common_companies=len(companies), summary=summary, independent=independent,
        statuses=dict(Counter(r['status'] for r in rows)), by_origin=by_origin,
        by_selection={s: dict(candidates=sum(r['selection'] == s for r in rows), common=summarize([r for r in common if r['selection'] == s])) for s in sorted({r['selection'] for r in rows})},
        by_choice={s: dict(candidates=sum(r.get('choice') == s for r in rows), common=summarize([r for r in common if r.get('choice') == s])) for s in (MODELS[0], MODELS[2])},
        by_profit_group={s: dict(candidates=sum(r['profit_group'] == s for r in rows), common=summarize([r for r in common if r['profit_group'] == s])) for s in ('positive', 'nonpositive', 'unknown')},
        decision=dict(passed=all(checks.values()), checks=checks), results=rows, amount_unit='million_CNY', boundary=p['boundary'])


def load_inputs(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('selection protocol hash differs')
    p = json.loads(raw)
    if hashlib.sha256((ROOT/p['parent_protocol']).read_bytes()).hexdigest() != p['parent_protocol_sha256']:
        raise ValueError('parent hash differs')
    base, inputs = parent.load_inputs(ROOT/p['parent_protocol'])
    return p, base, inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('protocol', type=Path); args = parser.parse_args()
    p, base, inputs = load_inputs(args.protocol)
    result = study(p, base, inputs['snapshot'], inputs['origin_diagnostics'])
    result['evidence'] = dict(protocol_sha256=PROTOCOL_SHA, parent_protocol_sha256=p['parent_protocol_sha256'], snapshot_sha256=base['snapshot_sha256'],
        origin_diagnostics_sha256=base['origin_diagnostics_sha256'], helpers={name: hashlib.sha256((ROOT/'valuation/backend'/name).read_bytes()).hexdigest()
        for name in ('tools/backtest_tdx_revenue_selection.py', 'tools/backtest_tdx_revenue_cagr.py', 'tools/backtest_tdx_history.py', 'tools/tdx_research_source.py')})
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
