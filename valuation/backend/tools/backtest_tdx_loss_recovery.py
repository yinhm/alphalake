"""非正起点EBIT的研究层恢复预测；保留缺项和未恢复实际，不开放生产估值。"""
import argparse
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path
from statistics import mean

from tools.backtest_tdx_history import at,error
from tools.backtest_tdx_multiyear_growth import actual_operating
from tools.backtest_tdx_normalized_margin import annual,summarize
from tools.validate_tdx_zero_growth import verify_sampling

ROOT=Path(__file__).resolve().parents[3]
MODELS=('loss_current_margin','loss_gradual_five_fy_margin','zero_ebit')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def study(p,source):
    if p['protocol_id']!='tdx-loss-recovery-v1' or (p['baseline'],p['candidate'],p['comparator'])!=MODELS or p['transition_years']!=5 or p['history']['completed_fiscal_years']!=5:raise ValueError('unsupported recovery study')
    if source['contract_version']!='tdx-history-source-v1':raise ValueError('unsupported source')
    samples=[s for s in p['samples'] if s['split']=='development'];codes={s['code'] for s in samples}
    if len({s['code'] for s in p['samples']})!=len(p['samples']) or not samples:raise ValueError('unique samples required')
    if len({(w['origin'],w['horizon']) for w in p['windows']})!=len(p['windows']):raise ValueError('duplicate window')
    index=defaultdict(list);artifacts={r['file']:r for r in source['artifacts']}
    if len(artifacts)!=len(source['artifacts']):raise ValueError('duplicate artifact')
    for r in source['records']:
        if r['code'] not in codes:raise ValueError('unexpected source company')
        index[r['code'],r['period']].append(r)
    rows=[]
    for sample in samples:
        for w in p['windows']:
            end=date.fromisoformat(w['origin']);h=w['horizon'];cutoff=f'{end.year}-09-01T00:00:00+08:00';target=end.replace(year=end.year+h)
            if h not in (1,2,3) or (end.month,end.day)!=(6,30) or target>=at(p['evaluation_as_of']).date() or at(cutoff)>=at(p['evaluation_as_of']):raise ValueError('invalid window chronology')
            row=dict(code=sample['code'],origin=end.isoformat(),horizon=h,target=target.isoformat(),forecast_as_of=cutoff,status='blocked',baseline_status='blocked',actual_fcff=None,production_status='not_requested');rows.append(row)
            try:
                base=actual_operating(index,artifacts,sample['code'],end,cutoff);row['base']=base
                if base['ebit']>0:row.update(status='outside_loss_scope',reason='positive current EBIT');continue
                row['production_status']='rejected_current_nonpositive_ebit'
                forecasts={MODELS[0]:dict(revenue=base['revenue'],ebit=base['ebit']),MODELS[2]:dict(revenue=base['revenue'],ebit=0)};row['forecasts']=forecasts
                try:
                    history=[annual(index,artifacts,sample['code'],y,cutoff) for y in range(end.year-5,end.year)]
                    normal=mean(r['margin'] for r in history);row.update(history=history,normalized_margin=normal)
                    if not 0<normal<=1:raise ValueError('normalized margin outside (0,1]')
                    current=base['ebit']/base['revenue'];margin=current+(normal-current)*h/5
                    forecasts[MODELS[1]]=dict(revenue=base['revenue'],ebit=base['revenue']*margin)
                except (ValueError,KeyError,ArithmeticError) as exc:row['candidate_reason']=str(exc)
                # Scope and all predictions are fixed before looking at future recovery or loss.
                actual=actual_operating(index,artifacts,sample['code'],target,p['evaluation_as_of']);row.update(actual=actual,baseline_status='evaluated')
                if MODELS[1] in forecasts:row.update(status='evaluated',errors={m:error(f,actual) for m,f in forecasts.items()})
                else:row['status']='blocked_candidate'
            except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    out=summarize(p,rows,'development',MODELS)
    checks=out['decision']['checks'];checks['breakeven_comparator_nonworse']=checks.pop('production_comparator_nonworse')
    out['production_comparison']='no production forecast: current EBIT nonpositive; zero EBIT comparator is mathematical benchmark only'
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);parser.add_argument('snapshot',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();p=json.loads(raw);verify_sampling(p)
        parent_raw=(ROOT/p['parent_protocol']).read_bytes()
        if digest(parent_raw)!=p['parent_protocol_sha256']:raise ValueError('parent protocol hash differs')
        universe=json.loads((ROOT/p['sampling']['universe']).read_bytes());excluded=set(p['sampling']['excluded_codes'])
        expected=[r|dict(split='development') for r in sorted(universe['companies'],key=lambda r:digest((p['sampling']['seed']+r['code']).encode())) if r['code'] in excluded][:600]
        if [r for r in p['samples'] if r['split']=='development']!=expected:raise ValueError('development selection differs')
        definition_raw=args.protocol.with_name('development-study.json').read_bytes();definition=json.loads(definition_raw)
        if definition!=dict(parent_protocol_sha256=digest(raw),phase='development',samples=expected):raise ValueError('development source definition differs')
        source_raw=args.snapshot.read_bytes();source=json.loads(source_raw)
        if source['study_sha256']!=digest(definition_raw):raise ValueError('source binding differs')
        out=study(p,source);out['evidence']=dict(protocol_sha256=digest(raw),snapshot_sha256=digest(source_raw),code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_normalized_margin.py','tools/backtest_tdx_multiyear_growth.py','tools/backtest_tdx_history.py','tools/validate_tdx_zero_growth.py']})
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
