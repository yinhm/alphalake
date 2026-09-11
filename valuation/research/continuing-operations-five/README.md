# 五家公司三起点经营预测基线

先在`f0e1876`固定[协议](study.json)，再复用18份既有TDX完整原包，经下载清单MD5/大小核对及Go解析取得[源切片](snapshot.json)。共90条源记录，与早期20家样本重叠的50条记录逐项完全相同。没有新下载，不发布标准事实或改变映射有效期。完整包约95MB留于本地`workspace/continuing-operations-five-20260911/`；源快照、原包SHA、原取得时间和清单SHA随仓库保存。

固定安克300866、苏泊尔002032、格力000651、伊利600887、海天603288五家，三起点2023H1、2024H1、2025H1，分别评价下一年同期TTM。信息截止为起点当年9月1日中国零点，实际评价截止为2026-09-10中国零点。现有FN314规则、原始float32与生产共享历史预测规则均复用；后来取得版本不是严格PIT，人工分层不是历史行业身份。

## 结果

[完整结果](baseline.json)15/15组在源层可评价，不等于15组生产估值准入：格力、伊利六组均有金融业务字段；海天原披露与后续比较列问题仍在既有审核隔离中。全部公司业务可比性不因本轮可计算而获得认证，完整FCFF仍未验证。这是已暴露目的样本的开发诊断，不是新独立留出或全市场结论。

| 指标 | 当前规则 | 零增长对照 |
| --- | ---: | ---: |
| 收入WAPE | 7.8508% | 7.8028% |
| 收入绝对百分比误差中位数 | 6.8857% | 6.0918% |
| 调整EBIT代理误差/实际收入的绝对值均值 | 1.3289% | 1.6004% |

合计收入平均偏差约−0.058%来自高估与低估抵消，不能据此称预测准确；同公司三期相关，15组不当作15个独立公司。经营利润为合并调整代理，不能把金融兼营公司的这一指标称纯实业EBIT。

| 代码 | 起点 | 当前规则预测一年收入增长 | 后来版本的实际增长 |
| --- | --- | ---: | ---: |
| 300866 | 2023-06-30 | 19.95% | 30.21% |
| 002032 | 2023-06-30 | -2.52% | 12.39% |
| 000651 | 2023-06-30 | 3.46% | 5.97% |
| 600887 | 2023-06-30 | 4.45% | -4.78% |
| 603288 | 2023-06-30 | -4.26% | 2.82% |
| 300866 | 2024-06-30 | 20.00% | 39.02% |
| 002032 | 2024-06-30 | 9.83% | 2.93% |
| 000651 | 2024-06-30 | 1.00% | -8.71% |
| 600887 | 2024-06-30 | -9.60% | -1.67% |
| 603288 | 2024-06-30 | 9.10% | 8.64% |
| 300866 | 2025-06-30 | 20.00% | 22.64% |
| 002032 | 2025-06-30 | 4.73% | -1.03% |
| 000651 | 2025-06-30 | 1.08% | -12.95% |
| 600887 | 2025-06-30 | 3.68% | 0.61% |
| 603288 | 2025-06-30 | 7.54% | 6.49% |

安克三个窗口均被低估，其中后两期触及20%上限；格力后两期由正增长预测转为实际下滑；苏泊尔三个窗口亦有方向变化。现有两项规则没有在收入与利润指标上同时胜出，不能统一将全部公司的增长压低，也不能据安克事后误差直接提高上限。保留原参数，不将本轮五家公司用于调整规则后自证。

## 复验

已用原函数精确重建三个结果；修改未来目标期FN86不改变起点输入/预测，但改变误差；重复代码/期间在三个起点均阻断。另真实翻转完整ZIP一个字节，原清单核验拒绝、没有发布snapshot。无代码/依赖变更，本轮不重跑全套Go/Python；沿用既有已验收解析器和预测函数。以下命令依赖已有后端Python环境，结果可从仓库切片离线复现；完整包字节篡改检查另依赖本地原包。

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PYTHON'
import hashlib, json
from pathlib import Path
from tools.backtest_tdx_history import run, metrics
root = Path('valuation/research/continuing-operations-five')
p = json.loads((root/'study.json').read_text())
source = json.loads((root/'snapshot.json').read_text())
expected = json.loads((root/'baseline.json').read_text())
assert hashlib.sha256((root/'study.json').read_bytes()).hexdigest() == expected['protocol_sha256'] == source['study_sha256']
assert hashlib.sha256((root/'snapshot.json').read_bytes()).hexdigest() == expected['source_sha256']
rows = []
for original in expected['origins']:
    origin = original['origin']; year = int(origin[:4])
    study = p | dict(origin=origin, target=f'{year+1}-06-30', forecast_as_of=f'{year}-09-01T00:00:00+08:00', policy=p['policy'] | dict(approved_report_period=origin))
    selected = source | dict(records=[r for r in source['records'] if f'{year-1}-01-01' <= r['period'] <= study['target']])
    result = run(study, selected)
    assert result == original
    rows.extend(result['results'])
