"""
Public database-backed endpoints.

These are NOT gated by admin auth — anyone using the app can search for a
company and run a valuation from the ingested database. The raw CIQ .xls
files are not exposed here; only the derived tables.

Endpoints:
  GET  /api/database/search?q=<query>            autocomplete
  GET  /api/database/company/<ticker>            full structured snapshot
  GET  /api/database/company-exists/<ticker>     lightweight existence check
  POST /api/valuation/from-database {ticker}     run full pipeline, create session
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_sources import us_cn_hk_db as db

router = APIRouter(prefix="/database", tags=["database"])


# ---------------------------------------------------------------------------
# Search + lookup
# ---------------------------------------------------------------------------

@router.get("/search")
def search(q: str = "", limit: int = 20) -> dict:
    """Case-insensitive substring on company_name + exact-prefix on ticker.
    Returns up to `limit` (default 20). Empty q → empty results."""
    if not q.strip():
        return {"query": q, "results": []}
    try:
        with db.get_connection() as conn:
            rows = db.search_companies(conn, q, limit=limit)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f'Database unavailable: {error}') from error
    return {"query": q, "results": rows}


@router.get("/company-exists/{ticker:path}")
def company_exists(ticker: str) -> dict:
    """Lightweight existence check — the onboarding wizard uses this to
    decide whether to offer the 'Value from Database' option."""
    try:
        with db.get_connection() as conn:
            row = db.fetch_company(conn, ticker)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f'Database unavailable: {error}') from error
    return {
        "ticker": ticker,
        "in_database": row is not None,
        "data_as_of": row["company"].get("data_as_of") if row else None,
    }


@router.get("/company/{ticker:path}")
def company(ticker: str) -> dict:
    """Full company snapshot — identifiers + snapshot fields + annual + quarterly
    financials. Raises 404 if the ticker isn't in the DB."""
    try:
        with db.get_connection() as conn:
            row = db.fetch_company(conn, ticker)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")
    if row is None:
        raise HTTPException(status_code=404, detail=f"Ticker not in database: {ticker}")
    return row


@router.get("/compatibility/{ticker:path}")
def compatibility(ticker: str) -> dict:
    """原生SQL契约诊断；不以专项载荷替代宽表缺项。"""
    try:
        with db.get_connection() as conn:
            result = db.native_compatibility(conn, ticker)
    except Exception as error:
        raise HTTPException(status_code=503, detail=f'Database unavailable: {error}') from error
    if result is None:
        raise HTTPException(status_code=404, detail=f'Ticker not in database: {ticker}')
    if result['status']=='ready':
        from api.routes import _get_damodaran_store
        from data_sources.native_references import attach_reference_diagnostic
        result=attach_reference_diagnostic(result,_get_damodaran_store(),ticker)
    else:
        result=dict(result,financial_status=result['status'],reference_missing=[],reference_status='not_evaluated_financial_blocked')
    return result


# ---------------------------------------------------------------------------
# Valuation from database — builds CompanyValuationInput from the DB record
# and runs through the existing orchestrator.
# ---------------------------------------------------------------------------

# Imports pulled in at request time to avoid circular imports with routes.py.

class FromDatabaseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ticker: str
    risk_free_rate: float = Field(default=0.0425, ge=0, lt=1)
    industry_override: str | None = None
    country_override: str | None = None


# Mount this endpoint on a SEPARATE router with /valuation prefix so it lives
# next to the existing template-upload endpoint under the same URL tree.
valuation_router = APIRouter(prefix="/valuation", tags=["valuation"])


