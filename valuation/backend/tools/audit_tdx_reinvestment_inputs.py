"""两期年度资本分量源盘点；源齐全不等于经营分类或可计算ROIC。"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_history import at, available, value

GROUPS = {
    'book_capital_components': ('FN72', 'FN8', 'FN133', 'FN25'),
    'debt_components': ('FN41', 'FN55', 'FN56', 'FN52', 'FN439'),
    'cash_capex_and_amortization': ('FN114', 'FN136', 'FN137', 'FN138'),
    'rd_expensed': ('FN304',),
    'working_capital_reconciliation': ('FN146', 'FN147', 'FN148'),
}
FIELDS = tuple(dict.fromkeys(f for group in GROUPS.values() for f in group))


def audit(source, samples):
    if source['contract_version'] != 'tdx-history-source-v1':
        raise ValueError('unsupported source')
    codes = [s['code'] for s in samples]
    artifacts = {a['file']: a for a in source['artifacts']}
    if not codes or len(set(codes)) != len(codes) or len(artifacts) != len(source['artifacts']):
        raise ValueError('empty/duplicate samples or artifacts')
    index = defaultdict(list)
    for r in source['records']:
        index[r['code'], r['period']].append(r)
    results = []
    for year in (2023, 2024, 2025):
        cutoff = f'{year}-09-01T00:00:00+08:00'
        for code in codes:
            periods = []
            for fiscal in (year-1, year-2):
                period = f'{fiscal}-12-31'
                matches = index[code, period]
                item = dict(period=period, fields={})
                status = 'duplicate_identity' if len(matches)>1 else 'missing_record'
                if len(matches)==1:
                    row = matches[0]
                    item['artifact'] = row['artifact']
                    try:
                        artifact = artifacts[row['artifact']]
                        if artifact['report_period'] != period:
                            raise ValueError('period mismatch')
                        status = 'available' if available(row, artifact)<=at(cutoff) else 'unavailable_at_cutoff'
                    except (KeyError, ValueError, ArithmeticError):
                        status = 'invalid_source'
                for field in FIELDS:
                    entry = dict(status=status)
                    if status == 'available':
                        try:
                            amount = value(row, field)
                            entry = dict(status='nonzero_source' if amount else 'source_zero_ambiguous',
                                         bits=row['bits'][field], source_value=str(amount))
                        except KeyError:
                            entry['status'] = 'missing_field'
                        except (ValueError, ArithmeticError):
                            entry['status'] = 'invalid_source'
                    item['fields'][field] = entry
                periods.append(item)
            results.append(dict(code=code,origin=f'{year}-06-30',information_as_of=cutoff,annual_inputs=periods,
                complete_source_groups={name:all(p['fields'][f]['status']=='nonzero_source' for p in periods for f in fs)
                                        for name,fs in GROUPS.items()},
                classified_reinvestment=None,economic_roic=None))
    summary = {}
    for year in (2023,2024,2025):
        rows = [r for r in results if r['origin'].startswith(str(year))]
        summary[str(year)] = dict(candidates=len(rows),annual_slots=len(rows)*2,
            complete_source_groups={name:sum(r['complete_source_groups'][name] for r in rows) for name in GROUPS},
            field_status={f:dict(Counter(p['fields'][f]['status'] for r in rows for p in r['annual_inputs'])) for f in FIELDS})
    return dict(summary=summary,results=results,
        boundary='later_acquired_source_only_not_strict_PIT; two_FY_not_H1_TTM; source_units_not_converted; '
        'zeros_unresolved; no_classified_capital_reinvestment_ROIC_growth_or_valuation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('study',type=Path);parser.add_argument('snapshot',type=Path)
    args = parser.parse_args(); config = args.study.read_bytes(); raw = args.snapshot.read_bytes()
    source = json.loads(raw)
    if source['study_sha256'] != hashlib.sha256(config).hexdigest():
        raise ValueError('study hash differs')
    result = audit(source,json.loads(config)['samples'])
    result['evidence'] = dict(source_adapter_sha256=hashlib.sha256(Path(__file__).with_name('tdx_research_source.py').read_bytes()).hexdigest(), snapshot_sha256=hashlib.sha256(raw).hexdigest(),study_sha256=source['study_sha256'],
        tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        helper_sha256=hashlib.sha256(Path(__file__).with_name('backtest_tdx_history.py').read_bytes()).hexdigest())
    print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
