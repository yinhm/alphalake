# 同六份研报的直接营运余额预测

结论：直接券商预测未提供可整体替换的资本需求规则。存货表现弱于既有比例预测；三项共同可比样本仅苏泊尔两个起点，完整余额子集亦未胜过简单重复余额。保留分项改善，不将其拼成事后择优政策。

[协议](direct-balances-plan.json)在查看附表前提交（db016bb）：六份报告、三个FY、三个科目共54个位置，沿用上一轮选报与信息截止，不换报告或补拆未披露金额。对照为[年度资金占用检验](capital-transmission.md)冻结的四条路径，仅在本轮相同可比科目位置上重新计分。实际仍来自TDX源，研报只提供预测，未覆盖标准事实。

## 口径与覆盖

| 报告 | 应收账款FN11 | 存货FN17 | 应付账款FN44 |
|---|---|---|---|
| 安克2023国金 | “应收款项”范围不明 | 缺2020历史锚点 | “应付款项”范围不明 |
| 安克2024开源 | 明确含应收票据，拒绝替代 | 可比 | 明确含应付票据，拒绝替代 |
| 安克2025国信 | “应收款项”范围不明 | 可比 | “应付款项”范围不明 |
| 苏泊尔2023信达 | 可比，票据另列 | 可比 | 可比，票据另列 |
| 苏泊尔2024华安 | 可比 | 可比 | 可比 |
| 苏泊尔2025国金 | “应收款项”范围不明 | 可比 | “应付款项”范围不明 |

可比较行要求明确科目、单位、绝对FY/E列，且报告所列历史金额均可按印刷精度与TDX核对。19个应核历史锚点中18个匹配；安克2023表附带2020列，冻结TDX快照从2021开始，故即使其2021/2022存货匹配，仍按冻结规则阻断该报告3项存货预测。不事后放宽为“部分历史匹配即可”；这不证明报告数字错误。

四份报告的“款项”或票据合并范围导致24个位置阻断，其中14个已到期、10个未来。历史锚点缺口另阻断3个到期位置；剩余19个到期位置可评价、8个未来位置保留。全部54个科目位置分别对应36个到期、18个未来，不把未来与缺口混算成功率。

[源清单](direct-balances-sources.json)保留页码、PDF哈希、单位/年份、仅该科目数值摘录、历史位值比较及拒绝原因；文件URL和取得时间沿用[sources.json](sources.json)。附表均为百万元：两份国金取右侧资产负债表，其余取左侧；双表头必须年份完全相同后取一侧，不能把两张表数字连成一行。仅有“存货”标签的报告通过历史净额锚点对照，不能由此泛化到其他报告。

两份国金的应收/应付标签不明确，历史数字即使碰巧接近也不升级口径。研报后来取得、目录/印刷日期及TDX FN314规则继续沿用简化回溯边界，不声称独立审计或严格PIT。

## 同分母结果

| 分量 | 可评价科目年度数 | 直接研报WAPE | 研报收入×冻结比例WAPE | 原规则收入×冻结比例WAPE | 重复余额WAPE |
|---|---:|---:|---:|---:|---:|
| 应收账款净额 | 5 | 13.7408% | 18.7392% | 28.8939% | 20.3180% |
| 存货净额 | 9 | 20.8849% | 13.8388% | 12.9644% | 22.1506% |
| 应付账款 | 5 | 14.1588% | 7.6123% | 18.7946% | 13.9931% |

直接应收预测在这5个位置改善，但只有苏泊尔2023/2024起点，不能据此推广两家公司或全市场。存货2023/2024起点的收入归一化误差均比原规则更差，2025起点改善；不选择性忽略早期结果。

三项均可比的仅**苏泊尔2023/2024起点、5个到期目标位置**，重复使用3个实际年度：三分量绝对误差之和/实际收入的均值，直接研报6.3003%，研报收入×冻结比例4.9429%，原规则收入×冻结比例7.1330%，零增长收入×冻结比例5.5759%，直接重复余额5.3553%。不能把该5位置结果与上轮12位置结果直接相减称改善。

[全量回执](direct-balances-result.json)同时保留逐公司、逐起点、逐科目、全部预测和拒绝。三科目都不等于完整非现金营运资本，更不等于营运现金流或FCFF；所有`actual_fcff`仍为空。结果不支持将“更详细的券商附表”直接升级为更准确的估值输入，也不支持只挑直接应收、机械存货、另一套应付来拼接最佳历史误差。

继续主线时，应保留来源交叉检查能力，但停止调整这六份报告的选取或科目组合来寻优；下一项须有不同的经济依据或独立样本，不能把重复诊断算作估值准确度已提高。

