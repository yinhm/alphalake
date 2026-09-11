"""五个完整财年利润率代理；开发不通过时不评分新留出。"""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from statistics import mean

from tools.backtest_tdx_history import at,available,quarter_periods,operating,error,metrics as base_metrics
from tools.backtest_tdx_cash_revenue import revenue_forecast
from tools.backtest_tdx_multiyear_growth import actual_operating
from tools.validate_tdx_zero_growth import verify_sampling

ROOT=Path(__file__).resolve().parents[3]
MODELS=('zero_growth_current_margin','zero_growth_five_fy_mean_margin','production_growth_current_margin')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def annual(index,artifacts,code,year,cutoff):
    end=date(year,12,31);rows={}
    for period in quarter_periods(end):
        matches=index.get((code,period),[])
        if len(matches)!=1:raise ValueError('annual source missing or duplicated: '+period)
        row=matches[0];artifact=artifacts[row['artifact']]
        if artifact['report_period']!=period or available(row,artifact)>at(cutoff):raise ValueError('annual source period/cutoff differs: '+period)
        rows[period]=row
    observed=operating(rows,end)
    if observed['revenue']<=0:raise ValueError('positive annual revenue required: '+str(year))
    return observed|dict(year=year,margin=observed['ebit']/observed['revenue'])


def metrics(rows,models=MODELS):
    out=base_metrics(rows,models);valid=[r for r in rows if r['status']=='evaluated']
    denominator=sum(abs(r['actual']['ebit']) for r in valid)
    for model in models:
        out['models'][model]['ebit_wape_pct']=100*sum(abs(r['errors'][model]['ebit_error_million_cny']) for r in valid)/denominator if denominator else None
    return out


def study(p,source,phase):
    if p['protocol_id']!='tdx-normalized-margin-v1' or (p['baseline'],p['candidate'],p['comparator'])!=MODELS or p['history']['completed_fiscal_years']!=5:raise ValueError('unsupported margin study')
    if phase not in ('development','holdout') or source['contract_version']!='tdx-history-source-v1':raise ValueError('invalid phase/source')
    samples=[s for s in p['samples'] if s['split']==phase]
    if not samples or len({s['code'] for s in p['samples']})!=len(p['samples']):raise ValueError('unique samples required')
    if len({(w['origin'],w['horizon']) for w in p['windows']})!=len(p['windows']):raise ValueError('duplicate forecast window')
    artifacts={a['file']:a for a in source['artifacts']};index=defaultdict(list)
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    codes={s['code'] for s in samples}
    for row in source['records']:
        if row['code'] not in codes:raise ValueError('unexpected source company')
        index[(row['code'],row['period'])].append(row)
    rows=[]
    for sample in samples:
        for w in p['windows']:
            end=date.fromisoformat(w['origin']);h=w['horizon'];target=end.replace(year=end.year+h);cutoff=f'{end.year}-09-01T00:00:00+08:00'
            if h not in (1,2,3) or (end.month,end.day)!=(6,30) or target>=at(p['evaluation_as_of']).date() or at(cutoff)>=at(p['evaluation_as_of']):raise ValueError('invalid window chronology')
            row=dict(code=sample['code'],origin=end.isoformat(),horizon=h,target=target.isoformat(),forecast_as_of=cutoff,status='blocked',baseline_status='blocked',actual_fcff=None);rows.append(row)
            try:
                _,evidence=revenue_forecast(p,index,artifacts,sample['code'],end,cutoff);base=evidence['operating_inputs'];margin=base['ebit']/base['revenue'];growth=evidence['rule']['clipped_scenario_growth']
                revenue=base['revenue']
                for _ in range(h):revenue*=1+growth
                forecasts={MODELS[0]:dict(revenue=base['revenue'],ebit=base['ebit']),MODELS[2]:dict(revenue=revenue,ebit=revenue*margin)}
                row.update(base=base,rule_evidence=evidence['rule'],forecasts=forecasts)
                try:
                    history=[annual(index,artifacts,sample['code'],year,cutoff) for year in range(end.year-5,end.year)]
                    normalized=mean(r['margin'] for r in history);row.update(history=history,normalized_margin=normalized)
                    if not 0<normalized<=1:raise ValueError('normalized margin outside (0,1]')
                    forecasts[MODELS[1]]=dict(revenue=base['revenue'],ebit=base['revenue']*normalized)
                except (ValueError,KeyError,ArithmeticError) as exc:row['candidate_reason']=str(exc)
                # All predictions and scope decisions precede reading the target outcome.
                actual=actual_operating(index,artifacts,sample['code'],target,p['evaluation_as_of'])
                row.update(actual=actual,baseline_status='evaluated')
                if MODELS[1] in forecasts:row.update(status='evaluated',errors={m:error(f,actual) for m,f in forecasts.items()})
                else:row['status']='blocked_candidate'
            except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    return summarize(p,rows,phase)


