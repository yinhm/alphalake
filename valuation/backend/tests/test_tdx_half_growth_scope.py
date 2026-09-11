"""真实原文、季度源精度和证据篡改拒绝。"""
import copy
import gzip
import hashlib
from datetime import datetime
import json
from decimal import Decimal

import pytest
from tools.audit_tdx_half_growth_scope import audit, reconcile_ttm, review_disclosures, ROOT

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


def test_full_revenue_ttm_chain(tmp_path):
    config = json.loads((DIR / 'ttm-scope-review.json').read_text())
    result = audit(config)
    saved = json.loads((DIR / 'ttm-scope-result.json').read_text())
    assert result == {k: v for k, v in saved.items() if k != 'evidence'}
    assert len(result['checks']) == 57
    ttm = result['ttm']
    assert (ttm['evaluated_windows'], ttm['blocked_windows']) == (15, 3)
    assert (ttm['required_quarter_records'], ttm['directly_checked_quarter_records'], ttm['grouped_only_quarter_records']) == (48, 42, 6)
    assert len(ttm['reconciled']) == 30
    assert not ttm['original_decision']['passed']
    # 用全年减上半年加次年上半年独立复算2026H1，与逐季拼接的核验结果对照。
    for code in config['forecast']['codes']:
        quarters = {r['periods'][0]: Decimal(r['pdf_cny']) for r in result['checks'] if r['code'] == code and len(r['periods']) == 1}
        half = next(Decimal(r['pdf_cny']) for r in result['checks'] if r['code'] == code and r['periods'] == ['2026-03-31', '2026-06-30'])
        fy = sum(quarters['2025-'+q] for q in ('03-31', '06-30', '09-30', '12-31'))
        expected = fy-quarters['2025-03-31']-quarters['2025-06-30']+half
        rows = [r for r in ttm['reconciled'] if r['code'] == code and r['part'] == 'actual' and int(r['origin'][:4])+r['horizon'] == 2026]
        assert rows and all(Decimal(r['pdf_cny']) == expected for r in rows)
    incomplete = [r for r in result['checks'] if not (r['code'] == '000876' and r['periods'] == ['2022-12-31'])]
    with pytest.raises(ValueError, match='coverage incomplete'): reconcile_ttm(config, incomplete)
    changed = copy.deepcopy(config); changed['forecast']['sha256'] = '0'*64
    with pytest.raises(ValueError, match='forecast hash differs'): reconcile_ttm(changed, result['checks'])
    forecast = json.loads(gzip.decompress((ROOT/config['forecast']['path']).read_bytes()))
    row = next(r for r in forecast['results'] if r['code'] == '000876' and r['status'] == 'evaluated')
    row['actual']['revenue'] += 1  # 百万元；哈希重新绑定仍须被PDF数值核验拒绝。
    raw = gzip.compress(json.dumps(forecast).encode(), mtime=0); path = tmp_path/'changed.json.gz'; path.write_bytes(raw)
    changed = copy.deepcopy(config); changed['forecast'] = dict(config['forecast'], path=str(path), sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='stored TTM differs'): reconcile_ttm(changed, result['checks'])


def test_disposal_disclosure_and_accounting_date_boundaries():
    config = json.loads((DIR/'disposal-review.json').read_text())
    result = audit(config)
    saved = json.loads((DIR/'disposal-result.json').read_text())
    assert result == {k: v for k, v in saved.items() if k != 'evidence'}
    assert result['business_phrases'] == 18 and result['checks'] == []
    assert [r['disclosed_date'] for r in result['disclosures']] == ['2023-12-16', '2023-12-16', '2023-12-27', '2024-01-30', '2024-04-30']
    assert [r['available_at'] for r in result['disclosures']] == ['2023-12-17T00:00:00+08:00', '2023-12-17T00:00:00+08:00', '2023-12-28T00:00:00+08:00', '2024-01-31T00:00:00+08:00', '2024-05-01T00:00:00+08:00']
    for year, expected in [(2023, 0), (2024, 5), (2025, 5)]:
        cutoff = datetime.fromisoformat(f'{year}-09-01T00:00:00+08:00')
        assert sum(datetime.fromisoformat(r['available_at']) <= cutoff for r in result['disclosures']) == expected
    # 中国12月17日零点对应UTC16日16点；不得提前到公告当日零点。
    available = datetime.fromisoformat(result['disclosures'][0]['available_at'])
    assert datetime.fromisoformat('2023-12-16T15:59:59+00:00') < available
    assert datetime.fromisoformat('2023-12-16T16:00:00+00:00') == available
    changed = copy.deepcopy(config)
    changed['documents'][-1]['contains'][0][1] = changed['documents'][-1]['contains'][0][1].replace('12月31日', '12月29日')
    with pytest.raises(ValueError, match='business phrase missing'): audit(changed)
    changed = copy.deepcopy(config); changed['documents'][0]['announcement_id'] = '1218637100'
    with pytest.raises(ValueError, match='identity differs'): review_disclosures(changed)
    changed = copy.deepcopy(config); changed['catalogues'][0]['sha256'] = '0'*64
    with pytest.raises(ValueError, match='catalogue hash differs'): review_disclosures(changed)
