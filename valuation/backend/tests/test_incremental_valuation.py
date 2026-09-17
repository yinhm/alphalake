"""真实冻结请求上的增量重估；标准库刷新/导出另由集成测试覆盖。"""
from copy import deepcopy
import gzip
import json
from pathlib import Path

from tools.batch_valuate_alphalake import BatchPolicy
from tools.incremental_valuation import run_incremental_batch

ROOT = Path(__file__).resolve().parents[2]/'research/method-closure-20260912'


def test_revaluation_changes_failures_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    from tools import incremental_valuation as incremental
    calls = []
    evaluate = incremental.evaluate
    def counted(request):
        calls.append(request.data.code)
        return evaluate(request)
    monkeypatch.setattr(incremental, 'evaluate', counted)
    request = json.loads(gzip.decompress((ROOT/'300866-request.json.gz').read_bytes()))
    data = request.pop('data')
    company = dict(instrument_id=data['facts'][0]['instrument_id'], name='安克创新', symbols=['sz300866'],
                   financial_status='financial_core_complete_requires_policy', missing_core_fields=[])
    readiness = dict(contract_version='alphalake-readiness-v1', universe_scope='frozen standard request',
                     universe_count=1, companies=[company], report_period=data['report_period'], information_as_of=data['information_as_of'])
    policy = BatchPolicy(policy_version='incremental-test', review_note='frozen real input, routing fixture', assignments={'300866':request})
    first = run_incremental_batch(readiness, policy, lambda _:data, None, tmp_path)
    row = first['companies'][0]
    assert row['refresh']['action'] == 'initial_valuation'
    original_id = row['run_id']
    second = run_incremental_batch(readiness, policy, lambda _:data, first, tmp_path)
    assert second['companies'][0]['run_id'] == original_id
    assert second['companies'][0]['refresh']['action'] == 'reused_unchanged'
    assert len(list(tmp_path.glob('*.json'))) == 1
    assert calls == ['300866']
    # 缺项不能沿用上次成功数值；失败保留最后成功证据，恢复不产生重复估值。
    missing = deepcopy(data);missing['windows'] = [r for r in missing['windows'] if r['field'] != 'FN52']
    failed = run_incremental_batch(readiness, policy, lambda _:missing, second, tmp_path)
    assert failed['companies'][0]['status'] == 'blocked_missing_inputs'
    assert 'run_id' not in failed['companies'][0]
    assert failed['companies'][0]['last_success'] == row['last_success']
    recovered = run_incremental_batch(readiness, policy, lambda _:data, failed, tmp_path)
    assert recovered['companies'][0]['run_id'] == original_id
    assert recovered['companies'][0]['refresh']['action'] == 'reused_unchanged'
    assert calls == ['300866']
    # 只有资本效率政策变化，重算并通过既有比较解释现金流变化。
    changed = policy.model_copy(deep=True)
    changed.assignments['300866'].capital_binding.policy.ratio_multiplier *= 1.1
    third = run_incremental_batch(readiness, changed, lambda _:data, recovered, tmp_path)
    row3 = third['companies'][0]
    assert row3['refresh']['action'] == 'recalculated'
    assert row3['run_id'] != original_id
    assert row3['refresh']['comparison']['status'] == 'compared'
    assert row3['refresh']['comparison']['derived']['forecast_unchanged'] is False
    assert row3['refresh']['comparison']['derived']['equity_bridge_inputs_unchanged'] is True
    assert third['automation_counts'] == dict(computable=1, conditional_valuations=1, requires_human_review=1, rejected=0, execution_failed=0)
    # 不能篡改增量索引的签名来复用旧报告。
    bad = deepcopy(first);bad['companies'][0]['last_success']['signature'] = '0'*64
    rejected = run_incremental_batch(readiness, policy, lambda _:data, bad, tmp_path)
    assert rejected['companies'][0]['status'] == 'rejected_input_or_policy'
    # 已保存报告损坏时显式拒绝，不覆盖；也不当作普通输入变化吞掉。
    target = tmp_path/(original_id+'.json')
    stored = json.loads(target.read_text());stored['report']['final']['value_per_share'] += 1
    target.write_text(json.dumps(stored))
    rejected = run_incremental_batch(readiness, policy, lambda _:data, first, tmp_path)
    assert rejected['companies'][0]['status'] == 'rejected_input_or_policy'


