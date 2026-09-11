"""营运现金调节项一年预测复验；不将报表调节贡献转换为分类营运资本或FCFF。"""
import argparse
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import mean

from tools.audit_tdx_reinvestment import component
from tools.backtest_tdx_history import at,available,quarter_periods,window,value

MODELS=('repeat_latest','zero_forecast','half_latest')


def revenue_window(index,artifacts,code,end,cutoff):
    rows={}
    for period in quarter_periods(end):
        matches=index.get((code,period),[])
        if len(matches)!=1:raise ValueError('revenue missing/duplicate identity: '+period)
        row=matches[0];a=artifacts[row['artifact']]
        if a['report_period']!=period or available(row,a)>at(cutoff):raise ValueError('revenue period/cutoff differs')
        rows[period]=row
    revenue,refs=window(rows,end,'FN230')
    if revenue<=0:raise ValueError('nonpositive revenue')
    return revenue,refs,rows


def observation(index,artifacts,code,end,cutoff):
    parts={f:component(index,artifacts,code,end,f,cutoff) for f in ('FN146','FN147','FN148')}
    if any(p['status']=='blocked' for p in parts.values()):raise ValueError('working cash component blocked: '+str({f:p['issues'] for f,p in parts.items() if p['issues']}))
    revenue,refs,_=revenue_window(index,artifacts,code,end,cutoff)
    amount=sum((Decimal(p['value_cny']) for p in parts.values()),Decimal(0))
    return dict(period=end.isoformat(),working_cash_cny=str(amount),revenue_cny=str(revenue),components=parts,revenue_inputs=refs)


def inventory_signal(index,artifacts,code,end,cutoff):
    history=[]
    for period in (end,end.replace(year=end.year-1)):
        revenue,refs,rows=revenue_window(index,artifacts,code,period,cutoff);row=rows[period.isoformat()];inventory=value(row,'FN17')
        if inventory<=0:raise ValueError('nonpositive inventory; zero ambiguous')
        history.append(dict(period=period.isoformat(),net_inventory_cny=str(inventory),revenue_cny=str(revenue),revenue_inputs=refs,
                            inventory_input=dict(field='FN17',artifact=row['artifact'],source_bits=row['bits']['FN17'])))
    current,prior=history
    signal=(Decimal(current['net_inventory_cny'])-Decimal(prior['net_inventory_cny'])*Decimal(current['revenue_cny'])/Decimal(prior['revenue_cny']))/2
    return signal,dict(history=history,inventory_cash_forecast_cny=str(signal),receivables_cash_forecast_cny='0',payables_cash_forecast_cny='0',
                      boundary='net_inventory_intensity_predictor_not_gross_inventory_or_accounting_identity')


def evaluate(p,source,split):
    artifacts={a['file']:a for a in source['artifacts']};index=defaultdict(list)
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    for r in source['records']:index[(r['code'],r['period'])].append(r)
    samples=[s for s in p['samples'] if s['split']==split]
    if not samples or len({s['code'] for s in samples})!=len(samples):raise ValueError('empty/duplicate sample')
    result=[]
    for s in samples:
        for origin in p['origins']:
            end=date.fromisoformat(origin);target=end.replace(year=end.year+1);cutoff=f'{end.year}-09-01T00:00:00+08:00'
            if origin[4:]!='-06-30' or at(cutoff)>=at(p['evaluation_as_of']):raise ValueError('invalid H1 chronology')
            row=dict(code=s['code'],origin=origin,target=target.isoformat(),forecast_as_of=cutoff,status='blocked',actual_fcff=None);result.append(row)
            try:
                base=observation(index,artifacts,s['code'],end,cutoff);amount=Decimal(base['working_cash_cny'])
                predictions=dict(repeat_latest=amount,zero_forecast=Decimal(0),half_latest=amount/2)
                if p['candidate']=='inventory_intensity_half':
                    signal,driver=inventory_signal(index,artifacts,s['code'],end,cutoff);row['driver']=driver
                    if p['protocol_id']=='tdx-working-cash-forecast-v4' and any(Decimal(h['net_inventory_cny'])>Decimal(h['revenue_cny']) for h in driver['history']):
                        row['driver']={k:driver[k] for k in ('history','boundary')}
                        raise ValueError('outside_inventory_scope')
                    predictions.pop('half_latest');predictions['inventory_intensity_half']=signal
                row.update(base=base,forecasts_cny={m:str(v) for m,v in predictions.items()})
                actual=observation(index,artifacts,s['code'],target,p['evaluation_as_of']);a=Decimal(actual['working_cash_cny']);rev=Decimal(actual['revenue_cny'])
                row.update(status='evaluated',actual=actual,errors={m:dict(cny=str(v-a),pct_actual_revenue=float(100*(v-a)/rev)) for m,v in predictions.items()})
            except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    return result


