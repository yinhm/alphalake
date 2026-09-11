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

下一步回到同一五家、多历史起点的多年收入与利润路径，复用现有工具，不围绕疫情公司或单年误差追加阈值扫描。生产[完整估值基线](../../../docs/valuation-accuracy.md#五家公司完整基线盘点2026-09-11)的两家成功、三家阻断继续单独保留；历史源研究可算不解除标准链准入。
