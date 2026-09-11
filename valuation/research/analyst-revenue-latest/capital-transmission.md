# 年度收入预测向营运余额的传递检验

结论：研报收入路径在两家公司平均余额误差上有改善，但存货WAPE和2025起点恶化，暂不采用。即使事后收入完全正确，固定余额/收入比例仍有明显误差，收入预测改善不能直接解释为资本需求或估值更准确。

## 冻结口径

[协议](capital-transmission-plan.json)先于计算提交（091d541）。复用[最新研报试验](README.md)18个预测位置及其三个收入模型，不改券商、增长、样本或截止。固定两家公司三个起点，12个到期位置重复使用六个公司/实际年度，6个未来位置保留；不是独立验证。

每个起点采用**上一年12月31日**的应收账款净额FN11、存货净额FN17、应付账款FN44，除以上年四季度FN230收入，形成各自比例。所有基期记录必须在起点年9月1日之前按FN314次日规则可用。三项比例分别乘冻结的研报、原规则年度桥接、零增长年度桥接收入；另列直接重复基期余额的基准。这与此前H1余额检验不同，是为匹配研报FY收入而冻结的年度季节口径，不能把两个试验串成同一模型的进步。

目标为当年及后两年的12月31日余额，实际值截止2026-09-10中国零点。源数据始终保留TDX float32位模式，不恢复PDF小数。缺季度、重复记录、非正/歧义值、晚于截止均拒绝相应位置，不删除分母。后来取得的历史TDX与FN314仍是简化回溯，不声称严格PIT或标准事实准入。

## 结果

主误差=逐位置三项余额绝对误差之和÷该目标年实际收入，再等权平均；它不是收入MAE，也不是净营运资本变化误差。

| 范围 | 到期位置 | 研报收入路径 | 原规则年度桥接 | 零增长年度桥接 | 重复原余额 | 事后实际收入反事实 |
|---|---:|---:|---:|---:|---:|---:|
| 两家公司 | 12 | 5.4497% | 6.3734% | 7.0682% | 7.8652% | 5.0782% |
| 安克 | 6 | 6.3722% | 6.4123% | 9.1251% | 10.8479% | 5.8417% |
| 苏泊尔 | 6 | 4.5272% | 6.3345% | 5.0114% | 4.8825% | 4.3147% |
| 2023起点 | 6 | 7.0757% | 8.3215% | 9.8734% | 10.3481% | 6.6421% |
| 2024起点 | 4 | 3.8845% | 4.8210% | 4.6310% | 5.7445% | 3.4685% |
| 2025起点 | 2 | 3.7019% | 3.6340% | 3.5274% | 4.6579% | 3.6059% |

| 单项WAPE | 研报收入路径 | 原规则年度桥接 |
|---|---:|---:|
| 应收账款净额 | 14.9265% | 19.8943% |
| 存货净额 | 21.8293% | 20.2472% |
| 应付账款 | 12.9693% | 18.7851% |

全量源引用、分组、正负净额误差及哈希见[压缩回执](capital-transmission-result.json.gz)。安克收入预测变差而余额平均误差略降，并不矛盾：余额比例误差与收入预测误差可能抵消，不能用后者解释单独收入规则的有效性。

逐分量验证的有符号恒等式为：余额预测误差 = 基期比例×收入预测误差＋（基期比例×实际收入−实际余额）。最后一项只作事后归因，不能作为预测或理论最低误差；绝对误差不能按这两个分量直接加减。事后收入反事实剩余5.0782%的量级说明固定比例本身受限，但不能据此声称其贡献了总误差的某个百分比。

三项余额不是完整非现金营运资本，资产净额变化也不等于现金流变化。预付款、合同项、其他经营往来、减值、并购、外币及非现金变化未因此闭合。所有位置`actual_fcff`仍为空；没有新每股估值、生产政策或数据库变更。

本轮不调整失败的比例权重、不事后为公司选择收入模型。后续[同六份报告直接余额检验](direct-balances.md)已完成，未支持整体替换；下一步如扩展，应优先验证能够同时预测利润和投入需求的完整前瞻路径；本结果仅支持把“收入假设”和“资本需求假设”分别检验，不能自动拼接为可采用的完整DCF。

