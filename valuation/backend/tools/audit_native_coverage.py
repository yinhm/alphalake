"""从主库和原生SQLite诊断通用缺口；不运行DCF、不写事实或更改准入。"""
import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from data_sources import us_cn_hk_db as db
from tools.export_alphalake_sqlite import CONTRACT, digest

from data_sources.valuation_scope import company_scope


def profit_asset_evidence(company, diagnostic):
    """显示收益分量和实际资产代理，不将报表行自动分类或相加扣除。"""
    fields = company.get('fields') or []
    inputs = diagnostic.get('input_contract', {}).get('inputs', [])
    basis = diagnostic.get('input_contract', {}).get('flow_basis')
    series = {'standard_cumulative_TTM': 'ttm', 'standard_annual': 'annual'}.get(basis)
    def consumed(field):
        rows = [r for r in inputs if r['series'] == series and r['offset'] == 0 and r['field'] == field]
        return rows[0] if len(rows) == 1 else {}
    reported = consumed('ebit')
    denominator = Decimal(str(reported['value'])) if reported.get('usable') and reported.get('value') is not None else None
    components = []
    for name in ('interest_income', 'investment_income', 'fair_value_change_income', 'asset_disposal_income'):
        rows = [r for r in fields if r['canonical_field'] == name]
        row = rows[0] if len(rows) == 1 else {}
        value = None
        status = row.get('status', 'missing_component' if not rows else 'ambiguous_component_scope')
        if status == 'complete' and row.get('unit') == 'CNY' and row.get('value') is not None:
            try:
                amount = Decimal(str(row['value']))
                if amount.is_finite():
                    value = amount/Decimal(1000000)
                else:
                    status = 'invalid_component_value'
            except InvalidOperation:
                status = 'invalid_component_value'
        elif status == 'complete':
            status = 'invalid_component_unit_or_value'
        components.append(dict(field=name, status=status, statement_scope=row.get('scope'),
            value_million_cny=str(value) if value is not None else None,
            ratio_to_reported_ebit=str(value/denominator) if value is not None and denominator not in (None, 0) else None))
    available = [r for r in components if r['value_million_cny'] is not None]
    nonzero = [r['field'] for r in available if Decimal(r['value_million_cny']) != 0]
    assets = []
    for name in ('cash_and_marketable_securities', 'cross_holdings', 'minority_interests', 'bv_debt'):
        row = consumed(name)
        assets.append(dict(field=name, usable=row.get('usable', False), value_million_cny=row.get('value'),
            source_status=row.get('source', {}).get('status', 'not_provided')))
    source_conflict = bool(company.get('source_conflicts'))
    return dict(status='source_conflict_requires_review' if source_conflict else
        'components_available_scope_unverified' if len(available) == 4 else 'partial_components_scope_unverified',
        source_conflicts_present=source_conflict,
        native_flow_basis=basis, native_reported_ebit_million_cny=reported.get('value'),
        components=components, nonzero_components=nonzero, current_equity_bridge_inputs=assets,
        automatic_adjustment=False, operating_scope_verified=False)


def gap_category(cell):
    if cell['field'] == 'r_and_d_expense':
        if cell['series'] in ('quarterly', 'ttm'):
            return 'research_ttm'
        return 'research_history' if cell['offset'] > 0 else 'research_base_year'
    if cell['field'] in ('revenues', 'ebit'):
        return 'operating_inputs'
    if cell['field'] in ('mv_equity_listing', 'fx_listing_to_reporting'):
        return 'market_capital_inputs'
    return 'equity_bridge_or_other_adjustment'


