"""前一成熟窗口的经验误差范围；检验覆盖，不改变点预测或DCF。"""
import argparse
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

from tools.backtest_tdx_history import run

ROOT = Path(__file__).resolve().parents[3]
FIELDS = ('revenue', 'ebit')


def baseline(p, source, policy, role, origin, cutoff):
    year = int(origin[:4])
    config = dict(study_id=p['protocol_id'], origin=origin, target=f'{year+1}-06-30',
                  forecast_as_of=f'{year}-09-01T00:00:00+08:00', evaluation_as_of=cutoff,
                  policy=policy | dict(approved_report_period=origin),
                  samples=[s for s in p['samples'] if s['role'] == role])
    selected = source | dict(records=[r for r in source['records']
                                      if f'{year-1}-01-01' <= r['period'] <= config['target']])
    return run(config, selected)['results']


def study(p, source, sample_protocol):
    if (p['protocol_id'] != 'tdx-past-error-bands-v1' or p['quantiles'] != [.05, .95]
            or p['quantile_method'] != 'nearest_rank_ceil_n_times_p'
            or p['nominal_marginal_coverage'] != .9 or p['revenue_floor'] != 0
            or p['minimum_calibration_pairs'] != 80 or p['minimum_evaluation_pairs_each_origin'] != 80):
        raise ValueError('unsupported error-band policy')
    codes = [r['code'] for r in p['samples']]
    if len(codes) != len(set(codes)) or Counter(s['role'] for s in p['samples']) != {'calibration':300, 'evaluation':300}:
        raise ValueError('disjoint 300/300 companies required')
    policy = sample_protocol['base_policy']; results = []; calibration = {}; summaries = {}
    for origin in p['origins']:
        end = date.fromisoformat(origin)
        if (end.month, end.day) != (6, 30):
            raise ValueError('H1 origin required')
        cutoff = f'{end.year}-09-01T00:00:00+08:00'
        training = baseline(p, source, policy, 'calibration', f'{end.year-1}-06-30', cutoff)
        scores = [dict(code=r['code'], profit_scope=r['profit_scope'], financial_scope_flags=r['financial_scope_flags'],
                       residual={k:(r['actual'][k]-r['forecast'][k])/r['base']['revenue']
                                                for k in FIELDS}) for r in training if r['status'] == 'evaluated']
        ready = len(scores) >= p['minimum_calibration_pairs']
        quantiles = {}
        if ready:
            for key in FIELDS:
                values = sorted(r['residual'][key] for r in scores)
                if not all(math.isfinite(v) for v in values):
                    raise ValueError('nonfinite calibration residual')
                quantiles[key] = [values[math.ceil(len(values)*q)-1] for q in p['quantiles']]
        calibration[origin] = dict(origin=f'{end.year-1}-06-30', target=origin, cutoff=cutoff,
                                   statuses=dict(Counter(r['status'] for r in training)), quantiles=quantiles,
                                   scores=scores, refusals=[dict(code=r['code'], reason=r.get('reason'))
                                                          for r in training if r['status'] != 'evaluated'])
        rows = []
        for original in baseline(p, source, policy, 'evaluation', origin, p['evaluation_as_of']):
            row = dict(code=original['code'], origin=origin, status=original['status'],
                       baseline_status=original['status'], reason=original.get('reason'),
                       profit_scope=original.get('profit_scope'), financial_scope_flags=original.get('financial_scope_flags'))
            rows.append(row)
            if 'forecast' not in original:
                continue
            row.update(point=original['forecast'], base_revenue=original['base']['revenue'])
            if not ready:
                row['status'] = 'blocked_calibration'
                continue
            row['bands'] = {k:[max(0, row['point'][k]+q*row['base_revenue']) if k == 'revenue'
                               else row['point'][k]+q*row['base_revenue'] for q in quantiles[k]] for k in FIELDS}
            if original['status'] == 'evaluated':
                row['actual'] = {k:original['actual'][k] for k in FIELDS}
                row['covered'] = {k:row['bands'][k][0] <= row['actual'][k] <= row['bands'][k][1] for k in FIELDS}
        valid = [r for r in rows if r['status'] == 'evaluated']
        summaries[origin] = dict(candidates=len(rows), statuses=dict(Counter(r['status'] for r in rows)),
            marginal={k:dict(coverage=mean(r['covered'][k] for r in valid) if valid else None,
                            mean_width_base_revenue=mean((r['bands'][k][1]-r['bands'][k][0])/r['base_revenue']
                                                        for r in valid) if valid else None) for k in FIELDS},
            joint_coverage=mean(all(r['covered'].values()) for r in valid) if valid else None)
        results.extend(rows)
    enough = all(s['statuses'].get('evaluated',0) >= p['minimum_evaluation_pairs_each_origin'] for s in summaries.values())
    covered = bool(summaries) and all(s['marginal'][k]['coverage'] is not None and s['marginal'][k]['coverage'] >= .9
                                     for s in summaries.values() for k in FIELDS)
    return dict(calibration=calibration, by_origin=summaries, results=results,
                decision='eligible_for_new_company_validation_not_adopted' if enough and covered else 'stop_fixed_empirical_bands',
                boundary=p['boundary'])


def load_inputs(path):
    raw = path.read_bytes(); p = json.loads(raw); inputs = {}
    for key, ref in p['inputs'].items():
        data = (ROOT/ref['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != ref['sha256']:
            raise ValueError('input hash differs: '+key)
        inputs[key] = json.loads(data)
    return p, inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('protocol', type=Path)
    args = parser.parse_args(); p, inputs = load_inputs(args.protocol)
    result = study(p, **inputs)
    result['protocol_sha256'] = hashlib.sha256(args.protocol.read_bytes()).hexdigest()
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))


if __name__ == '__main__':
    main()
