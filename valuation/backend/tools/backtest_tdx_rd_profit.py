"""冻结的年度研发重分类利润复验；不把资本化当成增长事实。"""
import argparse
from collections import Counter, defaultdict
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path

from engine.module_1_adjustments import capitalize_r_and_d
from tools.backtest_tdx_history import at, available, error
from tools.tdx_research_source import financial_value, source_field, evidence_value
from tools.backtest_tdx_normalized_margin import annual, metrics
from tools.backtest_tdx_revenue_cagr import predictions

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_SHA = 'b4706b3ce6268d548fb5571a55d0766937984c04bfa950004074f2cf1faba56e'
BASE, PRIMARY, BUDGET, SHORT = MODELS = ('flat_revenue_current_EBIT', 'rd_adjusted_margin', 'rd_budget_only', 'short_revenue_current_margin')


def rd_inputs(index, artifacts, code, year, cutoff):
    refs = []
    for y in range(year, year-6, -1):
        period = f'{y}-12-31'
        matches = index.get((code, period), [])
        if len(matches) != 1:
            raise ValueError('missing/duplicate RD year: '+period)
        r = matches[0]; a = artifacts[r['artifact']]
        if a['report_period'] != period or available(r, a) > at(cutoff):
            raise ValueError('RD period/cutoff differs: '+period)
        amount = financial_value(r, 'research_and_development_expense')
        if amount <= 0:
            raise ValueError('RD source zero ambiguous or negative: '+period)
        refs.append(dict(period=period, artifact=r['artifact'], bits=r['bits'][source_field('research_and_development_expense')], value_cny=str(amount)))
    return refs


