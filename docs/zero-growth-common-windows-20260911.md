# 零增长两批留出的共同期限复核

结论：中期平均改善在两批不同公司中均存在，应保留零增长作为中期对照；一年期及个别中期窗口反证仍成立，不升级统一增长政策。本次是已公开结果的证据复核，不是新留出或新规则通过验收。

复核动因是区分[早期两年/三年通过](../valuation/research/tdx-zero-growth-validation/README.md)与[后来一至三年未通过](../valuation/research/tdx-zero-growth-horizons/README.md)。不能只引用前者宣称可采用，也不能把后者简化成“所有期限均无改善”。此前前瞻研报试验未支持替换，故回查现有正向证据，避免继续扫描同类候选。

## 范围与结果

共同窗口取两个原实验窗口集合的交集：2023H1→2年、2023H1→3年、2024H1→2年。没有按结果删公司或年份；原实验包含的2022起点、2024/2025一年期结果仍保留原结论。本次不将未包含2022解释成已筛出“正常经营公司”。

两批各120家公司，代码集合互不重叠；每批360个候选位置，分别240/252个可评价，120/108个阻断保留。均沿用原模型准入，不对异常经营做新筛选。两批共享宏观时期与实际目标年度，不能声称独立时间复验或把492个位置视作独立公司。

| 共同三窗口指标 | 早期120家：原规则→零增长 | 后来120家：原规则→零增长 |
|---|---:|---:|
| 收入平均绝对百分比误差 | 27.4777%→24.4310% | 57.2462%→45.4527% |
| 收入WAPE | 14.1603%→13.3637% | 35.8863%→28.2424% |
| EBIT代理绝对误差/实际收入 | 8.7976%→8.1656% | 20.0063%→19.1356% |
| EBIT代理WAPE | 64.9494%→60.9725% | 53.3567%→51.4852% |

早期样本2024H1→2年收入主误差20.4954%→21.1488%，WAPE9.4140%→11.5629%，仍是负向结果；后来样本该窗口两指标改善。不得只取两批平均后掩盖这一差异。两批误差水平差别较大，也不能将某一批误差水平当成全市场预期。

后来实验的一年期合计收入误差16.9985%→17.0777%，2024/2025一年窗口超过事前容忍，原完整实验未通过的决定不变。共同窗口复核不新增“中期通过”门槛，不追认事前实验，也不把一个缺乏稳定依据的5年恒定增长政策换成另一个。

## 对后续工作的约束

中期研究继续同时报告机械增长与零增长基准；提出新候选须说明新增经济信息，并验证相对于两者的效果。近期六份研报已暴露且结果混合，不继续靠选报、科目组合或平滑权重寻优。现有显式零增长估值示例仍只是条件对照：收入更接近实际不证明净再投资归零、利润率、WACC、第四年至终值或股权桥接正确。

这条正向证据支持质疑无条件多年复利外推，并未证明安克等具体公司的零增长合理。没有修改生产估值、默认政策、源事实或原试验通过/失败结论。

## 验证

[回执](acceptance/zero-growth-common-windows-20260911.json)保留原结果/源快照哈希、全部原判定、共同窗口和逐窗口指标。实际使用的16个收入原始包SHA全部一致，取得时间可不同且未覆盖。复用源位解码，从四季度FN230重建492个可评价位置的基期及实际收入，共984个TTM值；原值以百万元保存，比较容忍只针对浮点表示，不修改财务源值。

零增长收入逐项等于基期；利润由利润率乘回产生的浮点误差限制在4 ULP内，不以float64逐位相等冒充经济差异。指标从逐项预测与实际值重新计算，不复制摘要。EBIT语义及原始准入复用既有实验，本次未重跑完整源解析、原实验全部门槛或Go测试，也没有新CI覆盖。

