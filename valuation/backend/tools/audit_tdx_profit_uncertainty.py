"""固定半量利润校准的评价公司整组区间；不重选规则或训练系数。"""
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from tools import backtest_tdx_origins as original
from tools.audit_tdx_ocf_uncertainty import draw_counts, statistics

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT/'valuation/research/tdx-growth-expanded'
PROTOCOL_SHA = '52e8dbb1d0ee20032a0bd979ae42b15b5291ac58bd5ba5eabd5420fcd68b799d'


def load_inputs():
    raw = (DIRECTORY/'profit-uncertainty-protocol.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('uncertainty protocol differs')
    protocol = json.loads(raw); inputs = {}
    for name, ref in protocol['inputs'].items():
        raw = (ROOT/ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError(name+' hash differs')
        inputs[name] = json.loads(raw)
    return protocol, inputs


def replay(inputs):
    rows = []; receipts = {}
    for version in ('v7', 'v8'):
        p = inputs['protocol-'+version+'.json']
        selection = original.study(p, inputs['snapshot-v7.json'], 'development')
        # 原回执保存训练摘要和系数，省略逐公司训练行。
        for training in selection['calibration_training'].values():
            training.pop('results')
        saved = inputs['development-'+version+'-summary.json']
        if any(selection[k] != saved[k] for k in ('summary', 'selection', 'calibration_training')):
            raise ValueError('original development results differ')
        result = original.study(p, inputs['snapshot-v7.json'], 'holdout', selection)
        for training in result['calibration_training'].values():
            training.pop('results')
        saved = inputs['holdout-'+version+'-summary.json']
        if any(result[k] != saved[k] for k in ('summary', 'validation', 'calibration_training')):
            raise ValueError('original holdout results differ')
        rows.extend(result['results'])
        receipts[version] = dict(statuses=result['summary']['total']['statuses'],
                                validation=result['validation'])
    return rows, receipts


def company_totals(rows, samples, origins):
    codes = sorted(s['code'] for s in samples)
    identities = [(r['code'], r['origin']) for r in rows]
    if (len(set(codes)) != len(codes) or len(set(origins)) != len(origins)
            or len(set(identities)) != len(identities)
            or set(identities) != {(c, o) for c in codes for o in origins}):
        raise ValueError('evaluation identities differ')
    totals = np.zeros((len(codes), len(origins), 6))
    for row in rows:
        if row['status'] == 'blocked':
            continue  # 可评价数为0；不把缺项当成0误差观测。
        if row['status'] != 'evaluated':
            raise ValueError('unsupported row status')
        revenue = row['actual']['revenue']; actual = row['actual']['ebit']
        forecasts = [row['forecasts'][m]['ebit'] for m in ('current_rule', 'bias_half')]
        if not np.isfinite([revenue, actual, *forecasts]).all() or revenue <= 0:
            raise ValueError('invalid evaluation amounts')
        base, candidate = [abs(f-actual) for f in forecasts]
        totals[codes.index(row['code']), origins.index(row['origin'])] = [
            1, 100*base/revenue, 100*candidate/revenue, base, candidate, abs(actual)]
    if not np.isfinite(totals).all():
        raise ValueError("nonfinite company totals")
    return totals


def study(p, inputs):
    rows, receipts = replay(inputs)
    samples = [s for s in inputs['protocol-v7.json']['samples'] if s['split'] == 'holdout']
    if samples != [s for s in inputs['protocol-v8.json']['samples'] if s['split'] == 'holdout']:
        raise ValueError('holdout companies differ across protocols')
    origins = sorted({r['origin'] for r in rows})
    totals = company_totals(rows, samples, origins)
    schemes = {}
    for name, stratified in (('stratified', True), ('unstratified_sensitivity', False)):
        counts = draw_counts(samples, p['sampling']['replicates'], p['sampling']['seed'], stratified)
        windows = {}
        for window, selected in p['windows'].items():
            grouped = totals[:, [origins.index(o) for o in selected]].sum(axis=1)
            point = statistics(grouped.sum(axis=0))
            sampled = counts @ grouped
            distribution = statistics(sampled)
            estimates = {}
            for j, metric in enumerate(p['statistics']):
                values = distribution[:, j]; valid = values[np.isfinite(values)]
                estimates[metric] = dict(
                    point=float(point[j]) if np.isfinite(point[j]) else None,
                    interval=np.quantile(valid, p['interval']['quantiles'], method='linear').tolist() if len(valid) else [None, None],
                    valid_replicates=len(valid), undefined_replicates=len(values)-len(valid))
            windows[window] = dict(evaluated=int(grouped[:, 0].sum()),
                evaluated_count_range=[int(sampled[:, 0].min()), int(sampled[:, 0].max())], metrics=estimates)
        schemes[name] = dict(draw_counts_sha256=hashlib.sha256(counts.astype('<i8').tobytes()).hexdigest(),
                             windows=windows)
    strata = Counter(s['stratum'] for s in samples)
    code_files = [Path(__file__), Path(original.__file__),
                  ROOT/'valuation/backend/tools/backtest_tdx_history.py', ROOT/'valuation/backend/tools/tdx_research_source.py',
                  ROOT/'valuation/backend/tools/audit_tdx_ocf_uncertainty.py',
                  ROOT/'valuation/backend/data_sources/alphalake_calibration.py',
                  ROOT/'valuation/backend/data_sources/alphalake.py']
    return dict(protocol_sha256=PROTOCOL_SHA, companies=len(samples), candidates=len(rows),
        statuses=dict(Counter(r['status'] for r in rows)), strata=len(strata),
        singleton_strata=sorted(k for k, n in strata.items() if n == 1),
        companies_without_evaluable_rows=int(np.sum(totals[:, :, 0].sum(axis=1) == 0)),
        original_validation=receipts, schemes=schemes,
        code_sha256={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in code_files},
        interpretation=p['interpretation'], boundary=p['boundary'])


if __name__ == '__main__':
    print(json.dumps(study(*load_inputs()), ensure_ascii=False, indent=2, allow_nan=False))
