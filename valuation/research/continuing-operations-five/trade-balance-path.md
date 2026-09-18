# 一至三年营运余额路径检验

结论：本固定五家样本不支持统一采用“当前余额/收入比率＋当前收入增长规则”来预测三项营运余额。总体误差有所下降，但改善集中在安克，其余四家均恶化；不把平均数当作规则有效或估值准确度提升的证明。

## 问题与冻结范围

[协议](trade-balance-plan.json)在评分前提交于`2d3082b`。复用原五家公司、2023/2024/2025H1起点及原收入路径，一至三年共45个公司/起点/期限位置。30个已到期、15个2027/2028目标期尚未到期，全部保留；30个位置复用了15个公司/目标期余额，不是30个独立公司或独立结果。

目标为TDX的应收账款净额FN11、存货净额FN17、应付账款FN44三项期末余额。金额为人民币元，收入为对应TTM；源零、非正分量、缺期、身份重复或晚于截止均阻断。实际本批30个到期位置均可读取，但不等于已逐公司原文审核或生产DCF准入；格力/伊利的经营范围信号、海天的版本边界继续保留。简化源回溯使用FN314与后来取得的历史版本，不能称严格PIT。

达摩达兰指出，历史营运资本变动会波动，可结合公司历史或行业的营运资本/收入比率预测未来需求；这要求合理的基期及业务口径，不能保证任一公司当前比率稳定。参见[营运资本估计原文](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/noncashwc.htm)。本研究只检验其中三项余额的比例关系，不采用当期行业数据回填历史。

## 算式与对照

预测余额＝基期余额×原规则预测TTM收入/基期TTM收入。收入沿用已冻结的一年预测和前五年维持增长的多年路径；没有重拟合增长或引入新阈值。基准是沿用基期余额，相当于三项余额均不增长，不表示公司没有资本投入。

另以实际目标收入替换预测收入，仅作为**事后误差归因**：

- 收入路径分量＝比例预测余额−以实际收入计算的反事实余额。
- 余额比例变化分量＝反事实余额−实际余额。
- 两分量之和＝比例预测余额−实际余额，逐项Decimal闭合；绝对误差不可按此直接拆成百分比贡献。

反事实使用未来实际收入，不能作为可用预测、效果上界或采用依据。三项净余额（应收＋存货−应付）的有符号误差另列，不能将相互抵消的小数当作分项预测准确，也不直接折现为现金流修正。

## 已到期结果

主指标为每个位置的三项绝对余额误差之和/实际TTM收入，再对位置等权平均，单位%。不是三个单项百分误差的平均，也不是全营运资本误差。

| 范围 | 已到期位置 | 沿用余额 | 随预测收入同比例 | 使用实际收入的事后对照 |
| --- | ---: | ---: | ---: | ---: |
| 全部 | 30 | 6.3344 | 5.7869 | 4.1488 |
| 一年 | 15 | 4.6541 | 4.0643 | 3.1545 |
| 两年 | 10 | 7.1350 | 6.4238 | 4.9574 |
| 三年 | 5 | 9.7741 | 9.6808 | 5.5146 |
| 安克 | 6 | 13.3808 | 6.9600 | 5.1170 |
| 苏泊尔 | 6 | 4.9234 | 6.9822 | 3.6991 |
| 格力 | 6 | 8.1630 | 8.7551 | 7.9931 |
| 伊利 | 6 | 3.5903 | 4.5496 | 2.9003 |
| 海天 | 6 | 1.6145 | 1.6875 | 1.0346 |

三个起点的综合误差也分别列于[完整结果](trade-balance-result.json.gz)，比例规则7.7740%/4.2954%/2.8085%，沿用余额8.2799%/4.5753%/4.0159%。起点的可观察期限不同，不能把逐期数字下降解释为模型在时间上进步。

分项WAPE同样不是一致改善：应收14.1580%→14.0964%，存货28.3728%→30.1549%（变差），应付11.0659%→10.6990%。WAPE分母为该分项实际余额之和；全部余额为正。本样本不能支持“只要收入预测准确，资本需求就已解决”：事后实际收入对照仍留下明显余额误差，应付WAPE甚至为11.4958%。

## 决定与后续

不推广这条组合、不调整生产资本效率、不依据五家结果挑选各公司的最优规则。这里确认的是联合路径必须检验：增长预测和资金周转假设一起决定未来占用，单独改善利润或更换行业比率不能证明资本路径有效。

