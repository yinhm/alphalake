"""公司诊断：合并账面资本配套合并利润；投资分类未闭合，不自动整桶扣除。"""

from __future__ import annotations

import json
import math
from calendar import monthrange

from .module_1_adjustments import after_tax_operating_income

from engine.data_dictionary import (
    AdjustedFinancials,
    CompanyMetrics,
    CostOfCapital,
    CompanyValuationInput,
    RawFinancials,
)


def compute_revenue_growth(inputs: CompanyValuationInput, current: RawFinancials) -> tuple[float | None, str]:
    """同比只比较相隔一年的同长度窗口，不将TTM/FY之比年化。"""
    if inputs.prepared_ttm is not None:
        prepared = inputs.prepared_ttm
        end = prepared.period_end
        prior_end = end.replace(year=end.year-1, day=min(end.day, monthrange(end.year-1, end.month)[1]))
        basis = f'TTM同比：{end} / {prior_end}'
        evidence = prepared.provenance.get('prior_ttm_revenue')
        if evidence is None:
            return None, basis + '；缺上年同截止月TTM收入，不以最近全年替代。'
        try:
            comparison = json.loads(evidence)
            if (comparison['period_end'] != prior_end.isoformat()
                    or comparison['currency'] != prepared.currency
                    or comparison['money_unit'] != prepared.money_unit):
                raise ValueError('unaligned comparison')
            prior = float(comparison['revenues'])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None, basis + '；比较收入的期间或单位证据无效。'
    elif inputs.quarters_since_10k:
        basis = 'TTM同比：当前十二个月 / 上年同截止月十二个月（FQ-4至FQ-7）'
        quarters = inputs.quarterly_financials or []
        if len(quarters) < 8 or any(q.revenues is None or not math.isfinite(q.revenues) for q in quarters[4:8]):
            return None, basis + '；比较窗口不完整，不以最近全年替代。'
        try:
            prior = math.fsum(q.revenues for q in quarters[4:8])
        except OverflowError:
            return None, basis + '；比较收入溢出。'
    else:
        annual = inputs.raw_financials
        basis = '全年同比：相邻完整财年收入'
        if len(annual) < 2 or annual[0].fiscal_year != annual[1].fiscal_year+1:
            return None, basis + '；缺相邻财年。'
        prior = annual[1].revenues
    if prior is None or not math.isfinite(prior) or prior <= 0 or not math.isfinite(current.revenues):
        return None, basis + '；比较收入缺失、非有限或非正，百分比增长无定义。'
    return current.revenues / prior - 1, basis + f'；收入 {current.revenues:g} / {prior:g} - 1，百万元报表币种；不是未来预测。'


def _unadjusted_ic(f: RawFinancials) -> float | None:
    bve = f.consolidated_book_equity
    bvd = f.bv_debt
    if bve is None or bvd is None or f.cash_and_marketable_securities is None:
        return None
    cash = f.cash_and_marketable_securities
    return bve + bvd - cash


def _adjusted_ic(f: RawFinancials, adjusted: AdjustedFinancials | None) -> float | None:
    """合并资本加研发资产及尚未入表租赁资本，不混用股权桥接市值。"""
    base = _unadjusted_ic(f)
    if base is None:
        return None
    if adjusted is None:
        return base
    research = adjusted.value_of_research_asset or 0.0
    lease_pv = adjusted.pv_of_operating_leases or 0.0
    return base + research + lease_pv


def compute_company_metrics(
    financials: list[RawFinancials],
    cost_of_capital: CostOfCapital | None = None,
    adjusted: AdjustedFinancials | None = None,
    tax_rate: float = 0.21,
) -> CompanyMetrics:
    """Compute the Damodaran-style company metrics.

    When ``adjusted`` is provided, ROIC and Sales-to-Capital use Damodaran's
    adjusted EBIT + adjusted IC. Otherwise they fall back to the simple
    book formulas so the function still works for test data and for
    companies with no R&D / no operating leases.
    """
    if not financials:
        return CompanyMetrics()

    fin0 = financials[0]
    fin1 = financials[1] if len(financials) > 1 else None

    # 1. Revenue growth (most recent)
    revenue_growth = None
    if fin1 and fin0.fiscal_year == fin1.fiscal_year+1 and fin1.revenues is not None and fin1.revenues > 0:
        revenue_growth = fin0.revenues / fin1.revenues - 1

    # 2. Pre-tax operating margin.
    # Use adjusted EBIT when available — matches Damodaran's "post-R&D-
    # capitalization" margin that feeds into target-margin comparisons.
    ebit_for_margin = adjusted.adjusted_ebit if (adjusted and adjusted.adjusted_ebit is not None) else fin0.ebit
    pretax_margin = None
    if fin0.revenues and fin0.revenues != 0:
        pretax_margin = ebit_for_margin / fin0.revenues

    # Consolidated capital + research asset + unrecognized lease capital.
    ic0 = _adjusted_ic(fin0, adjusted)
    ic1 = (_adjusted_ic(fin1, adjusted) if fin1 and fin0.fiscal_year == fin1.fiscal_year+1
           and not (adjusted and adjusted.pv_of_operating_leases) else None)
    if ic1 is not None and adjusted is not None:
        # Remove the current R&D net addition to recover opening research capital.
        # Lease opening capital is unavailable; never substitute current PV.
        ic1 -= adjusted.adjusted_ebit - fin0.ebit - adjusted.lease_adjustment_to_ebit
    # 3. Sales-to-capital ratio
    sales_to_cap = None
    if ic0 is not None and ic0 > 0:
        sales_to_cap = fin0.revenues / ic0

    # 4. Marginal sales-to-capital — ΔRev / ΔIC over the latest year
    marginal_stc = None
    if fin1 and ic0 is not None and ic1 is not None and (ic0 - ic1) != 0:
        marginal_stc = (fin0.revenues - fin1.revenues) / (ic0 - ic1)

    # 5. ROIC uses aligned opening capital; a current balance is not a substitute.
    roic = None
    if ic1 is not None and ic1 > 0:
        nopat = (after_tax_operating_income(adjusted, fin0, tax_rate)
                 if adjusted is not None else fin0.ebit * (1 - tax_rate))
        roic = nopat / ic1

    # 7. Cost of capital (WACC) — passthrough from Module 2
    wacc = cost_of_capital.wacc if cost_of_capital else None

    return CompanyMetrics(
        revenue_growth=revenue_growth,
        pretax_operating_margin=pretax_margin,
        sales_to_capital=sales_to_cap,
        marginal_sales_to_capital=marginal_stc,
        roic=roic,
        std_dev_stock=None,  # Only populated from industry reference data
        cost_of_capital=wacc,
    )
