"""真实文件切换与故障恢复；不以此冒充100家真实财务验收。"""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3

import pytest
from tools import publish_native_valuation as delivery


def test_publish_failure_rollback_and_success(tmp_path):
    old, new, journal = [tmp_path/n for n in ('current.sqlite', 'next.sqlite', 'publication.json')]
    old.write_bytes(b'trusted')
    new.write_bytes(b'candidate')
    def reject():
        assert old.read_bytes() == b'candidate'
        raise ValueError('live value differs')
    with pytest.raises(ValueError, match='live value'):
        delivery.publish(new, old, journal, reject)
    assert old.read_bytes() == b'trusted'
    assert json.loads(journal.read_text())['status'] == 'rolled_back'
    new.write_bytes(b'candidate')
    result = delivery.publish(new, old, journal, lambda: ['verified'])
    assert result['status'] == 'published'
    assert old.read_bytes() == b'candidate'
    assert not list(tmp_path.glob('*.delivery-backup'))


@pytest.mark.parametrize('point', ['before_backup', 'after_backup', 'after_replace', 'after_commit'])
def test_recover_each_crash_boundary(tmp_path, point):
    old, new, journal, backup = [tmp_path/n for n in ('current', 'candidate', 'journal.json', 'backup')]
    old.write_bytes(b'old'); new.write_bytes(b'new')
    record = dict(status='prepared', backup=str(backup), before_sha256=delivery.digest(old), candidate_sha256=delivery.digest(new))
    delivery.save(journal, record)
    if point != 'before_backup':
        os.link(old, backup)
    if point in ('after_replace', 'after_commit'):
        os.replace(new, old)
    if point == 'after_commit':
        delivery.save(journal, record | {'status':'published'})
    delivery.recover(old, journal)
    assert old.read_bytes() == (b'new' if point == 'after_commit' else b'old')
    assert not backup.exists()


def test_recovery_refuses_external_change(tmp_path):
    current, backup, journal = [tmp_path/n for n in ('current', 'backup', 'journal')]
    current.write_bytes(b'unknown'); backup.write_bytes(b'old')
    delivery.save(journal, dict(status='prepared', backup=str(backup), before_sha256=delivery.digest(backup), candidate_sha256='other'))
    with pytest.raises(ValueError, match='externally'):
        delivery.recover(current, journal)
    assert current.read_bytes() == b'unknown' and backup.read_bytes() == b'old'


def test_new_publication_failure_removes_untrusted_target(tmp_path):
    current, new, journal = [tmp_path/n for n in ('current', 'next', 'journal')]
    new.write_bytes(b'candidate')
    def reject():
        raise RuntimeError('HTTP check failed')
    with pytest.raises(RuntimeError):
        delivery.publish(new, current, journal, reject)
    assert not current.exists()


