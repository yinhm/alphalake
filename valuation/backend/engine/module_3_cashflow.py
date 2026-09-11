"""
Module 3: Cash Flow & Growth — FCFF, FCFE, ROIC, ROE, reinvestment, fundamental growth rates.

Takes Module 1 adjusted financials + raw data to compute reinvestment, free cash flows,
return metrics, and expected fundamental growth rates.
"""

from __future__ import annotations

from collections import Counter
import math

from .module_1_adjustments import after_tax_operating_income, capitalize_r_and_d

from .data_dictionary import (
    AdjustedFinancials,
    RawFinancials,
    AdjustmentInputs,
    CostOfCapital,
    CashFlowMetrics,
    MacroInputs,
)


def _safe_mean(values: list[float | None]) -> float | None:
    filtered = [v for v in values if v is not None]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def _revenue_cagr(history: list[RawFinancials], years: int) -> float | None:
    """Compound annual growth rate over the last `years` years.

    CAGR uses unique fiscal-year endpoints exactly `years` apart. Different from the
    arithmetic mean of annual growth rates: CAGR is the geometric measure,
    financially correct for multi-period growth and immune to volatility.
    """
    if not history:
        return None
    latest = history[0]
    endpoints = [f for f in history if f.fiscal_year == latest.fiscal_year-years]
    if len(endpoints) != 1 or sum(f.fiscal_year == latest.fiscal_year for f in history) != 1:
        return None
    rev_now = latest.revenues
    rev_then = endpoints[0].revenues
    if rev_now is None or rev_then is None or rev_then <= 0 or rev_now <= 0:
        return None
    return (rev_now / rev_then) ** (1.0 / years) - 1.0


