"""离线真实标准历史链：冻结政策→保存运行→到期实际核验。"""
import json
import os
from pathlib import Path
import subprocess

from api.alphalake import evaluate
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


def test_archived_real_main_copy_review(tmp_path):
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
    run, _ = load_run(tmp_path, receipt['run_id']); replay(run)
    saved = contents['review.json.gz']; calls = []
    def export(period):
        calls.append(period)
        return saved['results'][0]['actual_snapshot']
    assert review(run, saved['evaluation_as_of'], export) == saved
    assert calls == ['2026-06-30']
