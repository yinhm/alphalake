"""识别以细分业务为依据；周期条件失败不能悄悄展示原默认。"""
from copy import deepcopy

import pytest

from tools.csi300_valuation import CYCLE_GROUPS, LEAF_GROUP, check_cashflows, classify, comparison, display_value
from tests.test_cyclical_valuation import baseline


def company(leaf, scope='nonfinancial_by_reference'):
    return dict(ticker='SZSE:301308', scope=scope, industry_evidence=[
        dict(taxonomy_code='tdx_shenwan_industry', node_name=leaf),
        dict(taxonomy_code='damodaran_industry_2026', node_name='Computers/Peripherals')])


@pytest.mark.parametrize('leaf', ['板材', '动力煤', '铜', '航运', '面板', '生猪养殖', '住宅开发', '综合乘用车', '半导体设备'])
def test_cycle_mechanism_uses_specific_domestic_business(leaf):
    r = classify(company(leaf))
    assert r['category'] == '周期暴露' and r['group'] == LEAF_GROUP[leaf]
    assert not r['cycle_position_verified'] and not r['full_cycle_verified']


@pytest.mark.parametrize('leaf', ['调味品', '云软件服务', '水力发电', '品牌消费电子', '集成电路设计', '网络接配及塔设'])
def test_macro_exposure_and_broad_technology_are_not_sufficient(leaf):
    assert classify(company(leaf))['category'] == '未采用周期方法'


def test_review_requires_identity_business_and_source_locator():
    review = dict(ticker='SZSE:301308', expected_industry='集成电路设计', group='存储供需',
        business_basis='已归档原文说明存储晶圆价格、采购成本滞后及盈利传导',
        source=dict(path='evidence.pdf', sha256='a'*64, url='https://issuer.example/report.pdf', pages=[41]))
    assert classify(company('集成电路设计'), review)['group'] == '存储供需'
    for key, value in [('ticker', 'SHSE:603986'), ('expected_industry', '品牌消费电子'), ('source', {}), ('business_basis', '')]:
        changed = deepcopy(review); changed[key] = value
        with pytest.raises(ValueError, match='requires matching'):
            classify(company('集成电路设计'), changed)
    with pytest.raises(ValueError, match='financial scope'):
        classify(company('集成电路设计', 'outside_financial_scope'), review)
    ambiguous = company('板材')
    ambiguous['industry_evidence'].append(dict(taxonomy_code='tdx_shenwan_industry', node_name='云软件服务'))
    assert classify(ambiguous)['category'] == '分类待核实'
    assert len(LEAF_GROUP) == sum(map(len, CYCLE_GROUPS.values()))


def test_rejected_cycle_has_no_default_or_alternate_scenario_fallback():
    row = dict(classification={'category':'周期暴露'}, baseline_value=10000,
        variants={'arithmetic-3y':{'status':'requires_terminal_recovery_condition'},
                  'arithmetic-1y':{'status':'calculated_explicit_condition','value':50}})
    assert display_value(row) is None
    row['variants']['arithmetic-3y'] = dict(status='calculated_explicit_condition', value=-10)
    assert display_value(row) == -10
    row['classification']['category'] = '未采用周期方法'
    assert display_value(row) == 10000


def test_price_comparison_keeps_negative_values_and_rejects_invalid_prices():
    assert comparison(-5, 10) == (-15, -150)
    assert comparison(None, 10) == (None, None)
    for value, price in [(float('nan'), 1), (1, float('inf')), (1, 0), (1, -1)]:
        with pytest.raises(ValueError):
            comparison(value, price)


def test_independent_cashflow_and_terminal_aggregation_detect_tampering():
    body = baseline(research=True)
    check_cashflows(body)
    for field in ('pv_terminal_value', 'value_of_operating_assets'):
        changed = deepcopy(body); changed['dcf'][field] += 1
        with pytest.raises(ValueError, match='independent'):
            check_cashflows(changed)
    changed = deepcopy(body); changed['dcf']['fcff_projections'][0] += 1
    with pytest.raises(ValueError, match='independent'):
        check_cashflows(changed)
