"""复用生产历史预测过渡，不重复保存旧研究的源分量和实际值。"""
import argparse
import copy
from datetime import date
import hashlib
import json
from pathlib import Path

from data_sources.alphalake import HistoricalDCFPolicy,historical_forecast
from tools.backtest_tdx_history import error
from tools.tdx_research_source import financial_value,source_field
from tools.backtest_tdx_normalized_margin import study,summarize,MODELS

ROOT=Path(__file__).resolve().parents[3]
CANDIDATE='zero_growth_gradual_five_fy_margin'


def digest(raw):return hashlib.sha256(raw).hexdigest()


def forecast(row,index,base_policy):
    quarters={};code=row['code']
    for pair in row['rule_evidence']['revenue_yoy_pairs']:
        end=date.fromisoformat(pair['period'])
        for period in (end,end.replace(year=end.year-1)):
            source=index[(code,period.isoformat())]
            quarters[period]=(financial_value(source,'revenue'),source['artifact']+':'+code+':'+source_field('revenue'))
    base=row['base'];current=base['ebit']/base['revenue']
    policy=HistoricalDCFPolicy.model_validate(base_policy|dict(approved_report_period=row['origin'],growth_floor=0,growth_ceiling=0,margin_shift=row['normalized_margin']-current))
    annual,evidence=historical_forecast(base['revenue'],base['ebit'],quarters,date.fromisoformat(row['origin']),policy)
    selected=annual[row['horizon']-1]
    return dict(revenue=base['revenue'],ebit=base['revenue']*selected.margin),dict(current_margin=current,target_margin=row['normalized_margin'],forecast_margin=selected.margin,transition_years=5,source='existing_historical_forecast')


def run(p):
    if p['protocol_id']!='tdx-gradual-normalized-margin-v1' or p['candidate']!=CANDIDATE or p['transition_years']!=5:raise ValueError('unsupported gradual study')
    data={}
    for key in ('parent_protocol','parent_result','snapshot'):
        raw=(ROOT/p[key]).read_bytes()
        if digest(raw)!=p[key+'_sha256']:raise ValueError(key+' hash differs')
        data[key]=json.loads(raw)
    parent,recorded,source=(data[k] for k in ('parent_protocol','parent_result','snapshot'))
    replay=study(parent,source,'development')
    if replay!={k:v for k,v in recorded.items() if k!='evidence'}:raise ValueError('parent replay differs')
    rows=copy.deepcopy(recorded['results']);index={(r['code'],r['period']):r for r in source['records']}
    if len(index)!=len(source['records']):raise ValueError('duplicate source record')
    models=(MODELS[0],CANDIDATE,MODELS[2])
    for row in rows:
        if MODELS[1] in row.get('forecasts',{}):
            predicted,evidence=forecast(row,index,parent['base_policy'])
            del row['forecasts'][MODELS[1]];row['forecasts'][CANDIDATE]=predicted;row['gradual_rule']=evidence
        if row['status']=='evaluated':row['errors']={m:error(row['forecasts'][m],row['actual']) for m in models}
    out=summarize(parent|dict(protocol_id=p['protocol_id'],boundary=p['boundary']),rows,'development',models)
    # 原证据按唯一公司/起点/期限引用；保存新预测与误差，避免再复制17MB历史分量。
    out['results']=[{k:v for k,v in r.items() if k not in ('base','history','actual','rule_evidence')} for r in rows]
    out['results_reference']=dict(path=p['parent_result'],sha256=p['parent_result_sha256'],join_key=['code','origin','horizon'],omitted_fields=['base','history','actual','rule_evidence'])
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('protocol',type=Path);args=parser.parse_args()
    try:
        raw=args.protocol.read_bytes();out=run(json.loads(raw))
        out['evidence']=dict(protocol_sha256=digest(raw),code_sha256=digest(Path(__file__).read_bytes()),helpers={name:digest((ROOT/'valuation/backend'/name).read_bytes()) for name in ['tools/backtest_tdx_normalized_margin.py','tools/backtest_tdx_history.py', 'tools/tdx_research_source.py','data_sources/alphalake.py']})
        print(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps(dict(status='rejected',reason=str(exc)),ensure_ascii=False));raise SystemExit(1)


if __name__=='__main__':main()
