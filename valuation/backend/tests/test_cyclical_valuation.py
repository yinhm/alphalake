"""周期条件保持事实和亏损，研发／税／投入衔接沿共享引擎。"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from engine.data_dictionary import CompanyValuationInput
from engine.orchestrator import run_full_valuation
from tools.cyclical_valuation import PROFILES, INDUSTRY_PROFILE, cycle_conditions, economic_review, industry_scope, load_scope, profit_components
from tests.test_native_assumption_selection import sample


def baseline(research=False, lag=1):
    inputs = CompanyValuationInput.model_validate(sample()['inputs'])
    inputs.industry_data.industry_name = 'Semiconductor'
    a = inputs.valuation_assumptions
    a.sales_to_capital_high = 2
    a.cost_of_capital_stable_override = .08
    a.roic_stable_override = .08
    a.stable_growth_rate = .02
    a.override_reinvestment_lag = True
    a.reinvestment_lag_years = lag
    inputs.adjustment_inputs.has_r_and_d = research
    if research:
        inputs.adjustment_inputs.amortization_period_n = 5
        inputs.adjustment_inputs.r_and_d_expense_current = 60
        inputs.adjustment_inputs.r_and_d_expense_past = [40, 30, 20, 10, 5]
        for i, r in enumerate(inputs.raw_financials):
            r.r_and_d_expense = [60, 40, 30, 20, 10, 5][i]
    inputs.raw_financials[-2].ebit = -10
    return report_body(inputs)


def report_body(inputs):
    report = run_full_valuation(inputs)
    return dict(inputs=inputs.model_dump(mode='json'), **{field:getattr(report, field).model_dump(mode='json')
        for field in ('ltm_financials', 'adjusted', 'cost_of_capital', 'cashflow', 'dcf', 'final')},
        convertible_claim=report.convertible_claim, equity_bridge=report.equity_bridge)


@pytest.mark.parametrize('research,lag', [(False, 0), (True, 1), (True, 2), (True, 3)])
def test_cycle_conditions_keep_facts_losses_and_capital_lag(research, lag):
    original = baseline(research, lag); saved = deepcopy(original)
    variants, evidence = cycle_conditions(original)
    assert original == saved
    assert len(variants) == 4 and evidence['observation'][0]['reported_ebit'] == -10
    assert not evidence['full_cycle_verified'] and not evidence['automatic_adoption']
    for name, variant in variants.items():
        assert variant['status'] == 'prepared_explicit_condition'
        inputs = CompanyValuationInput.model_validate(variant['request']['inputs'])
        assert inputs.raw_financials == CompanyValuationInput.model_validate(original['inputs']).raw_financials
        assert inputs.macro_inputs.model_dump() == original['inputs']['macro_inputs']
        body = report_body(inputs)
        review = economic_review(body)
        assert review['terminal_reinvestment_continuous']
        assert body['dcf']['revenue_projections'][:3] == [original['ltm_financials']['revenues']]*3
        if research:
            assert review['nonresearch_capital_release_years']
            terminal = review['terminal_research_capital']
            expected_amort = sum(inputs.valuation_assumptions.research_tax.terminal_expense/(1.02)**i for i in range(1,6))/5
            assert terminal['amortization_million_cny'] == pytest.approx(expected_amort)
            assert terminal['nonresearch_net_investment_million_cny']+terminal['net_investment_million_cny'] == pytest.approx(body['dcf']['terminal_reinvestment'])
            for ebit, expense, amort, tax_base in zip(body['dcf']['ebit_projections'],
                    inputs.valuation_assumptions.research_tax.expenses,
                    body['dcf']['research_amortization_projections'], body['dcf']['tax_base_projections']):
                assert tax_base == pytest.approx(ebit+amort-expense)
        changed = deepcopy(body); changed['dcf']['fcff_projections'][0] += 1
        with pytest.raises(ValueError, match='saved report does not replay'):
            economic_review(changed)


def test_missing_or_zero_revenue_window_is_not_repaired():
    for zero in (False, True):
        body = baseline(); rows = body['inputs']['raw_financials']
        if zero:
            rows[2]['revenues'] = 0
        else:
            rows.pop(2)
        saved = deepcopy(body)
        with pytest.raises(ValueError, match='observation_margin_unavailable_years'):
            cycle_conditions(body)
        assert body == saved


def test_existing_explicit_forecast_is_preserved():
    body = baseline(); body['inputs']['valuation_assumptions']['annual_sales_to_capital'] = [2]*10
    saved = deepcopy(body)
    with pytest.raises(ValueError, match='explicit_future_path_preserved'):
        cycle_conditions(body)
    assert body == saved


def test_permanent_loss_requires_recovery_instead_of_positive_floor():
    body = baseline()
    for row in body['inputs']['raw_financials']:
        row['ebit'] = -row['revenues']*.1
    body = report_body(CompanyValuationInput.model_validate(body['inputs']))
    variants, evidence = cycle_conditions(body)
    assert len(evidence['observation']) == 5
    assert {r['status'] for r in variants.values()} == {'requires_terminal_recovery_condition'}


def test_industry_screen_is_disjoint_and_not_cycle_certification():
    catalog = set((Path(__file__).resolve().parents[3]/'internal/source/damodaran/industries-global-2026.txt').read_text().splitlines())
    industries = [i for _, _, names in PROFILES.values() for i in names]
    assert len(industries) == len(set(industries)) and set(industries) <= catalog
    assert industry_scope('Steel')['profile'] == 'materials'
    assert not industry_scope('Steel')['company_cycle_verified']
    assert industry_scope('Power')['status'] == 'mixed_cycle_exposure_candidate'
    assert industry_scope('Brokerage & Investment Banking')['status'] == 'outside_financial_scope'
    assert industry_scope(None)['status'] == 'unresolved_industry'
    assert industry_scope('Drugs (Biotechnology)')['status'] == 'not_screened_by_industry_policy'
    assert set(INDUSTRY_PROFILE) == set(industries)


def test_profit_scope_queries_data_side_and_keeps_missing_or_invalid(monkeypatch):
    values = [('2025-12-31','FY','10000000'),('2026-06-30','H1','5000000'),('2025-06-30','H1','2000000')]
    facts = [dict(ticker='SHSE:600019',period=p,field='investment_income',value=v,unit='CNY',period_type=k,
        statement_scope='provider_default',fact_id=i,source_record_id=i) for i,(p,k,v) in enumerate(values)]
    def query(connection,fields,periods):
        assert fields==('investment_income','fair_value_change_income')
        assert set(periods)=={p for p,_,_ in values}
        return facts
    monkeypatch.setattr('tools.export_alphalake_sqlite.query_standard_inputs',query)
    result = profit_components(None,'2026-06-30')['SHSE:600019']['components']
    assert result['investment_income']['amount_million_cny']==13
    assert len(result['fair_value_change_income']['missing_periods'])==3
    facts[1]['period_type']='Q2'
    result = profit_components(None,'2026-06-30')['SHSE:600019']['components']['investment_income']
    assert result['amount_million_cny'] is None and result['invalid_periods']==['2026-06-30']


def test_scope_requires_current_hash_cutoff_unique_universe_and_canonical_conflicts(tmp_path):
    metadata = dict(source_database_sha256='source', report_period='2026-06-30', information_as_of='2026-10-05T13:23:28+00:00')
    companies = [dict(ticker='SHSE:600517', scope='unresolved_industry_scope'),
        dict(ticker='SHSE:600019', scope='nonfinancial_by_reference')]
    coverage = dict(contract_version='alphalake-native-coverage-v1', scope_policy='nonfinancial-tdx-damodaran-2026-v1',
        sqlite_sha256='snapshot', **metadata, companies=companies, snapshot_candidates=2,
        scope_counts={'unresolved_industry_scope':1, 'nonfinancial_by_reference':1})
    path = tmp_path/'scope.json'; tickers = [r['ticker'] for r in companies]
    path.write_text(json.dumps(coverage))
    assert load_scope(path, 'snapshot', metadata, tickers)['SHSE:600517'] == 'unresolved_industry_scope'
    assert industry_scope('Electrical Equipment')['profile'] == 'capital_spending'
    for key, value in [('sqlite_sha256','old'), ('source_database_sha256','old'), ('report_period','2025-12-31'),
        ('information_as_of','2026-10-04T13:23:28+00:00'), ('companies',[companies[0]]*2),
        ('companies',companies[:1]), ('scope_counts',{'nonfinancial_by_reference':2})]:
        changed = dict(coverage, **{key:value}); path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            load_scope(path, 'snapshot', metadata, tickers)
