import copy
import json

import numpy as np
import pytest

from tools import audit_tdx_ocf_scope_uncertainty as tool


def test_frozen_scope_replay_same_company_draws_and_outside_error_isolation():
    p, inherited, inputs = tool.load_inputs()
    result = tool.study(p, inherited, inputs)
    saved = json.loads((tool.DIRECTORY/'scope-uncertainty-result.json').read_bytes())
    full = json.loads((tool.DIRECTORY/'uncertainty-result.json').read_bytes())
    assert result['companies'] == 120 and result['candidates'] == 360
    assert result['statuses'] == {'evaluated':331, 'blocked':29}
    assert result['scope'] == dict(group=p['scope_group'], candidates=255,
                                  statuses={'evaluated':244, 'blocked':11}, outside_scope_rows=105)
    for name, scheme in result['schemes'].items():
        assert scheme['draw_counts_sha256'] == full['schemes'][name]['draw_counts_sha256']
        assert scheme['evaluated_count_range'] == saved['schemes'][name]['evaluated_count_range']
        for window, kinds in scheme['intervals'].items():
            for kind, metrics in kinds.items():
                for metric, values in metrics.items():
                    expected = saved['schemes'][name]['intervals'][window][kind][metric]
                    assert values.keys() == expected.keys()
                    for key in values:
                        assert values[key] == pytest.approx(expected[key], abs=1e-10, rel=1e-12)
    _, parent, source, _ = inherited
    rows = tool.base.cash.evaluate(parent, source, 'holdout')
    membership = inputs['scope_result']['phases']['holdout']['membership']
    index = {(r['code'],r['origin']):r for r in membership}
    rows = [r | dict(origin_scope=index[r['code'],r['origin']]) for r in rows]
    samples = [s for s in parent['samples'] if s['split']=='holdout']
    before = tool.base.company_totals(rows, samples, parent['origins'], scope_group=p['scope_group'])
    for row in rows:
        if row['origin_scope']['group'] != p['scope_group']:
            row['actual'] = {'revenue_cny':'not-a-number'}
            row['forecasts'] = {}  # Outside outcomes must not enter the selected-scope errors.
    after = tool.base.company_totals(rows, samples, parent['origins'], scope_group=p['scope_group'])
    np.testing.assert_array_equal(before, after)
    changed = copy.deepcopy(inputs)
    changed['scope_result']['phases']['holdout']['membership'][0]['group'] = 'forged_scope'
    with pytest.raises(ValueError, match='original scope replay differs'):
        tool.study(p, inherited, changed)
