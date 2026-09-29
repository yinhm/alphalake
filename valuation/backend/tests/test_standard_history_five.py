"""归档主库15个位置重放；零事实分母、实际查询和未来披露拒绝。"""
from copy import deepcopy
from datetime import datetime
import gzip
import json
from pathlib import Path

from tools.audit_standard_history_five import audit
from data_sources.alphalake import AlphaLakeRequest
from api.alphalake import ENGINE_REVISION, runtime_versions
from data_sources.alphalake import content_hash


def test_archived_standard_history_five(tmp_path,monkeypatch):
    root=Path(__file__).resolve().parents[3]
    source=root/'valuation/research/standard-history-five'
    plan=json.loads((source/'plan.json').read_bytes())
    expected=json.loads(gzip.decompress((source/'result.json.gz').read_bytes()))
    policy=json.loads((root/'valuation/research/standard-history-2025H1/policy.json').read_bytes())
    current=json.loads(gzip.decompress((root/'valuation/research/current-contract-20260919/standard-history-five/inputs.json.gz').read_bytes()))
    snapshots={}
    def key(data):
        return data['code'],data['report_period'],datetime.fromisoformat(data['information_as_of'])
    for row in current:
        snapshots[key(row['snapshot'])]=row['snapshot']
        for data in row['actual_snapshots']:
            snapshots[key(data)]=data
    calls=[]
    def export(code,period,cutoff):
        k=code,period,datetime.fromisoformat(cutoff);calls.append(k)
        return snapshots[k]
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path/'runs'))
    actual = audit(plan,policy,export)
    # 契约/字段名称与哈希允许变化；分母、财务快照、全部经济输入和报告不得变化。
    assert {k:v for k,v in actual.items() if k!='rows'} == {k:v for k,v in expected.items() if k!='rows'}
    for before,after,inputs in zip(expected['rows'],actual['rows'],current,strict=True):
        assert (before['code'],before['period'],before['status']) == (after['code'],after['period'],after['status'])
        assert after['snapshot'] == inputs['snapshot']
        if 'run' not in before:
            assert after['missing'] == inputs['missing']
            continue
        old,new=deepcopy(before['run']),after['run']
        old['report']['dcf']['implied_roic_projections']=[None]*10
        old['report']['dcf']['implied_roic_terminal']=None
        # 冻结报告经济结果逐项保持；新增资本/税盾诊断由当前专门回归验证。
        for section,value in old['report'].items():
            if section == 'dcf':
                assert {k:new['report'][section][k] for k in value} == value
            elif section == 'ltm_financials':
                assert {k:v for k,v in new['report'][section].items() if k!='consolidated_book_equity'} == value
            else:
                assert new['report'][section] == value
        assert new['request']==AlphaLakeRequest.model_validate(inputs['request']).model_dump(mode='json')
        old['inputs']['prepared_ttm']['provenance']['alphalake_snapshot']=content_hash(new['request']['data'])
        # 冻结档案早于两个可空行业利润率字段；只补空契约，经济输入仍逐项相等。
        for field in ('pretax_lease_research_adjusted_operating_margin', 'aftertax_lease_research_adjusted_operating_margin'):
            assert field not in old['inputs']['industry_data']
            old['inputs']['industry_data'][field] = None
        # 冻结证据不改写；明确补入后来新增、在该旧请求中未启用的契约字段。
        assert 'historical_research_expenses' not in old['inputs']
        assert 'annual_sales_to_capital' not in old['inputs']['valuation_assumptions']
        old['inputs']['historical_research_expenses'] = {}
        old['inputs']['valuation_assumptions']['annual_sales_to_capital'] = None
        for row in old['inputs']['raw_financials'] + [old['inputs']['prepared_ttm']['financials']]:
            row['consolidated_book_equity'] = None
        assert new['inputs']==old['inputs']
        assert new['method_assessment']['reinvestment']['implied_roic_status']=='missing_opening_capital'
        assert after['review']['statuses']==before['review']['statuses']
        for x,y in zip(before['review']['results'],after['review']['results'],strict=True):
            for metric_key in ('status','horizon','target_period','metrics','predictions_million_cny','actual_fcff'):
                assert x.get(metric_key)==y.get(metric_key)
            if 'actual_snapshot' in x:
                assert y['actual_snapshot'] in inputs['actual_snapshots']
                assert y['actual_snapshot_sha256']==content_hash(y['actual_snapshot'])
    assert len(calls)==16  # 15个预测起点，仅一个获准运行查询一个到期实际。
    assert sum(not r['snapshot']['facts'] for r in expected['rows'])==9
    valid=next(r for r in current if 'request' in r)
    snapshots[key(valid['snapshot'])]=deepcopy(valid['snapshot'])
    snapshots[key(valid['snapshot'])]['facts'][0]['available_at']='2099-01-01T00:00:00+00:00'
    calls.clear()
    bad=audit(plan,policy,export)
    assert bad['statuses']=={'blocked':15} and len(calls)==15
    assert 'future or ambiguous disclosure time' in next(r for r in bad['rows'] if r['code']=='300866' and r['period']=='2025-06-30')['reason']
