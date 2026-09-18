"""在冻结开发预测上校准增长输入，复用指标并引用原始分量。"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean

from tools.backtest_tdx_history import error
from tools.backtest_tdx_normalized_margin import metrics as base_metrics
from tools.validate_tdx_zero_growth import verify_sampling

ROOT=Path(__file__).resolve().parents[3]
MODELS=('flat_first_five','half_growth','zero_growth')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def forecast(base,growth,horizon,growth_years=3):
    revenue=base['revenue'];margin=base['ebit']/revenue;annual=[]
    for year in range(horizon):
        g=growth*.5 if year<growth_years else 0
        revenue*=1+g;annual.append(dict(revenue=revenue,ebit=revenue*margin,growth=g))
    return dict(revenue=revenue,ebit=revenue*margin,annual=annual)


def metrics(rows,models=MODELS):
    result=base_metrics(rows,models);valid=[r for r in rows if r['status']=='evaluated']
    for m in models:result['models'][m]['revenue_mae_pct']=mean(abs(r['errors'][m]['revenue_error_pct']) for r in valid) if valid else None
    return result


def run(p):
    first=p['protocol_id']=='tdx-first-year-half-growth-v1'
    models=(MODELS[0],'first_year_half_growth' if first else MODELS[1],MODELS[2])
    if p['protocol_id'] not in ('tdx-half-growth-v1','tdx-first-year-half-growth-v1') or p['growth_multiplier']!=.5 or (p['baseline'],p['candidate'],p['benchmark'])!=models or (first and p['growth_years']!=1):raise ValueError('unsupported half growth')
    if first and digest((ROOT/p['previous_comparison']).read_bytes())!=p['previous_comparison_sha256']:raise ValueError('previous comparison hash differs')
    verify_sampling(p);data={}
    for key in ('parent_protocol','parent_result'):
        raw=(ROOT/p[key]).read_bytes()
        if digest(raw)!=p[key+'_sha256']:raise ValueError(key+' hash differs')
        data[key]=json.loads(gzip.decompress(raw) if key=='parent_result' else raw)
    parent,original=data['parent_protocol'],data['parent_result']
    if any(p[k]!=parent[k] for k in ('windows','evaluation_as_of')) or [s for s in p['samples'] if s['split']=='development']!=[s for s in parent['samples'] if s['split']=='development']:raise ValueError('development scope differs')
    if original['phase']!='development' or original['evidence']['protocol_sha256']!=p['parent_protocol_sha256'] or original['evidence']['snapshot_sha256']!=parent['development_snapshot_sha256']:raise ValueError('parent evidence binding differs')
    for name in ('tools/backtest_tdx_multiyear_growth.py','tools/backtest_tdx_cash_revenue.py','tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','data_sources/alphalake.py','engine/module_4_dcf.py'):
        if digest((ROOT/'valuation/backend'/name).read_bytes())!=original['evidence']['helpers'][name]:raise ValueError('parent forecast implementation differs')
    return compare(p,original,'development')


def compare(p,original,phase):
    first=p['protocol_id']=='tdx-first-year-half-growth-v1'
    if phase not in ('development','holdout') or (phase=='holdout' and not first):raise ValueError('unsupported phase')
    models=(MODELS[0],'first_year_half_growth' if first else MODELS[1],MODELS[2])
    rows=[]
    for original_row in original['results']:
        r=original_row.copy();rows.append(r)
        if 'forecasts' in r:
            r['forecasts']={m:r['forecasts'][m] for m in (models[0],models[2])}
            r['forecasts'][models[1]]=forecast(r['base'],r['rule_evidence']['clipped_scenario_growth'],r['horizon'],growth_years=1 if first else 3)
        if r['status']=='evaluated':r['errors']={m:error(f,r['actual']) for m,f in r['forecasts'].items()}
    summary=metrics(rows,models);by_horizon={str(h):metrics([r for r in rows if r['horizon']==h],models) for h in (1,2,3)}
    by_window={w['origin']+':'+str(w['horizon']):metrics([r for r in rows if (r['origin'],r['horizon'])==(w['origin'],w['horizon'])],models) for w in p['windows']}
    g=p['gates'];codes={r['code'] for r in rows if r['status']=='evaluated'}
    if any(v is not True for k,v in g.items() if k.startswith('require_')):raise ValueError('required gate disabled')
    def nonworse(m,key,baseline=models[0],ratio=1):
        a=m['models'][baseline][key];b=m['models'][models[1]][key]
        return a is not None and b is not None and b<=a*ratio
    enough=summary['models'][models[1]]['revenue_n']>=g[phase+'_minimum_pairs'] and len(codes)>=g[phase+'_minimum_companies'];key='revenue_mae_pct'
    checks=dict(minimum_sample=enough,primary_improvement=enough and summary['models'][models[0]][key]>0 and nonworse(summary,key,ratio=1-g['minimum_primary_improvement_fraction']),revenue_wape_nonworse=nonworse(summary,'revenue_wape_pct'),ebit_nonworse=all(nonworse(summary,k) for k in ('ebit_mae_pct_actual_revenue','ebit_wape_pct')),zero_growth_revenue_nonworse=all(nonworse(summary,k,models[2]) for k in (key,'revenue_wape_pct')),each_horizon_nonworse=all(nonworse(m,key) for m in by_horizon.values()),each_window_within_tolerance=all(nonworse(m,key,ratio=g['maximum_each_window_primary_ratio']) for m in by_window.values()),leave_one_company_out_nonworse=bool(codes) and all(nonworse(metrics([r for r in rows if r['code']!=c],models),key) for c in codes))
    return dict(protocol_id=p['protocol_id'],phase=phase,summary=summary,by_horizon=by_horizon,by_window=by_window,evaluated_companies=len(codes),decision=dict(passed=all(checks.values()),checks=checks),results=[{k:v for k,v in r.items() if phase=='holdout' or k not in ('base','actual','rule_evidence')} for r in rows],results_reference=None if phase=='holdout' else dict(path=p['parent_result'],sha256=p['parent_result_sha256'],join_key=['code','origin','horizon'],omitted_fields=['base','actual','rule_evidence']),boundary=p['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('--phase',choices=['development','holdout'],default='development');parser.add_argument('--snapshot',type=Path);parser.add_argument('--selection',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();out=run(json.loads(raw));out['evidence']=dict(protocol_sha256=digest(raw),code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_normalized_margin.py','tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','tools/validate_tdx_zero_growth.py','tools/backtest_tdx_multiyear_growth.py','tools/backtest_tdx_cash_revenue.py','data_sources/alphalake.py','engine/module_4_dcf.py']})
        if args.phase=='holdout':
            from tools.backtest_tdx_multiyear_growth import forecast_rows
            p=json.loads(raw)
            if p['protocol_id']!='tdx-first-year-half-growth-v1' or not out['decision']['passed'] or args.selection is None or args.snapshot is None:raise ValueError('passing first-year development selection required')
            if json.loads(args.selection.read_bytes())!={k:v for k,v in out.items() if k!='results'}:raise ValueError('development selection differs')
            definition_raw=args.protocol.with_name('first-year-holdout-study.json').read_bytes()
            if json.loads(definition_raw)!=dict(parent_protocol_sha256=digest(raw),phase='holdout',samples=[s for s in p['samples'] if s['split']=='holdout']):raise ValueError('holdout definition differs')
            source_raw=args.snapshot.read_bytes();source=json.loads(source_raw)
            if source['study_sha256']!=digest(definition_raw):raise ValueError('holdout source binding differs')
            parent=json.loads((ROOT/p['parent_protocol']).read_bytes());development_raw=(ROOT/parent['development_snapshot']).read_bytes()
            if digest(development_raw)!=parent['development_snapshot_sha256']:raise ValueError('development source hash differs')
            development=json.loads(development_raw)
            if source['artifacts']!=development['artifacts'] or source['source_lists']!=development['source_lists']:raise ValueError('source versions differ')
            if any(r['code'] not in {s['code'] for s in p['samples'] if s['split']=='holdout'} for r in source['records']):raise ValueError('unexpected holdout company')
            inner=parent|dict(protocol_id='tdx-multiyear-growth-v1',samples=p['samples'],baseline='flat_first_five',benchmark='zero_growth',candidate='fade_to_terminal_by_year_five')
            rows=forecast_rows(inner,source,'holdout',horizons=(1,2,3))
            evidence=out['evidence']|dict(snapshot_sha256=digest(source_raw),selection_sha256=digest(args.selection.read_bytes()),forecast_helper_sha256=digest((ROOT/'valuation/backend/tools/backtest_tdx_multiyear_growth.py').read_bytes()))
            out=compare(p,dict(results=rows),'holdout');out['evidence']=evidence
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
