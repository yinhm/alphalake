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
    request = json.loads(gzip.decompress((ROOT/'valuation/research/reviewed-assets-20260917/before-request.json.gz').read_bytes()))
    request['data'] = json.loads(raw)
    parsed = AlphaLakeRequest.model_validate(request)
    inputs, audit = build_inputs(parsed)
    cap = audit['company_capital_evidence']
    assert cap['cash_capex_million_cny'] == pytest.approx(319.63428)
    assert cap['asset_disposal_cash_million_cny'] is None
    assert cap['cash_capex_after_disposals_million_cny'] is None
    assert cap['cash_capex_after_disposals_status'] == 'missing_standard_cash_components'
    window, = [w for w in parsed.data.windows if w['field']=='FN110']
    assert window['available_inputs'] == 1 and window['missing_periods'] == ['2025-12-31','2025-06-30']
    old = parsed.model_copy(deep=True)
    old.data.windows = [w for w in old.data.windows if w['field']!='FN110']
    old.data.facts = [f for f in old.data.facts if f['field']!='FN110']
    before, _ = build_inputs(old)
    assert asdict(run_full_valuation(before)) == asdict(run_full_valuation(inputs))
    # 把缺期窗口伪装成完成仍须由共享血缘校验拒绝。
    bad = deepcopy(request)
    w = next(w for w in bad['data']['windows'] if w['field']=='FN110')
    w.update(coverage_status='complete', value='17350')
    with pytest.raises(ValueError):
        build_inputs(AlphaLakeRequest.model_validate(bad))

    # 受控完整窗口只检验减法方向；不是声称真实缺期已补齐。
    from data_sources import alphalake
    reader = alphalake.standard_window_reader
    def complete_reader(data):
        window, consumed = reader(data)
        def value(field, required=True):
            return 0.01735 if field == 'FN110' else window(field, required)
        return value, consumed
    monkeypatch.setattr(alphalake, 'standard_window_reader', complete_reader)
    _, complete = build_inputs(parsed)
    assert complete['company_capital_evidence']['cash_capex_after_disposals_million_cny'] == pytest.approx(319.61693)
    assert complete['company_capital_evidence']['cash_capex_after_disposals_status'] == 'cash_component_not_total_reinvestment'