## 复验

以下一次性片段依赖既有Python/pypdf环境与本地六份PDF，无网络与新依赖，缺文件或哈希变化即失败。按协议重提取18行、核验历史锚点、重建54位置与同分母结果，逐字节比较两个已提交JSON。错误单位、历史锚点加1百万元、明确标签改成歧义标签均有拒绝检查。比例预测和实际值直接消费上轮已验证且哈希锁定的回执，不另建估值或预测引擎。

本地复验、文档链接与diff检查通过。无生产代码、数据库、依赖变更，未重跑Go；此一次性PDF校验未接入CI，不以既有CI覆盖冒称本轮已自动运行。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
from collections import Counter
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import gzip,hashlib,io,json,re
from pypdf import PdfReader
from tools.backtest_tdx_history import at,available,value
p=Path('valuation/research/analyst-revenue-latest');plan=json.loads((p/'direct-balances-plan.json').read_bytes());inputs={}
for file,sha in plan['inputs'].items():
 raw=Path(file).read_bytes();assert hashlib.sha256(raw).hexdigest()==sha;inputs[Path(file).name]=json.loads(gzip.decompress(raw) if file.endswith('.gz') else raw)
sources=inputs['sources.json'];source=inputs['capital-snapshot.json'];baseline=inputs['capital-transmission-result.json.gz'];artifacts={a['file']:a for a in source['artifacts']}
specs=[(2,['应收款项','存货','应付款项']),(6,['应收票据及应收账款','存货','应付票据及应付账款']),(3,['应收款项','存货净额','应付款项']),(3,['应收账款','存货','应付账款']),(3,['应收账款','存货','应付账款']),(2,['应收款项','存货','应付款项'])]
fields=tuple(plan['fields']);exact={'FN11':('应收账款',),'FN17':('存货','存货净额'),'FN44':('应付账款',)}
def parse(header,line,label,unit):
 assert '百万元' in re.sub(r'\s+','',unit)
 cols=re.findall(r'(20\d{2})([AE]?)',header);assert len(cols)%2==0 and cols[:len(cols)//2]==cols[len(cols)//2:];cols=cols[:len(cols)//2]
 tail=line.split(label,1)[1];match=re.match(r'\s*((?:-?\d[\d,]*(?:\.\d+)?\s+)+)',tail+' ');assert match
 numbers=re.findall(r'-?\d[\d,]*(?:\.\d+)?',match[1]);assert len(numbers)==len(cols)
 return [(int(y),k,n.replace(',','')) for (y,k),n in zip(cols,numbers)]
def inspect(e):
 if e['label'] not in exact[e['field']]:return 'blocked_scope',[]
 checks=[]
 for y,k,n in e['values']:
  if k=='E':continue
  records=[r for r in source['records'] if r['code']==e['code'] and r['period']==f'{y}-12-31']
  if len(records)!=1:checks.append(dict(year=y,status='missing_or_duplicate_historical_anchor'));continue
  r=records[0];a=artifacts[r['artifact']]
  if a['report_period']!=r['period'] or available(r,a)>at(e['cutoff']):checks.append(dict(year=y,status='anchor_not_available'));continue
  amount=value(r,e['field']);printed=D(n);match=(amount/1000000).quantize(D(1).scaleb(printed.as_tuple().exponent))==printed
  checks.append(dict(year=y,status='matched' if match else 'historical_value_conflict',printed_million_cny=n,tdx_cny=str(amount),artifact=r['artifact'],bits=r['bits'][e['field']]))
 return ('eligible' if checks and all(c['status']=='matched' for c in checks) else 'blocked_historical_anchor'),checks
evidence=[]
for s,(page,labels) in zip(sources,specs):
 raw=(Path('workspace/analyst-revenue-latest-20260911')/s['pdf_file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==s['metadata']['sha256']
 lines=PdfReader(io.BytesIO(raw)).pages[page-1].extract_text().splitlines();year=int(s['provider_date'][:4])
 for f,label in zip(fields,labels):
  matches=[(i,line.strip()) for i,line in enumerate(lines) if re.search(re.escape(label)+r'\s+\d',line)];assert len(matches)==1
  i,line=matches[0];header=next(l.strip() for l in reversed(lines[:i]) if len(re.findall(r'20\d{2}',l))>=6)
  unit=next(l.strip() for l in reversed(lines[:i]) if '百万元' in re.sub(r'\s+','',l))
  values=parse(header,line,label,unit);assert [y for y,k,n in values if k=='E']==list(range(year,year+3))
  e=dict(code=s['code'],origin=year,info_code=s['info_code'],pdf_sha256=s['metadata']['sha256'],page=page,field=f,label=label,header=header,unit_line=unit,component_line=label+' '+ ' '.join(n for y,k,n in values),values=values,cutoff=f'{year}-09-01T00:00:00+08:00');e['status'],e['historical_checks']=inspect(e);evidence.append(e)
  try:parse(header,line,label,unit.replace('百万元','万元'))
  except AssertionError:pass
  else:raise AssertionError('wrong unit accepted')
rows=[]
for e in evidence:
 for y,k,n in e['values']:
  if k!='E':continue
  b=next(r for r in baseline['results'] if (r['code'],r['origin'],r['target_year'])==(e['code'],e['origin'],y));f=e['field'];row=dict(code=e['code'],origin=e['origin'],target_year=y,field=f,status=e['status'],source_info_code=e['info_code'],direct_forecast_cny=str(D(n)*1000000),actual_fcff=None);rows.append(row)
  if e['status']!='eligible':continue
  row['status']=b['status']
  if b['status']!='evaluated':continue
  predictions={m:D(v[f]) for m,v in b['predictions_cny'].items()};predictions['direct_broker']=D(n)*1000000;actual=D(b['actual_balances_cny'][f])
  row.update(actual_cny=str(actual),actual_revenue_cny=b['actual_revenue_cny'],predictions_cny={m:str(v) for m,v in predictions.items()},errors_cny={m:str(v-actual) for m,v in predictions.items()})
assert len(rows)==plan['expected_positions']
models=tuple(baseline['summary']['metrics'])[:-1]+('direct_broker',)
def summarize(rs):
 valid=[r for r in rs if r['status']=='evaluated']
 return dict(positions=len(rs),statuses=dict(Counter(r['status'] for r in rs)),metrics={m:dict(wape_pct=float(100*sum((abs(D(r['errors_cny'][m])) for r in valid),D(0))/sum((D(r['actual_cny']) for r in valid),D(0))) if valid else None,mae_pct_revenue=float(sum((abs(D(r['errors_cny'][m]))/D(r['actual_revenue_cny'])*100 for r in valid),D(0))/len(valid)) if valid else None) for m in models})
full_keys=[(b['code'],b['origin'],b['target_year']) for b in baseline['results'] if sum(r['status']=='evaluated' and (r['code'],r['origin'],r['target_year'])==(b['code'],b['origin'],b['target_year']) for r in rows)==3]
full=[r for r in rows if (r['code'],r['origin'],r['target_year']) in full_keys]
# Changing a historical anchor or account label must remove eligibility, not create a better forecast.
clean=next(e for e in evidence if e['status']=='eligible');bad=deepcopy(clean);y,k,n=bad['values'][0];bad['values'][0]=(y,k,str(D(n)+1));assert inspect(bad)[0]=='blocked_historical_anchor'
bad=deepcopy(clean);bad['label']='应收款项';assert inspect(bad)[0]=='blocked_scope'
result=dict(plan_sha256=hashlib.sha256((p/'direct-balances-plan.json').read_bytes()).hexdigest(),coverage=dict(Counter(r['status'] for r in rows)),by_field={f:summarize([r for r in rows if r['field']==f]) for f in fields},by_company={c:{f:summarize([r for r in rows if r['code']==c and r['field']==f]) for f in fields} for c in plan['codes']},by_origin={str(o):{f:summarize([r for r in rows if r['origin']==o and r['field']==f]) for f in fields} for o in plan['origins']},complete_three_component_positions=len(full_keys),complete_three_component_gross_mae_pct_revenue={m:float(sum((abs(D(r['errors_cny'][m]))/D(r['actual_revenue_cny'])*100 for r in full),D(0))/len(full_keys)) if full_keys else None for m in models},results=rows,validation=['all_frozen_input_and_PDF_hashes','54_positions_retained','wrong_unit_rejected','historical_anchor_tamper_removes_eligibility','ambiguous_label_never_promoted_by_matching_numbers'],decision=plan['decision'],boundary=plan['boundary'])
for name,data in [('direct-balances-sources.json',evidence),('direct-balances-result.json',result)]:
 text=json.dumps(data,ensure_ascii=False,indent=2)+'\n';path=p/name
 if path.exists():assert path.read_text()==text
 else:path.write_text(text)
print(result['coverage']);print('complete',result['complete_three_component_positions'],result['complete_three_component_gross_mae_pct_revenue'])
for f,s in result['by_field'].items():print(f,s)
for e in evidence:print(e['code'],e['origin'],e['field'],e['status'],[c for c in e['historical_checks'] if c['status']!='matched'])
PY
```
