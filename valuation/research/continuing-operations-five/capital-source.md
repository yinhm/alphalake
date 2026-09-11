# 固定五家的资本源字段与历史衔接

沿用安克、苏泊尔、格力、伊利、海天五家，不以未来经营结果重新选公司。本轮完成输入扩展，未新拟合预测规则或调整估值。

[冻结配置](capital-study.json)在读取扩展字段前提交。复用现有Go解析器与本地22份完整TDX包，获得2021Q1—2026H1共110条记录；[源切片](capital-snapshot.json)保留字段位、报告期、清单及原包血缘。原18包90条记录的1,080个原字段位值和15个历史起点预测结果全部不变。新字段仍处于研究源层，不冒充标准事实或严格PIT。没有新下载，临时完整包副本验收后自动清理，原归档未动。

## 三起点资本输入

使用现有 `audit_tdx_reinvestment_inputs.audit`，2023/2024/2025H1起点分别检查前两完整年度，截止为当年9月1日中国零点。各起点分母均为5家、10个年度槽位；不是H1 TTM资本。

| 两年度分量均为非零源值 | 2023 | 2024 | 2025 |
| --- | ---: | ---: | ---: |
| 权益/现金/长期股权投资四分量 | 4 | 4 | 4 |
| 五项常见债务 | 2 | 1 | 0 |
| 现金资本开支与三类折旧摊销 | 3 | 3 | 3 |
| 费用化研发 | 5 | 5 | 5 |
| 现金流营运调节三项 | 5 | 5 | 5 |

这是源组合计数，不是完整率或估值准入率。五项债务计数下降主要涉及源零，不能据此说公司债务数据缺失；非零也不证明完整分类。FN439仍保留万元源编码，尚未将上述分量求和为资本。全部15个位置的完整再投资和经济ROIC继续为空，生产的格力/伊利经营范围与海天版本核验阻断亦未因此解除。

## 安克10个源值与已有原文衔接

[六年历史样本](../../../internal/ingest/testdata/anker-history-2026/README.md)默认校验已重新执行，通过四份年报、180个历史金额及7个资本开支证据金额的原文重提取与派生表重建。本轮将其中2021—2025每年现金资本开支、研发费用两项，分别与新切片FN114、FN304做float32位比较，10项全部匹配，详见[完整回执](capital-audit.json.gz)。2020不在新源范围，不计入覆盖。

比较采用已有台账明确记录的本期/比较/重述版本；本次匹配不表示当时已取得这些版本，也不把PDF小数写回TDX。这增加了五个年度资本开支及研发分量的可追溯研究证据，仍不能将净现金资本开支、费用化研发和现金流调节直接相加为完整经济再投资。

后续[安克五年度资本分量衔接](anker-capital-components.md)已完成50项比较（含本轮10项），49匹配、1项版本冲突；下一步核对营运占用的归属与时序，再对照苏泊尔；需要的是正常经营下新增资本的归属与时序，不是先解释完极端公司。分类不足时，不用行业比率反推真实资本，不将报表现金代理当作FCFF实际值，也不据此宣布估值准确度提高。

## 重放与验证范围

本轮无生产代码、依赖、Go改动。沿用现有工具，未重跑全套Go/Python。原PDF校验已在既有CI；下列新衔接检查为本地验收，尚未单独接入CI。源切片及压缩结果已入库；完整包的原始路径依赖本地workspace的 `tdx-operating-cash-20260911/source-manifest.json`，干净克隆可重放切片检查，但不等于重新从全包解析。

以下检查同时覆盖源保留、历史预测不变及篡改边界：将2021年安克FN304置零后，只有2023起点的研发源组合计数5→4，2024/2025不变。源零不再通过“非零组合”，不是自动判定公司无研发。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PY'
import csv, gzip, hashlib, json, struct
from copy import deepcopy
from pathlib import Path
from tools.audit_tdx_reinvestment_inputs import audit
from tools.backtest_tdx_history import run
p=Path('valuation/research/continuing-operations-five')
load=lambda name: json.loads((p/name).read_bytes())
s=load('capital-snapshot.json');config=load('capital-study.json');old=load('snapshot.json')
assert s['study_sha256']==hashlib.sha256((p/'capital-study.json').read_bytes()).hexdigest()
index={(r['code'],r['period']):r for r in s['records']};assert len(index)==len(s['records'])==110
for r in old['records']:
 n=index[r['code'],r['period']]
 assert n['artifact']==r['artifact'] and all(n['bits'][f]==v for f,v in r['bits'].items())
for a in old['artifacts']:
 b=next(x for x in s['artifacts'] if x['file']==a['file'])
 assert all(b[k]==a[k] for k in ('sha256','md5','size','report_period'))
base=load('baseline.json');policy=load('study.json')
for expected in base['origins']:
 origin=expected['origin'];year=int(origin[:4])
 study=policy|dict(origin=origin,target=f'{year+1}-06-30',forecast_as_of=f'{year}-09-01T00:00:00+08:00',policy=policy['policy']|dict(approved_report_period=origin))
 selected=s|dict(records=[r for r in s['records'] if f'{year-1}-01-01'<=r['period']<=study['target']])
 assert run(study,selected)==expected
annual=Path('internal/ingest/testdata/anker-history-2026/annual-inputs.csv')
matched=[]
for row in csv.DictReader(annual.open()):
 if row['year']=='2020':continue
 r=index['300866',row['year']+'-12-31']
 for f,k in [('FN114','cash_capex'),('FN304','rd_expense')]:
  bits=struct.unpack('<I',struct.pack('<f',float(row[k])))[0]
  assert r['bits'][f]==bits
  matched.append(dict(period=r['period'],field=f,pdf_decimal=row[k],bits=bits))
assert len(matched)==10
result=audit(s,config['samples'])
tampered=deepcopy(s)
next(r for r in tampered['records'] if (r['code'],r['period'])==('300866','2021-12-31'))['bits']['FN304']=0
changed=audit(tampered,config['samples'])
assert result['summary']['2023']['complete_source_groups']['rd_expensed']==5
assert changed['summary']['2023']['complete_source_groups']['rd_expensed']==4
assert all(changed['summary'][y]==result['summary'][y] for y in ('2024','2025'))
result['verification']=dict(original_records=90,unchanged_original_bits=1080,original_forecast_positions_replayed=15,anker_pdf_source_matches=matched,negative_check='2021 Anker FN304 zero changes only 2023 RD readiness',actual_fcff=None)
result['evidence']={name:hashlib.sha256((p/name).read_bytes()).hexdigest() for name in ('capital-study.json','capital-snapshot.json','snapshot.json','baseline.json')}
result['evidence'][str(annual)]=hashlib.sha256(annual.read_bytes()).hexdigest()
encoded=(json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode()
target=p/'capital-audit.json.gz'
if target.exists():assert gzip.decompress(target.read_bytes())==encoded
else:target.write_bytes(gzip.compress(encoded,mtime=0))
print('110 source records; 15 forecasts unchanged; 10 PDF matches; targeted zero rejected as complete')
PY
```
