"""只读比较两个估值run ID；当前引擎重算核验，不覆盖历史运行。"""
import argparse
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re

from fastapi.encoders import jsonable_encoder
from api.alphalake import ENGINE_REVISION, runtime_versions
from data_sources.alphalake import AlphaLakeRequest, build_inputs, content_hash
from engine.orchestrator import run_full_valuation

CONTRACT = 'alphalake-valuation-comparison-v1'


def load_run(directory, run_id):
    if not re.fullmatch(r'[0-9a-f]{64}',run_id):
        raise ValueError('run ID must be 64 lowercase hex characters')
    path=Path(directory)/(run_id+'.json')
    raw=path.read_bytes()
    def invalid_number(value):
        raise ValueError('nonfinite JSON number: '+value)
    run=json.loads(raw,parse_constant=invalid_number)
    if run['run_id']!=run_id or content_hash(dict(request=run['request'],engine_revision=run['engine_revision']))!=run_id:
        raise ValueError('saved request/engine hash differs from run ID')
    return run,dict(path=str(path.resolve()),sha256=hashlib.sha256(raw).hexdigest())


def replay(run):
    inputs,audit=build_inputs(AlphaLakeRequest.model_validate(run['request']))
    report=jsonable_encoder(asdict(run_full_valuation(inputs)))
    if report!=run['report']:
        raise ValueError('current engine cannot exactly reproduce saved report; no attribution')
    # 重算不调用evaluate，避免为历史请求写入新运行或覆盖旧文件。
    return report,inputs.model_dump(mode='json')


def changes(a,b,path=''):
    """JSON Pointer路径；缺键与显式null分开，数组按保存顺序比较。"""
    if type(a) is type(b) and a==b:
        return
    if isinstance(a,dict) and isinstance(b,dict):
        for key in sorted(a.keys()|b.keys()):
            pointer=path+'/'+key.replace('~','~0').replace('/','~1')
            if key not in a or key not in b:
                yield dict(path=pointer,before_present=key in a,after_present=key in b,before=a.get(key),after=b.get(key))
            else:
                yield from changes(a[key],b[key],pointer)
    elif isinstance(a,list) and isinstance(b,list):
        for i in range(max(len(a),len(b))):
            if i>=len(a) or i>=len(b):
                yield dict(path=path+'/'+str(i),before_present=i<len(a),after_present=i<len(b),before=a[i] if i<len(a) else None,after=b[i] if i<len(b) else None)
            else:
                yield from changes(a[i],b[i],path+'/'+str(i))
    else:
        yield dict(path=path,before_present=True,after_present=True,before=a,after=b)


def brief(value):
    encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)
    if len(encoded)>600:
        return dict(summary_only=True,sha256=content_hash(value),json_characters=len(encoded))
    return value


def category(path):
    if path.startswith('/data/'):
        return 'time_and_identity' if path in ('/data/code','/data/report_period','/data/information_as_of') else 'financial_data_and_evidence'
    if path.startswith('/wacc_binding') or path in ('/policy/wacc','/policy/parameters/wacc'):
        return 'wacc_and_market_inputs'
    if path.startswith('/capital_binding'):
        return 'capital_efficiency_reference'
    if any(path.startswith('/policy/'+p) for p in ('capital_basis','capital_carry_reason','debt_basis','bridge','cash_recovery','operating_cash_ratio','minority_book_multiple','debt_book_multiple','extra_dilution_rate','additional_claims','financial_asset_policy','asset_addbacks')):
        return 'equity_and_share_policy'
    # 公司parameters含预测及桥接，不能根据不完整关键字表硬分成纯预测。
    return 'model_and_policy'


def without_wacc(request):
    r=deepcopy(request)
    r.pop('wacc_binding',None)
    r['policy'].pop('wacc',None)
    return r


