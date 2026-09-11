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


def test_real_gradual_replay_and_independent_aggregate():
    p=json.loads((DIR/'gradual-protocol.json').read_text())
    saved=json.loads((DIR/'gradual-result.json').read_text());actual=run(p)
    assert actual=={k:v for k,v in saved.items() if k!='evidence'}
    old=json.loads((DIR/'development-result.json').read_text())
    index={(r['code'],r['origin'],r['horizon']):r for r in old['results']}
    absolute=[];scaled=[];denominator=[]
    for row in actual['results']:
        if row['status']!='evaluated':continue
        parent=index[(row['code'],row['origin'],row['horizon'])]
        revenue=Decimal(str(parent['base']['revenue']));ebit=Decimal(str(parent['base']['ebit']))
        margin=ebit/revenue+(Decimal(str(parent['normalized_margin']))-ebit/revenue)*Decimal(row['horizon'])/5
        predicted=revenue*margin
        assert row['forecasts']['zero_growth_gradual_five_fy_margin']['ebit']==pytest.approx(float(predicted),rel=1e-12)
        difference=abs(predicted-Decimal(str(parent['actual']['ebit'])))
        absolute.append(difference);scaled.append(difference/Decimal(str(parent['actual']['revenue'])))
        denominator.append(abs(Decimal(str(parent['actual']['ebit']))))
    assert len(absolute)==578 and actual['baseline_evaluable']==995 and actual['evaluated_companies']==125
    metrics=actual['summary']['models']['zero_growth_gradual_five_fy_margin']
    assert metrics['ebit_mae_pct_actual_revenue']==pytest.approx(float(sum(scaled)/len(scaled)*100),rel=1e-12)
    assert metrics['ebit_wape_pct']==pytest.approx(float(sum(absolute)/sum(denominator)*100),rel=1e-12)
    assert actual['decision']['passed'] is False
