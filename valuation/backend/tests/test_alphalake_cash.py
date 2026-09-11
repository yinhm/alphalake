"""现金核对契约：真实研究数值配模拟标准血缘，不冒充历史标准链验收。"""
import copy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest
from data_sources.alphalake import AlphaLakeRequest
from data_sources.alphalake_cash import cash_crosscheck,VALIDATION
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
    request,prior,source=fixture();original=copy.deepcopy((request,prior));report=dict(dcf=dict(fcff_projections=[200],reinvestment_projections=[50]))
    result=cash_crosscheck(request,prior,report)
    assert result['status']=='research_crosscheck_available'
    p=json.loads((ROOT/'valuation/research/tdx-operating-cash-forecast/protocol.json').read_text())
    p.update(origins=['2026-06-30'],evaluation_as_of='2027-09-10T00:00:00+08:00',samples=[dict(code='002613',split='development')])
    prediction=evaluate(p,source,'development')[0]['forecasts']['mean_two_ocf_margins']
    assert result['cash_forecast']['operating_cashflow']==prediction['ocf_cny']
    assert result['cash_forecast']['ocf_less_capex']==prediction['cash_proxy_cny']
    c=result['comparison']
    assert Decimal(c['dcf_minus_cash_proxy'])==Decimal(c['nopat_minus_ocf'])-Decimal(c['reinvestment_minus_cash_capex'])
    assert c['classification_status']=='unclassified_difference_not_valuation_error'
    assert (request,prior)==original and report==dict(dcf=dict(fcff_projections=[200],reinvestment_projections=[50]))
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
    for key,name in [('protocol_sha256','protocol.json'),('holdout_sha256','holdout-summary.json')]:
        assert VALIDATION[key]==hashlib.sha256((ROOT/'valuation/research/tdx-operating-cash-forecast'/name).read_bytes()).hexdigest()