def _db_record_to_company_input(record: dict, risk_free_rate: float, industry_override: str | None, *, reference_store=None, country_override=None):
    """Translate a DB record into the CompanyValuationInput shape that the
    orchestrator expects. Reuses the same helpers (industry lookup, country
    ERP lookup, macro setup) as the /fetch-from-file path so the valuation
    math is identical."""
    from engine.data_dictionary import (
        CompanyValuationInput, RawFinancials, QuarterlyFinancials, MacroInputs, AdjustmentInputs,
        OptionInputs, ValuationAssumptions, MethodologyChoices, TaxHistory,
        GeographicSegment, SegmentResolution, SegmentMember,
    )
    from engine.segment_resolver import resolve_segments
    from api.routes import _get_damodaran_store, _get_industry_mapper, _clean_rating

    co = record["company"]
    window = db.native_input_window(record)
    annual_rows = record["financials_annual"]
    quarterly_rows = record["financials_quarterly"]

    # Industry / macro resolution — mirrors routes.py::fetch_from_file lines 542–587
    ticker = co["ticker"]
    store = reference_store if reference_store is not None else _get_damodaran_store()
    mapper = store.industry_mapper if reference_store is not None else _get_industry_mapper()
    company_info = mapper.lookup(ticker)
    country = country_override or (company_info.country if company_info else None)
    industry_name = industry_override or (company_info.industry_group if company_info else None)

    primary_region = "US"
    industry_data = store.lookup_industry(industry_name, region=primary_region) if industry_name else None
    industry_resolved = industry_data is not None
    if industry_data is None:
        raise ValueError('US industry reference unavailable; select an audited industry explicitly')
    industry_data_global = (
        store.lookup_industry(industry_data.industry_name, region="Global")
        if industry_data and primary_region != "Global" else None
    )

    macro = store.lookup_country(country)
    if macro is None:
        raise ValueError('Country ERP/tax reference unavailable')
    macro.risk_free_rate = risk_free_rate
    if co.get("effective_tax_rate") is not None:
        macro.tax_rate_effective = co["effective_tax_rate"]

    # FX defaults: 1.0 if currencies match; None otherwise (user supplies manually)
    fx_rate = co.get("fx_listing_to_reporting")
    fx_source = co.get("fx_rate_source") or "unset"
    if fx_rate is None and co.get("filing_currency") == co.get("listing_currency"):
        fx_rate = 1.0
        fx_source = "same currency"

    # Build only complete annual rows, preserving their actual fiscal years.
    # Base fiscal year: infer from period_date_annual (YYYY-MM-DD).
    base_fy_year = datetime.now().year
    pda = co.get("period_date_annual")
    if pda:
        try:
            base_fy_year = datetime.fromisoformat(pda[:10]).year
        except ValueError:
            pass

    raw_financials: list[RawFinancials] = []
    historical_research_expenses = {}
    # Map fy_offset → fiscal_year (offset 0 → base, offset 1 → base-1, etc.)
    annual_by_offset = {r["fy_offset"]: r for r in annual_rows}
    if not annual_by_offset.get(0):
        raise ValueError("FY0 required; cannot promote an older year")
    for offset in sorted(annual_by_offset.keys()):
        r = annual_by_offset[offset]
        if r.get("revenues") is None or r.get("ebit") is None:
            if offset == 0:
                raise ValueError("FY0 revenues/EBIT required; cannot promote an older year")
            if r.get("r_and_d_expense") is not None:
                historical_research_expenses[base_fy_year-offset] = r["r_and_d_expense"]
            continue
        is_current = (offset == 0)
        rf = RawFinancials(
            fiscal_year=base_fy_year - offset,
            revenues=r.get("revenues"),
            ebit=r.get("ebit"),
            ebitda=r.get("ebitda"),
            net_income=r.get("net_income"),
            interest_expense=r.get("interest_expense"),
            d_a=r.get("d_a"),
            r_and_d_expense=r.get("r_and_d_expense"),
            capex=r.get("capex"),
            operating_lease_expense=r.get("operating_lease_expense"),
            earnings_before_tax=r.get("earnings_before_tax"),
            total_tax_expense=r.get("total_tax_expense"),
            bv_equity=r.get("bv_equity"),
            bv_debt=r.get("bv_debt"),
            cash_and_marketable_securities=r.get("cash_and_marketable_securities"),
            cross_holdings=r.get("cross_holdings"),
            minority_interests=r.get("minority_interests"),
            shares_outstanding=r.get("shares_outstanding"),
            # Listing-currency snapshots land on the current-year row only
            stock_price=co.get("stock_price_listing") if is_current else None,
            mv_equity_listing=co.get("mv_equity_listing") if is_current else None,
        )
        raw_financials.append(rf)

    # Quarterly
    quarterly_financials: list[QuarterlyFinancials] = []
    q_by_offset = {r["fq_offset"]: r for r in quarterly_rows}
    for offset in range(max(window["quarterly_slots"], max(q_by_offset, default=-1)+1)):
        r = q_by_offset.get(offset, {})
        quarterly_financials.append(QuarterlyFinancials(
            fiscal_year=base_fy_year,  # CIQ quarterly is current-year slice; precise FY offsets aren't critical here
            revenues=r.get("revenues"),
            ebit=r.get("ebit"),
            ebitda=r.get("ebitda"),
            net_income=r.get("net_income"),
            interest_expense=r.get("interest_expense"),
            d_a=r.get("d_a"),
            r_and_d_expense=r.get("r_and_d_expense"),
            capex=r.get("capex"),
            operating_lease_expense=r.get("operating_lease_expense"),
            earnings_before_tax=r.get("earnings_before_tax"),
            total_tax_expense=r.get("total_tax_expense"),
            bv_equity=r.get("bv_equity"),
            bv_debt=r.get("bv_debt"),
            cash_and_marketable_securities=r.get("cash_and_marketable_securities"),
            cross_holdings=r.get("cross_holdings"),
            minority_interests=r.get("minority_interests"),
            shares_outstanding=r.get("shares_outstanding"),
            stock_price=co.get("stock_price_listing") if offset == 0 else None,
            mv_equity_listing=co.get("mv_equity_listing") if offset == 0 else None,
        ))

    # Option inputs — disable BSM when data is incomplete. Lenovo is the
    # typical case: options_outstanding = 132M but options_avg_strike = 0
    # (CIQ screener's Reported Currency bug returned 0 for strike on some
    # tickers); without a strike, BSM divides by zero. Safer to skip
    # option dilution and surface as UnresolvedField than crash.
    num_opts = co.get("options_outstanding") or 0.0
    avg_strike = co.get("options_avg_strike") or 0.0
    has_opts = num_opts > 0 and avg_strike > 0
    option_inputs = OptionInputs(
        number_of_options=num_opts,
        average_strike_price=avg_strike,
        average_maturity=5.0,  # screener doesn't include maturity — plan §2 acceptable default
        stock_price_std_dev=(industry_data.std_dev_stock if industry_data and industry_data.std_dev_stock else 0.35),
        dividend_yield=0.0,
        has_options=has_opts,
    )

    # Adjustment inputs — R&D / lease past-year arrays populated from annual data
    adj_inputs = AdjustmentInputs()
    # Pull past R&D values from older annual rows for the R&D capitalization
    past_rd: list[float] = []
    adj_inputs.has_r_and_d = (annual_by_offset.get(0,{}).get("r_and_d_expense") or 0) > 0
    if adj_inputs.has_r_and_d:
        for offset in range(1, adj_inputs.amortization_period_n+1):
            value = annual_by_offset.get(offset,{}).get("r_and_d_expense")
            if value is None:
                raise ValueError(f"R&D capitalization requires consecutive year offset {offset}")
            past_rd.append(float(value))
        adj_inputs.r_and_d_expense_current = float(annual_by_offset[0]["r_and_d_expense"])
    adj_inputs.r_and_d_expense_past = past_rd
    # Lease commitments from snapshot fields — schema uses
    # operating_lease_commitments: list[float] + separate has_leases flag.
    # Append the beyond-5-yr bucket as an optional 6th entry (matches the
    # template upload's AdjustmentInputs shape).
    lease_yrs = [co.get(f"lease_commitment_yr{i}") or 0.0 for i in range(1, 6)]
    beyond = float(co.get("lease_commitment_beyond") or 0.0)
    commitments = [float(v) for v in lease_yrs]
    if beyond > 0:
        commitments.append(beyond)
    adj_inputs.operating_lease_commitments = commitments
    # Current-year lease expense isn't captured on the company snapshot;
    # infer from the FY-0 annual row if available.
    if annual_by_offset.get(0) and annual_by_offset[0].get("operating_lease_expense"):
        adj_inputs.operating_lease_expense_current = float(annual_by_offset[0]["operating_lease_expense"])
    # Match the template path's convention: has_operating_leases is driven by
    # current-year lease EXPENSE, not merely the presence of commitment rows.
    # Screener snapshots may include multi-year commitments even when the firm
    # doesn't break out a separate lease expense — in that case the template
    # leaves leases disabled (the commitments are discounted elsewhere).
    adj_inputs.has_operating_leases = adj_inputs.operating_lease_expense_current > 0

    # Compute quarters_since_10k from period dates so the LTM rotation
    # pulls the correct number of quarters forward. E.g. Lenovo's
    # FY-0 ends Mar 31, 2025 and FQ-0 ends Dec 31, 2025 → 3 quarters.
    quarters_since = window["quarters_since_10k"]

    # Build methodology choices. If CIQ-sourced S&P issuer rating is
    # available, wire it to actual_rating + kd_approach='actual_rating'
    # so Module 2 uses the actual rating instead of industry fallback.
    # Geographic segments — run the ingested screener segments through the
    # same resolver the template path uses so Cost-of-Capital can blend the
    # country ERPs and show the mapped/unresolved state on the segment UI.
    raw_geo = co.get("geographic_segments") or []
    geo_segments_input: list[GeographicSegment] = []
    for s in resolve_segments(raw_geo, store):
        r = s["resolution"]
        geo_segments_input.append(GeographicSegment(
            name=s["name"],
            revenue=s["revenue"],
            pct=s.get("pct"),
            resolution=SegmentResolution(
                raw_name=r["raw_name"],
                mapped_to=r.get("mapped_to"),
                mapped_kind=r.get("mapped_kind", "unresolved"),
                erp=r.get("erp"),
                members=[SegmentMember(**m) for m in (r.get("members") or [])],
                confidence=r.get("confidence", 0.0),
                source=r.get("source", "auto"),
                note=r.get("note"),
            ),
        ))

    methodology_kwargs: dict = {"geographic_segments": geo_segments_input}
    rating_raw = co.get("actual_rating_fc") or co.get("actual_rating_lc")
    # Normalize CIQ S&P/Moody's labels ("BBB", "A-", "Baa2"…) to the compound
    # bucket key used by the rating-spread table (e.g. "Baa2/BBB"). Without
    # this, module_2 falls back to industry spread with a warning.
    rating = _clean_rating(rating_raw)
    if rating:
        methodology_kwargs["actual_rating"] = rating
        methodology_kwargs["kd_approach"] = "actual_rating"
    methodology = MethodologyChoices(**methodology_kwargs)

    # Assemble
    inputs = CompanyValuationInput(
        ticker=ticker,
        company_name=co.get("company_name"),
        country=country,
        reporting_currency=co.get("filing_currency"),
        stock_price_currency=co.get("listing_currency"),
        fx_rate=fx_rate,
        fx_rate_source=fx_source,
        fx_rate_date=co.get("period_date_annual"),
        raw_financials=raw_financials,
        historical_research_expenses=historical_research_expenses,
        quarterly_financials=quarterly_financials,
        quarters_since_10k=quarters_since,
        period_date_10k=co.get("period_date_annual"),
        period_date_10q=co.get("period_date_quarterly"),
        effective_tax_rate_ciq=co.get("effective_tax_rate"),
        adjustment_inputs=adj_inputs,
        macro_inputs=macro,
        industry_data=industry_data,
        industry_data_global=industry_data_global,
        option_inputs=option_inputs,
        valuation_assumptions=ValuationAssumptions(),
        methodology_choices=methodology,
    )
    return inputs, industry_resolved


