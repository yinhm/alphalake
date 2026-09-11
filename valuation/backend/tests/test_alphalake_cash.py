"""现金核对契约：真实研究数值配模拟标准血缘，不冒充历史标准链验收。"""
import copy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from data_sources.alphalake import AlphaLakeRequest
from data_sources.alphalake_cash import cash_crosscheck,VALIDATION,SCOPE_AUDIT,UNCERTAINTY
from tools.backtest_tdx_history import value,quarter_periods

ROOT=Path(__file__).resolve().parents[3]


def fixture():
    source=json.loads((ROOT/'valuation/research/tdx-operating-cash-forecast/snapshot.json').read_text())
    records=[r for r in source['records'] if r['code']=='002613']
    facts=[]
    for row in records:
        for field in ('FN230','FN234','FN114'):
            quarter=int(row['period'][5:7])//3
            facts.append(dict(code='002613',instrument_id=1,period=row['period'],field=field,value=str(value(row,field)),unit='CNY',
                period_type=({1:'Q1',2:'H1',3:'9M',4:'FY'}[quarter] if field=='FN114' else f'Q{quarter}'),statement_scope='provider_default',
                fact_id=len(facts)+1,artifact_sha256='synthetic_standard_provenance',announcement_id='synthetic_filing',available_at='2026-09-01T00:00:00+08:00',
                bits=row['bits'][field],multiplier=1))
    def snapshot(year):
        end=date(year,6,30);windows=[]
        included=[f for f in facts if f'{year-1}-01-01'<=f['period']<=end.isoformat()]
        index={(f['period'],f['field']):f for f in included}
        for field in ('FN230','FN234','FN114'):
            periods=([(end.isoformat(),1),(f'{year-1}-12-31',1),(f'{year-1}-06-30',-1)] if field=='FN114' else [(p,1) for p in quarter_periods(end)])
            used=[index[(p,field)] for p,c in periods]
            windows.append(dict(code='002613',instrument_id=1,field=field,coverage_status='complete',unit='CNY',statement_scope='provider_default',period_type='TTM',
                calculation_basis='ytd' if field=='FN114' else 'quarter',value=str(sum(Decimal(f['value'])*c for f,(p,c) in zip(used,periods))),
                required_inputs=len(used),available_inputs=len(used),source_fact_ids=[f['fact_id'] for f in used],input_periods=[p for p,c in periods],input_coefficients=[c for p,c in periods]))
        return dict(contract_version='alphalake-valuation-v1',code='002613',report_period=end.isoformat(),information_as_of='2026-09-10T00:00:00+08:00',facts=included,windows=windows,supplements=[])
    policy=json.loads((ROOT/'valuation/examples/nonfinancial-history-template.json').read_text())
    return AlphaLakeRequest(data=snapshot(2026),policy=policy),snapshot(2025),source


