"""显式导出AlphaLake标准事实到现有网页SQLite结构；不改变默认数据源。"""
import argparse
import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from data_sources import us_cn_hk_db as target
from tools.derive_tdx_assets import LONG_TERM

# 名称映射只描述目标格式，不建立TDX源编号词典。金额/股数统一除以百万。
FIELDS = {
    'revenues': ('revenue_cumulative', 'ytd', 'CNY'),
    'ebit': ('reported_ebit', 'ytd', 'CNY'),
    'ebitda': ('reported_ebitda', 'ytd', 'CNY'),
    'net_income': ('net_income_parent_ytd', 'ytd', 'CNY'),
    'interest_expense': ('interest_expense', 'ytd', 'CNY'),
    'capex': ('capital_expenditure_cash', 'ytd', 'CNY'),
    'total_tax_expense': ('income_tax_expense', 'ytd', 'CNY'),
    'r_and_d_expense': ('research_and_development_expense', 'ytd', 'CNY'),
    'bv_equity': ('equity_parent', 'instant', 'CNY'),
    'shares_outstanding': ('total_shares', 'instant', 'share'),
    'minority_interests': ('noncontrolling_interests', 'instant', 'CNY'),
    'cash_and_marketable_securities': ('cash_and_cash_equivalents', 'instant', 'CNY'),
    'cross_holdings': ('long_term_equity_investments', 'instant', 'CNY'),
}
EXCHANGES = {'XSHG': 'SHSE', 'XSHE': 'SZSE', 'XBSE': 'BJSE'}
CONTRACT = 'alphalake-sqlite-v5'
DEBT_COMPONENTS = ('short_term_borrowings', 'long_term_borrowings', 'bonds_payable',
                   'current_portion_noncurrent_liabilities', 'lease_liabilities')
ASSET_COMPONENTS = ('monetary_funds', 'cash_and_cash_equivalents', 'trading_financial_assets',
    'noncurrent_assets_due_within_one_year', 'long_term_equity_investments', 'debt_investments',
    'other_debt_investments', 'other_equity_instrument_investments', 'other_noncurrent_financial_assets')