def _compute_historical_series(
    history: list[RawFinancials],
    r_and_d_life: int | None,
) -> dict:
    """Compute 10-year historical diagnostic series for three-story examination.

    Convention:
      NOPAT_i        = raw_EBIT_i × (1 - effective_tax_i) + R&D net adjustment
      effective_tax_i = |tax_exp_i| / |ebt_i|                   IQ_INC_TAX/IQ_EBT_EXCL
                      (unavailable if tax/EBT is missing or nonfinite, or EBT <= 0)
      IC_i           = bv_equity_i + year-specific R&D asset + bv_debt_i - cash_i
      ROIC_i         = NOPAT_i / IC_{i+1}   (prior-year IC, standard)
      S_C_i          = Revenue_i / IC_i      (current-year IC)
      Margin_i       = EBIT_i / Revenue_i    (pre-tax operating margin)
      RevGrowth_i    = Revenue_i / Revenue_{i+1} - 1
      RevCAGR_n      = (Rev[0]/Rev[n]) ** (1/n) - 1            geometric

    When R&D is enabled, each year uses only its own and earlier annual
    expenses: n cohorts for closing capital, n+1 for amortization/NOPAT.
    Missing, invalid, duplicate or nonconsecutive cohorts leave nulls.
    Explicit raw-input zeros are allowed; unresolved provider zeros must
    not be supplied as known zero expenses.

    DISPLAY window is 10 years. Prior-year denominators reach into year 10
    (index 10) so ROIC and revenue growth can compute for the oldest
    displayed year when the CIQ template returned 11 annual years (default).
    """
    if r_and_d_life is not None and r_and_d_life <= 0:
        raise ValueError('R&D amortization life must be positive')
    n_display = min(len(history), 10)
    n_total = min(len(history), 11)  # display + 1 lookback slot
    roic: list[float | None] = [None] * n_display
    s_c: list[float | None] = [None] * n_display
    margin: list[float | None] = [None] * n_display
    rev_growth: list[float | None] = [None] * n_display
    nopat_series: list[float | None] = [None] * n_display  # for NOPAT-weighted ROIC avg

    # Per-year research cohorts; never borrow the current adjustment input.
    year_counts = Counter(f.fiscal_year for f in history)
    research_delta: list[float | None] = []
    ic_current: list[float | None] = []
    for i in range(n_total):
        f = history[i]
        asset = delta = 0.0
        if r_and_d_life is not None:
            n = r_and_d_life
            cohort = history[i:i+n+1]
            valid = [r.r_and_d_expense is not None and math.isfinite(r.r_and_d_expense)
                     and r.r_and_d_expense >= 0 and r.fiscal_year == f.fiscal_year-j
                     and year_counts[r.fiscal_year] == 1 for j, r in enumerate(cohort)]
            asset = delta = None
            if len(cohort) >= n and all(valid[:n]):
                # The nth past year's closing weight is zero; no need to invent it.
                _, _, asset = capitalize_r_and_d(cohort[0].r_and_d_expense,
                    [r.r_and_d_expense for r in cohort[1:n]], n)
            if len(cohort) == n+1 and all(valid):
                _, amortization, _ = capitalize_r_and_d(cohort[0].r_and_d_expense,
                    [r.r_and_d_expense for r in cohort[1:]], n)
                delta = cohort[0].r_and_d_expense - amortization
        research_delta.append(delta)
        if all(v is not None for v in (f.bv_equity, f.bv_debt, f.cash_and_marketable_securities, asset)):
            ic_current.append(f.bv_equity + asset + f.bv_debt - f.cash_and_marketable_securities)
        else:
            ic_current.append(None)

    for i in range(n_display):
        f = history[i]
        ebit_i = f.ebit + research_delta[i] if research_delta[i] is not None else None
        rev_i = f.revenues

        # Historical diagnostics require that year's tax evidence, not a country default.
        eff_tax_i = None
        if (f.total_tax_expense is not None and math.isfinite(f.total_tax_expense)
                and f.earnings_before_tax is not None and math.isfinite(f.earnings_before_tax)
                and f.earnings_before_tax > 0):
            eff_tax_i = abs(f.total_tax_expense) / f.earnings_before_tax

        # Margin
        if ebit_i is not None and rev_i not in (None, 0):
            margin[i] = ebit_i / rev_i

        # S/C — current-year IC
        if rev_i is not None and ic_current[i] not in (None, 0):
            s_c[i] = rev_i / ic_current[i]

        # Adjacent list entries need not be adjacent fiscal years.
        annual_pair = (i + 1 < len(history)
                       and history[i + 1].fiscal_year == f.fiscal_year - 1
                       and year_counts[f.fiscal_year] == 1
                       and year_counts[history[i + 1].fiscal_year] == 1)
        # ROIC — prior-year IC with per-year NOPAT
        if annual_pair and i + 1 < n_total and ebit_i is not None and eff_tax_i is not None and ic_current[i + 1] not in (None, 0):
            nopat_i = after_tax_operating_income(AdjustedFinancials(adjusted_ebit=ebit_i), f, eff_tax_i)
            nopat_series[i] = nopat_i
            roic[i] = nopat_i / ic_current[i + 1]

        # Revenue growth — from prior year
        if annual_pair:
            rev_prev = history[i + 1].revenues
            if rev_i is not None and rev_prev not in (None, 0):
                rev_growth[i] = rev_i / rev_prev - 1

    # NOPAT-weighted ROIC average (financially more robust than the naive mean
    # when IC varies a lot year-to-year): Σ NOPAT_i / Σ IC_{i+1}
    def _nopat_weighted_roic(k: int) -> float | None:
        num = 0.0
        den = 0.0
        for j in range(min(k, n_display)):
            if nopat_series[j] is not None and j + 1 < n_total and ic_current[j + 1] not in (None, 0):
                num += nopat_series[j]
                den += ic_current[j + 1]
        return num / den if den > 0 else None

    return {
        "historical_roic_by_year": roic,
        "historical_s_c_by_year": s_c,
        "historical_margin_by_year": margin,
        "historical_revenue_growth_by_year": rev_growth,
        "historical_roic_avg_3yr": _safe_mean(roic[:3]),
        "historical_roic_avg_5yr": _safe_mean(roic[:5]),
        "historical_s_c_avg_3yr": _safe_mean(s_c[:3]),
        "historical_s_c_avg_5yr": _safe_mean(s_c[:5]),
        "historical_margin_avg_3yr": _safe_mean(margin[:3]),
        "historical_margin_avg_5yr": _safe_mean(margin[:5]),
        "historical_margin_avg_10yr": _safe_mean(margin),
        "historical_revenue_growth_avg_3yr": _safe_mean(rev_growth[:3]),
        "historical_revenue_growth_avg_5yr": _safe_mean(rev_growth[:5]),
        "historical_revenue_growth_avg_10yr": _safe_mean(rev_growth),
        "historical_revenue_cagr_3yr": _revenue_cagr(history, 3),
        "historical_revenue_cagr_5yr": _revenue_cagr(history, 5),
        "historical_revenue_cagr_10yr": _revenue_cagr(history, 10),
        "historical_roic_avg_10yr": _safe_mean(roic),
        "historical_s_c_avg_10yr": _safe_mean(s_c),
        "historical_roic_weighted_3yr": _nopat_weighted_roic(3),
        "historical_roic_weighted_5yr": _nopat_weighted_roic(5),
        "historical_roic_weighted_10yr": _nopat_weighted_roic(10),
    }


