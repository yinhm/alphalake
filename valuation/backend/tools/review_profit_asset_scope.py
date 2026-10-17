"""重建标准收益／资产组成并对账原生消费值；不调整利润、不运行DCF。"""
import argparse
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
import json
import math
from pathlib import Path
import sqlite3
import time

from data_sources import us_cn_hk_db as native
from tools.export_alphalake_sqlite import digest, query_standard_inputs
from tools.derive_tdx_assets import LONG_TERM


FLOW_FIELDS = ('reported_ebit', 'profit_before_tax', 'finance_costs', 'interest_expense',
    'interest_income', 'investment_income', 'associates_and_joint_ventures_investment_income',
    'fair_value_change_income', 'asset_disposal_income', 'net_exposure_hedging_income')
BALANCE_FIELDS = ('monetary_funds', 'cash_and_cash_equivalents', 'trading_financial_assets',
    'derivative_financial_assets', 'closing_cash', 'closing_cash_equivalents',
    *LONG_TERM, 'equity_parent', 'noncontrolling_interests', 'total_equity')
PAIRS = {
    'cash_returns': (('cash_and_cash_equivalents',), ('interest_income',)),
    'equity_method_holdings': (('long_term_equity_investments',), ('associates_and_joint_ventures_investment_income',)),
    'financial_instrument_returns': (('trading_financial_assets', 'derivative_financial_assets',
        *LONG_TERM[1:]), ('investment_income', 'fair_value_change_income')),
}


def flow_terms(end):
    return [(end.isoformat(), 1)] if end.month == 12 else [
        (date(end.year-1, 12, 31).isoformat(), 1), (end.isoformat(), 1),
        (end.replace(year=end.year-1).isoformat(), -1)]


def projection(end):
    return {**{field: [p for p, _ in flow_terms(end)] for field in FLOW_FIELDS},
            **{field: [end.isoformat()] for field in BALANCE_FIELDS}}


def source_values(rows, instrument_id, end):
    """同一报告的来源只保存一次；保留缺项，不把源零与缺项混为一谈。"""
    facts, records, requested = {}, {}, projection(end)
    for row in rows:
        field, period = row['field'], row['period']
        if field not in requested or period not in requested[field]:
            raise ValueError('Unrequested source field/period')
        key = (field, period)
        expected = 'instant' if field in BALANCE_FIELDS else {3: 'Q1', 6: 'H1', 9: '9M', 12: 'FY'}[date.fromisoformat(period).month]
        if (key in facts or row['instrument_id'] != instrument_id or row['unit'] != 'CNY'
                or row['period_type'] != expected or row['statement_scope'] != 'provider_default'):
            raise ValueError('Incompatible/duplicate source identity/unit/period/scope')
        amount = Decimal(row['value'])
        if not amount.is_finite():
            raise ValueError('Nonfinite source value')
        facts[key] = amount
        record = {key: row[key] for key in ('source_record_id', 'artifact_sha256', 'available_at',
            'announcement_source', 'source_observed_at')}
        if period in records and records[period] != record:
            raise ValueError('Conflicting source records within a period')
        records[period] = record
    values = {}
    for field in FLOW_FIELDS + BALANCE_FIELDS:
        terms = [(end.isoformat(), 1)] if field in BALANCE_FIELDS else flow_terms(end)
        missing = [p for p, _ in terms if (field, p) not in facts]
        value = None if missing else sum((facts[field, p]*sign for p, sign in terms), Decimal(0))
        values[field] = dict(value_cny=str(value) if value is not None else None, missing_periods=missing)
    return values, records, facts


def rounding_bound(value):
    # 这里只审阅倍率为1的金额；半ULP界限不是经营范围或供应商公式的证明。
    exponent = math.frexp(abs(float(value)))[1]-25 if value else -150
    return Decimal(2) ** max(exponent, -150)


def identity_check(facts, terms):
    if any((field, period) not in facts for field, period, _ in terms):
        return dict(status='missing_components', residual_cny=None, rounding_bound_cny=None)
    residual = sum((facts[f, p]*c for f, p, c in terms), Decimal(0))
    bound = sum((rounding_bound(facts[f, p])*abs(c) for f, p, c in terms), Decimal(0))
    return dict(status='within_source_rounding' if abs(residual) <= bound else 'outside_source_rounding',
        residual_cny=str(residual), rounding_bound_cny=str(bound))


