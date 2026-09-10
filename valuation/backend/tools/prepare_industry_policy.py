"""将封闭行业准入清单展开为既有批次政策；不自动批准目录新增行业。"""
import argparse
import csv
from copy import deepcopy
import json
from pathlib import Path

from tools.batch_valuate_alphalake import BatchPolicy, IndustryRule

CATALOG = Path(__file__).resolve().parents[3]/'internal/source/damodaran/industries-global-2026.txt'


def expand_policy(recipe, bse_crosswalk=None):
    if set(recipe) != {'policy_version','review_note','template_rule','approved_industries','excluded_industries','exclusions','scope_review'}:
        raise ValueError('unexpected or missing industry recipe fields')
    approved, excluded = recipe['approved_industries'], recipe['excluded_industries']
    known = set(CATALOG.read_text().splitlines())
    if (not isinstance(approved,list) or not approved or not all(isinstance(x,str) for x in approved)
            or len(approved)!=len(set(approved)) or not isinstance(excluded,dict)
            or set(approved)&set(excluded) or set(approved)|set(excluded)!=known
            or not all(isinstance(v,str) and v.strip() for v in excluded.values())
            or not isinstance(recipe['scope_review'],str) or not recipe['scope_review'].strip()):
        raise ValueError('explicit disjoint complete industry scope and exclusion reasons required')
    template = IndustryRule.model_validate(recipe['template_rule'])
    if (template.source!='damodaran' or template.taxonomy_code!='damodaran_industry_2026' or template.exchange_mic is not None or template.node_names is not None
            or len(template.node_codes)!=1 or template.node_codes[0] not in known or template.capital_policy is None or template.wacc_policy is None
            or template.capital_policy.industry!=template.node_codes[0]
            or len(template.wacc_policy.industries)!=1 or template.wacc_policy.industries[0].industry!=template.node_codes[0]
            or template.wacc_policy.industries[0].weight!=1
            or template.wacc_policy.capital_structure_basis!='industry_reference_weights'):
        raise ValueError('same-source single-industry capital and reference-target WACC template required')
    rules=[]
    for index,industry in enumerate(sorted(approved),1):
        rule=deepcopy(template.model_dump(mode='json'))
        rule.update(rule_id=f'damodaran-admitted-{index}',node_codes=[industry])
        rule['policy']['nonfinancial_scope_review']=industry+'：'+recipe['scope_review']
        rule['capital_policy']['industry']=industry
        rule['wacc_policy']['industries'][0]['industry']=industry
        rules.append(rule)
    bse_excluded={}
    if bse_crosswalk is not None:
        seen=set()
        if not bse_crosswalk:
            raise ValueError('empty BSE industry policy crosswalk')
        for item in bse_crosswalk:
            if (set(item)!={'node_code','node_name','proxy_industry','review_note'}
                    or not all(isinstance(v,str) and (v.strip() or k=='proxy_industry') for k,v in item.items())
                    or item['node_code'] in seen or item['proxy_industry'] and item['proxy_industry'] not in approved):
                raise ValueError('invalid or duplicate BSE industry proxy mapping')
            seen.add(item['node_code'])
            industry=item['proxy_industry']
            if not industry:
                bse_excluded[item['node_code']]=item['node_name']+'：'+item['review_note']
                continue
            rule=deepcopy(template.model_dump(mode='json'))
            rule.update(rule_id='bse-shenwan-'+item['node_code'],source='tdx',taxonomy_code='tdx_shenwan_industry',
                        exchange_mic='XBSE',node_codes=[item['node_code']],node_names={item['node_code']:item['node_name']},
                        review_note=item['review_note'])
            rule['policy']['nonfinancial_scope_review']=item['review_note']+'；'+recipe['scope_review']
            rule['capital_policy']['industry']=industry
            rule['capital_policy']['mapping_reason']=item['review_note']
            rule['wacc_policy']['industries'][0].update(industry=industry,reason=item['review_note'])
            rules.append(rule)
    return BatchPolicy(policy_version=recipe['policy_version']+('-bse-proxies-v1' if bse_crosswalk is not None else ''),
        review_note=recipe['review_note']+'\n行业隔离：'+json.dumps(excluded,ensure_ascii=False,sort_keys=True)+('\n北交所行业隔离：'+json.dumps(bse_excluded,ensure_ascii=False,sort_keys=True) if bse_crosswalk is not None else ''),
        assignments={},industry_rules=rules,exclusions=recipe['exclusions'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recipe')
    parser.add_argument('--bse-crosswalk',help='显式TDX申万→全球行业代理CSV，只用于XBSE')
    args=parser.parse_args()
    crosswalk=None
    if args.bse_crosswalk:
        with Path(args.bse_crosswalk).open(newline='') as source:
            crosswalk=list(csv.DictReader(source))
    print(expand_policy(json.loads(Path(args.recipe).read_text()),crosswalk).model_dump_json(indent=2))


if __name__=='__main__':
    main()
