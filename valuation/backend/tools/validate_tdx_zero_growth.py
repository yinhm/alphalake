"""零增长候选：已观察开发选择，新公司留出在代码/选择冻结后才评分。"""
import argparse
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_multiyear_growth import study,metrics

ROOT=Path(__file__).resolve().parents[3]
BASE='flat_first_five'
CANDIDATE='zero_growth'


def digest(data):return hashlib.sha256(data).hexdigest()


def verify_sampling(p):
    s=p['sampling'];raw=(ROOT/s['universe']).read_bytes()
    if digest(raw)!=s['universe_sha256']:raise ValueError('universe hash differs')
    universe=json.loads(raw);excluded=set(universe['excluded_prior_codes'])
    for ref in s['prior_protocols']:
        raw=(ROOT/ref['path']).read_bytes()
        if digest(raw)!=ref['sha256']:raise ValueError('prior study hash differs')
        excluded.update(x['code'] for x in json.loads(raw)['samples'])
    if sorted(excluded)!=s['excluded_codes']:raise ValueError('exclusion ledger differs')
    ranked=sorted(universe['companies'],key=lambda r:digest((s['seed']+r['code']).encode()))
    selected=[r|dict(split='holdout') for r in ranked if r['code'] not in excluded][:120]
    if [r for r in p['samples'] if r['split']=='holdout']!=selected or len(selected)!=120:raise ValueError('holdout sampling differs')


def score(p,rows,phase,horizons=(2,3)):
    if p['protocol_id']!='tdx-zero-growth-validation-v1' or (p['baseline'],p['candidate'])!=(BASE,CANDIDATE):raise ValueError('unsupported zero-growth study')
    if phase not in ('development','holdout'):raise ValueError('invalid phase')
    g=p['gates']
    if any(v is not True for k,v in g.items() if k.startswith('require_')):raise ValueError('required gate disabled')
    summary=metrics(rows);by_horizon={str(h):metrics([r for r in rows if r['horizon']==h]) for h in horizons}
    by_window={w['origin']+':'+str(w['horizon']):metrics([r for r in rows if (r['origin'],r['horizon'])==(w['origin'],w['horizon'])]) for w in p['windows']}
    codes={r['code'] for r in rows if r['status']=='evaluated'};b=summary['models'][BASE];c=summary['models'][CANDIDATE]
    def nonworse(m,key):
        a=m['models'][BASE][key];z=m['models'][CANDIDATE][key]
        return a is not None and z is not None and z<=a
    sufficient=c['revenue_n']>=g[phase+'_minimum_pairs'] and len(codes)>=g[phase+'_minimum_companies']
    checks=dict(minimum_sample=sufficient,primary_improvement=sufficient and b['revenue_mae_pct']>0 and c['revenue_mae_pct']<=b['revenue_mae_pct']*(1-g['minimum_primary_improvement_fraction']),
        revenue_wape_nonworse=nonworse(summary,'revenue_wape_pct'),ebit_nonworse=all(nonworse(summary,k) for k in ('ebit_mae_pct_actual_revenue','ebit_wape_pct')),
        each_horizon_nonworse=all(nonworse(m,'revenue_mae_pct') for m in by_horizon.values()),
        each_window_nonworse=all(m['models'][CANDIDATE]['revenue_n']>0 and m['models'][CANDIDATE]['revenue_mae_pct']<=m['models'][BASE]['revenue_mae_pct']*g['maximum_each_window_primary_ratio'] for m in by_window.values()),
        leave_one_company_out_nonworse=bool(codes) and all(nonworse(metrics([r for r in rows if r['code']!=code]),'revenue_mae_pct') for code in codes))
    return dict(protocol_id=p['protocol_id'],phase=phase,evaluated_companies=len(codes),summary=summary,by_horizon=by_horizon,by_window=by_window,decision=dict(passed=all(checks.values()),checks=checks),boundary=p['boundary'])


def load_development(p):
    raw=(ROOT/p['parent_protocol']).read_bytes();data=(ROOT/p['development_snapshot']).read_bytes();recorded_raw=(ROOT/p['development_result']).read_bytes()
    if digest(raw)!=p['parent_protocol_sha256'] or digest(data)!=p['development_snapshot_sha256'] or digest(recorded_raw)!=p['development_result_sha256']:raise ValueError('development evidence hash differs')
    parent=json.loads(raw);source=json.loads(data);recorded=json.loads(recorded_raw)
    if parent['samples']!=[s for s in p['samples'] if s['split']=='development'] or any(parent[k]!=p[k] for k in ('base_policy','windows','evaluation_as_of')):raise ValueError('development definition differs')
    replay=study(parent,source)
    if replay!={k:v for k,v in recorded.items() if k!='evidence'}:raise ValueError('development replay differs')
    return parent,source,recorded['results']


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('--phase',required=True,choices=['development','holdout']);parser.add_argument('--snapshot',type=Path);parser.add_argument('--selection',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();p=json.loads(raw);verify_sampling(p);parent,source,rows=load_development(p)
        evidence=dict(protocol_sha256=digest(raw),code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_multiyear_growth.py','tools/backtest_tdx_cash_revenue.py','tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','data_sources/alphalake.py','engine/module_4_dcf.py']})
        dev=score(p,rows,'development');dev['evidence']=evidence;dev['results_reference']=dict(path=p['development_result'],sha256=p['development_result_sha256'])
        if args.phase=='development':out=dev
        else:
            # Verify the frozen selection before even opening the holdout snapshot.
            if args.selection is None or args.snapshot is None or not dev['decision']['passed']:raise ValueError('passing development selection required')
            if json.loads(args.selection.read_bytes())!=dev:raise ValueError('development selection differs')
            snapshot_raw=args.snapshot.read_bytes();fresh=json.loads(snapshot_raw)
            if fresh['study_sha256']!=digest(raw):raise ValueError('holdout source binding differs')
            if any(r['code'] not in {s['code'] for s in p['samples']} for r in fresh['records']):raise ValueError('unexpected source company')
            if fresh['artifacts']!=source['artifacts'] or fresh['source_lists']!=source['source_lists']:raise ValueError('source versions differ')
            old={(r['code'],r['period']):r for r in source['records']};dev_codes={s['code'] for s in parent['samples']}
            shared=[r for r in fresh['records'] if r['code'] in dev_codes]
            if len({(r['code'],r['period']) for r in shared})!=len(shared) or {(r['code'],r['period']) for r in shared}!={key for key in old if key[0] in dev_codes}:raise ValueError('development source records differ')
            for r in shared:
                before=old[(r['code'],r['period'])]
                required=set('FN230 FN86 FN305 FN306 FN83 FN82 FN301 FN314 FN506 FN509 FN510 FN413'.split())
                if any(before[k]!=v for k,v in r.items() if k!='bits') or r['bits']!={k:v for k,v in before['bits'].items() if k in required}:raise ValueError('development source bits differ')
            config=parent|dict(samples=p['samples']);computed=study(config,fresh,'holdout');held_rows=computed['results']
            out=score(p,held_rows,'holdout');out.update(evidence=evidence|dict(snapshot_sha256=digest(snapshot_raw)),results=held_rows)
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
