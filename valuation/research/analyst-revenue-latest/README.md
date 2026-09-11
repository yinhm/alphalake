# 两家公司最新研报的年度收入试验

结论：覆盖得到补齐，预测改善未跨公司、跨起点成立，不替换生产规则。疫情或异常经营不作本轮解释中心，也不按误差删公司或年份。

先提交[协议](protocol.json)（5465a4c），再取得目录与原文。固定安克300866、苏泊尔002032，2023/2024/2025H1三个起点；截至每年9月1日，在前180天目录内按发布日期最新、同日文件编号字典序最小选择。不看评级、标题或事后误差，不在PDF不合格时换选旧报告。它是独立于[固定东吴来源](../analyst-revenue-pilot/README.md)的新规则，不回填旧实验缺口。协议沿用字段中的`single-company`描述是旧试验文字残留，实际冻结分母由`codes`、`origins`及`boundary`确定为两家公司六个起点，原协议不事后改写。

## 来源与日期

| 公司/起点 | 目录返回条数 | 选中券商 | 目录日期 | PDF印刷日期 | 表格页 |
|---|---:|---|---|---|---:|
| 安克2023 | 13 | 国金证券 | 2023-08-31 | 2023-08-30 | 1 |
| 安克2024 | 11 | 开源证券 | 2024-08-31 | 2024-08-31 | 1 |
| 安克2025 | 18 | 国信证券 | 2025-08-31 | 2025-08-29 | 1 |
| 苏泊尔2023 | 15 | 信达证券 | 2023-08-31 | 2023-08-31 | 2 |
| 苏泊尔2024 | 6 | 华安证券 | 2024-07-25 | 2024-07-25 | 2 |
| 苏泊尔2025 | 10 | 国金证券 | 2025-08-30 | 2025-08-29 | 1 |

[目录清单](directories.json)保留请求URL、分页、选中完整记录和压缩原始响应哈希；[PDF清单](sources.json)保留公开原文URL、实际取得时间、哈希、页码及有限的数值表格摘录。[18项预测](forecasts.csv)单位统一为百万元，均为绝对年份FY预测；不读取接口以当前年份2026为基准的相对EPS字段。

目录日期与印刷日期分别保留，均按中国次日零点判断可用。原文是2026-09-11后来取得，不是当时留存，不声称严格PIT。苏泊尔2024选中的是7月25日快报点评，距截止38天；不凭事后结果改换8月或9月研报。两份国金报告的代码/日期在图像页眉中，已人工渲染核对，脚本仅锁定其PDF哈希，不冒称自动OCR独立核验。苏泊尔2025正文8月28日是公司公告日期，页眉研报日期为8月29日。

六份PDF约4.35MB，保留在本地`workspace/analyst-revenue-latest-20260911/`，不提交全文；裸克隆需按清单URL恢复同哈希文件。校验缺原文即失败，无静默跳过。

## 同口径比较

实际值为既有TDX源快照中各目标年度四个FN230季度相加，信息截止2026-09-10中国零点，保留float32源精度，不用研报历史列覆盖。报告的11个历史收入单元格与TDX年度加总按各自印刷精度（百万元整数或两位小数）全部匹配；这只是期间/单位交叉检查，不等于独立审计。

原规则年度桥接：当年已知H1＋上年H2×(1＋冻结的H1增长率)，后续FY连续乘同一增长率。零增长桥接：已知H1＋上年H2，此后不变。两者与上轮公式一致；不将FY预测插值成H1 TTM。原始源、冻结基线和结果哈希见[比较回执](comparison.json)。

| 范围 | 已到期位置 | 研报收入MAE | 原规则年度桥接MAE | 零增长桥接MAE |
|---|---:|---:|---:|---:|
| 安克 | 6 | 11.3774% | 10.5274% | 26.7007% |
| 苏泊尔 | 6 | 4.8119% | 10.2623% | 5.8218% |
| 合计 | 12 | 8.0947% | 10.3948% | 16.2613% |
| 2023起点 | 6 | 11.0711% | 13.3337% | 21.7165% |
| 2024起点 | 4 | 5.3536% | 10.0545% | 13.9057% |
| 2025起点 | 2 | 4.6477% | 2.2589% | 4.6067% |

MAE为逐位置收入绝对误差/实际收入的等权均值。合计WAPE分别8.7100%、10.6587%、17.7121%。18个预测保留12个到期、6个未来未到期；12个到期位置只对应6个公司/实际年度，重复目标不是12份独立样本。两家公司此前已暴露，不是独立留出，也不是券商一致预期。

