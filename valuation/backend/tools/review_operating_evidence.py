"""批量整理最近五年标准经营证据，与网页输入对账；不选择预测参数。"""
import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3
import time

from tools.export_alphalake_sqlite import FIELDS as NATIVE_FIELDS, digest, query_standard_inputs
from tools.review_profit_asset_scope import flow_terms

DEPRECIATION = ('depreciation_depletion', 'intangible_amortization',
    'deferred_expense_amortization', 'right_of_use_depreciation')
WORKING_CAPITAL = ('inventory_decrease_cashflow', 'operating_receivables_decrease_cashflow',
    'operating_payables_increase_cashflow')
COMPARABLE = ('revenues', 'ebit', 'capex', 'r_and_d_expense')
FIELDS = tuple(NATIVE_FIELDS[f][0] for f in COMPARABLE)+DEPRECIATION+WORKING_CAPITAL+('operating_cash_flow_cumulative',)


def projection(end):
    base_year = end.year if end.month == 12 else end.year-1
    periods = {date(y,12,31).isoformat() for y in range(base_year-4,base_year+1)}
    if end.month != 12:
        periods.update(end.replace(year=end.year-i).isoformat() for i in range(3))
    return {field:sorted(periods) for field in FIELDS}


def audit(connection, facts, tickers, end):
    if (not tickers or len(set(tickers)) != len(tickers)
            or any(not re.fullmatch(r'(?:SHSE|SZSE):[0-9]{6}',t) for t in tickers)):
        raise ValueError('unique SH/SZ tickers required')
    marks = ','.join('?' for _ in tickers)
    companies = {r['ticker']:dict(r) for r in connection.execute(
        f'SELECT ticker,company_name,period_date_annual FROM companies WHERE ticker IN ({marks})',tickers)}
    if set(companies) != set(tickers):
        raise ValueError('security outside published scope')
    annual = {(r['ticker'],r['fy_offset']):dict(r) for r in connection.execute(
        f'SELECT * FROM financials_annual WHERE ticker IN ({marks}) AND fy_offset BETWEEN 0 AND 4',tickers)}
    ttm = {r['ticker']:dict(r) for r in connection.execute(
        f'SELECT * FROM financials_ttm WHERE ticker IN ({marks})',tickers)}
    requested = projection(end)
    periods = sorted(next(iter(requested.values())))
    grouped = {ticker:{p:dict(values_cny={},source_record=None) for p in periods} for ticker in tickers}
    for fact in facts:
        ticker, period, field = fact['ticker'], fact['period'], fact['field']
        expected = {3:'Q1',6:'H1',9:'9M',12:'FY'}.get(date.fromisoformat(period).month)
        if (ticker not in grouped or field not in requested or period not in requested[field]
                or (fact['unit'],fact['period_type'],fact['statement_scope']) != ('CNY',expected,'provider_default')):
            raise ValueError('Unrequested or incompatible operating evidence')
        value = Decimal(fact['value'])
        if not value.is_finite():
            raise ValueError('Nonfinite operating evidence')
        row = grouped[ticker][period]
        if field in row['values_cny']:
            raise ValueError('Duplicate operating evidence')
        record = {k:fact[k] for k in ('instrument_id','source_record_id','artifact_sha256','available_at',
            'announcement_source','source_observed_at')}
        if row['source_record'] is not None and row['source_record'] != record:
            raise ValueError('Conflicting operating source record')
        row['source_record'] = record
        row['values_cny'][field] = str(value)
    results = []
    for ticker in tickers:
        source = grouped[ticker]
        def window(field, terms):
            amounts = [source[p]['values_cny'].get(field) for p,_ in terms]
            return None if any(v is None for v in amounts) else sum(
                (Decimal(v)*sign for v,(_,sign) in zip(amounts,terms,strict=True)),Decimal(0))
        def observed(terms):
            values = {field:window(field,terms) for field in FIELDS}
            revenue = values['revenue_cumulative']
            return dict(terms=[dict(period=p,coefficient=c) for p,c in terms],
                values_cny={k:str(v) if v is not None else None for k,v in values.items()},
                reported_ebit_margin=str(values['reported_ebit']/revenue)
                    if revenue is not None and revenue > 0 and values['reported_ebit'] is not None else None,
                research_expense_to_revenue=str(values['research_and_development_expense']/revenue)
                    if revenue is not None and revenue > 0 and values['research_and_development_expense'] is not None else None)
        base_year = end.year if end.month == 12 else end.year-1
        if companies[ticker]['period_date_annual'][:10] != f'{base_year}-12-31':
            raise ValueError('Annual consumer period mismatch')
        history = [dict(year=y,**observed([(f'{y}-12-31',1)])) for y in range(base_year-4,base_year+1)]
        current = observed(flow_terms(end))
        comparisons = []
        for period, native in [(f'{base_year-i}-12-31',annual.get((ticker,i))) for i in range(5)]+[('TTM',ttm.get(ticker))]:
            values = current['values_cny'] if period == 'TTM' else source[period]['values_cny']
            for target in COMPARABLE:
                field = NATIVE_FIELDS[target][0]
                expected = float(Decimal(values[field])/Decimal(1000000)) if values.get(field) is not None else None
                actual = native.get(target) if native is not None else None
                comparisons.append(dict(period=period,field=field,source_million_cny=expected,
                    sqlite_million_cny=actual,status=('matched' if expected is not None else 'missing_both')
                        if native is not None and expected == actual else 'missing_consumer_row' if native is None else 'different'))
        known = [f for f in DEPRECIATION if current['values_cny'][f] is not None]
        results.append(dict(ticker=ticker,company_name=companies[ticker]['company_name'],
            source_periods=source,annual=history,current_ttm=current,
            native_comparisons=comparisons,
            depreciation=dict(standard_components_present=known,
                standard_components_missing=[f for f in DEPRECIATION if f not in known],
                native_total_million_cny=ttm[ticker].get('d_a') if ticker in ttm else None,
                status='standard_components_present_native_total_not_supplied' if known and
                    ttm.get(ticker,{}).get('d_a') is None else 'components_require_scope_review',
                boundary='分量不自动加总：折旧/摊销范围、租赁、重复列示与现金资本开支范围须核对；SQLite空值不等于TDX没有数据'),
            budget_boundary='非研发负净投入可来自折旧超过资本支出、营运资本回收或资产处置；三项经营现金流调整不是完整非现金营运资本，不能据此还原FCFF或自动批准未来预算',
            forecast_approval='not_inferred_from_historical_facts'))
    return dict(contract='native-operating-evidence-v1',unit='CNY',companies=results,
        scope=dict(tickers=tickers,report_period=end.isoformat(),annual_years=5,automatic_adoption=False),
        comparisons=dict(matched=sum(c['status']=='matched' for r in results for c in r['native_comparisons']),
            missing_both=sum(c['status']=='missing_both' for r in results for c in r['native_comparisons']),
            differences=[dict(ticker=r['ticker'],**c) for r in results for c in r['native_comparisons']
                if c['status'] not in ('matched','missing_both')]),
        boundary='TDX标准事实及其窗口派生；报表EBIT不同于研发调整利润率，历史投入不是未来预算；不写财务事实、估值假设或网页默认')


def main():
    from data_sources.paths import workspace_path
    from data_sources.us_cn_hk_db import snapshot_metadata
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--source-database',type=Path,required=True)
    parser.add_argument('--ticker',action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(workspace_path('derived').resolve()):
        parser.error('new output under workspace/derived required')
    started = time.monotonic()
    hashes = dict(sqlite_sha256=digest(args.database),source_database_sha256=digest(args.source_database))
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory = sqlite3.Row
        metadata = snapshot_metadata(connection)
        if metadata is None:
            raise ValueError('current native SQLite required')
        end = date.fromisoformat(metadata['report_period'])
        rows = query_standard_inputs(connection,projection(end),args.source_database,tickers=args.ticker)
        result = audit(connection,rows,args.ticker,end)
    if hashes != dict(sqlite_sha256=digest(args.database),source_database_sha256=digest(args.source_database)):
        raise ValueError('Source changed during evidence review')
    result.update(**hashes,information_as_of=metadata['information_as_of'],
        reference_snapshot_id=metadata['reference_snapshot_id'],implementation_sha256=digest(__file__),
        elapsed_seconds=time.monotonic()-started)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'companies'},ensure_ascii=False))


if __name__ == '__main__':
    main()