以下一次性复核从仓库根目录执行，结果须与已提交回执逐字节一致；本地已通过，并检查文档链接和diff。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
import gzip,hashlib,json,math
from collections import Counter
from decimal import Decimal as D
from pathlib import Path
from tools.tdx_research_source import source_value as value
root=Path('valuation/research');specs=[('tdx-zero-growth-validation','holdout-result.json','snapshot.json'),('tdx-zero-growth-horizons','holdout-result.json.gz','holdout-snapshot.json')]
data=[];hashes={}
for folder,result,snapshot in specs:
 paths=[root/folder/result,root/folder/snapshot];objects=[]
 for p in paths:
  raw=p.read_bytes();hashes[str(p)]=hashlib.sha256(raw).hexdigest();objects.append(json.loads(gzip.decompress(raw) if p.suffix=='.gz' else raw))
 data.append((folder,*objects))
common=set.intersection(*[{(r['origin'],r['horizon']) for r in result['results']} for _,result,_ in data]);assert common=={('2023-06-30',2),('2023-06-30',3),('2024-06-30',2)}
codes=[{r['code'] for r in result['results']} for _,result,_ in data];assert len(codes[0])==len(codes[1])==120 and not codes[0]&codes[1]
models=('flat_first_five','zero_growth')
def summary(rows):
 valid=[r for r in rows if r['status']=='evaluated'];metrics={}
 for m in models:
  metrics[m]={}
  for field in ('revenue','ebit'):
   errors=[abs(D(str(r['forecasts'][m][field]))-D(str(r['actual'][field]))) for r in valid]
   metrics[m][field+'_mae_pct_actual_revenue']=float(sum((e/D(str(r['actual']['revenue']))*100 for e,r in zip(errors,valid)),D(0))/len(valid))
   metrics[m][field+'_wape_pct']=float(sum(errors,D(0))/sum((abs(D(str(r['actual'][field]))) for r in valid),D(0))*100)
 return dict(positions=len(rows),companies=len({r['code'] for r in rows}),statuses=dict(Counter(r['status'] for r in rows)),metrics=metrics)
cohorts={};used=set();checked=0
for folder,result,source in data:
 idx={(r['code'],r['period']):r for r in source['records']};assert len(idx)==len(source['records'])
 rows=[r for r in result['results'] if (r['origin'],r['horizon']) in common];assert len(rows)==360
 for r in rows:
  if r['status']!='evaluated':continue
  for side in ('base','actual'):
   total=D(0);refs=[ref for ref in r[side]['source_inputs'] if ref['field']=='FN230'];assert len(refs)==4
   for ref in refs:
    sr=idx[r['code'],ref['period']];assert sr['artifact']==ref['artifact'];used.add(ref['artifact']);total+=value(sr,'FN230')*ref['coefficient']
   assert math.isclose(float(total/1000000),r[side]['revenue'],rel_tol=1e-12,abs_tol=1e-9);checked+=1
  assert r['forecasts']['zero_growth']['revenue']==r['base']['revenue']
  assert abs(r['forecasts']['zero_growth']['ebit']-r['base']['ebit'])<=4*math.ulp(r['base']['ebit'])
 cohorts[folder]=dict(common=summary(rows),by_window={o+':'+str(h):summary([r for r in rows if (r['origin'],r['horizon'])==(o,h)]) for o,h in sorted(common)},original_decision=result['decision'])
artifacts=[{a['file']:a for a in source['artifacts']} for _,_,source in data]
versions={f:{'sha256': [a[f]['sha256'] for a in artifacts], 'same_content':artifacts[0][f]['sha256']==artifacts[1][f]['sha256']} for f in sorted(used)}
result=dict(inputs=hashes,common_windows=[list(v) for v in sorted(common)],cohort_codes_disjoint=True,cohorts=cohorts,source_version_comparison=versions,reconstructed_base_and_actual_revenues=checked,boundary='posthoc common-window evidence audit; original full-window pass/fail decisions unchanged; cohorts share macro periods and target years, not independent time replications; no new policy, combined significance or DCF accuracy claim')
p=Path('docs/acceptance/zero-growth-common-windows-20260911.json');text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
if p.exists():assert p.read_text()==text
else:p.write_text(text)
for name,c in cohorts.items():
 print(name,c['common'])
 for window,s in c['by_window'].items():print(window,{m:{k:round(v,4) for k,v in x.items()} for m,x in s['metrics'].items()})
print('reconstructed',checked,'source files',len(versions),'same',sum(v['same_content'] for v in versions.values()))
PY
```
