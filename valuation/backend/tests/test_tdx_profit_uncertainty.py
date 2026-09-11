import copy
from decimal import Decimal
import json

import numpy as np
import pytest

from tools import audit_tdx_profit_uncertainty as tool


def test_profit_company_blocks_and_independent_expanded_errors():
    samples = [dict(code=c, stratum='pair' if c != '3' else 'singleton') for c in ('1', '2', '3')]
    origins = ['2023-06-30', '2024-06-30']; rows = []
    for sample in samples:
        for origin in origins:
            c = sample['code']; row = dict(code=c, origin=origin, status='blocked')
            if c == '1' or (c == '2' and origin == origins[0]):
                row.update(status='evaluated', actual=dict(revenue=100 if c=='1' else 200, ebit=-100 if c=='1' else 50),
                           forecasts=dict(current_rule=dict(ebit=-90 if c=='1' else 50),
                                          bias_half=dict(ebit=-100 if c=='1' else 60)))
            rows.append(row)
    totals = tool.company_totals(rows, samples, origins)
    assert totals[:, :, 0].sum() == 3 and np.all(totals[2] == 0)
    counts = tool.draw_counts(samples, 500, 20260911, True)
    assert np.all(counts.sum(axis=1) == 3) and np.all(counts[:, 2] == 1)
    sampled = counts @ totals.sum(axis=1)
    values = tool.statistics(sampled)
    for b in range(20):
        expanded = [r for i, s in enumerate(samples) for _ in range(counts[b, i])
                    for r in rows if r['code'] == s['code'] and r['status'] == 'evaluated']
        errors = [[abs(Decimal(str(r['forecasts'][m]['ebit']))-Decimal(str(r['actual']['ebit'])))
                   for r in expanded] for m in ('current_rule', 'bias_half')]
        normalized = [sum(e*100/Decimal(str(r['actual']['revenue'])) for e, r in zip(es, expanded))
                      for es in errors]
        actual = sum(abs(Decimal(str(r['actual']['ebit']))) for r in expanded)
        expected = [float((normalized[0]-normalized[1])/len(expanded)),
                    float(100*(normalized[0]-normalized[1])/normalized[0]) if normalized[0] else np.nan,
                    float(100*(sum(errors[0])-sum(errors[1]))/actual)]
        np.testing.assert_allclose(values[b], expected, rtol=1e-12, atol=1e-12, equal_nan=True)
        assert sampled[b, 0] == len(expanded)
    assert np.isnan(tool.statistics(np.zeros(6))).all()
    for changed in (rows[:-1], rows+[rows[0]]):
        with pytest.raises(ValueError, match='identities differ'):
            tool.company_totals(changed, samples, origins)
    changed = copy.deepcopy(rows); changed[0]['actual']['revenue'] = 0
    with pytest.raises(ValueError, match='invalid evaluation amounts'):
        tool.company_totals(changed, samples, origins)


def test_real_profit_intervals_replay_and_reject_tampered_evidence():
    p, inputs = tool.load_inputs()
    result = tool.study(p, inputs)
    saved = json.loads((tool.DIRECTORY/'profit-uncertainty-result.json').read_text())
    def compare(a, b):
        if isinstance(a, dict):
            assert a.keys() == b.keys()
            for k in a:
                compare(a[k], b[k])
        elif isinstance(a, list):
            assert len(a) == len(b)
            for x, y in zip(a, b):
                compare(x, y)
        elif isinstance(a, float):
            assert a == pytest.approx(b, rel=1e-12, abs=1e-10)
        else:
            assert a == b
    # 历史代码哈希保留当时版本；数值与原门槛须重放，元数据变化不冒充数值变化。
    compare({k:v for k,v in result.items() if k != "code_sha256"},
            {k:v for k,v in saved.items() if k != "code_sha256"})
    assert result['companies'] == 120 and result['candidates'] == 360
    assert result['statuses'] == {'evaluated':256, 'blocked':104}
    assert result['companies_without_evaluable_rows'] == 14
    assert len(result['singleton_strata']) == 16
    assert result['original_validation']['v7']['validation']['verdict']['passed'] is True
    assert result['original_validation']['v8']['validation']['verdict']['passed'] is False
    summaries = inputs['holdout-v7-summary.json']['summary']
    old_windows = {'original_two_origins':summaries['total'],
                   **summaries['by_origin'],
                   'additional_2025_origin':inputs['holdout-v8-summary.json']['summary']['total']}
    for window, old in old_windows.items():
        metrics = old['models']
        base = metrics['current_rule']['ebit_mae_pct_actual_revenue']
        candidate = metrics['bias_half']['ebit_mae_pct_actual_revenue']
        actual = result['schemes']['stratified']['windows'][window]['metrics']['relative_mae_reduction_pct']['point']
        assert actual == pytest.approx(100*(base-candidate)/base, abs=1e-10)
    for scheme in result['schemes'].values():
        for window in scheme['windows'].values():
            assert all(m['valid_replicates']==9999 and m['undefined_replicates']==0 for m in window['metrics'].values())
    changed = copy.deepcopy(inputs)
    changed['development-v7-summary.json']['calibration_training']['2023-06-30']['ebit_scale'] += .01
    with pytest.raises(ValueError, match='original development results differ'):
        tool.study(p, changed)
    changed = copy.deepcopy(inputs)
    changed['holdout-v8-summary.json']['summary']['total']['models']['bias_half']['ebit_mae_pct_actual_revenue'] += 1
    with pytest.raises(ValueError, match='original holdout results differ'):
        tool.study(p, changed)