既有2025余额—现金调节、准备和套期差额已在[原分类台账](../../../internal/ingest/testdata/anker-valuation-2026/README.md)记录，不重复开展相同核对或转回异常公司追查。后续改进必须同时对照多年收入、余额需求和现金边界；不凭本小样本平均改善扩大采用，完整经营资本分类及FCFF仍需独立证据。

## 可重复检查

本轮没有生产代码、依赖或数据库变更；不运行Go全套。下面脚本验证输入哈希、所有收入路径与冻结结果一致、误差分量闭合。内存翻转未来存货一位只改变实际误差，不改变任何预测；删除或复制未来记录会保留预测及45个位置，并将三个受影响位置标为实际数据阻断。切片可离线重放，无新下载。此诊断未接入CI，不称全市场验证或独立留出。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
import gzip, hashlib, json, math
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from tools.backtest_tdx_history import at, available
from tools.tdx_research_source import financial_value as value, canonical_field, source_field
from tools.backtest_tdx_working_cash import revenue_window
p=Path('valuation/research/continuing-operations-five')
plan=json.loads((p/'trade-balance-plan.json').read_bytes())
for name,digest in plan['inputs'].items():assert hashlib.sha256((p/name).read_bytes()).hexdigest()==digest
source=json.loads((p/'capital-snapshot.json').read_bytes());base=json.loads((p/'baseline.json').read_bytes())
multi=json.loads(gzip.decompress((p/'multiyear-result.json.gz').read_bytes()))
fields=tuple(canonical_field(f) for f in plan['fields']);models=('proportional_revenue','repeat_balance','actual_revenue_counterfactual')
origins={(o['origin'],r['code']):r for o in base['origins'] for r in o['results']}
def analyze(s):
 artifacts={a['file']:a for a in s['artifacts']};assert len(artifacts)==len(s['artifacts'])
 index=defaultdict(list)
 for r in s['records']:index[r['code'],r['period']].append(r)
 def balances(code,period,cutoff):
  matches=index[code,period]
  if len(matches)!=1:raise ValueError('missing_or_duplicate_balance')
  r=matches[0];a=artifacts[r['artifact']]
  if a['report_period']!=period or available(r,a)>at(cutoff):raise ValueError('balance_period_or_cutoff')
  v={f:value(r,f) for f in fields}
  if any(x<=0 for x in v.values()):raise ValueError('nonpositive_or_ambiguous_balance')
  return v,dict(artifact=r['artifact'],period=period,bits={source_field(f):r['bits'][source_field(f)] for f in fields})
 rows=[]
 for code in plan['codes']:
  for origin in plan['origins']:
   b=origins[origin,code];year=int(origin[:4]);cutoff=f'{year}-09-01T00:00:00+08:00'
   for horizon in plan['horizons']:
    target=f'{year+horizon}-06-30';row=dict(code=code,origin=origin,horizon=horizon,target=target,status='blocked_base',actual_fcff=None,production_admission='not_assessed',financial_scope_flags=b['financial_scope_flags']);rows.append(row)
    try:
     amount,refs=balances(code,origin,cutoff)
     revenue,_,_=revenue_window(index,artifacts,code,date.fromisoformat(origin),cutoff)
     assert math.isclose(float(revenue/1000000),b['base']['revenue'],rel_tol=1e-13)
     future=b['base']['revenue']
     for _ in range(horizon):future*=1+b['rule_evidence']['clipped_scenario_growth']
     expected=b['forecast']['revenue'] if horizon==1 else next((x['forecasts']['flat_first_five']['revenue'] for x in multi['results'] if x['code']==code and x['origin']==origin and x['horizon']==horizon and x['status']=='evaluated'),future)
     assert math.isclose(future,expected,rel_tol=1e-13)
     predicted_revenue=D(str(future))*1000000
     predicted={f:amount[f]*predicted_revenue/revenue for f in fields}
     row.update(base_revenue_cny=str(revenue),predicted_revenue_cny=str(predicted_revenue),base_balances_cny={f:str(v) for f,v in amount.items()},base_source=refs,predictions={models[0]:{f:str(v) for f,v in predicted.items()},models[1]:{f:str(v) for f,v in amount.items()}})
     if date.fromisoformat(target)>at(plan['evaluation_as_of']).date():row['status']='not_yet_observable';continue
     row['status']='blocked_actual'
     actual,actual_refs=balances(code,target,plan['evaluation_as_of'])
     actual_revenue,_,_=revenue_window(index,artifacts,code,date.fromisoformat(target),plan['evaluation_as_of'])
     oracle={f:amount[f]*actual_revenue/revenue for f in fields}
     errors={models[0]:{f:predicted[f]-actual[f] for f in fields},models[1]:{f:amount[f]-actual[f] for f in fields},models[2]:{f:oracle[f]-actual[f] for f in fields}}
     attribution={f:dict(revenue_path_error_cny=str(predicted[f]-oracle[f]),balance_intensity_error_cny=str(oracle[f]-actual[f])) for f in fields}
     for f in fields:assert abs(D(attribution[f]['revenue_path_error_cny'])+D(attribution[f]['balance_intensity_error_cny'])-errors[models[0]][f])<D('.00000001')
     row.update(status='evaluated',actual_source=actual_refs,actual_revenue_cny=str(actual_revenue),actual_balances_cny={f:str(v) for f,v in actual.items()},hindsight_counterfactual_cny={f:str(v) for f,v in oracle.items()},errors_cny={m:{f:str(v) for f,v in e.items()} for m,e in errors.items()},attribution=attribution)
    except (ValueError,KeyError,ArithmeticError) as error:row['reason']=str(error)
 return rows
