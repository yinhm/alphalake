from copy import deepcopy
from decimal import Decimal

import pytest

from tests.test_operating_constraints import observation, rule
from tests.test_operating_constraints import review_case
from tools.review_operating_constraints import calculate


def data():
    current=observation('total',segment='enterprise',printed_low='120')
    prior=observation('prior',segment='enterprise',printed_low='100',period_start='2025-01-01',period_end='2025-06-30')
    a=observation('a',segment='product/a',printed_low='72')
    old_a=observation('old-a',segment='product/a',printed_low='60',period_start='2025-01-01',period_end='2025-06-30')
    b=observation('b',segment='product/b',printed_low='48')
    old_b=observation('old-b',segment='product/b',printed_low='40',period_start='2025-01-01',period_end='2025-06-30')
    return {o.id:o for o in (current,prior,a,old_a,b,old_b)}


def mix(values,**changes):
    return calculate(rule('revenue_mix',list(values),**changes),values)


def test_exact_contributions_reconcile_and_requirements_are_explicit_not_forecasts():
    values=data();before=deepcopy(values)
    result=mix(values,total_growth_condition='.20',growth_driver='product/a',other_segments_growth_condition='0')
    assert result['coverage']=='complete_with_display_rounding'
    assert result['prior_total_minus_reconstruction_interval_cny']==['0','0']
    assert [r['contribution_to_total_growth_interval'] for r in result['segments']]==[['0.12','0.12'],['0.08','0.08']]
    required=result['requirement']
    assert Decimal(required['required_driver_growth'])==Decimal(1)/3
    assert required['period_end']=='2027-06-30'
    assert required['status']=='conditional_identity_not_forecast' and result['future_bound'] is None
    assert values==before


def test_rounded_growth_preserves_uncertainty_instead_of_manufacturing_prior_amounts():
    values=data()
    for key in ('old-a','old-b'):
        original=values[key]
        values[key]=observation(key,segment=original.segment,metric='revenue_growth',printed_low='20.00',source_unit='percent')
    result=mix(values)
    a=result['segments'][0]
    assert Decimal(a['prior_revenue_interval_cny'][0])<60<Decimal(a['prior_revenue_interval_cny'][1])
    assert a['prior_basis']=='interval_from_rounded_growth_not_reported_amount'
    assert result['requirement'] is None


def test_prior_reconciliation_uses_prior_amount_display_precision():
    values=data()
    for key in ('total','prior','a','b'):
        values[key]=values[key].model_copy(update={'printed_low':values[key].printed_low+'.000'})
    values['old-a']=values['old-a'].model_copy(update={'printed_low':'60.0'})
    values['old-b']=values['old-b'].model_copy(update={'printed_low':'40.1'})
    result=mix(values)
    assert result['coverage']=='complete_with_display_rounding'
    assert result['prior_total_minus_reconstruction_interval_cny']==['-0.100','-0.100']


def test_missing_segments_remain_unclassified_and_cannot_be_assumed_flat():
    values=data();del values['b'];del values['old-b']
    result=mix(values,total_growth_condition='.2',growth_driver='product/a',other_segments_growth_condition='0')
    assert result['current_unclassified_residual_cny']=='48'
    assert result['coverage']=='partial_not_zero_filled'
    assert result['requirement']['value'] is None


@pytest.mark.parametrize('change', ['axis','duplicate','window','scope','overlap','prior','threshold','guidance','zero-rounded-base'])
def test_mix_rejects_false_partitions_or_incompatible_evidence(change):
    values=data()
    if change=='axis':
        for k in ('b','old-b'):values[k]=values[k].model_copy(update={'segment':'geography/b'})
    elif change=='duplicate':
        for k in ('b','old-b'):values[k]=values[k].model_copy(update={'segment':'product/a'})
    elif change=='window':values['a']=values['a'].model_copy(update={'period_end':values['prior'].period_end})
    elif change=='scope':values['a']=values['a'].model_copy(update={'scope':'parent'})
    elif change=='overlap':values['a']=values['a'].model_copy(update={'printed_low':'90'})
    elif change=='prior':values['old-a']=values['old-a'].model_copy(update={'printed_low':'10'})
    elif change=='threshold':values['a']=values['a'].model_copy(update={'comparison':'greater_than'})
    elif change=='guidance':values['a']=values['a'].model_copy(update={'kind':'guidance'})
    else:
        values['a']=values['a'].model_copy(update={'printed_low':'0'})
        values['old-a']=observation('old-a',segment='product/a',metric='revenue_growth',printed_low='10.00',source_unit='percent')
    with pytest.raises(ValueError):mix(values)


def test_explicit_condition_requires_all_values_and_preserves_impossible_requirement():
    with pytest.raises(ValueError,match='explicit total'):
        mix(data(),total_growth_condition='.2')
    result=mix(data(),total_growth_condition='-.9',growth_driver='product/a',other_segments_growth_condition='2')
    assert not result['requirement']['feasible_nonnegative_driver_revenue']
    assert Decimal(result['requirement']['required_driver_growth']) < -1


def test_source_decline_remains_a_negative_contribution():
    values=data()
    values['a']=values['a'].model_copy(update={'printed_low':'-10'})
    values['b']=values['b'].model_copy(update={'printed_low':'130'})
    result=mix(values)
    assert result['segments'][0]['contribution_to_total_growth_interval']==['-0.7','-0.7']


def test_mix_in_native_review_keeps_h1_separate_from_annual_or_ttm_forecast(review_case):
    tool,spec,database,report,pdf,calls=review_case
    total=spec['observations'][0]
    total['segment']='enterprise'
    for identifier,segment,prior in [('prior','enterprise',True),('part','product/a',False),('part-prior','product/a',True)]:
        row=deepcopy(total);row.update(id=identifier,segment=segment)
        if prior:row.update(period_start='2025-01-01',period_end='2025-06-30')
        spec['observations'].append(row)
    spec['calculations']=[dict(id='mix',ticker='SHSE:600519',kind='revenue_mix',
        observations=['revenue','prior','part','part-prior'],condition='同半年业务条件与年度原生输入分开',
        total_growth_condition='.2',growth_driver='product/a',other_segments_growth_condition='0')]
    spec['cards'][0]['calculations']=['mix']
    relation=tool.review(spec,database)['cards'][0]['quantitative_relations'][0]
    assert not relation['native_alignment']['mix_matches_base']
    assert not relation['native_alignment']['condition_matches_forecast']
    assert relation['native_alignment']['first_forecast_window']==dict(period_start='2026-01-01',period_end='2026-12-31')
    assert not relation['native_alignment']['automatic_conversion']