SOURCE_FIELDS = sorted({f[0] for f in FIELDS.values()} | set(DEBT_COMPONENTS) | set(ASSET_COMPONENTS) | {'listed_b_shares','listed_h_shares','profit_before_tax','operating_profit_cumulative','interest_income','investment_income','fair_value_change_income'})
PARTIAL_SCOPES = {
    'cash_and_marketable_securities': 'cash_equivalents_only; short_term_investment_scope_and_overlap_unresolved',
    'cross_holdings': 'known_long_term_investment_components; missing_components_and_valuation_scope_unresolved',
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def quarter(end, offset=0):
    year, q = divmod(end.year * 4 + (end.month // 3 - 1) - offset, 4)
    month = (q + 1) * 3
    return date(year, month, calendar.monthrange(year, month)[1])


def validate_dates(period, asof):
    if period.month % 3 or quarter(period) != period:
        raise ValueError('report period must be a quarter end')
    if asof.utcoffset() is None or asof.date() < period:
        raise ValueError('timezone-aware information cutoff after report period required')


def cell(facts, conflicts, instrument, end, column, annual):
    """只读取标准金额；累计差分不是供应商TTM，也不对每股值求和。"""
    if column == 'bv_debt':
        components, basis, unit = DEBT_COMPONENTS, 'instant', 'CNY'
    elif column == 'cross_holdings':
        components, basis, unit = LONG_TERM, 'instant', 'CNY'
    elif column in FIELDS:
        field, basis, unit = FIELDS[column]
        components = (field,)
    else:
        return None, 'requires_separate_valuation_definition', []
    terms = [(end, 1)]
    if basis == 'ytd' and not annual and end.month != 3:
        terms.append((quarter(end, 1), -1))
    evidence, total, missing = [], Decimal(0), []
    for field, (period, coefficient) in itertools.product(components, terms):
        if period.isoformat() in conflicts:
            return None, 'source_record_conflict', evidence
        row = facts.get((period.isoformat(), field))
        if row is None:
            if column == 'cross_holdings':
                missing.append(field)
                continue
            return None, 'missing_standard_fact', evidence
        expected = 'instant' if basis == 'instant' else {3: 'Q1', 6: 'H1', 9: '9M', 12: 'FY'}[period.month]
        if (row.get('instrument_id'), row.get('unit'), row.get('period_type'), row.get('statement_scope')) != (instrument, unit, expected, 'provider_default'):
            raise ValueError('incompatible standard identity/unit/period/scope: '+field)
        value = Decimal(row['value'])
        if not value.is_finite():
            raise ValueError('nonfinite standard value')
        if column == 'cross_holdings' and value < 0:
            raise ValueError('negative investment component requires review')
        total += value * coefficient
        evidence.append({'field': field, 'period': period.isoformat(), 'coefficient': coefficient,
                         'value': str(value), 'unit': unit, 'fact_id': row['fact_id'],
                         'available_at': row['available_at'], 'artifact_sha256': row['artifact_sha256']})
    if not evidence:
        return None, 'missing_standard_fact', []
    result = float(total / Decimal(1000000))
    if not math.isfinite(result):
        raise ValueError('SQLite REAL overflow')
    if column in PARTIAL_SCOPES:
        evidence.append({'scope': PARTIAL_SCOPES[column], 'not_complete_target': True,
            'available_component_million_cny': result,
            'component_fact_ids': {f: facts[(end.isoformat(), f)]['fact_id'] for f in ASSET_COMPONENTS if (end.isoformat(), f) in facts},
            'missing_components': missing if column == 'cross_holdings' else [f for f in ASSET_COMPONENTS if (end.isoformat(), f) not in facts],
            'component_arithmetic_complete': not missing if column == 'cross_holdings' else False})
        return result, 'estimated_partial_scope', evidence
    return result, 'available', evidence



def annual_effective_tax_rate(facts, conflicts, instrument, end):
    """最近完整年度的会计有效税率；不冒充现金税率、边际税率或正常化预测。"""
    evidence = {'basis': 'annual_income_tax_expense_divided_by_profit_before_tax', 'period': end.isoformat(), 'components': []}
    if end.isoformat() in conflicts:
        return None, 'source_record_conflict', evidence
    amounts = []
    for field in ('income_tax_expense', 'profit_before_tax'):
        row = facts.get((end.isoformat(), field))
        if row is None:
            return None, 'missing_standard_fact', evidence
        if (row['instrument_id'], row['unit'], row['period_type'], row['statement_scope']) != (instrument, 'CNY', 'FY', 'provider_default'):
            raise ValueError('invalid annual tax component identity/unit/period/scope')
        amount = Decimal(row['value'])
        if not amount.is_finite():
            raise ValueError('nonfinite annual tax component')
        amounts.append(amount)
        evidence['components'].append(row)
    if amounts[1] <= 0:
        return None, 'nonpositive_pretax_income', evidence
    rate = amounts[0] / amounts[1]
    evidence['reported_ratio'] = str(rate)
    if not 0 <= rate <= 1:
        return None, 'requires_tax_rate_review', evidence
    return float(rate), 'available', evidence


def export_snapshot(connection, companies, fetch, period, asof, years=10, quarters=8):
    target.init_schema(connection)
    connection.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE standard_facts(ticker TEXT,period TEXT,field TEXT,value TEXT,unit TEXT,period_type TEXT,statement_scope TEXT,evidence_json TEXT,PRIMARY KEY(ticker,period,field));
 CREATE TABLE export_universe(candidate INTEGER PRIMARY KEY,code TEXT,instrument_id INTEGER,status TEXT,details TEXT);
 CREATE TABLE export_cells(ticker TEXT,series TEXT,period TEXT,period_offset INTEGER,field TEXT,status TEXT,evidence_json TEXT,
 PRIMARY KEY(ticker,series,period_offset,field));''')
    connection.execute('INSERT INTO metadata VALUES(?,?)', ('contract', CONTRACT))
    base = date(period.year if period.month == 12 else period.year - 1, 12, 31)
    annual_ends = [date(base.year-i, 12, 31) for i in range(years)]
    quarter_ends = [quarter(period, i) for i in range(quarters)]
    count = 0
    for ordinal, company in enumerate(companies):
        symbols = company.get('symbols') or []
        symbol = symbols[0] if len(symbols) == 1 else ''
        code = symbol[2:]
        mic = company.get('exchange_mic')
        valid = (company.get('symbol_count') == 1 and company.get('identifier_count') == 1
                 and re.fullmatch(r'\d{6}', code) and mic in EXCHANGES
                 and symbol[:2] == {'XSHG': 'sh', 'XSHE': 'sz', 'XBSE': 'bj'}.get(mic)
                 and company.get('financial_status') != 'blocked_security_identity')
        connection.execute('INSERT INTO export_universe VALUES(?,?,?,?,?)', (ordinal, code or None, company['instrument_id'],
                           'exported' if valid else 'blocked_security_identity', json.dumps(company, ensure_ascii=False)))
        if not valid:
            continue
        ticker = EXCHANGES[mic] + ':' + code
        facts, conflicts = {}, set()
        # 每个现有导出覆盖当年及上年；按需缓存一家公司，不累积全市场历史。
        def load(end):
            payload = fetch(code, end)
            if (payload.get('contract_version'), payload.get('code'), payload.get('report_period')) != ('alphalake-valuation-v2', code, end.isoformat()):
                raise ValueError('unexpected standard export identity')
            if datetime.fromisoformat(payload['information_as_of']) != asof:
                raise ValueError('information cutoff differs')
            if any(row.get('source') != 'tdx' for row in payload['facts']):
                raise ValueError('only TDX financial facts may enter SQLite')
            conflicts.update(row['period'] for row in payload.get('source_conflicts', []))
            seen = set()
            for row in payload['facts']:
                if row.get('code') != code or row.get('instrument_id') != company['instrument_id']:
                    raise ValueError('historical security identity differs')
                if row.get('field') != row.get('canonical_field') or not re.fullmatch(r'[a-z][a-z0-9_]*', row['field']):
                    raise ValueError('standard field name required')
                available = datetime.fromisoformat(row['available_at'])
                if available.utcoffset() is None or available > asof or date.fromisoformat(row['period']) > end:
                    raise ValueError('future standard fact')
                key = (row['period'], row['field'])
                if key in seen or key in facts and facts[key] != row:
                    raise ValueError('duplicate or changed standard fact')
                seen.add(key)
                if key not in facts:
                    connection.execute('INSERT INTO standard_facts VALUES(?,?,?,?,?,?,?,?)',
                        (ticker, row['period'], row['field'], row['value'], row['unit'], row['period_type'],
                         row['statement_scope'], json.dumps(row, ensure_ascii=False, sort_keys=True)))
                facts[key] = row
        row = dict(ticker=ticker, company_name=company['name'], company_type='Public Company',
                   exchange_code=EXCHANGES[mic], primary_exchange=EXCHANGES[mic], region='CN',
                   filing_currency='CNY', listing_currency='CNY', fx_listing_to_reporting=1,
                   fx_rate_source='same currency', period_date_annual=base.isoformat(),
                   period_date_quarterly=period.isoformat(), data_as_of=asof.date().isoformat())
        load(period)
        quote = company.get('quote')
        market_evidence = {'quote': quote, 'share_period': period.isoformat(),
                           'basis': 'unadjusted_close_times_reported_total_shares'}
        shares = facts.get((period.isoformat(), 'total_shares'))
        foreign = [f for f in ('listed_b_shares', 'listed_h_shares')
                   if (period.isoformat(),f) in facts and Decimal(facts[(period.isoformat(),f)]['value']) > 0]
        if quote:
            quote_day = date.fromisoformat(quote['trade_date'])
            if not 0 <= (period-quote_day).days <= 14 or datetime.fromisoformat(quote['recorded_at']) > asof:
                raise ValueError('market observation outside date/cutoff')
            price = Decimal(quote['close'])
            if not price.is_finite() or price <= 0:
                raise ValueError('invalid market price')
            row['stock_price_listing'] = float(price)
        if foreign:
            market_status = 'requires_share_class_market_values'
        elif not quote:
            market_status = 'missing_eligible_close'
        elif shares is None or period.isoformat() in conflicts:
            market_status = 'missing_or_conflicting_reported_shares'
        else:
            share_value = Decimal(shares['value'])
            if not price.is_finite() or price <= 0 or not share_value.is_finite() or share_value <= 0:
                raise ValueError('invalid market price/shares')
            if (shares['unit'], shares['period_type'], shares['instrument_id']) != ('share', 'instant', company['instrument_id']):
                raise ValueError('invalid market share unit/identity/period')
            row['mv_equity_listing'] = float(price*share_value/Decimal(1000000))
            if not math.isfinite(row['mv_equity_listing']):
                raise ValueError('market cap overflow')
            market_evidence['shares'] = shares
            market_status = 'reported_share_price_proxy'
        tax_rate, tax_status, tax_evidence = annual_effective_tax_rate(facts, conflicts, company['instrument_id'], base)
        row['effective_tax_rate'] = tax_rate
        target.insert_companies(connection, [row])
        connection.execute('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
            (ticker, 'company', base.isoformat(), 0, 'effective_tax_rate', tax_status, json.dumps(tax_evidence, ensure_ascii=False)))
        connection.execute('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
            (ticker,'company',period.isoformat(),0,'mv_equity_listing',market_status,json.dumps(market_evidence,ensure_ascii=False)))
        for series, ends in [('annual', annual_ends), ('quarterly', quarter_ends)]:
            for offset, end in enumerate(ends):
                values = {'ticker': ticker, 'fy_offset' if series == 'annual' else 'fq_offset': offset}
                for column in target._ANNUAL_COLS[2:]:
                    value, status, evidence = cell(facts, conflicts, company['instrument_id'], end, column, series == 'annual')
                    values[column] = value
                    connection.execute('INSERT INTO export_cells VALUES(?,?,?,?,?,?,?)',
                                       (ticker, series, end.isoformat(), offset, column, status, json.dumps(evidence, ensure_ascii=False, sort_keys=True)))
                (target.insert_annual_financials if series == 'annual' else target.insert_quarterly_financials)(connection, [values])
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--period', type=date.fromisoformat, required=True)
    parser.add_argument('--as-of', type=datetime.fromisoformat, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--code', action='append')
    group.add_argument('--all', action='store_true')
    parser.add_argument('--years', type=int, default=10, choices=range(1, 11))
    parser.add_argument('--quarters', type=int, default=8, choices=range(1, 9))
    parser.add_argument('--alphalake', type=Path, default=Path(__file__).resolve().parents[3]/'alphalake')
    args = parser.parse_args()
    validate_dates(args.period, args.as_of)
    if args.code and any(not re.fullmatch(r'\d{6}', code) for code in args.code):
        parser.error('six-digit security codes required')
    if args.output.exists():
        parser.error('output already exists; choose a new snapshot path')
    source, binary = args.database.resolve(strict=True), args.alphalake.resolve(strict=True)
    def fingerprint():
        if Path(str(source)+'.wal').exists():
            raise ValueError('source has WAL: close writer and checkpoint before export')
        stat = source.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ino
    version, source_hash = fingerprint(), digest(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=args.output.name+'.', suffix='.tmp', dir=args.output.parent)
    os.close(fd)
    try:
        connection = sqlite3.connect(temporary)
        try:
            with tempfile.TemporaryDirectory(prefix='alphalake-export-') as spool:
                data = Path(spool)/'rows'
                base = args.period.year if args.period.month == 12 else args.period.year-1
                first = min(date(base-args.years+1, 1, 1), quarter(args.period, args.quarters))
                subprocess.run([str(binary), 'export-financial-snapshot', str(source),
                    '--output', str(data), '--fields', ','.join(SOURCE_FIELDS),
                    '--codes', ','.join(sorted(set(args.code or []))), '--from', first.isoformat(),
                    '--period', args.period.isoformat(), '--as-of', args.as_of.isoformat()], check=True, timeout=600)
                companies = [json.loads(line) for line in (data/'companies.jsonl').read_text().splitlines()]
                companies.sort(key=lambda c: ((c.get('symbols') or [''])[0][2:], c['instrument_id']))
                if args.code:
                    found = {s[2:] for c in companies for s in c.get('symbols') or []}
                    if set(args.code)-found:
                        raise ValueError('security not found: '+','.join(sorted(set(args.code)-found)))
                owners = {}
                for company in companies:
                    for symbol in company.get('symbols') or []:
                        owners[symbol[2:]] = owners.get(symbol[2:], 0)+1
                for company in companies:
                    if any(owners[symbol[2:]] > 1 for symbol in company.get('symbols') or []):
                        company['financial_status'] = 'blocked_security_identity'
                conflicts = {}
                with (data/'conflicts.jsonl').open() as stream:
                    for line in stream:
                        row = json.loads(line)
                        conflicts.setdefault(row['code'], []).append(row)
                with (data/'facts.jsonl').open() as stream:
                    groups = iter(itertools.groupby((json.loads(line) for line in stream), key=lambda r: r['code']))
                    current = next(groups, None)
                    def fetch(code, end):
                        nonlocal current
                        while current and current[0] < code:
                            current = next(groups, None)
                        facts = list(current[1]) if current and current[0] == code else []
                        return dict(contract_version='alphalake-valuation-v2', code=code,
                            report_period=end.isoformat(), information_as_of=args.as_of.isoformat(),
                            facts=facts, source_conflicts=conflicts.get(code, []))
                    count = export_snapshot(connection, companies, fetch, args.period, args.as_of, args.years, args.quarters)
            metadata = dict(contract=CONTRACT, source_database_sha256=source_hash, exporter_sha256=digest(__file__),
                            alphalake_binary_sha256=digest(binary), report_period=args.period.isoformat(),
                            information_as_of=args.as_of.isoformat(), exported_at=datetime.now(timezone.utc).isoformat(),
                            wide_money_unit='million_CNY', wide_shares_unit='million_shares',
                            standard_values='decimal_strings_in_each_rows_unit',
                            annual_net_income='parent_attributable', annual_bv_equity='parent_attributable',
                            market_price_window='latest_completed_unadjusted_close_at_or_before_report_period_within_14_days',
                            market_cap_basis='close_times_reported_total_shares_proxy; known_foreign_share_classes_rejected',
                            quarterly_flows='difference_of_standard_YTD_same_year; Q1 unchanged',
                            missing='NULL; see export_cells; source-zero/unreviewed not inferred from absence',
                            boundary='TDX_standard_components; partial_target_scope_values_are_not_complete_totals; market_value_is_reported_share_price_proxy',
                            standard_field_scope=','.join(SOURCE_FIELDS),
                            candidates=str(len(companies)), companies=str(count))
            connection.executemany('INSERT OR REPLACE INTO metadata VALUES(?,?)', metadata.items())
            connection.commit()
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite integrity check failed')
        finally:
            connection.close()
        if fingerprint() != version or digest(source) != source_hash:
            raise ValueError('source changed during export')
        # 同目录原子创建；不覆盖旧快照，也不修改默认seed或源库。
        with open(temporary, 'rb') as stream:
            os.fsync(stream.fileno())
        os.link(temporary, args.output)
        print(json.dumps({'output': str(args.output), 'companies': count, 'candidates': len(companies), 'contract': CONTRACT}, ensure_ascii=False))
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
