"""归档主库15个位置重放；零事实分母、实际查询和未来披露拒绝。"""
from copy import deepcopy
from datetime import datetime
import gzip
import json
from pathlib import Path

from tools.audit_standard_history_five import audit
from tools.migrate_standard_contract import upgrade_legacy
from data_sources.alphalake import AlphaLakeRequest
from api.alphalake import ENGINE_REVISION, runtime_versions
from data_sources.alphalake import content_hash


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
        return upgrade_legacy(snapshots[k])
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path/'runs'))
    actual = audit(plan,policy,export)
    # 契约/字段名称与哈希允许变化；分母、财务快照、全部经济输入和报告不得变化。
    assert {k:v for k,v in actual.items() if k!='rows'} == {k:v for k,v in expected.items() if k!='rows'}
    mapping={w['field']:w['canonical_field'] for row in expected['rows'] for w in row['snapshot']['windows']}
    for before,after in zip(expected['rows'],actual['rows'],strict=True):
        assert (before['code'],before['period'],before['status']) == (after['code'],after['period'],after['status'])
        assert after['snapshot'] == upgrade_legacy(before['snapshot'])
        if 'run' not in before:
            assert after['missing'] == [item.replace(item.split('/')[-1],mapping.get(item.split('/')[-1],item.split('/')[-1])) for item in before['missing']]
            continue
        old,new=deepcopy(before['run']),after['run']
        old['report']['dcf']['implied_roic_projections']=[None]*10
        old['report']['dcf']['implied_roic_terminal']=None
        assert new['report']==old['report']
        assert new['request']==AlphaLakeRequest.model_validate(upgrade_legacy(old['request'])).model_dump(mode='json')
        old['inputs']['prepared_ttm']['provenance']['alphalake_snapshot']=content_hash(new['request']['data'])
        assert new['inputs']==old['inputs']
        assert new['method_assessment']['reinvestment']['implied_roic_status']=='missing_opening_capital'
        assert after['review']['statuses']==before['review']['statuses']
        for x,y in zip(before['review']['results'],after['review']['results'],strict=True):
            for metric_key in ('status','horizon','target_period','metrics','predictions_million_cny','actual_fcff'):
                assert x.get(metric_key)==y.get(metric_key)
            if 'actual_snapshot' in x:
                assert y['actual_snapshot']==upgrade_legacy(x['actual_snapshot'])
                assert y['actual_snapshot_sha256']==content_hash(y['actual_snapshot'])
    assert len(calls)==16  # 15个预测起点，仅一个获准运行查询一个到期实际。
    assert sum(not r['snapshot']['facts'] for r in expected['rows'])==9
    valid=next(r for r in expected['rows'] if 'run' in r)
    snapshots[key(valid['snapshot'])]=deepcopy(valid['snapshot'])
    snapshots[key(valid['snapshot'])]['facts'][0]['available_at']='2099-01-01T00:00:00+00:00'
    calls.clear()
    bad=audit(plan,policy,export)
    assert bad['statuses']=={'blocked':15} and len(calls)==15
    assert 'future or ambiguous disclosure time' in next(r for r in bad['rows'] if r['code']=='300866' and r['period']=='2025-06-30')['reason']
