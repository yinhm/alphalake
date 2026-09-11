"""冻结研发候选：缺项不删分母，目标结果不参与选择。"""
import copy
import struct

import pytest

from tools.backtest_tdx_rd_growth import BASE, ZERO, PRIMARY, ROOT, load_inputs, signal, study


def test_rd_selector_boundaries_and_real_replay(tmp_path):
    bits = lambda n: struct.unpack('<I', struct.pack('<f', n))[0]
    row = dict(origin='2023-06-30', annual_inputs=[
        dict(period=f'{2022-j}-12-31', status='positive_source', bits=bits(100), value_cny='100')
        for j in range(6)])
    assert signal(row, 5)['selected'] == ZERO
    row['annual_inputs'][0].update(bits=bits(101), value_cny='101')
    assert signal(row, 5)['selected'] == BASE
    row['annual_inputs'][0].update(bits=bits(99), value_cny='99')
    assert signal(row, 5)['selected'] == ZERO
    row['annual_inputs'][-1]['status'] = 'source_zero_ambiguous'
    assert signal(row, 5)['group'] == 'rd_history_unavailable'
    row['annual_inputs'][-1]['status'] = 'positive_source'
    row['annual_inputs'][0]['value_cny'] = '99.01'
    with pytest.raises(ValueError, match='bits/value'):
        signal(row, 5)
    row['annual_inputs'][0]['period'] = '2023-12-31'
    with pytest.raises(ValueError, match='periods'):
        signal(row, 5)
    protocol = ROOT/'valuation/research/tdx-rd-growth/protocol.json'
    p, parent, revenues, rd = load_inputs(protocol)
    result = study(p, parent, revenues, rd)
    assert result['candidates'] == 1800
    assert result['summary'][BASE]['n'] == 1726
    assert result['summary'][PRIMARY]['n'] == result['independent'][BASE]['n']
    assert sum(result['statuses'].values()) == 1800
    changed = copy.deepcopy(revenues)
    for r in changed['results']:
        if 'actual' in r:
            r['actual']['value'] *= 2
        r['profit_group'] = 'unknown'
    rerun = study(p, parent, changed, rd)
    assert [(r['choices'],r['forecasts']) for r in rerun['results']] == [
        (r['choices'],r['forecasts']) for r in result['results']]
    changed = copy.deepcopy(rd)
    changed['results'].append(changed['results'][0])
    with pytest.raises(ValueError, match='duplicate'):
        study(p, parent, revenues, changed)
    altered = tmp_path/'protocol.json'
    altered.write_bytes(protocol.read_bytes()+b'\n')
    with pytest.raises(ValueError, match='protocol hash'):
        load_inputs(altered)
