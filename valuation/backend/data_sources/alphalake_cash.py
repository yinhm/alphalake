"""标准事实上的显式现金交叉检查；与DCF并列，不调整估值或补写历史FCFF。"""
from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path

from data_sources.alphalake import Snapshot,BookDCFPolicy,standard_window_reader,content_hash

MODEL='mean-two-ocf-margins-v1'
VALIDATION={'protocol_sha256': '6aeb6080de4f423b92896eea921acbdf7543a4865f0aa447364e52a59b32ce36', 'holdout_sha256': '6d8334c661e73acdc6cb43196eb1e986fb8323571ea49b285b2fcd1f6cc816cd'}

SCOPE_AUDIT=dict(summary_sha256='acd690ac5b0f5f480522dd5bb1274a3bb07808de0d67ce5818e890df5ad317ac',status='retrospective_subgroup_review_not_new_validation',
    boundary='forecast_inputs_ready_not_full_DCF_admission; zero_financial_signals_not_business_verification; subgroup_year_WAPE_can_worsen')


# Immutable research releases; regression checks every exported interval against its source artifact.
UNCERTAINTY = {'schema_version': 'cash-research-uncertainty-v1',
 'status': 'posthoc_conditional_evidence_not_new_validation',
 'current_company_applicability': 'not_established_by_research_summary',
 'interval_method': 'pointwise_percentile',
 'confidence_level': 0.95,
 'resampling_unit': 'company_all_origins_and_models_paired',
 'replicates': 9999,
 'seed': 20260911,
 'information_cutoff': 'September_1_China_midnight_each_origin',
 'origins': ['2023-06-30', '2024-06-30', '2025-06-30'],
 'evaluation_as_of': '2026-09-10T00:00:00+08:00',
 'sampled_companies': 120,
 'sampled_candidate_pairs': 360,
 'strata': 38,
 'singleton_strata': 17,
 'populations': {'full_holdout': {'protocol_sha256': 'a81b2944d1d86df23ff8cd1b51d63de5469bab545a49954126f19cbfe87afe22',
                                  'result_sha256': 'bc0007cb4262d6bafef6b27a3ccd25a491a6c0541486bbf37393283aa7567fd4',
                                  'candidate_pairs': 360,
                                  'statuses': {'evaluated': 331, 'blocked': 29},
                                  'outside_scope_pairs': 0,
                                  'overall': {'ocf_cny': {'relative_mae_reduction_pct': {'unit': 'percent_of_baseline_error',
                                                                                         'point': 6.844689857679764,
                                                                                         'stratified_interval': [2.082084517936023,
                                                                                                                 11.357515193431022],
                                                                                         'unstratified_sensitivity_interval': [1.3976760466168694,
                                                                                                                               12.482902513386822]},
                                                          'wape_reduction_pp': {'unit': 'percentage_points',
                                                                                'point': 3.4924521373856767,
                                                                                'stratified_interval': [0.8658856791402313,
                                                                                                        6.328918730797968],
                                                                                'unstratified_sensitivity_interval': [0.34106028933027194,
                                                                                                                      6.686007233782536]}},
                                              'cash_proxy_cny': {'relative_mae_reduction_pct': {'unit': 'percent_of_baseline_error',
                                                                                                'point': 6.0889361418381105,
                                                                                                'stratified_interval': [1.9736047417506544,
                                                                                                                        9.98215010371144],
                                                                                                'unstratified_sensitivity_interval': [1.3950945517579785,
                                                                                                                                      10.658595587867092]},
                                                                 'wape_reduction_pp': {'unit': 'percentage_points',
                                                                                       'point': 3.0276451316866857,
                                                                                       'stratified_interval': [-0.027834016241271978,
                                                                                                               6.764600674889431],
                                                                                       'unstratified_sensitivity_interval': [-0.820080880859054,
                                                                                                                             7.370836063880458]}}}},
                 'forecast_input_subgroup': {'protocol_sha256': '494be00c414cfa6d6695c63554d75bef3071c7b57f53d907c5764c2436e545d4',
                                             'result_sha256': 'bb03c2ad96794a24ee7c8bc1a2c4b247ce95c5a28eb2ad491a60316915d0804a',
                                             'candidate_pairs': 255,
                                             'statuses': {'evaluated': 244, 'blocked': 11},
                                             'outside_scope_pairs': 105,
                                             'overall': {'ocf_cny': {'relative_mae_reduction_pct': {'unit': 'percent_of_baseline_error',
                                                                                                    'point': 6.697742637884703,
                                                                                                    'stratified_interval': [1.6846689881630428,
                                                                                                                            11.62004262423399],
                                                                                                    'unstratified_sensitivity_interval': [0.5734184523901453,
                                                                                                                                          12.853265144205787]},
                                                                     'wape_reduction_pp': {'unit': 'percentage_points',
                                                                                           'point': 2.1841439099103814,
                                                                                           'stratified_interval': [-0.15743748609376287,
                                                                                                                   4.795359301886314],
                                                                                           'unstratified_sensitivity_interval': [-0.8470238335165026,
                                                                                                                                 5.171757867751187]}},
                                                         'cash_proxy_cny': {'relative_mae_reduction_pct': {'unit': 'percent_of_baseline_error',
                                                                                                           'point': 4.232255925137218,
                                                                                                           'stratified_interval': [-0.026823814135469557,
                                                                                                                                   8.328061733951772],
                                                                                                           'unstratified_sensitivity_interval': [-0.9496069514189639,
                                                                                                                                                 9.668819041352554]},
                                                                            'wape_reduction_pp': {'unit': 'percentage_points',
                                                                                                  'point': 0.8061572405915548,
                                                                                                  'stratified_interval': [-1.9196999271239954,
                                                                                                                          4.199851820535286],
                                                                                                  'unstratified_sensitivity_interval': [-2.997915292068354,
                                                                                                                                        4.877237592124426]}}},
                                             'scope_group': 'forecast_ready_no_financial_signal'}},
 'limitations': ['positive_values_mean_lower_historical_error_not_company_cash_or_valuation_bounds',
                 'fixed_three_origins_do_not_estimate_future_macro_regime_uncertainty',
                 'singleton_strata_cross_company_dependence_revisions_survivorship_and_missingness_limit_inference',
                 'some_single_origin_intervals_include_zero_not_stable_each_year',
                 'cash_proxy_WAPE_and_subgroup_cash_proxy_main_intervals_include_zero',
                 'forecast_input_subgroup_not_full_DCF_admission_or_verified_business',
                 'research_obtained_later_not_available_at_historical_information_cutoff',
                 'reported_OCF_less_cash_capex_not_FCFF_no_valuation_adjustment']}

