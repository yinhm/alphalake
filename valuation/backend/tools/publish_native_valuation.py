"""更新标准财务并验收、原子发布原生网页SQLite；所有运行记录写入workspace。"""
import argparse
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from tools.export_alphalake_sqlite import CONTRACT, digest, validate_dates
from tools.check_native_sqlite import value_digest
from tools.refresh_valuate_alphalake import execute, reference_commands, timestamp


def save(path, value):
    pending = path.with_suffix(path.suffix + '.tmp')
    with pending.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(pending, path)
    sync_directory(path.parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(path):
    """稳定逐表内容签名；不将生成时间/主库物理哈希当作业务变化。"""
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal', '-journal')):
        raise ValueError('snapshot has a write journal: '+str(path))
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('invalid SQLite snapshot')
        metadata = dict(conn.execute('SELECT key,value FROM metadata'))
        if metadata.get('contract') != CONTRACT:
            raise ValueError('current SQLite contract required')
        tickers = [r[0] for r in conn.execute('SELECT ticker FROM companies ORDER BY ticker')]
        if not tickers or any(not t.startswith(('SHSE:', 'SZSE:')) for t in tickers):
            raise ValueError('nonempty SH/SZ universe required')
        tables = {}
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for name in names:
            if name == 'metadata':
                continue
            quoted = '"'+name.replace('"','""')+'"'
            columns = len(conn.execute('PRAGMA table_info('+quoted+')').fetchall())
            order = ','.join(str(i+1) for i in range(columns))
            h = hashlib.sha256()
            for row in conn.execute('SELECT * FROM '+quoted+' ORDER BY '+order):
                h.update(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()+b'\n')
            tables[name] = h.hexdigest()
        semantic_metadata = {k:v for k,v in metadata.items() if k not in (
            'exported_at', 'source_database_sha256', 'exporter_sha256', 'alphalake_binary_sha256')}
        signature = hashlib.sha256(json.dumps([tables, semantic_metadata], sort_keys=True).encode()).hexdigest()
        identities = [r[0] for r in conn.execute('SELECT DISTINCT instrument_id FROM export_universe ORDER BY instrument_id')]
        return dict(sha256=digest(path), content_sha256=signature, tables=tables, metadata=metadata, tickers=tickers, instrument_ids=identities)


def validate_check(summary, expected):
    rows = summary['results']
    if sorted(r['ticker'] for r in rows) != expected or summary['companies'] != len(expected):
        raise ValueError('native API check universe mismatch')
    for row in rows:
        if row['admission'] == 'ready':
            if row['http_status'] != 200 or not isinstance(row.get('final'), dict):
                raise ValueError('ready company failed calculation: '+row['ticker'])
            value = row['final'].get('value_per_share')
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('nonfinite/missing valuation: '+row['ticker'])
        elif row['admission'] not in ('blocked_required_inputs','blocked_reference_inputs') or row['http_status'] != 422:
            raise ValueError('unexpected native rejection: '+row['ticker'])


def compare_checks(before, after):
    old = {r['ticker']:r for r in before['results']} if before else {}
    return [dict(ticker=r['ticker'], changed_fields=[k for k,v in r.items() if old.get(r['ticker'], {}).get(k) != v], before=old.get(r['ticker']), after=r)
            for r in after['results'] if old.get(r['ticker']) != r]


def verify_web(url, summary, calculate=True):
    """确认真实服务加载候选数据；HTTP422也是保留分母的一种结果。"""
    def request(path, body=None):
        req = Request(url.rstrip('/')+path,
                      data=json.dumps(body).encode() if body else None,
                      headers={'Content-Type':'application/json'})
        try:
            with urlopen(req, timeout=60) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)
    checked = []
    for row in summary['results']:
        ticker = row['ticker']
        status, diagnostic = request('/api/database/compatibility/'+ticker)
        if status != 200 or diagnostic.get('status') != row['admission'] or value_digest(diagnostic) != row['diagnostic_sha256']:
            raise ValueError('live admission differs: '+ticker)
        if not calculate:
            checked.append(dict(ticker=ticker, admission=row['admission'], calculation='reused_verified_inputs'))
            continue
        status, result = request('/api/valuation/from-database', dict(ticker=ticker, risk_free_rate=summary['risk_free_rate']))
        if status != row['http_status'] or (status == 200 and (result.get('final') != row['final'] or value_digest(result.get('inputs')) != row['inputs_sha256'] or value_digest(result.get('unresolved_fields')) != row['unresolved_sha256'])):
            raise ValueError('live valuation differs: '+ticker)
        checked.append(dict(ticker=ticker, http_status=status))
    return checked


