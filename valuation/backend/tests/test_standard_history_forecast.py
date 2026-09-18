"""离线真实标准历史链：冻结政策→保存运行→到期实际核验。"""
import json
import os
from pathlib import Path
import subprocess
from copy import deepcopy

import pytest

from api.alphalake import evaluate
from tools.migrate_standard_contract import upgrade_legacy
from data_sources.alphalake import AlphaLakeRequest
from tools.compare_valuations import replay
from tools.review_valuation_forecast import review


def test_standard_history_forecast(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path/'standard'
    env = os.environ | {'ALPHALAKE_EARNINGS_HISTORY_BASE_DB':'', 'ALPHALAKE_Q1_HISTORY_REVIEW_DIR':'', 'GOPROXY':'off', 'GOSUMDB':'off'}
    # 复用两步离线Go验收，实际值仍由标准事实导出。
    env['ALPHALAKE_EARNINGS_HISTORY_EXPORT_DIR'] = str(output)
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealAnkerEarningsHistory$','-count=1'],cwd=root,env=env,check=True,capture_output=True)
    env['ALPHALAKE_Q1_HISTORY_REVIEW_DIR'] = str(output)
    subprocess.run(['go','test','./internal/ingest','-run','^TestRealAnkerQ1History$','-count=1'],cwd=root,env=env,check=True,capture_output=True)
    policy = json.loads((root/'valuation/research/standard-history-2025H1/policy.json').read_bytes())
    template = json.loads((root/'valuation/examples/nonfinancial-history-template.json').read_bytes())
    assert {k:v for k,v in policy.items() if isinstance(v,(int,float))} == {k:v for k,v in template.items() if isinstance(v,(int,float))}
    data = json.loads((output/'historical-q1.json').read_bytes())
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path/'runs'))
    run = evaluate(AlphaLakeRequest(data=data,policy=policy))
    replay(run)
    actual = json.loads((output/'current.json').read_bytes())
    calls = []
    def export(period):
        calls.append(period)
        return actual
    result = review(run,'2026-09-10T00:00:00+00:00',export)
    assert calls == ['2026-06-30']
    assert result['statuses'] == {'evaluated':1,'not_yet_observable':9}
    metrics = result['results'][0]['metrics']
    assert set(metrics) == {'revenue','adjusted_ebit'}
    assert all(m['status']=='evaluated' for m in metrics.values())


def test_archived_real_main_copy_review(tmp_path,monkeypatch):
    import gzip
    import hashlib
    from tools.compare_valuations import load_run
    root = Path(__file__).resolve().parents[2]/'research/standard-history-2025H1'
    receipt = json.loads((root/'result.json').read_bytes())
    contents = {}
    for name, digests in receipt['archives'].items():
        compressed = (root/name).read_bytes(); raw = gzip.decompress(compressed)
        assert hashlib.sha256(compressed).hexdigest() == digests['sha256']
        assert hashlib.sha256(raw).hexdigest() == digests['uncompressed_sha256']
        contents[name] = json.loads(raw)
        if name == 'run.json.gz':
            (tmp_path/(receipt['run_id']+'.json')).write_bytes(raw)
    old, _ = load_run(tmp_path, receipt['run_id'])
    with pytest.raises(ValueError,match='alphalake-valuation-v2'):
        replay(old)  # 生产的历史报告严格核对不因本轮放宽。
    monkeypatch.setenv('ALPHALAKE_VALUATION_RUN_DIR',str(tmp_path/'current'))
    run = evaluate(AlphaLakeRequest.model_validate(upgrade_legacy(old['request']))); replay(run)
    expected_report = deepcopy(old['report'])
    expected_report['dcf']['implied_roic_projections'] = [None]*10
    expected_report['dcf']['implied_roic_terminal'] = None
    assert run['report'] == expected_report
    from data_sources.alphalake import content_hash
    assert run['request'] == AlphaLakeRequest.model_validate(upgrade_legacy(old['request'])).model_dump(mode='json')
    old_inputs = deepcopy(old['inputs'])
    old_inputs['prepared_ttm']['provenance']['alphalake_snapshot'] = content_hash(run['request']['data'])
    assert run['inputs'] == old_inputs
    saved = contents['review.json.gz']; calls = []
    def export(period):
        calls.append(period)
        return upgrade_legacy(saved['results'][0]['actual_snapshot'])
    from api.alphalake import ENGINE_REVISION
    from data_sources.alphalake import content_hash
    # 新契约改变证据哈希/运行标识；原预测与全部实际误差保持不变。
    result = review(run, saved['evaluation_as_of'], export)
    assert result['statuses'] == saved['statuses']
    for old_row,new_row in zip(saved['results'],result['results'],strict=True):
        for key in ('status','horizon','target_period','metrics','predictions_million_cny','actual_fcff'):
            assert old_row.get(key)==new_row.get(key)
        if 'actual_snapshot' in old_row:
            assert new_row['actual_snapshot']==upgrade_legacy(old_row['actual_snapshot'])
            assert new_row['actual_snapshot_sha256']==content_hash(new_row['actual_snapshot'])
    assert result['review_id']==content_hash({k:v for k,v in result.items() if k!='review_id'})
    assert calls == ['2026-06-30']
