"""TDX源层简化回溯：只评价经营预测，不发布标准事实或DCF结论。"""
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
import struct

from data_sources.alphalake import HistoricalDCFPolicy, historical_forecast

CONTRACT='tdx-history-backtest-v1'
EBIT={'FN86':1,'FN305':1,'FN306':-1,'FN83':-1,'FN82':-1,'FN301':-1}


def at(value):
    result=datetime.fromisoformat(value)
    if result.utcoffset() is None:raise ValueError('timezone required')
    return result


def value(row,field):
    bits=row['bits'][field]
    if isinstance(bits,bool) or not isinstance(bits,int) or not 0<=bits<=0xffffffff:
        raise ValueError('invalid float32 bits: '+field)
    n=struct.unpack('<f',struct.pack('<I',bits))[0]
    if not math.isfinite(n):raise ValueError('nonfinite source: '+field)
    return Decimal.from_float(n)


def available(row,artifact):
    n=value(row,'FN314')
    if n!=int(n) or not 10000<=n<=991231:raise ValueError('missing/invalid FN314')
    day=date.fromisoformat(f'{2000+int(n)//10000:04d}-{int(n)//100%100:02d}-{int(n)%100:02d}')
    if day<=date.fromisoformat(row['period']) or day>at(artifact['fetched_at']).astimezone(timezone(timedelta(hours=8))).date():
        raise ValueError('FN314 outside report/fetch dates')
    return datetime.combine(day+timedelta(days=1),datetime.min.time(),timezone(timedelta(hours=8)))


def quarter_periods(end):
    result=[]
    for _ in range(4):
        result.append(end.isoformat());end=date(end.year,end.month-2,1)-timedelta(days=1)
    return result


def window(rows,end,field):
    periods=[(p,1) for p in quarter_periods(end)] if field=='FN230' else [(end.isoformat(),1),(f'{end.year-1}-12-31',1),(end.replace(year=end.year-1).isoformat(),-1)]
    if field!='FN230' and end.month==12:periods=[(end.isoformat(),1)]
    total=sum((value(rows[p],field)*coefficient for p,coefficient in periods),Decimal(0))
    return total, [dict(period=p,coefficient=c,field=field,artifact=rows[p]['artifact']) for p,c in periods]


def operating(rows,end):
    revenue,refs=window(rows,end,'FN230');parts={}
    for field,sign in EBIT.items():
        n,r=window(rows,end,field);parts[field]=float(n);refs+=r
    # 与生产入口一样逐分量转为百万元float，再按固定顺序加减。
    w={f:v/1e6 for f,v in parts.items()}
    ebit=w['FN86']+w['FN305']-w['FN306']-w['FN83']-w['FN82']-w['FN301']
    return dict(revenue=float(revenue/Decimal(1000000)),ebit=ebit,ebit_components_cny=parts,source_inputs=refs)


def error(predicted,actual):
    revenue_error=predicted['revenue']-actual['revenue']
    ebit_error=predicted['ebit']-actual['ebit']
    return dict(revenue_error_million_cny=revenue_error,revenue_error_pct=100*revenue_error/actual['revenue'],
                ebit_error_million_cny=ebit_error,ebit_error_pct_actual_revenue=100*ebit_error/actual['revenue'],
                margin_error_pp=100*(predicted['ebit']/predicted['revenue']-actual['ebit']/actual['revenue']))


def metrics(rows):
    result={}
    for model in ('current_rule','zero_growth'):
        valid=[r for r in rows if r['status']=='evaluated']
        eligible=[r for r in valid if not r['financial_scope_flags']]
        errors=[r['errors'][model] for r in valid]
        result[model]=dict(revenue_n=len(valid),ebit_n=len(eligible),
            revenue_median_ape_pct=median(abs(e['revenue_error_pct']) for e in errors) if valid else None,
            revenue_mean_bias_pct=mean(e['revenue_error_pct'] for e in errors) if valid else None,
            revenue_wape_pct=100*sum(abs(e['revenue_error_million_cny']) for e in errors)/sum(r['actual']['revenue'] for r in valid) if valid else None,
            margin_mae_pp=mean(abs(r['errors'][model]['margin_error_pp']) for r in eligible) if eligible else None,
            ebit_mae_pct_actual_revenue=mean(abs(r['errors'][model]['ebit_error_pct_actual_revenue']) for r in eligible) if eligible else None)
    return dict(candidates=len(rows),statuses=dict(Counter(r['status'] for r in rows)),models=result)