def test_snapshot_ignores_only_physical_metadata_and_detects_value_change(tmp_path):
    path = tmp_path/'snapshot.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE companies(ticker TEXT PRIMARY KEY,value REAL); CREATE TABLE export_universe(instrument_id INTEGER); INSERT INTO export_universe VALUES(1);')
        conn.executemany('INSERT INTO metadata VALUES(?,?)', [('contract', delivery.CONTRACT), ('exported_at','before'), ('source_database_sha256','a'), ('information_as_of','2026-09-27')])
        conn.execute("INSERT INTO companies VALUES('SZSE:300866',42)")
    before = delivery.snapshot(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE metadata SET value='after' WHERE key IN ('exported_at','source_database_sha256')")
    replay = delivery.snapshot(path)
    assert replay['content_sha256'] == before['content_sha256'] and replay['sha256'] != before['sha256']
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE companies SET value=43')
    assert delivery.snapshot(path)['content_sha256'] != before['content_sha256']


def test_check_rejects_empty_universe_duplicate_or_ready_failure():
    row = dict(ticker='SZSE:300866', admission='ready', http_status=200, final={'value_per_share':42})
    delivery.validate_check(dict(companies=1, results=[row]), ['SZSE:300866'])
    for results in ([], [row, row], [row | {'http_status':422}], [row | {'final':{'value_per_share':float('nan')}}]):
        with pytest.raises(ValueError):
            delivery.validate_check(dict(companies=len(results), results=results), ['SZSE:300866'])


def test_cycle_publishes_replays_without_export_and_keeps_last_snapshot_on_source_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    workspace = tmp_path
    database = workspace/'alphalake.duckdb'; database.write_bytes(b'source')
    binary = workspace/'alphalake'; binary.write_bytes(b'binary')
    target = workspace/'valuation.sqlite'
    calls = []
    monkeypatch.setattr(delivery, 'runtime_identity', lambda _: {'engine':'fixed'})
    monkeypatch.setattr(delivery, 'verify_web', lambda *a, **kw: ['checked'])
    monkeypatch.setattr(delivery, 'freshness_report', lambda *a: {'contract':'test-only'})
    def execute(command, log, timeout):
        calls.append(log.stem)
        log.write_text('test boundary\n')
        if log.stem == 'sync-financial':
            return 1
        if log.stem == 'export':
            path = Path(command[command.index('--output')+1])
            with sqlite3.connect(path) as conn:
                conn.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE companies(ticker TEXT PRIMARY KEY,value REAL); CREATE TABLE export_universe(instrument_id INTEGER); INSERT INTO export_universe VALUES(1);')
                conn.executemany('INSERT INTO metadata VALUES(?,?)', [('contract',delivery.CONTRACT), ('source_database_sha256',delivery.digest(database))])
                conn.execute("INSERT INTO companies VALUES('SZSE:300866',42)")
        elif log.stem.startswith('check-'):
            assert Path(command[command.index('--database')+1]).exists()
            output = Path(command[command.index('--output')+1]); output.mkdir()
            assert '--review-methodology' in command
            delivery.save(output/'summary.json', dict(companies=1, admission={'ready':1}, methodology={'economic_approval':'not_inferred'}, risk_free_rate=float(command[command.index('--risk-free-rate')+1]),
                results=[dict(ticker='SZSE:300866', admission='ready', http_status=200, final={'value_per_share':42})]))
        return 0
    monkeypatch.setattr(delivery, 'execute', execute)
    args = SimpleNamespace(database=database, alphalake=binary, output=target, code=['300866'],
        period='2026-06-30', as_of='2026-09-27T00:00:00Z', risk_free_rate=.0425, stage_timeout=10,
        source_mode='local', sync_references=False, latest=6, web_url='http://localhost')
    first = tmp_path/'first'; first.mkdir()
    assert delivery.run(args, first) == 0
    saved = target.read_bytes(); stat = target.stat()
    calls.clear()
    second = tmp_path/'second'; second.mkdir()
    assert delivery.run(args, second) == 0
    assert calls == []  # 相同源、程序、参考及请求不重导、不重算。
    assert target.read_bytes() == saved and target.stat().st_ino == stat.st_ino
    assert json.loads((second/'run.json').read_text())['calculation_reused']
    args.risk_free_rate = .04
    changed_rate = tmp_path/'changed-rate'; changed_rate.mkdir()
    assert delivery.run(args, changed_rate) == 0
    assert calls == ['check-candidate', 'check-previous']
    assert json.loads((changed_rate/'run.json').read_text())['export_reused']
    assert not json.loads((changed_rate/'run.json').read_text()).get('calculation_reused')
    assert target.read_bytes() == saved and target.stat().st_ino == stat.st_ino
    calls.clear()
    args.source_mode = 'offline'
    third = tmp_path/'third'; third.mkdir()
    assert delivery.run(args, third) == 1
    assert calls == ['sync-financial'] and target.read_bytes() == saved
    assert json.loads((third/'run.json').read_text())['requested_codes'] == ['300866']

    # 历史代码虽不同，解析到同一标准证券时仍拒绝，不能误判范围外。
    def related_source(command, log, timeout):
        if log.stem == 'sync-financial':
            log.write_text('partial with an alias of requested instrument')
            delivery.save(Path(command[command.index('--report')+1]), dict(
                contract='alphalake-financial-sync-v1', pending_complete=True, selected_packages=6,
                unresolved_selected=1, error='', package_failures=0, master_failures=0, cache_fallbacks=0,
                pending_all=[{'code':'300750'}], pending_selected=[{'code':'300750','instrument_id':1}]))
            return 1
        return execute(command, log, timeout)
    monkeypatch.setattr(delivery, 'execute', related_source)
    fourth = tmp_path/'fourth'; fourth.mkdir()
    assert delivery.run(args, fourth) == 1
    assert 'requested security identity' in json.loads((fourth/'run.json').read_text())['error']
    assert target.read_bytes() == saved


def test_partial_source_is_scoped_not_silently_completed(tmp_path):
    path = tmp_path/'source.json'
    base = dict(contract='alphalake-financial-sync-v1', pending_complete=True, selected_packages=6,
        unresolved_selected=1, error='', package_failures=0, master_failures=0,
        pending_all=[{'code':'300750','reason':'duplicate source record'}], pending_selected=[{'code':'300750','instrument_id':2}], status='partial_or_failed')
    delivery.save(path, base)
    assert delivery.accept_financial_report(path, 1, ['300866'])['status'] == 'partial_or_failed'
    for changes in ({'pending_complete':False}, {'error':'download failed'}, {'package_failures':1},
                    {'master_failures':1}, {'pending_all':[]}, {'pending_selected':[{'code':'300866','instrument_id':2}]},
                    {'pending_selected':[{'code':'300750','instrument_id':None}]},
                    {'unresolved_selected':0}):
        delivery.save(path, base | changes)
        with pytest.raises(ValueError):
            delivery.accept_financial_report(path, 1, ['300866'])


def test_comparison_distinguishes_evidence_refresh_from_new_value():
    before = {'results':[{'ticker':'SZSE:300866','final':{'value_per_share':42},'diagnostic_sha256':'old'}]}
    after = {'results':[{'ticker':'SZSE:300866','final':{'value_per_share':42},'diagnostic_sha256':'new'}]}
    assert delivery.compare_checks(before, after)[0]['changed_fields'] == ['diagnostic_sha256']


def test_freshness_keeps_quote_and_reference_dates_separate(tmp_path):
    path = tmp_path/'freshness.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE export_cells(ticker,series,field,status,evidence_json); CREATE TABLE reference_release(release_id,dataset,source_version,available_at,first_seen_at,sha256); CREATE TABLE reference_value(release_id,observation_date);')
        evidence = dict(share_period='2026-06-30', quote=dict(trade_date='2026-09-29',acquisition_started_at='2026-09-29T08:00:00Z',run_finished_at='2026-09-29T08:01:00Z'))
        conn.execute('INSERT INTO export_cells VALUES(?,?,?,?,?)',('SZSE:300866','company','mv_equity_listing','reported_share_price_proxy',json.dumps(evidence)))
        conn.execute('INSERT INTO reference_release VALUES(1,?,?,?,?,?)',('beta','2026','2026-09-01T00:00:00Z','2026-08-31T00:00:00Z','hash'))
        conn.execute("INSERT INTO reference_value VALUES(1,'2026-01-05')")
    result = delivery.freshness_report(path,'2026-09-30T10:00:00Z')
    assert result['financial']['status']=='not_checked_this_run'
    assert result['quotes'][0]['recency']=='earlier_within_contract'
    assert result['quotes'][0]['calendar_age_days']==1
    assert result['references']['releases'][0]['observation_dates']==['2026-01-05']
    assert result['references']['releases'][0]['first_seen_at']=='2026-08-31T00:00:00Z'
    assert not result['references']['refresh_requested']
    with sqlite3.connect(path) as conn:
        evidence['quote']['trade_date']='2026-09-01'
        conn.execute('UPDATE export_cells SET evidence_json=?',(json.dumps(evidence),))
    with pytest.raises(ValueError,match='14-day'):
        delivery.freshness_report(path,'2026-09-30T10:00:00Z')


