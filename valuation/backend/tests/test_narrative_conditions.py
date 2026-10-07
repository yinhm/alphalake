"""显式锚点展开须保持事实、研发口径和拒绝边界。"""
from copy import deepcopy

import pytest

from engine.data_dictionary import CompanyValuationInput, ForecastYear, ResearchTaxForecast
from engine.orchestrator import run_full_valuation
from tests.test_native_assumption_selection import sample
from tools.narrative_conditions import Recipe, operating_condition


def recipe(**updates):
    return Recipe.model_validate(dict(id='continuation', title='延续条件', narrative='历史是条件锚点。',
        growth_anchor='three_year_cagr', hold_years=1, stable_year=5,
        margin_anchor='three_year_weighted', margin_year=3, **updates))


def test_explicit_history_path_and_research_bridge_preserve_source():
    baseline = sample()
    inputs = CompanyValuationInput.model_validate(baseline['inputs'])
    inputs.adjustment_inputs.has_r_and_d = True
    inputs.adjustment_inputs.r_and_d_expense_current = 30.
    inputs.adjustment_inputs.r_and_d_expense_past = [25., 20., 15., 10., 5.]
    for i, row in enumerate(inputs.raw_financials):
        row.r_and_d_expense = 30.-5*i
    report = run_full_valuation(inputs)
    baseline = dict(inputs=inputs.model_dump(mode='json'), **{k:getattr(report, k).model_dump(mode='json')
        for k in ('ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final')})
    original = deepcopy(baseline)
    plan, overrides, evidence = operating_condition(baseline, recipe())
    assert baseline == original and evidence['historical_anchor_years'] == [2022, 2023, 2024, 2025]
    assert plan[0]['revenue_million_cny'] == pytest.approx(1100.)
    assert plan[4]['revenue_million_cny']/plan[3]['revenue_million_cny']-1 == pytest.approx(report.dcf.terminal_growth)
    research = overrides['valuation_assumptions.research_tax']
    assert research['expenses'] == pytest.approx([r['revenue_million_cny']*.03 for r in plan])
    assert research['deductions'] == research['expenses']
    inputs.valuation_assumptions.research_tax = ResearchTaxForecast.model_validate(research)
    previous = report.ltm_financials.revenues
    inputs.valuation_assumptions.annual_forecast = [ForecastYear(growth=(r['revenue_million_cny']/
        (plan[i-1]['revenue_million_cny'] if i else previous)-1), margin=r['adjusted_operating_margin'], tax=r['tax_rate'])
        for i, r in enumerate(plan)]
    replay = run_full_valuation(CompanyValuationInput.model_validate(inputs.model_dump()))
    assert replay.dcf.tax_base_projections == pytest.approx([r['revenue_million_cny']*r['adjusted_operating_margin']-
        (expense-amort) for r, expense, amort in zip(plan, research['expenses'], replay.dcf.research_amortization_projections)])


def test_missing_history_extreme_growth_and_recovery_are_not_fabricated():
    baseline = sample()
    baseline['inputs']['raw_financials'] = [r for r in baseline['inputs']['raw_financials'] if r['fiscal_year'] != 2023]
    with pytest.raises(ValueError, match='complete positive annual'):
        operating_condition(baseline, recipe())
    baseline = sample()
    baseline['inputs']['raw_financials'][0]['revenues'] = 100000.
    with pytest.raises(ValueError, match='not clipped'):
        operating_condition(baseline, recipe())
    baseline = sample()
    for row in baseline['inputs']['raw_financials']:
        row['ebit'] = -10.
    with pytest.raises(ValueError, match='explicit recovery'):
        operating_condition(baseline, recipe())


def test_cycle_conditions_reuse_existing_variants():
    from tools.cyclical_valuation import cycle_conditions
    baseline = sample()
    baseline['inputs']['valuation_assumptions']['sales_to_capital_high'] = 2.
    selected = recipe().model_dump()
    selected.update(cycle_variant='arithmetic-3y', growth_anchor='existing_cycle', margin_anchor='existing_cycle')
    # Construct with both declared cycle anchors; no hidden inference from a company label.
    chosen = Recipe.model_validate(selected)
    expected, _ = cycle_conditions(baseline)
    plan, overrides, _ = operating_condition(baseline, chosen)
    assert overrides['valuation_assumptions.annual_sales_to_capital'] == expected['arithmetic-3y']['request']['inputs']['valuation_assumptions']['annual_sales_to_capital']
    assert [r['adjusted_operating_margin'] for r in plan] == [r['margin'] for r in expected['arithmetic-3y']['request']['inputs']['valuation_assumptions']['annual_forecast']]
