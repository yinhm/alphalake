"""显式导出AlphaLake标准事实到现有网页SQLite结构；不改变默认数据源。"""
import argparse
import calendar
from collections import Counter
from datetime import date, datetime, timedelta, timezone
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
CONTRACT = target.CONTRACT
DEBT_COMPONENTS = ('short_term_borrowings', 'long_term_borrowings', 'bonds_payable',
                   'current_portion_noncurrent_liabilities', 'lease_liabilities')
ASSET_COMPONENTS = ('monetary_funds', 'cash_and_cash_equivalents', 'trading_financial_assets',
    'noncurrent_assets_due_within_one_year', 'long_term_equity_investments', 'debt_investments',
    'other_debt_investments', 'other_equity_instrument_investments', 'other_noncurrent_financial_assets')
SOURCE_FIELDS = sorted({f[0] for f in FIELDS.values()} | set(DEBT_COMPONENTS) | set(LONG_TERM)
    | {'listed_b_shares','listed_h_shares','profit_before_tax'})
PARTIAL_SCOPES = target.ASSET_PROXIES


def record_facts(stream, definitions):
    """批量宽行的源定位只存一次；按公司消费时还原既有标准事实契约。"""
    catalog = {}
    for definition in definitions:
        catalog.setdefault(definition['field'], []).append(definition)
    for line in stream:
        row = json.loads(line)
        period = date.fromisoformat(row['period'])
        for field, definitions in sorted(catalog.items()):
            value = row.get(field)
            if value is None:
                continue
            applicable = [m for m in definitions if (m['valid_from'] is None or m['valid_from']<=row['period'])
                          and (m['valid_to'] is None or row['period']<m['valid_to'])]
            if len(applicable)>1:
                raise ValueError('overlapping standard export mapping: '+field)
            if not applicable:
                continue
            m = applicable[0]
            if not isinstance(value,str) or not re.fullmatch(r'FN[1-9][0-9]*',m['provider_field']):
                raise ValueError('invalid source decimal or locator')
            basis = m['period_basis']
            period_type = (basis if basis in ('instant','opening_instant') else
                           'TTM' if basis=='ttm' else 'Q'+str((period.month-1)//3+1) if basis=='quarter' else
                           {3:'Q1',6:'H1',9:'9M',12:'FY'}[period.month])
            yield dict(instrument_id=row['instrument_id'], code=row['code'], period=row['period'],
                field=field, canonical_field=field, value=value, unit=m['unit'], source='tdx',
                statement_scope='provider_default', period_type=period_type,
                fact_id=row['source_record_id']*8192+int(m['provider_field'][2:]),
                **{key:row[key] for key in ('source_record_id','source_filing_id','available_at','announcement_source',
                                         'source_observed_at','artifact_sha256')},
                financial_time_basis='current_standard_snapshot_not_pit')


def completed_market_date(asof):
    local = asof.astimezone(timezone(timedelta(hours=8)))
    return local.date() - timedelta(days=int(local.hour < 15))


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


def input_periods(period, years=10, quarters=8):
    """按字段实际消费的期间投影；年度窗口不拉入十年全部旧季度。"""
    base = date(period.year if period.month==12 else period.year-1,12,31)
    annual = {date(base.year-i,12,31) for i in range(years)}
    ends = {quarter(period,i) for i in range(quarters)}
    instant = annual | ends | {period}
    flow = instant | {quarter(end,1) for end in ends if end.month!=3}
    if period.month!=12:
        prior = period.replace(year=period.year-1)
        flow |= {base,prior,date(prior.year-1,12,31),prior.replace(year=prior.year-1)}
    balances = {f[0] for f in FIELDS.values() if f[1]=='instant'} | set(DEBT_COMPONENTS) | set(ASSET_COMPONENTS)
    return {field: sorted(d.isoformat() for d in ({base} if field=='profit_before_tax' else
        {period} if field in ('listed_b_shares','listed_h_shares') else instant if field in balances else flow))
        for field in SOURCE_FIELDS}


def financial_signature(directory):
    return hashlib.sha256(json.dumps({name:digest(directory/name) for name in
        ('fields.jsonl','records.jsonl','conflicts.jsonl','reviewed_zeros.jsonl')},sort_keys=True).encode()).hexdigest()


def export_runtime_files(backend=None):
    backend = backend or Path(__file__).resolve().parents[1]
    files = sorted((backend/'data_sources').glob('*.py')) + sorted((backend/'data_sources').glob('*.json'))
    files += [backend/'tools'/name for name in ('export_alphalake_sqlite.py','derive_tdx_assets.py')]
    return {p.relative_to(backend).as_posix():digest(p) for p in files if p.is_file()}


def source_input_signature(directory, references, period, asof, reference_asof, years, quarters):
    """绑定实际消费文件、参考和窗口；不以整个DuckDB物理文件代替输入内容。"""
    files = ('companies.jsonl','industries.jsonl','industry-reference.json',
             'fields.jsonl','records.jsonl','conflicts.jsonl','reviewed_zeros.jsonl')
    return hashlib.sha256(json.dumps(dict(files={name:digest(directory/name) for name in files},
        references=references, implementation=export_runtime_files(), contract=CONTRACT,
        period=period.isoformat(),asof=asof.isoformat(),
        reference_asof=reference_asof.isoformat(),years=years,quarters=quarters),sort_keys=True,
        ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def reusable_snapshot(path, signature):
    if path is None:
        return False
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal','-journal')):
        raise ValueError('reuse snapshot has a write journal')
    with sqlite3.connect(path.resolve(strict=True).as_uri()+'?mode=ro',uri=True) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('reuse snapshot integrity failure')
        values = dict(conn.execute("SELECT key,value FROM metadata WHERE key IN ('contract','source_input_signature')"))
    return values.get('contract') == CONTRACT and values.get('source_input_signature') == signature


def query_standard_inputs(connection, field_periods, source_database=None, *, tickers=None):
    """研究显式批量查询主库；先复核发布财务签名，不用当前数据补旧版本。"""
    metadata = target.snapshot_metadata(connection)
    if metadata is None or not metadata.get('financial_content_signature'):
        raise ValueError('Published financial projection signature required for source queries')
    from data_sources.paths import workspace_path
    source = Path(source_database) if source_database is not None else workspace_path('alphalake.duckdb')
    source = source.resolve(strict=True)
    binary = Path(__file__).resolve().parents[3]/'alphalake'
    if Path(str(source)+'.wal').exists():
        raise ValueError('Source has WAL; close writer before research query')
    before = digest(source)
    published_tickers = [r[0] for r in connection.execute('SELECT ticker FROM companies ORDER BY ticker')]
    frozen_scope = target.publication_scope(connection)
    if tickers is not None and (not tickers or len(set(tickers)) != len(tickers) or set(tickers)-set(published_tickers)):
        raise ValueError('unique tickers within published scope required')
    codes = [t.split(':')[-1] for t in (tickers if tickers is not None else published_tickers)]
    code_set = set(codes)
    published = json.loads(metadata['source_projection'])
    with tempfile.TemporaryDirectory(prefix='alphalake-research-query-') as temporary:
        root = Path(temporary)
        def export(name, projection, selected_codes):
            spec = root/(name+'.json')
            spec.write_text(json.dumps(projection,sort_keys=True))
            scope = root/(name+'-scope.json')
            selected = set(selected_codes)
            scope.write_text(json.dumps([s for s in frozen_scope if s['ticker'].split(':')[-1] in selected],ensure_ascii=False))
            directory = root/name
            first = min(day for days in projection.values() for day in days)
            subprocess.run([str(binary),'export-financial-snapshot',str(source),
                '--output',str(directory),'--scope-file',str(scope),'--fields',','.join(sorted(projection)),
                '--field-periods',str(spec),'--from',first,'--period',metadata['report_period'],
                '--as-of',metadata['information_as_of']],check=True,timeout=600,capture_output=True)
            return directory
        if before!=metadata['source_database_sha256']:
            frozen = export('published',published['field_periods'],[t.split(':')[-1] for t in published_tickers])
            if financial_signature(frozen)!=metadata['financial_content_signature']:
                raise ValueError('Source financial content differs from published inputs; use the frozen source version')
        directory = export('research',{field:sorted(set(periods)) for field, periods in field_periods.items()},codes)
        definitions = [json.loads(line) for line in (directory/'fields.jsonl').read_text().splitlines()]
        conflicts = {(r['code'],r['period']) for r in (json.loads(line) for line in (directory/'conflicts.jsonl').read_text().splitlines())}
        if conflicts:
            raise ValueError('Source conflicts in requested research periods: '+str(sorted(conflicts)))
        ticker_by_code = {r[0].split(':')[-1]:r[0] for r in connection.execute('SELECT ticker FROM companies')}
        identity_by_code = {s['ticker'].split(':')[-1]:s['instrument_id'] for s in frozen_scope}
        with (directory/'records.jsonl').open() as stream:
            rows = []
            for fact in record_facts(stream,definitions):
                if fact['code'] not in code_set:
                    raise ValueError('Source returned unrequested security')
                if fact['instrument_id']!=identity_by_code[fact['code']]:
                    raise ValueError('Source canonical security identity differs')
                rows.append(fact | {'ticker':ticker_by_code[fact['code']]})
    if digest(source)!=before:
        raise ValueError('Source changed during research query')
    return rows


def cell(facts, conflicts, instrument, end, column, annual, reviewed_zeros=None, *, ttm=False):
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
    if basis == 'ytd' and ttm and end.month != 12:
        terms = [(date(end.year-1, 12, 31), 1), (end, 1), (end.replace(year=end.year-1), -1)]
    elif basis == 'ytd' and not annual and end.month != 3:
        terms.append((quarter(end, 1), -1))
    evidence, total, missing = [], Decimal(0), []
    for field, (period, coefficient) in itertools.product(components, terms):
        if period.isoformat() in conflicts:
            return None, 'source_record_conflict', evidence
        row = facts.get((period.isoformat(), field))
        reviewed = False
        if row is None and reviewed_zeros is not None:
            row = reviewed_zeros.get((period.isoformat(), field))
            reviewed = row is not None
        if row is None:
            if column == 'cross_holdings':
                missing.append(field)
                continue
            missing.append(field)
            evidence.append(dict(kind='missing_standard_fact', field=field,
                                 period=period.isoformat(), coefficient=coefficient))
            continue
        expected = 'instant' if basis == 'instant' else {3: 'Q1', 6: 'H1', 9: '9M', 12: 'FY'}[period.month]
        if (row.get('instrument_id'), row.get('unit'), row.get('period_type'), row.get('statement_scope')) != (instrument, unit, expected, 'consolidated_statement' if reviewed else 'provider_default'):
            raise ValueError('incompatible standard identity/unit/period/scope: '+field)
        value = Decimal(row['value'])
        if not value.is_finite():
            raise ValueError('nonfinite standard value')
        if column == 'cross_holdings' and value < 0:
            return None, 'negative_investment_component_requires_review', evidence + [dict(
                kind='rejected_component', field=field, period=period.isoformat(), value=str(value),
                unit=unit, fact_id=row['fact_id'], artifact_sha256=row['artifact_sha256'])]
        if reviewed:
            if value != 0 or row['review']['source_zero']['conclusion'] != 'explicit_zero_balance':
                raise ValueError('reviewed source zero must remain zero')
            evidence.append(dict(kind='reviewed_source_zero', field=field, period=period.isoformat(),
                                 import_sha256=row['import_sha256'], coefficient=coefficient))
            continue
        total += value * coefficient
        evidence.append({'field': field, 'period': period.isoformat(), 'coefficient': coefficient,
                         'value': str(value), 'unit': unit, 'fact_id': row['fact_id'],
                         'available_at': row['available_at'], 'artifact_sha256': row['artifact_sha256'],
                         'announcement_source': row.get('announcement_source'),
                         'financial_time_basis': row.get('financial_time_basis')})
    if missing and column != 'cross_holdings':
        return None, 'missing_standard_fact', evidence
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
    return result, 'available_with_reviewed_source_zero' if any(r.get('kind') == 'reviewed_source_zero' for r in evidence) else 'available', evidence



def source_references(evidence):
    """仅交付定位和例外；完整金额与审核仍由数据侧提供。"""
    return [{k: v for k, v in part.items() if k in
             ('field', 'period', 'coefficient', 'kind', 'import_sha256')}
            for part in evidence if 'field' in part]


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
    from data_sources.valuation_scope import scope_context
    target.init_schema(connection)
    connection.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    ttm_columns = ['ticker'] + target._ANNUAL_COLS[2:]
    connection.execute('CREATE TABLE financials_ttm(ticker TEXT PRIMARY KEY REFERENCES companies(ticker),'
                       + ','.join(name+' REAL' for name in ttm_columns[1:])+')')
    connection.execute('INSERT INTO metadata VALUES(?,?)', ('contract', CONTRACT))
    connection.execute('INSERT INTO metadata VALUES(?,?)', ('input_context_format', 'native-wide-context-v2'))
    connection.executemany('INSERT INTO metadata VALUES(?,?)',dict(report_period=period.isoformat(),
        information_as_of=asof.isoformat(),wide_money_unit='million_CNY',wide_shares_unit='million_shares').items())
    base = date(period.year if period.month == 12 else period.year - 1, 12, 31)
    annual_ends = [date(base.year-i, 12, 31) for i in range(years)]
    quarter_ends = [quarter(period, i) for i in range(quarters)]
    count = 0
    for company in companies:
        symbols = company.get('symbols') or []
        symbol = symbols[0] if len(symbols) == 1 else ''
        code = symbol[2:]
        mic = company.get('exchange_mic')
        valid = (company.get('symbol_count') == 1 and company.get('identifier_count') == 1
                 and re.fullmatch(r'\d{6}', code) and mic in EXCHANGES
                 and symbol[:2] == {'XSHG': 'sh', 'XSHE': 'sz', 'XBSE': 'bj'}.get(mic)
                 and (company.get('financial_status') != 'blocked_security_identity' or company.get('scope_bound') is True))
        if not valid:
            continue
        ticker = EXCHANGES[mic] + ':' + code
        facts, conflicts, reviewed_zeros = {}, set(), {}
        # 每个现有导出覆盖当年及上年；按需缓存一家公司，不累积全市场历史。
        def load(end):
            if company.get('financial_status') == 'blocked_security_identity':
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
                available = datetime.fromisoformat(row['available_at']) if row['available_at'] is not None else None
                if available is not None and available.utcoffset() is None:
                    raise ValueError('announcement timezone required')
                if date.fromisoformat(row['period']) > end:
                    raise ValueError('future standard report period')
                key = (row['period'], row['field'])
                if key in seen or key in facts and facts[key] != row:
                    raise ValueError('duplicate or changed standard fact')
                seen.add(key)
                facts[key] = row
            for zero in payload.get('reviewed_zeros', []):
                review = zero['review']
                binding = review['source_zero']
                key = (zero['period'], zero['field'])
                available = datetime.fromisoformat(zero['available_at'])
                if (zero['code'] != code or zero['instrument_id'] != company['instrument_id']
                        or (review['code'], review['period'], binding['field']) != (code, *key)
                        or review['item'] != 'reviewed_source_zero_'+zero['field']
                        or review['source_zero']['conclusion'] != 'explicit_zero_balance'
                        or zero['value'] != '0' or Decimal(review['value']) != 0
                        or zero['unit'] != 'CNY' or review['unit'] != 'CNY'
                        or zero['statement_scope'] != 'consolidated_statement'
                        or review['scope'] != 'consolidated_statement'
                        or available.utcoffset() is None or available > asof
                        or date.fromisoformat(zero['period']) > end
                        or not re.fullmatch(r'[0-9a-f]{64}', zero['import_sha256'])
                        or binding['artifact_sha256'] != zero['artifact_sha256']):
                    raise ValueError('invalid reviewed source zero identity/value/evidence')
                if key in reviewed_zeros and reviewed_zeros[key] != zero:
                    raise ValueError('conflicting reviewed source zeros')
                reviewed_zeros[key] = zero
        row = dict(ticker=ticker, company_name=company['name'], company_type='Public Company',
                   exchange_code=EXCHANGES[mic], primary_exchange=EXCHANGES[mic], region='CN',
                   filing_currency='CNY', listing_currency='CNY', fx_listing_to_reporting=1,
                   fx_rate_source='same currency', period_date_annual=base.isoformat(),
                   period_date_quarterly=period.isoformat(), data_as_of=asof.date().isoformat())
        load(period)
        quote = company.get('quote')
        shares = facts.get((period.isoformat(), 'total_shares'))
        foreign = [f for f in ('listed_b_shares', 'listed_h_shares')
                   if (period.isoformat(),f) in facts and Decimal(facts[(period.isoformat(),f)]['value']) > 0]
        if quote:
            quote_day = date.fromisoformat(quote['trade_date'])
            if not 0 <= (completed_market_date(asof)-quote_day).days <= 14 or datetime.fromisoformat(quote['recorded_at']) > asof:
                raise ValueError('market observation outside date/cutoff')
            close_boundary = datetime.combine(quote_day, datetime.min.time(), timezone(timedelta(hours=8))) + timedelta(hours=15)
            if datetime.fromisoformat(quote['acquisition_started_at']) < close_boundary or datetime.fromisoformat(quote['run_finished_at']) > asof:
                raise ValueError('market observation acquired before close or completed after cutoff')
            price = Decimal(quote['close'])
            if not price.is_finite() or price <= 0:
                raise ValueError('invalid market price')
            row['stock_price_listing'] = float(price)
        if not quote:
            market_status = 'missing_eligible_close'
        elif shares is None or period.isoformat() in conflicts:
            market_status = 'missing_or_conflicting_reported_shares'
        else:
            share_value = Decimal(shares['value'])
            if not share_value.is_finite():
                raise ValueError('invalid market shares: '+ticker)
            if (shares['unit'], shares['period_type'], shares['instrument_id']) != ('share', 'instant', company['instrument_id']):
                raise ValueError('invalid market share unit/identity/period')
            if share_value <= 0:
                market_status = 'nonpositive_reported_shares'
            else:
                row['mv_equity_listing'] = float(price*share_value/Decimal(1000000))
                if not math.isfinite(row['mv_equity_listing']):
                    raise ValueError('market cap overflow')
                market_status = 'a_share_total_share_proxy' if foreign else 'reported_share_price_proxy'
        tax_rate, tax_status, tax_evidence = annual_effective_tax_rate(facts, conflicts, company['instrument_id'], base)
        row['effective_tax_rate'] = tax_rate
        target.insert_companies(connection, [row])
        context = dict(ticker=ticker, instrument_id=company['instrument_id'], report_period=period.isoformat(),
            currency='CNY', money_unit='million_reporting_currency', shares_unit='million_shares',
            market=dict(status=market_status, quote=quote, share_period=period.isoformat(),
                price_information_as_of=asof.isoformat(), basis='unadjusted_close_times_reported_total_shares',
                known_foreign_share_classes=foreign, share_class_prices_and_fx_complete=False),
            tax=dict(status=tax_status, period=base.isoformat(), basis=tax_evidence['basis']),
            model_scope=scope_context(company, company.get('industry_reference', {})),
            source_records={}, exceptions=[])
        if company.get('source_identity_status', 'active') != 'active':
            context['market']['source_identity_status'] = company['source_identity_status']
        def remember(series, offset, end, column, value, status, evidence):
            refs = source_references(evidence)
            if status != target.default_cell_status(column, value) or (status == 'missing_standard_fact'
                    and any('value' in part for part in evidence)):
                context['exceptions'].append(dict(series=series, offset=offset, field=column,
                    period=end.isoformat(), status=status, source_refs=refs))
        for series, ends in [('annual', annual_ends), ('quarterly', quarter_ends)]:
            batch = []
            for offset, end in enumerate(ends):
                values = {'ticker': ticker, 'fy_offset' if series == 'annual' else 'fq_offset': offset}
                for column in target._ANNUAL_COLS[2:]:
                    value, status, evidence = cell(facts, conflicts, company['instrument_id'], end, column, series == 'annual', reviewed_zeros)
                    values[column] = value
                    remember(series, offset, end, column, value, status, evidence)
                batch.append(values)
            (target.insert_annual_financials if series == 'annual' else target.insert_quarterly_financials)(connection, batch)
        values = {'ticker': ticker}
        ttm_evidence = {}
        for column in ttm_columns[1:]:
            value, status, evidence = cell(facts, conflicts, company['instrument_id'], period,
                column, True, reviewed_zeros, ttm=True)
            values[column] = value
            remember('ttm', 0, period, column, value, status, evidence)
            if column in ('revenues', 'ebit'):
                ttm_evidence[column] = evidence
        target._executemany_from_dicts(connection, 'financials_ttm', ttm_columns, [values])
        if period.month != 12:
            windows = dict(current={}, prior={}, quarters=period.month//3)
            for column, evidence in ttm_evidence.items():
                for name, end in [('current',period), ('prior',period.replace(year=period.year-1))]:
                    part = next((r for r in evidence if r.get('period')==end.isoformat() and 'value' in r), None)
                    windows[name][column] = float(Decimal(part['value'])/Decimal(1000000)) if part else None
            context['cumulative_windows'] = windows
            prior_end = period.replace(year=period.year-1)
            comparison = [date(prior_end.year-1,12,31), prior_end, prior_end.replace(year=prior_end.year-1)]
            terms = [facts.get((p.isoformat(),'revenue_cumulative')) for p in comparison]
            current = facts.get((period.isoformat(),'revenue_cumulative'))
            if current and all(r and r['unit']=='CNY' and r['period_type']=={3:'Q1',6:'H1',9:'9M',12:'FY'}[p.month]
                    and r['statement_scope']==current['statement_scope'] for p,r in zip(comparison,terms)):
                amounts = [Decimal(r['value']) for r in terms]
                if all(v.is_finite() for v in amounts):
                    context['prior_ttm_revenue'] = dict(period_end=prior_end.isoformat(), currency='CNY',
                        money_unit='million_reporting_currency', revenues=str((amounts[0]+amounts[1]-amounts[2])/Decimal(1000000)),
                        formula='FY + current_YTD - prior_same_YTD',
                        source_periods=[p.isoformat() for p in comparison])
        for (end, _), fact in facts.items():
            if fact.get('source_record_id') is not None:
                context['source_records'][end] = fact['source_record_id']
        context['input_sha256'] = target.input_signature(connection, ticker, context)
        connection.execute('INSERT INTO metadata VALUES(?,?)', ('input_context:'+ticker,
            json.dumps(context,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)))
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--period', type=date.fromisoformat, required=True)
    parser.add_argument('--as-of', type=datetime.fromisoformat, required=True)
    parser.add_argument('--reference-as-of', type=datetime.fromisoformat, help='explicit reference cutoff; defaults to financial cutoff')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--code', action='append')
    group.add_argument('--all', action='store_true')
    group.add_argument('--scope-file', type=Path, help='冻结的规范证券ID、ticker、名称；按既有身份保留源缺项公司')
    parser.add_argument('--years', type=int, default=10, choices=range(1, 11))
    parser.add_argument('--quarters', type=int, default=8, choices=range(1, 9))
    parser.add_argument('--alphalake', type=Path, default=Path(__file__).resolve().parents[3]/'alphalake')
    parser.add_argument('--reuse-from', type=Path, help='消费输入签名相同才复用已验收SQLite；未知签名重新导出')
    args = parser.parse_args()
    frozen_scope = None
    if args.scope_file:
        frozen_scope = json.loads(args.scope_file.read_text())
        if (not isinstance(frozen_scope,list) or not frozen_scope or any(not isinstance(s,dict)
                or set(s)!={'instrument_id','ticker','name'} or type(s['instrument_id']) is not int or s['instrument_id']<=0
                or not isinstance(s['ticker'],str) or not re.fullmatch(r'(SHSE|SZSE):[0-9]{6}',s['ticker'])
                or not isinstance(s['name'],str) or not s['name'] for s in frozen_scope)
                or len({s['instrument_id'] for s in frozen_scope})!=len(frozen_scope)
                or len({s['ticker'] for s in frozen_scope})!=len(frozen_scope)):
            parser.error('nonempty unique canonical publication identities required')
        args.code = [s['ticker'].split(':')[1] for s in frozen_scope]
    validate_dates(args.period, args.as_of)
    reference_asof = args.reference_as_of or args.as_of
    validate_dates(args.period, reference_asof)
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
                projection = input_periods(args.period,args.years,args.quarters)
                first = min(date.fromisoformat(day) for days in projection.values() for day in days)
                projection_path = Path(spool)/'field-periods.json'
                projection_path.write_text(json.dumps(projection,sort_keys=True))
                scope_args = ['--scope-file',str(args.scope_file)] if frozen_scope is not None else ['--codes', ','.join(sorted(set(args.code or [])))]
                subprocess.run([str(binary), 'export-financial-snapshot', str(source),
                    '--output', str(data), '--fields', ','.join(SOURCE_FIELDS),
                    '--field-periods', str(projection_path),
                    *scope_args, '--from', first.isoformat(),
                    '--period', args.period.isoformat(), '--as-of', args.as_of.isoformat()], check=True, timeout=600)
                reference_packet = subprocess.run([str(binary), 'export-native-references', str(source),
                    '--as-of', reference_asof.isoformat()], check=True, capture_output=True, text=True, timeout=120)
                references = json.loads(reference_packet.stdout)
                input_signature = source_input_signature(data,references,args.period,args.as_of,reference_asof,args.years,args.quarters)
                if reusable_snapshot(args.reuse_from,input_signature):
                    if fingerprint() != version or digest(source) != source_hash:
                        raise ValueError('source changed during input verification')
                    connection.close()
                    Path(temporary).unlink()
                    # 硬链接后再次核对实际候选，避免读取与链接间路径被替换。
                    os.link(args.reuse_from,temporary)
                    if not reusable_snapshot(Path(temporary),input_signature):
                        raise ValueError('reuse snapshot changed during verification')
                    os.link(temporary,args.output)
                    print(json.dumps(dict(status='reused_verified_source_inputs',source_database_sha256=source_hash,
                        source_input_signature=input_signature,output=str(args.output)),ensure_ascii=False))
                    return
                companies = [json.loads(line) for line in (data/'companies.jsonl').read_text().splitlines()]
                if frozen_scope is not None:
                    expected = {s['instrument_id']:s['ticker'] for s in frozen_scope}
                    actual = {c['instrument_id']:EXCHANGES[c['exchange_mic']]+':'+c['symbols'][0][2:] for c in companies}
                    if len(companies)!=len(frozen_scope) or actual!=expected:
                        raise ValueError('frozen canonical publication scope differs')
                companies.sort(key=lambda c: ((c.get('symbols') or [''])[0][2:], c['instrument_id']))
                reference = json.loads((data/'industry-reference.json').read_text())
                memberships = {row['instrument_id']: row['industry_memberships']
                    for row in map(json.loads, (data/'industries.jsonl').read_text().splitlines())}
                for company in companies:
                    company['industry_reference'] = reference
                    company['industry_memberships'] = memberships.get(company['instrument_id'], [])
                source_projection = dict(fields=SOURCE_FIELDS, first=first.isoformat(), field_periods=projection)
                source_signature = financial_signature(data)
                artifacts = set()
                with (data/'records.jsonl').open() as stream:
                    for line in stream:
                        artifacts.add(json.loads(line)['artifact_sha256'])
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
                reviewed_zeros = {}
                with (data/'reviewed_zeros.jsonl').open() as stream:
                    for line in stream:
                        zero = json.loads(line)
                        reviewed_zeros.setdefault(zero['code'], []).append(zero)
                definitions = [json.loads(line) for line in (data/'fields.jsonl').read_text().splitlines()]
                with (data/'records.jsonl').open() as stream:
                    groups = iter(itertools.groupby(record_facts(stream, definitions), key=lambda r: r['code']))
                    current = next(groups, None)
                    def fetch(code, end):
                        nonlocal current
                        while current and current[0] < code:
                            current = next(groups, None)
                        facts = list(current[1]) if current and current[0] == code else []
                        return dict(contract_version='alphalake-valuation-v2', code=code,
                            report_period=end.isoformat(), information_as_of=args.as_of.isoformat(),
                            facts=facts, source_conflicts=conflicts.get(code, []), reviewed_zeros=reviewed_zeros.get(code, []))
                    count = export_snapshot(connection, companies, fetch, args.period, args.as_of, args.years, args.quarters)
                scope_root = source.parent/'derived/sqlite-export'
                scope_root.mkdir(parents=True,exist_ok=True)
                scope_payload = dict(contract='alphalake-export-scope-v1',source_database_sha256=source_hash,
                    report_period=args.period.isoformat(),information_as_of=args.as_of.isoformat(),
                    companies=[{k:c[k] for k in ('instrument_id','name','exchange_mic','symbols','symbol_count','identifier_count','financial_status','source_identity_status','scope_bound')
                                if k in c} for c in companies])
                scope_raw = json.dumps(scope_payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
                scope_hash = hashlib.sha256(scope_raw.encode()).hexdigest()
                scope_file = scope_root/('scope-'+args.period.isoformat()+'-'+scope_hash[:16]+'.json')
            from data_sources.native_references import write_snapshot
            write_snapshot(connection, references)
            metadata = dict(contract=CONTRACT, source_database_sha256=source_hash, exporter_sha256=digest(__file__),
                            alphalake_binary_sha256=digest(binary), report_period=args.period.isoformat(),
                            financial_time_basis='current_standard_snapshot_not_pit',
                            announcement_policy='TDX_report_date_first; optional_CNINFO; unknown_date_does_not_block_amounts',
                            information_as_of=args.as_of.isoformat(), exported_at=datetime.now(timezone.utc).isoformat(),
                            wide_money_unit='million_CNY', wide_shares_unit='million_shares',
                            financial_content_signature=source_signature,
                            source_input_signature=input_signature,
                            source_projection=json.dumps(source_projection,sort_keys=True,separators=(',',':')),
                            source_artifacts=json.dumps(sorted(artifacts)),
                            scope_sha256=scope_hash, scope_receipt=str(scope_file.relative_to(source.parent)),
                            annual_net_income='parent_attributable', annual_bv_equity='parent_attributable',
                            market_price_window='latest_completed_unadjusted_close_at_information_cutoff_within_14_days',
                            market_cap_basis='close_times_reported_total_shares_proxy; known_foreign_share_classes_use_explicit_a_share_price_proxy',
                            quarterly_flows='difference_of_standard_YTD_same_year; Q1 unchanged',
                            ttm_flows='FY_plus_current_YTD_minus_prior_same_YTD; FY_unchanged',
                            ttm_balances='latest_report_period; never_sum_balances',
                            missing='NULL; sparse input_context exceptions; source-zero/unreviewed not inferred from absence',
                            reviewed_zero_basis='explicit_current_review_bound_to_TDX_zero_and_PDF; not_standard_fact_or_historical_system_time',
                            boundary='TDX_standard_components_and_explicit_reviewed_source_zeros; partial_target_scope_values_are_not_complete_totals; market_value_is_explicit_price_times_total_shares_proxy',
                            standard_field_scope=','.join(SOURCE_FIELDS),
                            publication_quality=json.dumps(dict(companies=len(companies),
                                source_identity=dict(Counter(c['source_identity_status'] for c in companies)),
                                exceptions=[dict(instrument_id=c['instrument_id'],ticker=EXCHANGES[c['exchange_mic']]+':'+c['symbols'][0][2:],status=c['source_identity_status'])
                                    for c in companies if c['source_identity_status']!='active']),ensure_ascii=False,sort_keys=True,separators=(',',':')),
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
        if scope_file.exists():
            if digest(scope_file)!=scope_hash:
                raise ValueError('Existing scope receipt hash mismatch')
        else:
            with scope_file.open('x') as stream:
                stream.write(scope_raw)
                stream.flush()
                os.fsync(stream.fileno())
        os.link(temporary, args.output)
        print(json.dumps({'output': str(args.output), 'companies': count, 'candidates': len(companies),
            'scope_receipt':str(scope_file),'contract': CONTRACT}, ensure_ascii=False))
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
