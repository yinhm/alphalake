# 安克三起点行业经营利润增长试算

`6bb452a`冻结[协议与映射](protocol.json)，然后生成[结果](result.json)。这是固定五家已暴露开发样本中的先行诊断，15个公司/起点位置中仅安克3项有本轮研究映射，另外12项保留`missing_research_mapping`；不把3组称为独立验证或五家公司完成。

## 原文与政策

复用2022、2023年完整年报，另从CNINFO实际下载2024年原报。三份全年收入构成页均将收入列为消费电子业100%；本目录保留原PDF第16/22/22页，完整文件哈希、页号、来源URL、首次取得分别写入协议。裁剪页与原页文本已核对，没有新增公司财务数值或覆盖TDX。原报告期末到研究起点的业务变化没有逐项核验。

分析政策选`Electronics (Consumer & Office)`作为整体业务代理，而不是沿用当前生产`Computers/Peripherals`。这不是达摩达兰当年的官方成员映射，也不是证实其细分业务经济特征完全一致；尤其储能与智能家居不等同于纯音频，未按分部加权。报表自身“消费电子业”是原文事实，映射到达摩达兰分类是研究判断，两者分开。

三起点分别使用当年1月全球行业EBIT增长。候选只令下一年EBIT代理=起点TDX调整EBIT代理×(1+行业EBIT增长)，不把它当收入增长，不扫描上下限或混合权重。收入沿用当前规则；本次只评分利润，未运行完整DCF。比较基准为当前规则和EBIT不增长，后者不是收入预测候选。

## 结果与停止决定

| 起点 | 行业EBIT增长 | 当前规则误差 | 行业候选误差 | 零增长误差 |
| --- | ---: | ---: | ---: | ---: |
| 2023H1 | 9.9763% | 1.9202% | 2.4990% | 3.0781% |
| 2024H1 | 5.4261% | 0.3869% | 1.3182% | 1.6649% |
| 2025H1 | 10.6125% | 1.6076% | 2.2242% | 2.9212% |
| 三组均值 | — | 1.3049% | 2.0138% | 2.5547% |

误差均为绝对EBIT代理误差/该期实际收入，不是EBIT增长误差或估值误差。三期行业候选都劣于当前规则，因此按事前停止条件，**不采用直接行业增长替代，不再扩大这条候选取证或调权重救结果**。它优于零增长不代表优于现有规则；该小样本结果不能否定所有行业参考方法，也不能证明当前规则有效。

这次诊断说明行业长期基本面增长不能未经公司层面的竞争位置、增长阶段与投入回报依据直接搬成次年预测。后续优先处理已有公司预测与再投资的一致性，不为已经失败的直接替代候选补齐600家映射。

## 离线复验

下面命令复用已有TDX回溯函数精确重建15项原基线，校验输入及三份裁剪证据哈希、正文锚点、披露日期代理早于起点截止；按Decimal交叉复算候选，并逐字节比较结果JSON。翻转PDF副本一个字节必须拒绝。行业原单元格与清单由现有`test_fundgr_semantics.py`三项回归另行核验。

本轮没有生产代码、依赖或数据库变更；对应三项回归及以下离线复验通过，不重复全套Go/Python。新试算是文档内复验命令，**尚未进入CI**；原行业表校验已在pytest CI。依赖既有后端环境的pypdf/xlrd，无新增安装或skip；运行代码仍使用`backend/tools`，本目录仅证据、配置、结果和复验说明。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PYTHON'
import hashlib, json, math
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal
from statistics import mean
from pypdf import PdfReader
from tools.backtest_tdx_history import run
root = Path('valuation/research/damodaran-growth-history/anker-pilot')
p = json.loads((root/'protocol.json').read_bytes())
def checked(path, sha):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('evidence hash mismatch: '+str(path))
    return raw