def metrics(rows,models=MODELS):
    valid=[r for r in rows if r['status']=='evaluated'];denominator=sum((abs(Decimal(r['actual']['working_cash_cny'])) for r in valid),Decimal(0))
    return dict(candidates=len(rows),statuses=dict(Counter(r['status'] for r in rows)),models={m:dict(n=len(valid),
        mae_pct_actual_revenue=mean(abs(r['errors'][m]['pct_actual_revenue']) for r in valid) if valid else None,
        wape_pct=float(100*sum((abs(Decimal(r['errors'][m]['cny'])) for r in valid),Decimal(0))/denominator) if denominator else None) for m in models})


def component_diagnostics(rows):
    """事后开发诊断，不选择分项倍率，也不扩大原共同可评价集合。"""
    valid=[r for r in rows if r['status']=='evaluated'];fields=('FN146','FN147','FN148');parts={};gross={m:Decimal(0) for m in MODELS};net={m:Decimal(0) for m in MODELS}
    for field in fields:
        projected=[];same=opposite=zeros=0
        for row in valid:
            base=Decimal(row['base']['components'][field]['value_cny']);actual=Decimal(row['actual']['components'][field]['value_cny']);rev=Decimal(row['actual']['revenue_cny'])
            if base*actual>0:same+=1
            elif base*actual<0:opposite+=1
            else:zeros+=1
            predictions=dict(repeat_latest=base,zero_forecast=Decimal(0),half_latest=base/2)
            errors={m:dict(cny=str(v-actual),pct_actual_revenue=float(100*(v-actual)/rev)) for m,v in predictions.items()}
            projected.append(dict(status='evaluated',actual=dict(working_cash_cny=str(actual)),errors=errors))
            for m in MODELS:gross[m]+=abs(predictions[m]-actual)/rev
        parts[field]=dict(metrics=metrics(projected),direction=dict(same=same,opposite=opposite,either_zero=zeros))
    for row in valid:
        for m in MODELS:net[m]+=abs(Decimal(row['forecasts_cny'][m])-Decimal(row['actual']['working_cash_cny']))/Decimal(row['actual']['revenue_cny'])
    return dict(candidates=len(rows),statuses=dict(Counter(r['status'] for r in rows)),components=parts,
                cancellation={m:dict(sum_component_absolute_error_pct_revenue=float(100*gross[m]/len(valid)) if valid else None,
                    net_absolute_error_pct_revenue=float(100*net[m]/len(valid)) if valid else None,
                    offset_fraction=float(1-net[m]/gross[m]) if gross[m] else None) for m in MODELS},
                boundary='posthoc_development_diagnostic_not_candidate_selection; same complete-case cohort; sign persistence is not prediction validity')


