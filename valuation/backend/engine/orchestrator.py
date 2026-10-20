"""
Valuation Orchestrator — chains Modules 1-6 into a complete valuation pipeline.

Supports full valuation and incremental recomputation when a user edits a variable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .data_dictionary import (
    CompanyValuationInput,
    AdjustedFinancials,
    CostOfCapital,
    CashFlowMetrics,
    DCFResult,
    MultiplesResult,
    FinalValuation,
    InvalidValuationInput,
)
from .module_1_adjustments import compute_adjustments
from .module_2_risk import compute_cost_of_capital, resolve_debt_cost
from .module_3_cashflow import compute_cashflow_and_growth
from .module_4_dcf import compute_dcf
from .module_5_multiples import compute_multiples
from .module_6_options import compute_options_and_final_value
from .ltm_calculator import compute_ltm_financials
from .company_metrics import compute_company_metrics, compute_revenue_growth

logger = logging.getLogger(__name__)


@dataclass
class ValuationReport:
    """Complete valuation output from all modules."""
    ticker: str
    ltm_financials: object = None          # RawFinancials — Ginzu-rotated base year
    adjusted: AdjustedFinancials | None = None
    cost_of_capital: CostOfCapital | None = None
    cashflow: CashFlowMetrics | None = None
    dcf: DCFResult | None = None
    multiples: MultiplesResult | None = None
    final: FinalValuation | None = None
    warnings: list[str] = field(default_factory=list)
    equity_bridge: dict | None = None
    convertible_claim: dict | None = None


def run_full_valuation(
    inputs: CompanyValuationInput,
    industry_lookup=None,
) -> ValuationReport:
    """
    Run the full M1 → M6 valuation pipeline.

    Args:
        inputs: Complete input data bundle from Module 0.
        industry_lookup: optional callable(industry_name, region) -> IndustryData | None.
            Enables multi-business β. Typically wired to DamodaranStore.lookup_industry.

    Returns:
        ValuationReport with all intermediate and final results.
    """
    report = ValuationReport(ticker=inputs.ticker)

    # Sort raw financials: fiscal_year 0 = most recent
    financials = sorted(inputs.raw_financials, key=lambda r: r.fiscal_year, reverse=True)
    if not financials and inputs.prepared_ttm is None:
        report.warnings.append("No financial data available")
        return report

    # --- FX derivation of reporting-ccy market values ---
    # Background: CIQ's "Reported Currency" modifier is inert for market data
    # (stock price, market cap, option strike) — those always come through in
    # LISTING currency regardless. Verified against 5 tickers on 2026-05-04.
    # Whenever fx_rate is set (manual override, currency-table-default for the
    # DB path, or "same currency" for single-currency filers), re-derive the
    # reporting-ccy variants from listing × fx so the valuation math (WACC uses
    # mv_equity; P/V ratio uses stock_price_reporting) reflects the resolved
    # rate rather than stale/missing CIQ values.
    #
    # Applied to every row in raw_financials (for the current year, these are
    # the fields that matter; historical years' mv_equity/stock_price are
    # typically None but we cover them for consistency).
    if inputs.fx_rate is not None:
        fx = inputs.fx_rate
        for fin in financials + list(inputs.quarterly_financials or []) + ([inputs.prepared_ttm.financials] if inputs.prepared_ttm else []):
            if fin.stock_price is not None:
                fin.stock_price_reporting = fin.stock_price * fx
            if fin.mv_equity_listing is not None:
                fin.mv_equity = fin.mv_equity_listing * fx
            elif fin.mv_equity is not None and fin.stock_price is not None \
                 and fin.stock_price_reporting is not None \
                 and abs(fin.stock_price_reporting / fin.stock_price - fx) > 1e-6:
                # mv_equity_listing was never populated; fall back to
                # rescaling existing mv_equity by the fx-rate ratio.
                old_fx = fin.stock_price_reporting / fin.stock_price
                fin.mv_equity = fin.mv_equity * (fx / old_fx)

    raw_fy0 = financials[0] if financials else None
    # Annual opening capital does not align with a rotated LTM flow window.
    raw_prior = financials[1] if (len(financials) > 1 and inputs.prepared_ttm is None
                                 and not inputs.quarters_since_10k) else None

    # --- LTM rotation (Ginzu Trailing 12 month formula) ---
    # Build the Ginzu-rotated base year: LTM flow values + FQ-0 balance sheet snapshot.
    # Downstream modules (M1 adjustments, M2 WACC, M3 cashflow, M4 DCF) operate on this
    # LTM-rotated base, NOT on the stale FY0 10-K values.
    ltm = inputs.prepared_ttm.financials.model_copy() if inputs.prepared_ttm else compute_ltm_financials(
        raw_fy0,
        list(inputs.quarterly_financials or []),
        inputs.quarters_since_10k or 0,
    )
    report.ltm_financials = ltm
    raw_current = ltm   # base year for all downstream modules

    # --- M2 consistency: source R&D current from LTM, not FY-0 ---
    # Ensures R&D capitalization uses the same time window (LTM) as the base-year EBIT.
    if ltm.r_and_d_expense is not None and inputs.adjustment_inputs.has_r_and_d:
        inputs.adjustment_inputs.r_and_d_expense_current = ltm.r_and_d_expense

    cost_of_debt_pretax = 0.0  # Unused when no off-balance-sheet leases are selected.
    if inputs.adjustment_inputs.has_operating_leases:
        if inputs.methodology_choices.kd_approach == 'synthetic_rating':
            raise InvalidValuationInput('lease/rating circular inputs require an explicit pre-tax debt cost; no automatic iteration policy')
        unleased = compute_adjustments(raw_current,
            inputs.adjustment_inputs.model_copy(update={'has_operating_leases': False}), 0.0)
        cost_of_debt_pretax, *_ = resolve_debt_cost(unleased, inputs.macro_inputs,
            inputs.industry_data, inputs.methodology_choices, raw_current.interest_expense or 0.0)

    # --- Module 1: Financial Adjustments ---
    report.adjusted = compute_adjustments(
        raw_current, inputs.adjustment_inputs, cost_of_debt_pretax
    )

    # --- Module 2: Risk & Cost of Capital ---
    mv_equity = raw_current.mv_equity or 0.0
    book_debt = raw_current.bv_debt or 0.0
    interest_expense = raw_current.interest_expense or 0.0
    report.cost_of_capital = compute_cost_of_capital(
        report.adjusted,
        inputs.macro_inputs,
        inputs.industry_data,
        mv_equity,
        methodology=inputs.methodology_choices,
        industry_lookup=industry_lookup,
        book_debt=book_debt,
        interest_expense=interest_expense,
        industry_global=inputs.industry_data_global,
        market_debt=raw_current.mv_debt,
    )

    # --- Module 3: Cash Flow & Growth ---
    # Pass the full raw_financials list (most-recent-first) so M3 can
    # compute historical ROIC / S/C / margin / growth series for the
    # three-story examination panel on Stories to Numbers.
    report.cashflow = compute_cashflow_and_growth(
        report.adjusted, raw_current, inputs.adjustment_inputs,
        report.cost_of_capital, raw_prior, macro=inputs.macro_inputs,
        raw_financials_history=financials,
        historical_research_expenses=inputs.historical_research_expenses,
    )

    if (inputs.prepared_ttm is None and (len(financials) > 1 or inputs.quarters_since_10k)
            and report.cashflow.adjusted_invested_capital is None):
        report.warnings.append("Current return metrics unavailable: aligned opening capital required")

    if inputs.adjustment_inputs.has_r_and_d and any(
        value is None for value in report.cashflow.historical_margin_by_year
    ):
        report.warnings.append("Historical R&D-adjusted diagnostics incomplete: consecutive annual R&D inputs required")

    if report.cashflow.fcff is None:
        report.warnings.append("Historical FCFF unavailable: incomplete reinvestment inputs")
    if report.cashflow.fcfe is None:
        report.warnings.append("Historical FCFE unavailable: incomplete equity cashflow inputs")
    if inputs.adjustment_inputs.has_operating_leases:
        report.warnings.append('Historical lease investment/principal financing bridge unavailable; historical FCFF/FCFE and reinvestment rates are not reconstructed')

    if inputs.adjustment_inputs.has_r_and_d and any(v < 0 for v in [
            inputs.adjustment_inputs.r_and_d_expense_current,
            *inputs.adjustment_inputs.r_and_d_expense_past[:inputs.adjustment_inputs.amortization_period_n]]):
        report.warnings.append('Signed historical R&D retained: net reversals require scope review; amounts are not replaced with zero')

    # --- Module 4: DCF ---
    report.dcf = compute_dcf(
        report.cashflow, report.cost_of_capital, report.adjusted,
        raw_current, inputs.valuation_assumptions, inputs.macro_inputs,
        adjustment_inputs=inputs.adjustment_inputs,
        preferred_value=(inputs.methodology_choices.preferred_stock.shares *
                         inputs.methodology_choices.preferred_stock.price_per_share
                         if inputs.methodology_choices.has_preferred else 0.0),
    )

    if any(report.dcf.wacc_tax_shield_adjustments) or report.dcf.terminal_tax_shield_adjustment:
        financing = "explicit normal WACC/debt-component path" if inputs.valuation_assumptions.debt_tax_shield else "constant initial debt cost/weight"
        report.warnings.append(f"Loss/NOL debt tax shield adjusted using {financing} and proportional taxable profit; partial-year availability is a local proxy, not a marginal interest deduction rate")
    if ('not_decomposed' in report.dcf.tax_shield_basis
            and any(v < 1 for v in report.dcf.debt_tax_shield_availability)):
        report.warnings.append("Aggregate WACC not decomposed: loss/NOL debt tax shield timing not automatically adjusted")

    if inputs.valuation_assumptions.research_tax is not None:
        report.warnings.append("Explicit research tax bridge: forecast expenses/deductions and unlimited NOL carryforward are conditions, not company tax facts; temporary tax benefits do not change total reinvestment")
    elif inputs.adjustment_inputs.has_r_and_d:
        report.warnings.append("Future research tax bridge unspecified: economic EBIT used as forecast tax proxy; research tax differences not separately reconstructed")
    if any(report.dcf.nol_projections) or inputs.valuation_assumptions.override_nol:
        report.warnings.append("NOL cash-tax/terminal tail assumes unlimited carryforward without expiry or deduction caps; balances and future utilization are conditions, not verified company tax facts")

    if inputs.equity_bridge is not None:
        from .equity_bridge import apply_equity_bridge
        report.equity_bridge = apply_equity_bridge(report.dcf, inputs.equity_bridge)

    if inputs.methodology_choices.has_convertible:
        from .equity_bridge import InvalidConvertibleInput, value_convertible
        policy = inputs.methodology_choices.convertible_debt
        if raw_current.bv_debt is None or policy.book_value is None or policy.book_value > raw_current.bv_debt:
            raise InvalidConvertibleInput('convertible carrying amount requires a known inclusive book debt total')
        risk = report.cost_of_capital
        split = value_convertible(policy, risk.cost_of_debt_pretax if risk.approach_used == 'detailed' else None)
        report.convertible_claim = split
        equity = report.dcf.value_of_equity
        shares = inputs.equity_bridge.shares if inputs.equity_bridge else raw_current.shares_outstanding
        if equity is not None and shares is not None and shares > 0:
            if inputs.equity_bridge is None and (risk.approach_used == 'detailed' or raw_current.mv_debt is not None):
                # M4 already deducted the current market debt, including pure convertible debt.
                adjustment = -split['conversion_rights_value']
                debt_basis = 'current_market_debt_and_separate_rights'
            else:
                adjustment = policy.book_value - split['total_market_value']
                debt_basis = 'replace_convertible_carrying_claim_only'
            report.dcf.value_of_equity = equity+adjustment
            report.dcf.equity_bridge_components['convertible_adjustment'] = adjustment
            report.dcf.value_per_share_pre_options = (equity+adjustment)/shares
            split.update(equity_before_revaluation=equity, equity_adjustment=adjustment,
                equity_after_claims=equity+adjustment, shares=shares, debt_bridge_basis=debt_basis)
            if report.equity_bridge is not None:
                report.equity_bridge['per_share'] = report.dcf.value_per_share_pre_options
        report.warnings.append('Convertible split uses straight-debt PV and dirty-market residual; conversion dilution is already in the rights deduction, no automatic extra shares. Non-detailed WACC remains an explicit financing condition.')

    # --- Module 5: Multiples ---
    report.multiples = compute_multiples(
        report.adjusted, raw_current, report.cashflow,
        report.cost_of_capital, inputs.valuation_assumptions, inputs.macro_inputs
    )

    # --- Module 6: Options & Final Value ---
    report.final = compute_options_and_final_value(
        report.dcf, inputs.option_inputs, inputs.macro_inputs
    )

    # --- Populate company_metrics so the Input Sheet "Company" column has
    #     data to display next to the industry references. Uses the LTM-
    #     rotated base year (not raw FY0) so the "Company" ratios match
    #     what drives the DCF. Effective tax for historical ROIC — Damodaran
    #     convention, matches what the firm actually paid.
    tax_rate_for_roic = (
        inputs.macro_inputs.tax_rate_effective
        if inputs.macro_inputs and inputs.macro_inputs.tax_rate_effective is not None
        else (inputs.macro_inputs.tax_rate_marginal if inputs.macro_inputs else 0.21)
    )
    # Prepend LTM as the "current year" so fin0 == LTM; fin1 onwards are
    # the prior annual periods. This mirrors how every other engine module
    # reads the base year.
    ltm_then_prior: list = []
    if report.ltm_financials is not None:
        ltm_then_prior.append(report.ltm_financials)
    ltm_then_prior.extend(inputs.raw_financials if inputs.quarters_since_10k or inputs.prepared_ttm else inputs.raw_financials[1:])
    computed_cm = compute_company_metrics(
        ltm_then_prior,
        cost_of_capital=report.cost_of_capital,
        adjusted=report.adjusted,
        tax_rate=tax_rate_for_roic,
    )
    computed_cm.revenue_growth, computed_cm.revenue_growth_basis = compute_revenue_growth(inputs, ltm)
    # Share the aligned opening-capital/tax calculation; LTM is not a full fiscal year.
    computed_cm.roic = report.cashflow.roic
    if inputs.quarters_since_10k or inputs.prepared_ttm:
        computed_cm.marginal_sales_to_capital = None
    # All of these fields are DERIVED from raw financials + module outputs,
    # so they MUST refresh on every run. Previously we only filled null
    # fields, which caused a subtle bug after PATCH: the session's
    # company_metrics still held the prior run's cost_of_capital (non-null),
    # so the Input Sheet's "Company WACC" got stuck at the old value while
    # the Cost of Capital page showed the freshly-computed one. Always
    # overwrite.
    existing = inputs.company_metrics
    _DERIVED_CM_FIELDS = (
        "revenue_growth", "revenue_growth_basis", "pretax_operating_margin", "sales_to_capital",
        "marginal_sales_to_capital", "roic", "cost_of_capital",
    )
    if existing is None:
        inputs.company_metrics = computed_cm
    else:
        for field_name in _DERIVED_CM_FIELDS:
            setattr(existing, field_name, getattr(computed_cm, field_name))
        # std_dev_stock is the only genuinely user-supplied field — if the
        # computed_cm doesn't know a value (we don't have a volatility
        # calculator yet) keep whatever the user passed.
        if computed_cm.std_dev_stock is not None:
            existing.std_dev_stock = computed_cm.std_dev_stock

    return report


# Module dependency graph for incremental recomputation
_MODULE_ORDER = ["M1", "M2", "M3", "M4", "M5", "M6"]
_DOWNSTREAM = {
    "M1": ["M2", "M3", "M4", "M5", "M6"],
    "M2": ["M3", "M4", "M5", "M6"],
    "M3": ["M4", "M5", "M6"],
    "M4": ["M6"],
    "M5": [],
    "M6": [],
}


def get_modules_to_rerun(edited_module: str) -> list[str]:
    """Given an edit in a module, return which modules need re-running."""
    if edited_module not in _DOWNSTREAM:
        return _MODULE_ORDER  # Full re-run on unknown
    return [edited_module] + _DOWNSTREAM[edited_module]