def cash_crosscheck(request,prior_data,report):
    current=request.data;prior=Snapshot.model_validate(prior_data)
    result=dict(model_id=MODEL,status='blocked_missing_standard_history',code=current.code,report_period=current.report_period.isoformat(),
        information_as_of=current.information_as_of.isoformat(),unit='CNY',cash_forecast=None,comparison=None,missing=[],
        evidence=dict(research_validation=VALIDATION,research_scope_audit=SCOPE_AUDIT,research_uncertainty=UNCERTAINTY,current_snapshot_sha256=content_hash(current.model_dump(mode='json')),prior_snapshot_sha256=content_hash(prior.model_dump(mode='json')),
                      code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        boundary='retrospective_research_crosscheck_not_asof_policy; reported_OCF_less_cash_capex_not_FCFF; no_valuation_adjustment; formula_validation_not_independent_semantic_evidence')
    result['evidence'] = deepcopy(result['evidence'])
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
    dcf=report.get('dcf') or {};fcff=dcf.get('fcff_projections',[]);reinvest=dcf.get('reinvestment_projections',[]);revenues=dcf.get('revenue_projections',[])
    if not fcff or not reinvest or not revenues:raise ValueError('first-year DCF cashflow and revenue required')
    f=Decimal(str(fcff[0]))*1000000;r=Decimal(str(reinvest[0]))*1000000
    if not f.is_finite() or not r.is_finite():raise ValueError('nonfinite DCF cashflow')
    revenue=Decimal(str(revenues[0]))*1000000
    if not revenue.is_finite() or revenue<=0:raise ValueError('positive finite DCF revenue required')
    scaled_ocf=ocf*revenue/now['FN230']
    result['forecast_basis']=dict(cash_revenue_cny=str(now['FN230']),cash_revenue_growth='0',dcf_revenue_cny=str(revenue),
        dcf_revenue_growth=str(revenue/now['FN230']-1),cash_capex_rule='repeat_current_ttm',
        cash_ocf_rule=MODEL,dcf_reinvestment_basis='net_capital_expenditure_plus_change_in_operating_working_capital_policy',
        cash_capex_basis='reported_cash_purchase_of_long_lived_assets',
        maintenance_capex_status='not_separately_estimated',
        comparability='net_reinvestment_and_gross_cash_capex_not_like_for_like')
    result['revenue_only_sensitivity']=dict(status='unvalidated_sensitivity_not_cash_forecast_or_fcff',
        operating_cashflow=str(scaled_ocf),capital_expenditure=str(capex),ocf_less_capex=str(scaled_ocf-capex),
        change_from_cash_forecast=str(scaled_ocf-ocf),dcf_minus_cash_proxy=str(f-(scaled_ocf-capex)),
        held_fixed=['mean_ocf_margin','cash_capital_expenditure','dcf_fcff'],
        boundary='only_cash_revenue_replaced_by_dcf_revenue; not_reinvestment_matched; no_valuation_adjustment')
    result.update(status='research_crosscheck_available',cash_forecast=dict(operating_cashflow=str(ocf),capital_expenditure=str(capex),ocf_less_capex=str(cash)),
        comparison=dict(forecast_year=1,dcf_fcff=str(f),dcf_reinvestment=str(r),dcf_nopat_derived=str(f+r),
            dcf_minus_cash_proxy=str(f-cash),nopat_minus_ocf=str(f+r-ocf),reinvestment_minus_cash_capex=str(r-capex),
            classification_status='unclassified_difference_not_valuation_error',
            unresolved=['different_revenue_and_profit_forecast_assumptions','operating_tax_and_financing_investment_classification','depreciation_and_noncash_reinvestment','working_capital_scope_acquisitions_and_asset_disposals']))
    return result