def business_diagnostics(p,rows):
    samples={s['code']:s for s in p['samples'] if s['split']=='development'}
    if any(r['code'] not in samples for r in rows):raise ValueError('business diagnosis is development only')
    models=(p['baseline'],p['benchmark'],p['candidate']);groups={}
    for industry in sorted({s['stratum'] for s in samples.values()}):
        codes={c for c,s in samples.items() if s['stratum']==industry};selected=[r for r in rows if r['code'] in codes]
        groups[industry]=dict(companies=len(codes),evaluated_companies=len({r['code'] for r in selected if r['status']=='evaluated'}),
            summary=metrics(selected,models),by_origin={o:metrics([r for r in selected if r['origin']==o],models) for o in p['origins']},
            refusals=dict(Counter(r['reason'] for r in selected if r['status']=='blocked')))
    return dict(development_companies=len(samples),industries=len(groups),by_industry=groups,
                largest_errors=[dict(sample=samples[r['code']],result=r) for r in sorted([r for r in rows if r['status']=='evaluated'],key=lambda r:abs(r['errors'][p['candidate']]['pct_actual_revenue']),reverse=True)[:5]],
                refusals=dict(Counter(r['reason'] for r in rows if r['status']=='blocked')),
                boundary='posthoc_all_development_industries_not_industry_selection; current industry proxy, not historical classification; no adoption gate or holdout score')


