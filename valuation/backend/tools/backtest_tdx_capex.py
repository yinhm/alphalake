"""固定资本开支现金预测复验：开发选择后再读留出，保留完整分母与源时点。"""
import argparse
from datetime import date
from decimal import Decimal
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean,median

from tools.backtest_tdx_history import at,available,window,quarter_periods

BASE='repeat_latest_capex'
CANDIDATES=('median_capex','median_intensity')


def financial(index,artifacts,code,end,cutoff):
    periods=set(quarter_periods(end))
    if end.month!=12:periods|={f'{end.year-1}-12-31',end.replace(year=end.year-1).isoformat()}
    rows={}
    for period in sorted(periods):
        matches=index.get((code,period),[])
        if len(matches)!=1:raise ValueError(('missing' if not matches else 'duplicate')+' source identity: '+period)
        row=matches[0];a=artifacts[row['artifact']]
        if a['report_period']!=period:raise ValueError('artifact period differs')
        if available(row,a)>at(cutoff):raise ValueError('source not available at cutoff: '+period)
        rows[period]=row
    capex,refs=window(rows,end,'FN114');revenue,more=window(rows,end,'FN230')
    if capex<0 or revenue<=0:raise ValueError('negative capex or nonpositive revenue: '+end.isoformat())
    return dict(period=end.isoformat(),capex_cny=str(capex),revenue_cny=str(revenue),source_inputs=refs+more)


def evaluate(p,source,split,models):
    artifacts={r['file']:r for r in source['artifacts']}
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    index=defaultdict(list)
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    samples=[s for s in p['samples'] if s['split']==split]
    if not samples or len({s['code'] for s in samples})!=len(samples):raise ValueError('empty or duplicate sample')
    results=[]
    for sample in samples:
        for origin in p['origins']:
            end=date.fromisoformat(origin);target=end.replace(year=end.year+1)
            if end.month!=6 or end.day!=30:raise ValueError('H1 origin required')
            cutoff=f'{end.year}-09-01T00:00:00+08:00'
            if at(cutoff)>=at(p['evaluation_as_of']):raise ValueError('invalid evaluation chronology')
            row=dict(code=sample['code'],stratum=sample['stratum'],origin=origin,target=target.isoformat(),status='blocked',actual_fcff=None)
            results.append(row)
            try:
                history=[financial(index,artifacts,sample['code'],d,cutoff) for d in (end,date(end.year-1,12,31),date(end.year-2,12,31))]
                amounts=[Decimal(r['capex_cny']) for r in history];revenues=[Decimal(r['revenue_cny']) for r in history]
                all_predictions={BASE:amounts[0],'median_capex':median(amounts),'median_intensity':median(c/r for c,r in zip(amounts,revenues))*revenues[0]}
                predictions={m:all_predictions[m] for m in models};row.update(history=history,forecast_as_of=cutoff,forecasts_cny={m:str(v) for m,v in predictions.items()})
                # 预测已固定之后才读取目标期；目标缺失仍保留已有预测及拒绝原因。
                actual=financial(index,artifacts,sample['code'],target,p['evaluation_as_of'])
                a=Decimal(actual['capex_cny']);r=Decimal(actual['revenue_cny'])
                row.update(status='evaluated',actual=actual,errors={m:dict(error_cny=str(v-a),error_pct_actual_revenue=float(100*(v-a)/r)) for m,v in predictions.items()})
            except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    return results


def metrics(rows,models):
    valid=[r for r in rows if r['status']=='evaluated'];total=sum((Decimal(r['actual']['capex_cny']) for r in valid),Decimal(0))
    return dict(candidates=len(rows),statuses=dict(Counter(r['status'] for r in rows)),models={m:dict(n=len(valid),
        mae_pct_actual_revenue=mean(abs(r['errors'][m]['error_pct_actual_revenue']) for r in valid) if valid else None,
        mean_bias_pct_actual_revenue=mean(r['errors'][m]['error_pct_actual_revenue'] for r in valid) if valid else None,
        capex_wape_pct=float(100*sum((abs(Decimal(r['errors'][m]['error_cny'])) for r in valid),Decimal(0))/total) if total>0 else None) for m in models})