安克2023/2025起点更差；苏泊尔2025亦更差，且2024/2025都不如零增长桥接。不能按公司或年份事后择优切换券商/机械预测。暂不进入生产、不产生新每股值：收入比较没有闭合利润、再投资、WACC或终值有效性。继续工作的重点仍是普通经营公司的增长与资本需求能否共同支撑现金流，不继续调这个选报规则的窗口、券商或评级来追求均值改善。

后续[年度营运余额传递检验](capital-transmission.md)保持本轮全部收入预测不变，继续检查投入需求；不因其综合均值变化重新选择研报。

## 复验

仅新增研究证据、结果及文档，不修改生产代码、数据库、依赖、CI或估值政策。以下为一次性复验片段，不在研究目录新增运行时代码。仓库根目录使用既有含pypdf 6.17.0的解释器：第一段重提取原文并逐字节比较提交表，第二段复算18个位置。缺PDF会失败；这两段尚未接入CI，不能用Go测试通过代替其覆盖。两段本地通过；目录日期边界、选择顺序、单位/E标签/PDF哈希篡改、未来实际值篡改及缺季度拒绝均有断言。未来实际变化不能改变18项预测，缺2025Q4只阻断对应3项安克实际值。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
import csv,gzip,hashlib,io,json,re
from datetime import date,timedelta
from pathlib import Path
from pypdf import PdfReader
p=Path('valuation/research/analyst-revenue-latest');rawdir=Path('workspace/analyst-revenue-latest-20260911')
plan=json.loads((p/'protocol.json').read_bytes());directories=json.loads((p/'directories.json').read_bytes())
specs={'AP202308311596906163':(1,'2023-08-30','营业收入(百万元)','visual_page1_header'), 'AP202408311639673106':(1,'2024-08-31','营业收入(百万元)','text_header_review'), 'AP202508311737221901':(1,'2025-08-29','营业收入(百万元)','text_header_review'), 'AP202308311596883311':(2,'2023-08-31','营业总收入(百万元)','text_header_review'), 'AP202407251638296815':(2,'2024-07-25','营业收入','text_header_review'), 'AP202508301736875870':(1,'2025-08-29','营业收入(百万元)','visual_page1_header')}
norm=lambda s:re.sub(r'\s+','',s).replace('（','(').replace('）',')')
def choose(data,cutoff):
 eligible=[r for r in data if date.fromisoformat(r['publishDate'][:10])+timedelta(days=1)<=cutoff]
 return sorted(eligible,key=lambda r:(-date.fromisoformat(r['publishDate'][:10]).toordinal(),r['infoCode']))[0] if eligible else None
sources=[];forecasts=[]
for directory in directories:
 code=directory['code'];year=int(directory['origin'][:4]);cutoff=date(year,9,1);data=[]
 for ref in directory['responses']:
  raw=gzip.decompress((p/ref['file']).read_bytes());assert hashlib.sha256(raw).hexdigest()==ref['sha256'];d=json.loads(raw);data.extend(d['data'])
 assert len(data)==d['hits'] and len({r['infoCode'] for r in data})==len(data)
 assert all(r['stockCode']==code and date.fromisoformat(directory['query_start'])<=date.fromisoformat(r['publishDate'][:10])<=cutoff for r in data)
 selected=choose(data,cutoff);assert selected==directory['selected']==choose(list(reversed(data)),cutoff)
 late=dict(selected,infoCode='late-fixture',publishDate=f'{year}-09-01 00:00:00.000');assert choose(data+[late],cutoff)==selected
 info=selected['infoCode'];page,printed,label,review=specs[info];pdfpath=rawdir/f'{info}.pdf';metadata=json.loads(pdfpath.with_suffix('.source.json').read_bytes());raw=pdfpath.read_bytes()
 assert hashlib.sha256(raw).hexdigest()==metadata['sha256']
 pdf=PdfReader(io.BytesIO(raw));first=norm(pdf.pages[0].extract_text());lines=pdf.pages[page-1].extract_text().splitlines()
 if review=='text_header_review':
  assert code in first
  dates=re.findall(r'(20\d{2})[年-](\d{1,2})[月-](\d{1,2})日?',first)
  assert printed in [date(int(y),int(m),int(d)).isoformat() for y,m,d in dates]
 assert date.fromisoformat(printed)+timedelta(days=1)<=cutoff
 indices=[i for i,line in enumerate(lines) if re.match(re.escape(norm(label))+r'\d',norm(line))];assert len(indices)==1
 i=indices[0];line=lines[i].strip();header=next(x.strip() for x in reversed(lines[:i]) if len(re.findall(r'20\d{2}[AE]?',x))>=3)
 unit_line=line if '百万元' in norm(line) else next(x.strip() for x in reversed(lines[:i]) if '百万元' in norm(x))
 def parse(h,r,u):
  assert '百万元' in norm(u)
  cols=re.findall(r'(20\d{2})([AE]?)',h);amounts=re.findall(r'\d[\d,]*(?:\.\d+)?',r)
  assert len(cols)==len(amounts) and len({y for y,k in cols})==len(cols)
  result=[(int(y),v.replace(',','')) for (y,k),v in zip(cols,amounts) if k=='E'];assert [y for y,v in result]==list(range(year,year+3))
  assert all(int(y)<year for y,k in cols if k!='E')
  return result
 values=parse(header,line,unit_line)
 for target,v in values:forecasts.append(dict(code=code,info_code=info,broker=selected['orgSName'],provider_date=selected['publishDate'][:10],printed_report_date=printed,origin_year=year,target_year=target,value=v,unit='million_CNY',period_basis='fiscal_year',source_page=page))
 sources.append(dict(code=code,info_code=info,pdf_file=pdfpath.name,metadata=metadata,source_page=page,printed_report_date=printed,identity_date_review=review,provider_date=selected['publishDate'][:10],provider_age_days=(cutoff-date.fromisoformat(selected['publishDate'][:10])).days,header=header,revenue_line=line,unit_line=unit_line,label=label))
 for h,r,u in ((header,line,unit_line.replace('百万元','万元').replace('百万 元','万元')),(header.replace(f'{year}E',f'{year}A'),line,unit_line)):
  try:parse(h,r,u)
  except AssertionError:pass
  else:raise AssertionError('unit/forecast label tamper accepted')
 bad=bytearray(raw);bad[-1]^=1;assert hashlib.sha256(bad).hexdigest()!=metadata['sha256']