def study(p,source,phase):
    candidate={'tdx-working-cash-forecast-v1':'half_latest','tdx-working-cash-forecast-v2':'inventory_intensity_half','tdx-working-cash-forecast-v3':'inventory_intensity_half','tdx-working-cash-forecast-v4':'inventory_intensity_half'}.get(p['protocol_id'])
    models=('repeat_latest','zero_forecast',candidate)
    if candidate is None or source['contract_version']!='tdx-history-source-v1' or (p['baseline'],p['benchmark'],p['candidate'])!=models:raise ValueError('unsupported study')
    if candidate=='inventory_intensity_half' and (p['driver_policy']['weight']!=.5 or p['driver_policy']['receivables_forecast']!=0 or p['driver_policy']['payables_forecast']!=0):raise ValueError('unsupported inventory driver policy')
    if any(p['gates'][key] is not True for key in ('require_wape_nonworse','require_zero_benchmark_nonworse','require_leave_one_company_out_nonworse')):raise ValueError('required comparison gate disabled')
    if phase not in ('development','holdout'):raise ValueError('invalid phase')
    scoped=p['protocol_id']=='tdx-working-cash-forecast-v4'
    if scoped and (p['scope_policy']['id']!='inventory_within_annual_revenue_v1' or type(p['scope_policy']['maximum_net_inventory_to_ttm_revenue']) is not int or p['scope_policy']['maximum_net_inventory_to_ttm_revenue']!=1 or p['gates']['minimum_retained_evaluable_fraction']!=.8):raise ValueError('unsupported scope policy')
    rows=evaluate(p,source,phase);summary=metrics(rows,models);s=summary['models'];b=s['repeat_latest'];c=s[candidate];z=s['zero_forecast'];g=p['gates']
    sufficient=b['n']>=g['minimum_pairs'] and b['mae_pct_actual_revenue'] is not None and b['mae_pct_actual_revenue']>0
    by_origin={o:metrics([r for r in rows if r['origin']==o],models) for o in p['origins']}
    checks=dict(minimum_pairs=sufficient,
        primary_improvement=sufficient and c['mae_pct_actual_revenue']<=b['mae_pct_actual_revenue']*(1-g['minimum_primary_improvement_fraction']),
        wape_nonworse=c['wape_pct'] is not None and b['wape_pct'] is not None and c['wape_pct']<=b['wape_pct'],
        zero_benchmark_nonworse=c['mae_pct_actual_revenue'] is not None and c['mae_pct_actual_revenue']<=z['mae_pct_actual_revenue'] and c['wape_pct'] is not None and c['wape_pct']<=z['wape_pct'],
        each_period_nonworse=all(v['models'][candidate]['n']>0 and v['models'][candidate]['mae_pct_actual_revenue']<=v['models']['repeat_latest']['mae_pct_actual_revenue']*g['maximum_each_period_primary_ratio'] for v in by_origin.values()))
    leave=[]
    for code in sorted({r['code'] for r in rows if r['status']=='evaluated'}):
        m=metrics([r for r in rows if r['code']!=code],models)['models'];leave.append(m[candidate]['n']>0 and m[candidate]['mae_pct_actual_revenue']<=m['repeat_latest']['mae_pct_actual_revenue'])
    checks['leave_one_company_out_nonworse']=bool(leave) and all(leave)
    scope={}
    if scoped:
        reference=evaluate(dict(p,protocol_id='tdx-working-cash-forecast-v3'),source,phase)
        original={(r['code'],r['origin']) for r in reference if r['status']=='evaluated'}
        refused=[r for r in rows if r.get('reason')=='outside_inventory_scope']
        retained=b['n']/len(original) if original else None
        checks['minimum_retained_evaluable_fraction']=retained is not None and retained>=g['minimum_retained_evaluable_fraction']
        scope=dict(scope_evaluation=dict(unrestricted_summary=metrics(reference,models),retained_evaluable_fraction=retained,
            scope_refusals=len(refused),lost_evaluable_pairs=sum((r['code'],r['origin']) in original for r in refused),
            by_origin={o:dict(unrestricted=metrics([r for r in reference if r['origin']==o],models),scope_refusals=sum(r['origin']==o for r in refused)) for o in p['origins']}))
    return dict(**scope,protocol_id=p['protocol_id'],phase=phase,summary=summary,by_origin=by_origin,decision=dict(passed=all(checks.values()),checks=checks),results=rows,boundary=p['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('snapshot',type=Path);parser.add_argument('--phase',choices=['development','holdout'],required=True);parser.add_argument('--selection',type=Path);parser.add_argument('--diagnose-components',action='store_true');parser.add_argument('--diagnose-business',action='store_true');args=parser.parse_args()
    try:
        if args.diagnose_components and args.phase!='development':raise ValueError('component diagnosis is development only')
        if args.diagnose_business and args.phase!='development':raise ValueError('business diagnosis is development only')
        raw=args.protocol.read_bytes();data=args.snapshot.read_bytes();p=json.loads(raw);source=json.loads(data);digest=lambda b:hashlib.sha256(b).hexdigest()
        if args.diagnose_components and p['protocol_id']!='tdx-working-cash-forecast-v1':raise ValueError('component diagnosis requires original lagged models')
        if p['protocol_id']=='tdx-working-cash-forecast-v4' and (not p.get('source_protocol_sha256') or not p.get('source_snapshot_sha256')):raise ValueError('scope study source binding required')
        if source['study_sha256']!=p.get('source_protocol_sha256',digest(raw)) or ('source_snapshot_sha256' in p and p['source_snapshot_sha256']!=digest(data)):raise ValueError('source/protocol hash differs')
        evidence=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(data),code_sha256=digest(Path(__file__).read_bytes()),history_sha256=digest(Path(__file__).with_name('backtest_tdx_history.py').read_bytes()),component_sha256=digest(Path(__file__).with_name('audit_tdx_reinvestment.py').read_bytes()))
        if args.phase=='holdout':
            if args.selection is None:raise ValueError('passing development receipt required')
            selected=json.loads(args.selection.read_bytes());dev=study(p,source,'development')
            if selected['evidence']!=evidence or any(selected[k]!=dev[k] for k in ('decision','summary','by_origin')) or not dev['decision']['passed']:raise ValueError('development selection failed or differs')
        result=study(p,source,args.phase);result['evidence']=evidence
        if args.diagnose_components:result['component_diagnostics']=dict(overall=component_diagnostics(result['results']),by_origin={o:component_diagnostics([r for r in result['results'] if r['origin']==o]) for o in p['origins']})
        if args.diagnose_business:result['business_diagnostics']=business_diagnostics(p,result['results'])
        if p['protocol_id'] in ('tdx-working-cash-forecast-v3','tdx-working-cash-forecast-v4') and args.phase=='development':
            original=set(p['sampling']['original_development_codes']);models=(p['baseline'],p['benchmark'],p['candidate'])
            result['development_cohorts']={name:metrics([r for r in result['results'] if (r['code'] in original)==is_original],models) for name,is_original in [('original60',True),('additional600',False)]}
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