def summarize(rows):
 valid=[r for r in rows if r['status']=='evaluated'];out={}
 for m in models:
  out[m]=dict(n=len(valid),gross_component_mae_pct_revenue=float(sum((sum((abs(D(r['errors_cny'][m][f])) for f in fields),D(0))/D(r['actual_revenue_cny'])*100 for r in valid),D(0))/len(valid)) if valid else None,components={})
  for f in fields:
   denom=sum((D(r['actual_balances_cny'][f]) for r in valid),D(0))
   out[m]['components'][f]=dict(wape_pct=float(100*sum((abs(D(r['errors_cny'][m][f])) for r in valid),D(0))/denom) if denom else None)
  out[m]['mean_signed_three_balance_net_error_pct_revenue']=float(sum(((D(r['errors_cny'][m]['accounts_receivable'])+D(r['errors_cny'][m]['inventories'])-D(r['errors_cny'][m]['accounts_payable']))/D(r['actual_revenue_cny'])*100 for r in valid),D(0))/len(valid)) if valid else None
 return dict(positions=len(rows),statuses=dict(Counter(r['status'] for r in rows)),models=out)
rows=analyze(source);assert len(rows)==45
changed=deepcopy(source);next(r for r in changed['records'] if (r['code'],r['period'])==('300866','2026-06-30'))['bits']['FN17']^=1
other=analyze(changed)
assert [r.get('predictions') for r in rows]==[r.get('predictions') for r in other]
assert [r.get('errors_cny') for r in rows]!=[r.get('errors_cny') for r in other]
removed=deepcopy(source);removed['records']=[r for r in removed['records'] if not (r['code']=='300866' and r['period']=='2026-06-30')]
missing=analyze(removed);assert [r.get('predictions') for r in rows]==[r.get('predictions') for r in missing]
assert sum(r['status']=='blocked_actual' for r in missing)==3
duplicated=deepcopy(source);duplicated['records'].append(deepcopy(next(r for r in source['records'] if (r['code'],r['period'])==('300866','2026-06-30'))))
duplicate_rows=analyze(duplicated)
assert sum(r['status']=='blocked_actual' for r in duplicate_rows)==3
assert [r.get('predictions') for r in rows]==[r.get('predictions') for r in duplicate_rows]
result=dict(plan_sha256=hashlib.sha256((p/'trade-balance-plan.json').read_bytes()).hexdigest(),boundary=plan['boundary'],summary=summarize(rows),by_origin={o:summarize([r for r in rows if r['origin']==o]) for o in plan['origins']},by_horizon={str(h):summarize([r for r in rows if r['horizon']==h]) for h in plan['horizons']},by_company={c:summarize([r for r in rows if r['code']==c]) for c in plan['codes']},results=rows,validation=['all_frozen_revenue_paths_match','future_balance_bit_tamper_preserves_all_forecasts_changes_errors','missing_future_balance_retains_forecasts_and_all_positions','decimal_signed_error_attribution_closes','duplicate_future_identity_blocks_actual_retains_forecasts'],decision='diagnostic_only_no_production_adoption')
# 仅在核对旧归档时恢复源键；上面的预测、误差和汇总均用通用字段。
def source_archive(v):
 if isinstance(v,dict):return {(source_field(k) if k in fields else k):source_archive(x) for k,x in v.items()}
 if isinstance(v,list):return [source_archive(x) for x in v]
 return v
encoded=(json.dumps(source_archive(result),ensure_ascii=False,indent=2)+'\n').encode();target=p/'trade-balance-result.json.gz'
if target.exists():assert gzip.decompress(target.read_bytes())==encoded
else:target.write_bytes(gzip.compress(encoded,mtime=0))
print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
PY
```