def test_cash_check_formula_scope_gaps_and_immutability():
    import hashlib
    from tools.backtest_tdx_operating_cash import evaluate
    request,prior,source=fixture();original=copy.deepcopy((request,prior));report=dict(dcf=dict(fcff_projections=[200],reinvestment_projections=[50],revenue_projections=[2500]))
    result=cash_crosscheck(request,prior,report)
    assert result['status']=='research_crosscheck_available'
    p=json.loads((ROOT/'valuation/research/tdx-operating-cash-forecast/protocol.json').read_text())
    p.update(origins=['2026-06-30'],evaluation_as_of='2027-09-10T00:00:00+08:00',samples=[dict(code='002613',split='development')])
    prediction=evaluate(p,source,'development')[0]['forecasts']['mean_two_ocf_margins']
    assert result['cash_forecast']['operating_cashflow']==prediction['ocf_cny']
    assert result['cash_forecast']['ocf_less_capex']==prediction['cash_proxy_cny']
    current_revenue=Decimal(result['observations'][0]['values_cny']['FN230'])
    original_ocf=Decimal(result['cash_forecast']['operating_cashflow'])
    sensitivity=result['revenue_only_sensitivity']
    assert Decimal(sensitivity['operating_cashflow'])==original_ocf*Decimal(2500000000)/current_revenue
    assert Decimal(sensitivity['change_from_cash_forecast'])==Decimal(sensitivity['operating_cashflow'])-original_ocf
    assert sensitivity['status']=='unvalidated_sensitivity_not_cash_forecast_or_fcff'
    assert Decimal(sensitivity['dcf_minus_cash_proxy'])==Decimal('200000000')-Decimal(sensitivity['ocf_less_capex'])
    for revenue in (None,0,-1,float('nan'),float('inf')):
        bad_report=copy.deepcopy(report);bad_report['dcf']['revenue_projections']=[] if revenue is None else [revenue]
        with pytest.raises(ValueError,match='revenue'):cash_crosscheck(request,prior,bad_report)
    basis=result['forecast_basis']
    assert basis['dcf_reinvestment_basis']=='net_capital_expenditure_plus_change_in_operating_working_capital_policy'
    assert basis['comparability']=='net_reinvestment_and_gross_cash_capex_not_like_for_like'
    assert basis['maintenance_capex_status']=='not_separately_estimated'
    zero_report=copy.deepcopy(report);zero_report['dcf']['reinvestment_projections']=[0]
    zero=cash_crosscheck(request,prior,zero_report)
    assert Decimal(zero['comparison']['dcf_reinvestment'])==0
    assert Decimal(zero['cash_forecast']['capital_expenditure'])>0
    assert zero['forecast_basis']['maintenance_capex_status']=='not_separately_estimated'
    assert zero['cash_forecast']==result['cash_forecast']
    c=result['comparison']
    assert Decimal(c['dcf_minus_cash_proxy'])==Decimal(c['nopat_minus_ocf'])-Decimal(c['reinvestment_minus_cash_capex'])
    assert c['classification_status']=='unclassified_difference_not_valuation_error'
    assert (request,prior)==original and report==dict(dcf=dict(fcff_projections=[200],reinvestment_projections=[50],revenue_projections=[2500]))
    missing=copy.deepcopy(prior);missing['windows']=[w for w in missing['windows'] if w['field']!='FN234']
    blocked=cash_crosscheck(request,missing,report)
    assert blocked['status']=='blocked_missing_standard_history' and blocked['cash_forecast'] is None and blocked['comparison'] is None
    assert blocked['missing']==[dict(period='2025-06-30',field='FN234',missing_periods=[])]
    bad=copy.deepcopy(prior);bad['report_period']='2024-06-30'
    with pytest.raises(ValueError,match='security/period/cutoff'):cash_crosscheck(request,bad,report)
    bad=copy.deepcopy(prior);bad['facts'][-1]['bits']+=1
    with pytest.raises(ValueError,match='shared standard'):cash_crosscheck(request,bad,report)
    bad=copy.deepcopy(prior);bad['windows'][0]['value']='999'
    with pytest.raises(ValueError,match='window differs'):cash_crosscheck(request,bad,report)
    bad=copy.deepcopy(prior);bad['facts'][0]['available_at']='2027-01-01T00:00:00Z'
    with pytest.raises(ValueError,match='future'):cash_crosscheck(request,bad,report)
    bad=copy.deepcopy(prior);bad['windows'][1]['calculation_basis']='ytd'
    with pytest.raises(ValueError):cash_crosscheck(request,bad,report)
    assert result['evidence']['research_scope_audit']==SCOPE_AUDIT
    assert SCOPE_AUDIT['summary_sha256']==hashlib.sha256((ROOT/'valuation/research/tdx-operating-cash-forecast/scope-summary.json').read_bytes()).hexdigest()
    for key,name in [('protocol_sha256','protocol.json'),('holdout_sha256','holdout-summary.json')]:
        assert VALIDATION[key]==hashlib.sha256((ROOT/'valuation/research/tdx-operating-cash-forecast'/name).read_bytes()).hexdigest()


def test_uncertainty_metadata_matches_frozen_results_and_cannot_mutate_shared_evidence():
    import hashlib
    directory = ROOT/'valuation/research/tdx-operating-cash-forecast'
    p = json.loads((directory/'uncertainty-protocol.json').read_text())
    assert (UNCERTAINTY['replicates'], UNCERTAINTY['seed']) == (p['sampling']['replicates'], p['sampling']['seed'])
    assert UNCERTAINTY['confidence_level'] == p['interval']['confidence']
    assert UNCERTAINTY['current_company_applicability'] == 'not_established_by_research_summary'
    for population, prefix in [('full_holdout',''), ('forecast_input_subgroup','scope-')]:
        evidence = UNCERTAINTY['populations'][population]
        for key, suffix in [('protocol_sha256','protocol.json'), ('result_sha256','result.json')]:
            assert evidence[key] == hashlib.sha256((directory/(prefix+'uncertainty-'+suffix)).read_bytes()).hexdigest()
        result = json.loads((directory/(prefix+'uncertainty-result.json')).read_text())
        scope = result.get('scope', result)
        assert evidence['candidate_pairs'] == scope['candidates'] and evidence['statuses'] == scope['statuses']
        assert evidence['outside_scope_pairs'] == scope.get('outside_scope_rows',0)
        assert UNCERTAINTY['sampled_companies'] == result['companies']
        assert UNCERTAINTY['sampled_candidate_pairs'] == result['candidates']
        for kind, metrics in evidence['overall'].items():
            for metric, values in metrics.items():
                primary = result['schemes']['stratified']['intervals']['all'][kind][metric]
                sensitivity = result['schemes']['unstratified_sensitivity']['intervals']['all'][kind][metric]
                assert values['point'] == primary['point']
                assert values['stratified_interval'] == primary['interval']
                assert values['unstratified_sensitivity_interval'] == sensitivity['interval']
                assert values['unit'] == ('percentage_points' if metric=='wape_reduction_pp' else 'percent_of_baseline_error')
    request, prior, _ = fixture()
    report = dict(dcf=dict(fcff_projections=[200],reinvestment_projections=[50],revenue_projections=[2500]))
    first = cash_crosscheck(request, prior, report)
    first['evidence']['research_validation'].clear()
    first['evidence']['research_scope_audit'].clear()
    first['evidence']['research_uncertainty']['populations'].clear()
    second = cash_crosscheck(request, prior, report)
    assert second['evidence']['research_validation'] == VALIDATION
    assert second['evidence']['research_scope_audit'] == SCOPE_AUDIT
    assert second['evidence']['research_uncertainty'] == UNCERTAINTY
    assert second['cash_forecast'] == first['cash_forecast'] and second['comparison'] == first['comparison']


