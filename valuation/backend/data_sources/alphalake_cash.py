"""标准事实上的显式现金交叉检查；与DCF并列，不调整估值或补写历史FCFF。"""
from decimal import Decimal
import hashlib
from pathlib import Path

from data_sources.alphalake import Snapshot,BookDCFPolicy,standard_window_reader,content_hash

MODEL='mean-two-ocf-margins-v1'
VALIDATION={'protocol_sha256': '6aeb6080de4f423b92896eea921acbdf7543a4865f0aa447364e52a59b32ce36', 'holdout_sha256': '6d8334c661e73acdc6cb43196eb1e986fb8323571ea49b285b2fcd1f6cc816cd'}


def cash_crosscheck(request,prior_data,report):
    current=request.data;prior=Snapshot.model_validate(prior_data)
    result=dict(model_id=MODEL,status='blocked_missing_standard_history',code=current.code,report_period=current.report_period.isoformat(),
        information_as_of=current.information_as_of.isoformat(),unit='CNY',cash_forecast=None,comparison=None,missing=[],
        evidence=dict(research_validation=VALIDATION,current_snapshot_sha256=content_hash(current.model_dump(mode='json')),prior_snapshot_sha256=content_hash(prior.model_dump(mode='json')),
                      code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        boundary='retrospective_research_crosscheck_not_asof_policy; reported_OCF_less_cash_capex_not_FCFF; no_valuation_adjustment; formula_validation_not_independent_semantic_evidence')
    if not isinstance(request.policy,BookDCFPolicy) or current.report_period.month!=6:
        result['status']='unsupported_policy_or_period';return result
    if prior.code!=current.code or prior.report_period!=current.report_period.replace(year=current.report_period.year-1) or prior.information_as_of!=current.information_as_of:
        raise ValueError('cash history security/period/cutoff differs')
    if current.source_conflicts or prior.source_conflicts:raise ValueError('cash history source conflict')
    ids=[{r['instrument_id'] for r in d.facts if 'instrument_id' in r} for d in (current,prior)]
    if all(ids) and ids[0]!=ids[1]:raise ValueError('cash history instrument identity differs')
    shared={(f['field'],f['period']):f for f in current.facts}
    for f in prior.facts:
        if (f['field'],f['period']) in shared and shared[(f['field'],f['period'])]!=f:raise ValueError('shared standard history differs at same cutoff')
    observations=[]
    for d in (current,prior):
        window,consumed=standard_window_reader(d);values={};windows={r['field']:r for r in d.windows}
        for field,basis in [('FN230','quarter'),('FN234','quarter'),('FN114','ytd')]:
            if window(field,False) is None:
                result['missing'].append(dict(period=d.report_period.isoformat(),field=field,missing_periods=windows.get(field,{}).get('missing_periods',[])));continue
            if windows[field]['calculation_basis']!=basis:raise ValueError('cash field period basis differs')
            values[field]=Decimal(windows[field]['value'])
        used={fid for c in consumed for fid in c['source_fact_ids']};facts=[f for f in d.facts if f['fact_id'] in used]
        if any(f['multiplier']!=1 for f in facts):raise ValueError('cash field multiplier differs')
        observations.append(dict(period=d.report_period.isoformat(),values_cny={f:str(v) for f,v in values.items()},facts=facts))
    result['observations']=observations
    if result['missing']:return result
    now,before=({f:Decimal(v) for f,v in o['values_cny'].items()} for o in observations)
    if min(now['FN230'],before['FN230'])<=0 or min(now['FN114'],before['FN114'])<0:
        result['status']='blocked_nonpositive_revenue_or_negative_capex';return result
    ocf=(now['FN234']+before['FN234']*now['FN230']/before['FN230'])/2
    capex=now['FN114'];cash=ocf-capex
    dcf=report.get('dcf') or {};fcff=dcf.get('fcff_projections',[]);reinvest=dcf.get('reinvestment_projections',[])
    if not fcff or not reinvest:raise ValueError('first-year DCF cashflow required')
    f=Decimal(str(fcff[0]))*1000000;r=Decimal(str(reinvest[0]))*1000000
    if not f.is_finite() or not r.is_finite():raise ValueError('nonfinite DCF cashflow')
    result.update(status='research_crosscheck_available',cash_forecast=dict(operating_cashflow=str(ocf),capital_expenditure=str(capex),ocf_less_capex=str(cash)),
        comparison=dict(forecast_year=1,dcf_fcff=str(f),dcf_reinvestment=str(r),dcf_nopat_derived=str(f+r),
            dcf_minus_cash_proxy=str(f-cash),nopat_minus_ocf=str(f+r-ocf),reinvestment_minus_cash_capex=str(r-capex),
            classification_status='unclassified_difference_not_valuation_error',
            unresolved=['different_revenue_and_profit_forecast_assumptions','operating_tax_and_financing_investment_classification','depreciation_and_noncash_reinvestment','working_capital_scope_acquisitions_and_asset_disposals']))
    return result