def recover(target, journal):
    """未提交的切换一律回滚；外部修改不猜测、不覆盖。"""
    if not journal.exists():
        return None
    record = json.loads(journal.read_text())
    backup = Path(record['backup'])
    actual = digest(target) if target.exists() else None
    if record['status'] != 'prepared':
        if backup.exists():
            if record['status'] != 'published' or actual != record['candidate_sha256'] or digest(backup) != record['before_sha256']:
                raise ValueError('unrecognized leftover publication backup')
            backup.unlink(); sync_directory(target.parent)
        return None
    if actual not in (record['before_sha256'], record['candidate_sha256']):
        raise ValueError('publication interrupted and target changed externally')
    if actual == record['before_sha256']:
        if backup.exists():
            if digest(backup) != record['before_sha256']:
                raise ValueError('rollback backup was changed externally')
            backup.unlink()
    elif record['before_sha256'] is None:
        target.unlink(missing_ok=True)
    else:
        if not backup.exists() or digest(backup) != record['before_sha256']:
            raise ValueError('publication rollback evidence missing/corrupt')
        os.replace(backup, target)
    sync_directory(target.parent)
    record.update(status='rolled_back', recovered_at=timestamp())
    save(journal, record)
    return record


def publish(candidate, target, journal, verify):
    before = digest(target) if target.exists() else None
    backup = target.with_name(target.name+'.delivery-backup')
    if backup.exists():
        raise ValueError('unresolved previous backup: '+str(backup))
    record = dict(status='prepared', target=str(target), backup=str(backup),
                  before_sha256=before, candidate_sha256=digest(candidate), prepared_at=timestamp())
    save(journal, record)
    try:
        if target.exists():
            # 同一文件系统硬链接保留旧快照，不复制整库。
            os.link(target, backup)
            sync_directory(target.parent)
        os.replace(candidate, target)
        sync_directory(target.parent)
        record['web_checks'] = verify()
        record.update(status='published', published_at=timestamp())
        save(journal, record)
    except BaseException:
        recover(target, journal)
        raise
    backup.unlink(missing_ok=True)
    sync_directory(target.parent)
    return record


def runtime_identity(workspace):
    """原生计算绑定SQLite参考快照及后端/冻结模型参考版本。"""
    from api.alphalake import runtime_versions
    backend = Path(__file__).resolve().parents[1]
    files = sorted(backend.rglob('*.py')) + sorted((backend/'data_sources').glob('*.json'))
    hashes = {str(p):digest(p) for p in files}
    return dict(versions=runtime_versions(), files=hashes)


def accept_financial_report(path, exit_code, codes):
    report = json.loads(path.read_text())
    if report['contract'] != 'alphalake-financial-sync-v1' or report['pending_complete'] is not True:
        raise ValueError('incomplete financial source receipt')
    if exit_code not in (0, 1) or report['error'] or report['package_failures'] or report['master_failures'] or report['selected_packages'] < 1:
        raise ValueError('financial source failed; published snapshot retained')
    pending = report['pending_all']
    if len(pending) < report['unresolved_selected'] or (exit_code and report['unresolved_selected'] < 1):
        raise ValueError('financial exit status not explained by pending evidence')
    selected = report['pending_selected']
    if len(selected) != report['unresolved_selected'] or any(not isinstance(r['instrument_id'], int) or r['instrument_id'] <= 0 for r in selected):
        raise ValueError('pending source identity not fully resolved for scope check')
    affected = sorted({row['code'] for row in selected} & set(codes))
    if affected:
        raise ValueError('requested companies have unresolved source records: '+','.join(affected))
    return report