def test_real_standard_history_to_company_cash_check(tmp_path):
    import os,subprocess,sys
    from data_sources.alphalake import content_hash
    output=tmp_path/'standard';binary=tmp_path/'alphalake';runs=tmp_path/'runs'
    env=os.environ|{'GOPROXY':'off','GOSUMDB':'off','ALPHALAKE_CASH_HISTORY_EXPORT_DIR':str(output),'ALPHALAKE_VALUATION_RUN_DIR':str(runs)}
    env.pop('ALPHALAKE_CASH_HISTORY_BASE_DB',None)
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealAnkerCashHistory$','-count=1'],cwd=ROOT,env=env,check=True,capture_output=True)
    subprocess.run(['go','build','-o',str(binary),'./cmd/alphalake'],cwd=ROOT,env=env,check=True,capture_output=True)
    args=[sys.executable,'-m','tools.company_valuation',str(output/'acceptance.duckdb'),'300866','--period','2026-06-30',
          '--as-of','2026-09-10T22:18:56.906406Z','--policy',str(ROOT/'valuation/examples/nonfinancial-baseline-2026H1-pilot.json'),'--alphalake',str(binary)]
    def run(extra):return json.loads(subprocess.check_output(args+extra,cwd=ROOT/'valuation/backend',env=env,text=True))
    baseline=run([]);checked=run(['--cash-check']);cash=checked['cash_check']
    assert baseline['valuation']==checked['valuation'] and baseline['status']==checked['status']=='illustrative_book_equity_scenario'
    assert cash['status']=='research_crosscheck_available' and cash['missing']==[]
    assert cash['valuation_run_id']==baseline['valuation']['run_id']
    assert cash['check_id']==content_hash({k:v for k,v in cash.items() if k!='check_id'})
    assert cash['evidence']['research_uncertainty'] == UNCERTAINTY
    snapshots=[json.loads((output/name).read_text()) for name in ('current.json','prior.json')]
    amounts=[{w['field']:Decimal(w['value']) for w in s['windows'] if w['field'] in ('FN230','FN234','FN114')} for s in snapshots]
    current,prior=amounts;ocf=(current['FN234']+prior['FN234']*current['FN230']/prior['FN230'])/2
    assert Decimal(cash['cash_forecast']['operating_cashflow'])==ocf
    assert Decimal(cash['cash_forecast']['ocf_less_capex'])==ocf-current['FN114']
    basis=cash['forecast_basis'];sensitivity=cash['revenue_only_sensitivity']
    assert basis['cash_revenue_growth']=='0' and Decimal(basis['dcf_revenue_growth'])==Decimal('0.2')
    assert Decimal(sensitivity['operating_cashflow'])==ocf*Decimal(basis['dcf_revenue_cny'])/current['FN230']
    assert sensitivity['capital_expenditure']==cash['cash_forecast']['capital_expenditure']
    older=[f for f in cash['observations'][1]['facts'] if f['period'].startswith('2024')]
    assert len(older)==6 and all(f['announcement_id'] and f['pdf_sha256'] and f['artifact_sha256'] for f in older)
    assert {f['field'] for f in older}=={'FN230','FN234','FN114'}
    # Same-cutoff historical evidence is independently required, not silently replaced by a source snapshot.
    request=AlphaLakeRequest.model_validate(json.loads((runs/(cash['valuation_run_id']+'.json')).read_text())['request'])
    bad=copy.deepcopy(snapshots[1]);bad['facts'][0]['instrument_id']+=100
    with pytest.raises(ValueError):cash_crosscheck(request,bad,{'dcf':{}})
    missing=copy.deepcopy(snapshots[1]);missing['windows']=[w for w in missing['windows'] if w['field']!='FN234']
    blocked=cash_crosscheck(request,missing,{'dcf':{}})
    assert blocked['status']=='blocked_missing_standard_history' and blocked['cash_forecast'] is None
