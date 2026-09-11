"""渐进正常化复用生产规则，计算不读取目标实际值。"""
import copy
from decimal import Decimal
import json
from pathlib import Path

import pytest
from tools.backtest_tdx_gradual_margin import forecast,run

ROOT=Path(__file__).resolve().parents[3]
DIR=ROOT/'valuation/research/tdx-normalized-margin'


def test_production_transition_and_future_isolation():
    p=json.loads((DIR/'protocol.json').read_text());source=json.loads((DIR/'development-snapshot.json').read_text())
    old=json.loads((DIR/'development-result.json').read_text());row=next(r for r in old['results'] if r['status']=='evaluated')
    index={(r['code'],r['period']):r for r in source['records']}
    for horizon in (1,2,3,5):
        item=row|dict(horizon=horizon);actual,evidence=forecast(item,index,p['base_policy'])
        current=Decimal(str(row['base']['ebit']))/Decimal(str(row['base']['revenue']))
        expected=current+(Decimal(str(row['normalized_margin']))-current)*Decimal(horizon)/5
        assert actual['ebit']==pytest.approx(float(expected*Decimal(str(row['base']['revenue']))),rel=1e-12)
        assert actual['revenue']==row['base']['revenue']
        assert evidence['transition_years']==5
        changed=copy.deepcopy(index);changed[(row['code'],row['target'])]['bits']['FN230']+=1000000
        assert forecast(item|dict(actual={'ebit':1e90}),changed,p['base_policy'])==(actual,evidence)
    child=json.loads((DIR/'gradual-protocol.json').read_text())
    with pytest.raises(ValueError,match='unsupported gradual'):run(child|dict(transition_years=6))
    with pytest.raises(ValueError,match='parent_result hash differs'):run(child|dict(parent_result_sha256='0'*64))
