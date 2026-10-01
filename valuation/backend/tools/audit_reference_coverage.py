"""核对既有沪深证券范围的原生参考关联；只读，不选择代理或运行估值。"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3

from data_sources.native_references import load_snapshot, reference_gaps
from data_sources.damodaran_parsers.company_industry_parser import alphalake_company_industry_snapshot
from tools.audit_native_coverage import company_scope
from tools.export_alphalake_sqlite import CONTRACT, digest


def audit(connection, coverage, source_snapshot):
    metadata = dict(connection.execute('SELECT key,value FROM metadata'))
    if metadata.get('contract') != CONTRACT or coverage.get('contract_version') != 'alphalake-native-coverage-v1':
        raise ValueError('current SQLite and coverage contracts required')
    companies = coverage['companies']
    codes = [c['code'] for c in companies]
    if not coverage['full_source_universe'] or len(companies) != coverage['source_universe'] or len(set(codes)) != len(codes):
        raise ValueError('complete unique security denominator required')
    store = load_snapshot(connection)
    releases = {r['release_id']: r for r in store.reference_snapshot['releases']}
    associations = store.reference_snapshot.get('issuer_associations',{})
    source = {r[1]: r for r in connection.execute('SELECT release_id,ticker,industry,country,source_locator FROM reference_company')}
    raw = {r['ticker']: r for r in source_snapshot['companies']}
    if set(raw) != set(source) or len(raw) != len(source_snapshot['companies']):
        raise ValueError('official workbook and published company scope differ')
    for ticker, ref in source.items():
        row = raw[ticker]
        if (releases[ref[0]]['sha256'] != source_snapshot['sha256']
                or (row['industry'],row['country'],row['source_locator']) != ref[2:]):
            raise ValueError('official workbook/published reference mismatch: '+ticker)
    peers = defaultdict(Counter)
    rows = []
    for company in companies:
        ticker = company.get('ticker')
        if not ticker or ticker not in ('SHSE:'+company['code'], 'SZSE:'+company['code']):
            raise ValueError('unambiguous SHSE/SZSE identity required')
        members = company['industry_evidence']
        official = [m for m in members if m['source']=='damodaran' and m['taxonomy_code']=='damodaran_industry_2026']
        ref = source.get(ticker)
        if ref:
            if len(official) != 1:
                raise ValueError('published reference differs from identity association: '+ticker)
            member = official[0]
            if (member['source_release_id'] != ref[0] or member['source_ticker'] != ticker
                    or member['node_name'] != ref[2] or member['source_locator'] != ref[4]
                    or member['artifact_sha256'] != releases[ref[0]]['sha256']):
                raise ValueError('reference lineage mismatch: '+ticker)
        elif ticker in associations:
            a = associations[ticker]
            if a['instrument_id'] != company['instrument_id']:
                raise ValueError('reviewed issuer target identity mismatch: '+ticker)
            c = a['source_company']
            if official:
                if (len(official)!=1 or official[0]['review_sha256']!=a['review_sha256']
                        or official[0]['source_ticker']!=a['source_ticker']
                        or official[0]['artifact_sha256']!=a['workbook_sha256']):
                    raise ValueError('reviewed issuer membership differs: '+ticker)
            else:
                # 旧盘点仅复用证券身份；新增关系必须来自当前主库导出的审核记录。
                members = members + [dict(source='damodaran',taxonomy_code='damodaran_industry_2026',
                    node_name=c['industry'],node_code=c['industry'],source_ticker=a['source_ticker'],
                    source_release_id=a['source_release_id'],artifact_sha256=a['workbook_sha256'],
                    source_locator=a['source_locator'],review_sha256=a['review_sha256'],
                    identity_basis='reviewed_same_legal_issuer_different_share_class')]
            ref = (a['source_release_id'],ticker,c['industry'],c['country'],a['source_locator'])
        elif official:
            raise ValueError('associated source row absent from snapshot: '+ticker)
        scope, _ = company_scope(dict(industry_memberships=members), {'status':'verified_source'})
        nodes = [(m['node_code'],m['node_name']) for m in members
                 if m['source']=='tdx' and m['taxonomy_code']=='tdx_shenwan_industry']
        if scope=='nonfinancial_by_reference':
            for node in set(nodes):
                peers[node][ref[2]] += 1
        gaps = reference_gaps(store, ticker)
        rows.append(dict(ticker=ticker, name=company['name'], scope=scope,
            association_status=('exact_source_security' if ticker in source else 'reviewed_same_issuer' if ref else 'missing_exact_source_security'),
            source_ticker=ticker if ticker in source else associations[ticker]['source_ticker'] if ref else None,
            source_industry=ref[2] if ref else None, source_country=ref[3] if ref else None,
            source_locator=ref[4] if ref else None, reference_missing=gaps, tdx_nodes=nodes))
    unresolved = []
    for row in rows:
        if row['reference_missing'] or row['scope']=='unresolved_industry_scope':
            candidates = Counter()
            for node in set(row['tdx_nodes']):
                candidates.update(peers[node])
            unresolved.append(dict(**row, peer_industry_counts=dict(sorted(candidates.items())),
                candidate_status='not_company_fact_or_automatic_default'))
    pending_nodes = {tuple(n) for r in unresolved if r['scope']=='unresolved_industry_scope' for n in r['tdx_nodes']}
    single_candidate_tickers = {r['ticker'] for r in unresolved
        if r['scope']=='unresolved_industry_scope' and len(r['peer_industry_counts'])==1}
    categories = []
    for node in sorted(pending_nodes):
        members = [r for r in rows if node in r['tdx_nodes']]
        categories.append(dict(node_code=node[0], node_name=node[1],
            peer_industry_counts=dict(sorted(peers[node].items())), members=members,
            unresolved_tickers=[r['ticker'] for r in members if r['scope']=='unresolved_industry_scope'],
            priority_single_candidate_tickers=[r['ticker'] for r in members if r['ticker'] in single_candidate_tickers],
            status='candidate_not_approved'))
    return dict(contract='alphalake-reference-coverage-v1', source_universe=len(rows),
        coverage_source_database_sha256=coverage['source_database_sha256'],
        snapshot_source_database_sha256=metadata['source_database_sha256'],
        reference_snapshot_id=store.reference_snapshot['id'],
        company_workbook_sha256=source_snapshot['sha256'],
        reference_information_as_of=store.reference_snapshot['information_as_of'],
        source_country_counts=dict(Counter(r[3] for r in source.values())),
        scope_counts=dict(Counter(r['scope'] for r in rows)),
        association_counts=dict(Counter(r['association_status'] for r in rows)),
        reference_gap_counts=dict(Counter(g['field'] for r in rows for g in r['reference_missing'])),
        reference_gap_scope_counts=dict(Counter(r['scope'] for r in rows if r['reference_missing'])),
        unresolved_companies=unresolved, industry_categories=categories,
        reference_complete=sum(not r['reference_missing'] for r in rows),
        scope_and_reference_complete=sum(not r['reference_missing'] and r['scope']=='nonfinancial_by_reference' for r in rows),
        valuations_run=0, defaults_changed=False,
        boundary='仅审核参考关联及版本血缘；不是财务完成度、发行人等同性或经济适用性认证。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sqlite')
    parser.add_argument('coverage', help='已有完整范围覆盖盘点；逐条验证参考血缘，不沿用其财务完成度')
    parser.add_argument('company_workbook', help='已归档的官方公司行业表，核对三张工作表及发布值')
    args = parser.parse_args()
    with sqlite3.connect(Path(args.sqlite).resolve().as_uri()+'?mode=ro', uri=True) as conn:
        result = audit(conn, json.loads(Path(args.coverage).read_text()), alphalake_company_industry_snapshot(args.company_workbook))
    result.update(sqlite_sha256=digest(args.sqlite), coverage_sha256=digest(args.coverage))
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