def test_cutoff_only_reuse_rechecks_asset_review_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    p = ROOT.parent/'reviewed-assets-20260917/after-request.json.gz'
    request = json.loads(gzip.decompress(p.read_bytes()))
    data = request.pop('data')
    policy = BatchPolicy(policy_version='reviewed', review_note='real reviewed archive', assignments={'300866':request})
    company = dict(instrument_id=data['facts'][0]['instrument_id'], name='安克创新', symbols=['sz300866'],
                   financial_status='financial_core_complete_requires_policy', missing_core_fields=[])
    scan = dict(contract_version='alphalake-readiness-v1', universe_scope='archived reviewed inputs', universe_count=1,
                companies=[company], report_period=data['report_period'], information_as_of=data['information_as_of'])
    first = run_incremental_batch(scan, policy, lambda _:data, None, tmp_path)
    assert first['companies'][0]['refresh']['action'] == 'initial_valuation'
    data = deepcopy(data);data['information_as_of'] = '2026-09-18T00:00:00Z'
    scan['information_as_of'] = data['information_as_of']
    second = run_incremental_batch(scan, policy, lambda _:data, first, tmp_path)
    assert second['companies'][0]['refresh']['action'] == 'reused_unchanged'
    assert second['companies'][0]['refresh']['checked_as_of'].startswith('2026-09-18')
    data['information_as_of'] = '2026-10-18T00:00:00Z';scan['information_as_of'] = data['information_as_of']
    expired = run_incremental_batch(scan, policy, lambda _:data, second, tmp_path)
    assert expired['companies'][0]['status'] == 'rejected_input_or_policy'
    assert 'expired' in expired['companies'][0]['reason']
    assert expired['companies'][0]['last_success'] == first['companies'][0]['last_success']
    assert 'run_id' not in expired['companies'][0]


def test_financial_and_reference_changes_trigger_revaluation(tmp_path, monkeypatch):
    import struct
    from decimal import Decimal
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    request = json.loads(gzip.decompress((ROOT/'300866-request.json.gz').read_bytes()))
    data = request.pop('data')
    policy = BatchPolicy(policy_version='changes', review_note='controlled mutations of real packet', assignments={'300866':request})
    company = dict(instrument_id=data['facts'][0]['instrument_id'], name='安克创新', symbols=['sz300866'],
                   financial_status='financial_core_complete_requires_policy', missing_core_fields=[])
    scan = dict(contract_version='alphalake-readiness-v1', universe_scope='controlled update events', universe_count=1,
                companies=[company], report_period=data['report_period'], information_as_of=data['information_as_of'])
    first = run_incremental_batch(scan, policy, lambda _:data, None, tmp_path)
    # 人工财务更新事件，仅验证触发与归因，不声称是真实新财报。
    changed = deepcopy(data)
    f = next(f for f in changed['facts'] if f['field'] == 'FN86' and f['period'] == changed['report_period'])
    f['bits'] = struct.unpack('<I',struct.pack('<f',float(f['value'])+1000000))[0]
    f['value'] = str(Decimal.from_float(struct.unpack('<f',struct.pack('<I',f['bits']))[0]))
    facts = {f['fact_id']: f for f in changed['facts']}
    row = next(w for w in changed['windows'] if w['field'] == 'FN86')
    row['value'] = str(sum(Decimal(facts[i]['value'])*c for i,c in zip(row['source_fact_ids'],row['input_coefficients'])))
    financial = run_incremental_batch(scan, policy, lambda _:changed, first, tmp_path)
    assert financial['companies'][0]['refresh']['action'] == 'recalculated'
    assert financial['companies'][0]['value_per_share'] != first['companies'][0]['value_per_share']
    # 受控参考值变动，同一行十进制/原始值一起调整；不冒充新取得的行业文件。
    updated = policy.model_copy(deep=True)
    binding = updated.assignments['300866'].capital_binding
    observation = next(r for r in binding.references.observations if r['industry'] == binding.policy.industry)
    value = Decimal(observation['value'])+Decimal('.1')
    observation.update(value=f'{value:.12f}', raw_value=str(value))
    references = run_incremental_batch(scan, updated, lambda _:data, first, tmp_path)
    assert references['companies'][0]['refresh']['action'] == 'recalculated'
    assert references['companies'][0]['value_per_share'] != first['companies'][0]['value_per_share']
