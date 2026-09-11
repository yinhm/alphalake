"""经验范围的真实开发重放、独立计数与历史截止隔离。"""
from copy import deepcopy
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import struct

import pytest

from tools.backtest_tdx_error_bands import load_inputs, study

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT/'valuation/research/tdx-past-error-bands'


def test_error_bands_real_replay_and_future_isolation(tmp_path):
    p, inputs = load_inputs(DIRECTORY/'protocol.json')
    saved = json.loads(gzip.decompress((DIRECTORY/'result.json.gz').read_bytes()))
    assert saved['protocol_sha256'] == hashlib.sha256((DIRECTORY/'protocol.json').read_bytes()).hexdigest()
    result = study(p, **inputs)
    assert result == {k:v for k,v in saved.items() if k != 'protocol_sha256'}
    assert result['decision'] == 'stop_fixed_empirical_bands'
    assert len(result['results']) == 900
    assert sum(r['status']=='evaluated' for r in result['results']) == 632
    original = [r for r in inputs['sample_protocol']['samples'] if r['split']=='development']
    ordered = sorted(original,key=lambda r:hashlib.sha256((p['seed']+':'+r['code']).encode()).hexdigest())
    assert p['samples'] == [r | dict(role='calibration' if i<300 else 'evaluation') for i,r in enumerate(ordered)]
    for origin, calibration in result['calibration'].items():
        assert calibration['target'] == origin
        assert calibration['cutoff'] == origin[:4]+'-09-01T00:00:00+08:00'
        rows = [r for r in result['results'] if r['origin']==origin and r['status']=='evaluated']
        for field in ('revenue','ebit'):
            scores = sorted(Decimal(str(r['residual'][field])) for r in calibration['scores'])
            expected = [scores[int((Decimal(len(scores))*Decimal(str(q))).to_integral_value(rounding='ROUND_CEILING'))-1]
                        for q in p['quantiles']]
            assert [Decimal(str(v)) for v in calibration['quantiles'][field]] == expected
            count = 0
            for row in rows:
                bounds = [Decimal(str(row['point'][field]))+q*Decimal(str(row['base_revenue'])) for q in expected]
                if field == 'revenue': bounds = [max(Decimal(0),v) for v in bounds]
                for actual, calculated in zip(row['bands'][field],bounds):
                    assert math.isclose(actual,float(calculated),rel_tol=1e-12,abs_tol=1e-10)
                count += row['bands'][field][0] <= row['actual'][field] <= row['bands'][field][1]
            assert result['by_origin'][origin]['marginal'][field]['coverage'] == count/len(rows)
        assert result['by_origin'][origin]['joint_coverage'] == sum(all(r['covered'].values()) for r in rows)/len(rows)
    # 最晚未来利润篡改只能改变实际/误差，不能进入任何校准、点预测或范围。
    row = next(r for r in result['results'] if r['origin']=='2025-06-30' and r['status']=='evaluated')
    source = deepcopy(inputs['source'])
    record = next(r for r in source['records'] if (r['code'],r['period'])==(row['code'],'2026-06-30'))
    record['bits']['FN86'] ^= 1
    changed = study(p,source,inputs['sample_protocol'])
    assert changed['calibration'] == result['calibration']
    assert any(a.get('actual') != b.get('actual') for a,b in zip(result['results'],changed['results']))
    for a,b in zip(result['results'],changed['results']):
        assert (a.get('point'),a.get('bands')) == (b.get('point'),b.get('bands'))
    source['records'].remove(record)
    missing = study(p,source,inputs['sample_protocol'])
    absent = next(r for r in missing['results'] if (r['code'],r['origin'])==(row['code'],row['origin']))
    assert absent['status']=='blocked' and 'actual' not in absent
    assert (absent['point'],absent['bands']) == (row['point'],row['bands'])
    # 校准实际也必须在当前起点截止前披露，不得借用最终评价日。
    code = result['calibration']['2023-06-30']['scores'][0]['code']
    source = deepcopy(inputs['source'])
    record = next(r for r in source['records'] if (r['code'],r['period'])==(code,'2023-06-30'))
    record['bits']['FN314'] = struct.unpack('<I',struct.pack('<f',20230902))[0]
    changed = study(p,source,inputs['sample_protocol'])
    assert code not in {r['code'] for r in changed['calibration']['2023-06-30']['scores']}
    assert len(changed['calibration']['2023-06-30']['scores']) == len(result['calibration']['2023-06-30']['scores'])-1
    bad = deepcopy(p); bad['inputs']['source']['sha256'] = '0'*64
    path = tmp_path/'bad.json'; path.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='input hash differs'): load_inputs(path)
    bad = deepcopy(p); bad['samples'][0] = bad['samples'][-1]
    with pytest.raises(ValueError,match='disjoint'): study(bad,**inputs)
