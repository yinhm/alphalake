"""在目标期尚未结束时保存固定模型预测；不读取目标实际或评分。"""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

from tools.backtest_tdx_cash_revenue import revenue_forecast
from tools.backtest_tdx_history import at, available, operating
from tools.backtest_tdx_origins import fit_calibration, forecast_cutoff


def prepare(plan,parent,source,prepared_at):
    origin=date.fromisoformat(plan['origin'])
    assert origin.replace(year=origin.year+1).isoformat()==plan['target']
    assert at(plan['forecast_cutoff'])<=at(prepared_at)<at(plan['target']+'T00:00:00+08:00')
    assert forecast_cutoff(parent,origin)==plan['forecast_cutoff']
    assert plan['models']==['current_rule','zero_growth','bias_half']
    calibration=fit_calibration(parent,source,plan['origin'])
    index=defaultdict(list);artifacts={a['file']:a for a in source['artifacts']}
    assert len(artifacts)==len(source['artifacts'])
    for row in source['records']:index[row['code'],row['period']].append(row)
    samples=[s for s in parent['samples'] if s['split']=='holdout']
    assert len(samples)==len({s['code'] for s in samples})==120
    rows=[]
    for sample in samples:
        code=sample['code'];row=dict(code=code,status='blocked_inputs',actual=None,errors=None)
        rows.append(row)
        try:
            if calibration['status']!='fitted':raise ValueError('insufficient past calibration pairs')
            _,evidence=revenue_forecast(parent,index,artifacts,code,origin,plan['forecast_cutoff'])
            history={r['period']:r for r in source['records'] if r['code']==code and date(origin.year-1,1,1)<=date.fromisoformat(r['period'])<=origin
                     and available(r,artifacts[r['artifact']])<=at(plan['forecast_cutoff'])}
            prior=operating(history,date(origin.year-1,12,31))
            if prior['revenue']<=0:raise ValueError('positive prior full-year revenue required')
            base=evidence['operating_inputs'];g=evidence['rule']['clipped_scenario_growth']
            revenue=base['revenue']*(1+g);margin=base['ebit']/base['revenue']
            weight=parent['candidate_weights']['bias_half']['calibration_weight']
            assert weight==0.5
            forecasts=dict(current_rule=dict(revenue=revenue,ebit=revenue*margin),
                zero_growth={k:base[k] for k in ('revenue','ebit')},
                bias_half=dict(revenue=revenue,ebit=revenue*(margin*(1+weight*(calibration['ebit_scale']-1)))))
            row.update(status='awaiting_target',base=base,rule_evidence=evidence['rule'],forecasts=forecasts)
        except (ValueError,KeyError,ArithmeticError) as exc:row['reason']=str(exc)
    return dict(study_id=plan['study_id'],origin=plan['origin'],target=plan['target'],prepared_at=prepared_at,
        forecast_cutoff=plan['forecast_cutoff'],evaluation_cutoff=plan['evaluation_cutoff'],unit='million_CNY',
        positions=len(rows),statuses=dict(Counter(r['status'] for r in rows)),calibration=calibration,rows=rows,
        validation_gates=parent['validation'],boundary=plan['boundary'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();plan=json.loads(args.plan.read_bytes())
    inputs={}
    for name in ('parent','source'):
        raw=Path(plan[name+'_path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==plan[name+'_sha256']
        inputs[name]=json.loads(raw)
    result=prepare(plan,inputs['parent'],inputs['source'],datetime.now(timezone.utc).isoformat())
    result['inputs']={k:v for k,v in plan.items() if k.endswith('_sha256')}
    raw=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode()+b'\n'
    with args.output.open('xb') as f:f.write(gzip.compress(raw,mtime=0))
    print(json.dumps(dict(positions=result['positions'],statuses=result['statuses'],prepared_at=result['prepared_at']),ensure_ascii=False))
