"""研发连续年度源输入盘点；不计算资本化、增长或标准事实。"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_history import at, available, value


def audit(source, samples):
    if source['contract_version'] != 'tdx-history-source-v1':
        raise ValueError('unsupported source')
    codes = [s['code'] for s in samples]
    if not codes or len(set(codes)) != len(codes):
        raise ValueError('empty or duplicate samples')
    artifacts = {a['file']: a for a in source['artifacts']}
    if len(artifacts) != len(source['artifacts']):
        raise ValueError('duplicate artifacts')
    index = defaultdict(list)
    for r in source['records']:
        index[r['code'], r['period']].append(r)
    results = []
    for origin in (2023, 2024, 2025):
        cutoff = f'{origin}-09-01T00:00:00+08:00'
        for code in codes:
            inputs = []
            # Last completed FY plus five earlier FYs: enough for five-year amortization.
            for year in range(origin-1, origin-7, -1):
                period = f'{year}-12-31'
                matches = index[code, period]
                item = dict(period=period, status='missing_record')
                if len(matches) > 1:
                    item['status'] = 'duplicate_identity'
                elif matches:
                    row = matches[0]
                    item['artifact'] = row['artifact']
                    try:
                        artifact = artifacts[row['artifact']]
                        if artifact['report_period'] != period:
                            raise ValueError('artifact period differs')
                        if available(row, artifact) > at(cutoff):
                            item['status'] = 'unavailable_at_cutoff'
                        elif 'FN304' not in row['bits']:
                            item['status'] = 'missing_field'
                        else:
                            amount = value(row, 'FN304')
                            item.update(bits=row['bits']['FN304'], value_cny=str(amount))
                            item['status'] = ('positive_source' if amount > 0 else
                                              'source_zero_ambiguous' if amount == 0 else 'negative_source')
                    except (ValueError, KeyError, ArithmeticError):
                        item['status'] = 'invalid_source'
                inputs.append(item)
            results.append(dict(code=code, origin=f'{origin}-06-30', information_as_of=cutoff,
                                annual_inputs=inputs,
                                complete_by_life={str(n): all(r['status'] == 'positive_source' for r in inputs[:n+1])
                                                  for n in (3, 5)}))
    summary = {}
    for origin in (2023, 2024, 2025):
        rows = [r for r in results if r['origin'].startswith(str(origin))]
        summary[str(origin)] = dict(candidates=len(rows),
            complete_source_by_life={str(n): sum(r['complete_by_life'][str(n)] for r in rows) for n in (3, 5)},
            annual_slot_status=dict(Counter(i['status'] for r in rows for i in r['annual_inputs'])))
    return dict(summary=summary, results=results,
                boundary='source_only_not_semantically_audited_or_strict_PIT; last_FY_not_H1_TTM; '
                         '3_and_5_years_are_inventory_windows_not_adopted_lives; no_growth_or_valuation')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study', type=Path)
    parser.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    study_bytes = args.study.read_bytes()
    raw = args.snapshot.read_bytes()
    source = json.loads(raw)
    if source['study_sha256'] != hashlib.sha256(study_bytes).hexdigest():
        raise ValueError('study hash differs')
    result = audit(source, json.loads(study_bytes)['samples'])
    result['evidence'] = dict(source_adapter_sha256=hashlib.sha256(Path(__file__).with_name('tdx_research_source.py').read_bytes()).hexdigest(), snapshot_sha256=hashlib.sha256(raw).hexdigest(),
        study_sha256=source['study_sha256'], tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_helper_sha256=hashlib.sha256(Path(__file__).with_name('backtest_tdx_history.py').read_bytes()).hexdigest())
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