def run(study,snapshot):
    if snapshot['contract_version']!='tdx-history-source-v1':raise ValueError('unsupported source snapshot')
    origin=date.fromisoformat(study['origin']);target=date.fromisoformat(study['target'])
    if origin.isoformat()!=study['policy']['approved_report_period'] or origin.month not in (3,6,9,12) or (origin+timedelta(days=1)).day!=1 or target!=origin.replace(year=origin.year+1):
        raise ValueError('one-year same-quarter horizon and same-period policy required')
    cutoff=at(study['forecast_as_of']);evaluation=at(study['evaluation_as_of'])
    if cutoff>=evaluation or cutoff.date()<=origin or evaluation.date()<=target:raise ValueError('invalid evaluation chronology')
    policy=HistoricalDCFPolicy.model_validate(study['policy'])
    artifacts={a['file']:a for a in snapshot['artifacts']}
    if len(artifacts)!=len(snapshot['artifacts']):raise ValueError('duplicate artifact')
    samples=study['samples'];codes=[r['code'] for r in samples]
    if not codes or len(set(codes))!=len(codes) or any(len(c)!=6 or not c.isascii() or not c.isdigit() for c in codes):raise ValueError('invalid sample codes')
    if any(s['split'] not in ('development','holdout') for s in samples):raise ValueError('invalid split')
    grouped={c:[] for c in codes}
    for row in snapshot['records']:
        if row['code'] in grouped:grouped[row['code']].append(row)
    output=[]
    for sample in samples:
        r=dict(sample,status='blocked',diagnostics=[]);output.append(r)
        try:
            seen=set();base_rows={};actual_rows={}
            for row in grouped[sample['code']]:
                p=row['period'];day=date.fromisoformat(p)
                if day.month not in (3,6,9,12) or (day+timedelta(days=1)).day!=1:raise ValueError('invalid report quarter')
                if p in seen:raise ValueError('duplicate code/period; identity or version ambiguous')
                seen.add(p)
                a=artifacts[row['artifact']]
                if a['report_period']!=p:raise ValueError('artifact period differs')
                try:usable=available(row,a)
                except (ValueError,KeyError,OverflowError) as exc:
                    r['diagnostics'].append(dict(period=p,reason=str(exc)));continue
                if day<=origin and usable<=cutoff:base_rows[p]=row
                if day<=target and usable<=evaluation:actual_rows[p]=row
            base=operating(base_rows,origin)
            quarters={date.fromisoformat(p):(value(v,'FN230'),v['artifact']+':'+sample['code']+':FN230') for p,v in base_rows.items()}
            forecast,evidence=historical_forecast(base['revenue'],base['ebit'],quarters,origin,policy)
            first=forecast[0];predicted_revenue=base['revenue']*(1+first.growth)
            predicted=dict(revenue=predicted_revenue,ebit=predicted_revenue*first.margin)
            r.update(base=base,forecast=predicted,rule_evidence=evidence)
            actual=operating(actual_rows,target)
            if actual['revenue']<=0:raise ValueError('positive actual revenue required for error scaling')
            flags=[]
            for stage,rows,end in [('base',base_rows,origin),('actual',actual_rows,target)]:
                for field in ('FN506','FN509','FN510','FN413'):
                    n=value(rows[end.isoformat()],field) if field=='FN413' else window(rows,end,field)[0]
                    if n!=0:flags.append(stage+':'+field)
            r.update(status='evaluated',actual=actual,financial_scope_flags=flags,
                     errors=dict(current_rule=error(predicted,actual),zero_growth=error(base,actual)))
        except (ValueError,KeyError,OverflowError,ArithmeticError) as exc:
            r['reason']=str(exc)
    return dict(contract_version=CONTRACT,status='completed',study_id=study['study_id'],origin=study['origin'],target=study['target'],
        forecast_as_of=study['forecast_as_of'],evaluation_as_of=study['evaluation_as_of'],amount_unit='million_CNY',
        results=output,summary=metrics(output),by_split={s:metrics([r for r in output if r['split']==s]) for s in ('development','holdout')},
        by_stratum={s:metrics([r for r in output if r['stratum']==s]) for s in sorted({r['stratum'] for r in output})},
        boundaries=['简化TDX历史回溯，FN314日期精度，无CNINFO逐公司核验；可能包含后续修订，不是严格PIT或前瞻检验',
                    '2024字段语义沿用已审核后续期间映射，研究外推不扩大生产映射有效期；代码/人工分层不是标准历史身份',
                    '仅经营预测子规则；非完整DCF准入或每股估值；金融字段命中者不进入EBIT/利润率汇总，零字段不证明无兼营',
                    '同一历史起点、目的抽样且存在幸存者偏差；留出组未调参，但不是时间样本外验证',
                    '对零增长的收入和EBIT比较不需要股价/WACC，结果不得解释为一年投资收益预测'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('study',type=Path);p.add_argument('snapshot',type=Path);args=p.parse_args()
    try:
        study_raw=args.study.read_bytes();snapshot_raw=args.snapshot.read_bytes()
        study=json.loads(study_raw);snapshot=json.loads(snapshot_raw)
        if hashlib.sha256(study_raw).hexdigest()!=snapshot['study_sha256']:raise ValueError('frozen study hash mismatch')
        out=run(study,snapshot)
        out['evidence']=dict(study_sha256=hashlib.sha256(study_raw).hexdigest(),snapshot_sha256=hashlib.sha256(snapshot_raw).hexdigest(),
            forecast_code_sha256=hashlib.sha256((Path(__file__).resolve().parents[1]/'data_sources/alphalake.py').read_bytes()).hexdigest(),
            backtest_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(contract_version=CONTRACT,status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