assert metrics(rows) == expected['summary']
print('15 company/origin results and aggregate metrics replayed')
PYTHON
```

已完成下方同一五家的两年/三年路径诊断；没有围绕疫情公司或单年误差追加阈值扫描。生产[完整估值基线](../../../docs/valuation-accuracy.md#五家公司完整基线盘点2026-09-11)的两家成功、三家阻断继续单独保留；历史源研究可算不解除标准链准入。

## 两年/三年路径诊断

`1fba225`事前冻结[计划](multiyear-plan.json)，复用既有`forecast_rows`及三个既有路径：前五年沿用起点增长、零增长、从第二年开始至第五年收敛至终值增长。没有新候选或阈值调参，不重新评采用门槛。三起点×两期限×五家=30组，15组已到期可评价，15组目标为2027/2028H1，记`not_yet_observable`；未冒充缺字段，也未纳入误差。

[完整结果](multiyear-result.json.gz)保留全部30组及金融兼营/可比性未闭合的统一边界。两年包括2023H1→2025H1及2024H1→2026H1共10组，三年仅2023H1→2026H1共5组；期限之间样本窗口不同，不能把合计误差差异全归因于期限。同公司多期重叠，不算独立宏观样本。

| 期限与指标 | 前五年沿用起点增长 | 零增长 | 第五年收敛 |
| --- | ---: | ---: | ---: |
| 两年收入MAE/实际收入 | 16.609% | 16.266% | 16.275% |
| 两年收入WAPE | 17.479% | 12.624% | 16.972% |
| 两年EBIT代理MAE/实际收入 | 2.409% | 2.578% | 2.413% |
| 三年收入MAE/实际收入 | 24.018% | 21.693% | 23.719% |
| 三年收入WAPE | 26.073% | 17.473% | 25.096% |
| 三年EBIT代理MAE/实际收入 | 2.684% | 3.046% | 2.573% |

零增长降低合计收入误差，却恶化合计利润代理误差。对安克，三个成熟窗口的收入绝对百分比误差均值为原路径19.441%、零增长47.020%、提前收敛24.324%；不能只看总体误差就给安克统一压低增长。其他四家公司本样本零增长收入误差较低，不构成可事后指定的公司规则；五家均未成为新的独立留出。

结论：保留公司差异，不自动采用统一零增长或提前收敛；当前增长规则也没有获得完整DCF有效性认证。这轮诊断到此收口，不继续扫描收敛年数以改善同一批结果。后续候选需要起点可得的经济依据及未参与选择的验证，不能按这张结果表反推公司分类。既有固定10%终值ROIC对照仍只解释价值影响，不能替代多年经营验证。

三个路径均经Decimal复利计算交叉核对；首年预测与上轮基线完全相同。篡改2026H1实际收入仅改变误差、不改变任何起点与预测；重复历史代码/期间继续拒绝。无代码或依赖变更，复用现有工具、18份原包与90条切片；不新增采集或标准事实。离线精确重放：

```bash
PYTHONPATH=valuation/backend workspace/anker-agent-adapter-20260906/venv/bin/python - <<'PYTHON'
import gzip, hashlib, json
from pathlib import Path
from tools.backtest_tdx_multiyear_growth import forecast_rows, metrics
root = Path('valuation/research/continuing-operations-five')
plan = json.loads((root/'multiyear-plan.json').read_text())
data = {}
for name, ref in plan['inputs'].items():
    raw = Path(ref['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ref['sha256']
    data[name] = json.loads(raw)
expected = json.loads(gzip.decompress((root/'multiyear-result.json.gz').read_bytes()))
assert hashlib.sha256((root/'multiyear-plan.json').read_bytes()).hexdigest() == expected['plan_sha256']
s = data['study']
valid = [r for r in expected['results'] if r['status'] == 'evaluated']
windows = [dict(origin=o, horizon=h) for o,h in sorted({(r['origin'],r['horizon']) for r in valid})]
p = data['helper_protocol'] | dict(samples=s['samples'], base_policy=s['policy'], evaluation_as_of=s['evaluation_as_of'], windows=windows)
assert forecast_rows(p, data['snapshot']) == valid
assert metrics(expected['results']) == expected['summary']
assert len(valid) == 15 and len(expected['results']) == 30
print('15 mature forecasts replayed; 15 future windows retained')
PYTHON
```

## 资本输入扩展

[固定五家资本源衔接](capital-source.md)已完成22包110条源记录、原1,080个位值和15组预测不变；安克五年度资本开支/研发10项与已有原文台账位匹配。完整经营资本与FCFF仍未闭合，不改变上述预测结论或生产准入。

## 多年营运余额路径

[三项余额随收入增长的检验](trade-balance-path.md)已完成30/45个到期位置：总体误差下降但仅安克改善，其余四家变差，不推广。15个未来位置保留，事后实际收入对照仅作误差归因，不冒充预测或完整营运资本。

## 两期比例候选

[同季度两期余额/收入比率均值](trade-balance-mean.md)在同30个到期位置将误差5.7869%提高到6.2550%，存货及应付WAPE亦恶化，按冻结门槛停止；不继续扫描权重或扩大采用。新增真实回归进入现有后端CI。

## 经营利润误差来源

[对称分解](profit-error-attribution.md)已复用全部45个一至三年位置，30个成熟位置中19个收入贡献更大，两至三年为12/15；14个存在收入/利润率误差抵消。不根据最新单年偏差统一调整利润率，也不改变既有增长与资本需求候选的结论。

[格力金融兼营来源衔接](gree-finance-scope.md)已补2025年7个独立主体金额、核对4个合并TDX字段；内部往来和金融资产归属仍待闭合，不解除生产准入或更改历史预测。
