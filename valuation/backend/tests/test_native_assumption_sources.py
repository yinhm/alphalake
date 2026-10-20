"""实际消费分支及输入来源各自留痕，不从数值存在推断用户预测。"""
from copy import deepcopy

from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation
from tests.test_native_assumption_selection import sample
from tools.check_native_sqlite import economic_payload, value_digest
from tools.review_native_policy import assumption_sources, review_report, summarize_methodology


def report(**assumptions):
    inputs = CompanyValuationInput.model_validate(sample()['inputs'])
    inputs.valuation_assumptions = inputs.valuation_assumptions.model_copy(update=assumptions)
    result = run_full_valuation(inputs)
    return dict(inputs=inputs.model_dump(mode='json'), **{name:getattr(result, name).model_dump(mode='json')
        for name in ('ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final')})


def test_native_initial_values_are_not_user_forecasts_and_review_is_read_only():
    body = report(revenue_growth_next_year=.1)
    body['source_metadata'] = {'valuation_assumptions':'native-source-initialization-v1; editable conditions',
        'valuation_assumptions.revenue_growth_next_year':'Comparable historical growth, not a forecast'}
    saved = deepcopy(body)
    signature = value_digest(economic_payload(body))
    reviewed = review_report(body)
    sources = reviewed['methodology']['assumption_sources']
    growth = sources['parameters']['revenue_growth_next_year']
    assert growth['resolution'] == 'explicit_input'
    assert growth['drivers'][0]['origin'] == 'source_initialized_condition'
    assert growth['drivers'][0]['recorded_basis'].startswith('Comparable historical growth')
    assert sources['parameters']['margin_convergence_year']['drivers'][0]['origin'] == 'inherited_native_condition'
    assert sources['economic_approval'] == 'not_inferred_from_input_origin'
    assert body == saved and value_digest(economic_payload(body)) == signature


def test_zero_override_and_clear_are_not_deduced_from_value_equality():
    path = 'valuation_assumptions.revenue_growth_next_year'
    context = dict(prepared_from_sqlite=True, overrides={path:0})
    zero = assumption_sources(report(revenue_growth_next_year=0), context)['parameters']['revenue_growth_next_year']
    assert zero['value'] == 0 and zero['resolution'] == 'explicit_input'
    assert zero['drivers'][0]['origin'] == 'user_override'
    # 清空也须记录用户动作；实际增长取应用回退，不冒称用户显式给了0%。
    context['overrides'][path] = None
    cleared = assumption_sources(report(), context)['parameters']['revenue_growth_next_year']
    assert cleared['value'] == 0 and cleared['resolution'] == 'application_default'
    assert cleared['drivers'][0]['input_state'] == 'unset'
    assert cleared['drivers'][0]['origin'] == 'user_override'
    assert cleared['drivers'][0]['override_actions'] == {path:'clear'}
    # 融资清空可恢复来源联动，用户动作不等于用户明确填写了恢复后的数值。
    path = 'valuation_assumptions.cost_of_capital_stable_override'
    restored = assumption_sources(report(cost_of_capital_stable_override=.08), dict(
        prepared_from_sqlite=True, overrides={path:None}, source_metadata={path:'Native CNY source-linked WACC'}))
    driver = restored['parameters']['cost_of_capital_stable_override']['drivers'][0]
    assert driver['input_state'] == 'provided' and driver['override_actions'] == {path:'clear'}
    assert driver['recorded_basis'] == 'Native CNY source-linked WACC'


def test_annual_paths_shadow_scalars_and_direct_budget_shadows_ratios():
    body = report()
    payload = body['inputs']
    payload['valuation_assumptions']['annual_forecast'] = [dict(growth=.08, margin=.12, tax=.25)]*10
    # 走typed校验后计算，直接预算与倍率互斥，不能因来源解释恢复被覆盖标量。
    payload['valuation_assumptions']['annual_net_reinvestment'] = [12]*10
    inputs = CompanyValuationInput.model_validate(payload)
    result = run_full_valuation(inputs)
    body = dict(inputs=inputs.model_dump(mode='json'), dcf=result.dcf.model_dump(mode='json'))
    sources = assumption_sources(body, dict(prepared_from_sqlite=True,
        generated_paths=['valuation_assumptions.annual_forecast.0.growth'], condition='example'))
    growth = sources['parameters']['revenue_growth_next_year']
    assert growth['resolution'] == 'annual_forecast'
    assert growth['drivers'][0]['path'] == 'valuation_assumptions.annual_forecast'
    assert growth['drivers'][0]['origin'] == 'partly_generated_named_condition'
    assert growth['drivers'][0]['generated_paths'] == ['valuation_assumptions.annual_forecast.0.growth']
    capital = sources['parameters']['sales_to_capital_high']
    assert capital['resolution'] == 'annual_net_reinvestment'
    assert capital['drivers'][0]['path'] == 'valuation_assumptions.annual_net_reinvestment'
    assert capital['drivers'][0]['origin'] == 'inherited_native_condition'


def test_generated_field_does_not_relabel_inherited_reference_or_sibling():
    body = report(revenue_growth_next_year=.1, target_operating_margin=.12, stable_growth_rate=.02)
    context = dict(prepared_from_sqlite=True,
        source_metadata={'macro_inputs.risk_free_rate':'Published CNY risk-free reference'},
        overrides={'valuation_assumptions.target_operating_margin':.12},
        generated_paths=['valuation_assumptions.revenue_growth_next_year'], condition='example')
    sources = assumption_sources(body, context)['parameters']
    assert sources['revenue_growth_next_year']['drivers'][0]['origin'] == 'generated_named_condition'
    assert sources['target_operating_margin']['drivers'][0]['origin'] == 'user_override'
    terminal = sources['growth_perpetuity_rate']
    assert terminal['resolution'] == 'explicit_stable_capped'
    assert [r['origin'] for r in terminal['drivers']] == ['inherited_native_condition', 'source_initialized_condition']
    inherited = sources['revenue_growth_years_2_5']
    assert inherited['resolution'] == 'inherited'
    assert inherited['drivers'][0]['path'] == 'valuation_assumptions.revenue_growth_next_year'
    assert inherited['drivers'][0]['origin'] == 'generated_named_condition'


def test_summary_counts_companies_and_keeps_rejected_denominator():
    sources = assumption_sources(report(stable_growth_rate=.02), dict(prepared_from_sqlite=True,
        source_metadata={'macro_inputs.risk_free_rate':'Published reference'}))
    result = summarize_methodology([dict(version='native-methodology-v1', findings=['example'], assumption_sources=sources),
        dict(status='not_calculated', reason='missing_input')])
    assert result['companies'] == 2 and result['reviewed'] == 1 and result['not_calculated'] == 1
    assert result['findings'] == {'example':1}
    origins = result['assumption_origin_company_counts']['growth_perpetuity_rate']
    assert origins == {'inherited_native_condition':1, 'source_initialized_condition':1}
    assert result['parameter_resolution_company_counts']['growth_perpetuity_rate'] == {'explicit_stable_capped':1}
