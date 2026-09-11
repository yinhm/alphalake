"""训练剔除不删除评价公司，未来实际值不影响重拟合。"""
import copy
import json
import pytest
from tools import diagnose_tdx_training_exclusions as tool
from tools import verify_training_source_review as evidence


def test_real_exclusions_replay_and_evaluation_denominator():
    result = tool.load_result()
    saved = json.loads((tool.DIRECTORY / 'training-exclusions.json').read_bytes())
    assert json.loads(json.dumps(tool.diagnose(result))) == saved
    rows, _ = tool.refit(result, ('600751', '688670'))
    assert [(r['code'], r['origin'], r['status']) for r in rows] == [
        (r['code'], r['origin'], r['status']) for r in result['results']]
    assert sum(r['status'] == 'evaluated' for r in rows) == 1726
    assert sum(r.get('profit_group') == 'nonpositive' and r['status'] == 'evaluated' for r in rows) == 438
    assert all(r['forecasts'] == original['forecasts'] for r, original in zip(rows, result['results'])
               if r.get('profit_group') == 'nonpositive')
    altered = copy.deepcopy(result)
    for r in altered['results']:
        if 'actual' in r:
            r['actual']['ebit'] += 1000000
    changed, _ = tool.refit(altered, ('600751', '688670'))
    assert [(r['forecasts'], r['calibration']) for r in rows] == [
        (r['forecasts'], r['calibration']) for r in changed]
    assert any(r.get('errors') != s.get('errors') for r, s in zip(rows, changed))


def test_original_evidence_and_tamper_rejection():
    checked = evidence.verify()
    assert checked == json.loads((evidence.DIRECTORY / 'checks.json').read_bytes())
    differences = [r for r in checked['quarter_source_matches'] if r['status'] != 'matched']
    assert [(r['code'], r['period']) for r in differences] == [('600751', '2021-09-30'), ('600751', '2021-12-31')]
    assert checked['jindike_2024H1_rd']['total_cny'] == '20698165.13'
    manifest = json.loads((evidence.DIRECTORY / 'manifest.json').read_bytes())
    bad = copy.deepcopy(manifest)
    bad[0]['evidence'][1]['line'] = bad[0]['evidence'][1]['line'].replace('3,699,614', '3,699,615')
    with pytest.raises(ValueError, match='evidence line'):
        evidence.verify(bad)
    bad = copy.deepcopy(manifest)
    bad[0]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='PDF bytes'):
        evidence.verify(bad)
