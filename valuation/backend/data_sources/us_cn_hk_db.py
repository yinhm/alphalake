"""
SQLite database layer for the US/CN/HK (and future regions) company dataset.

Schema-as-code: three tables + ingest log. Idempotent — the ingester drops
and recreates tables on refresh, so re-running is always safe. Every write
is a single transaction; partial failures don't leave the DB inconsistent.

Public surface:
  - get_connection()            open a connection (creates the DB file if absent)
  - init_schema()               drop + recreate tables (called at start of every refresh)
  - insert_companies()          bulk-insert companies table
  - insert_annual_financials()  bulk-insert financials_annual
  - insert_quarterly_financials() bulk-insert financials_quarterly
  - log_ingest()                append a row to ingest_log
  - search_companies()          GET /api/database/search — LIKE on name + exact-prefix on ticker
  - fetch_company()             GET /api/database/company/<ticker> — fully-assembled dict
  - latest_ingest_summary()     GET /api/admin/dataset-status — last refresh report
"""
from __future__ import annotations

import json
import math
from datetime import date
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from data_sources.paths import workspace_path

DB_PATH = workspace_path('derived', 'valuation.sqlite')


def get_db_path() -> Path:
    """使用显式配置或唯一动态快照；不回退到仓库内的示例数据库。"""
    env = os.environ.get('US_CN_HK_DB_PATH')
    return Path(env) if env else workspace_path('derived', 'valuation.sqlite')


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with sensible defaults for a read-mostly,
    single-writer app. Always commits or rolls back on exit."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if snapshot_metadata(conn) is not None:
            conn.execute("PRAGMA query_only = ON")
        else:
            conn.execute("PRAGMA journal_mode = WAL")  # vendor datasets remain writable
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_COMPANIES_DDL = """
CREATE TABLE IF NOT EXISTS companies (
    ticker                  TEXT PRIMARY KEY,
    company_name            TEXT NOT NULL,
    company_type            TEXT,
    exchange_code           TEXT,
    primary_exchange        TEXT,
    secondary_exchanges     TEXT,
    region                  TEXT,
    filing_currency         TEXT,
    listing_currency        TEXT,
    fx_listing_to_reporting REAL,          -- derived at ingest when possible
    fx_rate_source          TEXT,          -- 'same currency' | 'unset' | 'manual' | …
    effective_tax_rate      REAL,          -- /100 already applied
    stock_price_listing     REAL,
    mv_equity_listing       REAL,
    actual_rating_fc        TEXT,
    actual_rating_lc        TEXT,
    options_outstanding     REAL,
    options_avg_strike      REAL,          -- listing currency
    period_date_annual      TEXT,
    period_date_quarterly   TEXT,
    lease_commitment_yr1    REAL,
    lease_commitment_yr2    REAL,
    lease_commitment_yr3    REAL,
    lease_commitment_yr4    REAL,
    lease_commitment_yr5    REAL,
    lease_commitment_beyond REAL,
    geographic_segments_json TEXT,         -- list[{name, revenue, pct}]
    data_as_of              TEXT           -- ISO date of the ingest
);
"""

_FINANCIALS_ANNUAL_DDL = """
CREATE TABLE IF NOT EXISTS financials_annual (
    ticker                          TEXT NOT NULL,
    fy_offset                       INTEGER NOT NULL,   -- 0 = FY-0 (most recent)
    revenues                        REAL,
    ebit                            REAL,
    ebitda                          REAL,
    net_income                      REAL,
    interest_expense                REAL,
    capex                           REAL,
    d_a                             REAL,
    earnings_before_tax             REAL,
    total_tax_expense               REAL,
    operating_lease_expense         REAL,
    r_and_d_expense                 REAL,
    cash_and_marketable_securities  REAL,
    cross_holdings                  REAL,
    bv_debt                         REAL,
    bv_equity                       REAL,
    shares_outstanding              REAL,
    minority_interests              REAL,
    PRIMARY KEY (ticker, fy_offset),
    FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
);
"""

