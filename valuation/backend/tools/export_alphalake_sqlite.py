"""显式导出AlphaLake标准事实到现有网页SQLite结构；不改变默认数据源。"""
import argparse
import calendar
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from data_sources import us_cn_hk_db as target

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
}
EXCHANGES = {'XSHG': 'SHSE', 'XSHE': 'SZSE', 'XBSE': 'BJSE'}
CONTRACT = 'alphalake-sqlite-v2'


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
    if column not in FIELDS:
        return None, 'requires_separate_valuation_definition', []
    field, basis, unit = FIELDS[column]
    terms = [(end, 1)]
    if basis == 'ytd' and not annual and end.month != 3:
        terms.append((quarter(end, 1), -1))
    evidence, total = [], Decimal(0)
    for period, coefficient in terms:
        if period.isoformat() in conflicts:
            return None, 'source_record_conflict', evidence
        row = facts.get((period.isoformat(), field))
        if row is None:
            return None, 'missing_standard_fact', evidence
        expected = 'instant' if basis == 'instant' else {3: 'Q1', 6: 'H1', 9: '9M', 12: 'FY'}[period.month]
        if (row.get('instrument_id'), row.get('unit'), row.get('period_type'), row.get('statement_scope')) != (instrument, unit, expected, 'provider_default'):
            raise ValueError('incompatible standard identity/unit/period/scope: '+field)
        value = Decimal(row['value'])
        if not value.is_finite():
            raise ValueError('nonfinite standard value')
        total += value * coefficient
        evidence.append({'field': field, 'period': period.isoformat(), 'coefficient': coefficient,
                         'value': str(value), 'unit': unit, 'fact_id': row['fact_id'],
                         'available_at': row['available_at'], 'artifact_sha256': row['artifact_sha256']})
    result = float(total / Decimal(1000000))
    if not math.isfinite(result):
        raise ValueError('SQLite REAL overflow')
    return result, 'available', evidence


def export_snapshot(connection, companies, fetch, period, asof, years=10, quarters=8, *, fetch_statements):
    target.init_schema(connection)
    connection.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
 CREATE TABLE standard_facts(ticker TEXT,period TEXT,field TEXT,value TEXT,unit TEXT,period_type TEXT,statement_scope TEXT,evidence_json TEXT,PRIMARY KEY(ticker,period,field));
 CREATE TABLE valuation_inputs(ticker TEXT PRIMARY KEY,payload_json TEXT NOT NULL,sha256 TEXT NOT NULL);
 CREATE TABLE financial_statements(ticker TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
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
        facts, conflicts, loaded = {}, set(), set()
        # 每个现有导出覆盖当年及上年；按需缓存一家公司，不累积全市场历史。
        def load(end):
            if end in loaded:
                return
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
            if end == period:
                # 附注数值不进入纯TDX快照；估值参数不在财务事实层提供。
                payload = dict(payload, supplements=[])
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
                connection.execute('INSERT INTO valuation_inputs VALUES(?,?,?)',
                    (ticker, raw, hashlib.sha256(raw.encode()).hexdigest()))
            for year in (end.year-1, end.year):
                for month in (3, 6, 9, 12):
                    candidate = date(year, month, calendar.monthrange(year, month)[1])
                    if candidate <= end:
                        loaded.add(candidate)
        row = dict(ticker=ticker, company_name=company['name'], company_type='Public Company',
                   exchange_code=EXCHANGES[mic], primary_exchange=EXCHANGES[mic], region='CN',
                   filing_currency='CNY', listing_currency='CNY', fx_listing_to_reporting=1,
                   fx_rate_source='same currency', period_date_annual=base.isoformat(),
                   period_date_quarterly=period.isoformat(), data_as_of=asof.date().isoformat())
        target.insert_companies(connection, [row])
        load(period)
        statements = fetch_statements(code, period)
        if (statements.get('contract_version'), statements.get('code'), statements.get('report_period')) != ('alphalake-financial-statements-v1', code, period.isoformat()) or datetime.fromisoformat(statements['information_as_of']) != asof:
            raise ValueError('unexpected financial statements identity/cutoff')
        connection.execute('INSERT INTO financial_statements VALUES(?,?)',
                           (ticker, json.dumps(statements, ensure_ascii=False, sort_keys=True)))
        for series, ends in [('annual', annual_ends), ('quarterly', quarter_ends)]:
            for offset, end in enumerate(ends):
                load(end)
                if series == 'quarterly' and end.month != 3:
                    load(quarter(end, 1))
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
    def command(name, *extra, end=args.period):
        if fingerprint() != version:
            raise ValueError('source changed during export')
        return json.loads(subprocess.check_output([str(binary), name, str(source), *extra,
                          '--period', end.isoformat(), '--as-of', args.as_of.isoformat()], text=True, timeout=300))
    companies = []
    for code in sorted(set(args.code or [''])):
        readiness = command('valuation-readiness', *(['--code', code] if code else []))
        if readiness.get('contract_version') != 'alphalake-readiness-v2':
            raise ValueError('current readiness contract required')
        if code and not readiness['companies']:
            raise ValueError('security not found: '+code)
        companies.extend(readiness['companies'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=args.output.name+'.', suffix='.tmp', dir=args.output.parent)
    os.close(fd)
    try:
        connection = sqlite3.connect(temporary)
        try:
            count = export_snapshot(connection, companies, lambda code, end: command('export-valuation', code, end=end),
                                    args.period, args.as_of, args.years, args.quarters,
                                    fetch_statements=lambda code, end: command('financial-statements', code, end=end))
            metadata = dict(contract=CONTRACT, source_database_sha256=source_hash, exporter_sha256=digest(__file__),
                            alphalake_binary_sha256=digest(binary), report_period=args.period.isoformat(),
                            information_as_of=args.as_of.isoformat(), exported_at=datetime.now(timezone.utc).isoformat(),
                            wide_money_unit='million_CNY', wide_shares_unit='million_shares',
                            standard_values='decimal_strings_in_each_rows_unit',
                            annual_net_income='parent_attributable', annual_bv_equity='parent_attributable',
                            quarterly_flows='difference_of_standard_YTD_same_year; Q1 unchanged',
                            missing='NULL; see export_cells; source-zero/unreviewed not inferred from absence',
                            boundary='TDX_only_financial_snapshot; explicit_separate_policy_required; no_supplement_values_or_market_defaults',
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
