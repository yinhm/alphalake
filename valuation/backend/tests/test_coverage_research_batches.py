"""研究批次机械装配与累计交付；不调用真实库、网络或DCF计算。"""
from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest

from tools.coverage_research_delivery import merge_rows, verify_model, price_context
from tools.check_native_sqlite import value_digest
from tools.coverage_research_notes import authored_story


def test_authored_values_and_new_terminal_growth_are_consumed_without_selection():
    note = dict(annual_assumptions=[
        dict(year=1, growth=.1, adjusted_operating_margin=.12, tax_rate=.2, research_expense_ratio=.05, sales_to_capital=2.),
        dict(year=2, growth=.2, adjusted_operating_margin=.13, tax_rate=.25, research_expense_ratio=.06, sales_to_capital=1.5)],
        capital_path_mode='annual_sales_to_capital', terminal=dict(growth=.01, marginal_roic=None),
        tax_and_research=dict(tax_path_is_analyst_policy=True))
    original = deepcopy(note)
    baseline = dict(ltm_financials=dict(revenues=1000.), inputs=dict(adjustment_inputs=dict(has_r_and_d=True)),
        dcf=dict(terminal_growth=.03))
    years, overrides = authored_story(note, baseline, 'synthetic')
    assert [year['revenue_million_cny'] for year in years] == [1100., 1320.]
    assert overrides['valuation_assumptions.annual_sales_to_capital'] == [2., 1.5]
    tax = overrides['valuation_assumptions.research_tax']
    assert tax['expenses'] == tax['deductions'] == [55., 79.2]
    assert tax['terminal_expense'] == tax['terminal_deduction'] == 79.2*1.01
    assert tax['tax_rate_basis'] == 'taxable_profit_rate'
    assert overrides['valuation_assumptions.roic_stable_override'] is None
    assert note == original
    note['capital_path_mode'] = 'annual_net_reinvestment'
    for year, budget in zip(note['annual_assumptions'], [90., 120.], strict=True):
        year['annual_net_reinvestment_million_cny'] = budget
    _, overrides = authored_story(note, baseline, 'synthetic')
    assert overrides['valuation_assumptions.annual_sales_to_capital'] is None
    assert overrides['valuation_assumptions.annual_net_reinvestment'] == [90., 120.]
    note['capital_path_mode'] = 'guess'
    with pytest.raises(ValueError, match='不得自行选择'):
        authored_story(note, baseline, 'synthetic')


def test_incremental_delivery_keeps_other_company_inputs_results_and_denominator():
    old = [dict(ticker='SHSE:600519', value=1906.45, run_id='existing-frozen-run'),
           dict(ticker='SZSE:300750', value=539.18, run_id='another-frozen-run'),
           dict(ticker='SHSE:600000', value=None, reason='outside_financial_scope')]
    original = deepcopy(old)
    update = dict(ticker='SZSE:300750', value=540., run_id='explicit-revision')
    scope = {row['ticker']: 'scope' for row in old}
    rows = merge_rows(old, [update], scope)
    assert rows == [old[0], update, old[2]] and old == original
    with pytest.raises(ValueError, match='完整且唯一'):
        merge_rows(old[:-1], [update], scope)
    with pytest.raises(ValueError, match='重复证券'):
        merge_rows(old, [update, update], scope)
    with pytest.raises(ValueError, match='不属于'):
        merge_rows(old, [dict(ticker='SHSE:600001')], scope)


def test_future_research_cannot_disappear_when_baseline_research_is_disabled():
    note = dict(annual_assumptions=[dict(year=1, growth=.1, adjusted_operating_margin=.1,
        tax_rate=.25, research_expense_ratio=.015, annual_net_reinvestment_million_cny=10.)],
        capital_path_mode='annual_net_reinvestment', terminal=dict(growth=.01, marginal_roic=None))
    baseline = dict(ltm_financials=dict(revenues=1000.),
        inputs=dict(adjustment_inputs=dict(has_r_and_d=False)))
    original = deepcopy(note)
    with pytest.raises(ValueError, match='不能忽略非零未来研发条件'):
        authored_story(note, baseline, 'synthetic')
    assert note == original
    note['annual_assumptions'][0]['research_expense_ratio'] = 0.
    years, overrides = authored_story(note, baseline, 'synthetic')
    assert years[0]['revenue_million_cny'] == 1100.
    assert overrides['valuation_assumptions.annual_net_reinvestment'] == [10.]
    assert 'valuation_assumptions.research_tax' not in overrides


