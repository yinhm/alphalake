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


def test_review_waits_without_actual_io_and_rejects_frozen_tamper(tmp_path,monkeypatch):
    from tools.review_temporal_forecasts import load_frozen,review
    import shutil
    monkeypatch.chdir(ROOT)
    plan,saved,_=load_frozen(DIRECTORY)
    def forbidden():raise AssertionError('actual file opened before cutoff')
    for now in ('2026-09-11T22:00:00+00:00','2027-08-31T15:59:59+00:00'):
        result=review(plan,saved,forbidden,now)
        assert result['status']=='awaiting_evaluation_cutoff' and result['decision'] is None
        assert result['rows']==saved['rows']
    for name in ('forward-plan.json','forward-receipt.json','forward-2026H1.json.gz'):
        shutil.copyfile(DIRECTORY/name,tmp_path/name)
    changed=deepcopy(saved)
    next(r for r in changed['rows'] if r['status']=='awaiting_target')['forecasts']['bias_half']['ebit']+=1
    (tmp_path/'forward-2026H1.json.gz').write_bytes(gzip.compress(json.dumps(changed).encode()))
    with pytest.raises(ValueError,match='frozen file hash differs'):load_frozen(tmp_path)


def test_mature_review_matches_original_and_does_not_refit():
    from tools.review_temporal_forecasts import review
    from tools.backtest_tdx_origins import summarize,gates
    plan=json.loads((DIRECTORY/'forward-plan.json').read_bytes())
    parent=json.loads((ROOT/plan['parent_path']).read_bytes())
    source=json.loads((ROOT/plan['source_path']).read_bytes())
    origin='2024-06-30'
    plan=plan|dict(origin=origin,target='2025-06-30',forecast_cutoff='2024-09-01T00:00:00+08:00',evaluation_cutoff=parent['evaluation_as_of'])
    saved=prepare(plan,parent,source,'2024-09-02T00:00:00+08:00')
    before=deepcopy(saved)
    result=review(plan,saved,lambda:source,'2026-09-11T22:00:00+00:00')
    old=evaluate(parent,source,'holdout',[origin],plan['models'],{origin:fit_calibration(parent,source,origin)})
    original={r['code']:r for r in old}
    for row in result['rows']:
        if row['status']=='evaluated':assert row['errors']==original[row['code']]['errors']
    old_total=summarize(old,plan['models'])['total']
    assert {k:v for k,v in result['summary']['total'].items() if k!='statuses'}=={k:v for k,v in old_total.items() if k!='statuses'}
    assert result['summary']['total']['statuses']=={'blocked_inputs':34,'evaluated':86}
    assert old_total['statuses']=={'blocked':34,'evaluated':86}
    assert result['decision']==gates(summarize(old,plan['models']),'bias_half',parent['validation'],old)
    assert saved==before
    changed=deepcopy(source)
    code=next(r['code'] for r in result['rows'] if r['status']=='evaluated')
    target=next(r for r in changed['records'] if r['code']==code and r['period']==plan['target'])
    target['bits']['FN86']=0
    altered=review(plan,saved,lambda:changed,'2026-09-11T22:00:00+00:00')
    one=lambda output:next(r for r in output['rows'] if r['code']==code)
    assert one(altered)['forecasts']==one(result)['forecasts']
    assert one(altered)['errors']!=one(result)['errors']
    changed['records'].append(deepcopy(target))
    duplicate=review(plan,saved,lambda:changed,'2026-09-11T22:00:00+00:00')
    assert one(duplicate)['status']=='blocked_actual' and duplicate['positions']==120
