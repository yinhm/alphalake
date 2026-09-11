import copy
import gzip
import json
from pathlib import Path

import pytest

from tools.audit_tdx_origin_inputs import diagnose

ROOT = Path(__file__).resolve().parents[3]


def test_real_origin_inputs_and_isolation():
    p = json.loads((ROOT/'valuation/research/tdx-zero-growth-horizons/protocol.json').read_bytes())
    source = json.loads((ROOT/p['development_snapshot']).read_bytes())
    result = diagnose(p, source)
    saved = json.loads(gzip.decompress((ROOT/'valuation/research/tdx-origin-inputs/result.json.gz').read_bytes()))
    assert result == {k: v for k, v in saved.items() if k != 'evidence'}
    assert result['companies'] == 600 and len(result['results']) == 1800
    assert [x['statuses']['available'] for x in result['by_origin'].values()] == [556, 585, 589]
    assert sum(r.get('ebit_sign') == 'nonpositive' for r in result['results']) == 441
    assert all(r['comparability'] == 'unknown' and r['actual_fcff'] is None for r in result['results'])
    # A complete real company; neither future malformed rows nor outcome values enter diagnosis.
    chosen = next(r['code'] for r in result['results'] if r['origin'] == '2023-06-30' and r['consecutive_complete_years'] == 5 and r['status'] == 'available')
    small = copy.deepcopy(p)
    small['samples'] = [s for s in p['samples'] if s['code'] == chosen]
    small['windows'] = [w for w in p['windows'] if w['origin'] == '2023-06-30']
    s = copy.deepcopy(source)
    baseline = diagnose(small, s)
    for r in s['records']:
        if r['period'] > '2023-06-30':
            r['bits'] = {}; r['artifact'] = 'future-invalid'
    assert diagnose(small, s) == baseline
    target = next(r for r in s['records'] if r['code'] == chosen and r['period'] == '2023-06-30')
    s['records'].append(copy.deepcopy(target))
    assert 'duplicated' in diagnose(small, s)['results'][0]['reason']
    s['records'].pop()
    s['records'] = [r for r in s['records'] if not (r['code'] == chosen and r['period'] == '2022-12-31')]
    row = diagnose(small, s)['results'][0]
    assert row['consecutive_complete_years'] == 0 and row['annual_history'][-1]['status'] == 'blocked'
    s = copy.deepcopy(source)
    next(r for r in s['records'] if r['code'] == chosen and r['period'] == '2023-06-30')['bits']['FN230'] = 0
    assert diagnose(small, s)['results'][0]['status'] == 'available'
    for r in s['records']:
        if r['code'] == chosen and r['period'] <= '2023-06-30':
            for field in ('FN86', 'FN305', 'FN306', 'FN83', 'FN82', 'FN301'):
                r['bits'][field] = 0
    zero = diagnose(small, s)['results'][0]
    assert zero['status'] == 'available' and zero['current']['ebit'] == 0 and zero['ebit_sign'] == 'nonpositive'
    small['samples'] *= 2
    with pytest.raises(ValueError, match='unique'):
        diagnose(small, s)
