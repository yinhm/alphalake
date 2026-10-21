"""真实文件切换与故障恢复；不以此冒充100家真实财务验收。"""
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3

import pytest
from tools import publish_native_valuation as delivery


@pytest.mark.parametrize('mismatch', [None, 'final', 'cashflow'])
def test_live_check_releases_own_session_even_when_value_differs(monkeypatch, mismatch):
    import io
    diagnostic = {'status':'ready'}
    result = dict(id='verification', inputs={}, unresolved_fields=[], final={'value_per_share':42},cashflow={'fcff':[1,2]})
    calls = []
    def respond(request, timeout):
        calls.append((request.get_method(), request.full_url))
        method = request.get_method()
        body = diagnostic if method=='GET' else result if method=='POST' else None
        stream = io.BytesIO(json.dumps(body).encode())
        stream.status = 204 if method=='DELETE' else 200
        return stream
    monkeypatch.setattr(delivery,'urlopen',respond)
    row = dict(ticker='SZSE:300866',admission='ready',http_status=200,
        diagnostic_sha256=delivery.value_digest(diagnostic),inputs_sha256=delivery.value_digest({}),
        unresolved_sha256=delivery.value_digest([]),final={'value_per_share':43 if mismatch=='final' else 42},
        economic_report_sha256=delivery.value_digest(delivery.economic_payload(result)))
    if mismatch=='cashflow':result['cashflow']['fcff'][0]=100
    summary = dict(risk_free_rate=None,results=[row])
    if mismatch:
        with pytest.raises(ValueError,match='live valuation differs'):
            delivery.verify_web('http://localhost',summary)
    else:
        assert delivery.verify_web('http://localhost',summary)==[dict(ticker='SZSE:300866',http_status=200)]
    assert [m for m,u in calls] == ['GET','POST','DELETE']
    assert calls[-1][1] == 'http://localhost/api/valuation/verification'


