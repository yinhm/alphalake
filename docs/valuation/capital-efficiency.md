# 资本效率：方法、接口与当前边界

本页合并原经济一致性检查、资本效率过渡和公司/行业倍率说明。原逐轮计算、数值及审核记录保留于[历史验收](../history/README.md)，不作为新的默认政策。

## 方法

依据达摩达兰[增长与再投资](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/littlebook/growthvaluedrivers.htm)及[终值回报](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/termvalueexreturns.htm)：预测期可用收入增量/销售资本倍率估计净再投资，保留投入滞后；稳定期再投资与增长/资本回报配套。检查隐含回报，不能只因使用了教材公式就证明公司参数合理。

| 概念 | 用途与限制 |
|---|---|
| 公司年度收入/期末调整资本 | 历史存量诊断；不等于项目新增效率 |
| 行业销售/投入资本 | 显式预测代理；行业分类、租赁及研发口径须核对 |
| 预测收入增量/资本倍率 | 共享引擎的净再投资；不再重复扣研发或营运资本 |
| 终值NOPAT×g/ROIC | 稳定期再投资；新增资本回报不等于存量账面ROIC |
| 历史账面资本变动 | 含现金、债务、权益及研发资产等影响，不能直接当成经营净再投资 |

## 现有接口

**审阅：** `tools.review_native_policy`先重放保存输入和结果，再核查资本投入年份、增量利润来源及终值再投资规则。`capital.proxy_comparison`固定最新完整财年及之前四年，逐年列出模型与独立SQLite倍率、缺余额/研发队列原因、行业对照和前五年投入反事实。缺年不延长窗口，负资本不删，两个口径不得拼接。

**显式过渡：** 后端`valuation_assumptions.annual_sales_to_capital`可指定每个预测投入年度的正有限倍率，长度等于预测年数；存在时覆盖两阶段倍率。恢复两阶段时显式清除该字段；自动假设重选会清除覆盖。网页默认流程不变。

`tools.compare_native_capital`复用共享引擎，对已核验的十年统一场景施加`native-capital-transition-v1`：前五年倍率不变，第六至十年在资本强度`1/(S/C)`上线性过渡至原终值政策隐含的等价倍率。保持收入、利润率、税、WACC、终值及股权口径，逐项核对再投资变化。无有效正终值再投资/倍率时显式不适用。

五年过渡和线性规则是固定分析假设，不是教材要求或公司事实。衔接消除是构造结果，不意味着预测更准确，不能按股价挑选情景。

## 使用

先用[统一场景入口](native-uniform-scenarios.md)按当前运行时生成完整基线目录，再在核验过1GiB硬限额的独立服务中串行运行：

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.review_native_policy \
  workspace/derived/capital-transition/baseline \
  workspace/derived/capital-proxy-review/new-review.json

PYTHONPATH=valuation/backend .venv/bin/python -m tools.compare_native_capital \
  workspace/derived/capital-transition/baseline \
  workspace/derived/capital-transition/new-comparison.json \
  --web http://127.0.0.1:8080
```

输出必须为新文件。来源目录、实际API、财务/参考时点必须对应，冻结旧报告按原提交复验。保留全部公司分母，不为凑齐情景替换公司。运行结果包含政策、完整输入、来源哈希、资本变化、报告及运行标识。

## 当前结论

- 既有12家固定样本中6家公司形成21场景，另外6家保留选择拒绝或财务缺项。资本过渡真实API验收中18个条件值下降、3个上升；原基线不变，没有自动采用该政策。
- 字段级放行后，按当前SQLite和既有五年研发政策复算2021—2025年模型资本倍率：安克及茅台各5/5，安井、天富、航天及扬农各3/5。可算不等于稳定或预测有效；安克独立研究的现金政策不同，不直接混合历史序列。
- 应付债券及本轮借款等字段已统一放行实际源零，不再要求公司逐项PDF审批。六家2024年十项债务源零已全部闭合；2024年安克、天富、安井、航天、扬农、茅台模型倍率分别约1.964、0.487、1.356、0.835、0.851、2.669。原86家成功默认估值未变；本轮研发字段放行使另3家恢复准入。
- 航天电器长期应付款约2.916亿元已确认计息，但TDX总项包含专项应付款，缺可靠结构化分拆。单独计入的研究敏感性使2024年倍率0.8347→0.7962，不代表供给已接通；流动部分不重复加总。

下一步只做会改变近期资本范围及预测代理判断的取证，不继续扩展早年历史，不把未审核余额归零。证据不足时保留获准代理与明确限制，不宣称完整债务或公司资本效率已经闭合。

此前六家公司、七个受控情景表明：只补2024年历史债务不改变当前估值；采用历史倍率预测才改变再投资。保留行业代理，暂不自动采用公司倍率；此前四项逐公司推定零提案已被[字段级统一放行](../guides/tdx-offline-sync.md#字段零值审核)取代；原取证保留在[历史核验](../history/capital-scope-review-20260929.md)。

## 证据导航

- [经济一致性初次验收](../history/native-current-economic-checks.md)
- [资本过渡21场景验收](../history/native-capital-transition.md)
- [公司/行业核对、源零审核与发布](../history/native-capital-proxy-review.md)
- [债务审核通道及安克先例](../history/historical-debt-zero-review.md)
- [EBIT与投资范围审计](../history/ebit-investment-scope-audit.md)
- [标准资本分量核算](native-capital-definition.md)
