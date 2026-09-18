"""TDX源层简化回溯：只评价经营预测，不发布标准事实或DCF结论。"""
import argparse
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import mean, median

from data_sources.alphalake import HistoricalDCFPolicy, historical_forecast
from tools.tdx_research_source import (at, available, financial_value,
    period_basis, source_field, source_components, TIME_BOUNDARY)

CONTRACT='tdx-history-backtest-v2'
OPERATING_COMPONENTS={'operating_profit_cumulative':1,'interest_expense':1,'interest_income':-1,'investment_income':-1,'fair_value_change_income':-1,'asset_disposal_income':-1}


def quarter_periods(end):
    result=[]
    for _ in range(4):
        result.append(end.isoformat());end=date(end.year,end.month-2,1)-timedelta(days=1)
    return result


def window(rows,end,field):
    periods=[(p,1) for p in quarter_periods(end)] if period_basis(field)=='quarter' else [(end.isoformat(),1),(f'{end.year-1}-12-31',1),(end.replace(year=end.year-1).isoformat(),-1)]
    if period_basis(field)=='instant' or (period_basis(field)=='ytd' and end.month==12):periods=[(end.isoformat(),1)]
    total=sum((financial_value(rows[p],field)*coefficient for p,coefficient in periods),Decimal(0))
    return total, [dict(period=p,coefficient=c,field=source_field(field),artifact=rows[p]['artifact']) for p,c in periods]


def operating(rows,end):
    revenue,refs=window(rows,end,'revenue');parts={}
    for field in OPERATING_COMPONENTS:
        n,r=window(rows,end,field);parts[field]=float(n);refs+=r
    # 与生产入口一样逐分量转为百万元float，再按固定顺序加减。
    w={f:v/1e6 for f,v in parts.items()}
    ebit=w['operating_profit_cumulative']+w['interest_expense']-w['interest_income']-w['investment_income']-w['fair_value_change_income']-w['asset_disposal_income']
    return dict(revenue=float(revenue/Decimal(1000000)),ebit=ebit,ebit_components_cny=source_components(parts),source_inputs=refs)


def error(predicted,actual):
    revenue_error=predicted['revenue']-actual['revenue']
    ebit_error=predicted['ebit']-actual['ebit']
    return dict(revenue_error_million_cny=revenue_error,revenue_error_pct=100*revenue_error/actual['revenue'],
                ebit_error_million_cny=ebit_error,ebit_error_pct_actual_revenue=100*ebit_error/actual['revenue'],
                margin_error_pp=100*(predicted['ebit']/predicted['revenue']-actual['ebit']/actual['revenue']))


def metrics(rows,models=('current_rule','zero_growth')):
    result={}
    for model in models:
        valid=[r for r in rows if r['status']=='evaluated']
        eligible=valid
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
            quarters={date.fromisoformat(p):(financial_value(v,'revenue'),v['artifact']+':'+sample['code']+':'+source_field('revenue')) for p,v in base_rows.items()}
            forecast,evidence=historical_forecast(base['revenue'],base['ebit'],quarters,origin,policy)
            first=forecast[0];predicted_revenue=base['revenue']*(1+first.growth)
            predicted=dict(revenue=predicted_revenue,ebit=predicted_revenue*first.margin)
            r.update(base=base,forecast=predicted,rule_evidence=evidence)
            actual=operating(actual_rows,target)
            if actual['revenue']<=0:raise ValueError('positive actual revenue required for error scaling')
            flags=[]
            for stage,rows,end in [('base',base_rows,origin),('actual',actual_rows,target)]:
                for field in ('financial_business_interest_income','financial_business_interest_expense','financial_business_fee_expense','deposits_and_interbank_placements'):
                    n=financial_value(rows[end.isoformat()],field) if field=='deposits_and_interbank_placements' else window(rows,end,field)[0]
                    if n!=0:flags.append(stage+':'+source_field(field))
            r.update(status='evaluated',actual=actual,financial_scope_flags=flags,
                     profit_scope='financial_fields_present' if flags else 'no_financial_fields_detected',
                     profit_basis='consolidated_adjusted_ebit_proxy',
                     errors=dict(current_rule=error(predicted,actual),zero_growth=error(base,actual)))
        except (ValueError,KeyError,OverflowError,ArithmeticError) as exc:
            r['reason']=str(exc)
    return dict(contract_version=CONTRACT,status='completed',study_id=study['study_id'],origin=study['origin'],target=study['target'],
        forecast_as_of=study['forecast_as_of'],evaluation_as_of=study['evaluation_as_of'],amount_unit='million_CNY',
        results=output,summary=metrics(output),
        by_profit_scope={s:metrics([r for r in output if r.get('profit_scope','not_evaluated')==s])
                         for s in ('no_financial_fields_detected','financial_fields_present','not_evaluated')},
        by_split={s:metrics([r for r in output if r['split']==s]) for s in ('development','holdout')},
        by_stratum={s:metrics([r for r in output if r['stratum']==s]) for s in sorted({r['stratum'] for r in output})},
        boundaries=[TIME_BOUNDARY,
                    '2024字段语义沿用已审核后续期间映射，研究外推不扩大生产映射有效期；代码/人工分层不是标准历史身份',
                    '仅非金融主业固定样本的经营预测子规则；合并调整EBIT代理包含未拆分金融业务，非纯实业利润或完整DCF准入；汇总覆盖全部可评价样本并按金融字段信号分组，非行业分类，零字段不证明无兼营',
                    '同一历史起点、目的抽样且存在幸存者偏差；留出组未调参，但不是时间样本外验证',
                    '对零增长的收入和EBIT比较不需要股价/WACC，结果不得解释为一年投资收益预测'])


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('study',type=Path);p.add_argument('snapshot',type=Path);args=p.parse_args()
    try:
        study_raw=args.study.read_bytes();snapshot_raw=args.snapshot.read_bytes()
        study=json.loads(study_raw);snapshot=json.loads(snapshot_raw)
        if hashlib.sha256(study_raw).hexdigest()!=snapshot['study_sha256']:raise ValueError('frozen study hash mismatch')
        out=run(study,snapshot)
        out['evidence']=dict(source_adapter_sha256=hashlib.sha256(Path(__file__).with_name('tdx_research_source.py').read_bytes()).hexdigest(),study_sha256=hashlib.sha256(study_raw).hexdigest(),snapshot_sha256=hashlib.sha256(snapshot_raw).hexdigest(),
            forecast_code_sha256=hashlib.sha256((Path(__file__).resolve().parents[1]/'data_sources/alphalake.py').read_bytes()).hexdigest(),
            backtest_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(contract_version=CONTRACT,status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
