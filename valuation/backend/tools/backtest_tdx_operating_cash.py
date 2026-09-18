"""直接预测报表经营现金流，并单列OCF减资本开支误差；不生成实际FCFF。"""
import argparse
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import mean

from tools.audit_tdx_reinvestment import component
from tools.backtest_tdx_capex import financial
from tools.backtest_tdx_history import at

MODELS=('repeat_latest','zero_ocf','mean_two_ocf_margins')


def observation(index,artifacts,code,end,cutoff):
    result=financial(index,artifacts,code,end,cutoff)
    ocf=component(index,artifacts,code,end,'operating_cash_flow',cutoff)
    if ocf['status']=='blocked':raise ValueError('OCF blocked: '+str(ocf['issues']))
    result.update(ocf_cny=ocf['value_cny'],ocf_inputs=ocf['source_inputs'])
    return result


def evaluate(p,source,phase):
    artifacts={a['file']:a for a in source['artifacts']};index=defaultdict(list)
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    samples=[s for s in p['samples'] if s['split']==phase]
    if not samples or len({s['code'] for s in samples})!=len(samples):raise ValueError('empty/duplicate sample')
    result=[]
    for s in samples:
        for origin in p['origins']:
            end=date.fromisoformat(origin);cutoff=f'{end.year}-09-01T00:00:00+08:00';target=end.replace(year=end.year+1)
            if end.month!=6 or end.day!=30 or at(cutoff)>=at(p['evaluation_as_of']):raise ValueError('invalid H1 chronology')
            row=dict(code=s['code'],origin=origin,target=target.isoformat(),forecast_as_of=cutoff,status='blocked',actual_fcff=None);result.append(row)
            try:
                current=observation(index,artifacts,s['code'],end,cutoff)
                prior=observation(index,artifacts,s['code'],end.replace(year=end.year-1),cutoff)
                ocf=Decimal(current['ocf_cny']);rev=Decimal(current['revenue_cny']);capex=Decimal(current['capex_cny'])
                predicted=dict(repeat_latest=ocf,zero_ocf=Decimal(0),mean_two_ocf_margins=(ocf+Decimal(prior['ocf_cny'])*rev/Decimal(prior['revenue_cny']))/2)
                row.update(current=current,prior=prior,forecasts={m:dict(ocf_cny=str(v),capex_cny=str(capex),cash_proxy_cny=str(v-capex)) for m,v in predicted.items()})
                actual=observation(index,artifacts,s['code'],target,p['evaluation_as_of'])
                actual['cash_proxy_cny']=str(Decimal(actual['ocf_cny'])-Decimal(actual['capex_cny']))
                errors={kind:{m:dict(cny=str(Decimal(f[kind])-Decimal(actual[kind])),pct_actual_revenue=float(100*(Decimal(f[kind])-Decimal(actual[kind]))/Decimal(actual['revenue_cny']))) for m,f in row['forecasts'].items()} for kind in ('ocf_cny','cash_proxy_cny')}
                row.update(status='evaluated',actual=actual,errors=errors)
            except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    return result


def metrics(rows):
    valid=[r for r in rows if r['status']=='evaluated'];targets={}
    for kind in ('ocf_cny','cash_proxy_cny'):
        denominator=sum((abs(Decimal(r['actual'][kind])) for r in valid),Decimal(0))
        targets[kind]={m:dict(n=len(valid),mae_pct_actual_revenue=mean(abs(r['errors'][kind][m]['pct_actual_revenue']) for r in valid) if valid else None,
            wape_pct=float(100*sum((abs(Decimal(r['errors'][kind][m]['cny'])) for r in valid),Decimal(0))/denominator) if denominator else None) for m in MODELS}
    return dict(candidates=len(rows),statuses=dict(Counter(r['status'] for r in rows)),targets=targets)


def study(p,source,phase):
    if p['protocol_id']!='tdx-operating-cash-forecast-v1' or source['contract_version']!='tdx-history-source-v1' or (p['baseline'],p['benchmark'],p['candidate'])!=MODELS:raise ValueError('unsupported study')
    if phase not in ('development','holdout'):raise ValueError('invalid phase')
    g=p['gates']
    if any(g[k] is not True for k in ('require_wape_nonworse','require_zero_benchmark_nonworse','require_leave_one_company_out_nonworse','require_cash_proxy_nonworse')):raise ValueError('required gate disabled')
    rows=evaluate(p,source,phase);summary=metrics(rows);by_origin={o:metrics([r for r in rows if r['origin']==o]) for o in p['origins']}
    b,z,c=(summary['targets']['ocf_cny'][m] for m in MODELS)
    sufficient=b['n']>=g['minimum_pairs'] and b['mae_pct_actual_revenue'] is not None and b['mae_pct_actual_revenue']>0
    def nonworse(a,b,metric):return a[metric] is not None and b[metric] is not None and a[metric]<=b[metric]
    checks=dict(minimum_pairs=sufficient,primary_improvement=sufficient and c['mae_pct_actual_revenue']<=b['mae_pct_actual_revenue']*(1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(c,b,'wape_pct'),zero_benchmark_nonworse=all(nonworse(c,z,k) for k in ('mae_pct_actual_revenue','wape_pct')),
        each_period_nonworse=all(x['targets']['ocf_cny'][MODELS[2]]['n']>0 and x['targets']['ocf_cny'][MODELS[2]]['mae_pct_actual_revenue']<=x['targets']['ocf_cny'][MODELS[0]]['mae_pct_actual_revenue']*g['maximum_each_period_primary_ratio'] for x in by_origin.values()),
        cash_proxy_nonworse=all(nonworse(summary['targets']['cash_proxy_cny'][MODELS[2]],summary['targets']['cash_proxy_cny'][MODELS[0]],k) for k in ('mae_pct_actual_revenue','wape_pct')))
    leave=[]
    for code in sorted({r['code'] for r in rows if r['status']=='evaluated'}):
        m=metrics([r for r in rows if r['code']!=code])['targets']['ocf_cny'];leave.append(nonworse(m[MODELS[2]],m[MODELS[0]],'mae_pct_actual_revenue'))
    checks['leave_one_company_out_nonworse']=bool(leave) and all(leave)
    return dict(protocol_id=p['protocol_id'],phase=phase,summary=summary,by_origin=by_origin,decision=dict(passed=all(checks.values()),checks=checks),
        refusals=dict(Counter(r['reason'] for r in rows if r['status']=='blocked')),results=rows,boundary=p['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('snapshot',type=Path);parser.add_argument('--phase',required=True,choices=['development','holdout']);parser.add_argument('--selection',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();data=args.snapshot.read_bytes();p=json.loads(raw);source=json.loads(data);digest=lambda b:hashlib.sha256(b).hexdigest()
        if source['study_sha256']!=digest(raw):raise ValueError('source/protocol hash differs')
        evidence=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),
            helpers={n:digest(Path(__file__).with_name(n+'.py').read_bytes()) for n in ('backtest_tdx_capex','backtest_tdx_history','tdx_research_source','audit_tdx_reinvestment')})
        if args.phase=='holdout':
            if args.selection is None:raise ValueError('passing development receipt required')
            selected=json.loads(args.selection.read_bytes());dev=study(p,source,'development')
            if selected['evidence']!=evidence or any(selected[k]!=dev[k] for k in ('summary','by_origin','decision')) or not dev['decision']['passed']:raise ValueError('development selection failed or differs')
        result=study(p,source,args.phase);result['evidence']=evidence
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
