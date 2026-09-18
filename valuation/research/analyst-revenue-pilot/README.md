# 公司前瞻收入来源：安克三期券商预测试验

已获得并核验同一家券商三份原始研报的9个年度收入预测。6个到期预测的平均绝对百分误差为9.9122%，同年度口径机械基线为10.5274%；但2023、2025起点均更差，不能据总体小幅改善采用。该来源可供后续研究，尚不支持替换生产预测或声称估值准确度提高。

## 来源选择与时点

[协议](protocol.json)以`e2a457f`冻结比较公式与范围。最先检索到2023年东吴证券安克半年报点评后，固定同一券商，查询三个年度8月1日—9月1日窗口，并选择该接口唯一返回的同公司半年报点评。三个真实目录响应分别5、3、9条，均为一页；不声称检索了全市场所有研报、所有修订版本或未发表预测，亦不是市场一致预期。原五家三起点15个公司/起点位置中，本轮仅研究安克3个，其余12个未展开，不标成数据缺失或验证通过。

[来源回执](sources.json)保存请求URL、原目录哈希、原PDF URL/哈希、首次归档时间、PDF页码及两行表格证据。目录原响应已无损压缩入库；三份完整PDF共约1.6MB保留在本地`workspace/analyst-forecast-20260911`，未重复下载或进入Git。文件取得于2026-09-11，不能说当时已留存。

