"""调度边界使用真实子进程、模拟各数据命令退出码；不声称真实数据源验收。"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from tools import refresh_valuate_alphalake as refresh


@pytest.mark.parametrize('failed', ['sync-financial','materialize-fundamentals','repair-filings','materialize-after-filing-repair','sync-cny-yield','sync-industry-capital'])
def test_refresh_preserves_stage_failures_and_gates_materialization(tmp_path,monkeypatch,failed):
    execute=refresh.execute
    seen=[]
    def stub(command,log,timeout):
        name=log.stem;seen.append(name)
        if name in ('sync-country-risk','sync-industry-beta','sync-industry-capital','sync-cny-yield','sync-credit-spreads'):
            assert command[2]=='references.duckdb'
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
    if failed!='materialize-fundamentals':
        assert seen[4:6]==['repair-filings','materialize-after-filing-repair']
        assert report['information_as_of']>=report['stages'][5]['finished_at']
    if 'batch-valuation' in seen:
        assert seen[6:11]==['sync-country-risk','sync-industry-beta','sync-industry-capital','sync-cny-yield','sync-credit-spreads']
        assert report['information_as_of']>=report['stages'][10]['finished_at']
    for stage in report['stages']:
        if stage['name']=='batch-valuation':assert stage['command'][-2:]==['--reference-database','references.duckdb']
    assert all(Path(r['log']).read_text().strip()==r['name'] for r in report['stages'])
    assert report['information_as_of']>=report['stages'][3]['finished_at']


def test_refresh_stage_timeout_stops_child(tmp_path):
    log=tmp_path/'timeout.log'
    code=refresh.execute([sys.executable,'-c',
        'import signal,time;signal.signal(signal.SIGINT,lambda *_:exit(0));print("started",flush=True);time.sleep(60)'],log,1)
    assert code==124
    assert log.read_text()=='started\n'
