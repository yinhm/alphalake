"""真实标准请求：压力测试不改基线，缺项不归零，源血缘篡改须拒绝。"""
import gzip
from tools.migrate_standard_contract import upgrade_legacy
import json
from pathlib import Path

import pytest
from data_sources.alphalake import AlphaLakeRequest
from tools.review_company_inputs import review

ROOT = Path(__file__).resolve().parents[2]/'research/method-closure-20260912'


@pytest.mark.parametrize('code', ['300866', '002032'])
def test_company_input_review(code, tmp_path, monkeypatch):
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path))
    request = AlphaLakeRequest.model_validate(upgrade_legacy(json.loads(gzip.decompress((ROOT/f'{code}-request.json.gz').read_bytes()))))
    original = request.model_dump_json()
    result = review(request)
    assert request.model_dump_json() == original
    assert review(request) == result
    assert result['coverage']['asset_fields_approved_for_addback'] == 0
    shares = next(r['value'] for r in request.data.windows if r['field'] == 'total_shares')
    shares = float(shares)/1e6*(1+request.policy.extra_dilution_rate)
    for item in result['financial_assets']:
        if item['value_million_cny'] is None:
            assert item['status'] == 'missing_standard_value'
            assert 'book_addback_sensitivity' not in item
        else:
            assert item['book_addback_sensitivity']['delta_per_share'] == pytest.approx(item['value_million_cny']/shares)
    runs = {p.stem: upgrade_legacy(json.loads(p.read_text())) for p in tmp_path.glob('*.json')}
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
    next(r for r in bad.data.windows if r['field'] == 'trading_financial_assets')['value'] = '1'
    with pytest.raises(ValueError, match='window differs'): review(bad)
    bad = request.model_copy(deep=True)
    next(r for r in bad.data.windows if r['field'] == 'trading_financial_assets')['input_periods'] = ['2025-06-30']
    with pytest.raises(ValueError, match='incorrect window periods'): review(bad)


def test_anker_asset_amounts_against_existing_pdf_ledger():
    # 原文逐项提取另由既有 verify.py 锁定；此处连接冻结标准事实与该证据。
    import csv
    import hashlib
    import struct
    evidence = ROOT.parents[2]/'internal/ingest/testdata/anker-valuation-2026'
    request = AlphaLakeRequest.model_validate(upgrade_legacy(json.loads(gzip.decompress((ROOT/'300866-request.json.gz').read_bytes()))))
    manifest = upgrade_legacy(json.loads((evidence/'reports.json').read_text()))['1225533054']
    assert hashlib.sha256((evidence/manifest['file']).read_bytes()).hexdigest() == manifest['sha256']
    rows = {r['id']: r for r in csv.DictReader((evidence/'reported.csv').open())}
    for field, key in [('trading_financial_assets', 'trading_assets'), ('long_term_equity_investments', 'equity_investments')]:
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
    expected = upgrade_legacy(json.loads((ROOT/'valuation/research/company-inputs-20260917/anker-capital-decision.json').read_bytes()))
    assert expected['identified_capital']['formula'] == 'standard_FN114 + reviewed_ROU_additions - standard_FN136_FN137_FN138_FN579_FN581'
    expected['identified_capital']['formula'] = 'capital_expenditure_cash + reviewed_ROU_additions - matched_standard_depreciation_amortization'
    assert review() == expected
    assert expected['company_marginal_sales_to_capital'] is None
    assert expected['baseline_policy_unchanged']


def test_anker_opening_allowance_tamper_rejected(tmp_path, monkeypatch):
    import shutil
    from tools import review_anker_capital_evidence as module
    relative = Path('valuation/research/company-inputs-20260917/anker-opening-2024')
    shutil.copytree(module.ROOT/relative, tmp_path/relative)
    pdf_relative = Path('internal/ingest/testdata/anker-cash-history-2024/1223379891.pdf')
    (tmp_path/pdf_relative).parent.mkdir(parents=True)
    shutil.copyfile(module.ROOT/pdf_relative, tmp_path/pdf_relative)
    notes_path = tmp_path/relative/'supplements.json'
    notes = upgrade_legacy(json.loads(notes_path.read_bytes()))
    notes[1]['value'] = '2656060.73'
    notes_path.write_text(json.dumps(notes))
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    with pytest.raises(AssertionError):
        module.verify_opening_2024()


def test_anker_lease_additions_tamper_rejected(tmp_path, monkeypatch):
    import shutil
    from tools import review_anker_capital_evidence as module
    directory = Path('valuation/research/company-inputs-20260917')
    (tmp_path/directory).mkdir(parents=True)
    for name in ('anker-capital-standard.json.gz', 'anker-lease-supplements.json'):
        shutil.copyfile(module.ROOT/directory/name, tmp_path/directory/name)
    reports = Path('internal/ingest/testdata/anker-valuation-2026')
    (tmp_path/reports).parent.mkdir(parents=True)
    (tmp_path/reports).symlink_to(module.ROOT/reports, target_is_directory=True)
    path = tmp_path/directory/'anker-lease-supplements.json'
    notes = upgrade_legacy(json.loads(path.read_bytes()))
    notes[0]['value'] = '135396200.08'
    path.write_text(json.dumps(notes))
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    with pytest.raises(AssertionError):
        module.verify_identified_capital()