def review_company(rows, context, consumed, end):
    values, records, facts = source_values(rows, context['instrument_id'], end)
    def amount(field):
        value = values[field]['value_cny']
        return Decimal(value) if value is not None else None
    checks = {}
    for field, source in (('ebit', 'reported_ebit'), ('cash_and_marketable_securities', 'cash_and_cash_equivalents'),
                          ('bv_equity', 'equity_parent'), ('minority_interests', 'noncontrolling_interests'),
                          ('cross_holdings', None)):
        known = [amount(f) for f in LONG_TERM if amount(f) is not None] if source is None else []
        source_amount = sum(known, Decimal(0)) if known else None
        if source is not None:
            source_amount = amount(source)
        actual = consumed.get(field)
        if source is None and any(v < 0 for v in known):
            status = 'negative_investment_component_requires_review'
        elif source_amount is None:
            status = 'source_missing'
        elif actual is None:
            status = 'native_missing'
        else:
            status = 'matches' if float(source_amount/Decimal(1000000)) == actual else 'differs'
        checks[field] = dict(status=status, native_million_cny=actual,
            source_known_subtotal_cny=str(source_amount) if source_amount is not None else None,
            missing_components=[f for f in LONG_TERM if amount(f) is None] if source is None else
                ([source] if source_amount is None else []))
    pairs = {}
    for name, (assets, income) in PAIRS.items():
        complete = all(amount(f) is not None for f in assets+income)
        asset_observed = any(amount(f) not in (None, 0) for f in assets)
        return_observed = any(amount(f) not in (None, 0) for f in income)
        pairs[name] = dict(status='incomplete_components' if not complete else
            'asset_and_return_observed' if asset_observed and return_observed else
            'asset_only_observed' if asset_observed else 'return_only_observed' if return_observed else
            'zero_observations_not_absence_proof', asset_nonzero=asset_observed, return_nonzero=return_observed)
    identities = {}
    for name, components in (
            ('pretax_profit_plus_interest_expense', (('interest_expense', -1),)),
            ('pretax_profit_plus_finance_costs', (('finance_costs', -1),)),
            ('pretax_profit_plus_net_interest', (('interest_expense', -1), ('interest_income', 1)))):
        identities[name] = identity_check(facts, [(field, p, c*sign)
            for field, c in (('reported_ebit', 1), ('profit_before_tax', -1), *components)
            for p, sign in flow_terms(end)])
        identities[name]['source_period_checks'] = {p: identity_check(facts,
            [(field, p, c) for field, c in (('reported_ebit', 1), ('profit_before_tax', -1), *components)])
            for p, _ in flow_terms(end)}
    identities['consolidated_equity'] = identity_check(facts,
        [(f, end.isoformat(), c) for f, c in (('total_equity', 1), ('equity_parent', -1), ('noncontrolling_interests', -1))])
    cash_parts = ('cash_and_cash_equivalents', 'closing_cash', 'closing_cash_equivalents')
    cash_check = identity_check(facts,
        [(f, end.isoformat(), c) for f, c in zip(cash_parts, (1, -1, -1))])
    cash_equivalents = amount('closing_cash_equivalents')
    if any(amount(f) is not None and amount(f) < 0 for f in cash_parts):
        overlap = 'negative_cash_component_requires_review'
    elif cash_check['status'] != 'within_source_rounding':
        overlap = 'missing_cash_components' if cash_check['status'] == 'missing_components' else 'cash_components_differ'
    else:
        overlap = 'no_cash_equivalents_reported' if cash_equivalents == 0 else 'cash_equivalent_composition_unknown'
    trading, money, cash = (amount(f) for f in ('trading_financial_assets', 'monetary_funds', 'cash_and_cash_equivalents'))
    hedging = amount('net_exposure_hedging_income')
    asset_boundary = dict(
        cash_equivalent_overlap=dict(status=overlap, reconciliation=cash_check),
        monetary_cash_difference=dict(status='missing_components' if money is None or cash is None else 'difference_not_restriction_classification',
            value_cny=str(money-cash) if money is not None and cash is not None else None),
        trading_assets=dict(status='missing_standard_value' if trading is None else
            'negative_balance_requires_review' if trading < 0 else 'reported_zero' if trading == 0 else 'nonzero_requires_composition_review',
            native_projection='not_separately_projected' if all(checks[f]['status']=='matches'
                for f in ('cash_and_marketable_securities', 'cross_holdings')) else 'native_mapping_requires_review'),
        net_exposure_hedging=dict(status='missing_standard_value' if hedging is None else
            'zero_not_absence_proof' if hedging == 0 else 'nonzero_hedging_line_observed'))
    return dict(ticker=context['ticker'], instrument_id=context['instrument_id'],
        model_scope=context['model_scope']['status'], source_records=records, source_values=values,
        native_reconciliation=checks, observed_pairs=pairs, arithmetic_checks=identities, asset_boundary=asset_boundary,
        asset_disposal_return_observed=amount('asset_disposal_income') not in (None, 0),
        minority_balance_observed=amount('noncontrolling_interests') not in (None, 0))


