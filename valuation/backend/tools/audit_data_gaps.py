"""按现有估值契约批量回读财务缓存，区分源位置和交付缺口；不写事实或修改政策。"""
import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import subprocess
import tempfile

from tools.export_alphalake_sqlite import FIELDS, DEBT_COMPONENTS, LONG_TERM, digest
from tools.audit_native_coverage import FINANCIAL_INDUSTRIES, TDX_FINANCIAL_NODES


def source_requests(coverage):
    requests = set()
    for company in coverage['companies']:
        if company['scope'] != 'nonfinancial_by_reference':
            continue
        for gap in company['gaps']:
            if gap['category'] == 'market_capital_inputs':
                continue
            field = gap['field']
            if field == 'bv_debt':
                components, basis = DEBT_COMPONENTS, 'instant'
            elif field == 'cross_holdings':
                components, basis = LONG_TERM, 'instant'
            else:
                component, basis, _ = FIELDS[field]
                components = (component,)
            end = date.fromisoformat(gap['period'])
            periods = [end]
            if basis == 'ytd' and gap['series'] == 'ttm' and end.month != 12:
                periods += [date(end.year-1, 12, 31), end.replace(year=end.year-1)]
            if gap['series'] == 'quarterly':
                raise ValueError('use current cumulative TTM coverage, not obsolete quarterly gaps')
            requests.update((company['code'], p.isoformat(), f) for p in periods for f in components)
    return [dict(code=c, period=p, field=f) for c,p,f in sorted(requests)]


def summarize(coverage, probe):
    classification = Counter()
    for company in coverage['companies']:
        if company['scope'] == 'unresolved_industry_scope':
            members = company['industry_evidence']
            reference = [m for m in members if m['source']=='damodaran']
            financial = any(m['source']=='tdx' and TDX_FINANCIAL_NODES.get(m['node_code'])==m['node_name'] for m in members)
            reason = ('missing_exact_reference_classification' if not reference else
                      'source_classification_conflict' if financial and len(reference)==1 and reference[0]['node_name'] not in FINANCIAL_INDUSTRIES else
                      'ambiguous_or_unverified_reference_membership')
            classification[reason] += 1
    return dict(contract='alphalake-data-gap-followup-v1', report_period=coverage['report_period'],
        information_as_of=coverage['information_as_of'], snapshot_candidates=coverage['snapshot_candidates'],
        full_source_universe=coverage['full_source_universe'], scope_counts=coverage['scope_counts'],
        financial_source_cells=dict(Counter(r['status'] for r in probe['results'])),
        classification=dict(classification),
        market_capital_gap_companies=sum(c['scope']=='nonfinancial_by_reference' and any(g['category']=='market_capital_inputs' for g in c['gaps']) for c in coverage['companies']),
        source_audit=probe,
        boundary='源命中不批准证券身份、零披露或标准值；无代码仅指所核验本地包，不证明上游永远缺失。不自动推断行业/国家，不扩大历史取证。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--coverage', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--source-auditor', type=Path, required=True)
    parser.add_argument('--source-database', type=Path, required=True, help='校验与当前清单不同的历史缓存归档收据；不冒称上游最新')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    coverage_hash = digest(args.coverage)
    source_hash = digest(args.source_database)
    coverage = json.loads(args.coverage.read_text())
    if coverage['contract_version'] != 'alphalake-native-coverage-v1':
        parser.error('current native coverage required')
    requests = source_requests(coverage)
    if not requests:
        parser.error('no nonfinancial financial gaps to probe')
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary)/'requests.json'
        path.write_text(json.dumps(requests))
        with tempfile.TemporaryFile(mode='w+') as output:
            subprocess.run([str(args.source_auditor.resolve()), '--cache', str(args.cache.resolve()), '--requests', str(path),
                            '--database', str(args.source_database.resolve())],
                stdout=output, check=True, timeout=600)
            output.seek(0)
            probe = json.load(output)
    if digest(args.coverage) != coverage_hash or digest(args.source_database) != source_hash:
        raise ValueError('coverage/source changed during audit')
    result = summarize(coverage, probe)
    result.update(coverage_sha256=coverage_hash, source_database_sha256=source_hash,
        coverage_source_database_sha256=coverage['source_database_sha256'], source_auditor_sha256=digest(args.source_auditor))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k!='source_audit'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
