# 苏泊尔：同券商规则未取得历史覆盖

[事前协议](supor-plan.json)固定苏泊尔、东吴证券、三个年度8月1日—9月1日查询及原截止口径。实际同券商覆盖**0/3**，没有可评价的苏泊尔预测，不生成误差或以其他券商替补。该固定来源规则尚不能跨第二家公司复验，不能提升为全市场估值的必需输入。

## 实际返回

| 起点 | 目录条数 | 同券商条数 | 处理 |
| --- | ---: | ---: | --- |
| 2023H1 | 4 | 0 | 返回其他券商，保留未覆盖 |
| 2024H1 | 0 | 0 | 合法空目录，保留未覆盖 |
| 2025H1 | 1 | 0 | 返回其他券商，保留未覆盖 |

[三个原请求回执](supor-directories.json)、对应无损压缩响应及[完整覆盖结果](supor-coverage.json)均保留。三个位置的`annual_forecasts`为空，不是零收入预测。与安克合看，固定两家公司、三个起点的6个报告位置只取得3个；不冒称全市场覆盖率，也不把源覆盖失败说成预测误差变差。

2024响应为`hits=0, size=0, TotalPage=0, pageNo=1, data=[]`。初始探针沿用非空目录的`TotalPage=1`断言而停止；已改为按实际空/非空响应核验，并保留零报告分母。这是本轮一次性盘点的修正，没有发布通用API适配器或静默跳过失败。

2023首轮归档未保留精确时刻，回执只记录首次观察日期2026-09-11，`archived_at`留空；不以再次读取时间补写为首次取得。其余响应保留首次归档时间。所有查询都是今天取得的历史目录，不证明过去的完整公开范围。

## 边界复核

不能由上述空结果推断“苏泊尔没有研报”或“东吴从未覆盖苏泊尔”。本轮只确认该接口、该查询范围的返回情况：

- 在不变更原选择范围的前提下，另查2024-09-01至09-07，返回一份开源证券2024-09-03记录，仍无东吴；该记录只用于覆盖诊断，不进入原截止前的预测输入。见[请求回执](supor-after-cutoff-source.json)。
- 参考[AKShare公开请求实现](https://raw.githubusercontent.com/akfamily/akshare/main/akshare/stock_feature/stock_research_report_em.py)，显式补齐行业、评级等通配参数和分页别名重查2024原窗口，仍为空。见[显式参数回执](supor-explicit-filters-source.json)。不把参考开源代码当作接口语义证明，结论来自保存的实际响应。

因此本次不能复现安克9.91%的预测误差结果，也不能证明换券商就能改善。原安克9个预测及比较结果保持不变，仍只代表单公司、单券商诊断。若以后研究多机构预测，需另行冻结选择、去重、时点、缺项及比较范围；不能补入其他机构后仍声称相同规则验证成功。

## 验证

本轮无生产代码、依赖或数据库变更，不运行Go/后端全套。以下标准库检查从保存的真实响应重建0/3结果，明确接受真实空目录，并拒绝篡改计数或证券代码；两个额外查询只列诊断，不改变选择。原文预测未新增，不下载多余PDF。该一次性研究检查未接入CI；没有网络依赖或本地跳过分支。文档链接与`git diff --check`通过。

```bash
python3 - <<'PY'
import gzip, hashlib, json
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
p=Path('valuation/research/analyst-revenue-pilot')
plan=json.loads((p/'supor-plan.json').read_bytes())
receipts=json.loads((p/'supor-directories.json').read_bytes())
assert hashlib.sha256((p/'protocol.json').read_bytes()).hexdigest()==plan['baseline_protocol_sha256']
def validate(data,code):
 assert data['pageNo']==1 and data['hits']==data['size']==len(data['data'])
 assert data['TotalPage']==(1 if data['data'] else 0)
 assert len({r['infoCode'] for r in data['data']})==len(data['data'])
 assert all(r['stockCode']==code for r in data['data'])
 return [r for r in data['data'] if r['orgSName']==plan['broker']]
rows=[]
assert sorted(r['year'] for r in receipts)==[int(o[:4]) for o in plan['origins']]
for receipt in receipts:
 query=parse_qs(urlsplit(receipt['url']).query)
 assert query['code']==[plan['code']] and query['beginTime']==[f"{receipt['year']}-08-01"] and query['endTime']==[f"{receipt['year']}-09-01"]
 raw=gzip.decompress((p/receipt['directory_file']).read_bytes());assert hashlib.sha256(raw).hexdigest()==receipt['sha256']
 d=json.loads(raw);same=validate(d,plan['code']);assert not same
 rows.append(dict(code=plan['code'],origin=f"{receipt['year']}-06-30",status='no_same_broker_record_in_returned_window',directory_rows=len(d['data']),same_broker_records=0,annual_forecasts=None,reason='empty_returned_directory' if not d['data'] else 'returned_reports_from_other_brokers',directory_file=receipt['directory_file'],sha256=receipt['sha256']))
 assert receipt['matches']==[]
 bad=deepcopy(d);bad['hits']+=1
 try:validate(bad,plan['code'])
 except AssertionError:pass
 else:raise AssertionError('incomplete count accepted')
 if d['data']:
  bad=deepcopy(d);bad['data'][0]['stockCode']='300866'
  try:validate(bad,plan['code'])
  except AssertionError:pass
  else:raise AssertionError('wrong company accepted')
checks=[]
for file,meta in [('supor-after-cutoff-2024.json.gz','supor-after-cutoff-source.json'),('supor-explicit-filters-2024.json.gz','supor-explicit-filters-source.json')]:
 raw=gzip.decompress((p/file).read_bytes());m=json.loads((p/meta).read_bytes());assert hashlib.sha256(raw).hexdigest()==m['sha256'];d=json.loads(raw);same=validate(d,plan['code']);assert not same
 if file.startswith('supor-after'):
  assert all(r['publishDate'][:10]>'2024-09-01' for r in d['data'])
 else:assert d['data']==[]
 checks.append(dict(file=file,sha256=m['sha256'],rows=len(d['data']),same_broker_records=len(same),use='diagnostic_only_not_added_to_selection'))
result=dict(plan_sha256=hashlib.sha256((p/'supor-plan.json').read_bytes()).hexdigest(),companies=1,requested_report_positions=3,matched_report_positions=0,missing_positions=3,results=rows,diagnostic_checks=checks,forecast_accuracy=None,decision='cannot_replicate_same_broker_rule_on_second_company; do_not_replace_broker_or_fill_forecasts',boundary='returned_directory_scope_only_not_proof_no_research_exists; no_dates_or_cutoffs_relaxed; prior_anker_accuracy_not_generalized',validation=['real_empty_directory_accepted_with_TotalPage_zero','missing_count_tamper_rejected','wrong_company_tamper_rejected'])
encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n';target=p/'supor-coverage.json'
if target.exists():assert target.read_text()==encoded
else:target.write_text(encoded)
print('Supor 0/3 same-broker coverage; real empty response retained; count and identity tamper rejected')

PY
```
