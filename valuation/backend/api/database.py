"""
Public database-backed endpoints.

These are NOT gated by admin auth — anyone using the app can search for a
company and run a valuation from the ingested database. The raw CIQ .xls
files are not exposed here; only the derived tables.

Endpoints:
  GET  /api/database/search?q=<query>            autocomplete
  GET  /api/database/company/<ticker>            full structured snapshot
  GET  /api/database/company-exists/<ticker>     lightweight existence check
  GET  /api/database/wacc-references              published CNY/Damodaran sources
  POST /api/valuation/from-database {ticker}     run full pipeline, create session
"""
from __future__ import annotations

import sys
import json
import math
from datetime import date, datetime, timedelta
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

@router.get("/wacc-references")
def wacc_references() -> dict:
    """提供版本绑定的人民币及达摩达兰来源参考，不计算或自动采用公司WACC。"""
    from api.routes import _get_damodaran_store
    store = _get_damodaran_store()
    return dict(reference_snapshot_id=store.reference_snapshot['id'], references=store.wacc_references,
                selection='source_references_only; explicit_policy_required')


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
    risk_free_rate: float | None = Field(default=None, ge=0, lt=1)
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
        GeographicSegment, SegmentResolution, SegmentMember, PreparedTTM,
    )
    from engine.segment_resolver import resolve_segments
    from api.routes import _get_damodaran_store, _get_industry_mapper, _clean_rating

    co = record["company"]
    window = db.native_input_window(record)
    annual_rows = record["financials_annual"]
    quarterly_rows = record["financials_quarterly"]
    use_prepared_ttm = bool(record.get('data_source') and window['quarters_since_10k'])

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
            consolidated_book_equity=(r["bv_equity"] + r["minority_interests"]
                if record.get("data_source", {}).get("annual_bv_equity") == "parent_attributable"
                and r.get("bv_equity") is not None and r.get("minority_interests") is not None else None),
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
            consolidated_book_equity=(r["bv_equity"] + r["minority_interests"]
                if record.get("data_source", {}).get("annual_bv_equity") == "parent_attributable"
                and r.get("bv_equity") is not None and r.get("minority_interests") is not None else None),
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

    prepared_ttm = None
    if use_prepared_ttm:
        from decimal import Decimal
        end = date.fromisoformat(co['period_date_quarterly'])
        current, prior = {}, {}
        for field in ('revenues', 'ebit'):
            evidence = record['ttm_evidence'].get(field, [])
            for values, term_end in ((current, end), (prior, end.replace(year=end.year-1))):
                term = next((r for r in evidence if r.get('period') == term_end.isoformat() and 'value' in r), None)
                values[field] = float(Decimal(term['value']) / Decimal(1000000)) if term else None
        r = record['financials_ttm']
        prepared_ttm = PreparedTTM(
            financials=RawFinancials(fiscal_year=base_fy_year,
                **{field: r.get(field) for field in db._ANNUAL_COLS[2:]},
                consolidated_book_equity=(r['bv_equity'] + r['minority_interests']
                    if record['data_source'].get('annual_bv_equity') == 'parent_attributable'
                    and r.get('bv_equity') is not None and r.get('minority_interests') is not None else None),
                stock_price=co.get('stock_price_listing'), mv_equity_listing=co.get('mv_equity_listing')),
            period_start=end.replace(year=end.year-1)+timedelta(days=1), period_end=end,
            information_as_of=datetime.fromisoformat(record['data_source']['information_as_of']),
            currency=co['filing_currency'], money_unit='million_reporting_currency', shares_unit='million_shares',
            provenance=dict(basis='standard_cumulative_ttm', contract=record['data_source']['contract'],
                source_database_sha256=record['data_source']['source_database_sha256'],
                formula='FY + current_YTD - prior_same_YTD',
                cumulative_windows=json.dumps(dict(current=current, prior=prior, quarters=window['quarters_since_10k'])),
                quarterly_display=json.dumps([q.model_dump(mode='json') for q in quarterly_financials]),
                components=json.dumps(record['ttm_evidence'], ensure_ascii=False, sort_keys=True)))
        # 比较TTM仍用标准累计三项；无需八个完整季度，不增加估值准入条件。
        prior_end = end.replace(year=end.year-1)
        comparison_periods = [f'{prior_end.year-1}-12-31', prior_end.isoformat(),
                              prior_end.replace(year=prior_end.year-1).isoformat()]
        terms = [[f for f in record.get('standard_financials', [])
                  if f['field'] == 'revenue_cumulative' and f['period'] == period]
                 for period in comparison_periods]
        if all(len(matches) == 1 for matches in terms):
            facts = [matches[0] for matches in terms]
            expected_types = ['FY', {3:'Q1', 6:'H1', 9:'9M', 12:'FY'}[end.month]]
            if (all(f['unit'] == co['filing_currency'] for f in facts)
                    and [f['period_type'] for f in facts] == [expected_types[0], expected_types[1], expected_types[1]]
                    and len({f['statement_scope'] for f in facts}) == 1
                    and facts[0]['statement_scope'] == next((f['statement_scope'] for f in record.get('standard_financials', [])
                        if f['field'] == 'revenue_cumulative' and f['period'] == end.isoformat()), None)):
                amounts = [Decimal(f['value']) for f in facts]
                if all(v.is_finite() for v in amounts):
                    prepared_ttm.provenance['prior_ttm_revenue'] = json.dumps(dict(
                        period_end=prior_end.isoformat(), currency=co['filing_currency'],
                        money_unit='million_reporting_currency',
                        revenues=str((amounts[0]+amounts[1]-amounts[2])/Decimal(1000000)),
                        formula='FY + current_YTD - prior_same_YTD',
                        components=[f | {'coefficient': coefficient} for f, coefficient in zip(facts, (1,1,-1))]),
                        ensure_ascii=False, sort_keys=True)

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
        prepared_ttm=prepared_ttm,
        quarterly_financials=[] if use_prepared_ttm else quarterly_financials,
        quarters_since_10k=0 if use_prepared_ttm else quarters_since,
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