def compare_runs(before,after,max_changes=100):
    if not 1<=max_changes<=10000:
        raise ValueError('max_changes must be between 1 and 10000')
    if before['request']['data']['code']!=after['request']['data']['code']:
        raise ValueError('cannot attribute valuations of different security codes')
    (left,li),(right,ri)=replay(before),replay(after)
    normalization=[list(changes(run['inputs'],current)) for run,current in ((before,li),(after,ri))]
    groups={}
    for change in changes(before['request'],after['request']):
        group=groups.setdefault(category(change['path']),dict(count=0,items=[]))
        group['count']+=1
        if len(group['items'])<max_changes:
            group['items'].append(change|dict(before=brief(change['before']),after=brief(change['after'])))
    for group in groups.values():
        group['omitted_count']=group['count']-len(group['items'])
    pv=left['final']['value_per_share'];nv=right['final']['value_per_share']
    delta=str(Decimal(str(nv))-Decimal(str(pv))) if pv is not None and nv is not None else None
    same_forecast=all(left['dcf'][k]==right['dcf'][k] for k in ('revenue_projections','ebit_projections','reinvestment_projections','fcff_projections'))
    same_bridge=li['equity_bridge']==ri['equity_bridge']
    pure=(before['request']['policy']['policy_id']==after['request']['policy']['policy_id']=='nonfinancial-history-fcff-v1'
          and without_wacc(before['request'])==without_wacc(after['request'])
          and not any(normalization) and same_forecast and same_bridge
          and left['cost_of_capital']['wacc']!=right['cost_of_capital']['wacc'])
    def summary(run,report):
        p=run['request']['policy'];d=run['request']['data']
        return dict(run_id=run['run_id'],engine_revision=run['engine_revision'],runtime_versions=run['runtime_versions'],
                    code=d['code'],report_period=d['report_period'],information_as_of=d['information_as_of'],
                    instrument_ids=sorted({f['instrument_id'] for f in d['facts']}),
                    policy_id=p['policy_id'],scenario=p['scenario'],
                    capital_basis=p.get('capital_basis',p.get('bridge_basis','not_applicable')),
                    value_per_share=report['final']['value_per_share'],wacc=report['cost_of_capital']['wacc'])
    return dict(contract_version=CONTRACT,status='compared',before=summary(before,left),after=summary(after,right),
                value_difference=dict(after_minus_before=delta,currency='CNY',unit='CNY/share'),
                changes=groups,
                derived=dict(forecast_unchanged=same_forecast,equity_bridge_inputs_unchanged=same_bridge,
                             equity_bridge_before=left['equity_bridge'],equity_bridge_after=right['equity_bridge']),
                verification=dict(method='current_engine_report_replay_exact',engine_revision=ENGINE_REVISION,runtime_versions=runtime_versions(),
                                  normalization_changes=[dict(count=len(items),omitted_count=max(0,len(items)-max_changes),items=[c|dict(before=brief(c['before']),after=brief(c['after'])) for c in items[:max_changes]]) for items in normalization],
                                  saved_engine_changed=before['engine_revision']!=after['engine_revision']),
                attribution=dict(status='verified_wacc_only' if pure else 'not_attributed',
                                 wacc_contribution_per_share=delta if pure else None,
                                 reason='同一当前引擎重现两端；除WACC配置外请求相同，逐年现金流及股权桥接输入相同。' if pure else '仅列差异；未满足限定的通用模型WACC单因素条件，不分摊贡献。'),
                boundary='请求哈希不认证来源；重算不是独立财报核验。数组按保存顺序比较，同代码不证明跨库证券身份相同。条件估值非当前目标价。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before_run_id');parser.add_argument('after_run_id')
    default=os.environ.get('ALPHALAKE_VALUATION_RUN_DIR',str(Path(__file__).resolve().parents[1]/'data/alphalake_runs'))
    parser.add_argument('--run-dir',default=default)
    parser.add_argument('--after-run-dir',help='另一独立运行目录；默认与run-dir相同')
    parser.add_argument('--max-changes',type=int,default=100,help='每组最多列出的差异数，保留总数及截断计数')
    args=parser.parse_args()
    try:
        a,ae=load_run(args.run_dir,args.before_run_id)
        b,be=load_run(args.after_run_dir or args.run_dir,args.after_run_id)
        result=compare_runs(a,b,args.max_changes)
        result['evidence']=dict(before=ae,after=be)
        payload=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False);exit_code=0
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError) as error:
        payload=json.dumps(dict(contract_version=CONTRACT,status='comparison_rejected',reason=str(error),attribution=None),ensure_ascii=False)
        exit_code=2
    print(payload)
    raise SystemExit(exit_code)


if __name__=='__main__':
    main()