@valuation_router.post("/from-database")
def from_database(req: FromDatabaseRequest) -> dict:
    """Run the full valuation pipeline starting from a DB-sourced ticker.
    Returns the same shape as /valuation/fetch-from-file."""
    try:
        with db.get_connection() as conn:
            record = db.fetch_company(conn, req.ticker)
            compatibility = db.native_compatibility(conn, req.ticker) if record and record.get("data_source") else None
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")
    if record is None:
        raise HTTPException(status_code=404, detail=f"Ticker not in database: {req.ticker}")

    if compatibility is not None and compatibility['status'] != 'ready':
        raise HTTPException(status_code=422, detail='原生估值数据尚未达标：' + '；'.join(compatibility['blockers']) + '。详细字段见 /api/database/compatibility/' + req.ticker)

    from engine.orchestrator import run_full_valuation
    from api.routes import _build_industry_lookup, _get_damodaran_store, _report_to_dict, _build_unresolved_fields
    from api.session_store import create_session

    store = _get_damodaran_store()
    source=record.get('data_source') or {}
    if source and source.get('reference_snapshot_id') != store.reference_snapshot.get('id'):
        raise HTTPException(status_code=503,detail='Financial/reference snapshot changed; retry against one publication')
    from data_sources.native_references import reference_gaps
    gaps=reference_gaps(store,req.ticker,req.industry_override,req.country_override)
    if gaps:
        raise HTTPException(status_code=422,detail='；'.join(r['reason'] for r in gaps))
    try:
        inputs, industry_resolved = _db_record_to_company_input(record, req.risk_free_rate, req.industry_override, reference_store=store, country_override=req.country_override)
    except ValueError as e:
        raise HTTPException(status_code=422,detail=str(e)) from e
    ind_lookup = _build_industry_lookup(store)
    report = run_full_valuation(inputs, industry_lookup=ind_lookup)
    if compatibility is not None:
        report.warnings.extend(compatibility["warnings"])
        if compatibility["optional_history_missing"]:
            report.warnings.append("可选历史不完整；历史统计仅使用完整且期间对齐的数据，不影响当前输入准入")

    # Use the same session layout + serializer the template path uses so the
    # response shape is byte-identical.
    unresolved = _build_unresolved_fields(inputs, store, industry_resolved=industry_resolved)
    if not industry_resolved:
        report.warnings.append('公司行业未匹配；当前行业仅为原模型占位，请使用原页面确认行业，不能视为公司分类事实。')
    session = create_session(inputs, report, unresolved_fields=unresolved, reference_store=store)
    session.source_tracker.record('macro_inputs.risk_free_rate',
        'User-provided assumption; not an automatically selected market yield' if 'risk_free_rate' in req.model_fields_set
        else 'Original model default 0.0425; not an observed market yield')
    session.source_tracker.record('valuation_assumptions','Original model defaults; editable assumptions, not reported company facts')
    session.source_tracker.record('methodology_choices','Original model defaults; explicit later choices are recorded separately')
    for field,value in (('industry_data.industry_name',req.industry_override),('country',req.country_override)):
        if value is not None:
            session.source_tracker.record(field,'User-selected reference assumption; company classification unchanged')
    if compatibility and (compatibility['exported_asset_proxies'] or compatibility['market_proxy']):
        proxy = dict(session.valuation_proxy or dict(version=record['data_source']['contract'],
            status='estimated', source_snapshot=record['data_source'],
            basis='initial_database_estimates; subsequent_user_overrides_are_separate', limitations=[]))
        proxy['exported_asset_proxies'] = compatibility['exported_asset_proxies']
        proxy['limitations'] = list(dict.fromkeys(proxy['limitations'] + [
            '现金和长期投资使用已知组成的账面代理；范围、受限及经营属性未闭合，遗漏不等于零。',
            '资产加回与非经营收益剔除尚未逐公司配套审核；条件估值可能重复计价或遗漏，不能视为完整公允价值。']))
        if compatibility['market_proxy']:
            proxy['market_proxy'] = compatibility['market_proxy']
            proxy['limitations'].append('多股类市值采用A股价格×总股本代理；未取得B/H股价格及汇率，可能影响WACC权重、杠杆调整和估值。')
        session.valuation_proxy = proxy
    result = _report_to_dict(session)
    # Mirror the template-path's root-level context fields (company_name,
    # country, industry_name). Template path sets these after
    # _report_to_dict in routes.py::fetch_from_file; we replicate here so
    # the two paths return identical top-level shapes.
    result["company_name"] = inputs.company_name
    result["country"] = inputs.country
    result["industry_name"] = inputs.industry_data.industry_name if inputs.industry_data else None
    return result
