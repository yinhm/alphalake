"""起点财务输入诊断；不读取目标期实际、不预测、不判定周期行业。"""
import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_history import at, available, operating, quarter_periods
from tools.backtest_tdx_normalized_margin import annual

ROOT = Path(__file__).resolve().parents[3]


def diagnose(protocol, source):
    if source['contract_version'] != 'tdx-history-source-v1':
        raise ValueError('unsupported source')
    samples = [s for s in protocol['samples'] if s['split'] == 'development']
    codes = [s['code'] for s in samples]
    if len(codes) != len(set(codes)) or not codes:
        raise ValueError('unique development samples required')
    artifacts = {a['file']: a for a in source['artifacts']}
    if len(artifacts) != len(source['artifacts']):
        raise ValueError('duplicate artifact')
    index = defaultdict(list)
    for r in source['records']:
        index[(r['code'], r['period'])].append(r)
    origins = sorted({w['origin'] for w in protocol['windows']})
    output = []
    for code in codes:
        for origin in origins:
            end = date.fromisoformat(origin)
            if (end.month, end.day) != (6, 30):
                raise ValueError('H1 origin required')
            cutoff = f'{end.year}-09-01T00:00:00+08:00'
            row = dict(code=code, origin=origin, forecast_as_of=cutoff,
                       comparability='unknown', cycle='unknown', actual_fcff=None,
                       status='blocked', annual_history=[])
            output.append(row)
            try:
                rows = {}
                for period in quarter_periods(end) + [f'{end.year-1}-06-30']:
                    matches = index[(code, period)]
                    if len(matches) != 1:
                        raise ValueError('TTM source missing or duplicated: ' + period)
                    r = matches[0]
                    a = artifacts[r['artifact']]
                    if a['report_period'] != period or available(r, a) > at(cutoff):
                        raise ValueError('TTM source period/cutoff differs: ' + period)
                    rows[period] = r
                current = operating(rows, end)
                row.update(status='available', current=current,
                           current_margin=current['ebit']/current['revenue'] if current['revenue'] > 0 else None,
                           ebit_sign='positive' if current['ebit'] > 0 else 'nonpositive')
            except (ValueError, KeyError, ArithmeticError) as exc:
                row['reason'] = str(exc)
            for year in range(end.year-5, end.year):
                try:
                    observation = annual(index, artifacts, code, year, cutoff)
                    row['annual_history'].append(observation | dict(status='available'))
                except (ValueError, KeyError, ArithmeticError) as exc:
                    row['annual_history'].append(dict(year=year, status='blocked', reason=str(exc)))
            consecutive = 0
            for observation in reversed(row['annual_history']):
                if observation['status'] != 'available':
                    break
                consecutive += 1
            row['consecutive_complete_years'] = consecutive
    return dict(boundary='descriptive_source_research_not_model_admission_or_strict_PIT',
                amount_unit='million_CNY', companies=len(codes), results=output,
                by_origin={o: dict(pairs=sum(r['origin'] == o for r in output),
                    statuses=dict(Counter(r['status'] for r in output if r['origin'] == o)),
                    complete_years=dict(Counter(str(r['consecutive_complete_years']) for r in output if r['origin'] == o)),
                    ebit_signs=dict(Counter(r.get('ebit_sign', 'unknown') for r in output if r['origin'] == o))) for o in origins})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('protocol', type=Path)
    args = parser.parse_args()
    raw = args.protocol.read_bytes()
    p = json.loads(raw)
    data = (ROOT/p['development_snapshot']).read_bytes()
    if hashlib.sha256(data).hexdigest() != p['development_snapshot_sha256']:
        raise ValueError('snapshot hash differs')
    result = diagnose(p, json.loads(data))
    result['evidence'] = dict(protocol_sha256=hashlib.sha256(raw).hexdigest(),
        snapshot_sha256=hashlib.sha256(data).hexdigest(), helpers={f: hashlib.sha256((ROOT/'valuation/backend'/f).read_bytes()).hexdigest()
        for f in ('tools/audit_tdx_origin_inputs.py', 'tools/backtest_tdx_history.py', 'tools/backtest_tdx_normalized_margin.py')})
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
