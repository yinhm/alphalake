"""显式重建双公司适配器的现行契约证据；不覆盖冻结输入或提供运行兼容。"""
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path

from engine.data_dictionary import CompanyValuationInput, RawFinancials, ValuationAssumptions

ROOT = Path(__file__).resolve().parents[3]
CHAIN = ROOT / 'internal/ingest/testdata/valuation-chain-2026'
SOURCE_CHANGE_REVISION = '586677c2ecb0ddb886fe82dc64490e6a5f7159c2'
SOURCE_HASHES = {
    'facts.csv': (
        '681bc21d3324b438173a2c776a976e0262edd1a2520f31201cec4336d3314c17',
        '1cb8ffd2dec29bddb30510c14295ec8c3ef4dc45adb6ebb8d569c2fa31c97814'),
    'windows.csv': (
        '006fa8518d106cd1f642f870f3a8fd7ac56432e6448a82593ce8164210db326e',
        '39ab145ddf4c0f1f10e7f4b2c274c2858159f850897ac76cec78c187dca8e8da'),
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def verify_source_changes():
    """精确逆转已审核的20条源零/3个窗口，复原旧字节哈希；其余来源内容必须不变。"""
    periods = ('2025-03-31', '2025-06-30', '2025-09-30', '2025-12-31',
               '2026-03-31', '2026-06-30')
    added = {('300866', period, field) for period in periods
             for field in ('debt_investments', 'other_debt_investments')}
    added |= {('600519', period, 'trading_financial_assets') for period in periods}
    added |= {('600519', period, 'other_debt_investments') for period in periods[:2]}
    changed = {('300866', '2026-06-30', field)
               for field in ('debt_investments', 'other_debt_investments')}
    changed.add(('600519', '2026-06-30', 'trading_financial_assets'))
    for filename, (old_hash, current_hash) in SOURCE_HASHES.items():
        raw = (CHAIN / filename).read_bytes()
        if digest(raw) != current_hash:
            raise ValueError(f'{filename}: reviewed source hash changed')
        reader = csv.DictReader(io.StringIO(raw.decode()))
        original, seen = [], set()
        for row in reader:
            key = (row['code'], row['period'], row['canonical_field'])
            if filename == 'facts.csv' and key in added:
                if key in seen or row['value'] != '0.0000000000' or row['bits'] != '0':
                    raise ValueError('reviewed added fact is not a unique source zero')
                seen.add(key)
                continue
            if filename == 'windows.csv' and key in changed:
                if key in seen or (row['coverage_status'], row['value'], row['available_inputs']) != (
                        'complete', '0.0000000000', '1'):
                    raise ValueError('reviewed zero window changed')
                seen.add(key)
                row.update(coverage_status='missing_inputs', value='', available_inputs='0')
            original.append(row)
        if seen != (added if filename == 'facts.csv' else changed):
            raise ValueError('reviewed source change set differs')
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=reader.fieldnames, lineterminator='\n')
        writer.writeheader()
        writer.writerows(original)
        if digest(stream.getvalue().encode()) != old_hash:
            raise ValueError('source content outside the reviewed zero additions changed')
    return dict(revision=SOURCE_CHANGE_REVISION, added_zero_facts=len(added),
                missing_to_zero_windows=len(changed), existing_fact_changes=0,
                hashes={name: dict(previous=old, current=current)
                        for name, (old, current) in SOURCE_HASHES.items()})


def rebind_sources(provenance):
    updated = deepcopy(provenance)
    for name, old_hash in provenance.items():
        path = '../supplement-review-2026/resolved.csv' if name == 'resolved.csv' else name
        current_hash = digest((CHAIN / path).read_bytes())
        if name in SOURCE_HASHES:
            if (old_hash, current_hash) != SOURCE_HASHES[name]:
                raise ValueError('adapter provenance does not match reviewed source change')
        elif current_hash != old_hash:
            raise ValueError(f'unreviewed adapter source change: {name}')
        updated[name] = current_hash
    return updated


def add_fields(value, additions):
    for name, default in additions.items():
        if name in value:
            raise ValueError(f'expected frozen contract without {name}')
        value[name] = default


def validated_dump(model, value):
    current = model.model_validate(value).model_dump(mode='json')
    if current != value:
        raise ValueError('unreviewed model contract or default change')
    return current


def rebuild_request(frozen):
    current = deepcopy(frozen)
    inputs = current['inputs']
    add_fields(inputs['prepared_ttm']['financials'], {'consolidated_book_equity': None})
    add_fields(inputs['valuation_assumptions'], {'annual_sales_to_capital': None})
    add_fields(inputs, {'historical_research_expenses': {}})
    add_fields(inputs['industry_data'], {
        'pretax_lease_research_adjusted_operating_margin': None,
        'aftertax_lease_research_adjusted_operating_margin': None})
    inputs['prepared_ttm']['provenance'] = rebind_sources(inputs['prepared_ttm']['provenance'])
    current['inputs'] = validated_dump(CompanyValuationInput, inputs)
    return current


def rebuild(output, recorded_at):
    """只迁移列明的空契约字段和已审核来源哈希，全部数值/警告/缺项保持原值。"""
    if datetime.fromisoformat(recorded_at).utcoffset() is None:
        raise ValueError('aware reconstruction time required')
    source_changes = verify_source_changes()
    records, files = [], {}
    for company in ('anker', 'moutai'):
        source = Path('internal/ingest/testdata') / f'{company}-agent-adapter-2026'
        request_name = 'request.json' if company == 'anker' else 'requests.json'
        for filename in (request_name, 'result.json'):
            raw = (ROOT / source / filename).read_bytes()
            frozen = json.loads(raw)
            current = deepcopy(frozen)
            if filename == 'request.json':
                current = rebuild_request(frozen)
            elif filename == 'requests.json':
                for row in current:
                    row['request'] = rebuild_request(row['request'])
            else:
                current['source_sha256'] = rebind_sources(frozen['source_sha256'])
                if company == 'anker':
                    add_fields(current['prepared_base'], {'consolidated_book_equity': None})
                    current['prepared_base'] = validated_dump(RawFinancials, current['prepared_base'])
                    add_fields(current['assumptions'], {'annual_sales_to_capital': None})
                    current['assumptions'] = validated_dump(ValuationAssumptions, current['assumptions'])
            relative = Path(company) / filename
            body = encode(current)
            files[relative] = body
            records.append(dict(source=(source / filename).as_posix(), source_sha256=digest(raw),
                                file=relative.as_posix(), sha256=digest(body)))
    receipt = dict(purpose='current_adapter_contract_evidence_preserving_frozen_numeric_results',
                   recorded_at=recorded_at, source_changes=source_changes,
                   contract_additions={'financials': {'consolidated_book_equity': None},
                       'valuation_assumptions': {'annual_sales_to_capital': None},
                       'inputs': {'historical_research_expenses': {}},
                       'industry_data': {'pretax_lease_research_adjusted_operating_margin': None,
                           'aftertax_lease_research_adjusted_operating_margin': None}},
                   records=records)
    # 只可写入新目录；旧请求、结果和CSV均为只读输入。
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for relative, body in files.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    (output / 'receipt.json').write_bytes(encode(receipt))
    return receipt


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--recorded-at', required=True)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.output, args.recorded_at), ensure_ascii=False, indent=2))
