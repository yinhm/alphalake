"""一年窗口与既有两/三年预测共用实现，目标值不得改变预测。"""
import copy
import json
from pathlib import Path
import struct

import pytest
from tools.validate_tdx_zero_horizons import evaluate

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-zero-growth-horizons'


def test_one_year_and_future_isolation():
    p=json.loads((DIR/'protocol.json').read_text());p['samples']=[r for r in p['samples'] if r['code']=='000829'];p['windows']=[dict(origin='2023-06-30',horizon=1)]
    source=json.loads((ROOT/p['development_snapshot']).read_text());source['records']=[r for r in source['records'] if r['code']=='000829']
    r=evaluate(p,source,'development')['results'][0]
    assert r['status']=='evaluated'
    assert r['forecasts']['zero_growth']['revenue']==r['base']['revenue']
    assert r['forecasts']['zero_growth']['ebit']==pytest.approx(r['base']['ebit'])
    assert r['forecasts']['flat_first_five']['revenue']==r['base']['revenue']*(1+r['rule_evidence']['clipped_scenario_growth'])
    assert all(len(f['annual'])==1 for f in r['forecasts'].values())
    changed=copy.deepcopy(source);target=next(v for v in changed['records'] if v['period']=='2024-06-30');target['bits']['FN230']=struct.unpack('<I',struct.pack('<f',1e12))[0]
    after=evaluate(p,changed,'development')['results'][0]
    assert r['forecasts']==after['forecasts'] and r['actual']!=after['actual']
    with pytest.raises(ValueError,match='unexpected source'):evaluate(p|dict(samples=[]),source,'development')