def run(args, root):
    ledger = dict(status='running', started_at=timestamp(), stages=[], database=str(args.database),
                  output=str(args.output), report_period=args.period, source_mode=args.source_mode,
                  freshness='local_evidence_only_not_upstream_verified' if args.source_mode != 'online' else 'see_source_logs_and_cache_fallbacks',
                  risk_free_rate=dict(value=args.risk_free_rate, basis='explicit_acceptance_assumption_not_observed_rate'))
    candidate = args.output.with_name(args.output.name+'.next')
    journal = args.output.with_name(args.output.name+'.publication.json')
    started = time.monotonic()
    workspace = args.database.parent
    receipt = args.output.with_name(args.output.name+'.delivery.json')

    def stage(name, command, allow_partial=False):
        entry = dict(name=name, command=command, started_at=timestamp(), log=str(root/(name+'.log')))
        ledger['stages'].append(entry); save(root/'run.json', ledger)
        code = execute(command, Path(entry['log']), args.stage_timeout)
        entry.update(exit_code=code, finished_at=timestamp())
        save(root/'run.json', ledger)
        if code and not allow_partial:
            raise ValueError(name+' failed; see '+entry['log'])
        return code

    try:
        ledger['recovery'] = recover(args.output, journal)
        if candidate.exists():
            ledger['discarded_candidate'] = dict(path=str(candidate), sha256=digest(candidate), reason='unpublished_candidate_rebuild')
            candidate.unlink()
        codes = sorted(set(args.code))
        if args.source_mode != 'local':
            extra = ['--offline'] if args.source_mode == 'offline' else []
            report = root/'financial-source.json'
            code = stage('sync-financial', [str(args.alphalake), 'sync-financial', str(args.database), '--latest', str(args.latest), '--report', str(report), *extra], allow_partial=True)
            ledger['financial_source'] = accept_financial_report(report, code, codes)
            ledger['freshness'] = ('local_or_fallback_not_upstream_verified' if args.source_mode == 'offline' or ledger['financial_source']['cache_fallbacks'] else 'selected_financial_packages_checked_against_upstream_manifest')
            if args.source_mode == 'online':
                (root/'codes.txt').write_text('\n'.join(codes)+'\n')
                stage('sync-filings', [str(args.alphalake), 'sync-filings', str(args.database),
                    '--start', args.filings_start, '--end', args.filings_end, '--metadata-only', '--codes-file', str(root/'codes.txt')])
                # 沿用现有证券身份，不按代码前缀猜交易所。
                if not args.output.exists():
                    raise ValueError('online quote sync needs an existing reviewed SH/SZ snapshot; bootstrap with local mode')
                old = snapshot(args.output)
                by_code = {t.split(':')[1]:t for t in old['tickers']}
                if any(c not in by_code for c in codes):
                    raise ValueError('quote identities missing from published snapshot')
                symbols = [('sh' if by_code[c].startswith('SHSE:') else 'sz')+c for c in codes]
                stage('sync-quotes', [str(args.alphalake), 'sync-valuation-quotes', str(args.database), '--symbols', ','.join(symbols), '--period', str(args.period)])
            stage('materialize', [str(args.alphalake), 'materialize-fundamentals', str(args.database)])
        if args.sync_references:
            if args.source_mode == 'local':
                raise ValueError('local mode does not synchronize references')
            for name, command in reference_commands(args.alphalake, args.database, args.source_mode == 'offline'):
                stage(name, command)
        asof = args.as_of or timestamp()
        ledger['information_as_of'] = asof
        identity = runtime_identity(workspace)
        save(root/'runtime-inputs.json', identity)
        previous_receipt = json.loads(receipt.read_text()) if receipt.exists() else None
        validate_dates(date.fromisoformat(args.period), datetime.fromisoformat(asof))
        command = [sys.executable, '-m', 'tools.export_alphalake_sqlite', '--database', str(args.database),
            '--output', str(candidate), '--period', args.period, '--as-of', asof, '--alphalake', str(args.alphalake)]
        for code in codes:
            command += ['--code', code]
        request_identity = dict(source_sha256=digest(args.database), binary_sha256=digest(args.alphalake),
                                codes=codes, period=args.period, as_of=asof)
        export_reused = (previous_receipt is not None and args.output.exists()
                         and previous_receipt['request_identity'] == request_identity
                         and previous_receipt['runtime_identity'] == identity
                         and previous_receipt['published_sha256'] == digest(args.output))
        if export_reused:
            current = snapshot(args.output)
            ledger['export_reused'] = True
        else:
            stage('export', command)
            current = snapshot(candidate)
        if sorted(t.split(':')[1] for t in current['tickers']) != codes:
            raise ValueError('exported universe differs from requested codes')
        if ledger.get('financial_source'):
            pending_ids = {r['instrument_id'] for r in ledger['financial_source']['pending_selected']}
            if pending_ids.intersection(current['instrument_ids']):
                raise ValueError('pending source record resolves to a requested security identity')
        previous = snapshot(args.output) if args.output.exists() else None
        ledger['candidate'] = current
        ledger['previous'] = previous
        reuse = (previous_receipt is not None
                 and previous_receipt['content_sha256'] == current['content_sha256']
                 and previous_receipt['runtime_identity'] == identity
                 and previous_receipt['risk_free_rate'] == args.risk_free_rate)
        if reuse:
            checked = previous_receipt['check']
            ledger['calculation_reused'] = True
        else:
            stage('check-candidate', [sys.executable, '-m', 'tools.check_native_sqlite', '--database', str(candidate),
                  '--output', str(root/'candidate-check'), '--risk-free-rate', str(args.risk_free_rate)])
            checked = json.loads((root/'candidate-check/summary.json').read_text())
        validate_check(checked, current['tickers'])
        before = None
        if previous:
            if previous_receipt and previous_receipt['published_sha256'] == previous['sha256'] and previous_receipt['runtime_identity'] == identity and previous_receipt['risk_free_rate'] == args.risk_free_rate:
                before = previous_receipt['check']
            else:
                stage('check-previous', [sys.executable, '-m', 'tools.check_native_sqlite', '--database', str(args.output),
                    '--output', str(root/'previous-check'), '--risk-free-rate', str(args.risk_free_rate)])
                before = json.loads((root/'previous-check/summary.json').read_text())
            validate_check(before, previous['tickers'])
        save(root/'comparison.json', dict(changed_companies=compare_checks(before, checked),
            removed_tickers=sorted(set(previous['tickers'])-set(current['tickers'])) if previous else [],
            changed_tables=[k for k,v in current['tables'].items() if not previous or previous['tables'].get(k)!=v]))
        if runtime_identity(workspace) != identity:
            raise ValueError('runtime/reference inputs changed during validation')
        if digest(args.database) != request_identity['source_sha256']:
            raise ValueError('source database changed after export')
        if (digest(args.output) if args.output.exists() else None) != (previous['sha256'] if previous else None):
            raise ValueError('published snapshot changed during validation')
        if previous and previous['content_sha256'] == current['content_sha256']:
            ledger['web_checks'] = verify_web(args.web_url, before, calculate=not reuse)
            checked = before
            ledger['status'] = 'unchanged'
            candidate.unlink(missing_ok=True)
        else:
            ledger['publication'] = publish(candidate, args.output, journal, lambda: verify_web(args.web_url, checked))
            ledger['status'] = 'published'
        ledger['admission'] = checked['admission']
        ledger['published_sha256'] = digest(args.output)
        save(receipt, dict(published_sha256=ledger['published_sha256'], content_sha256=current['content_sha256'],
            runtime_identity=identity, request_identity=request_identity, risk_free_rate=args.risk_free_rate, check=checked, report=str(root/'run.json')))
    except (Exception, KeyboardInterrupt) as error:
        ledger.update(status='failed', error=str(error), error_type=type(error).__name__)
    finally:
        ledger.update(finished_at=timestamp(), elapsed_seconds=time.monotonic()-started)
        save(root/'run.json', ledger)
    print(json.dumps(dict(status=ledger['status'], source_status=ledger.get('financial_source', {}).get('status', 'not_synchronized'), freshness=ledger.get('freshness', 'existing_local_standard_facts'), admission=ledger.get('admission'), error=ledger.get('error'), report=str(root/'run.json')), ensure_ascii=False), flush=True)
    return 0 if ledger['status'] in ('published', 'unchanged') else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--period', required=True)
    parser.add_argument('--as-of')
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--code', action='append')
    scope.add_argument('--codes-file', type=Path, help='每行一个六位证券代码；明确固定验收分母')
    parser.add_argument('--source-mode', choices=['online', 'offline', 'local'], required=True,
                        help='online刷新源；offline重放财务缓存；local仅发布既有标准事实')
    parser.add_argument('--filings-start')
    parser.add_argument('--filings-end', default=datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    parser.add_argument('--latest', type=int, default=6)
    parser.add_argument('--sync-references', action='store_true', help='将已接入参考刷新至同一主库；导出时绑定参考发布')
    parser.add_argument('--risk-free-rate', type=float, required=True)
    parser.add_argument('--web-url', required=True, help='读取output的本地原生服务URL；切换后逐公司复验')
    parser.add_argument('--stage-timeout', type=int, default=3600)
    parser.add_argument('--alphalake', type=Path, default=Path(__file__).resolve().parents[3]/'alphalake')
    args = parser.parse_args()
    workspace = Path(os.environ.get('ALPHALAKE_WORKSPACE', Path(__file__).resolve().parents[3]/'workspace')).resolve()
    args.database = args.database.resolve(strict=True)
    args.alphalake = args.alphalake.resolve(strict=True)
    args.output = args.output.resolve()
    if args.database != workspace/'alphalake.duckdb' or not args.output.is_relative_to(workspace/'derived'):
        parser.error('use authoritative workspace/alphalake.duckdb and an output under workspace/derived')
    if args.codes_file:
        args.code = args.codes_file.read_text().split()
    if not args.code:
        parser.error('empty company universe')
    if any(len(c)!=6 or not c.isascii() or not c.isdigit() for c in args.code):
        parser.error('six-digit codes required')
    if args.latest < 1 or args.stage_timeout < 1 or not math.isfinite(args.risk_free_rate) or not 0 <= args.risk_free_rate < 1:
        parser.error('positive limits and risk-free rate in [0,1) required')
    if args.source_mode == 'online' and (not args.filings_start or date.fromisoformat(args.filings_start)>date.fromisoformat(args.filings_end)):
        parser.error('online mode needs a valid filings date range')
    validate_dates(date.fromisoformat(args.period), datetime.fromisoformat(args.as_of or timestamp()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    root = workspace/'derived/system-delivery'; root.mkdir(parents=True, exist_ok=True)
    # ponytail: 单机排他锁；不实现分布式调度。直接调用底层命令的操作者仍须遵守串行约定。
    with ExitStack() as stack:
        for name in (str(args.database)+'.valuation.lock', str(args.output)+'.lock'):
            lock = stack.enter_context(open(name, 'a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        root = Path(tempfile.mkdtemp(prefix='run-', dir=root))
        def cancel(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, cancel)
        return run(args, root)


if __name__ == '__main__':
    raise SystemExit(main())
