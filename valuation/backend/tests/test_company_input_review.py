"""真实标准请求：压力测试不改基线，缺项不归零，源血缘篡改须拒绝。"""
import gzip
import json
from pathlib import Path

import pytest
from data_sources.alphalake import AlphaLakeRequest
from tools.review_company_inputs import review

ROOT = Path(__file__).resolve().parents[2]/'research/method-closure-20260912'


@pytest.mark.parametrize('code', ['300866', '002032'])
def test_company_input_review(code, tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    request = AlphaLakeRequest.model_validate_json(gzip.decompress((ROOT/f'{code}-request.json.gz').read_bytes()))
    original = request.model_dump_json()
    result = review(request)
    assert request.model_dump_json() == original
    assert review(request) == result
    assert result['coverage']['asset_fields_approved_for_addback'] == 0
    shares = next(r['value'] for r in request.data.windows if r['field'] == 'FN238')
    shares = float(shares)/1e6*(1+request.policy.extra_dilution_rate)
    for item in result['financial_assets']:
        if item['value_million_cny'] is None:
            assert item['status'] == 'missing_standard_value'
            assert 'book_addback_sensitivity' not in item
        else:
            assert item['book_addback_sensitivity']['delta_per_share'] == pytest.approx(item['value_million_cny']/shares)
    runs = {p.stem: json.loads(p.read_text()) for p in tmp_path.glob('*.json')}
    baseline = runs[result['baseline_run_id']]
    for item in result['financial_assets']:
        if 'book_addback_sensitivity' in item:
            assert item['book_addback_sensitivity']['operating_value'] == baseline['report']['dcf']['value_of_operating_assets']
    for item in result['capital_efficiency_sensitivity']:
        changed = runs[item['run_id']]
        assert changed['report']['cost_of_capital'] == baseline['report']['cost_of_capital']
        assert changed['inputs']['equity_bridge'] == baseline['inputs']['equity_bridge']
        for key in ('revenue_projections', 'ebit_projections'):
            assert changed['report']['dcf'][key] == baseline['report']['dcf'][key]
        for before, after in zip(baseline['report']['dcf']['reinvestment_projections'], item['annual_reinvestment']):
            assert after == pytest.approx(before/item['multiplier'])
    bad = request.model_copy(deep=True)
    next(r for r in bad.data.windows if r['field'] == 'FN9')['value'] = '1'
    with pytest.raises(ValueError, match='window differs'): review(bad)
    bad = request.model_copy(deep=True)
    next(r for r in bad.data.windows if r['field'] == 'FN9')['input_periods'] = ['2025-06-30']
    with pytest.raises(ValueError, match='incorrect window periods'): review(bad)


def test_anker_asset_amounts_against_existing_pdf_ledger():
    # 原文逐项提取另由既有 verify.py 锁定；此处连接冻结标准事实与该证据。
    import csv
    import hashlib
    import struct
    evidence = ROOT.parents[2]/'internal/ingest/testdata/anker-valuation-2026'
    request = AlphaLakeRequest.model_validate_json(gzip.decompress((ROOT/'300866-request.json.gz').read_bytes()))
    manifest = json.loads((evidence/'reports.json').read_text())['1225533054']
    assert hashlib.sha256((evidence/manifest['file']).read_bytes()).hexdigest() == manifest['sha256']
    rows = {r['id']: r for r in csv.DictReader((evidence/'reported.csv').open())}
    for field, key in [('FN9', 'trading_assets'), ('FN25', 'equity_investments')]:
        original = rows['2026-06-30/'+key]
        window = next(w for w in request.data.windows if w['field'] == field)
        assert len(window['source_fact_ids']) == 1
        fact = next(f for f in request.data.facts if f['fact_id'] == window['source_fact_ids'][0])
        assert fact['announcement_id'] == original['pdf_id'] == '1225533054'
        assert fact['pdf_url'] == manifest['url']
        assert fact['period'] == original['period'] == '2026-06-30'
        assert struct.unpack('<I', struct.pack('<f', float(original['value'])))[0] == fact['bits']


def test_anker_capital_evidence_does_not_approve_incomplete_ratio():
    from tools.review_anker_capital_evidence import ROOT, review
    expected = json.loads((ROOT/'valuation/research/company-inputs-20260917/anker-capital-decision.json').read_bytes())
    assert review() == expected
    assert expected['company_marginal_sales_to_capital'] is None
    assert expected['baseline_policy_unchanged']
