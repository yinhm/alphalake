"""Local sensitivity examples. Industry bands are 80–120% of a
reference point, not observed quartiles or forecast confidence intervals."""

from __future__ import annotations

from dataclasses import dataclass

from .data_dictionary import (
    CompanyValuationInput,
    IndustryData,
    MacroInputs,
)


@dataclass
class DriverRange:
    """A single driver's sweep definition."""
    driver: str           # short key for patch path
    label: str            # display label
    patch_path: str       # dot-path used by PATCH /valuation/{sid}
    range_lo: float       # sweep low
    range_hi: float       # sweep high
    range_source: str     # 'industry_median_band' | 'application_example'


def _median_band_from_industry(industry: IndustryData, field_name: str) -> tuple[float, float] | None:
    """Return a local 80–120% band around the published reference point."""
    median = getattr(industry, field_name, None)
    if median is None or median == 0:
        return None
    # ±20% around the median. Works for positive-valued drivers
    # (growth, margin, sales/capital, WACC).
    return tuple(sorted((median * 0.80, median * 1.20)))


def build_ranges(
    inputs: CompanyValuationInput,
) -> list[DriverRange]:
    """Return the local sensitivity-tornado example ranges."""
    industry = inputs.industry_data
    macro: MacroInputs = inputs.macro_inputs
    ranges: list[DriverRange] = []

    # 1) Revenue growth, year 1
    rg_q = _median_band_from_industry(industry, "revenue_growth")
    if rg_q:
        ranges.append(DriverRange(
            driver="revenue_growth_next_year",
            label="Revenue growth (Y1)",
            patch_path="valuation_assumptions.revenue_growth_next_year",
            range_lo=rg_q[0], range_hi=rg_q[1],
            range_source="industry_median_band",
        ))
    else:
        ranges.append(DriverRange(
            driver="revenue_growth_next_year",
            label="Revenue growth (Y1)",
            patch_path="valuation_assumptions.revenue_growth_next_year",
            range_lo=0.03, range_hi=0.25,
            range_source="application_example",
        ))

    # 2) Revenue growth, years 2-5 CAGR — same industry anchor, slightly tighter bounds
    if rg_q:
        ranges.append(DriverRange(
            driver="revenue_growth_years_2_5",
            label="Revenue growth (Y2-5)",
            patch_path="valuation_assumptions.revenue_growth_years_2_5",
            range_lo=rg_q[0] * 0.90, range_hi=rg_q[1] * 0.90,
            range_source="industry_median_band",
        ))
    else:
        ranges.append(DriverRange(
            driver="revenue_growth_years_2_5",
            label="Revenue growth (Y2-5)",
            patch_path="valuation_assumptions.revenue_growth_years_2_5",
            range_lo=0.02, range_hi=0.20,
            range_source="application_example",
        ))

    # 3) Terminal growth — Damodaran's hard ceiling is RF. Range is [0, RF].
    rf = macro.risk_free_rate or 0.04
    ranges.append(DriverRange(
        driver="growth_perpetuity_rate",
        label="Terminal growth",
        patch_path="valuation_assumptions.growth_perpetuity_rate",
        range_lo=max(0.0, rf - 0.01),
        range_hi=rf,
        range_source="application_example",
    ))

    # 4) Target operating margin — from industry
    m_q = _median_band_from_industry(industry, "pretax_operating_margin")
    if m_q:
        ranges.append(DriverRange(
            driver="target_operating_margin",
            label="Target margin",
            patch_path="valuation_assumptions.target_operating_margin",
            range_lo=m_q[0], range_hi=m_q[1],
            range_source="industry_median_band",
        ))
    else:
        ranges.append(DriverRange(
            driver="target_operating_margin",
            label="Target margin",
            patch_path="valuation_assumptions.target_operating_margin",
            range_lo=0.08, range_hi=0.35,
            range_source="application_example",
        ))

    # 5) Margin convergence year
    ranges.append(DriverRange(
        driver="margin_convergence_year",
        label="Convergence year",
        patch_path="valuation_assumptions.margin_convergence_year",
        range_lo=2, range_hi=8,
        range_source="application_example",
    ))

    # 6) Sales-to-capital — from industry
    sc_q = _median_band_from_industry(industry, "sales_to_capital")
    if sc_q:
        ranges.append(DriverRange(
            driver="sales_to_capital_high",
            label="Sales/capital",
            patch_path="valuation_assumptions.sales_to_capital_high",
            range_lo=sc_q[0], range_hi=sc_q[1],
            range_source="industry_median_band",
        ))
    else:
        ranges.append(DriverRange(
            driver="sales_to_capital_high",
            label="Sales/capital",
            patch_path="valuation_assumptions.sales_to_capital_high",
            range_lo=1.0, range_hi=4.0,
            range_source="application_example",
        ))

    # 7) WACC level shift (bps)
    ranges.append(DriverRange(
        driver="wacc_level_shift_bps",
        label="WACC level shift",
        patch_path="methodology_choices.wacc_level_shift_bps",
        range_lo=-150.0, range_hi=150.0,
        range_source="application_example",
    ))

    # 8) Failure probability
    ranges.append(DriverRange(
        driver="failure_probability",
        label="Failure probability",
        patch_path="valuation_assumptions.failure_probability",
        range_lo=0.0, range_hi=0.30,
        range_source="application_example",
    ))

    return ranges
