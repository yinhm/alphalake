"""预先固定候选的多起点复验；先开发选择，再独立读取留出结果。"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
from statistics import median
from data_sources.alphalake_calibration import weighted_median

from tools.backtest_tdx_history import at, available, operating, error, metrics, run

CONTRACT='tdx-multi-origin-v1'
DEFAULT_WEIGHTS={'half_growth':dict(growth_weight=.5,prior_margin_weight=0),
                 'half_margin':dict(growth_weight=1,prior_margin_weight=.5),
                 'half_both':dict(growth_weight=.5,prior_margin_weight=.5)}


def digest(raw):return hashlib.sha256(raw).hexdigest()


def evaluate(protocol,snapshot,split,origins,models,calibrations=None):
    samples=[s for s in protocol['samples'] if s['split']==split]
    output=[]
    for origin in origins:
        end=date.fromisoformat(origin);first=date(end.year-1,1,1)
        study=dict(study_id=protocol['protocol_id']+':'+origin,origin=origin,target=end.replace(year=end.year+1).isoformat(),
                   forecast_as_of=f'{end.year}-09-01T00:00:00+08:00',evaluation_as_of=protocol['evaluation_as_of'],
                   policy=protocol['base_policy']|dict(approved_report_period=origin),samples=samples)
        # 对齐生产导出上年年初起的范围；目标期未来数据只用于实际，不扩大起点同比输入。
        source=snapshot|dict(records=[r for r in snapshot['records'] if first<=date.fromisoformat(r['period'])<=date.fromisoformat(study['target'])])
        baseline=run(study,source)
        artifacts={a['file']:a for a in source['artifacts']}
        for row in baseline['results']:
            result={k:row[k] for k in ('code','split','stratum','status')};result['origin']=origin;output.append(result)
            if row['status']!='evaluated':
                result['reason']=row.get('reason');result['diagnostics']=row['diagnostics'];continue
            try:
                calibration=calibrations[origin] if calibrations is not None else None
                if calibration and calibration['status']!='fitted':raise ValueError('insufficient past calibration pairs')
                # 只读取起点前且FN314不晚于起点的记录；不使用未来年度利润率。
                history={r['period']:r for r in source['records'] if r['code']==row['code'] and date.fromisoformat(r['period'])<=end
                         and available(r,artifacts[r['artifact']])<=at(study['forecast_as_of'])}
                prior=operating(history,date(end.year-1,12,31))
                if prior['revenue']<=0:raise ValueError('positive prior full-year revenue required')
                base=row['base'];margin=base['ebit']/base['revenue'];prior_margin=prior['ebit']/prior['revenue']
                growth=row['rule_evidence']['clipped_scenario_growth']
                forecasts={}
                for model in models:
                    if model=='current_rule':forecasts[model]=row['forecast'];continue
                    if model=='zero_growth':forecasts[model]={k:base[k] for k in ('revenue','ebit')};continue
                    weights=protocol.get('candidate_weights',DEFAULT_WEIGHTS)[model]
                    g=growth*weights['growth_weight'];w=weights['prior_margin_weight']
                    m=(1-w)*margin+w*prior_margin
                    if 'margin_change_cap' in weights:
                        cap=weights['margin_change_cap'];m=margin+max(-cap,min(cap,m-margin))
                    if 'calibration_weight' in weights:
                        if 'ebit_scale' in calibration:m*=1+weights['calibration_weight']*(calibration['ebit_scale']-1)
                        else:m-=weights['calibration_weight']*calibration['margin_shift']
                    revenue=base['revenue']*(1+g);forecasts[model]=dict(revenue=revenue,ebit=revenue*m)
                result.update(actual=row['actual'],base=row['base'],prior_full_year=prior,forecasts=forecasts,
                              profit_scope=row['profit_scope'],financial_scope_flags=row['financial_scope_flags'],
                              rule_evidence=row['rule_evidence'],errors={m:error(f,row['actual']) for m,f in forecasts.items()})
            except (ValueError,KeyError,ArithmeticError) as exc:
                # 所有候选在同一可评价集合比较，不只丢弃某个候选的坏案例。
                result.update(status='blocked',reason=str(exc))
    return output


def fit_calibration(protocol,snapshot,origin):
    """只用当前起点前已结束的开发窗口；源版本的后修订风险仍保留。"""
    end=date.fromisoformat(origin);previous=end.replace(year=end.year-1).isoformat()
    training={k:v for k,v in protocol.items() if k!='calibration'}
    training['evaluation_as_of']=f'{end.year}-09-01T00:00:00+08:00'
    rows=evaluate(training,snapshot,'development',[previous],('current_rule','zero_growth'))
    errors=[r['errors']['current_rule']['margin_error_pp']/100 for r in rows if r['status']=='evaluated']
    config=protocol['calibration'];raw=median(errors) if errors else None
    scaled=config['method']=='previous_origin_development_weighted_ebit_scale'
    if errors and config['method']!='previous_origin_development_median_margin_error':
        observations=[]
        for row in rows:
            if row['status']!='evaluated':continue
            f=row['forecasts']['current_rule'];a=row['actual']
            observations.append((a['ebit']/f['ebit'],f['ebit']/a['revenue']) if scaled else
                                ((f['ebit']-a['ebit'])/f['revenue'],f['revenue']/a['revenue']))
        raw=weighted_median(observations)
    ready=len(errors)>=config['minimum_training_pairs']
    if scaled:
        fitted=dict(weighted_ebit_scale=raw,ebit_scale=max(config['minimum_ebit_scale'],min(config['maximum_ebit_scale'],raw)) if ready else None)
    else:
        cap=config['maximum_absolute_margin_shift']
        fitted={('median_margin_error' if config['method']=='previous_origin_development_median_margin_error' else 'weighted_ebit_margin_error'):raw,
                'margin_shift':max(-cap,min(cap,raw)) if ready else None}
    return dict(origin=previous,target=origin,evaluation_as_of=training['evaluation_as_of'],
                status='fitted' if ready else 'insufficient_training_pairs',training_pairs=len(errors),
                **fitted,
                summary=metrics(rows),results=rows)


def summarize(rows,models):
    return dict(total=metrics(rows,models),by_origin={p:metrics([r for r in rows if r['origin']==p],models) for p in sorted({r['origin'] for r in rows})},
                by_profit_scope={s:metrics([r for r in rows if r.get('profit_scope','not_evaluated')==s],models)
                                 for s in ('no_financial_fields_detected','financial_fields_present','not_evaluated')})


def gates(summary,model,rule,rows=()):
    current=summary['total']['models']['current_rule'];candidate=summary['total']['models'][model]
    checks=dict(minimum_pairs=candidate['ebit_n']>=rule['minimum_pairs'])
    def bounded(field,factor,left=candidate,right=current):
        return left[field] is not None and right[field] is not None and left[field]<=right[field]*factor
    checks['primary_improvement']=(current['ebit_mae_pct_actual_revenue'] or 0)>0 and bounded('ebit_mae_pct_actual_revenue',1-rule['minimum_primary_improvement_fraction'])
    checks['revenue_wape']=bounded('revenue_wape_pct',rule['maximum_revenue_wape_ratio'])
    if 'maximum_revenue_median_ape_ratio' in rule:checks['revenue_median_ape']=bounded('revenue_median_ape_pct',rule['maximum_revenue_median_ape_ratio'])
    for origin,group in summary['by_origin'].items():
        checks['period_'+origin]=bounded('ebit_mae_pct_actual_revenue',rule['maximum_each_period_primary_ratio'],group['models'][model],group['models']['current_rule'])
    if rule.get('require_leave_one_company_out_nonworse'):
        codes=sorted({r['code'] for r in rows})
        checks['company_removal_available']=bool(codes)
        for code in codes:
            group=metrics([r for r in rows if r['code']!=code],('current_rule',model))['models']
            checks['without_'+code]=bounded('ebit_mae_pct_actual_revenue',1,group[model],group['current_rule'])
    return dict(passed=all(checks.values()),checks=checks)


def study(protocol,snapshot,phase,selection=None):
    weights=protocol.get('candidate_weights',DEFAULT_WEIGHTS)
    candidates=('current_rule','zero_growth',*weights)
    if tuple(protocol['candidates'])!=candidates or len(set(candidates))!=len(candidates):raise ValueError('candidate definitions differ')
    for values in weights.values():
        required={'growth_weight','prior_margin_weight'}
        if not required<=set(values)<=required|{'margin_change_cap','calibration_weight'}:
            raise ValueError('invalid candidate parameters')
        for key,value in values.items():
            lower=-1 if key=='prior_margin_weight' else 0
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not lower<=value<=1:
                raise ValueError('candidate parameters outside finite bounds')
        if values['prior_margin_weight']<0 and not values.get('margin_change_cap',0)>0:
            raise ValueError('margin extrapolation requires positive change cap')
    config=protocol.get('calibration')
    if config is not None:
        if (config.get('method') not in ('previous_origin_development_median_margin_error','previous_origin_development_weighted_ebit_error','previous_origin_development_weighted_ebit_scale')
                or config.get('origin_lag_years')!=1 or config.get('training_split')!='development'
                or config.get('training_evaluation_cutoff')!='current forecast cutoff'
                or type(config.get('minimum_training_pairs')) is not int or config['minimum_training_pairs']<1):
            raise ValueError('invalid past calibration policy')
        if config['method']=='previous_origin_development_weighted_ebit_scale':
            low,high=config.get('minimum_ebit_scale'),config.get('maximum_ebit_scale')
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in (low,high)) or not 0<low<=1<=high<=2:
                raise ValueError('invalid EBIT scale bounds')
        else:
            cap=config.get('maximum_absolute_margin_shift')
            if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not 0<cap<=1:raise ValueError('invalid margin shift bound')
        if any(v.get('growth_weight')!=1 or v.get('prior_margin_weight')!=0 or 'calibration_weight' not in v for v in weights.values()):
            raise ValueError('calibration candidates must keep growth and base margin')
    elif any('calibration_weight' in v for v in weights.values()):raise ValueError('missing calibration policy')
    if phase=='development':
        models=candidates;split='development';origins=protocol['selection']['periods']
    elif phase=='holdout':
        if not selection or selection['phase']!='development':raise ValueError('development selection required')
        selected=selection['selection']['model']
        if selected is not None and (selected not in candidates[2:] or not selection['selection']['gates'][selected]['passed']):raise ValueError('invalid selected candidate')
        if 'fixed_validation_model' in protocol and selected!=protocol['fixed_validation_model']:
            raise ValueError('selection differs from frozen replication model')
        models=tuple(dict.fromkeys(['current_rule','zero_growth']+([selected] if selected else [])));split='holdout';origins=protocol['origins']
    else:raise ValueError('unsupported phase')
    calibrations={origin:fit_calibration(protocol,snapshot,origin) for origin in origins} if config is not None else None
    rows=evaluate(protocol,snapshot,split,origins,models,calibrations);summary=summarize(rows,models)
    result=dict(contract_version=CONTRACT,protocol_id=protocol['protocol_id'],phase=phase,summary=summary,results=rows,
                boundary=('简化源数据回溯、一个目的样本的多个起点；非严格PIT或DCF公允价值证明；'
                          +('追加2025时间窗口；同公司旧窗口已评分，不称新公司独立验证' if '2025-06-30' in protocol['origins'] else '2025已知结果未用于本轮选择或验证')))
    if calibrations is not None:result['calibration_training']=calibrations
    if phase=='development':
        verdicts={m:gates(summary,m,protocol['selection'],rows) for m in candidates[2:]}
        passed=[m for m in candidates[2:] if verdicts[m]['passed']]
        chosen=min(passed,key=lambda m:summary['total']['models'][m]['ebit_mae_pct_actual_revenue']) if passed else None
        result['selection']=dict(model=chosen,gates=verdicts,status='selected_for_validation' if chosen else 'no_candidate_passed_development')
    else:
        chosen=selection['selection']['model']
        result['validation']=dict(model=chosen,verdict=gates(summary,chosen,protocol['validation'],rows) if chosen else None,
                                  status='evaluated' if chosen else 'baseline_only_no_selected_candidate')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('protocol',type=Path);p.add_argument('snapshot',type=Path)
    p.add_argument('--phase',choices=['development','holdout'],required=True);p.add_argument('--selection',type=Path);a=p.parse_args()
    try:
        raw=a.protocol.read_bytes();source=a.snapshot.read_bytes();protocol=json.loads(raw);snapshot=json.loads(source)
        if snapshot['study_sha256']!=protocol.get('source_study_sha256',digest(raw)):raise ValueError('source study differs from protocol')
        if 'source_snapshot_sha256' in protocol and protocol['source_snapshot_sha256']!=digest(source):raise ValueError('source snapshot differs from protocol')
        evidence=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(source),
                      rule_code_sha256=digest((Path(__file__).resolve().parents[1]/'data_sources/alphalake.py').read_bytes()),
                      calibration_code_sha256=digest((Path(__file__).resolve().parents[1]/'data_sources/alphalake_calibration.py').read_bytes()),
                      base_backtest_sha256=digest((Path(__file__).parent/'backtest_tdx_history.py').read_bytes()),code_sha256=digest(Path(__file__).read_bytes()))
        selection=json.loads(a.selection.read_bytes()) if a.selection else None
        if selection and selection['evidence']!=evidence:raise ValueError('selection code, source or protocol differs')
        if selection and selection['selection']!=study(protocol,snapshot,'development')['selection']:
            raise ValueError('saved selection does not reproduce development decision')
        result=study(protocol,snapshot,a.phase,selection);result['evidence']=evidence
        print(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(contract_version=CONTRACT,status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
