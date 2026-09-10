"""封闭准入清单展开，不把源目录新增项静默批准为可估值。"""
import copy
import csv
from collections import Counter, defaultdict
from datetime import datetime
from tools.batch_valuate_alphalake import match_industry_rules
import json
from pathlib import Path

import pytest
from tools.prepare_industry_policy import expand_policy

RECIPE=Path(__file__).resolve().parents[2]/'examples/a-share-nonfinancial-recipe-2026H1.json'


def test_reviewed_recipe_expands_without_changing_economic_assumptions():
    recipe=json.loads(RECIPE.read_text());original=copy.deepcopy(recipe)
    policy=expand_policy(recipe)
    assert recipe==original
    assert len(policy.industry_rules)==78 and len(recipe['excluded_industries'])==16
    assert policy.exclusions==recipe['exclusions']
    assert all(name in policy.review_note for name in recipe['excluded_industries'])
    for rule in policy.industry_rules:
        name,=rule.node_codes
        assert name in recipe['approved_industries'] and name not in recipe['excluded_industries']
        assert rule.capital_policy.industry==rule.wacc_policy.industries[0].industry==name
        assert rule.wacc_policy.industries[0].weight==1
        assert rule.policy.growth_ceiling==.2 and rule.policy.terminal_growth==.02
        assert rule.wacc_policy.capital_structure_basis=='industry_reference_weights'
    assert expand_policy(recipe).model_dump_json()==policy.model_dump_json()


@pytest.mark.parametrize('mutation',['duplicate','unknown','omitted','overlap','no_reason','different_source','mixed_industry','target_override'])
def test_recipe_rejects_incomplete_scope_and_mismatched_template(mutation):
    r=json.loads(RECIPE.read_text());t=r['template_rule']
    if mutation=='duplicate':r['approved_industries'].append(r['approved_industries'][0])
    elif mutation=='unknown':r['approved_industries'].append('future provider industry')
    elif mutation=='omitted':r['approved_industries'].pop()
    elif mutation=='overlap':r['approved_industries'].append(next(iter(r['excluded_industries'])))
    elif mutation=='no_reason':r['excluded_industries'][next(iter(r['excluded_industries']))]=''
    elif mutation=='different_source':t['source']='tdx'
    elif mutation=='mixed_industry':t['capital_policy']['industry']='Machinery'
    else:t['wacc_policy']['target_debt_weight']=.1
    with pytest.raises(ValueError):expand_policy(r)


def test_bse_crosswalk_cohort_market_and_semantic_boundaries():
    root=RECIPE.parent
    with (root/'bse-shenwan-proxies-2026H1.csv').open(newline='') as f:
        crosswalk=list(csv.DictReader(f))
    counts=defaultdict(Counter)
    names={r['node_code']:r['node_name'] for r in crosswalk}
    identities=set()
    with (root/'bse-shenwan-cohort-2026H1.csv').open(newline='') as f:
        for row in csv.DictReader(f):
            assert row['instrument_id'] not in identities
            identities.add(row['instrument_id'])
            assert row['symbol'].startswith(('sh','sz'))
            assert row['tdx_node_name']==names[row['tdx_node_code']]
            counts[row['tdx_node_code']][row['damodaran_industry']]+=1
    recipe=json.loads(RECIPE.read_text())
    policy=expand_policy(recipe,crosswalk)
    bse=[r for r in policy.industry_rules if r.exchange_mic=='XBSE']
    assert len(crosswalk)==128 and len(bse)==48 and len(policy.industry_rules)==126
    for row in crosswalk:
        n=sum(counts[row['node_code']].values())
        top=counts[row['node_code']].most_common(1)
        eligible=n>=5 and top[0][1]/n>=.8 and top[0][0] in recipe['approved_industries']
        assert bool(row['proxy_industry'])==eligible
        if eligible:assert row['proxy_industry']==top[0][0]
        else:assert row['node_code'] in policy.review_note
    rule=bse[0];code=rule.node_codes[0]
    member=dict(source='tdx',taxonomy_code='tdx_shenwan_industry',node_code=code,node_name=rule.node_names[code],
                observed_at='2026-09-10T00:00:00Z',run_finished_at='2026-09-10T00:01:00Z')
    company=dict(exchange_mic='XBSE',industry_memberships=[member])
    cutoff=datetime.fromisoformat('2026-09-10T04:42:23Z')
    assert len(match_industry_rules(company,[rule],cutoff))==1
    for mic in ('XSHG','XSHE',None):
        assert not match_industry_rules(dict(company,exchange_mic=mic),[rule],cutoff)
    member['node_name']='changed source meaning'
    with pytest.raises(ValueError,match='name differs'):match_industry_rules(company,[rule],cutoff)
    member['observed_at']='2025-01-01T00:00:00Z'
    assert not match_industry_rules(company,[rule],cutoff)
    for mutation in ('duplicate','unknown_proxy','no_reason'):
        broken=copy.deepcopy(crosswalk)
        if mutation=='duplicate':broken.append(broken[0])
        elif mutation=='unknown_proxy':broken[0]['proxy_industry']='new unreviewed industry'
        else:broken[0]['review_note']=''
        with pytest.raises(ValueError):expand_policy(recipe,broken)