def study(p, parent, source):
    if p['protocol_id'] != 'tdx-rd-profit-v1' or p['life'] != 5 or source['contract_version'] != 'tdx-history-source-v1':
        raise ValueError('unsupported protocol/source')
    samples = parent['samples']; codes = [s['code'] for s in samples]
    if not codes or len(set(codes)) != len(codes) or any(s['split'] != 'development' for s in samples):
        raise ValueError('unique development samples required')
    artifacts = {a['file']: a for a in source['artifacts']}
    if len(artifacts) != len(source['artifacts']):
        raise ValueError('duplicate artifact')
    index = defaultdict(list)
    code_set = set(codes)
    for r in source['records']:
        if r['code'] not in code_set:
            raise ValueError('unexpected source company')
        index[r['code'], r['period']].append(r)
    rows = []
    # Finish every prediction before opening target-period outcomes.
    for code in codes:
        for origin in p['origins']:
            end = date.fromisoformat(origin)
            cutoff = f'{end.year+1}-05-01T00:00:00+08:00'
            target = end.replace(year=end.year+1)
            if end.month != 12 or end.day != 31 or not end < at(cutoff).date() < target < at(p['evaluation_as_of']).date():
                raise ValueError('annual chronology differs')
            row = dict(code=code, origin=origin, forecast_as_of=cutoff, target=target.isoformat(),
                       forecasts={}, candidate_ready=False, status='baseline_blocked', actual_fcff=None)
            rows.append(row)
            try:
                base = annual(index, artifacts, code, end.year, cutoff)
                row['base'] = base
                row['profit_group'] = 'positive' if base['ebit'] > 0 else 'nonpositive'
                row['forecasts'] = {m: dict(revenue=base['revenue'], ebit=base['ebit']) for m in MODELS}
                rev, issues, evidence = predictions(parent, index, artifacts, code, end, cutoff)
                row['short_revenue_evidence'] = evidence
                if 'short_quarter_yoy_median_revenue_only' in rev:
                    amount = rev['short_quarter_yoy_median_revenue_only']
                    row['forecasts'][SHORT] = dict(revenue=amount, ebit=amount*base['ebit']/base['revenue'])
                else:
                    row['short_fallback_reason'] = issues.get('short_quarter_yoy_median_revenue_only', 'short history unavailable')
                try:
                    refs = rd_inputs(index, artifacts, code, end.year, cutoff)
                    previous = annual(index, artifacts, code, end.year-1, cutoff)
                    rd = [float(evidence_value(r, 'research_and_development_expense')/1000000) for r in refs]
                    next_rd = (rd[0]/base['revenue'] + rd[1]/previous['revenue'])/2*base['revenue']
                    _, amortization, asset = capitalize_r_and_d(rd[0], rd[1:], 5)
                    _, next_amortization, next_asset = capitalize_r_and_d(next_rd, rd[:5], 5)
                    adjusted = base['ebit'] + rd[0] - amortization
                    row['rd'] = dict(source_inputs=refs, prior_operating=previous, current_amortization=amortization,
                        current_asset=asset, forecast_amortization=next_amortization, forecast_asset=next_asset,
                        forecast_rd=next_rd, current_adjusted_ebit=adjusted)
                    row['forecasts'][PRIMARY]['ebit'] = adjusted - next_rd + next_amortization
                    row['forecasts'][BUDGET]['ebit'] = base['ebit'] + rd[0] - next_rd
                    row['candidate_ready'] = True
                except (ValueError, KeyError, ArithmeticError) as exc:
                    row['candidate_fallback_reason'] = str(exc)
            except (ValueError, KeyError, ArithmeticError) as exc:
                row['baseline_reason'] = str(exc)
    for row in rows:
        if not row['forecasts']:
            continue
        try:
            actual = annual(index, artifacts, row['code'], int(row['target'][:4]), p['evaluation_as_of'])
            row.update(actual=actual, status='evaluated', errors={m: error(f, actual) for m, f in row['forecasts'].items()})
        except (ValueError, KeyError, ArithmeticError) as exc:
            row.update(status='actual_blocked', actual_reason=str(exc))
    valid = [r for r in rows if r['status'] == 'evaluated']
    summary = metrics(rows, MODELS)
    by_origin = {o: metrics([r for r in rows if r['origin'] == o], MODELS) for o in p['origins']}
    g = p['gates']; key = 'ebit_mae_pct_actual_revenue'
    def nonworse(m, other=BASE, metric=key, ratio=1):
        a, b = m['models'][PRIMARY][metric], m['models'][other][metric]
        return a is not None and b is not None and a <= b*ratio
    losses = {m: defaultdict(float) for m in (BASE, PRIMARY)}
    for r in valid:
        for m in losses:
            losses[m][r['code']] += abs(r['errors'][m]['ebit_error_pct_actual_revenue'])
    totals = {m: sum(v.values()) for m, v in losses.items()}
    checks = dict(minimum_pairs=len(valid) >= g['minimum_pairs'],
        minimum_companies=len(losses[BASE]) >= g['minimum_companies'],
        minimum_each_origin=all(v['models'][PRIMARY]['ebit_n'] >= g['minimum_each_origin_pairs'] for v in by_origin.values()),
        candidate_ready_each_origin=all(sum(r['candidate_ready'] and r['origin'] == o for r in valid) >= g['minimum_candidate_ready_each_origin'] for o in p['origins']),
        coverage_retention=bool(valid) and sum(PRIMARY in r['forecasts'] for r in valid)/len(valid) >= g['minimum_baseline_retention'],
        primary_improvement=summary['models'][BASE][key] is not None and summary['models'][BASE][key] > 0 and nonworse(summary, ratio=1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(summary, metric='ebit_wape_pct'),
        short_nonworse=all(nonworse(summary, SHORT, k) for k in (key, 'ebit_wape_pct')),
        budget_nonworse=nonworse(summary, BUDGET),
        each_origin_nonworse=all(nonworse(v, ratio=g['maximum_each_origin_primary_ratio']) for v in by_origin.values()),
        leave_one_company_out_nonworse=len(losses[BASE]) > 1 and all(totals[PRIMARY]-losses[PRIMARY][c] <= totals[BASE]-losses[BASE][c] for c in losses[BASE]))
    return dict(protocol_id=p['protocol_id'], candidates=len(rows), summary=summary, by_origin=by_origin,
        candidate_ready={o:sum(r['candidate_ready'] and r['origin'] == o for r in rows) for o in p['origins']},
        by_profit_group={k:metrics([r for r in rows if r.get('profit_group', 'unknown') == k], MODELS) for k in ('positive', 'nonpositive', 'unknown')},
        by_rd_readiness={str(k):metrics([r for r in rows if r['candidate_ready'] == k], MODELS) for k in (False, True)},
        decision=dict(passed=all(checks.values()), checks=checks), results=rows, boundary=p['boundary'])


def load_inputs(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('frozen protocol hash differs')
    p = json.loads(raw); loaded = {}
    for key, ref in p['inputs'].items():
        data = (ROOT/ref['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError(key+' hash differs')
        loaded[key] = json.loads(gzip.decompress(data) if ref['path'].endswith('.gz') else data)
    source = loaded['snapshot']; extra = loaded['additional_source']
    if source['study_sha256'] != p['inputs']['study']['sha256'] or extra['study_sha256'] != source['study_sha256']:
        raise ValueError('source study binding differs')
    if loaded['study']['samples'] != loaded['parent_protocol']['samples']:
        raise ValueError('sample source binding differs')
    source['records'] += extra['records']; source['artifacts'] += extra['artifacts']
    return p, loaded['parent_protocol'], source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('protocol', type=Path)
    result = study(*load_inputs(parser.parse_args().protocol))
    names = ('engine/module_1_adjustments.py', 'tools/backtest_tdx_history.py', 'tools/tdx_research_source.py', 'tools/backtest_tdx_normalized_margin.py', 'tools/backtest_tdx_revenue_cagr.py')
    result['evidence'] = dict(protocol_sha256=PROTOCOL_SHA, tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        helpers={n:hashlib.sha256((ROOT/'valuation/backend'/n).read_bytes()).hexdigest() for n in names})
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
