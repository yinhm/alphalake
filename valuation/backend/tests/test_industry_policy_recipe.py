"""封闭准入清单展开，不把源目录新增项静默批准为可估值。"""
import copy
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
