"""调度边界使用真实子进程、模拟各数据命令退出码；不声称真实数据源验收。"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from tools import refresh_valuate_alphalake as refresh


@pytest.mark.parametrize('failed', ['sync-financial','sync-bse-code-transitions','materialize-fundamentals','repair-filings','materialize-after-filing-repair','sync-cny-yield','sync-industry-capital','sync-company-industries'])
def test_refresh_preserves_stage_failures_and_gates_materialization(tmp_path,monkeypatch,failed):
    execute=refresh.execute
    seen=[]
    def stub(command,log,timeout):
        name=log.stem;seen.append(name)
        if name in ('sync-bse-code-transitions','sync-country-risk','sync-industry-beta','sync-industry-capital','sync-cny-yield','sync-credit-spreads','sync-company-industries'):
            assert command[2]==('test.duckdb' if name in ('sync-company-industries','sync-bse-code-transitions') else 'references.duckdb')
            assert command[command.index('--python')+1]==sys.executable
            assert Path(command[command.index('--parser')+1]).is_absolute()
            assert Path(command[command.index('--parser')+1]).is_file()
        return execute([sys.executable,'-c',f'print({name!r});raise SystemExit({int(name==failed)})'],log,timeout)
    monkeypatch.setattr(refresh,'execute',stub)
    args=SimpleNamespace(database='test.duckdb',period='2026-06-30',as_of=None,latest=6,
                         filings_start='2025-04-01',filings_end='2026-09-09',alphalake='alphalake',
                         policy='policy.json',stage_timeout=10,reference_database='references.duckdb',sync_references=True)
    assert refresh.run_cycle(args,tmp_path)==1
    report=json.loads((tmp_path/'run.json').read_text())
    assert report['status']=='partial_or_failed'
    assert [r['name'] for r in report['stages'] if r['status']=='failed']==[failed]
    assert ('batch-valuation' in seen)==(failed not in ('materialize-fundamentals','materialize-after-filing-repair'))
    assert seen[:5]==['sync-financial','sync-bse-code-transitions','sync-filings','sync-industries','materialize-fundamentals']
    if failed!='materialize-fundamentals':
        assert seen[5:7]==['repair-filings','materialize-after-filing-repair']
        assert report['information_as_of']>=report['stages'][6]['finished_at']
    if 'batch-valuation' in seen:
        assert seen[7:13]==['sync-country-risk','sync-industry-beta','sync-industry-capital','sync-cny-yield','sync-credit-spreads','sync-company-industries']
        assert report['information_as_of']>=report['stages'][12]['finished_at']
    for stage in report['stages']:
        if stage['name']=='batch-valuation':assert stage['command'][-2:]==['--reference-database','references.duckdb']
    assert all(Path(r['log']).read_text().strip()==r['name'] for r in report['stages'])
    assert report['information_as_of']>=report['stages'][4]['finished_at']


def test_refresh_stage_timeout_stops_child(tmp_path):
    log=tmp_path/'timeout.log'
    code=refresh.execute([sys.executable,'-c',
        'import signal,time;signal.signal(signal.SIGINT,lambda *_:exit(0));print("started",flush=True);time.sleep(60)'],log,1)
    assert code==124
    assert log.read_text()=='started\n'


def test_refresh_publishes_attempt_state_and_retains_on_failure(tmp_path, monkeypatch):
    state = tmp_path/'incremental-state.json'
    old = dict(contract_version='alphalake-batch-v1', source_database='test.duckdb',
               universe_count=0, companies=[], automation_counts={'computable':0})
    state.write_text(json.dumps(old))
    args = SimpleNamespace(database='test.duckdb', period='2026-06-30', as_of='2026-09-17T00:00:00Z', latest=6,
        filings_start='2026-09-01', filings_end='2026-09-17', alphalake='alphalake', policy='policy.json',
        stage_timeout=10, reference_database=None, sync_references=False, incremental_state=str(state))
    def execute(command, log, timeout):
        if log.stem == 'batch-valuation':
            assert command[command.index('--previous-report')+1] == str(state)
            output = Path(command[command.index('--output-dir')+1]);output.mkdir()
            report = output/'batch-1.json'
            report.write_text(json.dumps(old | {'attempt':'new'}))
            log.write_text(json.dumps({'report':str(report)}))
        else:
            log.write_text(log.stem)
        return 0
    monkeypatch.setattr(refresh, 'execute', execute)
    root = tmp_path/'first';root.mkdir()
    assert refresh.run_cycle(args, root) == 0
    published = state.read_bytes()
    assert json.loads(published)['attempt'] == 'new'
    # 子进程失败不发布；成功退出但输出损坏同样不能覆盖最后状态。
    for malformed in (False, True):
        def fail(command, log, timeout):
            if log.stem == 'batch-valuation':
                log.write_text('invalid json')
                return 0 if malformed else 1
            return execute(command, log, timeout)
        monkeypatch.setattr(refresh, 'execute', fail)
        root = tmp_path/str(malformed);root.mkdir()
        assert refresh.run_cycle(args, root) == 1
        assert state.read_bytes() == published
