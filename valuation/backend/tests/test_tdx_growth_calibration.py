"""增长校准：独立系数/现金算式、成熟时点与未来隔离。"""
from copy import deepcopy
from decimal import Decimal as D
import gzip
import hashlib
import json
from pathlib import Path
import struct

import pytest

from tools.backtest_tdx_growth_calibration import load_inputs, study

ROOT = Path(__file__).resolve().parents[3]
DIRECTORY = ROOT/'valuation/research/tdx-growth-calibration'


def test_real_growth_calibration_and_cash_bridge():
    p, inputs = load_inputs(DIRECTORY/'protocol.json')
    saved = json.loads(gzip.decompress((DIRECTORY/'development.json.gz').read_bytes()))
    assert saved['protocol_sha256'] == hashlib.sha256((DIRECTORY/'protocol.json').read_bytes()).hexdigest()
    result = study(p,**inputs)
    assert result == {k:v for k,v in saved.items() if k!='protocol_sha256'}
    assert result['decision']['passed'] and result['summary']['statuses']=={'evaluated':632,'blocked':268}
    for fit in result['fits'].values():
        x = [D(str(r['x'])) for r in fit['pairs']]; y = [D(str(r['y'])) for r in fit['pairs']]
        mx = sum(x)/len(x); my = sum(y)/len(y)
        slope = sum((a-mx)*(b-my) for a,b in zip(x,y))/sum((a-mx)**2 for a in x)
        assert fit['slope'] == pytest.approx(float(slope),rel=1e-12)
        assert fit['intercept'] == pytest.approx(float(my-slope*mx),rel=1e-12)
    policy = inputs['sample_protocol']['base_policy']
    for row in result['results']:
        assert row['actual_fcff'] is None
        for model,cash in row.get('policy_cash',{}).items():
            f = row['forecasts'][model]
            nopat = D(str(f['ebit']))*(1-D(str(policy['tax_rate'])))
            reinvestment = (D(str(f['revenue']))-D(str(row['base']['revenue'])))/D(str(policy['sales_to_capital']))
            for k,v in [('nopat',nopat),('reinvestment',reinvestment),('fcff',nopat-reinvestment)]:
                assert cash[k] == pytest.approx(float(v),rel=1e-12,abs=1e-8)
    row = next(r for r in result['results'] if r['origin']=='2025-06-30' and r['status']=='evaluated')
    source = deepcopy(inputs['source'])
    record = next(r for r in source['records'] if (r['code'],r['period'])==(row['code'],'2026-06-30'))
    record['bits']['FN230'] ^= 1
    changed = study(p,source,inputs['sample_protocol'],inputs['design'])
    assert changed['fits'] == result['fits']
    assert any(a.get('actual') != b.get('actual') for a,b in zip(result['results'],changed['results']))
    for a,b in zip(result['results'],changed['results']):
        assert (a.get('forecasts'),a.get('policy_cash')) == (b.get('forecasts'),b.get('policy_cash'))
    source['records'].remove(record)
    changed = study(p,source,inputs['sample_protocol'],inputs['design'])
    absent = next(r for r in changed['results'] if (r['code'],r['origin'])==(row['code'],row['origin']))
    assert absent['status']=='blocked' and 'actual' not in absent and absent['forecasts']==row['forecasts']
    code = result['fits']['2023-06-30']['pairs'][0]['code']; source=deepcopy(inputs['source'])
    record = next(r for r in source['records'] if (r['code'],r['period'])==(code,'2023-06-30'))
    record['bits']['FN314'] = struct.unpack('<I',struct.pack('<f',20230902))[0]
    changed = study(p,source,inputs['sample_protocol'],inputs['design'])
    assert code not in {r['code'] for r in changed['fits']['2023-06-30']['pairs']}
    assert len(changed['fits']['2023-06-30']['pairs']) == len(result['fits']['2023-06-30']['pairs'])-1