def _initialize_native_cny_financing(inputs, store, risk_free_rate):
    """同一发布内的人民币融资条件；沿用原Beta/ERP/权重和可编辑Kd入口。"""
    from datetime import date
    from decimal import Decimal
    from data_sources.alphalake_wacc import ReferenceSnapshot
    from tools.audit_native_coverage import FINANCIAL_INDUSTRIES
    if (inputs.reporting_currency!='CNY' or inputs.stock_price_currency!='CNY'
            or not inputs.ticker.startswith(('SHSE:', 'SZSE:'))
            or inputs.industry_data.industry_name in FINANCIAL_INDUSTRIES):
        return {}, []
    refs=ReferenceSnapshot.model_validate(store.wacc_references)
    curves=[r for r in refs.yield_curve if r['currency']=='CNY' and r['tenor_months']==120]
    sovereign=[r for r in refs.country_risk if r['subject_code']=='CN' and r['metric_code']=='sovereign_default_spread']
    if len(curves)!=1 or len(sovereign)!=1:
        raise ValueError('unique CNY ten-year curve and China sovereign spread required')
    curve, country=curves[0], sovereign[0]
    gov, spread=Decimal(curve['value']),Decimal(country['value'])
    rf=gov-spread if risk_free_rate is None else Decimal(str(risk_free_rate))
    basis=store.native_debt_bases[inputs.industry_data.region]
    industry_spread=Decimal(str(inputs.industry_data.cost_of_debt_pretax))-Decimal(basis['value'])
    if rf<0 or not 0<=industry_spread<1 or not 0<=rf+spread+industry_spread<1:
        raise ValueError('invalid CNY financing condition; no clipping or USD fallback')
    inputs.macro_inputs.risk_free_rate=float(rf)
    inputs.methodology_choices.kd_approach='direct'
    inputs.methodology_choices.kd_direct_input=float(rf+spread+industry_spread)
    prefix='人民币融资初值v1；可编辑条件代理，非公司市场WACC：'
    sources={
        'macro_inputs.risk_free_rate':('User-provided CNY risk-free assumption' if risk_free_rate is not None else
            prefix+f"{curve['observation_date']}十年人民币国债{gov}减中国主权利差{spread}；发布{curve['release_id']}/{country['release_id']}"),
        'methodology_choices.kd_approach':prefix+'direct仅表示显式供给融资初值，不表示债务成本已被市场核实',
        'methodology_choices.kd_direct_input':prefix+f"Rf_CNY+中国主权利差+US行业利差；US行业Kd减同工作簿USD基准{basis['value']}（发布{basis['release_id']}，{basis['source_locator']}）；行业信用/全额主权传导为代理，非公司评级",
    }
    age=(refs.information_as_of.date()-date.fromisoformat(curve['observation_date'])).days
    warnings=[f"人民币融资初值采用已发布参考（国债{curve['observation_date']}，截至快照{age}天）；未核实公司借款报价/经营地域，用户覆盖优先。"]
    if age>7:
        warnings.append('人民币国债超过显式市场WACC政策的7天新鲜度；当前仅为归档来源初值，等待手工刷新，不认证新鲜市场WACC。')
    return sources,warnings


