"""归档主库15个位置重放；零事实分母、实际查询和未来披露拒绝。"""
from copy import deepcopy
from datetime import datetime
import gzip
import json
from pathlib import Path

from tools.audit_standard_history_five import audit


def test_archived_standard_history_five(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[3]
    source=root/'valuation/research/standard-history-five'
    plan=json.loads((source/'plan.json').read_bytes())
    expected=json.loads(gzip.decompress((source/'result.json.gz').read_bytes()))
    policy=json.loads((root/'valuation/research/standard-history-2025H1/policy.json').read_bytes())
    snapshots={}
    def key(data):
        return data['code'],data['report_period'],datetime.fromisoformat(data['information_as_of'])
    for row in expected['rows']:
        snapshots[key(row['snapshot'])]=row['snapshot']
        for actual in row.get('review',{}).get('results',[]):
            if 'actual_snapshot' in actual:
                data=actual['actual_snapshot'];snapshots[key(data)]=data
    calls=[]
    def export(code,period,cutoff):
        k=code,period,datetime.fromisoformat(cutoff);calls.append(k)
        return deepcopy(snapshots[k])
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path/'runs'))
    assert audit(plan,policy,export)==expected
    assert len(calls)==16  # 15个预测起点，仅一个获准运行查询一个到期实际。
    assert sum(not r['snapshot']['facts'] for r in expected['rows'])==9
    valid=next(r for r in expected['rows'] if 'run' in r)
    snapshots[key(valid['snapshot'])]=deepcopy(valid['snapshot'])
    snapshots[key(valid['snapshot'])]['facts'][0]['available_at']='2099-01-01T00:00:00+00:00'
    calls.clear()
    bad=audit(plan,policy,export)
    assert bad['statuses']=={'blocked':15} and len(calls)==15
    assert 'future or ambiguous disclosure time' in next(r for r in bad['rows'] if r['code']=='300866' and r['period']=='2025-06-30')['reason']
