# ADR 009——估值所需核心财务明细

状态：已接受，2026-09-06。

## 范围与证据

从九个标准映射扩充到二十九个，优先支持普通非金融公司的现金、借款、权益、营运资本和再投资分析。字段编号依据 [TDX 官方目录](https://help.tdx.com.cn/quant/docs/markdown/TdxQuant.md/mindoc-1h10m001ic888.html)，单位和实际口径由[两份真实合并财报、35 个金额](../../internal/ingest/testdata/core-financial-2025/README.md)验证。

| FN | 标准字段 | 财报含义 |
| --- | --- | --- |
| 8 | monetary_funds | 货币资金 |
| 11 | accounts_receivable | 应收账款 |
| 17 | inventories | 存货 |
| 21 | current_assets | 流动资产合计 |
| 25 | long_term_equity_investments | 长期股权投资 |
| 40 | total_assets | 资产总计 |
| 41 | short_term_borrowings | 短期借款 |
| 44 | accounts_payable | 应付账款 |
| 52 | current_portion_noncurrent_liabilities | 一年内到期的非流动负债 |
| 54 | current_liabilities | 流动负债合计 |
| 55 | long_term_borrowings | 长期借款 |
| 63 | total_liabilities | 负债合计 |
| 69 | noncontrolling_interests | 少数股东权益 |
| 72 | total_equity | 所有者权益合计 |
| 114 | capital_expenditure_cash | 购建固定资产、无形资产和其他长期资产支付的现金 |
| 133 | cash_and_cash_equivalents | 期末现金及现金等价物余额 |
| 136 | depreciation_depletion | 固定资产折旧、油气资产折耗、生产性生物资产折旧 |
| 137 | intangible_amortization | 无形资产摊销 |
| 138 | deferred_expense_amortization | 长期待摊费用摊销 |
| 271 | equity_parent | 归属于母公司股东权益 |

## 期间与精度

迁移 018 为字段目录增加 `period_basis`，默认 `report` 保持原字段约定。新增字段除 FN114、136–138 为 `ytd` 外，其余均为 `instant`。物化时，`instant` 保留期末存量；`ytd` 标记 Q1/H1/9M/FY；原 FN230–237 仍是单季度 Q1/Q2/Q3/Q4。未知期间策略拒绝物化。

新增字段均为人民币元，沿用 float32 到 DECIMAL(38,10) 的确定性表示，不恢复源编码丢失的分位。映射从 2025-01-01 起有效；当前证据不足以宣称更早报告期已审核。既有映射的有效期不变，报表范围仍为 `provider_default`，本次合并报表样本不能证明所有公司、所有报告期口径都一致。

## 缺失值和估值边界

真实 Q1/Q3 包中 FN136–138 的零值与未披露现金流补充资料并存。因此这些字段的零值不进入标准事实，写入 `provider_zero_ambiguous` 诊断，物化运行标为 partial；不可变源值保留。无法区分真实零和未披露时，接受缺口。其他字段仍按原有源值语义处理，原始零不等于已经逐份财报验证为零。

货币资金可能含受限资金，不等于可加入企业价值的超额现金；现金等价物余额也需估值时判断经营用途。长期股权投资不自动认定为非经营资产。短期/长期借款仅为债务组成部分，应付债券、租赁负债及附注拆分尚未补齐；一年内到期的非流动负债不能全部当成有息债务。资产、负债总额不直接替代投入资本或非现金营运资本。

资本开支现金不包含全部并购再投资或非现金投入。原两家公司样本中FN136不含另列的使用权资产折旧，不能将该发现泛化到所有公司/期间。2026-09-10新增[中邮科技三期核验](../../valuation/research/tdx-growth-expanded/README.md)：FN136在两个半年报包含投资性房地产折旧，2025年报又包含使用权资产折旧；字段对应报表合并列示的范围会变化。三项明细之和尚不能直接宣称完整经营D&A，不能固定将FN136、FN579、FN581相加。不生成未经核实的总债务、净债务、EBIT、FCFF 或估值结论。税项、EBIT 调整、完整债务/再投资口径、年度/TTM 和连续历史覆盖仍需补齐。

## 已有数据库

使用新程序执行迁移及本地重物化，无需重下已有源包：

```bash
alphalake init ./alphalake.duckdb
alphalake materialize-fundamentals ./alphalake.duckdb
```

物化器版本升为 `pit-fundamental-v3`，现有事实按不可变源身份更新，新字段追加；原始来源、公告关联与 PIT 规则不变。迁移不直接改写标准事实，重物化后才得到新结果。缺少公告或身份的记录沿用原有待解析行为。

验证包含 v17 数据库升级与重复迁移、35 个真实金额及原文重提取、H1/9M/期末期间、原始公告及 PIT 边界、缺失折旧诊断、旧年度/更正样本和幂等重放。将九个月资本开支故意标为 Q3，真实回归按预期失败。