## 复验与边界

复用现有源值、日期与季度收入工具，无新增依赖或运行时模块。下方一次性片段从已提交源快照和冻结预测重建结果，并逐字节比较解压回执。含18位置/源哈希、独立交叉相乘、有符号归因、未来存货位篡改、未来季度缺失与基期FN314过晚拒绝检查。未来实际变动不影响18项预测；缺2025Q4仅阻断3项安克实际值；2022年末基期晚于2023截止仅阻断3项基期预测。

本地运行通过，文档链接及diff检查通过；没有生产代码改动，未重跑Go全套。本片段尚未进入CI，不把既有CI当作其覆盖；只需已有Python环境及仓库证据，不读取原文PDF或访问网络，本轮不重复上一轮PDF校验。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
from collections import Counter,defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import gzip,hashlib,json
from pathlib import Path
from tools.backtest_tdx_history import at,available,value
from tools.backtest_tdx_working_cash import revenue_window
p=Path('valuation/research/analyst-revenue-latest');plan=json.loads((p/'capital-transmission-plan.json').read_bytes())
loaded={}
for path,sha in plan['inputs'].items():
 raw=Path(path).read_bytes();assert hashlib.sha256(raw).hexdigest()==sha;loaded[Path(path).name]=json.loads(raw)
source=loaded['capital-snapshot.json'];baseline=loaded['comparison.json'];fields=tuple(plan['fields']);models=('broker','current_rule_fy_bridge','zero_growth_fy_bridge','repeat_balance')
assert len(baseline['results'])==18 and {(r['code'],r['origin_year'],r['target_year']-r['origin_year']) for r in baseline['results']}=={(c,o,h) for c in plan['codes'] for o in plan['origins'] for h in plan['horizons']}
def evaluate(s):
 index=defaultdict(list);artifacts={a['file']:a for a in s['artifacts']};assert len(artifacts)==len(s['artifacts'])
 for r in s['records']:index[r['code'],r['period']].append(r)
 def observation(code,year,cutoff):
  end=date(year,12,31);revenue,refs,quarters=revenue_window(index,artifacts,code,end,cutoff)
  if any(value(r,'FN230')<=0 for r in quarters.values()):raise ValueError('nonpositive/ambiguous revenue quarter')
  r=quarters[end.isoformat()];balances={f:value(r,f) for f in fields}
  if any(v<=0 for v in balances.values()):raise ValueError('nonpositive/ambiguous balance')
  return balances,revenue,dict(period=end.isoformat(),artifact=r['artifact'],balance_bits={f:r['bits'][f] for f in fields},revenue_inputs=refs)
 rows=[]
 for b in baseline['results']:
  c=b['code'];o=b['origin_year'];y=b['target_year'];row=dict(code=c,origin=o,target_year=y,horizon=y-o,status='blocked_input',actual_fcff=None);rows.append(row)
  try:
   amounts,rev,refs=observation(c,o-1,b['forecast_as_of'])
   predictions={m:{f:amounts[f]*D(b['predictions_cny'][m])/rev for f in fields} for m in models[:3]};predictions['repeat_balance']=amounts
   row.update(base_source=refs,base_revenue_cny=str(rev),base_balances_cny={f:str(v) for f,v in amounts.items()},predictions_cny={m:{f:str(v) for f,v in vals.items()} for m,vals in predictions.items()},frozen_revenue_predictions_cny=b['predictions_cny'])
   if date(y,12,31)>at(plan['evaluation_as_of']).date():row['status']='not_yet_observable';continue
   row['status']='blocked_actual';actual,actual_r,actual_refs=observation(c,y,plan['evaluation_as_of'])
   errors={m:{f:vals[f]-actual[f] for f in fields} for m,vals in predictions.items()}
   oracle={f:amounts[f]*actual_r/rev-actual[f] for f in fields}
   row.update(status='evaluated',actual_source=actual_refs,actual_balances_cny={f:str(v) for f,v in actual.items()},actual_revenue_cny=str(actual_r),errors_cny={m:{f:str(v) for f,v in vals.items()} for m,vals in errors.items()},signed_net_error_cny={m:str(vals['FN11']+vals['FN17']-vals['FN44']) for m,vals in errors.items()},hindsight_intensity_error_cny={f:str(v) for f,v in oracle.items()})
   for m in models[:3]:
    for f in fields:
     attribution=amounts[f]*(D(b['predictions_cny'][m])-actual_r)/rev+oracle[f]
     assert abs(attribution-errors[m][f])<=D('1e-15')
  except (ValueError,KeyError,ArithmeticError) as error:row['reason']=str(error)
 return rows