def test_publish_failure_rollback_and_success(tmp_path):
    state = tmp_path/'derived/system-delivery'; state.mkdir(parents=True)
    old, new, journal = tmp_path/'current.sqlite', state/'next.sqlite', state/'publication.json'
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
    assert set(tmp_path.iterdir()) == {state.parent, old}
    assert not list(state.glob('*.delivery-backup'))


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
        conn.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE companies(ticker TEXT PRIMARY KEY,value REAL);')
        conn.executemany('INSERT INTO metadata VALUES(?,?)', [('contract', delivery.CONTRACT), ('exported_at','before'), ('source_database_sha256','a'), ('information_as_of','2026-09-27')])
        conn.execute("INSERT INTO companies VALUES('SZSE:300866',42)")
        conn.execute('INSERT INTO metadata VALUES(?,?)',('input_context:SZSE:300866',json.dumps(dict(ticker='SZSE:300866',instrument_id=1))))
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
    monkeypatch.setattr(delivery, 'runtime_identity', lambda _: {'stages':dict(export='fixed',review='fixed',delivery='fixed')})
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
                conn.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE companies(ticker TEXT PRIMARY KEY,value REAL);')
                conn.executemany('INSERT INTO metadata VALUES(?,?)', [('contract',delivery.CONTRACT), ('source_database_sha256',delivery.digest(database))])
                conn.execute("INSERT INTO companies VALUES('SZSE:300866',42)")
                conn.execute('INSERT INTO metadata VALUES(?,?)',('input_context:SZSE:300866',json.dumps(dict(ticker='SZSE:300866',instrument_id=1))))
        elif log.stem.startswith('check-'):
            assert Path(command[command.index('--database')+1]).exists()
            output = Path(command[command.index('--output')+1]); output.mkdir()
            assert '--review-methodology' in command
            delivery.save(output/'summary.json', dict(companies=1, admission={'ready':1}, methodology={'economic_approval':'not_inferred'}, risk_free_rate=float(command[command.index('--risk-free-rate')+1]) if '--risk-free-rate' in command else None,
                results=[dict(ticker='SZSE:300866', admission='ready', http_status=200, final={'value_per_share':42})]))
        return 0
    monkeypatch.setattr(delivery, 'execute', execute)
    args = SimpleNamespace(database=database, alphalake=binary, output=target, code=['300866'], scope_file=None,
        period='2026-06-30', as_of='2026-09-27T00:00:00Z', risk_free_rate=.0425, stage_timeout=10,
        source_mode='local', sync_references=False, latest=6, web_url='http://localhost')
    first = tmp_path/'first'; first.mkdir()
    assert delivery.run(args, first) == 0
    state = workspace/'derived/system-delivery'
    assert (state/'valuation.sqlite.delivery.json').exists()
    assert (state/'valuation.sqlite.publication.json').exists()
    assert not list(workspace.glob('*.json')) and not list(workspace.glob('*.next'))
    saved = target.read_bytes(); stat = target.stat()
    calls.clear()
    second = tmp_path/'second'; second.mkdir()
    assert delivery.run(args, second) == 0
    assert calls == []  # 相同源、程序、参考及请求不重导、不重算。
    assert target.read_bytes() == saved and target.stat().st_ino == stat.st_ino
    assert json.loads((second/'run.json').read_text())['calculation_reused']
    receipt = state/'valuation.sqlite.delivery.json'
    old_receipt = json.loads(receipt.read_text()); old_receipt.pop('contract')
    delivery.save(receipt,old_receipt)
    migration = tmp_path/'receipt-migration'; migration.mkdir()
    assert delivery.run(args,migration) == 0
    assert calls == ['export','check-candidate','check-previous']
    assert json.loads((migration/'previous-delivery-receipt.json').read_text()) == old_receipt
    assert json.loads(receipt.read_text())['contract'] == 'alphalake-native-delivery-v2'
    assert target.read_bytes() == saved
    calls.clear()
    args.risk_free_rate = .04
    changed_rate = tmp_path/'changed-rate'; changed_rate.mkdir()
    assert delivery.run(args, changed_rate) == 0
    assert calls == ['check-candidate', 'check-previous']
    assert json.loads((changed_rate/'run.json').read_text())['export_reused']
    assert not json.loads((changed_rate/'run.json').read_text()).get('calculation_reused')
    assert target.read_bytes() == saved and target.stat().st_ino == stat.st_ino
    calls.clear()
    args.risk_free_rate=None
    published_initial = tmp_path/'published-initial'; published_initial.mkdir()
    assert delivery.run(args,published_initial)==0
    assert calls==['check-candidate','check-previous']
    assert json.loads((published_initial/'run.json').read_text())['risk_free_rate']['basis']=='published_native_initial_condition'
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

    # 仅消费签名变化仍须切换元数据，但经济内容相同就不重复网页计算。
    def metadata_export(command,log,timeout):
        result = execute(command,log,timeout)
        if log.stem == 'export':
            with sqlite3.connect(command[command.index('--output')+1]) as conn:
                conn.execute('INSERT INTO metadata VALUES(?,?)',('source_input_signature','new-signature'))
        return result
    checked_modes=[]
    monkeypatch.setattr(delivery,'execute',metadata_export)
    monkeypatch.setattr(delivery,'runtime_identity',lambda _: {'stages':dict(export='changed',review='fixed',delivery='changed')})
    monkeypatch.setattr(delivery,'verify_web',lambda *a,**kw: checked_modes.append(kw['calculate']) or ['checked'])
    args.source_mode='local'
    metadata_only=tmp_path/'metadata-only';metadata_only.mkdir()
    assert delivery.run(args,metadata_only)==0
    result=json.loads((metadata_only/'run.json').read_text())
    assert result['status']=='published' and result['calculation_reused']
    assert checked_modes==[False]


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
    assert delivery.compare_checks(before, after)[0]['before'] == {'diagnostic_sha256':'old'}
    assert delivery.compare_checks(before, after)[0]['after'] == {'diagnostic_sha256':'new'}
    after['results'][0]['unknown'] = None
    assert 'unknown' in delivery.compare_checks(before, after)[0]['changed_fields']