_FINANCIALS_QUARTERLY_DDL = """
CREATE TABLE IF NOT EXISTS financials_quarterly (
    ticker                          TEXT NOT NULL,
    fq_offset                       INTEGER NOT NULL,   -- 0 = FQ-0 (most recent)
    revenues                        REAL,
    ebit                            REAL,
    ebitda                          REAL,
    net_income                      REAL,
    interest_expense                REAL,
    capex                           REAL,
    d_a                             REAL,
    earnings_before_tax             REAL,
    total_tax_expense               REAL,
    operating_lease_expense         REAL,
    r_and_d_expense                 REAL,
    cash_and_marketable_securities  REAL,           -- FQ-0 only on balance-sheet
    cross_holdings                  REAL,
    bv_debt                         REAL,
    bv_equity                       REAL,
    shares_outstanding              REAL,
    minority_interests              REAL,
    PRIMARY KEY (ticker, fq_offset),
    FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
);
"""

_INGEST_LOG_DDL = """
CREATE TABLE IF NOT EXISTS ingest_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc   TEXT NOT NULL,
    n_companies     INTEGER NOT NULL,
    n_rejected      INTEGER NOT NULL,
    n_files         INTEGER NOT NULL,
    file_manifest   TEXT,     -- JSON: [{name, size, mtime}, …]
    unmapped_columns TEXT,    -- JSON list
    unmapped_exchanges TEXT,  -- JSON list
    warnings        TEXT,     -- JSON list of strings
    duration_ms     INTEGER
);
"""

_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(company_name COLLATE NOCASE)",
    "CREATE INDEX IF NOT EXISTS idx_companies_exchange ON companies(exchange_code)",
    "CREATE INDEX IF NOT EXISTS idx_companies_region ON companies(region)",
]


def init_schema(conn: sqlite3.Connection, drop_existing: bool = True) -> None:
    """Create all tables (optionally dropping first, which is what a refresh does)."""
    if drop_existing:
        for table in ("financials_annual", "financials_quarterly", "companies"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        # ingest_log is preserved across refreshes — it's the audit trail.
    conn.execute(_COMPANIES_DDL)
    conn.execute(_FINANCIALS_ANNUAL_DDL)
    conn.execute(_FINANCIALS_QUARTERLY_DDL)
    conn.execute(_INGEST_LOG_DDL)
    for ddl in _INDEX_DDL:
        conn.execute(ddl)


# ---------------------------------------------------------------------------
# Bulk inserts — use executemany for speed. All columns explicit to catch
# schema drift at write time.
# ---------------------------------------------------------------------------

_COMPANIES_COLS = [
    "ticker", "company_name", "company_type", "exchange_code", "primary_exchange",
    "secondary_exchanges", "region", "filing_currency", "listing_currency",
    "fx_listing_to_reporting", "fx_rate_source", "effective_tax_rate",
    "stock_price_listing", "mv_equity_listing", "actual_rating_fc", "actual_rating_lc",
    "options_outstanding", "options_avg_strike", "period_date_annual",
    "period_date_quarterly", "lease_commitment_yr1", "lease_commitment_yr2",
    "lease_commitment_yr3", "lease_commitment_yr4", "lease_commitment_yr5",
    "lease_commitment_beyond", "geographic_segments_json", "data_as_of",
]

_ANNUAL_COLS = [
    "ticker", "fy_offset", "revenues", "ebit", "ebitda", "net_income",
    "interest_expense", "capex", "d_a", "earnings_before_tax", "total_tax_expense",
    "operating_lease_expense", "r_and_d_expense", "cash_and_marketable_securities",
    "cross_holdings", "bv_debt", "bv_equity", "shares_outstanding", "minority_interests",
]

_QUARTERLY_COLS = list(_ANNUAL_COLS)
_QUARTERLY_COLS[1] = "fq_offset"  # replace fy_offset


def _executemany_from_dicts(conn: sqlite3.Connection, table: str, columns: list[str],
                             rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    placeholders = ",".join("?" for _ in columns)
    cols_sql = ",".join(columns)
    sql = f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})"
    tuples = [tuple(r.get(c) for c in columns) for r in rows]
    conn.executemany(sql, tuples)