inputs = {k: json.loads(checked(Path(v['path']), v['sha256'])) for k,v in p['inputs'].items()}
s = inputs['study']; rows = []
for old in inputs['baseline']['origins']:
    origin = old['origin']; year = int(origin[:4])
    study = s | dict(origin=origin, target=f'{year+1}-06-30', forecast_as_of=f'{year}-09-01T00:00:00+08:00', policy=s['policy'] | dict(approved_report_period=origin))
    source = inputs['snapshot'] | dict(records=[r for r in inputs['snapshot']['records'] if f'{year-1}-01-01' <= r['period'] <= study['target']])
    actual_run = run(study, source)
    assert actual_run == old
    for baseline in actual_run['results']:
        row = dict(origin=origin, code=baseline['code'], status='missing_research_mapping')
        mappings = [m for m in p['mappings'] if (m['origin'],m['code']) == (origin,row['code'])]
        assert len(mappings) <= 1
        rows.append(row)
        if not mappings: continue
        m = mappings[0]
        crop = root/m['crop']['file']; checked(crop, m['crop']['sha256'])
        text = ''.join(PdfReader(crop).pages[0].extract_text().split())
        assert all(t in text for t in m['required_text'])
        assert date.fromisoformat(m['source']['disclosure_date']) + timedelta(days=1) < date.fromisoformat(study['forecast_as_of'][:10])
        dataset, = [d for d in inputs['industry']['datasets'] if d['observation_date'] == f'{year}-01-05']
        checked(root.parent/dataset['file'], dataset['sha256'])
        industry, = [d for d in dataset['industries'] if d['industry'] == m['industry']]
        growth = industry['expected_ebit_growth']; assert growth is not None and math.isfinite(growth)
        assert baseline['status'] == 'evaluated'
        base = baseline['base']['ebit']; actual = baseline['actual']['ebit']; revenue = baseline['actual']['revenue']
        predictions = dict(current_rule=baseline['forecast']['ebit'], zero_growth=base, industry_ebit=base*(1+growth))
        decimal_prediction = Decimal(str(base))*(1+Decimal(str(growth)))
        assert abs(predictions['industry_ebit']-float(decimal_prediction)) <= 2*math.ulp(predictions['industry_ebit'])
        row.update(status='evaluated', industry=m['industry'], industry_growth=growth, base_ebit=base, actual_ebit=actual, actual_revenue=revenue, predicted_ebit=predictions,
            absolute_error_pct_actual_revenue={k: 100*abs(v-actual)/revenue for k,v in predictions.items()})
valid = [r for r in rows if r['status']=='evaluated']
summary = {k: mean(r['absolute_error_pct_actual_revenue'][k] for r in valid) for k in ('current_rule','zero_growth','industry_ebit')}
worse = any(r['absolute_error_pct_actual_revenue']['industry_ebit'] > r['absolute_error_pct_actual_revenue']['current_rule'] for r in valid)
result = dict(protocol_sha256=hashlib.sha256((root/'protocol.json').read_bytes()).hexdigest(), planned_pairs=len(rows), evaluated_pairs=len(valid), summary_ebit_mae_pct_actual_revenue=summary, decision='stop_direct_industry_replacement' if worse or summary['industry_ebit']>summary['current_rule'] else 'insufficient_evidence_no_adoption', results=rows, boundaries=p['boundaries'])
encoded = json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
output = root/'result.json'
if output.exists(): assert output.read_text() == encoded
else: output.write_text(encoded)
# 翻转副本一个字节，原哈希必须拒绝；归档不变。
from tempfile import TemporaryDirectory
with TemporaryDirectory() as tmp:
    damaged = bytearray((root/'2022-business.pdf').read_bytes()); damaged[-1] ^= 1
    path = Path(tmp)/'damaged.pdf'; path.write_bytes(damaged)
    try: checked(path,p['mappings'][0]['crop']['sha256'])
    except ValueError: pass
    else: raise AssertionError('tampered evidence accepted')
print(json.dumps({k:v for k,v in result.items() if k!='results'},ensure_ascii=False))
PYTHON
```
