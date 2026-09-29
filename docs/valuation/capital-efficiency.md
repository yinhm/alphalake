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

## 公司历史代理的完整DCF对照

同一工具新增`--basis company-history`，政策`native-company-capital-v2`：只用最新完整财年及前两年，计算`三年收入合计 / 三年期末含少数股权的研发调整资本合计`，作为十年显式期的恒定倍率。它相当于按资本加权年度倍率，不是倍率算术平均；三年窗口和恒定路径是本地情景政策，不是教材规定。资本以原模型历史诊断的归母权益＋债务－现金＋研发资产为起点，再加各年账面少数股东权益，不与独立SQLite或未调整行业数据拼接。

v1漏计了合并子公司属于其他股东的资本；本次按达摩达兰[回报率论文](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/returnmeasures.pdf)中合并回报的资本公式修正。AlphaLake[出口契约](../guides/valuation-sqlite-export.md)明确`bv_equity`是归母权益，故补计少数股权不会与归母权益重复。政策中明确`equity_basis`，不得将已含少数股权的总权益冒充这一输入。最终股权桥接仍由共享引擎扣除原少数股权代理，两个位置用途不同：前者匹配合并收入的资本需求，后者归属于母公司股东。

金融投资尚有混合经营/非经营范围，未据上述论文将`cross_holdings`小计自动全额扣出；研发/现金/债务和投资收益分类限制继续保留。共享引擎的原始历史倍率仍作输入诊断，本修正只作用于显式公司代理，不声称已全面修正原生历史ROIC。

三年必须连续、唯一，收入、原历史资本及补计少数股权后的资本均正且有限，少数股权必须有有限数值（实际零有效，缺项不归零，负值按实际参与）；任一年缺项/非正则保留原记录，不用较早年份补位。当前历史诊断只包含研发调整，因此另有租赁资本化或prepared TTM时，这个对照不自动成立。研发资产只要求实际需要的队列；不把完整摊销/利润率历史额外设为资本倍率门槛。

共享引擎只改变`annual_sales_to_capital`；逐项检查财务、收入、利润、税、折现因子、终值与股权桥接输入不变，并将再投资变化的折现值与显式期现值差核对。终值资本规则不因公司倍率自动改写，可能出现的衔接差异继续输出。困境覆盖、期权等仍由共享引擎处理，最终每股差额不直接用经营价值差除股数代替。

```bash
PYTHONPATH=valuation/backend .venv/bin/python -m tools.compare_native_capital \
  workspace/derived/contraction-method/scenarios \
  workspace/derived/company-capital-method/new-comparison.json \
  --basis company-history --web http://127.0.0.1:8080
```

运行仍须使用独立1GiB服务。输出保存三年证据、选定倍率、完整前后报告、逐年投入变化和每股影响；不按估值高低选择代理，`automatic_adoption=false`。现金、投资、债务和少数股权范围限制并未由三年聚合消除；公司历史存量比率不自动成为未来新增投资效率。

2026-09-29同一12家/30场景复验：v1曾有7家24场景完成公司代理对照，但该分母遗漏了少数股权，故旧结果只留作历史差异依据。v2在同一快照上0场景完成、30场景保留缺项；原1家选择拒绝、2家输入阻断仍在分母。除原连续历史缺口外，最新年度之前的模型少数股权均未齐备，不能把旧24个结果继续称作已符合新口径。

已追踪到历史标准映射边界：`noncontrolling_interests`原映射从2025年开始，资本历史扩展目录未包含它；SQLite历史为空并非已经证明TDX没有数据。下一步应按官方定义及现有字段审核通道补足历史映射、核验源分布，再重建标准事实和发布快照；不从PDF另采主值，也不借独立历史直接填进旧模型。本轮未改主库/SQLite/网页，亦未将方法缺项升级为原生默认估值阻断。

48项相关Python回归覆盖实际零、负少数股权、缺项、非正调整资本及股权桥接不重复扣减；Go全套及构建通过。v1冻结证据在`workspace/derived/company-capital-method/`，v2缺项及映射查询在`workspace/derived/consolidated-capital-method/`。首轮验收汇总对空成功集合调用min失败、另一次只读查询缺Python duckdb依赖均保留日志；改用已有Go驱动查询，不新增依赖。这轮没有成功的v2真实公司DCF，不能称8080已通过v2数值验收。


## 当前结论

- 此前资本过渡验收中，12家固定样本有6家公司形成21场景，18个条件值下降、3个上升；原基线不变，没有自动采用该政策。最新同名单方法审阅为9家公司30场景，见[通用方法契约](methodology-contract.md)，不冒称已重做30场景资本过渡。
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