def insert_companies(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    _executemany_from_dicts(conn, "companies", _COMPANIES_COLS, rows)


def insert_annual_financials(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    _executemany_from_dicts(conn, "financials_annual", _ANNUAL_COLS, rows)


def insert_quarterly_financials(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    _executemany_from_dicts(conn, "financials_quarterly", _QUARTERLY_COLS, rows)


def log_ingest(conn: sqlite3.Connection, *, timestamp_utc: str, n_companies: int,
               n_rejected: int, n_files: int, file_manifest: list,
               unmapped_columns: list, unmapped_exchanges: list,
               warnings: list, duration_ms: int) -> None:
    conn.execute("""
        INSERT INTO ingest_log (timestamp_utc, n_companies, n_rejected, n_files,
            file_manifest, unmapped_columns, unmapped_exchanges, warnings, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        timestamp_utc, n_companies, n_rejected, n_files,
        json.dumps(file_manifest), json.dumps(unmapped_columns),
        json.dumps(unmapped_exchanges), json.dumps(warnings), duration_ms,
    ))


# ---------------------------------------------------------------------------
# Public read APIs
# ---------------------------------------------------------------------------

def search_companies(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Case-insensitive substring on company_name OR exact-prefix on ticker.
    Results sorted: exact ticker match first, then ticker prefix, then name
    prefix, then name substring — alphabetical within each group."""
    if not query or not query.strip():
        return []
    q = query.strip()
    rows = conn.execute("""
        SELECT ticker, company_name, exchange_code, region, filing_currency,
               listing_currency, period_date_annual,
               CASE
                 WHEN ticker = ? COLLATE NOCASE THEN 0
                 WHEN ticker LIKE ? COLLATE NOCASE THEN 1
                 WHEN company_name LIKE ? COLLATE NOCASE THEN 2
                 ELSE 3
               END AS match_rank
        FROM companies
        WHERE ticker LIKE ? COLLATE NOCASE OR company_name LIKE ? COLLATE NOCASE
           OR REPLACE(company_name, ' ', '') LIKE ? COLLATE NOCASE
        ORDER BY match_rank, company_name COLLATE NOCASE
        LIMIT ?
    """, (q, f"{q}%", f"{q}%", f"%{q}%", f"%{q}%", "%"+q.replace(" ", "")+"%", limit)).fetchall()
    return [dict(r) for r in rows]


def snapshot_metadata(conn: sqlite3.Connection) -> dict | None:
    """按数据产品契约识别来源；不把损坏或旧版TDX快照降级为外部数据。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not tables.intersection({'metadata', 'valuation_inputs', 'standard_facts', 'export_cells'}):
        return None
    metadata = dict(conn.execute('SELECT key,value FROM metadata')) if 'metadata' in tables else {}
    if metadata.get('contract') != 'alphalake-sqlite-v10':
        raise ValueError('Unsupported AlphaLake SQLite contract; rebuild the snapshot with the current exporter')
    if not {'standard_facts', 'export_cells', 'export_universe', 'reviewed_source_zeros', 'financials_ttm'} <= tables:
        raise ValueError('Incomplete AlphaLake SQLite snapshot')
    if metadata.get('ttm_evidence_format') != 'standard_fact_refs':
        raise ValueError('Invalid TTM evidence contract; rebuild the snapshot')
    return metadata


def fetch_company(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """Return the full company snapshot — identifiers + snapshot fields +
    list of annual + list of quarterly financials. Returns None if not found."""
    co = conn.execute("SELECT * FROM companies WHERE ticker = ?", (ticker,)).fetchone()
    if co is None:
        return None
    co_dict = dict(co)
    # Inflate geographic_segments_json back into a list.
    gs = co_dict.pop("geographic_segments_json", None)
    try:
        co_dict["geographic_segments"] = json.loads(gs) if gs else []
    except (json.JSONDecodeError, TypeError):
        co_dict["geographic_segments"] = []

    annual = conn.execute(
        "SELECT * FROM financials_annual WHERE ticker = ? ORDER BY fy_offset",
        (ticker,),
    ).fetchall()
    quarterly = conn.execute(
        "SELECT * FROM financials_quarterly WHERE ticker = ? ORDER BY fq_offset",
        (ticker,),
    ).fetchall()

    result = {
        "company": co_dict,
        "financials_annual": [dict(r) for r in annual],
        "financials_quarterly": [dict(r) for r in quarterly],
    }

    metadata = snapshot_metadata(conn)
    if metadata is not None:
        result['data_source'] = metadata
        ttm = conn.execute('SELECT * FROM financials_ttm WHERE ticker=?', (ticker,)).fetchone()
        if ttm is None:
            raise ValueError('Missing TTM row; rebuild the snapshot')
        result['financials_ttm'] = dict(ttm)
        result['ttm_evidence'] = {r['field']: json.loads(r['evidence_json']) for r in conn.execute(
            "SELECT field,evidence_json FROM export_cells WHERE ticker=? AND series='ttm' AND period_offset=0", (ticker,))}
        periods = sorted({p['period'] for parts in result['ttm_evidence'].values() for p in parts if 'period' in p})
        facts = {(r['period'], r['field']): json.loads(r['evidence_json']) for r in conn.execute(
            'SELECT period,field,evidence_json FROM standard_facts WHERE ticker=? AND period IN ('
            + ','.join('?' for _ in periods) + ')', (ticker, *periods))}
        for parts in result['ttm_evidence'].values():
            for part in parts:
                if 'field' not in part or part.get('kind') in ('missing_standard_fact', 'reviewed_source_zero'):
                    continue
                fact = facts.get((part['period'], part['field']))
                if fact is None:
                    raise ValueError('Missing TTM component evidence; rebuild the snapshot')
                part.update({key: fact.get(key) for key in ('value', 'unit', 'fact_id', 'available_at',
                    'artifact_sha256', 'announcement_source', 'financial_time_basis')})
        result['standard_financials'] = [dict(r) for r in conn.execute(
            'SELECT period,field,value,unit,period_type,statement_scope FROM standard_facts WHERE ticker=? ORDER BY period,field', (ticker,))]
    return result


def native_input_window(record: dict) -> dict:
    """实际期间与消费路径；标准累计TTM不消费季度位置，不压缩历史偏移。"""
    co = record['company']
    annual = date.fromisoformat(co['period_date_annual'][:10])
    quarterly = date.fromisoformat((co.get('period_date_quarterly') or co['period_date_annual'])[:10])
    months = (quarterly.year-annual.year)*12 + quarterly.month-annual.month
    if months < 0 or months > 12 or months % 3:
        raise ValueError('财年末与季度末必须相隔0至4个完整季度')
    k = months // 3
    prepared = bool(record.get('data_source') and k)
    return dict(quarters_since_10k=k, annual_offsets=[0],
                quarterly_offsets=[] if prepared else list(range(k))+list(range(4, 4+k)),
                quarterly_slots=0 if prepared else k+4 if k else 0,
                flow_series='ttm' if prepared else 'quarterly' if k else 'annual')


def standard_cumulative_ttm(inputs) -> bool:
    """普通标准事实TTM，不是专项调整利润/资本输入；仅识别输入形态。"""
    return (inputs.prepared_ttm is not None
            and inputs.prepared_ttm.provenance.get('basis') == 'standard_cumulative_ttm'
            and inputs.prepared_ttm.provenance.get('contract') == 'alphalake-sqlite-v10')


def native_compatibility(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """按当前原生入口默认选择评估；覆盖率、核心输入和条件输入分别计数。"""
    record = fetch_company(conn, ticker)
    if record is None:
        return None
    co = record['company']
    source = record.get('data_source')
    cells = {(r['series'], r['period_offset'], r['field']):
             dict(period=r['period'], status=r['status'], evidence=json.loads(r['evidence_json']))
             for r in conn.execute('SELECT series,period_offset,field,period,status,evidence_json FROM export_cells WHERE ticker=?', (ticker,))} if source else {}
    rows = {series: {r[key]: r for r in record['financials_'+series]}
            for series, key in [('annual','fy_offset'),('quarterly','fq_offset')]}
    if source:
        rows['ttm'] = {0: record['financials_ttm']}
    required, conditional, history, warnings, blockers = [], [], [], [], []
    consumed_inputs = []
    zero_refs = [dict(series=s, offset=o, field=f, component=part['field'], period=part['period'], import_sha256=part['import_sha256'])
        for (s,o,f),e in cells.items() for part in e['evidence'] if isinstance(part, dict) and part.get('kind') == 'reviewed_source_zero']
    reviewed_zeros = []
    if zero_refs:
        approvals = {r['import_sha256']: json.loads(r['evidence_json']) for r in conn.execute(
            'SELECT import_sha256,evidence_json FROM reviewed_source_zeros WHERE ticker=?', (ticker,))}
        if any(r['import_sha256'] not in approvals for r in zero_refs):
            raise ValueError('Missing reviewed source zero evidence; rebuild snapshot')
        for ref in zero_refs:
            proof = approvals[ref['import_sha256']]
            if (proof['import_sha256'] != ref['import_sha256'] or proof['code'] != ticker.split(':')[-1]
                    or proof['field'] != ref['component'] or proof['period'] != ref['period'] or proof['value'] != '0'):
                raise ValueError('Mismatched reviewed source zero evidence')
        reviewed_zeros = [dict(import_sha256=h, evidence=approvals[h],
            cells=[r for r in zero_refs if r['import_sha256'] == h]) for h in sorted({r['import_sha256'] for r in zero_refs})]
    if reviewed_zeros:
        warnings.append('部分输入采用逐期间审核的TDX源零；原标准事实仍缺项，依据与审核版本见reviewed_source_zeros，撤销后须重新发布快照')
    reviewed_cells = {(r['series'],r['offset'],r['field']) for r in zero_refs}
    def missing(series, offset, field, purpose, dest, positive=False):
        row = co if series == 'company' else rows[series].get(offset, {})
        v = row.get(field)
        evidence = cells.get((series,offset,field))
        invalid_evidence = source and series != 'company' and (evidence is None or evidence['status'] != 'available')
        if (evidence and evidence['status'] == 'available_with_reviewed_source_zero'
                and (series,offset,field) in reviewed_cells):
            invalid_evidence = False
        if (evidence and evidence['status'] == 'estimated_partial_scope'
                and field in ('cash_and_marketable_securities', 'cross_holdings')):
            invalid_evidence = False
            warnings.append(f'{field}使用已知组成的账面代理；范围、受限及经营属性未闭合，遗漏组成不等于零，可能影响股权价值')
        if source and series == 'company' and field == 'mv_equity_listing':
            invalid_evidence = evidence is None or evidence['status'] not in ('available','reported_share_price_proxy','a_share_total_share_proxy')
        invalid = bool(invalid_evidence or v is None or not isinstance(v, (int,float)) or not math.isfinite(v) or (positive and v <= 0))
        consumed_inputs.append(dict(series=series, offset=offset, field=field, purpose=purpose,
            requirement='required' if dest is required else 'conditional' if dest is conditional else 'optional',
            usable=not invalid, value=v if isinstance(v,(int,float)) and math.isfinite(v) else None,
            source=evidence or dict(status='unverified_input'),
            missing_action='retain_gap' if dest is history else 'block_selected_method'))
        if invalid:
            partial = next((e for e in (evidence or {}).get('evidence', [])
                            if isinstance(e, dict) and e.get('not_complete_target')), {})
            dest.append(dict(series=series, offset=offset, field=field, purpose=purpose,
                             available_value=v if isinstance(v,(int,float)) and math.isfinite(v) else None,
                             available_component_value=partial.get('available_component_million_cny'),
                             **cells.get((series,offset,field), {'status':'missing_or_invalid_value'})))
    try:
        window = native_input_window(record)
    except (TypeError, KeyError, ValueError) as e:
        window = dict(quarters_since_10k=0, annual_offsets=[0], quarterly_offsets=[], quarterly_slots=0)
        blockers.append('期间身份无效：'+str(e))
    prepared = bool(source and window['quarters_since_10k'])
    flow_windows = [('annual',[0]), ('ttm',[0])] if prepared else [('annual',[0]), ('quarterly',window['quarterly_offsets'])]
    for series, offsets in flow_windows:
        for offset in offsets:
            for field in ('revenues','ebit'):
                missing(series,offset,field,'base_year_or_TTM',required)
    current = 'ttm' if prepared else 'quarterly' if window['quarters_since_10k'] else 'annual'
    for field in ('cash_and_marketable_securities','bv_debt','cross_holdings','minority_interests','shares_outstanding'):
        missing(current,0,field,'per_share_equity_bridge',required,positive=field=='shares_outstanding')
    # 默认入口使用详细WACC和行业beta，市场股权权重不能把未知市值当零。
    missing('company',0,'mv_equity_listing','default_market_capital_weights',required,positive=True)
    if co.get('fx_listing_to_reporting') is not None or co.get('filing_currency') != co.get('listing_currency'):
        missing('company',0,'fx_listing_to_reporting','listing_to_reporting_currency',required,positive=True)
    fy0 = rows['annual'].get(0,{})
    rd_enabled = (fy0.get('r_and_d_expense') or 0) > 0
    lease_enabled = (fy0.get('operating_lease_expense') or 0) > 0
    if rd_enabled:
        from engine.data_dictionary import AdjustmentInputs
        n = AdjustmentInputs().amortization_period_n
        for offset in range(1,n+1):
            missing('annual',offset,'r_and_d_expense','selected_RD_capitalization',conditional)
        if prepared:
            missing('ttm',0,'r_and_d_expense','selected_RD_TTM',conditional)
        else:
            for offset in window['quarterly_offsets']:
                missing('quarterly',offset,'r_and_d_expense','selected_RD_TTM',conditional)
    if lease_enabled:
        for name in [*(f'lease_commitment_yr{i}' for i in range(1,6)), 'lease_commitment_beyond']:
            missing('company',0,name,'selected_lease_capitalization',conditional)
    if fy0.get('r_and_d_expense') is None:
        warnings.append('研发费用未知；默认未启用研发资本化，不代表无研发')
    if fy0.get('operating_lease_expense') is None:
        warnings.append('租赁支付未知；默认未启用租赁资本化，不代表无租赁或已完成租赁调整')
    if co.get('options_outstanding') is None:
        warnings.append('期权数量未知；默认未启用期权扣减，不代表无稀释')
    if (co.get('options_outstanding') or 0)>0 and not (co.get('options_avg_strike') or 0)>0:
        missing('company',0,'options_avg_strike','known_employee_options',conditional,positive=True)
    coverage = []
    for series in ('annual','quarterly'):
        for name in _ANNUAL_COLS[2:]:
            coverage.append(dict(series=series,field=name,present=sum(r.get(name) is not None for r in rows[series].values()),total=len(rows[series]),
                estimated=sum(cells.get((series,o,name), {}).get('status') in ('estimated','estimated_partial_scope') for o in rows[series])))
        needed = {0} if series=='annual' else set() if source else set(window['quarterly_offsets'])
        for offset in rows[series]:
            if offset not in needed:
                for name in ('revenues','ebit'):
                    missing(series,offset,name,'optional_history',history)
    partial_count = sum(r['status'] == 'partial_target_scope' for r in required)
    if len(required) > partial_count:
        blockers.append(f'当前估值有{len(required)-partial_count}个必需输入未供给或无效')
    if partial_count:
        blockers.append(f'已接入{partial_count}项必需输入的部分组成，但目标总额范围尚未闭合')
    if conditional:
        blockers.append(f'当前启用的调整缺少{len(conditional)}个条件输入')
    if source:
        tax = cells.get(('company',0,'effective_tax_rate'))
        if tax and tax['status'] == 'available':
            warnings.append('有效税率来自最近完整年度所得税费用/利润总额；不是现金税率、边际税率或正常化预测')
        else:
            warnings.append('公司年度有效税率未供给或须审核；原模型税率默认值仍是估值假设')
        warnings.append('债务为短长借款、债券、一年内到期非流动负债及租赁负债的账面合计；到期项范围与租赁重复资本化须另核')
        if any(r.get('minority_interests') not in (None, 0) for group in rows.values() for r in group.values()):
            warnings.append('资本诊断使用归母权益加账面少数股权的合并权益；股权桥接的少数股权扣减仍为账面代理而非市场价值，经营/投资分类未因此闭合')
        market = cells.get(('company',0,'mv_equity_listing'))
        if market and market['status'] == 'a_share_total_share_proxy':
            warnings.append('多股类市值按A股价格×含B/H股的总股本估计；缺其他股类价格及汇率，不是分股类真实合计市值，影响WACC权重和杠杆调整')
        if market and market['status'] == 'reported_share_price_proxy':
            warnings.append('市值为最新合格未复权收盘价×报告期总股本的代理；股本基期与价格日期分别留痕，未认证当前完整多股类市值')
        warnings.append('使用TDX来源报告EBIT；不代表已完成非经营/特殊项目调整或CIQ逐项口径认证')
        warnings.append('来源EBIT与现金/投资资产加回尚未完成经营范围配套核验，存在重复计价或遗漏风险；结果是条件估值，不是已正常化经营价值')
    return dict(ticker=ticker,status='blocked_required_inputs' if blockers else 'ready',
        report_period=source.get('report_period') if source else co['period_date_quarterly'],
        information_as_of=source.get('information_as_of') if source else co['data_as_of'],
        blockers=blockers,required_missing=required,conditional_missing=conditional,
        input_contract=dict(version='native-input-selection-v3', inputs=consumed_inputs,
            flow_basis='standard_cumulative_TTM' if prepared else 'standard_annual' if source else 'provided_quarters_or_annual',
            profit_basis='tdx_reported_ebit' if source else 'provided_statement_ebit',
            automatic_company_overrides=False, operating_scope_verified=False,
            scope='native_database_default_method; estimates_are_not_reported_facts',
            historical_fcff_required=False, silent_previous_period_fallback=False),
        optional_history_missing=history,warnings=warnings,input_window=window,
        adjustment_selection=dict(rd=rd_enabled,leases=lease_enabled,basis='native_database_defaults'),
        market_proxy=dict(value=co.get('mv_equity_listing'), **cells[('company',0,'mv_equity_listing')])
            if cells.get(('company',0,'mv_equity_listing'), {}).get('status') == 'a_share_total_share_proxy' else None,
        exported_asset_proxies=[dict(series=s, offset=o, field=f, value=next((part['available_component_million_cny'] for part in e['evidence']
                if isinstance(part, dict) and 'available_component_million_cny' in part), None), **e)
            for (s,o,f),e in cells.items() if e['status']=='estimated_partial_scope'
            and f in ('cash_and_marketable_securities','cross_holdings')],
        reviewed_source_zeros=reviewed_zeros,
        financial_fields=coverage,
        company_fields=[dict(field=name,present=co.get(name) is not None) for name in _COMPANIES_COLS],
        scope='当前原生入口默认选择的数据准入；不认证预测假设、市场参数或调整后经营口径；政策改变须重新检查')


def latest_ingest_summary(conn: sqlite3.Connection) -> dict | None:
    """Most recent ingest_log row, with JSON-decoded fields."""
    row = conn.execute(
        "SELECT * FROM ingest_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    for k in ("file_manifest", "unmapped_columns", "unmapped_exchanges", "warnings"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except json.JSONDecodeError:
            d[k] = []
    return d


def company_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()
    return int(row["n"]) if row else 0
