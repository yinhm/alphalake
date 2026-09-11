import copy
from decimal import Decimal
import json

import numpy as np
import pytest

from tools import audit_tdx_ocf_uncertainty as tool


def test_company_blocks_keep_periods_pairs_singletons_and_missing_denominators():
    samples = [dict(code=c, stratum='pair' if c != '000003' else 'singleton')
               for c in ('000001', '000002', '000003')]
    origins = ['2023-06-30', '2024-06-30']; rows = []
    for sample in samples:
        for origin in origins:
            c = sample['code']; row = dict(code=c, origin=origin, status='blocked')
            if c != '000003':
                row.update(status='evaluated', actual=dict(revenue_cny='100', ocf_cny='100', cash_proxy_cny='100'),
                    forecasts={m:{k:str(110 if (c=='000001') == (m=='repeat_latest') else 100)
                                  for k in tool.KINDS} for m in ('repeat_latest','mean_two_ocf_margins')})
            rows.append(row)
    totals = tool.company_totals(rows, samples, origins)
    assert np.all(totals[2] == 0)  # No evaluable observations, including the count column.
    counts = tool.draw_counts(samples, 500, 20260911, True)
    assert np.all(counts.sum(axis=1) == 3) and np.all(counts[:,2] == 1)
    assert np.all(counts[:,:2].sum(axis=1) == 2)
    np.testing.assert_array_equal(counts, tool.draw_counts(samples[::-1], 500, 20260911, True))
    sums = counts @ totals.sum(axis=1).reshape(3, -1)
    statistics = tool.statistics(sums.reshape(500, 2, 6))
    assert set(statistics[:,0,0]) == {-10., 0., 10.}
    assert np.isnan(statistics[:,0,1]).any()  # Zero baseline error has no relative reduction.
    # Independently expand sampled companies, keeping both of their periods and model pairs.
    for b in range(20):
        expanded = [r for i,s in enumerate(samples) for _ in range(counts[b,i])
                    for r in rows if r['code']==s['code'] and r['status']=='evaluated']
        losses = [sum((abs(Decimal(r['forecasts'][m]['ocf_cny'])-Decimal(r['actual']['ocf_cny']))
                       for r in expanded), Decimal(0)) for m in ('repeat_latest','mean_two_ocf_margins')]
        assert statistics[b,0,0] == float((losses[0]-losses[1])/len(expanded))
    unstratified = tool.draw_counts(samples, 500, 20260911, False)
    assert np.any(unstratified[:,2] == 3)
    missing = tool.statistics(np.zeros((1, 2, 6)))
    assert np.isnan(missing).all()
    with pytest.raises(ValueError, match='evaluation identities differ'):
        tool.company_totals(rows+[rows[0]], samples, origins)


def test_real_conditional_intervals_replay_without_reclassifying_original_gate():
    inputs = tool.load_inputs(); result = tool.study(*inputs)
    saved = json.loads((tool.DIRECTORY/'uncertainty-result.json').read_bytes())
    def compare(a, b):
        if isinstance(a, dict):
            assert a.keys() == b.keys()
            for k in a: compare(a[k], b[k])
        elif isinstance(a, list):
            assert len(a) == len(b)
            for x,y in zip(a,b): compare(x,y)
        elif isinstance(a, float):
            assert a == pytest.approx(b, abs=1e-10, rel=1e-12)
        else:
            assert a == b
    compare(result, saved)
    assert result['statuses'] == {'evaluated':331, 'blocked':29}
    assert len(result['singleton_strata']) == 17
    for scheme in result['schemes'].values():
        for targets in scheme['intervals'].values():
            for metrics in targets.values():
                assert all(x['valid_replicates']==9999 and x['undefined_replicates']==0 for x in metrics.values())
    assert inputs[-1]['decision']['passed'] is True
    changed = copy.deepcopy(inputs[-1])
    changed['summary']['targets']['ocf_cny']['repeat_latest']['mae_pct_actual_revenue'] += 1
    with pytest.raises(ValueError, match='original holdout point results differ'):
        tool.study(*inputs[:-1], changed)
