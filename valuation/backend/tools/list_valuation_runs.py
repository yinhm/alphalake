"""只读发现已保存估值；最新按信息截止判断，同一模型同一截止保留并列。"""
import argparse
from datetime import date, datetime
import json
import math
import os
from pathlib import Path
import re

from data_sources.alphalake import content_hash
from tools.compare_valuations import load_run
from tools.company_valuation import SUCCESS

CONTRACT = 'alphalake-run-query-v1'


def timestamp(value):
    result=datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError('timezone-aware information cutoff required')
    return result


def summarize(run, evidence):
    request=run['request'];data=request['data'];policy=request['policy']
    if run['status'] not in SUCCESS or not re.fullmatch(r'\d{6}',data['code']):
        raise ValueError('not a successful company valuation record')
    at=timestamp(data['information_as_of']);period=date.fromisoformat(data['report_period'])
    value=run['report']['final']['value_per_share'];wacc=run['report']['cost_of_capital']['wacc']
    for number in (value,wacc):
        if number is not None and (isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number)):
            raise ValueError('invalid saved numeric result')
    if not isinstance(policy['policy_id'],str) or not policy['policy_id'] or not isinstance(policy['scenario'],str):
        raise ValueError('invalid model or scenario')
    return dict(run_id=run['run_id'],code=data['code'],policy_id=policy['policy_id'],scenario=policy['scenario'],
                report_period=period.isoformat(),information_as_of=at.isoformat(),status=run['status'],
                value_per_share=dict(value=value,currency='CNY',unit='CNY/share'),wacc=wacc,
                capital_basis=policy.get('capital_basis',policy.get('bridge_basis','not_applicable')),
                engine_revision=run['engine_revision'],request_sha256=content_hash(request),locations=[evidence])


def list_runs(directories,code,models=(),period=None,as_of=None,latest_per_model=False,limit=50,offset=0):
    if not re.fullmatch(r'\d{6}',code) or not 1<=limit<=500 or offset<0:
        raise ValueError('six-digit code, limit 1..500 and nonnegative offset required')
    cutoff=timestamp(as_of) if as_of else None
    report_period=date.fromisoformat(period).isoformat() if period else None
    if not directories:
        raise ValueError('at least one run directory required')
    records={};digests={};conflicted=set();issues=[];scanned=0
    roots=list(dict.fromkeys(str(Path(d).resolve()) for d in directories))
    # ponytail: 逐文件扫描的耗时随历史增长；实测成为瓶颈后再加可重建索引。
    for root in roots:
        try:
            directory=Path(root)
            if not directory.is_dir():raise ValueError('run directory missing or not a directory')
            # 非递归读取发布完成的JSON；不扫描workspace、数据库或临时文件。
            paths=sorted(directory.iterdir())
        except (ValueError,OSError) as error:
            issues.append(dict(path=root,reason=str(error)));continue
        for path in paths:
            if path.suffix!='.json':continue
            scanned+=1
            try:
                run,evidence=load_run(directory,path.stem)
                row=summarize(run,evidence)
                digest=content_hash(run)
                ident=run['run_id']
                if ident in digests and digests[ident]!=digest:
                    conflicted.add(ident)
                    issues.append(dict(path=str(path),run_id=ident,reason='same run ID has conflicting saved contents'))
                    continue
                digests[ident]=digest
                if ident in records:records[ident]['locations'].append(evidence)
                else:records[ident]=row
            except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as error:
                issues.append(dict(path=str(path),reason=str(error)))
    for ident in conflicted:records.pop(ident,None)
    rows=[r for r in records.values() if r['code']==code and (not models or r['policy_id'] in models)
          and (report_period is None or r['report_period']==report_period)
          and (cutoff is None or timestamp(r['information_as_of'])<=cutoff)]
    matched=len(rows)
    if latest_per_model:
        latest={}
        for row in rows:
            model=row['policy_id'];at=timestamp(row['information_as_of'])
            latest[model]=max(latest.get(model,at),at)
        rows=[r for r in rows if timestamp(r['information_as_of'])==latest[r['policy_id']]]
    # ID只稳定展示顺序，绝不据它选“最近的一次计算”。
    rows.sort(key=lambda r:(r['policy_id'],r['run_id']))
    rows.sort(key=lambda r:timestamp(r['information_as_of']),reverse=True)
    groups=[]
    for model in sorted({r['policy_id'] for r in rows}):
        candidates=[r for r in rows if r['policy_id']==model]
        latest_at=max(timestamp(r['information_as_of']) for r in candidates)
        ties=[r['run_id'] for r in candidates if timestamp(r['information_as_of'])==latest_at]
        groups.append(dict(policy_id=model,latest_information_as_of=latest_at.isoformat(),latest_count=len(ties),
                           unique_latest_run_id=ties[0] if len(ties)==1 and not issues else None))
    return dict(contract_version=CONTRACT,status='partial' if issues else 'ok' if rows else 'no_matches',
                query=dict(code=code,models=list(models),report_period=report_period,as_of=as_of,latest_per_model=latest_per_model),
                directories=roots,scanned_files=scanned,matched_count=matched,result_count=len(rows),offset=offset,limit=limit,
                has_more=offset+limit<len(rows),results=rows[offset:offset+limit],model_groups=groups,
                issues=issues[:100],issue_count=len(issues),omitted_issues=max(0,len(issues)-100),latest_selection_complete=not issues,
                scan_scope='configured_directories_nonrecursive_non_atomic',
                verification='request_engine_hash_only_not_report_replay',
                boundary='仅配置目录中的历史运行；不代表最新市场数据或计算创建时间。同截止保留并列，文件修改时间不参与排序；价格未在查询阶段重算。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('code')
    parser.add_argument('--run-dir',action='append',help='可重复提供；默认ALPHALAKE_VALUATION_RUN_DIR或后端data/alphalake_runs')
    parser.add_argument('--model',action='append',default=[],help='精确policy_id，可重复')
    parser.add_argument('--period');parser.add_argument('--as-of')
    parser.add_argument('--latest-per-model',action='store_true')
    parser.add_argument('--limit',type=int,default=50);parser.add_argument('--offset',type=int,default=0)
    args=parser.parse_args()
    try:
        dirs=args.run_dir or [os.environ.get('ALPHALAKE_VALUATION_RUN_DIR',str(Path(__file__).resolve().parents[1]/'data/alphalake_runs'))]
        result=list_runs(dirs,args.code,args.model,args.period,args.as_of,args.latest_per_model,args.limit,args.offset)
        code=2 if result['status']=='partial' else 0
        payload=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
    except (ValueError,TypeError,OSError) as error:
        payload=json.dumps(dict(contract_version=CONTRACT,status='query_rejected',reason=str(error)),ensure_ascii=False);code=1
    print(payload)
    raise SystemExit(code)


if __name__=='__main__':
    main()
