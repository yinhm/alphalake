"""通用利润—税—研发—总投入桥，不靠重复引擎计算证明会计恒等式。"""
from copy import deepcopy

import pytest

from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation
from tests.test_native_assumption_selection import sample
from tools.evaluate_native_policy import research_tax_effect
from tools.review_native_policy import review_report


def inputs(*, loss=False, budget=True):
    payload = sample()['inputs']
    payload['raw_financials'][0]['r_and_d_expense'] = 100
    payload['adjustment_inputs'].update(has_r_and_d=True, r_and_d_expense_current=100, r_and_d_expense_past=[40]*5)
    payload['valuation_assumptions'].update(projection_years=2, high_growth_years=1,
        annual_forecast=[dict(growth=0, margin=m, tax=.25) for m in ([-.01,.15] if loss else [.12,.12])],
        annual_net_reinvestment=[48,46] if budget else None, stable_growth_rate=.02,
        cost_of_capital_stable_override=.08, roic_stable_override=.08,
        override_nol=loss, nol_amount=20 if loss else 0,
        research_tax=dict(policy_id='explicit-test-budget', expenses=[100,110], deductions=[100,110],
            terminal_expense=110, terminal_deduction=110, tax_rate_basis='taxable_profit_rate'))
    return CompanyValuationInput.model_validate(payload)


def saved(value):
    result = run_full_valuation(value)
    return dict(inputs=value.model_dump(mode='json'), **{name:getattr(result,name).model_dump(mode='json')
        for name in ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')})


def test_expense_basis_and_total_budget_do_not_deduct_research_twice():
    body = saved(inputs())
    original = deepcopy(body)
    review = review_report(body)
    first, second = review['forecast']
    assert first['economic_ebit_million_cny'] == 120
    assert first['expense_basis_ebit_million_cny'] == first['tax_base_million_cny'] == 72
    assert first['research_net_investment_million_cny'] == first['reinvestment_million_cny'] == 48
    assert first['implied_nonresearch_net_reinvestment_million_cny'] == 0
    assert first['cash_tax_million_cny'] == 18 and first['nopat_million_cny'] == 102
    assert first['fcff_million_cny'] == 54  # 72−18，无第二次扣48。
    assert second['cash_tax_million_cny'] == 18.5 and second['fcff_million_cny'] == 55.5
    assert review['economic_checks']['cashflow_bridge']['total_cash_tax_million_cny'] == 36.5
    assert body == original


def test_loss_carryforward_uses_tax_base_instead_of_economic_profit():
    review = review_report(saved(inputs(loss=True)))
    first, second = review['forecast']
    assert first['economic_ebit_million_cny'] == -10 and first['tax_base_million_cny'] == -58
    assert first['opening_nol_million_cny'] == 20 and first['nol_added_million_cny'] == 58
    assert first['closing_nol_million_cny'] == second['opening_nol_million_cny'] == 78
    assert second['tax_base_million_cny'] == 104 and second['nol_used_million_cny'] == 78
    assert second['closing_nol_million_cny'] == 0
    assert second['cash_tax_million_cny'] == 6.5 and second['fcff_million_cny'] == 97.5


def test_ratio_budget_keeps_implied_nonresearch_release_and_proxy_keeps_unknowns():
    value = inputs(budget=False)
    review = review_report(saved(value))
    assert review['forecast'][0]['reinvestment_million_cny'] == 0
    assert review['forecast'][0]['implied_nonresearch_net_reinvestment_million_cny'] == -48
    assert 'research_condition_requires_nonresearch_capital_release_basis' in review['methodology']['findings']
    value.valuation_assumptions.research_tax = None
    proxy = review_report(saved(value))
    assert proxy['economic_checks']['cashflow_bridge']['research_basis'] == 'economic_ebit_tax_proxy'
    assert proxy['economic_checks']['cashflow_bridge']['nonresearch_investment_basis'] == 'not_inferred'
    assert 'expense_basis_ebit_million_cny' not in proxy['forecast'][0]


@pytest.mark.parametrize('field', ['cash_tax_projections','nopat_projections','tax_base_projections','nol_projections','fcff_projections'])
def test_same_engine_replay_cannot_hide_broken_cashflow_identity(monkeypatch, field):
    value = inputs()
    result = run_full_valuation(value)
    getattr(result.dcf,field)[0] += .01
    body = dict(inputs=value.model_dump(mode='json'), **{name:getattr(result,name).model_dump(mode='json')
        for name in ('ltm_financials','adjusted','cost_of_capital','cashflow','dcf','final')})
    # 即便保存响应与同一错误引擎完全相同，基本恒等式仍须拒绝。
    monkeypatch.setattr('tools.review_native_policy.run_full_valuation', lambda _:result)
    with pytest.raises(ValueError, match='cashflow bridge does not reconcile'):
        review_report(body)


def test_research_tax_effect_includes_discount_and_terminal_changes():
    value = inputs(loss=True)
    candidate = saved(value)
    value.valuation_assumptions.research_tax = None
    baseline = saved(value)
    effect = research_tax_effect(baseline,candidate)
    assert effect['annual'][1]['cash_tax_change_million_cny'] == -23.5
    assert effect['annual'][1]['fcff_change_million_cny'] == 23.5
    assert effect['discount_factors_changed'] and effect['discount_pv_effect_million_cny'] != 0
    assert sum(effect[k] for k in ('cashflow_pv_effect_million_cny','discount_pv_effect_million_cny','terminal_pv_effect_million_cny')) == pytest.approx(
        candidate['dcf']['value_as_going_concern']-baseline['dcf']['value_as_going_concern'])
    unrelated = deepcopy(candidate); unrelated['inputs']['valuation_assumptions']['stable_growth_rate'] = .03
    with pytest.raises(ValueError,match='unrelated inputs'):
        research_tax_effect(baseline,unrelated)
    tampered = deepcopy(candidate); tampered['dcf']['fcff_projections'][0] += .01
    with pytest.raises(ValueError,match='FCFF change'):
        research_tax_effect(baseline,tampered)