@pytest.mark.full
def test_freshness_keeps_quote_and_reference_dates_separate(tmp_path, reference_export):
    path = tmp_path/'freshness.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE metadata(key TEXT,value TEXT); CREATE TABLE reference_release(release_id,dataset,source_version,available_at,first_seen_at,sha256); CREATE TABLE reference_value(release_id,observation_date);')
        evidence = dict(share_period='2026-06-30', quote=dict(trade_date='2026-09-29',acquisition_started_at='2026-09-29T08:00:00Z',run_finished_at='2026-09-29T08:01:00Z'))
        evidence['status']='reported_share_price_proxy'
        conn.execute('INSERT INTO metadata VALUES(?,?)',('input_context:SZSE:300866',json.dumps(dict(ticker='SZSE:300866',market=evidence))))
        conn.execute('INSERT INTO reference_release VALUES(1,?,?,?,?,?)',('beta','2026','2026-09-01T00:00:00Z','2026-08-31T00:00:00Z','hash'))
        conn.execute("INSERT INTO reference_value VALUES(1,'2026-01-05')")
        conn.execute('CREATE TABLE reference_wacc(singleton INTEGER PRIMARY KEY,packet_json TEXT NOT NULL)')
        conn.execute('INSERT INTO reference_wacc VALUES(1,?)',(json.dumps(reference_export),))
    result = delivery.freshness_report(path,'2026-09-30T10:00:00Z')
    assert result['financial']['status']=='not_checked_this_run'
    assert result['quotes'][0]['recency']=='earlier_within_contract'
    assert result['quotes'][0]['calendar_age_days']==1
    assert result['references']['releases'][0]['observation_dates']==['2026-01-05']
    assert result['references']['releases'][0]['first_seen_at']=='2026-08-31T00:00:00Z'
    assert not result['references']['refresh_requested']
    curve = next(r for r in result['references']['wacc_releases'] if r['source']=='chinabond')
    assert curve['observation_dates']==['2026-09-09']
    assert curve['observation_calendar_ages_days']==[21]
    with sqlite3.connect(path) as conn:
        evidence['quote']['trade_date']='2026-09-01'
        conn.execute('UPDATE metadata SET value=?',(json.dumps(dict(ticker='SZSE:300866',market=evidence)),))
    with pytest.raises(ValueError,match='14-day'):
        delivery.freshness_report(path,'2026-09-30T10:00:00Z')


@pytest.mark.parametrize('filing_exit', [None, 0, 1, 124])
def test_online_sync_uses_reviewed_symbols_and_optional_filings(tmp_path, monkeypatch, filing_exit):
    from types import SimpleNamespace
    target = tmp_path/'published.sqlite'
    with sqlite3.connect(target) as db:
        db.execute('CREATE TABLE companies(ticker TEXT)')
        db.executemany('INSERT INTO companies VALUES(?)',[('SHSE:600519',),('SZSE:300866',)])
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
        if log.stem == 'sync-quotes':
            delivery.save(Path(command[command.index('--report')+1]), dict(
                contract='alphalake-quote-sync-v1', date='2026-09-29', symbols=['sz300866','sh600519'],run_status='completed',
                result=dict(run_id=1,companies=2,observations_written=20,failures=[],complete=True)))
        return 1 if log.stem == 'materialize' else 0
    monkeypatch.setattr(delivery, 'execute', execute)
    args = SimpleNamespace(database=tmp_path/'alphalake.duckdb', alphalake=tmp_path/'alphalake',
        output=target, code=['600519','300866'], scope_file=None, period='2026-06-30', risk_free_rate=.0425,
        source_mode='online', sync_references=False, as_of='2026-09-30T05:00:00Z', quote_date='2026-09-29', latest=6, stage_timeout=10, filings_start='2026-09-01' if filing_exit is not None else None, filings_end='2026-09-27')
    assert delivery.run(args, tmp_path) == 1
    assert [c[1] for c in calls] == ['sync-financial', *(['sync-filings'] if filing_exit is not None else []), 'sync-valuation-quotes','materialize-fundamentals']
    record=json.loads((tmp_path/'run.json').read_text())
    assert record['filing_metadata'] == dict(required_for_current_values=False, **({'exit_code':filing_exit} if filing_exit is not None else {'status':'not_requested'}))
    if filing_exit is not None:
        assert calls[1][3:] == ['--start','2026-09-01','--end','2026-09-27','--metadata-only','--codes-file',str(tmp_path/'codes.txt')]
    assert calls[-2][3:] == ['--symbols','sz300866,sh600519','--date','2026-09-29','--report',str(tmp_path/'quote-source.json')]
    assert record['quote_source']['status']=='completed'


