"""真实原文、季度源精度和证据篡改拒绝。"""
import copy
import json
from decimal import Decimal

import pytest
from tools.audit_tdx_half_growth_scope import audit, ROOT

DIR = ROOT / 'valuation/research/tdx-half-growth'


def test_real_source_scope():
    config = json.loads((DIR / 'scope-review.json').read_text())
    result = audit(config)
    saved = json.loads((DIR / 'scope-result.json').read_text())
    assert result == {k: v for k, v in saved.items() if k != 'evidence'}
    assert len(result['checks']) == 15
    assert result['business_phrases'] == 3
    assert not result['full_archive_checked']
    for row in result['checks']:
        assert abs(Decimal(row['tdx_cny'])-Decimal(row['pdf_cny'])) <= Decimal(row['rounding_bound_cny'])
    # 年报四个单季与原文全年收入独立加总；不把H1重复比较列算作新季度。
    for code, annual in [('000876', '103062962254.34'), ('002714', '110860727714.40'), ('002459', '70120697029.73')]:
        quarters = [r for r in result['checks'] if r['code'] == code and len(r['periods']) == 1]
        assert len(quarters) == 4
        assert sum(Decimal(r['pdf_cny']) for r in quarters) == Decimal(annual)


def test_tampering_rejected():
    config = json.loads((DIR / 'scope-review.json').read_text())
    changed = copy.deepcopy(config)
    changed['documents'][1]['checks'][0]['values'][0] = '24198301291.42'
    with pytest.raises(ValueError, match='PDF amounts differ'): audit(changed)
    changed = copy.deepcopy(config)
    changed['documents'][1]['checks'][0]['source_period_groups'][0] = ['2023-06-30']
    with pytest.raises(ValueError, match='source revenue differs'): audit(changed)
    changed = copy.deepcopy(config); changed['source']['sha256'] = '0'*64
    with pytest.raises(ValueError, match='source hash differs'): audit(changed)
    changed = copy.deepcopy(config); changed['documents'][0]['selected_sha256'] = '0'*64
    with pytest.raises(ValueError, match='PDF hash differs'): audit(changed)
