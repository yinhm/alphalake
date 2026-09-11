import copy
from decimal import Decimal
import gzip
import hashlib
import json
from statistics import median_low

import pytest

from tools import backtest_tdx_equal_weight as tool


def test_equal_weight_excludes_target_and_keeps_lower_median_convention():
    joint = tool.prior.joint
    rows = [joint.CalibrationObservation(code=f'{i:06}', predicted_ebit=100.,
        actual_ebit=80. if i < 16 else 120., actual_revenue=1000.) for i in range(32)]
    rows[-1] = rows[-1].model_copy(update={'actual_revenue': .001})
    rows.append(joint.CalibrationObservation(code='000999', predicted_ebit=100.,
        actual_ebit=1000000., actual_revenue=.001))
    equal = joint.fit(rows, '000999', equal_weight=True)
    assert equal['observations'] == 32 and equal['excluded_target']
    assert equal['raw_scale'] == .8 and equal['multiplier'] == .9
    assert joint.fit(rows, '000999')['raw_scale'] == 1.2
    assert joint.fit([r.model_copy(update={'actual_revenue': 1.}) for r in rows],
                     '000999', equal_weight=True) == equal
    with pytest.raises(ValueError, match='duplicate training company'):
        joint.fit(rows+[rows[0]], '000999', equal_weight=True)
    with pytest.raises(ValueError, match='insufficient training'):
        joint.fit(rows[:29]+rows[-1:], '000999', equal_weight=True)


def test_real_equal_weight_replay_decimal_coefficients_and_future_isolation():
    p, parent = tool.load_inputs()
    raw = (tool.DIRECTORY/'equal-weight-result.json.gz').read_bytes()
    summary = json.loads((tool.DIRECTORY/'equal-weight-summary.json').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == summary['result_sha256']
    actual = tool.study(p, parent)
    assert actual == json.loads(gzip.decompress(raw))
    assert tool.prior.refit(parent, ())[0] == parent['results']
    assert [(r['code'], r['origin'], r['status']) for r in actual['results']] == [
        (r['code'], r['origin'], r['status']) for r in parent['results']]
    joint = tool.prior.joint
    for row in actual['results']:
        if row.get('profit_group') == 'nonpositive':
            assert row['forecasts'][joint.COMBINED] == row['forecasts'][joint.ZERO]
        for base, model in ((joint.ZERO, joint.COMBINED), (joint.SHORT, joint.CALIBRATED)):
            fitted = row['calibration'].get(model, {})
            if fitted.get('status') != 'applied':
                continue
            pool = [r for r in parent['training'][row['origin']]['admitted'] if r['code'] != row['code']]
            ratios = [Decimal(str(r['realized']['ebit']))/Decimal(str(r['prior']['forecasts'][base]['ebit'])) for r in pool]
            median = median_low(ratios)
            multiplier = (1+max(Decimal('.5'), min(Decimal('1.5'), median)))/2
            assert fitted['observations'] == len(pool)
            assert abs(Decimal(str(fitted['raw_scale']))-median) < Decimal('1e-12')
            assert abs(Decimal(str(fitted['multiplier']))-multiplier) < Decimal('1e-12')
    changed = copy.deepcopy(parent)
    for row in changed['results']:
        if 'actual' in row:
            row['actual']['ebit'] += 1000000
    altered = tool.study(p, changed)
    assert [(r['forecasts'], r['calibration']) for r in altered['results']] == [
        (r['forecasts'], r['calibration']) for r in actual['results']]
    assert altered['summary'] != actual['summary']