assert len(sources)==6 and len(forecasts)==18
csvout=io.StringIO();writer=csv.DictWriter(csvout,fieldnames=list(forecasts[0]),lineterminator='\n');writer.writeheader();writer.writerows(forecasts)
for name,text in {'sources.json':json.dumps(sources,ensure_ascii=False,indent=2)+'\n','forecasts.csv':csvout.getvalue()}.items():
 target=p/name
 if target.exists():assert target.read_text()==text
 else:target.write_text(text)
print('6 selections and PDFs; 18 FY forecasts; date/tie selection and unit/E-label/PDF hash checks passed')
PY
```

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
import csv, hashlib, json, re
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from tools.backtest_tdx_history import available, at, value
p=Path('valuation/research/analyst-revenue-latest');plan=json.loads((p/'protocol.json').read_bytes())
source_path=Path('valuation/research/continuing-operations-five/capital-snapshot.json');baseline_path=Path('valuation/research/continuing-operations-five/baseline.json')
source=json.loads(source_path.read_bytes());base=json.loads(baseline_path.read_bytes());forecasts=list(csv.DictReader((p/'forecasts.csv').open()))
for path,sha in plan['inputs'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha
growth={(r['code'],int(o['origin'][:4])):D(str(r['rule_evidence']['clipped_scenario_growth'])) for o in base['origins'] for r in o['results'] if r['code'] in plan['codes']}
def evaluate(s):
 artifacts={a['file']:a for a in s['artifacts']};assert len(artifacts)==len(s['artifacts'])
 def revenues(code,year,quarters,cutoff):
  total=D(0);refs=[]
  for month in quarters:
   period=f'{year}-{month}';rows=[r for r in s['records'] if r['code']==code and r['period']==period]
   if len(rows)!=1:raise ValueError('missing_or_duplicate_quarter')
   r=rows[0];a=artifacts[r['artifact']]
   if a['report_period']!=period or available(r,a)>at(cutoff):raise ValueError('quarter_period_or_cutoff')
   amount=value(r,'FN230')
   if amount<=0:raise ValueError('nonpositive_or_ambiguous_quarter_revenue')
   total+=amount;refs.append(dict(period=period,artifact=r['artifact'],field='FN230',bits=r['bits']['FN230']))
  return total,refs
 anchors=0
 for evidence in json.loads((p/'sources.json').read_bytes()):
  columns=re.findall(r'(20\d{2})([AE]?)',evidence['header'])
  amounts=re.findall(r'\d[\d,]*(?:\.\d+)?',evidence['revenue_line'])
  assert len(columns)==len(amounts)
  for (year,kind),amount in zip(columns,amounts):
   if kind=='E':continue
   actual,_=revenues(evidence['code'],int(year),('03-31','06-30','09-30','12-31'),evidence['printed_report_date']+'T23:59:59+08:00')
   printed=D(amount.replace(',',''));quantum=D(1).scaleb(printed.as_tuple().exponent)
   assert (actual/1000000).quantize(quantum)==printed,(evidence['info_code'],year,str(actual),str(printed))
   anchors+=1
 assert anchors==11
 rows=[]
 for f in forecasts:
  code=f['code'];origin=int(f['origin_year']);year=int(f['target_year']);cutoff=f'{origin}-09-01T00:00:00+08:00'
  row=dict(code=code,broker=f['broker'],provider_date=f['provider_date'],printed_report_date=f['printed_report_date'],info_code=f['info_code'],origin_year=origin,target_year=year,forecast_as_of=cutoff,status='blocked_inputs',actual_revenue_cny=None);rows.append(row)
  try:
   h1,refs1=revenues(code,origin,('03-31','06-30'),cutoff);prior_h2,refs2=revenues(code,origin-1,('09-30','12-31'),cutoff)
   current=(h1+prior_h2*(1+growth[(code,origin)]))*(1+growth[(code,origin)])**(year-origin)
   predictions=dict(broker=D(f['value'])*1000000,current_rule_fy_bridge=current,zero_growth_fy_bridge=h1+prior_h2)
   row.update(growth=str(growth[(code,origin)]),known_h1_cny=str(h1),prior_h2_cny=str(prior_h2),forecast_source_inputs=refs1+refs2,predictions_cny={k:str(v) for k,v in predictions.items()})
   if date(year,12,31)>at(plan['comparison_plan']['evaluation_as_of']).date():row['status']='not_yet_observable';continue
   row['status']='blocked_actual'
   actual,refs=revenues(code,year,('03-31','06-30','09-30','12-31'),plan['comparison_plan']['evaluation_as_of'])
   row.update(status='evaluated',actual_revenue_cny=str(actual),actual_source_inputs=refs,errors_pct_actual={k:float((v-actual)/actual*100) for k,v in predictions.items()})
  except (ValueError,KeyError,ArithmeticError) as error:row['reason']=str(error)
 return rows
def summary(rows):
 valid=[r for r in rows if r['status']=='evaluated'];models={}
 for m in ('broker','current_rule_fy_bridge','zero_growth_fy_bridge'):
  models[m]=dict(n=len(valid),mae_pct_actual=float(sum((abs(D(r['predictions_cny'][m])-D(r['actual_revenue_cny']))/D(r['actual_revenue_cny'])*100 for r in valid),D(0))/len(valid)) if valid else None,wape_pct=float(100*sum((abs(D(r['predictions_cny'][m])-D(r['actual_revenue_cny'])) for r in valid),D(0))/sum((D(r['actual_revenue_cny']) for r in valid),D(0))) if valid else None)
 return dict(positions=len(rows),statuses=dict(Counter(r['status'] for r in rows)),models=models)
rows=evaluate(source);assert len(rows)==18
changed=deepcopy(source);next(r for r in changed['records'] if r['code']=='300866' and r['period']=='2025-12-31')['bits']['FN230']^=1
altered=evaluate(changed);assert [r.get('predictions_cny') for r in rows]==[r.get('predictions_cny') for r in altered];assert [r.get('errors_pct_actual') for r in rows]!=[r.get('errors_pct_actual') for r in altered]
missing=deepcopy(source);missing['records']=[r for r in missing['records'] if not (r['code']=='300866' and r['period']=='2025-12-31')]
blocked=evaluate(missing);assert [r.get('predictions_cny') for r in rows]==[r.get('predictions_cny') for r in blocked];assert sum(r['status']=='blocked_actual' for r in blocked)==3
result=dict(evidence={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in (source_path,baseline_path,p/'protocol.json',p/'forecasts.csv',p/'sources.json')},summary=summary(rows),by_origin={str(y):summary([r for r in rows if r['origin_year']==y]) for y in (2023,2024,2025)},by_company={code:summary([r for r in rows if r['code']==code]) for code in plan['codes']},by_target_year={str(y):summary([r for r in rows if r['target_year']==y]) for y in range(2023,2028)},results=rows,validation=['future_target_bit_tamper_changes_actual_errors_only','missing_future_actual_keeps_all_forecasts_and_positions','eleven_historical_cells_match_TDX_FY_at_printed_million_precision'],decision='two_exposed_companies_diagnostic_no_adoption_or_consensus_claim',boundary=plan['boundary'])
encoded=json.dumps(result,ensure_ascii=False,indent=2)+'\n';target=p/'comparison.json'
if target.exists():assert target.read_text()==encoded
else:target.write_text(encoded)
print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
PY
```
