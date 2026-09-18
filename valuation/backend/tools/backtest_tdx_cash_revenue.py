"""冻结开发样本上的现金收入假设比较，不把报表现金代理当FCFF。"""
import argparse
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import mean

from data_sources.alphalake import HistoricalDCFPolicy,historical_forecast
from tools.backtest_tdx_history import at,available,operating
from tools.tdx_research_source import financial_value,source_field
from tools.backtest_tdx_operating_cash import observation

MODELS=('mean_two_ocf_margins_zero_growth','mean_two_ocf_margins_production_revenue')
ROOT=Path(__file__).resolve().parents[3]


def revenue_forecast(p,index,artifacts,code,end,cutoff):
    history={}
    for (ticker,period),rows in index.items():
        if ticker!=code or not date(end.year-1,1,1)<=date.fromisoformat(period)<=end:continue
        if len(rows)!=1:raise ValueError('duplicate revenue history')
        row=rows[0];a=artifacts[row['artifact']]
        if a['report_period']!=period:raise ValueError('revenue artifact period differs')
        if available(row,a)<=at(cutoff):history[period]=row
    base=operating(history,end)
    quarters={date.fromisoformat(k):(financial_value(v,'revenue'),v['artifact']+':'+code+':'+source_field('revenue')) for k,v in history.items()}
    policy=HistoricalDCFPolicy.model_validate(p['base_policy']|dict(approved_report_period=end.isoformat()))
    forecast,evidence=historical_forecast(base['revenue'],base['ebit'],quarters,end,policy)
    # Match the production float revenue multiplication, then return CNY for Decimal cash math.
    return Decimal(str(base['revenue']*(1+forecast[0].growth)))*1000000,dict(rule=evidence,operating_inputs=base)


def metrics(rows):
    valid=[r for r in rows if r['status']=='evaluated'];out={}
    for kind in ('ocf','cash_proxy'):
        denominator=sum((abs(Decimal(r['actual'][kind])) for r in valid),Decimal(0))
        out[kind]={}
        for model in MODELS:
            errors=[abs(Decimal(r['forecasts'][model][kind])-Decimal(r['actual'][kind])) for r in valid]
            out[kind][model]=dict(n=len(valid),mae_pct_actual_revenue=mean(float(100*e/Decimal(r['actual']['revenue_cny'])) for e,r in zip(errors,valid)) if valid else None,
                wape_pct=float(100*sum(errors)/denominator) if denominator else None)
    return out