def audit(connection, rows):
    metadata = native.snapshot_metadata(connection)
    if metadata is None:
        raise ValueError('Current native SQLite snapshot required')
    end = date.fromisoformat(metadata['report_period'])
    contexts = {r['ticker']: r for r in native.input_contexts(connection)}
    companies = list(connection.execute('SELECT * FROM companies ORDER BY ticker'))
    if set(contexts) != {r['ticker'] for r in companies}:
        raise ValueError('Company/context universe mismatch')
    grouped = defaultdict(list)
    for row in rows:
        if row['ticker'] not in contexts:
            raise ValueError('Unpublished source security')
        grouped[row['ticker']].append(row)
    table = 'financials_annual' if end.month == 12 else 'financials_ttm'
    native_rows = {r['ticker']: dict(r) for r in connection.execute('SELECT * FROM '+table+
        (' WHERE fy_offset=0' if end.month == 12 else ''))}
    results, counts, scope_counts = [], defaultdict(Counter), Counter()
    for company in companies:
        ticker = company['ticker']
        context = native.load_input_context(connection, ticker, company)
        scope = context['model_scope']['status']
        scope_counts[scope] += 1
        if scope != 'nonfinancial_by_reference':
            continue
        result = review_company(grouped[ticker], context, native_rows.get(ticker, {}), end)
        for category in ('native_reconciliation', 'observed_pairs', 'arithmetic_checks', 'asset_boundary'):
            for name, check in result[category].items():
                counts[category+':'+name][check['status']] += 1
        for flag in ('asset_disposal_return_observed', 'minority_balance_observed'):
            counts[flag][str(result[flag]).lower()] += 1
        net_interest = result['arithmetic_checks']['pretax_profit_plus_net_interest']['source_period_checks']
        counts['net_interest_source_periods'][
            'all_within_source_rounding' if all(r['status']=='within_source_rounding' for r in net_interest.values()) else
            'missing_components' if any(r['status']=='missing_components' for r in net_interest.values()) else
            'outside_source_rounding'] += 1
        results.append(result)
    return dict(contract='alphalake-profit-asset-scope-v2', report_period=metadata['report_period'],
        information_as_of=metadata['information_as_of'], published_companies=len(companies),
        scope_counts=dict(scope_counts), reviewed_nonfinancial_companies=len(results), counts=dict(counts),
        source_flow_terms=flow_terms(end), balance_date=end.isoformat(), currency='CNY', source_money_unit='CNY',
        native_money_unit='million_CNY', pairing_fields={k:dict(asset_fields=a, return_fields=r) for k,(a,r) in PAIRS.items()},
        automatic_adjustment=False, valuations_run=0, operating_scope_verified=False,
        boundary='组成对账及舍入界限不是经营／非经营分类、供应商公式认证或估值审批；权益法收益包含于投资收益，不重复相减；代理账面值不是市场价值。现金等价物源零须组成核对，不证明所有交易性资产可加回；货币资金差额不是受限额；净敞口套期行零不证明没有套期，套期可能进入其他损益或OCI。未分类和金融保留分母，不进行专项模型审核。',
        companies=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True, help='已发布原生SQLite')
    parser.add_argument('--source-database', type=Path, required=True, help='权威DuckDB')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    started = time.monotonic()
    sqlite_hash, source_hash = digest(args.database), digest(args.source_database)
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA cache_size=-4096')
        metadata = native.snapshot_metadata(connection)
        if metadata is None:
            raise ValueError('Current native SQLite snapshot required')
        rows = query_standard_inputs(connection, projection(date.fromisoformat(metadata['report_period'])), args.source_database)
        report = audit(connection, rows)
    if digest(args.database) != sqlite_hash or digest(args.source_database) != source_hash:
        raise ValueError('Source changed during scope review')
    report.update(sqlite_sha256=sqlite_hash, source_database_sha256=source_hash,
        published_source_database_sha256=metadata['source_database_sha256'],
        financial_content_signature=metadata['financial_content_signature'],
        auditor_sha256=digest(__file__), binary_sha256=digest(Path(__file__).resolve().parents[3]/'alphalake'),
        elapsed_seconds=time.monotonic()-started)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        stream.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'companies'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