| 研报日期 | 券商原文 | 三个预测年度 | 年度收入预测（百万元） |
| --- | --- | --- | --- |
| 2023-08-30 | [东吴2023半年报点评](https://pdf.dfcfw.com/pdf/H3_AP202308301596817366_1.pdf) | 2023 / 2024 / 2025 | 17234 / 20504 / 23061 |
| 2024-08-30 | [东吴2024半年报点评](https://pdf.dfcfw.com/pdf/H3_AP202408301639629259_1.pdf) | 2024 / 2025 / 2026 | 23266 / 29612 / 36193 |
| 2025-08-31 | [东吴2025半年报点评](https://pdf.dfcfw.com/pdf/H3_AP202508311737338558_1.pdf) | 2025 / 2026 / 2027 | 32853 / 41968 / 50768 |

三个目录的`publishDate`与PDF印刷日期一致，但都是日精度；接口的`00:00:00.000`不解释为已知精确发布时间。研究采用该日期的中国次日零点作为可用日代理，均不晚于各年9月1日截止。印刷日期与今日返回的历史条目相符，仍不证明当时首次公开的准确时点或版本存续，不能称严格PIT。

**三个历史查询的顶层`currentYear`都为2026。** 不据此解释`predictThisYearEps`等相对年份字段，也不把返回的当前行业标签当作历史分类。本试验只从PDF绝对年份列读取E预测，A历史列单独识别；五个A列收入锚点另与TDX全年FN230合计按百万元显示精度匹配。这支持本样本的数量级及期间衔接，不能推及所有券商的营业总收入/营业收入口径。

[9个预测值](forecasts.csv)属于外部分析者预测，不是TDX或公司报告事实；不回写标准财务库，不把券商“买入”评级当作正确性标签，不读取其目标价来评价估值。

## 同年度口径比较

研报预测完整日历年度收入，不能直接代入原来“次年H1 TTM”的预测位。协议已明确一个独立研究桥接：

- 机械当年FY收入＝已知本年H1＋上年H2×(1＋原起点冻结增长率)。之后两个FY逐年乘同一增长率，沿用原前五年不衰减设定。
- 零增长对照＝已知本年H1＋上年H2，后续年度保持同值。
- 券商对照直接读取研报FY预测，不拆成猜测的季度值。
- 实际值逐年加总TDX四个FN230季度，保留源位与公告日代理检查，不由PDF替换。2026、2027全年尚未到期，保留3项未观察预测。

这不是生产引擎已存在的全年预测接口，也不是把H1 TTM结果改标签；只比较相同目标年度的收入，不涉及利润、WACC、资本效率或股本。

| 来源起点 | 已到期预测数 | 券商MAE/实际收入 | 机械FY桥接 | 零增长FY桥接 |
| --- | ---: | ---: | ---: | ---: |
| 2023 | 3 | 14.3360% | 12.9123% | 32.9565% |
| 2024 | 2 | 4.4007% | 11.5148% | 26.4303% |
| 2025 | 1 | 7.6639% | 1.3976% | 8.4743% |
| 合计 | 6 | 9.9122% | 10.5274% | 26.7007% |

[完整比较](comparison.json)保留9个位置、三个模型的原金额、有符号误差、按目标年份及起点的汇总。总体WAPE分别为10.4863%、10.9721%、27.8133%。只有一家公司、三个不同实际年度，6个预测重复使用部分实际年度，不是6个独立样本；起点比较又混合不同预测期限。未设置事后通过门槛，不把小幅均值优势解释为统计显著、普遍有效或足以提高完整DCF准确度。

第二家[苏泊尔的固定来源覆盖检验](supor-coverage.md)已完成，同券商0/3，尚不能跨公司复现；合法空目录及返回其他机构的情况均保留，不替换券商或填零。该固定来源规则不提升为通用估值的必需输入；多机构方案若要研究，须另行冻结选择与比较范围。暂停历史平滑候选的决定保持不变。

## 验证范围

原文重提取9个E预测，单位、A/E标记及PDF字节篡改均拒绝。比较部分另验证五个A历史锚点、未来目标季度位变化只改变实际误差、不改变所有预测；缺失2025Q4时保留9个位置及预测，将相应3项标为实际数据阻断。

无生产代码、迁移、依赖或数据库变更，本轮不重跑Go/后端全套。两个脚本均本地执行通过，尚未接入CI：源校验必须有三份原PDF，缺失直接失败，没有静默降级；比较脚本只依赖已提交的数据和现有后端环境。PDF可按`sources.json`的URL重新取得，但必须与保存的SHA-256一致；重新下载时间不改写首次归档记录。文档链接及`git diff --check`已检查。

### 原文与目录重放

从仓库根目录，在已有pypdf环境下执行；原PDF应置于上述workspace目录。

```bash
workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
import csv, gzip, hashlib, io, json, re
from datetime import date, timedelta
from pathlib import Path
from pypdf import PdfReader
p=Path('valuation/research/analyst-revenue-pilot');raw_dir=Path('workspace/analyst-forecast-20260911')
plan=json.loads((p/'protocol.json').read_bytes())
recorded={r['info_code']:r for r in json.loads((p/'sources.json').read_bytes())}
def parse(header, revenue_line, report_year):
 assert '盈利预测与估值' in header and '营业总收入（百万元）' in revenue_line
 columns=re.findall(r'(\d{4})([AE])',header)
 amounts=re.findall(r'\d[\d,]*(?:\.\d+)?',revenue_line.split('营业总收入（百万元）',1)[1])
 assert len(columns)==len(amounts) and len(set(columns))==len(columns)
 expected=[(int(year),amount.replace(',','')) for (year,kind),amount in zip(columns,amounts) if kind=='E']
 assert [year for year,_ in expected]==list(range(report_year,report_year+3))
 assert all(int(year)<report_year for year,kind in columns if kind=='A')
 return expected
sources=[];forecasts=[]
for planned in plan['reports']:
 info=planned['info_code'];year=int(planned['provider_report_date'][:4]);directory_bytes=gzip.decompress((p/recorded[info]['directory_file']).read_bytes())
 d=json.loads(directory_bytes);dm=recorded[info]['directory_source']
 assert hashlib.sha256(directory_bytes).hexdigest()==dm['sha256']
 assert d['TotalPage']==d['pageNo']==1 and d['hits']==d['size']==len(d['data'])
 chosen=[r for r in d['data'] if r['stockCode']==plan['code'] and r['orgSName']==plan['broker'] and '半年报' in r['title']]
 assert len(chosen)==1 and chosen[0]['infoCode']==info and chosen[0]['publishDate']==planned['provider_report_date']
 assert all(r['stockCode']==plan['code'] for r in d['data'])
 pdf=raw_dir/f'{info}.pdf';pm=recorded[info]['pdf_source'];pdf_bytes=pdf.read_bytes()
 def verify_bytes(data):
  assert hashlib.sha256(data).hexdigest()==pm['sha256']
  return PdfReader(io.BytesIO(data)).pages[0].extract_text()
 text=verify_bytes(pdf_bytes);compact=re.sub(r'\s+','',text)
 assert '安克创新（300866）' in compact and '东吴证券研究所' in compact
 printed_dates=re.findall(r'(\d{4})\s*年\s*(\d{2})\s*月\s*(\d{2})\s*日',text)
 assert len(printed_dates)==1
 printed='-'.join(printed_dates[0]);assert printed==planned['provider_report_date'][:10]
 available_proxy=(date.fromisoformat(printed)+timedelta(days=1)).isoformat()
 assert available_proxy<=f'{year}-09-01'
 headers=[line.strip() for line in text.splitlines() if '盈利预测与估值' in line]
 revenue_lines=[line.strip() for line in text.splitlines() if '营业总收入（百万元）' in line]
 assert len(headers)==len(revenue_lines)==1
 header,line=headers[0],revenue_lines[0];values=parse(header,line,year)
 for target,amount in values:
  forecasts.append(dict(info_code=info,code=plan['code'],broker=plan['broker'],report_date=printed,availability_date_proxy=available_proxy,target_year=target,metric='broker_total_revenue_forecast',value=amount,unit='million_CNY',period_basis='fiscal_year',source_page=1))
 archive=p/f'directory-{year}.json.gz'
 if archive.exists():assert gzip.decompress(archive.read_bytes())==directory_bytes
 else:archive.write_bytes(gzip.compress(directory_bytes,mtime=0))
 sources.append(dict(info_code=info,directory_file=archive.name,directory_source=dm,pdf_source=pm,pdf_file=pdf.name,raw_pdf_directory=str(raw_dir),source_page=1,provider_report_date=planned['provider_report_date'],printed_report_date=printed,date_precision='day_not_exact_midnight',availability_basis='provider_and_printed_report_date_proxy_not_historical_capture',table_header=header,revenue_line=line,api_current_year=d['currentYear']))
 changed=bytearray(pdf_bytes);changed[-1]^=1
 try:verify_bytes(bytes(changed))
 except AssertionError:pass
 else:raise AssertionError('PDF byte tamper accepted')
 for changed_header,changed_line in ((header,line.replace('百万元','万元')),(header.replace(str(year)+'E',str(year)+'A'),line)):
  try:parse(changed_header,changed_line,year)
  except AssertionError:pass
  else:raise AssertionError('unit or forecast/actual label tamper accepted')
assert len(forecasts)==9
out=io.StringIO();writer=csv.DictWriter(out,fieldnames=list(forecasts[0]),lineterminator='\n');writer.writeheader();writer.writerows(forecasts)
outputs={'sources.json':json.dumps(sources,ensure_ascii=False,indent=2)+'\n','forecasts.csv':out.getvalue()}
for name,data in outputs.items():
 target=p/name
 if target.exists():assert target.read_text()==data
 else:target.write_text(data)
print('3 raw directories, 3 original PDFs, 9 FY forecasts; PDF/unit/A-E tamper rejected')
PY
```

### 年度收入比较重放

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
import csv, hashlib, json, re
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from tools.backtest_tdx_history import available, at
from tools.tdx_research_source import financial_value as value, source_field
p=Path('valuation/research/analyst-revenue-pilot');plan=json.loads((p/'protocol.json').read_bytes())
source_path=Path('valuation/research/continuing-operations-five/capital-snapshot.json');baseline_path=Path('valuation/research/continuing-operations-five/baseline.json')
source=json.loads(source_path.read_bytes());base=json.loads(baseline_path.read_bytes());forecasts=list(csv.DictReader((p/'forecasts.csv').open()))
growth={int(o['origin'][:4]):D(str(next(r for r in o['results'] if r['code']==plan['code'])['rule_evidence']['clipped_scenario_growth'])) for o in base['origins']}
def evaluate(s):
 artifacts={a['file']:a for a in s['artifacts']};assert len(artifacts)==len(s['artifacts'])
 def revenues(year,quarters,cutoff):
  total=D(0);refs=[]
  for month in quarters:
   period=f'{year}-{month}';rows=[r for r in s['records'] if r['code']==plan['code'] and r['period']==period]
   if len(rows)!=1:raise ValueError('missing_or_duplicate_quarter')
   r=rows[0];a=artifacts[r['artifact']]
   if a['report_period']!=period or available(r,a)>at(cutoff):raise ValueError('quarter_period_or_cutoff')
   amount=value(r,'revenue')
   if amount<=0:raise ValueError('nonpositive_or_ambiguous_quarter_revenue')
   total+=amount;refs.append(dict(period=period,artifact=r['artifact'],field='FN230',bits=r['bits']['FN230']))
  return total,refs
 anchors=0
 for evidence in json.loads((p/'sources.json').read_bytes()):
  columns=re.findall(r'(\d{4})([AE])',evidence['table_header'])
  amounts=re.findall(r'\d[\d,]*(?:\.\d+)?',evidence['revenue_line'].split('营业总收入（百万元）',1)[1])
  for (year,kind),amount in zip(columns,amounts):
   if kind!='A':continue
   actual,_=revenues(int(year),('03-31','06-30','09-30','12-31'),evidence['printed_report_date']+'T23:59:59+08:00')
   assert (actual/1000000).quantize(D('1'))==D(amount.replace(',',''))
   anchors+=1
 assert anchors==5
 rows=[]
 for f in forecasts:
  origin=int(f['report_date'][:4]);year=int(f['target_year']);cutoff=f'{origin}-09-01T00:00:00+08:00'
  row=dict(info_code=f['info_code'],origin_year=origin,target_year=year,forecast_as_of=cutoff,status='blocked_inputs',actual_revenue_cny=None);rows.append(row)
  try:
   h1,refs1=revenues(origin,('03-31','06-30'),cutoff);prior_h2,refs2=revenues(origin-1,('09-30','12-31'),cutoff)
   current=(h1+prior_h2*(1+growth[origin]))*(1+growth[origin])**(year-origin)
   predictions=dict(broker=D(f['value'])*1000000,current_rule_fy_bridge=current,zero_growth_fy_bridge=h1+prior_h2)
   row.update(growth=str(growth[origin]),known_h1_cny=str(h1),prior_h2_cny=str(prior_h2),forecast_source_inputs=refs1+refs2,predictions_cny={k:str(v) for k,v in predictions.items()})
   if date(year,12,31)>at(plan['comparison_plan']['evaluation_as_of']).date():row['status']='not_yet_observable';continue
   row['status']='blocked_actual'
   actual,refs=revenues(year,('03-31','06-30','09-30','12-31'),plan['comparison_plan']['evaluation_as_of'])
   row.update(status='evaluated',actual_revenue_cny=str(actual),actual_source_inputs=refs,errors_pct_actual={k:float((v-actual)/actual*100) for k,v in predictions.items()})
  except (ValueError,KeyError,ArithmeticError) as error:row['reason']=str(error)
 return rows
def summary(rows):
 valid=[r for r in rows if r['status']=='evaluated'];models={}
 for m in ('broker','current_rule_fy_bridge','zero_growth_fy_bridge'):
  models[m]=dict(n=len(valid),mae_pct_actual=float(sum((abs(D(r['predictions_cny'][m])-D(r['actual_revenue_cny']))/D(r['actual_revenue_cny'])*100 for r in valid),D(0))/len(valid)) if valid else None,wape_pct=float(100*sum((abs(D(r['predictions_cny'][m])-D(r['actual_revenue_cny'])) for r in valid),D(0))/sum((D(r['actual_revenue_cny']) for r in valid),D(0))) if valid else None)
 return dict(positions=len(rows),statuses=dict(Counter(r['status'] for r in rows)),models=models)
rows=evaluate(source);assert len(rows)==9
changed=deepcopy(source);next(r for r in changed['records'] if r['code']=='300866' and r['period']=='2025-12-31')['bits']['FN230']^=1
altered=evaluate(changed);assert [r.get('predictions_cny') for r in rows]==[r.get('predictions_cny') for r in altered];assert [r.get('errors_pct_actual') for r in rows]!=[r.get('errors_pct_actual') for r in altered]
missing=deepcopy(source);missing['records']=[r for r in missing['records'] if not (r['code']=='300866' and r['period']=='2025-12-31')]
blocked=evaluate(missing);assert [r.get('predictions_cny') for r in rows]==[r.get('predictions_cny') for r in blocked];assert sum(r['status']=='blocked_actual' for r in blocked)==3
result=dict(evidence={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in (source_path,baseline_path,p/'protocol.json',p/'forecasts.csv',p/'sources.json')},summary=summary(rows),by_origin={str(y):summary([r for r in rows if r['origin_year']==y]) for y in growth},by_target_year={str(y):summary([r for r in rows if r['target_year']==y]) for y in range(2023,2028)},results=rows,validation=['future_target_bit_tamper_changes_actual_errors_only','missing_future_actual_keeps_all_forecasts_and_positions','five_reported_A_cells_match_TDX_FY_at_printed_million_precision'],decision='single_company_diagnostic_no_adoption_or_consensus_claim',boundary=plan['boundary'])
encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n';target=p/'comparison.json'
if target.exists():assert target.read_text()==encoded
else:target.write_text(encoded)
print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
PY
```