def gates(p,rows,model):
    models=(BASE,model);s=metrics(rows,models)['models'];b=s[BASE];c=s[model];g=p['gates']
    sufficient=b['n']>=g['minimum_pairs'] and b['mae_pct_actual_revenue'] is not None and b['mae_pct_actual_revenue']>0
    checks=dict(minimum_pairs=sufficient,primary_improvement=sufficient and c['mae_pct_actual_revenue']<=b['mae_pct_actual_revenue']*(1-g['minimum_primary_improvement_fraction']),
                capex_wape_nonworse=c['capex_wape_pct'] is not None and b['capex_wape_pct'] is not None and c['capex_wape_pct']<=b['capex_wape_pct']*g['maximum_capex_wape_ratio'])
    per_period={o:metrics([r for r in rows if r['origin']==o],models)['models'] for o in p['origins']}
    checks['each_period_nonworse']=all(s[BASE]['n']>0 and s[model]['mae_pct_actual_revenue']<=s[BASE]['mae_pct_actual_revenue']*g['maximum_each_period_primary_ratio'] for s in per_period.values())
    valid=[r for r in rows if r['status']=='evaluated'];leave=[]
    for code in sorted({r['code'] for r in valid}):
        s=metrics([r for r in valid if r['code']!=code],models)['models']
        leave.append(s[BASE]['n']>0 and s[model]['mae_pct_actual_revenue']<=s[BASE]['mae_pct_actual_revenue'])
    checks['leave_one_company_out_nonworse']=bool(leave) and all(leave)
    return dict(passed=all(checks.values()),checks=checks)


def study(p,source,phase,selection=None):
    if p['protocol_id']!='tdx-capex-forecast-v1' or p['baseline']!=BASE or tuple(p['candidates'])!=CANDIDATES or source['contract_version']!='tdx-history-source-v1':raise ValueError('unsupported study')
    if phase=='development':models=(BASE,)+CANDIDATES
    elif phase=='holdout' and selection and selection['decision']['selected'] in CANDIDATES:models=(BASE,selection['decision']['selected'])
    else:raise ValueError('holdout requires passing development selection')
    rows=evaluate(p,source,phase if phase=='development' else 'holdout',models)
    summary=metrics(rows,models);verdicts={m:gates(p,rows,m) for m in models if m!=BASE}
    if phase=='development':
        passing=[m for m in CANDIDATES if verdicts[m]['passed']]
        selected=min(passing,key=lambda m:summary['models'][m]['mae_pct_actual_revenue']) if passing else None
        decision=dict(selected=selected,verdicts=verdicts)
    else:decision=dict(selected=models[1],verdicts=verdicts)
    return dict(protocol_id=p['protocol_id'],phase=phase,summary=summary,by_origin={o:metrics([r for r in rows if r['origin']==o],models) for o in p['origins']},decision=decision,results=rows,
                boundary='报告资本开支现金预测，不是净再投资或FCFF；相同三历史窗口分母，亏损不剔除；TDX+FN314后来取得版本研究，不是严格PIT或生产政策')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('snapshot',type=Path);parser.add_argument('--phase',choices=['development','holdout'],required=True);parser.add_argument('--selection',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();data=args.snapshot.read_bytes();p=json.loads(raw);source=json.loads(data);digest=lambda b:hashlib.sha256(b).hexdigest()
        if source['study_sha256']!=digest(raw):raise ValueError('source/protocol hash differs')
        evidence=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),dependency_sha256=digest(Path(__file__).with_name('backtest_tdx_history.py').read_bytes()))
        selection=None
        if args.phase=='holdout':
            if args.selection is None:raise ValueError('selection receipt required')
            selection=json.loads(args.selection.read_bytes());reproduced=study(p,source,'development')
            if selection['evidence']!=evidence or any(selection[k]!=reproduced[k] for k in ('decision','summary','by_origin')):raise ValueError('development selection does not reproduce')
        result=study(p,source,args.phase,selection);result['evidence']=evidence
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