def test_partial_quote_receipt_keeps_failures_and_rejects_incomplete_or_all_failed(tmp_path):
    symbols=['sz300866','sh600519'];path=tmp_path/'quotes.json'
    result=dict(run_id=1,companies=2,observations_written=10,failures=['sh600519: timeout'],complete=True)
    receipt=dict(contract='alphalake-quote-sync-v1',date='2026-09-30',symbols=symbols,run_status='partial',result=result)
    delivery.save(path,receipt)
    accepted=delivery.accept_quote_report(path,1,symbols,'2026-09-30')
    assert accepted['status']=='partial' and accepted['result']['failures']==result['failures']
    for change in ({'complete':False},{'companies':1},{'observations_written':-1},
                   {'failures':['sz000001: timeout']},{'failures':result['failures']*2},
                   {'failures':['sz300866: timeout','sh600519: timeout']}):
        delivery.save(path,receipt|{'result':result|change})
        with pytest.raises(ValueError):delivery.accept_quote_report(path,1,symbols,'2026-09-30')
    delivery.save(path,receipt)
    for exit_code, expected_date in [(0,'2026-09-30'),(124,'2026-09-30'),(1,'2026-09-29')]:
        with pytest.raises(ValueError):delivery.accept_quote_report(path,exit_code,symbols,expected_date)
    delivery.save(path,receipt|{'run_status':'running'})
    with pytest.raises(ValueError):delivery.accept_quote_report(path,1,symbols,'2026-09-30')


def test_native_response_archive_preserves_bytes_and_retains_original_on_tampering(tmp_path, monkeypatch):
    directory=tmp_path/'candidate-check';directory.mkdir()
    delivery.save(directory/'summary.json',{'companies':1})
    response=directory/'SZSE_300866.json';response.write_bytes(b'{"value":42}\n')
    expected=delivery.digest(response)
    record=delivery.archive_check(directory)
    assert record['files'][response.name]==expected and not response.exists()
    assert (directory/'summary.json').exists()
    assert json.loads((tmp_path/'candidate-check.archive.json').read_text())==record
    directory=tmp_path/'previous-check';directory.mkdir()
    delivery.save(directory/'summary.json',{'companies':1})
    response=directory/'SZSE_300866.json';response.write_bytes(b'{"value":42}\n')
    original_add=delivery.tarfile.TarFile.add
    def tamper(stream,path,**kwargs):
        if Path(path)==response: response.write_bytes(b'{"value":43}\n')
        return original_add(stream,path,**kwargs)
    monkeypatch.setattr(delivery.tarfile.TarFile,'add',tamper)
    with pytest.raises(ValueError,match='archive differs'):
        delivery.archive_check(directory)
    assert response.exists() and not (tmp_path/'previous-check.archive.json').exists()