def summarize(p,rows,phase,models=MODELS):
    summary=metrics(rows,models);by_horizon={str(h):metrics([r for r in rows if r['horizon']==h],models) for h in (1,2,3)}
    by_window={w['origin']+':'+str(w['horizon']):metrics([r for r in rows if (r['origin'],r['horizon'])==(w['origin'],w['horizon'])],models) for w in p['windows']}
    g=p['gates'];valid_codes={r['code'] for r in rows if r['status']=='evaluated'}
    if any(v is not True for k,v in g.items() if k.startswith('require_')):raise ValueError('required gate disabled')
    def nonworse(m,key,base=models[0],ratio=1):
        a=m['models'][base][key];b=m['models'][models[1]][key]
        return a is not None and b is not None and b<=a*ratio
    sufficient=summary['models'][models[1]]['ebit_n']>=g[phase+'_minimum_pairs'] and len(valid_codes)>=g[phase+'_minimum_companies']
    key='ebit_mae_pct_actual_revenue'
    checks=dict(minimum_sample=sufficient,primary_improvement=sufficient and summary['models'][models[0]][key]>0 and nonworse(summary,key,ratio=1-g['minimum_primary_improvement_fraction']),ebit_wape_nonworse=nonworse(summary,'ebit_wape_pct'),
        production_comparator_nonworse=all(nonworse(summary,k,models[2]) for k in (key,'ebit_wape_pct')),
        each_horizon_nonworse=all(nonworse(m,key) for m in by_horizon.values()),each_window_within_tolerance=all(nonworse(m,key,ratio=g['maximum_each_window_primary_ratio']) for m in by_window.values()),
        leave_one_company_out_nonworse=bool(valid_codes) and all(nonworse(metrics([r for r in rows if r['code']!=code],models),key) for code in valid_codes))
    return dict(protocol_id=p['protocol_id'],phase=phase,baseline_evaluable=sum(r['baseline_status']=='evaluated' for r in rows),evaluated_companies=len(valid_codes),summary=summary,by_horizon=by_horizon,by_window=by_window,decision=dict(passed=all(checks.values()),checks=checks),results=rows,boundary=p['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('snapshot',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();p=json.loads(raw);verify_sampling(p)
        frozen=(args.protocol.parent/'development-study.json').read_bytes();definition=json.loads(frozen)
        if definition!=dict(parent_protocol_sha256=digest(raw),phase='development',samples=[s for s in p['samples'] if s['split']=='development']):raise ValueError('development definition differs')
        data=args.snapshot.read_bytes();source=json.loads(data)
        if source['study_sha256']!=digest(frozen):raise ValueError('source binding differs')
        out=study(p,source,'development');out['evidence']=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_history.py','tools/backtest_tdx_cash_revenue.py','tools/backtest_tdx_multiyear_growth.py','tools/validate_tdx_zero_growth.py','data_sources/alphalake.py']})
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
