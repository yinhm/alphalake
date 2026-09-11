"""两年/三年经营预测路径比较；复用生产增速规则，不生成历史FCFF。"""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from statistics import mean

from engine.module_4_dcf import _revenue_growth_path
from tools.backtest_tdx_cash_revenue import revenue_forecast
from tools.backtest_tdx_history import at,available,quarter_periods,operating,error,metrics as original_metrics

ROOT=Path(__file__).resolve().parents[3]
MODELS=('flat_first_five','zero_growth','fade_to_terminal_by_year_five')


def actual_operating(index,artifacts,code,end,cutoff):
    periods=set(quarter_periods(end))|{end.isoformat(),f'{end.year-1}-12-31',end.replace(year=end.year-1).isoformat()};rows={}
    for period in sorted(periods):
        matches=index.get((code,period),[])
        if len(matches)!=1:raise ValueError('actual source identity missing or duplicated: '+period)
        row=matches[0];a=artifacts[row['artifact']]
        if a['report_period']!=period or available(row,a)>at(cutoff):raise ValueError('actual source period/cutoff differs')
        rows[period]=row
    result=operating(rows,end)
    if result['revenue']<=0:raise ValueError('positive actual revenue required')
    return result


def metrics(rows):
    result=original_metrics(rows,MODELS);valid=[r for r in rows if r['status']=='evaluated']
    denominator=sum(abs(r['actual']['ebit']) for r in valid)
    for model in MODELS:
        result['models'][model].update(revenue_mae_pct=mean(abs(r['errors'][model]['revenue_error_pct']) for r in valid) if valid else None,
            ebit_wape_pct=100*sum(abs(r['errors'][model]['ebit_error_million_cny']) for r in valid)/denominator if denominator else None)
    return result


def study(p,source,phase='development'):
    if phase not in ('development','holdout'):raise ValueError('invalid study phase')
    p=p|dict(samples=[s for s in p['samples'] if s['split']==phase])
    if p['protocol_id']!='tdx-multiyear-growth-v1' or (p['baseline'],p['benchmark'],p['candidate'])!=MODELS or p['base_policy']['margin_shift']!=0:raise ValueError('unsupported growth study')
    if source['contract_version']!='tdx-history-source-v1':raise ValueError('source contract differs')
    if not p['samples'] or any(s['split']!=phase for s in p['samples']) or len({s['code'] for s in p['samples']})!=len(p['samples']):raise ValueError('unique phase samples required')
    if len({(w['origin'],w['horizon']) for w in p['windows']})!=len(p['windows']):raise ValueError('duplicate forecast window')
    if any(v is not True for k,v in p['gates'].items() if k.startswith('require_')):raise ValueError('required gate disabled')
    index=defaultdict(list);artifacts={a['file']:a for a in source['artifacts']}
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    rows=[]
    for sample in p['samples']:
        for w in p['windows']:
            end=date.fromisoformat(w['origin']);horizon=w['horizon'];target=end.replace(year=end.year+horizon);cutoff=f'{end.year}-09-01T00:00:00+08:00'
            if horizon not in (2,3) or (end.month,end.day)!=(6,30) or at(cutoff)>=at(p['evaluation_as_of']) or target>=at(p['evaluation_as_of']).date():raise ValueError('invalid forecast window')
            row=dict(code=sample['code'],origin=end.isoformat(),horizon=horizon,target=target.isoformat(),forecast_as_of=cutoff,status='blocked',actual_fcff=None);rows.append(row)
            try:
                _,evidence=revenue_forecast(p,index,artifacts,sample['code'],end,cutoff)
                base=evidence['operating_inputs'];g=evidence['rule']['clipped_scenario_growth'];terminal=p['base_policy']['terminal_growth'];margin=base['ebit']/base['revenue']
                paths={MODELS[0]:_revenue_growth_path(g,g,terminal,5,10),MODELS[1]:[0.]*10,MODELS[2]:_revenue_growth_path(g,g,terminal,1,5)+[terminal]*5}
                forecasts={}
                for model,path in paths.items():
                    revenue=base['revenue'];annual=[]
                    for growth in path[:horizon]:
                        revenue*=1+growth;annual.append(dict(revenue=revenue,ebit=revenue*margin,growth=growth))
                    forecasts[model]=dict(revenue=revenue,ebit=revenue*margin,annual=annual)
                row.update(base=base,rule_evidence=evidence['rule'],forecasts=forecasts)
                actual=actual_operating(index,artifacts,sample['code'],target,p['evaluation_as_of'])
                row.update(status='evaluated',actual=actual,errors={model:error(f,actual) for model,f in forecasts.items()})
            except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    summary=metrics(rows);by_horizon={str(h):metrics([r for r in rows if r['horizon']==h]) for h in (2,3)}
    by_window={w['origin']+':'+str(w['horizon']):metrics([r for r in rows if (r['origin'],r['horizon'])==(w['origin'],w['horizon'])]) for w in p['windows']}
    g=p['gates'];b,z,c=(summary['models'][m] for m in MODELS)
    def nonworse(m,key,benchmark=MODELS[0]):
        baseline=m['models'][benchmark][key];candidate=m['models'][MODELS[2]][key]
        return candidate is not None and baseline is not None and candidate<=baseline
    sufficient=c['revenue_n']>=g['minimum_pairs']
    checks=dict(minimum_pairs=sufficient,primary_improvement=sufficient and b['revenue_mae_pct']>0 and c['revenue_mae_pct']<=b['revenue_mae_pct']*(1-g['minimum_primary_improvement_fraction']),
        revenue_wape_nonworse=nonworse(summary,'revenue_wape_pct'),zero_growth_revenue_nonworse=all(nonworse(summary,k,MODELS[1]) for k in ('revenue_mae_pct','revenue_wape_pct')),
        ebit_nonworse=all(nonworse(summary,k) for k in ('ebit_mae_pct_actual_revenue','ebit_wape_pct')),
        each_horizon_nonworse=all(nonworse(m,'revenue_mae_pct') for m in by_horizon.values()),
        each_window_nonworse=all(m['models'][MODELS[2]]['revenue_n']>0 and m['models'][MODELS[2]]['revenue_mae_pct']<=m['models'][MODELS[0]]['revenue_mae_pct']*g['maximum_each_window_primary_ratio'] for m in by_window.values()))
    codes={r['code'] for r in rows if r['status']=='evaluated'}
    checks['leave_one_company_out_nonworse']=bool(codes) and all(nonworse(metrics([r for r in rows if r['code']!=code]),'revenue_mae_pct') for code in codes)
    return dict(protocol_id=p['protocol_id'],summary=summary,by_horizon=by_horizon,by_window=by_window,decision=dict(passed=all(checks.values()),checks=checks),results=rows,boundary=p['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();p=json.loads(raw);digest=lambda b:hashlib.sha256(b).hexdigest();parent=(ROOT/p['parent_protocol']).read_bytes();data=(ROOT/p['snapshot']).read_bytes();parent_data=json.loads(parent);source=json.loads(data)
        if digest(parent)!=p['parent_protocol_sha256'] or digest(data)!=p['snapshot_sha256'] or source['study_sha256']!=parent_data['parent_protocol_sha256']:raise ValueError('parent/source binding differs')
        if p['samples']!=parent_data['samples'] or p['base_policy']!=parent_data['base_policy']:raise ValueError('sample or base policy differs')
        out=study(p,source);out['evidence']=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_cash_revenue.py','tools/backtest_tdx_history.py','data_sources/alphalake.py','engine/module_4_dcf.py']})
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
