# 运行发现、证据更新与版本比较

处理已有结果、财报后更新及“为什么变了”。不因缺run ID重算，不把当前引擎重放称为独立DCF校验。

新交付先从`GET /api/research?q=<公司或代码>`或`workspace/research/*/research.json`定位报告ID，再读取登记的`run_ids`、文件与原运行契约；研究ID不是计算ID。不递归扫描整个workspace，不把内部验收当正式覆盖。

## 找到已有结果

按会话引用、现行发布收据或确认的目录定位，不递归扫描整个workspace或静默挪用另一库结果。旧研究可能按授权退役，先查收据；`no_matches`只是目录内无匹配，不代表从未估值。

专项／行业政策按[运行查询](../../../docs/guides/valuation-run-query.md)执行：

```bash
cd "$ALPHALAKE_ROOT/valuation/backend"
"$ALPHALAKE_PYTHON" -m tools.list_valuation_runs "$ALPHALAKE_CODE" \
  --run-dir "$ALPHALAKE_RUN_DIR" --period "$ALPHALAKE_PERIOD" \
  --latest-per-model
```

检查`alphalake-run-query-v1`、范围及`latest_selection_complete`。`partial`／退出2先说明不完整，`has_more`按分页继续；符合证券、模型、期间、资本口径且`unique_latest_run_id`非空才直接选择。最新按`information_as_of`，不是文件时间、创建日期、ID或最高价值；同截止并列须说明差异，不擅选。

查询检查请求与哈希，不重算金额。完整归档可在隔离临时workspace按原提交恢复；紧凑摘要无请求不可重放，不能造旧文件。解释一个结果直接读保存证据即可。

## 先识别运行种类

| 种类 | 查找与验证 | 比较 |
| --- | --- | --- |
| 专项／行业政策 | `tools.list_valuation_runs`、保存请求／audit／report | `tools.compare_valuations BEFORE_ID AFTER_ID --run-dir …`；另一目录用`--after-run-dir` |
| 故事 | 目录摘要、story、manifest、请求／响应；`tools.narrative_valuation verify RUN_DIR` | `tools.narrative_valuation compare BEFORE_DIR AFTER_DIR` |
| 原生具名条件 | `native-conditional-delivery-v2`及实际请求／响应；沿[具名入口](../../../docs/guides/company-valuation-entry.md#原生具名条件自动交付)审阅／重放 | 对照两份输入与输出，保留`input_changes`；不能把ID传旧比较器 |

旧政策比较器从保存请求在当前引擎复算，不访问数据库、不写新估值；契约`alphalake-valuation-comparison-v1`。旧公式不能复现则按原提交核验或显式重建，不加兼容分支，也不把历史合法值说成错误。

## 更新流程

1. 冻结旧报告与条件，核对证据是否比原信息截止新，是否改变财务／经营判断。源刷新另用`alphalake`，不是默认动作。
2. 列“原判断 → 新证据 → 主张是否改变 → 哪项输入需变”。实际值与本研究原预测比较，不虚构共识；更新可以维持论点或只改观察状态。
3. 已获授权就提出并运行成套新条件，保留用户覆盖和旧基线。新快照重新准备，说明参考、股本、报价是否一起变；不借单项更新重选所有参数。
4. 故事修订填`previous_run_id`、增加改动故事`revision`，运行新目录并verify／compare。具名条件保存旧路径／ID与新输入差异，不冒造故事字段。
5. 报告写哪些反证已解决、哪些仍在、下一次观察什么；没改数值就不强行生成新估值。

业绩预告可改变论点强弱，但不等于正式财报或单季EBIT。自动关联预告到故事尚未实现：现阶段报告独立引用区间、指标、报告期／单季是否明确及公告时点；数值条件须有显式推导和限制，不自动取中点、更新TTM或新增证据种类。

## 解释变化的顺序

先比**实际输入**，至少覆盖：

- 财务版本／期间：FY或TTM、累计形成、利润调整、研发队列与资本口径。
- 预测：年度收入、利润率、税／研发、资本倍率或总预算、终值路径。
- 折现／参考：币种、无风险、ERP、Beta、债务组件、权重及税盾。
- 股权桥／股数：现金／投资／债务／少数权益、权利价值、股本日期、报价。
- 公式／政策：不同则先按[引擎台账](../../../docs/valuation/engine-change-ledger.md)说明合法变更，不叫纯数据更新。

`verified_wacc_only`才可称验证过的WACC单因素贡献；`not_attributed`只列变化输入及总差额，多因素交互不能直接相加。确需因果量化时，沿完整入口运行已授权、其他输入固定的条件，保留无法归因／交互部分，不另写自动归因算法。

价差还要区分价格变化与价值变化。结论可以是数据更新、经营判断改变或公式修复后重建；不能把所有变化都说成更准确，也不能因不同于旧值拒绝演进。