def audit(conn, readiness, scope):
    metadata = dict(conn.execute("SELECT key,value FROM metadata WHERE key < 'input_context:' OR key >= 'input_context;'"))
    if metadata.get('contract') != CONTRACT or readiness['contract_version'] != 'alphalake-readiness-v3':
        raise ValueError('current snapshot and readiness contracts required')
    if metadata['report_period'] != readiness['report_period'] or datetime.fromisoformat(metadata['information_as_of']) != datetime.fromisoformat(readiness['information_as_of']):
        raise ValueError('snapshot/readiness period or cutoff mismatch')
    companies = {c['instrument_id']: c for c in readiness['companies']}
    if len(companies) != readiness['universe_count']:
        raise ValueError('incomplete/duplicate readiness universe')
    reference = readiness['company_industry_reference']
    results, categories, scope_counts, admissions = [], {}, Counter(), Counter()
    economic_evidence = Counter()
    if (scope['contract']!='alphalake-export-scope-v1' or scope['source_database_sha256']!=metadata['source_database_sha256']
            or scope['report_period']!=metadata['report_period'] or scope['information_as_of']!=metadata['information_as_of']):
        raise ValueError('Frozen export scope differs from snapshot')
    exported = {r['instrument_id']:r['ticker'].split(':')[-1] for r in db.input_contexts(conn)}
    candidates = [dict(instrument_id=c['instrument_id'],code=exported.get(c['instrument_id']) or
        ((c.get('symbols') or [''])[0][2:] or None),status='exported' if c['instrument_id'] in exported else 'blocked_security_identity')
        for c in scope['companies']]
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
            if scope == 'nonfinancial_by_reference':
                row['profit_asset_evidence'] = profit_asset_evidence(company, diagnostic)
                economic_evidence[row['profit_asset_evidence']['status']] += 1
                economic_evidence['nonzero_listed_returns'] += bool(row['profit_asset_evidence']['nonzero_components'])
            for category in {g['category'] for g in gaps}:
                counts = categories.setdefault(category, Counter())
                counts[scope] += 1
        admissions[(scope, row['admission'])] += 1
        results.append(row)
    return dict(contract_version='alphalake-native-coverage-v1', report_period=metadata['report_period'],
        information_as_of=metadata['information_as_of'], source_database_sha256=metadata['source_database_sha256'],
        source_universe=readiness['universe_count'], snapshot_candidates=len(candidates),
        full_source_universe=set(ids)==set(companies), scope_policy='nonfinancial-tdx-damodaran-2026-v1',
        not_exported_source_candidates=[dict(instrument_id=i, name=companies[i]['name'], symbols=companies[i].get('symbols'))
            for i in sorted(set(companies)-set(ids))],
        profit_asset_evidence_counts=dict(economic_evidence),
        profit_asset_evidence_policy=dict(currency='CNY', money_unit='million_CNY', source_flow_basis='standard_TTM',
            source_query='valuation-readiness.fields', automatic_adjustment=False, operating_scope_verified=False,
            boundary='收益组成分别展示，不自动相加或认作非经营；现金/投资加回、收益剔除、合并资本及少数权益须同范围。四项为零也不证明没有其他收益或兼营；金额比值不是批准阈值，不影响准入或默认。'),
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
        scope='standalone_wacc_reference_export_not_native_sqlite_reference_v4',
        native_reference_completeness_evaluated=False,
        boundary='该出口仅描述本WACC数据包；不能据此推断原生SQLite参考缺项或将Global替换US，实际准入沿原生参考诊断。')


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
        metadata = dict(conn.execute("SELECT key,value FROM metadata WHERE key < 'input_context:' OR key >= 'input_context;'"))
        if metadata.get('source_database_sha256') != source_hash:
            raise ValueError('SQLite must be exported from this unchanged source database')
        def query(command, extra):
            with tempfile.TemporaryFile(mode='w+') as stream:
                subprocess.run([str(args.alphalake.resolve()), command, str(args.source_database.resolve()),
                                '--as-of', metadata['information_as_of'], *extra], stdout=stream, check=True, timeout=600)
                stream.seek(0)
                return json.load(stream)
        scope_path = args.source_database.parent/metadata['scope_receipt']
        if digest(scope_path)!=metadata['scope_sha256']:
            raise ValueError('Frozen scope receipt hash mismatch')
        report = audit(conn, query('valuation-readiness', ['--period',metadata['report_period']]),json.loads(scope_path.read_text()))
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
