from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from tools.prepare_temporal_forecasts import prepare
from tools.backtest_tdx_origins import evaluate, fit_calibration

ROOT=Path(__file__).resolve().parents[3]
DIRECTORY=ROOT/'valuation/research/tdx-growth-expanded'


def test_frozen_forecasts_replay_and_ignore_future():
    plan=json.loads((DIRECTORY/'forward-plan.json').read_bytes())
    parent=json.loads((ROOT/plan['parent_path']).read_bytes())
    source=json.loads((ROOT/plan['source_path']).read_bytes())
    saved=json.loads(gzip.decompress((DIRECTORY/'forward-2026H1.json.gz').read_bytes()))
    for name in ('parent','source'):
        assert hashlib.sha256((ROOT/plan[name+'_path']).read_bytes()).hexdigest()==plan[name+'_sha256']
    result=prepare(plan,parent,source,saved['prepared_at'])
    assert result=={k:v for k,v in saved.items() if k!='inputs'}
    assert result['statuses']=={'blocked_inputs':39,'awaiting_target':81}
    assert all(r['actual'] is None and r['errors'] is None for r in result['rows'])
    changed=deepcopy(source)
    future=deepcopy(source['records'][0]);future['period']='2027-06-30';future['bits']={k:0 for k in future['bits']}
    changed['records'].extend([future,future])
    assert prepare(plan,parent,changed,saved['prepared_at'])==result
    with pytest.raises(AssertionError):prepare(plan,parent,source,'2027-07-01T00:00:00+00:00')


def test_predict_only_matches_original_mature_forecasts():
    plan=json.loads((DIRECTORY/'forward-plan.json').read_bytes())
    parent=json.loads((ROOT/plan['parent_path']).read_bytes())
    source=json.loads((ROOT/plan['source_path']).read_bytes())
    origin='2024-06-30'
    plan=plan|dict(origin=origin,target='2025-06-30',forecast_cutoff='2024-09-01T00:00:00+08:00')
    new=prepare(plan,parent,source,'2024-09-02T00:00:00+08:00')
    old=evaluate(parent,source,'holdout',[origin],plan['models'],{origin:fit_calibration(parent,source,origin)})
    index={r['code']:r for r in new['rows']};count=0
    for row in old:
        if row['status']=='evaluated':
            assert index[row['code']]['forecasts']==row['forecasts']
            count+=1
    assert count==86