@pytest.mark.parametrize('filing_exit', [0, 1, 124])
def test_online_sync_uses_reviewed_symbols_and_optional_filings(tmp_path, monkeypatch, filing_exit):
    from types import SimpleNamespace
    target = tmp_path/'published.sqlite'; target.write_bytes(b'reviewed')
    monkeypatch.setattr(delivery, 'snapshot', lambda _: {'tickers':['SHSE:600519','SZSE:300866']})
    calls = []
    def execute(command, log, timeout):
        calls.append(command)
        if log.stem == 'sync-financial':
            delivery.save(Path(command[command.index('--report')+1]), dict(
                contract='alphalake-financial-sync-v1', status='completed', pending_complete=True,
                selected_packages=6, unresolved_selected=0, pending_all=[], pending_selected=[],
                error='', package_failures=0, master_failures=0, cache_fallbacks=0))
        if log.stem == 'sync-filings':
            return filing_exit
        return 1 if log.stem == 'materialize' else 0
    monkeypatch.setattr(delivery, 'execute', execute)
    args = SimpleNamespace(database=tmp_path/'alphalake.duckdb', alphalake=tmp_path/'alphalake',
        output=target, code=['600519','300866'], period='2026-06-30', risk_free_rate=.0425,
        source_mode='online', sync_references=False, as_of='2026-09-30T05:00:00Z', latest=6, stage_timeout=10, filings_start='2026-09-01', filings_end='2026-09-27')
    assert delivery.run(args, tmp_path) == 1
    assert [c[1] for c in calls] == ['sync-financial','sync-filings','sync-valuation-quotes','materialize-fundamentals']
    assert json.loads((tmp_path/'run.json').read_text())['filing_metadata'] == dict(required_for_current_values=False, exit_code=filing_exit)
    assert calls[1][3:] == ['--start','2026-09-01','--end','2026-09-27','--metadata-only','--codes-file',str(tmp_path/'codes.txt')]
    assert calls[2][3:] == ['--symbols','sz300866,sh600519','--date', delivery.completed_market_date(datetime.fromisoformat(args.as_of)).isoformat()]
    assert target.read_bytes() == b'reviewed'