def _initialize_native_assumptions(inputs, report):
    """已批准的可编辑来源条件；不选择胜出预测，不覆盖显式路径或输入。"""
    assumptions = inputs.valuation_assumptions
    if assumptions.annual_forecast is not None:
        return {}
    sources = {}

    def initialize(name, value, basis):
        if getattr(assumptions, name) is None and value is not None and math.isfinite(value):
            setattr(assumptions, name, value)
            sources['valuation_assumptions.' + name] = '来源初值v1；条件假设，未认证预测：' + basis

    growth = inputs.company_metrics.revenue_growth
    if (assumptions.revenue_growth_next_year is None
            and assumptions.revenue_growth_years_2_5 is None
            and growth is not None and math.isfinite(growth) and -1 < growth <= 1):
        initialize('revenue_growth_next_year', growth, inputs.company_metrics.revenue_growth_basis)
        initialize('revenue_growth_years_2_5', growth, '同一历史增长作为过渡起点，不保持五年；其后沿现有引擎向稳态衰减')
        if 'high_growth_years' not in assumptions.model_fields_set:
            assumptions.high_growth_years = 1
            sources['valuation_assumptions.high_growth_years'] = '来源初值v1；本地条件：历史增长只保持首年，之后过渡；不认证公司竞争优势持续期。税率及WACC过渡也按此期间启动'
        if growth < 0:
            report.warnings.append('来源初值：收入收缩条件可能隐含资本释放；回收金额、恢复速度与生存风险尚未核实，不能当成已实现现金')
    elif assumptions.revenue_growth_next_year is None and assumptions.revenue_growth_years_2_5 is None:
        report.warnings.append('来源初值：同比缺失或超出既有条件范围(-100%,100%]；增长保留原应用条件，请显式判断。保留原诊断，不截尾，不将真实零或负收入补成正数')
    current = report.ltm_financials
    if assumptions.operating_margin_next_year is None and current.revenues is not None and current.revenues > 0:
        margin = report.adjusted.adjusted_ebit / current.revenues
        initialize('operating_margin_next_year', margin, '当前调整后EBIT/同窗收入；含既有研发调整，不是原始报表利润率')
        initialize('target_operating_margin', margin, '当前调整后利润率持平延续；未与不同口径行业均值混合，不认证可持续目标')
    if assumptions.annual_sales_to_capital is None and assumptions.sales_to_capital_high is None:
        ratio = inputs.industry_data.sales_to_capital
        if ratio is not None and math.isfinite(ratio) and ratio > 0:
            basis = f'{inputs.industry_data.region}/{inputs.industry_data.industry_name}行业销售资本倍率参考；作为新增投入效率的条件代理，不是公司边际效率'
            initialize('sales_to_capital_high', ratio, basis)
            initialize('sales_to_capital_stable', ratio, basis + '；终值再投资仍由g/ROIC决定')
        else:
            report.warnings.append('来源初值：没有合格匹配行业资本倍率；保留原2.5倍条件，不能冒称有来源的公司效率')
    return sources


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
        inputs, industry_resolved = _db_record_to_company_input(record, .0425 if req.risk_free_rate is None else req.risk_free_rate, req.industry_override, reference_store=store, country_override=req.country_override)
        financing_sources, financing_warnings = _initialize_native_cny_financing(inputs, store, req.risk_free_rate)
    except ValueError as e:
        raise HTTPException(status_code=422,detail=str(e)) from e
    ind_lookup = _build_industry_lookup(store)
    report = run_full_valuation(inputs, industry_lookup=ind_lookup)
    initialization_sources = _initialize_native_assumptions(inputs, report)
    if financing_sources:
        inputs.valuation_assumptions.cost_of_capital_stable_override=report.cost_of_capital.wacc
        financing_sources['valuation_assumptions.cost_of_capital_stable_override']='人民币融资初值v1；正常税盾初始WACC持有至稳态；现有税盾不可用调整另算，ROIC沿既有无超额回报分支。持有信用/杠杆/Beta是条件，不是成熟业务预测'
        initialization_sources.update(financing_sources)
    initialization_warnings = list(report.warnings)
    if initialization_sources:
        report = run_full_valuation(inputs, industry_lookup=ind_lookup)
        report.warnings.extend(w for w in initialization_warnings if w.startswith('来源初值：'))
    report.warnings.extend(financing_warnings)
    report.warnings.append('经营及融资参数为可编辑来源初值，不是已验证公司预测或市场WACC。用户覆盖优先，清空恢复引擎回退，重新加载恢复来源初值；初始与稳态融资条件分别编辑')
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
        'User-provided assumption; not an automatically selected market yield' if req.risk_free_rate is not None
        else 'Original model default 0.0425; not an observed market yield')
    session.source_tracker.record('valuation_assumptions','native-source-initialization-v1; editable conditional starting values, not validated forecasts; unspecified inputs retain original model conditions')
    for field, basis in initialization_sources.items():
        session.source_tracker.record(field, basis)
    session.source_tracker.record('methodology_choices', 'Native CNY financing initialization v1; other original model defaults; user overrides are separate' if financing_sources else 'Original model defaults; explicit later choices are recorded separately')
    for field,value in (('industry_data.industry_name',req.industry_override),('country',req.country_override)):
        if value is not None:
            session.source_tracker.record(field,'User-selected reference assumption; company classification unchanged')
    association = store.reference_snapshot.get('issuer_associations',{}).get(req.ticker)
    if association:
        for field, override in (('industry_data.industry_name',req.industry_override),('country',req.country_override)):
            if override is None:
                session.source_tracker.record(field, 'Reviewed same-issuer reference association; source '+association['source_ticker']+'; review '+association['review_sha256'])
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
