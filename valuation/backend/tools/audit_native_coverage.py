"""从主库和原生SQLite诊断通用缺口；不运行DCF、不写事实或更改准入。"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from data_sources import us_cn_hk_db as db
from tools.export_alphalake_sqlite import CONTRACT, digest

# 当前范围政策，只匹配已核验Damodaran分类；不按公司名称猜行业。
FINANCIAL_INDUSTRIES = frozenset({
    'Bank (Money Center)', 'Banks (Regional)', 'Brokerage & Investment Banking',
    'Financial Svcs. (Non-bank & Insurance)', 'Insurance (General)',
    'Insurance (Life)', 'Insurance (Prop/Cas.)', 'Investments & Asset Management',
})

TDX_FINANCIAL_NODES = {
    'X500101':'国有大型银行', 'X500102':'股份制银行', 'X500201':'城商行', 'X500202':'农商行',
    'X5101':'证券', 'X5102':'保险', 'X510301':'非货币银行', 'X510302':'信托',
    'X510303':'投资管理', 'X510304':'期货', 'X510305':'租赁', 'X510306':'金融控股', 'X510399':'其他金融服务',
}


def company_scope(company, reference):
    all_members = company.get('industry_memberships') or []
    members = [m for m in all_members if m['source'] == 'damodaran' and m['taxonomy_code'] == 'damodaran_industry_2026']
    tdx_financial = any(m['source']=='tdx' and m['taxonomy_code']=='tdx_shenwan_industry'
                        and TDX_FINANCIAL_NODES.get(m['node_code'])==m['node_name'] for m in all_members)
    if reference.get('status') != 'verified_source' or len(members) != 1:
        return ('outside_financial_scope' if tdx_financial else 'unresolved_industry_scope'), all_members
    if tdx_financial and members[0]['node_name'] not in FINANCIAL_INDUSTRIES:
        return 'unresolved_industry_scope', all_members
    return ('outside_financial_scope' if members[0]['node_name'] in FINANCIAL_INDUSTRIES
            else 'nonfinancial_by_reference'), all_members


def gap_category(cell):
    if cell['field'] == 'r_and_d_expense':
        if cell['series'] == 'quarterly':
            return 'research_ttm'
        return 'research_history' if cell['offset'] > 0 else 'research_base_year'
    if cell['field'] in ('revenues', 'ebit'):
        return 'operating_inputs'
    if cell['field'] in ('mv_equity_listing', 'fx_listing_to_reporting'):
        return 'market_capital_inputs'
    return 'equity_bridge_or_other_adjustment'


def audit(conn, readiness):
    metadata = dict(conn.execute('SELECT key,value FROM metadata'))
    if metadata.get('contract') != CONTRACT or readiness['contract_version'] != 'alphalake-readiness-v3':
        raise ValueError('current snapshot and readiness contracts required')
    if metadata['report_period'] != readiness['report_period'] or datetime.fromisoformat(metadata['information_as_of']) != datetime.fromisoformat(readiness['information_as_of']):
        raise ValueError('snapshot/readiness period or cutoff mismatch')
    companies = {c['instrument_id']: c for c in readiness['companies']}
    if len(companies) != readiness['universe_count']:
        raise ValueError('incomplete/duplicate readiness universe')
    reference = readiness['company_industry_reference']
    results, categories, scope_counts, admissions = [], {}, Counter(), Counter()
    candidates = list(conn.execute('SELECT code,instrument_id,status FROM export_universe ORDER BY candidate'))
    ids = [c['instrument_id'] for c in candidates]
    if len(set(ids)) != len(ids) or any(i not in companies for i in ids):
        raise ValueError('snapshot universe differs from source identities')
    for candidate in candidates:
        company = companies[candidate['instrument_id']]
        scope, membership = company_scope(company, reference)
        scope_counts[scope] += 1
        row = dict(instrument_id=candidate['instrument_id'], code=candidate['code'], name=company['name'],
                   scope=scope, industry_evidence=membership, export_status=candidate['status'])
        if candidate['status'] != 'exported':
            row.update(admission='blocked_security_identity', gaps=[])
        else:
            market = {'XSHG':'SHSE', 'XSHE':'SZSE'}.get(company['exchange_mic'])
            if not market or company['symbols'] != [('sh' if market=='SHSE' else 'sz')+candidate['code']]:
                raise ValueError('snapshot/readiness security identity mismatch')
            ticker = market+':'+candidate['code']
            diagnostic = db.native_compatibility(conn, ticker)
            if diagnostic is None:
                raise ValueError('missing native diagnostic: '+ticker)
            gaps = [dict(category=gap_category(c), requirement=group, **{k:v for k,v in c.items() if k!='evidence'})
                    for group in ('required_missing','conditional_missing') for c in diagnostic[group]]
            row.update(ticker=ticker, admission=diagnostic['status'], gaps=gaps,
                blockers=diagnostic['blockers'], adjustment_selection=diagnostic['adjustment_selection'],
                optional_history_missing=len(diagnostic['optional_history_missing']),
                uses_market_proxy=diagnostic['market_proxy'] is not None,
                partial_asset_proxy_cells=len(diagnostic['exported_asset_proxies']))
            for category in {g['category'] for g in gaps}:
                counts = categories.setdefault(category, Counter())
                counts[scope] += 1
        admissions[(scope, row['admission'])] += 1
        results.append(row)
    return dict(contract_version='alphalake-native-coverage-v1', report_period=metadata['report_period'],
        information_as_of=metadata['information_as_of'], source_database_sha256=metadata['source_database_sha256'],
        source_universe=readiness['universe_count'], snapshot_candidates=len(candidates),
        full_source_universe=set(ids)==set(companies), scope_policy='nonfinancial-tdx-damodaran-2026-v1',
        scope_counts=dict(scope_counts), admission_by_scope=[dict(scope=s,admission=a,companies=n) for (s,a),n in sorted(admissions.items())],
        missing_categories={k:dict(v) for k,v in sorted(categories.items())}, companies=results, valuations_run=0,
        boundary='原生默认调整的数据准入；行业映射是范围代理，不证明无金融兼营；分类缺失保留待审。缺标准事实不等于TDX缺数；本报告不推断上市前、源零或公告歧义根因。类别可重叠。',
        closure_attribution='未从快照推断自动/人工闭合数；需要审核事件与前后版本依据')


def reference_coverage(packet):
    from data_sources.alphalake_wacc import ReferenceSnapshot
    s = ReferenceSnapshot.model_validate(packet)
    cutoff = s.information_as_of.date()
    return dict(contract=s.contract_version, releases=s.releases,
        observation_ages_days={name:sorted({(cutoff-datetime.fromisoformat(r['observation_date']).date()).days for r in getattr(s,name)})
                              for name in ('country_risk','industry_stats','yield_curve','credit_spreads')},
        industry_regions=sorted({r['sample_region'] for r in s.industry_stats}),
        industry_metrics=sorted({r['metric_code'] for r in s.industry_stats}),
        country_metrics=sorted({r['metric_code'] for r in s.country_risk}),
        boundary='版本可用不等于满足消费政策的新鲜度；未改变原生输入或选择Global替代US。',
        native_gaps=['原生默认US行业参考未包含在本参考出口', '国家边际税率未包含在本参考出口',
                     '公司行业选择需绑定主库分类版本', '信用利差与原生债务成本分支须明确对齐',
                     '无风险利率显式请求优先；自动取值需规定期限、方法和年龄限制'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True, help='原生SQLite；可为固定样本或全范围候选')
    parser.add_argument('--source-database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--alphalake', type=Path, default=Path(__file__).resolve().parents[3]/'alphalake')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    source_hash, snapshot_hash = digest(args.source_database), digest(args.database)
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA cache_size=-4096')
        metadata = dict(conn.execute('SELECT key,value FROM metadata'))
        if metadata.get('source_database_sha256') != source_hash:
            raise ValueError('SQLite must be exported from this unchanged source database')
        def query(command, extra):
            with tempfile.TemporaryFile(mode='w+') as stream:
                subprocess.run([str(args.alphalake.resolve()), command, str(args.source_database.resolve()),
                                '--as-of', metadata['information_as_of'], *extra], stdout=stream, check=True, timeout=600)
                stream.seek(0)
                return json.load(stream)
        report = audit(conn, query('valuation-readiness', ['--period',metadata['report_period']]))
        report['reference_coverage'] = reference_coverage(query('export-wacc-references', ['--latest']))
    if digest(args.source_database) != source_hash or digest(args.database) != snapshot_hash:
        raise ValueError('source changed during audit')
    report.update(sqlite_sha256=snapshot_hash, auditor_sha256=digest(__file__), binary_sha256=digest(args.alphalake))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('companies','reference_coverage')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
