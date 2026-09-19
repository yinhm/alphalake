"""真实原文与新标准导出；缺期不得变零，诊断不修改DCF。"""
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from data_sources.alphalake import AlphaLakeRequest, build_inputs
from engine.orchestrator import run_full_valuation
from tools.verify_asset_disposal_cash import verify

ROOT = Path(__file__).resolve().parents[3]


def test_disposal_evidence_and_missing_ttm(monkeypatch):
    evidence = verify()
    assert [r['standard_eligible'] for r in evidence] == [False, False, True]
    for row in evidence:
        with pytest.raises(AssertionError):
            verify({row['period']:str(float(row['pdf_value_cny'])+.01)})
    directory = ROOT/'valuation/research/disposal-cash-20260918'
    raw = gzip.decompress((directory/'standard-export.json.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == json.loads((directory/'receipt.json').read_text())['export_sha256']
    request = json.loads(gzip.decompress((ROOT/'valuation/research/current-contract-20260919/reviewed-assets-20260917/before-request.json.gz').read_bytes()))
    request['data'] = json.loads(gzip.decompress((ROOT/'valuation/research/current-contract-20260919/disposal-cash-20260918/standard-export.json.gz').read_bytes()))
    parsed = AlphaLakeRequest.model_validate(request)
    inputs, audit = build_inputs(parsed)
    cap = audit['company_capital_evidence']
    assert cap['cash_capex_million_cny'] == pytest.approx(319.63428)
    assert cap['asset_disposal_cash_million_cny'] is None
    assert cap['cash_capex_after_disposals_million_cny'] is None
    assert cap['cash_capex_after_disposals_status'] == 'missing_standard_cash_components'
    window, = [w for w in parsed.data.windows if w['field']=='long_lived_asset_disposal_cash']
    assert window['available_inputs'] == 1 and window['missing_periods'] == ['2025-12-31','2025-06-30']
    old = parsed.model_copy(deep=True)
    old.data.windows = [w for w in old.data.windows if w['field']!='long_lived_asset_disposal_cash']
    old.data.facts = [f for f in old.data.facts if f['field']!='long_lived_asset_disposal_cash']
    before, _ = build_inputs(old)
    assert asdict(run_full_valuation(before)) == asdict(run_full_valuation(inputs))
    # 把缺期窗口伪装成完成仍须由共享血缘校验拒绝。
    bad = deepcopy(request)
    w = next(w for w in bad['data']['windows'] if w['field']=='long_lived_asset_disposal_cash')
    w.update(coverage_status='complete', value='17350')
    with pytest.raises(ValueError):
        build_inputs(AlphaLakeRequest.model_validate(bad))

    # 受控完整窗口只检验减法方向；不是声称真实缺期已补齐。
    from data_sources import alphalake
    reader = alphalake.standard_window_reader
    def complete_reader(data):
        window, consumed = reader(data)
        def value(field, required=True):
            return 0.01735 if field == 'long_lived_asset_disposal_cash' else window(field, required)
        return value, consumed
    monkeypatch.setattr(alphalake, 'standard_window_reader', complete_reader)
    _, complete = build_inputs(parsed)
    assert complete['company_capital_evidence']['cash_capex_after_disposals_million_cny'] == pytest.approx(319.61693)
    assert complete['company_capital_evidence']['cash_capex_after_disposals_status'] == 'cash_component_not_total_reinvestment'


def test_reviewed_zero_real_lifecycle(tmp_path, monkeypatch):
    import os
    import subprocess
    from tools.verify_disposal_review import verify_chain, request_for, approve_zeros
    directory = tmp_path/'source'
    env = dict(os.environ, ALPHALAKE_DISPOSAL_EXPORT_DIR=str(directory))
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealAssetDisposalCash$','-count=1'],cwd=ROOT,env=env,check=True,capture_output=True)
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR', str(tmp_path/'runs'))
    receipt, _ = verify_chain(directory,tmp_path/'runs')
    assert receipt['reviewed_cash_capex_after_disposals_cny']=='319616930'
    assert receipt['dcf_report_unchanged'] and receipt['historical_fcff'] is None
    data = json.loads((directory/'disposal-reviewed-export.json').read_text())
    request = approve_zeros(request_for(data))
    notes = json.loads((ROOT/'valuation/research/disposal-cash-20260918/zero-supplements.json').read_text())
    evidence = {r['period']:r for r in verify()}
    for note in notes:
        assert note['pdf_sha256']==evidence[note['period']]['pdf_sha256']
        assert note['pdf_page']==evidence[note['period']]['page']
        assert note['value']=='0.00' and evidence[note['period']]['source_bits']==0
    for problem in ('amount','unit','period','hash','source','filing','missing','expired','future','duplicate','override'):
        bad = deepcopy(request)
        rule = bad['policy']['disposal_cash_zeros'][0]
        note = next(n for n in bad['data']['supplements'] if n['period']==rule['period'] and n['item']==rule['item'])
        if problem=='amount': note['value']='0.01'
        elif problem=='unit': note['unit']='CNY/share'
        elif problem=='period': note['period_basis']='instant'
        elif problem=='hash': rule['evidence_sha256']='0'*64
        elif problem=='source': rule['source_artifact_sha256']='0'*64
        elif problem=='filing': note['announcement_id']='different'
        elif problem=='missing': bad['data']['supplements'].remove(note)
        elif problem=='expired': bad['data']['information_as_of']='2026-10-18T00:00:00Z'
        elif problem=='future': bad['policy']['reviewed_at']='2026-09-19T00:00:00Z'
        elif problem=='duplicate': bad['policy']['disposal_cash_zeros'].append(deepcopy(rule))
        elif problem=='override':
            fact = deepcopy(next(f for f in bad['data']['facts'] if f['field']=='long_lived_asset_disposal_cash'))
            fact['period']=rule['period'];fact['fact_id']=999999
            bad['data']['facts'].append(fact)
        with pytest.raises(ValueError):
            build_inputs(AlphaLakeRequest.model_validate(bad))
