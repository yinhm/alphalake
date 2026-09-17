"""依次刷新财务/公告/行业、物化并批量估值；可由 cron/systemd 定时调用。"""
import argparse
from datetime import date, datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from tools.batch_valuate_alphalake import BatchPolicy


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def execute(command, log, timeout):
    with log.open('w') as output:
        with subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT) as child:
            try:
                return child.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                if isinstance(error, KeyboardInterrupt):
                    raise
                return 124


def run_cycle(args, root):
    ledger = dict(database=args.database, report_period=args.period, started_at=timestamp(),
                  status='running', stages=[], boundary='execution status is not valuation coverage; see batch report')
    target = root/'run.json'

    def save():
        with (root/'run.tmp').open('w') as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(root/'run.tmp', target)

    def stage(name, command):
        record = dict(name=name, command=command, started_at=timestamp(), status='running', log=str(root/(name+'.log')))
        ledger['stages'].append(record); save()
        print(json.dumps(dict(stage=name,status='running',log=record['log'])), flush=True)
        try:
            code = execute(command, Path(record['log']), args.stage_timeout)
            record.update(status='completed' if code == 0 else 'failed', exit_code=code)
        except OSError as error:
            code = 1
            record.update(status='failed', error=str(error))
        except KeyboardInterrupt:
            record.update(status='canceled'); raise
        finally:
            record['finished_at'] = timestamp(); save()
        return code

    repo=Path(__file__).resolve().parents[3]
    try:
        for name, extra in [('sync-financial',['--latest',str(args.latest)]),
                            ('sync-bse-code-transitions',['--python',sys.executable,'--parser',str(repo/'internal/source/bse/parse.py')]),
                            ('sync-filings',['--start',args.filings_start,'--end',args.filings_end,'--metadata-only']),
                            ('sync-industries',[])]:
            stage(name, [args.alphalake,name,args.database,*extra])
        # 物化失败时不拿旧标准事实冒充本轮结果；独立采集阶段失败则仍检查成功发布的部分。
        materialized = stage('materialize-fundamentals',[args.alphalake,'materialize-fundamentals',args.database]) == 0
        if materialized:
            stage('repair-filings',[args.alphalake,'repair-filings',args.database,'--period',args.period])
            materialized = stage('materialize-after-filing-repair',[args.alphalake,'materialize-fundamentals',args.database]) == 0
        if materialized and getattr(args,'sync_references',False):
            for name,script in [('sync-country-risk','valuation/backend/data_sources/damodaran_parsers/country_risk_parser.py'),
                                ('sync-industry-beta','valuation/backend/data_sources/damodaran_parsers/beta_parser.py'),
                                ('sync-industry-capital','valuation/backend/data_sources/damodaran_parsers/capex_parser.py'),
                                ('sync-cny-yield','internal/source/chinabond/parse.py'),
                                ('sync-credit-spreads','internal/source/damodaran/ratings.py'),
                                ('sync-company-industries','valuation/backend/data_sources/damodaran_parsers/company_industry_parser.py')]:
                database = args.database if name == 'sync-company-industries' else args.reference_database
                stage(name,[args.alphalake,name,database,'--python',sys.executable,'--parser',str(repo/script)])
        ledger['information_as_of'] = args.as_of or timestamp()
        if materialized:
            reference_args = ['--reference-database',args.reference_database] if getattr(args,'reference_database',None) else []
            state = getattr(args, 'incremental_state', None)
            previous_args = ['--previous-report',str(state)] if state and Path(state).exists() else []
            code = stage('batch-valuation',[sys.executable,'-m','tools.batch_valuate_alphalake',args.database,
                  '--period',args.period,'--as-of',ledger['information_as_of'],'--policy',args.policy,
                  '--output-dir',str(root/'batches'),'--alphalake',args.alphalake,
                  '--query-timeout',str(args.stage_timeout),*previous_args,*reference_args])
            if code == 0 and state:
                try:
                    receipt = json.loads((root/'batch-valuation.log').read_text())
                    report = Path(receipt['report']).resolve()
                    if report.parent != (root/'batches').resolve():
                        raise ValueError('batch report outside cycle directory')
                    payload = report.read_bytes()
                    batch = json.loads(payload)
                    if batch['contract_version'] != 'alphalake-batch-v1' or batch['source_database'] != args.database:
                        raise ValueError('batch state contract/database mismatch')
                    counts = batch['automation_counts']
                    if not isinstance(counts, dict) or batch['universe_count'] != len(batch['companies']):
                        raise ValueError('incomplete batch state')
                    target_state = Path(state); target_state.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=target_state.parent, delete=False) as output:
                        pending = Path(output.name)
                        try:
                            output.write(payload); output.flush(); os.fsync(output.fileno())
                            os.replace(pending, target_state)
                        finally:
                            pending.unlink(missing_ok=True)
                    ledger['incremental_state'] = dict(status='published_attempt', path=str(target_state),
                        batch_report=str(report), automation_counts=counts)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    ledger['incremental_state'] = dict(status='failed', reason=str(error))
        ledger['status'] = 'completed' if all(r['status']=='completed' for r in ledger['stages']) and ledger.get('incremental_state',{}).get('status') != 'failed' else 'partial_or_failed'
    except KeyboardInterrupt:
        ledger['status'] = 'canceled'
    finally:
        ledger['finished_at'] = timestamp(); save()
    print(json.dumps(dict(report=str(target),status=ledger['status']),ensure_ascii=False),flush=True)
    return 0 if ledger['status']=='completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--period',required=True)
    parser.add_argument('--filings-start',required=True)
    parser.add_argument('--filings-end',default=datetime.now(timezone(timedelta(hours=8))).date().isoformat())
    parser.add_argument('--as-of',help='默认取同步/物化结束后时点；显式值必须带时区')
    parser.add_argument('--latest',type=int,default=6)
    parser.add_argument('--stage-timeout',type=int,default=3600)
    parser.add_argument('--policy',required=True)
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--alphalake',default=str(Path(__file__).resolve().parents[3]/'alphalake'))
    parser.add_argument('--reference-database',help='批次运行时自动选择本地最新WACC参考版本')
    parser.add_argument('--sync-references',action='store_true',help='批次前刷新WACC四源、行业资本效率及公司行业，需指定参考库')
    args = parser.parse_args()
    period = date.fromisoformat(args.period)
    if args.latest<1 or args.stage_timeout<1 or period.month%3 or (period+timedelta(days=1)).day!=1:
        parser.error('positive limits and quarter-end period required')
    if args.sync_references and not args.reference_database:
        parser.error('sync-references requires reference-database')
    if date.fromisoformat(args.filings_start)>date.fromisoformat(args.filings_end):
        parser.error('filings start exceeds end')
    if args.as_of and (datetime.fromisoformat(args.as_of).utcoffset() is None or datetime.fromisoformat(args.as_of).date()<period):
        parser.error('aware cutoff after report period required')
    policy = BatchPolicy.model_validate_json(Path(args.policy).read_text())
    if any(p.approved_report_period!=period for p in [a.policy for a in policy.assignments.values()]+[r.policy for r in policy.industry_rules]):
        parser.error('policy report period differs from requested period')
    args.incremental_state = str((Path(args.output_dir)/'incremental-state.json').resolve())
    args.database = str(Path(args.database).resolve())
    args.policy = str(Path(args.policy).resolve())
    args.alphalake = str(Path(args.alphalake).resolve())
    if args.reference_database:
        args.reference_database = str(Path(args.reference_database).resolve())
        if args.sync_references:
            Path(args.reference_database).parent.mkdir(parents=True,exist_ok=True)
    Path(args.database).parent.mkdir(parents=True,exist_ok=True)
    # ponytail: 本机进程锁；远程分布式调度不在此入口范围。
    with open(args.database+'.valuation.lock','a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('another valuation refresh owns this database lock')
        root = Path(args.output_dir); root.mkdir(parents=True,exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix='refresh-',dir=root)).resolve()
        def cancel(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM,cancel)
        return run_cycle(args,root)


if __name__=='__main__':
    raise SystemExit(main())
