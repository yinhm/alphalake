"""固定现金预测的公司整组重采样；不重新选规则，不视作新的留出。"""
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random

import numpy as np

from tools import backtest_tdx_operating_cash as cash

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT/'valuation/research/tdx-operating-cash-forecast'
PROTOCOL_SHA = 'a81b2944d1d86df23ff8cd1b51d63de5469bab545a49954126f19cbfe87afe22'
KINDS = ('ocf_cny', 'cash_proxy_cny')
STATISTICS = ('mae_reduction_pp', 'relative_mae_reduction_pct', 'wape_reduction_pp')


def load_inputs():
    raw = (DIRECTORY/'uncertainty-protocol.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PROTOCOL_SHA:
        raise ValueError('uncertainty protocol differs')
    p = json.loads(raw); inputs = []
    for key in ('parent_protocol', 'snapshot', 'holdout_receipt'):
        ref = p['inputs'][key]; raw = (ROOT/ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError(key+' hash differs')
        inputs.append(json.loads(raw))
    return p, *inputs


def company_totals(rows, samples, origins, *, scope_group=None):
    """保留缺项公司的零个可评价观测；不是把缺项的误差记为零。"""
    codes = sorted(s['code'] for s in samples)
    if len(codes) != len(set(codes)) or len(origins) != len(set(origins)):
        raise ValueError('duplicate sample/origin')
    expected = {(c, o) for c in codes for o in origins}
    identities = [(r['code'], r['origin']) for r in rows]
    if len(identities) != len(set(identities)) or set(identities) != expected:
        raise ValueError('evaluation identities differ')
    totals = np.zeros((len(codes), len(origins), len(KINDS), 6))
    for row in rows:
        if scope_group is not None and row['origin_scope']['group'] != scope_group:
            continue
        if row['status'] == 'blocked':
            continue
        if row['status'] != 'evaluated':
            raise ValueError('unsupported row status')
        revenue = Decimal(row['actual']['revenue_cny'])
        if not revenue.is_finite() or revenue <= 0:
            raise ValueError('invalid actual revenue')
        for k, kind in enumerate(KINDS):
            actual = Decimal(row['actual'][kind])
            losses = [abs(Decimal(row['forecasts'][m][kind])-actual)
                      for m in ('repeat_latest', 'mean_two_ocf_margins')]
            totals[codes.index(row['code']), origins.index(row['origin']), k] = [
                1, float(100*losses[0]/revenue), float(100*losses[1]/revenue),
                float(losses[0]), float(losses[1]), float(abs(actual))]
    if not np.isfinite(totals).all():
        raise ValueError('nonfinite company totals')
    return totals


def draw_counts(samples, replicates, seed, stratified):
    ordered = sorted(samples, key=lambda s:s['code']); groups = defaultdict(list)
    if not ordered or replicates < 1 or len({s['code'] for s in ordered}) != len(ordered):
        raise ValueError('empty/duplicate resampling sample')
    for i, sample in enumerate(ordered):
        groups[sample['stratum'] if stratified else 'all'].append(i)
    rng = random.Random(seed); counts = np.zeros((replicates, len(samples)), dtype=np.int64)
    for b in range(replicates):
        indices = [i for label in sorted(groups) for i in rng.choices(groups[label], k=len(groups[label]))]
        counts[b] = np.bincount(indices, minlength=len(samples))
    return counts


def statistics(totals):
    """输入最后一轴：有效数、两种归一误差、两种金额误差、实际绝对金额。"""
    n, base, candidate, base_cash, candidate_cash, actual = np.moveaxis(totals, -1, 0)
    def ratio(numerator, denominator):
        return np.divide(numerator, denominator, out=np.full_like(numerator, np.nan), where=denominator > 0)
    return np.stack((ratio(base-candidate, n), ratio(100*(base-candidate), base),
                     ratio(100*(base_cash-candidate_cash), actual)), axis=-1)


def analyze(p, parent, rows, *, scope_group=None):
    samples = [s for s in parent['samples'] if s['split'] == 'holdout']
    origins = parent['origins']; totals = company_totals(rows, samples, origins, scope_group=scope_group)
    windows = ['all']+origins
    if windows != p['windows'] or list(KINDS) != p['targets'] or list(STATISTICS) != p['statistics']:
        raise ValueError('statistic contract differs')
    def with_all(v):
        return np.concatenate((v.sum(axis=1, keepdims=True), v), axis=1)
    point = statistics(with_all(totals.sum(axis=0, keepdims=True)))[0]
    strata = Counter(s['stratum'] for s in samples); schemes = {}
    for name, stratified in (('stratified', True), ('unstratified_sensitivity', False)):
        sampling = p['sampling']
        counts = draw_counts(samples, sampling['replicates'], sampling['seed'], stratified)
        sampled = (counts @ totals.reshape(len(samples), -1)).reshape((-1,)+totals.shape[1:])
        sampled = with_all(sampled); distribution = statistics(sampled); results = {}
        for w, window in enumerate(windows):
            results[window] = {}
            for k, kind in enumerate(KINDS):
                estimates = {}
                for j, metric in enumerate(STATISTICS):
                    values = distribution[:, w, k, j]; valid = values[np.isfinite(values)]
                    ci = np.quantile(valid, p['interval']['quantiles'], method='linear').tolist() if len(valid) else [None, None]
                    estimates[metric] = dict(point=float(point[w,k,j]) if np.isfinite(point[w,k,j]) else None,
                        interval=ci, valid_replicates=len(valid), undefined_replicates=len(values)-len(valid),
                        positive_resample_fraction=float(np.mean(valid > 0)) if len(valid) else None)
                results[window][kind] = estimates
        schemes[name] = dict(draw_counts_sha256=hashlib.sha256(counts.astype('<i8').tobytes()).hexdigest(),
            evaluated_count_range={w:[int(sampled[:,i,0,0].min()), int(sampled[:,i,0,0].max())] for i,w in enumerate(windows)},
            intervals=results)
    result = dict(protocol_sha256=PROTOCOL_SHA, companies=len(samples), candidates=len(rows),
        statuses=dict(Counter(r['status'] for r in rows)), strata=len(strata),
        singleton_strata=sorted(k for k,v in strata.items() if v == 1), schemes=schemes,
        interpretation=p['interpretation'], boundary=p['boundary'])
    if scope_group is not None:
        selected = [r for r in rows if r['origin_scope']['group'] == scope_group]
        result['scope'] = dict(group=scope_group, candidates=len(selected),
            statuses=dict(Counter(r['status'] for r in selected)), outside_scope_rows=len(rows)-len(selected))
    return result


def study(p, parent, source, receipt):
    original = cash.study(parent, source, 'holdout')
    if any(original[k] != receipt[k] for k in ('summary', 'by_origin', 'decision', 'refusals')):
        raise ValueError('original holdout point results differ')
    return analyze(p, parent, original['results'])


if __name__ == '__main__':
    print(json.dumps(study(*load_inputs()), ensure_ascii=False, indent=2, allow_nan=False))