def compute_cashflow_and_growth(
    adjusted: AdjustedFinancials,
    raw: RawFinancials,
    adj_inputs: AdjustmentInputs,
    cost_of_capital: CostOfCapital,
    raw_prior_year: RawFinancials | None = None,
    macro: MacroInputs | None = None,
    raw_financials_history: list[RawFinancials] | None = None,
) -> CashFlowMetrics:
    """
    Compute reinvestment, free cash flows, return metrics, and growth rates.

    Args:
        adjusted: Module 1 output for current year.
        raw: Current year raw financials.
        adj_inputs: R&D/lease adjustment inputs.
        cost_of_capital: Module 2 output.
        raw_prior_year: Prior year raw financials (for invested capital calculation).
        macro: Macro inputs (for direct tax rate access). If omitted, reverse-engineer
               tax from cost_of_debt_pretax / cost_of_debt_aftertax (fragile fallback).

    Returns:
        CashFlowMetrics with all computed values.
    """
    # Damodaran convention for base-year NOPAT / ROIC / reinvestment-rate:
    # use the EFFECTIVE tax rate (what the firm actually paid) so historical
    # ROIC matches reality. Marginal is reserved for DCF projections
    # (Module 4) where it represents the tax the firm would pay on
    # incremental future earnings. Fall back to marginal if effective
    # isn't available, then to reverse-engineering from Kd.
    if macro is not None and macro.tax_rate_effective is not None:
        tax_rate = macro.tax_rate_effective
    elif macro is not None:
        tax_rate = macro.tax_rate_marginal
    elif cost_of_capital.cost_of_debt_pretax > 0:
        tax_rate = 1 - (cost_of_capital.cost_of_debt_aftertax / cost_of_capital.cost_of_debt_pretax)
    else:
        tax_rate = 0.0

    # --- Adjusted CapEx and D&A ---
    # CapEx is adjusted by adding back current R&D (treated as capital investment)
    # D&A is adjusted by adding R&D amortization + lease depreciation (Ginzu F34)
    adjusted_capex = (raw.capex + (adj_inputs.r_and_d_expense_current if adj_inputs.has_r_and_d else 0.0)
                      if raw.capex is not None else None)
    adjusted_d_a = (raw.d_a + adjusted.amortization_r_and_d + adjusted.depreciation_on_lease_asset
                    if raw.d_a is not None else None)
    reinvestment_firm = (adjusted_capex - adjusted_d_a + raw.change_in_noncash_wc
        if all(v is not None for v in (adjusted_capex, adjusted_d_a, raw.change_in_noncash_wc)) else None)
    reinvestment_equity = (reinvestment_firm - raw.net_debt_issued
        if reinvestment_firm is not None and raw.net_debt_issued is not None else None)
    nopat = after_tax_operating_income(adjusted, raw, tax_rate)
    fcff = nopat - reinvestment_firm if reinvestment_firm is not None else None
    adjusted_net_income = adjusted.adjusted_net_income
    fcfe = (adjusted_net_income - reinvestment_equity
            if adjusted_net_income is not None and reinvestment_equity is not None else None)

    # --- Return Metrics ---
    roic = None
    roe = None
    adjusted_invested_capital = None

    annual_prior_valid = raw_prior_year is not None and raw_prior_year.fiscal_year == raw.fiscal_year - 1
    if annual_prior_valid and raw_financials_history is not None:
        years = Counter(f.fiscal_year for f in raw_financials_history)
        annual_prior_valid = years[raw.fiscal_year] == years[raw_prior_year.fiscal_year] == 1
    if annual_prior_valid and all(v is not None for v in (raw_prior_year.bv_equity, raw_prior_year.bv_debt, raw_prior_year.cash_and_marketable_securities)):
        # Invested capital = BV Equity + BV Debt - Cash (beginning of period = prior year end)
        prior_bv_equity = raw_prior_year.bv_equity or 0.0
        prior_bv_debt = raw_prior_year.bv_debt or 0.0
        prior_cash = raw_prior_year.cash_and_marketable_securities or 0.0

        # M1 research roll-forward: opening = closing - additions + amortization.
        prior_research_asset = adjusted.value_of_research_asset
        if adj_inputs.has_r_and_d:
            prior_research_asset -= adj_inputs.r_and_d_expense_current - adjusted.amortization_r_and_d
        prior_adjusted_bv_equity = prior_bv_equity + prior_research_asset

        adjusted_invested_capital = prior_adjusted_bv_equity + prior_bv_debt - prior_cash

        if adjusted_invested_capital > 0:
            roic = nopat / adjusted_invested_capital

        if prior_adjusted_bv_equity > 0 and adjusted_net_income is not None:
            roe = adjusted_net_income / prior_adjusted_bv_equity

    # --- Reinvestment Rates ---
    rir_firm = reinvestment_firm / nopat if nopat > 0 and reinvestment_firm is not None else None
    rir_equity = reinvestment_equity / adjusted_net_income if adjusted_net_income is not None and adjusted_net_income > 0 and reinvestment_equity is not None else None

    # --- Expected Growth Rates ---
    expected_growth_ebit = roic * rir_firm if (roic is not None and rir_firm is not None) else None
    expected_growth_ni = roe * rir_equity if (roe is not None and rir_equity is not None) else None

    # --- Historical-series diagnostics for three-story examination ---
    historical: dict = {}
    if raw_financials_history:
        historical = _compute_historical_series(
            raw_financials_history,
            r_and_d_life=adj_inputs.amortization_period_n if adj_inputs.has_r_and_d else None,
        )

    return CashFlowMetrics(
        adjusted_capex=adjusted_capex,
        adjusted_d_a=adjusted_d_a,
        reinvestment_firm=reinvestment_firm,
        reinvestment_equity=reinvestment_equity,
        fcff=fcff,
        fcfe=fcfe,
        adjusted_invested_capital=adjusted_invested_capital,
        roic=roic,
        roe=roe,
        rir_firm=rir_firm,
        rir_equity=rir_equity,
        expected_growth_ebit=expected_growth_ebit,
        expected_growth_ni=expected_growth_ni,
        historical_roic_by_year=historical.get("historical_roic_by_year", []),
        historical_s_c_by_year=historical.get("historical_s_c_by_year", []),
        historical_margin_by_year=historical.get("historical_margin_by_year", []),
        historical_revenue_growth_by_year=historical.get("historical_revenue_growth_by_year", []),
        historical_roic_avg_3yr=historical.get("historical_roic_avg_3yr"),
        historical_roic_avg_5yr=historical.get("historical_roic_avg_5yr"),
        historical_s_c_avg_3yr=historical.get("historical_s_c_avg_3yr"),
        historical_s_c_avg_5yr=historical.get("historical_s_c_avg_5yr"),
        historical_margin_avg_3yr=historical.get("historical_margin_avg_3yr"),
        historical_margin_avg_5yr=historical.get("historical_margin_avg_5yr"),
        historical_revenue_growth_avg_3yr=historical.get("historical_revenue_growth_avg_3yr"),
        historical_revenue_growth_avg_5yr=historical.get("historical_revenue_growth_avg_5yr"),
        historical_roic_avg_10yr=historical.get("historical_roic_avg_10yr"),
        historical_s_c_avg_10yr=historical.get("historical_s_c_avg_10yr"),
        historical_margin_avg_10yr=historical.get("historical_margin_avg_10yr"),
        historical_revenue_growth_avg_10yr=historical.get("historical_revenue_growth_avg_10yr"),
        historical_revenue_cagr_3yr=historical.get("historical_revenue_cagr_3yr"),
        historical_revenue_cagr_5yr=historical.get("historical_revenue_cagr_5yr"),
        historical_revenue_cagr_10yr=historical.get("historical_revenue_cagr_10yr"),
        historical_roic_weighted_3yr=historical.get("historical_roic_weighted_3yr"),
        historical_roic_weighted_5yr=historical.get("historical_roic_weighted_5yr"),
        historical_roic_weighted_10yr=historical.get("historical_roic_weighted_10yr"),
    )