def test_delivery_rejects_result_or_request_drift_before_publishing():
    request = dict(inputs=dict(ticker='SHSE:600519', condition='synthetic'))
    expected = value_digest(request)
    result = dict(ticker='SHSE:600519', story_id='synthetic', value_per_share=12.)
    run = dict(run_id='same-run', snapshot=dict(sqlite_sha256='same-snapshot'))
    protocol = dict(frozen_sha256='same-freeze')
    model = dict(contract='coverage-company-research-result-v1', ticker=result['ticker'], story_id=result['story_id'],
        run_id=run['run_id'], snapshot=run['snapshot'], frozen_sha256=protocol['frozen_sha256'],
        request_sha256=expected, request=request, response=dict(final=dict(value_per_share=12.)))
    verify_model(model, result, run, protocol, expected)
    with pytest.raises(ValueError, match='冻结运行不一致'):
        verify_model(model, result | {'value_per_share': 13.}, run, protocol, expected)
    model['request']['inputs']['condition'] = 'post-calculation-edit'
    with pytest.raises(ValueError, match='冻结运行不一致'):
        verify_model(model, result, run, protocol, expected)


def test_retry_reuses_only_identical_registered_company_report(tmp_path, monkeypatch):
    from tools import coverage_research_delivery as delivery
    monkeypatch.setenv('ALPHALAKE_WORKSPACE', str(tmp_path))
    folder = tmp_path/'research'/'existing'
    folder.mkdir(parents=True)
    (folder/'research.json').write_text('{}')
    note = tmp_path/'note.json'
    note.write_text('{}')
    note.with_suffix('.md').write_text('同一份计算前研究正文。')
    report = SimpleNamespace(tickers=['SHSE:600519'], run_ids=[])
    monkeypatch.setattr(delivery, 'read_report', lambda _: report)
    def verified(report, field):
        selected = note if field == 'authored-note' else note.with_suffix('.md')
        return SimpleNamespace(sha256=delivery.digest(selected)), selected
    monkeypatch.setattr(delivery, 'verified_file', verified)
    result = dict(status='pending')
    actual, model = delivery.company_report('existing', dict(ticker='SHSE:600519'), result,
        None, None, None, None, dict(run_id='same-run'), note)
    assert actual is report and model is None
    report.tickers = ['SZSE:300750']
    with pytest.raises(ValueError, match='身份或运行不一致'):
        delivery.company_report('existing', dict(ticker='SHSE:600519'), result,
            None, None, None, None, dict(run_id='same-run'), note)


def test_known_post_period_capital_change_cannot_be_hidden_by_approved_proxy():
    native = dict(price_comparison_status='capital_date_proxy', price_comparison_reason='已批准基期代理')
    assert price_context(native, {}) == native
    assert price_context(native, dict(price_comparison=dict(status='capital_date_proxy',
        reason='笔记重述相同基期代理，不替换来源限制'))) == native
    actual = price_context(native, dict(price_comparison=dict(status='unreconciled_post_period_capital_change',
        reason='新股已登记但尚未桥接资金及股数')))
    assert actual['price_comparison_status'] == 'unreconciled_post_period_capital_change'
    with pytest.raises(ValueError, match='不能自动放开'):
        price_context(actual, dict(price_comparison=dict(status='capital_date_proxy', reason='希望继续比较')))


def test_delivery_identifier_is_checked_before_reading_or_writing_files():
    from tools.coverage_research_delivery import deliver
    with pytest.raises(ValueError, match='不能包含目录路径'):
        deliver(None, 'unused', 'unused', '../outside')


def test_calculated_diagnostic_is_visible_but_not_labelled_as_adopted_company_case():
    from tools.coverage_research_delivery import condition_row
    source = dict(admission='not_calculated', close_cny=10., price_comparison_status='capital_date_proxy',
        price_comparison_reason='已批准的资本基期描述比较')
    report = SimpleNamespace(id='diagnostic-report', share_date=date(2026, 6, 30),
        files=[SimpleNamespace(id='valuation', path='research/model.json', sha256='a'*64)])
    forecast = dict(growth=.1, adjusted_operating_margin=.12, capital_funding=dict(sales_to_capital=None))
    model = dict(run_id='diagnostic-run', response=dict(final=dict(value_per_share=12.), cost_of_capital=dict(wacc=.08)),
        request=dict(inputs=dict(adjustment_inputs=dict(has_r_and_d=False))),
        review=dict(forecast=[forecast], terminal=dict(growth=.01, wacc=.08, value_share=.6),
            economic_checks=dict(capital_release=dict(gross_release_million_cny=0., gross_nonresearch_release_million_cny=None))))
    row = condition_row(source, dict(value_per_share=12.), model, report, 'frozen-time', 'diagnostic_condition')
    assert row['condition_value_cny_per_share'] == 12. and row['admission'] == 'ready'
    assert row['mode'] == 'authored_company_diagnostic_condition' and '未采纳' in row['reason']
    assert row['run_id'] == 'diagnostic-run'