def test_published_scope_uses_current_snapshot_instead_of_stale_sample_file(tmp_path, monkeypatch):
    workspace=tmp_path;monkeypatch.setenv('ALPHALAKE_WORKSPACE',str(workspace))
    database=workspace/'alphalake.duckdb';database.write_bytes(b'source')
    binary=workspace/'alphalake';binary.write_bytes(b'binary')
    target=workspace/'valuation.sqlite'
    with sqlite3.connect(target) as db:
        db.executescript('CREATE TABLE metadata(key TEXT,value TEXT);CREATE TABLE companies(ticker TEXT,company_name TEXT);')
        db.execute('INSERT INTO metadata VALUES(?,?)',('contract',delivery.CONTRACT))
        for i,ticker in enumerate(['SZSE:300866','SHSE:600519','SHSE:600000'],1):
            db.execute('INSERT INTO companies VALUES(?,?)',(ticker,'sample'))
            db.execute('INSERT INTO metadata VALUES(?,?)',('input_context:'+ticker,json.dumps(dict(ticker=ticker,instrument_id=i))))
    state=workspace/'derived/system-delivery';state.mkdir(parents=True)
    (state/'published-codes.txt').write_text('300866\n')
    monkeypatch.setattr(delivery.sys,'argv',['publish','--database',str(database),'--output',str(target),
        '--alphalake',str(binary),'--period','2026-06-30','--source-mode','local','--published-scope','--web-url','http://localhost'])
    def run(args,root):
        assert args.code==['600000','600519','300866']  # 金融／缺项仍保留在原分母。
        assert json.loads(args.scope_file.read_text()) == [
            dict(instrument_id=i,ticker=t,name='sample') for i,t in [(3,'SHSE:600000'),(2,'SHSE:600519'),(1,'SZSE:300866')]]
        return 0
    monkeypatch.setattr(delivery,'run',run)
    assert delivery.main()==0
    with sqlite3.connect(target) as db: db.execute("UPDATE companies SET ticker='BSE:920001' WHERE ticker='SHSE:600000'")
    with pytest.raises(SystemExit):delivery.main()


def test_frozen_scope_rejects_unknown_and_repeated_canonical_ids():
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE metadata(key TEXT,value TEXT);CREATE TABLE companies(ticker TEXT,company_name TEXT);')
        db.execute("INSERT INTO companies VALUES('SZSE:300866','sample')")
        with pytest.raises(ValueError,match='canonical security identity'):delivery.publication_scope(db)
        db.execute('INSERT INTO metadata VALUES(?,?)',('input_context:SZSE:300866',json.dumps(dict(ticker='SZSE:300866',instrument_id=1))))
        db.execute("INSERT INTO companies VALUES('SHSE:600519','another')")
        db.execute('INSERT INTO metadata VALUES(?,?)',('input_context:SHSE:600519',json.dumps(dict(ticker='SHSE:600519',instrument_id=1))))
        with pytest.raises(ValueError,match='unique'):delivery.publication_scope(db)


def test_runtime_identity_excludes_tests_but_tracks_execution_code(tmp_path, monkeypatch):
    backend = tmp_path/'backend'
    for name in ('tools/publish_native_valuation.py','tools/export_alphalake_sqlite.py',
                 'tools/review_native_policy.py','tools/research_only.py','engine/dcf.py','tests/test_dcf.py'):
        path=backend/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original')
    monkeypatch.setattr(delivery, '__file__', str(backend/'tools/publish_native_valuation.py'))
    before=delivery.runtime_identity(tmp_path)
    (backend/'tests/test_dcf.py').write_text('new test only')
    assert delivery.runtime_identity(tmp_path)==before
    (backend/'tools/research_only.py').write_text('changed unrelated research')
    research = delivery.runtime_identity(tmp_path)
    assert research != before and research['stages'] == before['stages']
    (backend/'tools/export_alphalake_sqlite.py').write_text('changed conversion')
    export = delivery.runtime_identity(tmp_path)
    assert export['stages']['export'] != research['stages']['export']
    assert export['stages']['review'] == research['stages']['review']
    (backend/'tools/review_native_policy.py').write_text('changed review')
    review = delivery.runtime_identity(tmp_path)
    assert review['stages']['review'] != export['stages']['review']
    assert review['stages']['export'] == export['stages']['export']
    (backend/'engine/dcf.py').write_text('changed calculation')
    assert delivery.runtime_identity(tmp_path)['stages']['review'] != review['stages']['review']