def summarize(rows):
 valid=[r for r in rows if r['status']=='evaluated'];metrics={}
 for m in models+('hindsight_actual_revenue',):
  def err(r,f):return abs(D(r['hindsight_intensity_error_cny'][f] if m=='hindsight_actual_revenue' else r['errors_cny'][m][f]))
  metrics[m]=dict(gross_component_mae_pct_revenue=float(sum((sum((err(r,f) for f in fields),D(0))/D(r['actual_revenue_cny'])*100 for r in valid),D(0))/len(valid)) if valid else None,component_wape_pct={f:float(100*sum((err(r,f) for r in valid),D(0))/sum((D(r['actual_balances_cny'][f]) for r in valid),D(0))) if valid else None for f in fields})
 return dict(positions=len(rows),statuses=dict(Counter(r['status'] for r in rows)),metrics=metrics)
rows=evaluate(source);assert Counter(r['status'] for r in rows)=={'evaluated':12,'not_yet_observable':6}
# Independent arithmetic check on one company/year; do not reconstruct source precision.
first=rows[0];direct=next(r for r in source['records'] if r['code']==first['code'] and r['period']==f"{first['origin']-1}-12-31")
for f in fields:
 got=D(first['predictions_cny']['broker'][f]);assert abs(got*D(first['base_revenue_cny'])-value(direct,f)*D(first['frozen_revenue_predictions_cny']['broker']))<D('0.000001')
pred=lambda rs:[r.get('predictions_cny') for r in rs]
changed=deepcopy(source);next(r for r in changed['records'] if r['code']=='300866' and r['period']=='2025-12-31')['bits']['FN17']^=1
altered=evaluate(changed);assert pred(rows)==pred(altered);assert sum(a.get('errors_cny')!=b.get('errors_cny') for a,b in zip(rows,altered))==3
missing=deepcopy(source);missing['records']=[r for r in missing['records'] if not(r['code']=='300866' and r['period']=='2025-12-31')]
blocked=evaluate(missing);assert pred(rows)==pred(blocked) and sum(r['status']=='blocked_actual' for r in blocked)==3
late=deepcopy(source);import struct
next(r for r in late['records'] if r['code']=='300866' and r['period']=='2022-12-31')['bits']['FN314']=struct.unpack('<I',struct.pack('<f',230901))[0]
blocked=evaluate(late);assert sum(r['status']=='blocked_input' for r in blocked)==3
result=dict(plan_sha256=hashlib.sha256((p/'capital-transmission-plan.json').read_bytes()).hexdigest(),summary=summarize(rows),by_company={c:summarize([r for r in rows if r['code']==c]) for c in plan['codes']},by_origin={str(o):summarize([r for r in rows if r['origin']==o]) for o in plan['origins']},by_horizon={str(h):summarize([r for r in rows if r['horizon']==h]) for h in plan['horizons']},results=rows,validation=['source_hashes_and_exact_18_positions','first_case_independent_cross_multiplication','signed_error_attribution_identity','future_inventory_bit_changes_three_actual_errors_only','missing_future_quarter_retains_18_predictions','late_base_FN314_blocks_three_inputs'],decision=plan['decision'],boundary=plan['boundary'])
encoded=(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode();path=p/'capital-transmission-result.json.gz'
if path.exists():assert gzip.decompress(path.read_bytes())==encoded
else:path.write_bytes(gzip.compress(encoded,mtime=0))
print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
for group in ('by_company','by_origin'):
 for k,v in result[group].items():print(group,k,{m:round(x['gross_component_mae_pct_revenue'],4) for m,x in v['metrics'].items()})
PY
```
