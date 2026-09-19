"""一次性转换输入的来源和经济内容核验；不提供旧契约运行兼容。"""
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CURRENT = ROOT/'valuation/research/current-contract-20260919'


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def snapshots(value):
    if isinstance(value, dict):
        if value.get('contract_version', '').startswith('alphalake-valuation-v'):
            yield value
        else:
            for child in value.values():
                yield from snapshots(child)
    elif isinstance(value, list):
        for child in value:
            yield from snapshots(child)


def financial_content(snapshot):
    # 仅排除本次契约明确改变的命名、来源栏和股本期间标签，其余逐项比较。
    result = {k:v for k,v in snapshot.items() if k not in ('contract_version','facts','windows')}
    for group in ('facts','windows'):
        result[group] = []
        for row in snapshot[group]:
            normalized = {k:v for k,v in row.items() if k not in ('field','source','source_provider_field')}
            if group == 'facts' and row['canonical_field'] == 'total_shares':
                normalized.pop('period_type')
            result[group].append(normalized)
    return json.dumps(result,sort_keys=True,ensure_ascii=False)


def requests(value):
    if isinstance(value, dict):
        if 'data' in value and 'policy' in value:
            yield value
        else:
            for child in value.values():
                yield from requests(child)
    elif isinstance(value, list):
        for child in value:
            yield from requests(child)


def policy_content(request):
    result = {k:v for k,v in request.items() if k not in ('data','policy')}
    policy = request['policy']
    result['policy'] = {k:v for k,v in policy.items() if k != 'asset_addbacks'}
    if 'asset_addbacks' in policy:
        names = {row['field']:row['canonical_field'] for row in request['data']['facts']}
        result['policy']['asset_addbacks'] = [dict(rule,field=names[rule['field']]) for rule in policy['asset_addbacks']]
    return json.dumps(result,sort_keys=True,ensure_ascii=False)


def test_current_inputs_preserve_source_evidence_and_financial_content():
    receipt = read(CURRENT/'receipt.json')
    assert receipt['purpose'] == 'current_contract_inputs_not_saved_valuation_runs'
    assert len(receipt['records']) == 12
    for entry in receipt['records']:
        source, target = ROOT/entry['source'], CURRENT/entry['file']
        assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['source_sha256']
        assert hashlib.sha256(target.read_bytes()).hexdigest() == entry['sha256']
        source_body, target_body = read(source), read(target)
        assert {policy_content(r) for r in requests(source_body)} == {policy_content(r) for r in requests(target_body)}
        old, current = list(snapshots(source_body)), list(snapshots(target_body))
        assert old and current
        assert {financial_content(s) for s in old} == {financial_content(s) for s in current}
        for snapshot in current:
            assert snapshot['contract_version'] == 'alphalake-valuation-v2'
            for row in snapshot['facts'] + snapshot['windows']:
                assert row['field'] == row['canonical_field']
            for fact in snapshot['facts']:
                assert fact['source'] == 'tdx' and fact['source_provider_field']
                if fact['canonical_field'] == 'total_shares':
                    assert fact['period_type'] == 'instant'