def study(p,source):
    if p['protocol_id']!='tdx-cash-revenue-comparison-v1' or (p['baseline'],p['candidate'])!=MODELS:raise ValueError('unsupported comparison')
    if not p['samples'] or any(s['split']!='development' for s in p['samples']) or len({s['code'] for s in p['samples']})!=len(p['samples']):raise ValueError('development samples required')
    artifacts={a['file']:a for a in source['artifacts']};index=defaultdict(list)
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    if any(p['gates'][k] is not True for k in ('wape_nonworse','cash_proxy_nonworse','leave_one_company_out_nonworse')):raise ValueError('required gate disabled')
    rows=[]
    for sample in p['samples']:
        for origin in p['origins']:
            end=date.fromisoformat(origin);cutoff=f'{end.year}-09-01T00:00:00+08:00';target=end.replace(year=end.year+1)
            if (end.month,end.day)!=(6,30) or at(cutoff)>=at(p['evaluation_as_of']):raise ValueError('invalid chronology')
            row=dict(code=sample['code'],origin=origin,target=target.isoformat(),forecast_as_of=cutoff,status='blocked',baseline_status='blocked',actual_fcff=None);rows.append(row)
            try:
                now=observation(index,artifacts,sample['code'],end,cutoff);prior=observation(index,artifacts,sample['code'],end.replace(year=end.year-1),cutoff)
                rev=Decimal(now['revenue_cny']);capex=Decimal(now['capex_cny'])
                ocf=(Decimal(now['ocf_cny'])+Decimal(prior['ocf_cny'])*rev/Decimal(prior['revenue_cny']))/2
                forecasts={MODELS[0]:dict(ocf=str(ocf),capex=str(capex),cash_proxy=str(ocf-capex),revenue=str(rev))}
                row.update(current=now,prior=prior,forecasts=forecasts)
                try:
                    revenue,evidence=revenue_forecast(p,index,artifacts,sample['code'],end,cutoff)
                    candidate=ocf*revenue/rev
                    forecasts[MODELS[1]]=dict(ocf=str(candidate),capex=str(capex),cash_proxy=str(candidate-capex),revenue=str(revenue))
                    row['revenue_evidence']=evidence
                except (ValueError,KeyError,ArithmeticError) as exc:row['candidate_reason']=str(exc)
                actual=observation(index,artifacts,sample['code'],target,p['evaluation_as_of'])
                row.update(baseline_status='evaluated',actual=actual|dict(ocf=actual['ocf_cny'],cash_proxy=str(Decimal(actual['ocf_cny'])-Decimal(actual['capex_cny']))))
                row['status']='evaluated' if MODELS[1] in forecasts else 'blocked_candidate'
            except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    summary=metrics(rows);by_origin={o:metrics([r for r in rows if r['origin']==o]) for o in p['origins']};g=p['gates'];b,c=(summary['ocf'][m] for m in MODELS)
    n=sum(r['baseline_status']=='evaluated' for r in rows)
    def nonworse(m,kind,key):
        a,z=(m[kind][model][key] for model in MODELS)
        return a is not None and z is not None and z<=a
    checks=dict(minimum_pairs=c['n']>=g['minimum_pairs'],coverage_retention=n>0 and c['n']/n>=g['minimum_cash_baseline_retention'],
        primary_improvement=b['mae_pct_actual_revenue'] is not None and b['mae_pct_actual_revenue']>0 and c['mae_pct_actual_revenue']<=b['mae_pct_actual_revenue']*(1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=nonworse(summary,'ocf','wape_pct'),cash_proxy_nonworse=all(nonworse(summary,'cash_proxy',k) for k in ('mae_pct_actual_revenue','wape_pct')),
        each_origin_nonworse=all(m['ocf'][MODELS[1]]['n']>0 and m['ocf'][MODELS[1]]['mae_pct_actual_revenue']<=m['ocf'][MODELS[0]]['mae_pct_actual_revenue']*g['maximum_each_origin_primary_ratio'] for m in by_origin.values()))
    codes={r['code'] for r in rows if r['status']=='evaluated'}
    checks['leave_one_company_out_nonworse']=bool(codes) and all(nonworse(metrics([r for r in rows if r['code']!=code]),'ocf','mae_pct_actual_revenue') for code in codes)
    return dict(protocol_id=p['protocol_id'],candidates=len(rows),baseline_evaluable=n,statuses=dict(Counter(r['status'] for r in rows)),summary=summary,by_origin=by_origin,decision=dict(passed=all(checks.values()),checks=checks),results=rows,boundary=p['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();p=json.loads(raw);digest=lambda b:hashlib.sha256(b).hexdigest()
        parent=(ROOT/p['parent_protocol']).read_bytes();data=(ROOT/p['snapshot']).read_bytes();source=json.loads(data)
        if digest(parent)!=p['parent_protocol_sha256'] or digest(data)!=p['snapshot_sha256'] or source['study_sha256']!=digest(parent):raise ValueError('parent/source binding differs')
        parent_data=json.loads(parent)
        if p['samples']!=[s for s in parent_data['samples'] if s['split']=='development']:raise ValueError('development sample differs')
        out=study(p,source);out['evidence']=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),
            helpers={name:digest((Path(__file__).resolve().parents[1]/name).read_bytes()) for name in ['tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','tools/backtest_tdx_operating_cash.py','tools/backtest_tdx_capex.py','tools/audit_tdx_reinvestment.py','data_sources/alphalake.py']})
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
