"""显式迁移旧估值请求；只转换已有标准字段标识，不重新解释TDX源数值。"""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re


def upgrade_legacy(value):
    """用于历史证据复验；返回新对象，原归档及旧运行ID不改写。"""
    value = deepcopy(value)
    if isinstance(value, list):
        return [upgrade_legacy(v) for v in value]
    if not isinstance(value, dict):
        return value
    snapshot = value.get('data', value)
    if isinstance(snapshot, dict) and snapshot.get('contract_version') == 'alphalake-valuation-v1':
        names = {}
        for row in snapshot['facts'] + snapshot['windows']:
            old, canonical = row['field'], row['canonical_field']
            if not re.fullmatch(r'[a-z][a-z0-9_]*', canonical):
                raise ValueError('missing canonical identity in legacy snapshot')
            if old in names and names[old] != canonical:
                raise ValueError('ambiguous legacy canonical identity')
            names[old] = canonical
        for row in snapshot['facts']:
            row['source_provider_field'] = row['field']
            row['source'] = 'tdx'
            if row['canonical_field'] == 'total_shares':
                row['period_type'] = 'instant'
        for row in snapshot['facts'] + snapshot['windows']:
            row['field'] = names[row['field']]
        snapshot['contract_version'] = 'alphalake-valuation-v2'
        if snapshot is not value:
            for rule in value.get('policy', {}).get('asset_addbacks', []):
                if rule['field'] not in names:
                    raise ValueError('legacy asset policy has no standard field evidence')
                rule['field'] = names[rule['field']]
        return value
    return {k: upgrade_legacy(v) for k, v in value.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    raw = args.input.read_bytes()
    result = upgrade_legacy(json.loads(raw))
    # 不覆盖旧证据，也不继承旧run ID作为新运行。
    if isinstance(result, dict) and 'run_id' in result:
        raise ValueError('migrate the request, not a saved valuation run')
    with args.output.open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(dict(source_sha256=hashlib.sha256(raw).hexdigest(),
                         output_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))


if __name__ == '__main__':
    main()
