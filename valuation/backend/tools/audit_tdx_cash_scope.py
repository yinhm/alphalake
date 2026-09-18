"""复核已评分现金规则在起点预测输入范围内的表现，不选择新规则。"""
import argparse
from collections import defaultdict
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from tools.audit_tdx_reinvestment import component
from tools.backtest_tdx_cash_revenue import revenue_forecast
from tools.backtest_tdx_history import at,available,value
from tools.backtest_tdx_operating_cash import study,metrics

ROOT=Path(__file__).resolve().parents[3]


def origin_scope(forecast_protocol,index,artifacts,code,end,cutoff):
    try:
        _,evidence=revenue_forecast(forecast_protocol,index,artifacts,code,end,cutoff)
    except (ValueError,KeyError,ArithmeticError) as exc:
        return dict(group='forecast_blocked',reason=str(exc))
    signals={};refs=[]
    try:
        for field in ('FN506','FN509','FN510'):
            c=component(index,artifacts,code,end,field,cutoff)
            if c['status']=='blocked':raise ValueError(field+':'+str(c['issues']))
            signals[field]=c['value_cny'];refs+=c['source_inputs']
        rows=index.get((code,end.isoformat()),[])
        if len(rows)!=1:raise ValueError('instant signal identity differs')
        r=rows[0];a=artifacts[r['artifact']]
        if a['report_period']!=end.isoformat() or available(r,a)>at(cutoff):raise ValueError('instant signal period/cutoff differs')
        signals['FN413']=str(value(r,'FN413'));refs.append(dict(period=end.isoformat(),field='FN413',bits=r['bits']['FN413'],artifact=r['artifact'],coefficient=1))
        group='forecast_ready_financial_signal' if any(Decimal(v)!=0 for v in signals.values()) else 'forecast_ready_no_financial_signal'
        return dict(group=group,signals=signals,source_inputs=refs,forecast_rule=evidence['rule'])
    except (ValueError,KeyError,ArithmeticError) as exc:
        return dict(group='financial_signal_unknown',reason=str(exc),forecast_rule=evidence['rule'])


def audit(p,cash_protocol,forecast_protocol,source):
    if p['protocol_id']!='tdx-cash-origin-scope-audit-v1':raise ValueError('unsupported scope audit')
    index=defaultdict(list);artifacts={a['file']:a for a in source['artifacts']}
    for row in source['records']:index[(row['code'],row['period'])].append(row)
    scopes={}
    # Classification consumes only origin inputs, before cash outcome scoring.
    for s in cash_protocol['samples']:
        for origin in cash_protocol['origins']:
            end=date.fromisoformat(origin);cutoff=f'{end.year}-09-01T00:00:00+08:00'
            scopes[(s['code'],origin)]=origin_scope(forecast_protocol,index,artifacts,s['code'],end,cutoff)
    phases={}
    for phase in ('development','holdout'):
        original=study(cash_protocol,source,phase);rows=original['results']
        for row in rows:row['origin_scope']=scopes[(row['code'],row['origin'])]
        def grouped(selected):return {g:metrics([r for r in selected if r['origin_scope']['group']==g]) for g in p['groups']}
        phases[phase]=dict(original_summary=original['summary'],original_decision=original['decision'],by_scope=grouped(rows),
            by_origin={o:grouped([r for r in rows if r['origin']==o]) for o in cash_protocol['origins']},
            membership=[dict(code=r['code'],origin=r['origin'],cash_status=r['status'],**r['origin_scope']) for r in rows])
    return dict(protocol_id=p['protocol_id'],phases=phases,boundary=p['boundary'])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();p=json.loads(raw);digest=lambda b:hashlib.sha256(b).hexdigest();inputs={}
        for key in ('cash_protocol','forecast_protocol','snapshot'):
            data=(ROOT/p[key]).read_bytes()
            if digest(data)!=p[key+'_sha256']:raise ValueError(key+' hash differs')
            inputs[key]=json.loads(data)
        if inputs['snapshot']['study_sha256']!=p['cash_protocol_sha256']:raise ValueError('source cash protocol differs')
        out=audit(p,inputs['cash_protocol'],inputs['forecast_protocol'],inputs['snapshot'])
        out['evidence']=dict(protocol_sha256=digest(raw),code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_cash_revenue.py','tools/backtest_tdx_operating_cash.py','tools/backtest_tdx_capex.py','tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','tools/audit_tdx_reinvestment.py','data_sources/alphalake.py']})
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
