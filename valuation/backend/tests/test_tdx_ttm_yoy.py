import copy
from decimal import Decimal as D
import gzip
import json
from pathlib import Path

import pytest

from tools import backtest_tdx_ttm_yoy as tool

PATH = Path(__file__).resolve().parents[2]/'research/tdx-ttm-yoy/protocol.json'


def test_real_replay_and_decimal_growth():
    p, inputs = tool.load_inputs(PATH)
    result = tool.study(p, **inputs)
    saved = json.loads(gzip.decompress((PATH.parent/'development-result.json.gz').read_bytes()))
    assert result == {k: v for k, v in saved.items() if k != 'protocol_sha256'}
    assert result['decision']['passed'] is False
    assert result['summary']['candidates'] == 1800
    assert result['summary']['models']['ttm_yoy']['revenue_n'] == 1228
    for row in result['results']:
        if 'candidate_growth' not in row:
            continue
        prior = sum(D(q['value_cny']) for q in row['prior_revenue']['source_inputs'])/1000000
        current = D(str(row['base']['revenue']))
        growth = max(D('-.1'), min(D('.2'), current/prior-1))
        assert row['forecasts']['ttm_yoy']['revenue'] == pytest.approx(float(current*(1+growth)), rel=1e-12)


def test_future_missing_duplicate_and_hash(tmp_path):
    p, inputs = tool.load_inputs(PATH)
    saved = json.loads(gzip.decompress((PATH.parent/'development-result.json.gz').read_bytes()))
    chosen = next(r for r in saved['results'] if r['status'] == 'evaluated' and r['origin'] == '2023-06-30')
    p = p | dict(origins=[chosen['origin']])
    inputs['sample_protocol'] = inputs['sample_protocol'] | dict(samples=[s for s in inputs['sample_protocol']['samples'] if s['code'] == chosen['code']])
    expected = tool.study(p, **inputs)['results'][0]
    bad = copy.deepcopy(inputs)
    for row in bad['source']['records']:
        if row['code'] == chosen['code'] and row['period'] == '2024-06-30':
            row['bits'].pop('FN230', None)
    future = tool.study(p, **bad)['results'][0]
    assert future['status'] == 'blocked'
    assert future['base'] == expected['base'] and future['forecasts'] == expected['forecasts']
    period = '2021-09-30'
    missing = copy.deepcopy(inputs)
    missing['source']['records'] = [r for r in missing['source']['records'] if (r['code'], r['period']) != (chosen['code'], period)]
    row = tool.study(p, **missing)['results'][0]
    assert row['baseline_status'] == 'evaluated' and row['status'] == 'incomplete_candidate'
    assert row['forecasts']['current_rule'] == expected['forecasts']['current_rule']
    duplicate = copy.deepcopy(inputs)
    duplicate['source']['records'].append(next(r for r in duplicate['source']['records'] if (r['code'],r['period']) == (chosen['code'],period)))
    row = tool.study(p, **duplicate)['results'][0]
    assert row['status'] == 'incomplete_candidate' and 'duplicate' in row['candidate_issue']
    bad_plan = copy.deepcopy(p); bad_plan['inputs']['source']['sha256'] = '0'*64
    path = tmp_path/'protocol.json'; path.write_text(json.dumps(bad_plan))
    with pytest.raises(ValueError, match='input hash differs'):
        tool.load_inputs(path)
