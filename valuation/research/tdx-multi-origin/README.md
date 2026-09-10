# 多历史起点预测复验

当前结论：首轮候选未通过开发门槛；第二轮开发选择通过，但留出验证失败。生产预测规则未替换，尚未证明估值准确度提升。

## 范围与冻结顺序

沿用[首轮研究](../tdx-history/README.md)的20家目的样本，12家开发、8家留出。新增2023H1→2024H1、2024H1→2025H1两个一年窗口；2025H1起点结果此前已知，不计本轮独立验证。研究归档覆盖2022Q1—2026H1共18个TDX包，各包按对应取得版本的原始清单核验大小和MD5，快照保留float32位及来源引用。

1. `1109edd`先冻结[协议v1](protocol.json)，随后运行开发集。24个公司/期间组合全部可评价，但没有候选满足全部门槛；结果见[开发摘要](development-summary.json)。未打开v1留出。
2. `ea79d86`根据开发结果冻结[协议v2](protocol-v2.json)：较弱增长收敛、前一全年利润率，以及逐公司剔除后的稳健性门槛。此为开发结果指导的迭代，不冒称初始假设。
3. `4c95dae`冻结实现及[开发选择](development-v2-summary.json)，选中`quarter_growth_full_margin`：原增长乘0.75，利润率采用前一完整年度值。
4. 随后打开留出，结果见[留出摘要](holdout-v2-summary.json)。这批留出已使用，不能再用于调参后声称独立验证，也不能改选第二名自证。

## 留出结果

16个公司/期间组合中14个可评价；宁德时代两个窗口因重复代码/报告期而拒绝，保留在分母。

| 模型 | 收入加权绝对百分比误差 | 利润绝对误差/实际收入的公司等权均值 |
| --- | --- | --- |
| 当前规则 | 8.2957% | 1.6749% |
| 零增长、当前利润率 | 6.6082% | 1.9124% |
| 开发集选中的候选 | 7.1786% | 1.9832% |

候选改善收入指标，但利润指标恶化约18.4%，且2024起点和逐公司剔除门槛未通过。当前规则相对零增长在利润与收入指标上胜负不同，不能笼统宣称有效或无效。利润为合并调整经营利润代理；包含金融业务信号的公司另组报告，不称纯工业EBIT。

## 复现

在仓库根目录，使用已安装后端依赖的Python解释器：

```bash
cd valuation/backend
python -m tools.backtest_tdx_origins ../research/tdx-multi-origin/protocol-v2.json ../research/tdx-multi-origin/snapshot.json --phase development > /tmp/alphalake-development-v2.json
python -m tools.backtest_tdx_origins ../research/tdx-multi-origin/protocol-v2.json ../research/tdx-multi-origin/snapshot.json --phase holdout --selection /tmp/alphalake-development-v2.json > /tmp/alphalake-holdout-v2.json
python -m pytest tests/test_tdx_history.py -q
```

选择文件绑定协议、源快照和代码哈希，并重新计算开发决策；篡改所选候选会拒绝。代码版本变化后应重新生成选择文件，历史摘要原样保留，不能改写历史实验。快照可离线评分；重新从完整包构建需本地原始归档及对应清单，工具为`cmd/prepare-tdx-history`。

## 边界和下一步

复用生产规则和上年年初起的输入窗口，不能因多下载历史而悄悄增加同比对数。采用后来取得的TDX数值与FN314日期，不是严格PIT；FN314不能证明源数值未被后续修订。异常定向核验CNINFO，保留版本风险与原始误差，不用PDF覆盖TDX或删除难预测公司。

下一轮先在开发集合拆解增长、利润率及版本影响，再预先固定新增公司与验证门槛，复用现有包提取新样本。旧留出只作诊断；新增证据通过后才考虑版本化接入生产。收入/利润预测的一年期复验不等于五年路径、再投资、WACC或完整DCF有效性验证。

五粮液2025H1异常已记录为[版本风险台账](revision-risk-000858.json)：两版原文第6页收入不同，TDX季度加总接近更新版但FN314仍为250828。此次为定向原文检查，PDF本体尚未进入离线CI；不称独立累计收入位级核验，不替换源值。
